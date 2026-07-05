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

Usage:
    import _bash_state

    _bash_state.write_state(session_id, repo_root, branch, cwd)
    recorded = _bash_state.read_state(session_id)
    warning = _bash_state.format_drift_warning(recorded, repo_root, branch, cwd)
"""
from __future__ import annotations

import json
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"


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
    session) or when ``(repo_root, branch)`` is unchanged. Otherwise returns a
    formatted multi-line warning naming both states so the reader can decide
    whether the change was intentional (EnterWorktree / cd) or a silent
    revert (dev-env#573)."""
    if not recorded:
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
