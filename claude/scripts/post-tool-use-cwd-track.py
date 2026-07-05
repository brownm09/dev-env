#!/usr/bin/env python3
"""Claude Code PostToolUse hook — records the current repo root + branch
after every Bash call, for dev-env#573's cwd/branch drift detection.

dev-env#573: a session's Bash cwd (and separately, a checked-out branch) can
silently revert to a stale/default state with no error surfaced — most
likely tied to an intermittent Git Bash (MSYS2) crash under resource
pressure. This hook cannot detect or prevent that crash (it lives in Claude
Code's own harness / in MSYS2, outside this repo); instead it maintains the
per-session "last known repo + branch" marker that
pre-commit-branch-check.py, pre-pr-create-check.py, and
pre-merge-branch-check.py read to flag a mismatch at the moment a
consequential command runs.

Best-effort only: a cwd that isn't inside a git repo (or a `git` call that
fails/times out) simply records repo_root/branch as None rather than
raising — the next comparison then sees "no git state" rather than crashing
this hook.

Also opportunistically sweeps state files older than
_bash_state.MAX_AGE_DAYS on every call — this hook is the only place a state
file is ever written, so it is the natural place to also expire them
(matches every other per-session file in this codebase, e.g. _hookutil.py's
sentinel cleanup).

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", ...},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 always — pure side-channel recording, never blocks or messages.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import sys

import _bash_state


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    session_id = data.get("session_id", "") or ""
    cwd = data.get("cwd", "") or ""
    if not session_id or not cwd:
        sys.exit(0)

    repo_root, branch = _bash_state.current_repo_state(cwd)
    _bash_state.write_state(session_id, repo_root, branch, cwd)
    _bash_state.cleanup_stale_state()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: this hook must never block or surface an error.
        sys.exit(0)
