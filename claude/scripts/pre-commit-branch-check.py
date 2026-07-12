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
import sys

import _bash_state

# Matches `git commit` as an actual command invocation, not inside a string or
# after --message / -m (where "commit" would be a flag argument value).
_GIT_COMMIT_RE = re.compile(
    r"(?:^|&&|\|\||;|\n)\s*(?:cd\s+\S+\s+&&\s+)?git\s+commit\b"
)


def is_git_commit_command(command: str) -> bool:
    return bool(_GIT_COMMIT_RE.search(command))


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
    _, branch, drift_warning = _bash_state.drift_warning_for(session_id, cwd)

    print(json.dumps({"systemMessage": build_message(branch, drift_warning)}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
