#!/usr/bin/env python3
"""Claude Code PreToolUse hook — detects 'git commit' in Bash commands and
emits a systemMessage showing the current branch as a visible checkpoint.

Does NOT block the commit (exit 0). The message appears in the Claude Code UI
so the user can catch wrong-branch commits before they land.

Also appends a drift warning (dev-env#573) when the repo/branch recorded by
post-tool-use-cwd-track.py after the session's last Bash call differs from
the repo/branch at commit time — a signal that the session's tracked cwd may
have silently reverted (e.g. after an intermittent Git Bash crash) between
that call and this one. Advisory only; never blocks.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 — always; hook is advisory only.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import subprocess
import sys

import _bash_state

# Matches `git commit` as an actual command invocation, not inside a string or
# after --message / -m (where "commit" would be a flag argument value).
_GIT_COMMIT_RE = re.compile(
    r"(?:^|&&|\|\||;|\n)\s*(?:cd\s+\S+\s+&&\s+)?git\s+commit\b"
)


def is_git_commit_command(command: str) -> bool:
    return bool(_GIT_COMMIT_RE.search(command))


def current_branch(cwd: str) -> str | None:
    """Return the current branch, or None for a detached HEAD / git failure.

    Returning None (rather than a display placeholder) here matters:
    post-tool-use-cwd-track.py's writer also records None for this same
    case, so the drift comparison in main() stays apples-to-apples — a
    string placeholder here would otherwise never equal the recorded None
    and manufacture a spurious drift warning on every detached-HEAD commit.
    build_message() maps None to a display placeholder for the printed line.
    """
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


def current_repo_root(cwd: str) -> str | None:
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


def build_message(branch: str | None, drift_warning: str | None) -> str:
    display_branch = branch if branch is not None else "<detached HEAD or unknown>"
    message = f"[branch-check] committing to: {display_branch}"
    if drift_warning:
        message += "\n" + drift_warning
    return message


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

    command = data.get("tool_input", {}).get("command", "")
    if not is_git_commit_command(command):
        sys.exit(0)

    cwd = data.get("cwd", "")
    session_id = data.get("session_id", "") or ""
    branch = current_branch(cwd)
    repo_root = current_repo_root(cwd)

    drift_warning = None
    if session_id:
        recorded = _bash_state.read_state(session_id)
        drift_warning = _bash_state.format_drift_warning(recorded, repo_root, branch, cwd)

    print(json.dumps({"systemMessage": build_message(branch, drift_warning)}))
    sys.exit(0)


if __name__ == "__main__":
    main()
