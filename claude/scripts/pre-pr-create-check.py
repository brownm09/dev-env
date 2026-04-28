#!/usr/bin/env python3
"""Claude Code PreToolUse hook — detects 'gh pr create' in Bash commands and
emits a systemMessage checklist requiring test verification before the PR lands.

Enforcement model (layered):
  - This hook is advisory only (always exits 0). It fires a systemMessage
    reminder visible in the Claude Code UI.
  - Hard enforcement is handled by CLAUDE.md instruction: Claude must not
    run `gh pr create` until the project's ## Testing section is satisfied.
    The hook is a secondary reminder, not the gate.

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
import sys

_GH_PR_CREATE_RE = re.compile(
    r"(?:^|&&|\|+|;|\n)\s*gh\s+pr\s+create\b"
)


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
    if not _GH_PR_CREATE_RE.search(command):
        sys.exit(0)

    checklist = (
        "[pre-pr-check] Before this PR is created, confirm:\n"
        "  1. Ran the project test command (see ## Testing in project CLAUDE.md)\n"
        "  2. Tests passed (or documented why they are not applicable)\n"
        "  3. PR body includes what was tested and the outcome"
    )
    print(json.dumps({"systemMessage": checklist}))
    sys.exit(0)


if __name__ == "__main__":
    main()
