#!/usr/bin/env python3
"""Claude Code PostToolUse hook — records the current repo root + branch
after every Bash call, for dev-env#573's cwd/branch drift detection.

dev-env#573: a session's Bash cwd (and separately, a checked-out branch) can
silently revert to a stale/default state with no error surfaced — most
likely tied to an intermittent Git Bash (MSYS2) crash under resource
pressure. This hook cannot detect or prevent that crash (it lives in Claude
Code's own harness / in MSYS2, outside this repo); instead it maintains the
per-session "last known repo + branch" marker that
pre-commit-branch-check.py and pre-pr-create-check.py read to flag a
mismatch at the moment a consequential command runs.

Best-effort only: a cwd that isn't inside a git repo (or a `git` call that
fails/times out) simply records repo_root/branch as None rather than
raising — the next comparison then sees "no git state" rather than crashing
this hook.

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
import subprocess
import sys

import _bash_state


def _repo_root(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=cwd or None,
            timeout=5,
        )
        root = result.stdout.strip()
        return root if result.returncode == 0 and root else None
    except Exception:
        return None


def _branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            cwd=cwd or None,
            timeout=5,
        )
        branch = result.stdout.strip()
        return branch if result.returncode == 0 and branch else None
    except Exception:
        return None


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

    repo_root = _repo_root(cwd)
    branch = _branch(cwd) if repo_root else None
    _bash_state.write_state(session_id, repo_root, branch, cwd)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: this hook must never block or surface an error.
        sys.exit(0)
