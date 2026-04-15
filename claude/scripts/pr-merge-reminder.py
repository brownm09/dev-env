#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh pr merge' in Bash commands and
emits a journal-update reminder via stderr (exit code 2) so Claude sees it.

Matches only actual CLI invocations of `gh pr merge`, not the string appearing
inside commit messages, heredocs, or other quoted arguments.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"output": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0  — not a gh pr merge; no action
Exit 2  — gh pr merge detected; reminder emitted via stderr
"""
import json
import re
import sys

# Matches `gh pr merge` at the start of a sub-command (after shell operators
# or at the very beginning of the command string). This avoids false positives
# from commit messages or heredoc content that happen to contain the phrase.
_GH_PR_MERGE_RE = re.compile(
    r"(?:^|&&|\|\||;|\n)\s*(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b"
)


def is_pr_merge_command(command: str) -> bool:
    return bool(_GH_PR_MERGE_RE.search(command))


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
    if not is_pr_merge_command(command):
        sys.exit(0)

    cwd = data.get("cwd", "<unknown>")
    print(
        "[journal-reminder] gh pr merge detected — update the engineering journal now:\n"
        f"  cwd: {cwd}\n"
        "  1. Identify the project journal path from cwd.\n"
        "  2. Check out or create the draft branch in engineering-journal.\n"
        "  3. Append a <!-- session: <slug> --> block documenting this PR merge.\n"
        "  4. Add token comment and <!-- next-session-context --> paragraph.\n"
        '  5. git commit -m "draft: YYYY-MM-DD session N" && git push',
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
