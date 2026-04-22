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


def _find_heredoc_end(cmd: str, start: int) -> int:
    """start = index of first '<' in '<<…'. Returns index just past the heredoc body.

    Handles <<DELIM, <<'DELIM', <<"DELIM", and <<-DELIM (tab-stripping) forms.
    """
    n = len(cmd)
    i = start + 2  # skip '<<'
    strip_tabs = False
    if i < n and cmd[i] == "-":
        strip_tabs = True
        i += 1
    # Read delimiter — may be wrapped in ' or "
    quote: str | None = None
    if i < n and cmd[i] in ("'", '"'):
        quote = cmd[i]
        i += 1
    stop_chars = "\n\r" + (quote or "")
    delim_start = i
    while i < n and cmd[i] not in stop_chars:
        i += 1
    delimiter = cmd[delim_start:i]
    if quote and i < n and cmd[i] == quote:
        i += 1  # skip closing quote
    # Skip to end of the <<… declaration line
    while i < n and cmd[i] not in ("\n", "\r"):
        i += 1
    if i < n:
        i += 1  # skip newline
    # Scan lines until we find the terminator
    while i < n:
        line_start = i
        if strip_tabs:
            while i < n and cmd[i] == "\t":
                i += 1
            line_start = i
        while i < n and cmd[i] not in ("\n", "\r"):
            i += 1
        if cmd[line_start:i] == delimiter:
            if i < n:
                i += 1  # skip terminator's newline
            return i
        if i < n:
            i += 1  # skip newline
    return i


def is_pr_merge_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr merge`
    invocation — i.e. not inside a quoted string, $() subshell, or heredoc body.

    Uses a stack-based parser with four states ('top', 'single', 'double',
    'subshell') so that shell operators buried inside quoted arguments, command
    substitutions, or heredoc content are never mistaken for top-level statement
    separators.  Specifically handles:
    - Single/double quotes
    - $() subshells (including $() inside "…")
    - <<DELIM / <<'DELIM' heredoc bodies
    """
    n = len(command)
    i = 0
    stmt_start = 0
    # Stack entries: 'top' | 'single' | 'double' | 'subshell'
    stack = ["top"]

    while i < n:
        c = command[i]
        state = stack[-1]

        if state == "single":
            if c == "'":
                stack.pop()

        elif state == "double":
            if c == "\\" and i + 1 < n:
                i += 1  # skip escaped char
            elif c == '"':
                stack.pop()
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                # $() inside "…" — track subshell so its content is opaque
                stack.append("subshell")
                i += 1  # skip '('

        elif state == "subshell":
            if c == ")":
                stack.pop()
            elif c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "(":
                stack.append("subshell")
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                # heredoc inside subshell — skip body entirely
                i = _find_heredoc_end(command, i)
                continue

        else:  # state == 'top'
            if c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                i = _find_heredoc_end(command, i)
                continue
            elif c in (";", "\n"):
                if _check_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 1
            elif c == "&" and i + 1 < n and command[i + 1] == "&":
                if _check_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1
            elif c == "|" and i + 1 < n and command[i + 1] == "|":
                if _check_stmt(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1

        i += 1

    if stack == ["top"]:
        return _check_stmt(command[stmt_start:])
    return False


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
