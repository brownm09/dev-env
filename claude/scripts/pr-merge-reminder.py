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

# Matches the start of a statement token (after leading whitespace and an
# optional `cd dir &&` prefix) against `gh pr merge`.
_STMT_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")


def _check_stmt(token: str) -> bool:
    return bool(_STMT_RE.match(token.lstrip()))


def is_pr_merge_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr merge`
    invocation — i.e. not inside a quoted string or heredoc body.

    Walks the command character-by-character, tracking single/double-quote
    depth so that shell operators (&&, ||, ;, newline) found inside quoted
    arguments are treated as literal text rather than statement boundaries.
    This prevents false positives when `gh issue create` is called with a
    --body argument whose text happens to contain the phrase `gh pr merge`.
    """
    i = 0
    n = len(command)
    stmt_start = 0
    in_single = False
    in_double = False

    while i < n:
        c = command[i]

        if in_single:
            if c == "'":
                in_single = False
        elif in_double:
            if c == "\\" and i + 1 < n:
                i += 1  # skip escaped character
            elif c == '"':
                in_double = False
        else:
            if c == "'":
                in_single = True
            elif c == '"':
                in_double = True
            elif c in (";", "\n"):
                if _check_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 1
            elif c == "&" and i + 1 < n and command[i + 1] == "&":
                if _check_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1  # skip second &
            elif c == "|" and i + 1 < n and command[i + 1] == "|":
                if _check_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1  # skip second |
        i += 1

    return _check_stmt(command[stmt_start:])


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
