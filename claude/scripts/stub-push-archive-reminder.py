#!/usr/bin/env python3
"""PostToolUse/Bash hook — remind Claude to archive the session after a stub
is pushed to engineering-journal.

Fires on every Bash tool call. Most calls are skipped quickly:
  1. Command must contain "git push"
  2. Command must reference the engineering-journal repo
  3. Push must have succeeded (no error output)
  4. Most-recent commit in engineering-journal must touch a .stub.md file

When all four conditions are met, exits 2 with a systemMessage prompting
Claude to call ccd_session_mgmt__archive_session before stopping.

Exit 0 on every non-trigger path and on any exception — never blocks.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", ...},
    "tool_response": {"output": "...", ...}
  }
"""
import json
import subprocess
import sys
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"


def most_recent_commit_has_stub(repo: Path) -> bool:
    """Return True if HEAD commit in the repo touches at least one .stub.md file."""
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and any(
            line.endswith(".stub.md") for line in result.stdout.splitlines()
        )
    except Exception:
        return False


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    command = (data.get("tool_input") or {}).get("command", "")
    output = str((data.get("tool_response") or {}).get("output", ""))

    # Must be a git push
    if "git push" not in command:
        sys.exit(0)

    # Must reference engineering-journal
    if "engineering-journal" not in command and "engineering_journal" not in command:
        sys.exit(0)

    # Must not show an obvious error
    lower_output = output.lower()
    if "error:" in lower_output or "fatal:" in lower_output:
        sys.exit(0)

    # Confirm the pushed commit contains a stub file
    if not most_recent_commit_has_stub(JOURNAL_REPO):
        sys.exit(0)

    msg = (
        "Stub committed and pushed to engineering-journal. "
        "Archive this session now: call ccd_session_mgmt__archive_session "
        "(use list_sessions to look up the current session_id if needed). "
        "Then stop."
    )
    print(json.dumps({"systemMessage": msg}))
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
