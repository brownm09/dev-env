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
called on every single Bash tool call) and the three checkpoint hooks
(pre-commit-branch-check.py, pre-pr-create-check.py, pre-merge-branch-check.py)
rather than each defining its own git-wrapping helper. Originally duplicated
across all four call sites; consolidated here after the duplication already
caused one bug during development (a display-placeholder return value in one
copy that didn't match the others' ``None`` convention, which would have
manufactured spurious drift warnings on every detached-HEAD commit).

Usage:
    import _bash_state

    repo_root, branch = _bash_state.current_repo_state(cwd)
    _bash_state.write_state(session_id, repo_root, branch, cwd)
    _bash_state.cleanup_stale_state()
    recorded = _bash_state.read_state(session_id)
    warning = _bash_state.format_drift_warning(recorded, repo_root, branch, cwd)
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import _winsubp  # noqa: F401  -- suppress console windows on Windows

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


def current_repo_state(cwd: str) -> tuple[str | None, str | None]:
    """Return ``(repo_root, branch)`` for *cwd* via a single git subprocess call.

    Both ``None`` for a non-git cwd or any subprocess failure/timeout;
    ``branch`` alone is ``None`` for a detached HEAD. Never raises.

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

    Returns ``None`` on a missing file, unreadable file, or malformed/non-dict
    JSON — a session's first Bash call (or a cleared scratch dir) is not an
    error. *scratch* overrides SCRATCH (used by tests)."""
    root = scratch if scratch is not None else SCRATCH
    try:
        raw = state_path(session_id, scratch=root).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
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
