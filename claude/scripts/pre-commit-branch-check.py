#!/usr/bin/env python3
"""Claude Code PreToolUse hook — detects 'git commit' in Bash commands and
emits a systemMessage showing the current branch as a visible checkpoint.

Does NOT block the commit (exit 0). The message appears in the Claude Code UI
so the user can catch wrong-branch commits before they land.

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
import json
import re
import subprocess
import sys

# Matches `git commit` as an actual command invocation, not inside a string or
# after --message / -m (where "commit" would be a flag argument value).
_GIT_COMMIT_RE = re.compile(
    r"(?:^|&&|\|\||;|\n)\s*(?:cd\s+\S+\s+&&\s+)?git\s+commit\b"
)


def is_git_commit_command(command: str) -> bool:
    return bool(_GIT_COMMIT_RE.search(command))


def current_branch(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            cwd=cwd or None,
            timeout=5,
        )
        branch = result.stdout.strip()
        return branch if branch else "<detached HEAD>"
    except Exception:
        return "<unknown>"


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
    branch = current_branch(cwd)

    print(json.dumps({"systemMessage": f"[branch-check] committing to: {branch}"}))
    sys.exit(0)


if __name__ == "__main__":
    main()
