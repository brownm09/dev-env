#!/usr/bin/env python3
"""Shared per-session repo/branch state tracking for Claude Code Bash hooks.

dev-env#573: a session's Bash cwd (and separately, a checked-out branch) can
silently revert to a stale/default state with no error surfaced — most
likely tied to an intermittent Git Bash (MSYS2) crash under resource
pressure. That crash, and the harness's own cwd-tracking, are outside this
repo's reach. What dev-env CAN do is make the *symptom* visible:
post-tool-use-cwd-track.py records the repo root + branch after every Bash
call, and pre-commit-branch-check.py / pre-pr-create-check.py compare that
record against the current state at the moment a consequential command
runs, surfacing a loud (but non-blocking) warning on a mismatch.

Comparing on (repo_root, branch) rather than raw cwd is the key precision
choice: it does not fire for ordinary same-repo subdirectory navigation
(routine and extremely common), only for a genuinely different repo/worktree
or a different branch of the same repo — covering both failure sub-modes
observed in the issue (a worktree silently replaced by the canonical root;
the same repo with its branch silently reverted).

Imported the same way as _hookutil: a sibling module in scripts/ that the
`pyw -3` hook launcher (which puts the script's own directory on sys.path)
and the test harness (sys.path.insert(0, scripts_dir)) both resolve.

``current_repo_state()`` is shared by post-tool-use-cwd-track.py (the writer,
called on every single Bash tool call) and four checkpoint hooks:
pre-commit-branch-check.py, pre-pr-create-check.py, and pre-merge-branch-check.py
(gated on specific command content — ``git commit`` / ``gh pr create`` /
``gh pr merge``), plus pre-bash-drift-check.py (dev-env#682 — gated instead on
elapsed time since the last recorded call, via ``state_age_seconds()`` below,
so a drift affecting any *other* Bash command — not just those three — also
gets caught, without paying a git subprocess on every single call). Originally
duplicated across all four original call sites; consolidated here after the
duplication already caused one bug during development (a display-placeholder
return value in one copy that didn't match the others' ``None`` convention,
which would have manufactured spurious drift warnings on every detached-HEAD
commit).

The *orchestration* around ``current_repo_state()`` — read the recorded state,
compute the current state, diff them — was still duplicated across all four
checkpoint hooks even after that consolidation, the exact class of risk the
module docstring above already names. ``drift_warning_for()`` closes that
second layer: all four hooks now call it instead of re-composing the
three-call sequence by hand.

Usage:
    import _bash_state

    repo_root, branch = _bash_state.current_repo_state(cwd)
    _bash_state.write_state(session_id, repo_root, branch, cwd)
    _bash_state.cleanup_stale_state()
    recorded = _bash_state.read_state(session_id)
    warning = _bash_state.format_drift_warning(recorded, repo_root, branch, cwd)
    age = _bash_state.state_age_seconds(session_id)
    repo_root, branch, warning = _bash_state.drift_warning_for(session_id, cwd)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import _winsubp  # noqa: F401  -- suppress console windows on Windows
from _worktree_topology import DETACHED, resolve_current_branch

SCRATCH = Path.home() / ".claude" / "scratch"
MAX_AGE_DAYS = 30


def cleanup_stale_state(scratch: Path | None = None) -> None:
    """Remove per-session state files whose mtime is older than MAX_AGE_DAYS.

    Mirrors _hookutil.cleanup_stale_sentinels — every other per-session file
    in this codebase already expires this way; this module was the one
    producer without it. Swallows all I/O errors — best-effort, must never
    block a hook. *scratch* overrides SCRATCH (used by tests)."""
    root = scratch if scratch is not None else SCRATCH
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        files = list(root.glob("bash_state_*.json"))
    except Exception:
        return
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except Exception:
            continue


_DETACHED_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_HEADS_PREFIX = "ref: refs/heads/"


def repo_state_from_files(cwd: str) -> tuple[str | None, str | None] | None:
    """Return ``(repo_root, branch)`` by reading ``.git`` and ``HEAD``, or None to ask git.

    post-tool-use-cwd-track.py runs this on every Bash call, and a git process
    there cost 263 ms at the median on this machine (dev-env#849 baseline), while
    these reads take 1-2 ms. The result is in the same form `git rev-parse
    --show-toplevel --abbrev-ref HEAD` gives (forward slashes, real path, a
    ``.git`` file followed for a worktree), checked against git on the canonical,
    worktrees, subdirectories and a junctioned path, because write_state and the
    drift checks compare it as an exact string. None for anything unusual (GIT_DIR
    set, a cwd inside ``.git``, an unreadable or non-branch HEAD), so git decides.
    ``(None, None)`` when no ``.git`` is found up to the root, as git reports.
    """
    if not cwd or os.environ.get("GIT_DIR") or os.environ.get("GIT_WORK_TREE"):
        return None
    try:
        start = Path(cwd).resolve()
        if ".git" in start.parts:
            return None
        for directory in (start, *start.parents):
            dot_git = directory / ".git"
            if dot_git.is_dir():
                git_dir = dot_git
            elif dot_git.is_file():
                text = dot_git.read_text(encoding="utf-8").strip()
                if not text.startswith("gitdir:"):
                    return None
                git_dir = Path(text[len("gitdir:"):].strip())
                if not git_dir.is_absolute():
                    git_dir = directory / git_dir
            else:
                continue
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if head.startswith(_HEADS_PREFIX):
                return directory.as_posix(), head[len(_HEADS_PREFIX):]
            if _DETACHED_SHA_RE.fullmatch(head):
                return directory.as_posix(), None
            return None
        return None, None
    except (OSError, ValueError):
        return None


def read_branch(repo: Path, run) -> str:
    """The branch *repo* has checked out, or ``DETACHED``, as resolve_current_branch gives it.

    Read from ``.git/HEAD`` without a process (dev-env#849); when that read cannot answer,
    *run* (the caller's own git runner, already bound to *repo*) runs
    ``git symbolic-ref --short HEAD``. Used for the every-prompt canonical checks in
    dev-env-sync.py and journal-canonical-guard.py.
    """
    state = repo_state_from_files(str(repo))
    if state is not None and state[0] is not None:
        return state[1] or DETACHED
    result = run(["git", "symbolic-ref", "--short", "HEAD"])
    return resolve_current_branch(result.returncode, result.stdout)


def current_repo_state(cwd: str) -> tuple[str | None, str | None]:
    """Return ``(repo_root, branch)`` for *cwd*, from the files when it can, else one git call.

    Both ``None`` for a non-git cwd or any subprocess failure/timeout;
    ``branch`` alone is ``None`` for a detached HEAD. Never raises.
    ``repo_state_from_files`` answers without a process in the common case
    (dev-env#849); the git call below is its fallback.

    Combines what were two separate ``git rev-parse --show-toplevel`` +
    ``git branch --show-current`` calls into one ``git rev-parse
    --show-toplevel --abbrev-ref HEAD`` invocation (two lines of output: the
    toplevel path, then the abbreviated ref — the literal string ``"HEAD"``
    when detached, which is mapped to ``None`` here to match
    ``git branch --show-current``'s empty-detached-HEAD convention). Halving
    the spawn count matters most for post-tool-use-cwd-track.py, the one
    caller of this function that runs on every Bash tool call rather than at
    a low-frequency checkpoint.
    """
    from_files = repo_state_from_files(cwd)
    if from_files is not None:
        return from_files
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd or None,
            timeout=5,
        )
        if result.returncode != 0:
            return None, None
        lines = result.stdout.splitlines()
        repo_root = lines[0].strip() if lines else ""
        branch_raw = lines[1].strip() if len(lines) > 1 else ""
        branch = None if not branch_raw or branch_raw == "HEAD" else branch_raw
        return (repo_root or None), branch
    except Exception:
        return None, None


def state_path(session_id: str, scratch: Path | None = None) -> Path:
    """Return ``scratch / f"bash_state_{session_id}.json"``.

    *scratch* overrides SCRATCH (used by tests)."""
    root = scratch if scratch is not None else SCRATCH
    safe = session_id or "unknown"
    return root / f"bash_state_{safe}.json"


def state_age_seconds(session_id: str, scratch: Path | None = None) -> float | None:
    """Return seconds since *session_id*'s state file was last written, or
    ``None`` if it doesn't exist (or can't be stat'd).

    Used by pre-bash-drift-check.py (dev-env#682) to gate its own git
    subprocess call on elapsed time rather than command content — cheap
    (a file stat, not a subprocess) on the common case of two Bash calls in
    quick succession. A future/skewed mtime is not special-cased here: it
    simply yields a negative float, which the caller's gating function treats
    the same as "too recent to check" (see ``pre-bash-drift-check.py``'s
    ``should_check_drift``). *scratch* overrides SCRATCH (used by tests)."""
    root = scratch if scratch is not None else SCRATCH
    try:
        mtime = state_path(session_id, scratch=root).stat().st_mtime
    except OSError:
        return None
    return time.time() - mtime


def write_state(
    session_id: str,
    repo_root: str | None,
    branch: str | None,
    cwd: str,
    scratch: Path | None = None,
) -> None:
    """Best-effort write of the current repo/branch/cwd for *session_id*.

    Swallows all I/O errors — this is an advisory side-channel, never a hard
    dependency for the calling hook. *scratch* overrides SCRATCH (used by
    tests)."""
    root = scratch if scratch is not None else SCRATCH
    try:
        root.mkdir(parents=True, exist_ok=True)
        state_path(session_id, scratch=root).write_text(
            json.dumps({"repo_root": repo_root, "branch": branch, "cwd": cwd}),
            encoding="utf-8",
        )
    except OSError:
        pass


def read_state(session_id: str, scratch: Path | None = None) -> dict | None:
    """Best-effort read of the last-recorded state for *session_id*.

    Returns ``None`` on a missing file, unreadable file (``OSError``), non-UTF-8
    bytes (``UnicodeDecodeError``), or malformed/non-dict JSON
    (``json.JSONDecodeError``) — a session's first Bash call (or a cleared/
    externally-corrupted scratch file) is not an error. Both decode failures are
    ``ValueError`` subclasses, so one ``except (OSError, ValueError)`` covers all
    three (mirrors ``dev-env-sync.py``'s ``read_failure_state``; the pre-fix
    ``OSError``-only catch let a non-UTF-8 file escape — dev-env#801).
    *scratch* overrides SCRATCH (used by tests)."""
    root = scratch if scratch is not None else SCRATCH
    try:
        raw = state_path(session_id, scratch=root).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def format_drift_warning(
    recorded: dict | None,
    current_repo_root: str | None,
    current_branch: str | None,
    current_cwd: str,
) -> str | None:
    """Pure: return a drift-warning string, or ``None`` if there's nothing to warn about.

    ``None`` when *recorded* is ``None`` (no prior state — first call of the
    session), when *both* current values are ``None`` (the checkpoint's own
    git read failed/timed out — nothing to meaningfully compare against, and
    firing here would falsely claim drift while displaying an unchanged cwd
    on both the "was" and "now" lines), or when ``(repo_root, branch)`` is
    unchanged. Otherwise returns a formatted multi-line warning naming both
    states so the reader can decide whether the change was intentional
    (EnterWorktree / cd) or a silent revert (dev-env#573)."""
    if not recorded:
        return None
    if current_repo_root is None and current_branch is None:
        return None
    recorded_repo = recorded.get("repo_root")
    recorded_branch = recorded.get("branch")
    if (recorded_repo, recorded_branch) == (current_repo_root, current_branch):
        return None
    recorded_cwd = recorded.get("cwd") or "<unknown>"
    return (
        "⚠ [cwd-drift] Since your last Bash call, the active repo/branch changed:\n"
        f"    was: {recorded_repo or '<unknown>'} @ {recorded_branch or '<unknown>'} (cwd: {recorded_cwd})\n"
        f"    now: {current_repo_root or '<unknown>'} @ {current_branch or '<unknown>'} (cwd: {current_cwd})\n"
        "  If this wasn't an intentional EnterWorktree/cd, STOP and verify with `pwd` "
        "and `git branch --show-current` before proceeding — see dev-env#573."
    )


def drift_warning_for(
    session_id: str, cwd: str, scratch: Path | None = None
) -> tuple[str | None, str | None, str | None]:
    """Convenience wrapper combining ``current_repo_state`` + ``read_state`` +
    ``format_drift_warning`` — the exact three-call sequence that was
    duplicated verbatim across all four PreToolUse(Bash) consumers of this
    module (dev-env#682 `/review`: the same class of divergence risk this
    module's own consolidation of ``current_repo_state`` was originally meant
    to close, see the module docstring, had crept back in one layer up).

    Returns ``(repo_root, branch, drift_warning)`` — ``repo_root``/``branch``
    for a caller's own "current branch/repo" display (three of the four
    consumers show this regardless of drift), and ``drift_warning`` (``None``
    when there's nothing to warn about, see ``format_drift_warning``).
    *session_id* may be empty — ``drift_warning`` is then always ``None``,
    matching each consumer's own prior ``if session_id:`` guard; ``cwd`` is
    still resolved either way, since every consumer needs it independent of
    whether a session record exists. *scratch* overrides SCRATCH (used by
    tests) and is forwarded to ``read_state`` only — no real caller passes
    it; it exists purely so tests can inject a recorded state without
    touching ``~/.claude/scratch``."""
    repo_root, branch = current_repo_state(cwd)
    drift_warning = None
    if session_id:
        recorded = read_state(session_id, scratch=scratch)
        drift_warning = format_drift_warning(recorded, repo_root, branch, cwd)
    return repo_root, branch, drift_warning
