#!/usr/bin/env python3
"""PostToolUse/Bash hook — set a sentinel flag after a stub is pushed to
engineering-journal so the Stop hook can remind Claude to archive the session.

Fires on every Bash tool call. Most calls are skipped quickly:
  1. Command must contain a top-level `git push` invocation (scan_top_level-
     anchored — not text inside a heredoc body, a quoted argument, or a $()
     subshell)
  2. Command must reference the engineering-journal repo
  3. Push must have succeeded (no error output)
  4. Most-recent commit in engineering-journal must touch a .stub.md file

When all four conditions are met, writes a sentinel file to the scratch
directory. The Stop hook (journal-stop-check.py) reads and clears the
sentinel and issues a closing reminder via stdout — the correct output
channel for Stop hook messages.

Exit 0 on every code path — never blocks.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", ...},
    "tool_response": {"stdout": "...", "stderr": "..."}  # NOT "output" — ADR-049
  }
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import subprocess
import sys
from pathlib import Path

from _hookio import read_command_output, scan_top_level

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL = Path.home() / ".claude" / "scratch" / "stub-pushed.flag"

# Anchored top-level match — identical to pr-merge-reminder.py's
# _check_push_stmt / is_git_push_command (ADR-050 Amendments 5/6/10).
_PUSH_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?git\s+push\b")


def _check_push_stmt(token: str) -> bool:
    return bool(_PUSH_RE.match(token.lstrip()))


def is_git_push_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `git push`.

    Anchored via `scan_top_level` rather than a raw substring test, so
    `git push` text inside a heredoc body, a quoted argument, or a `$()`
    subshell does not count as an invocation — matching the pattern used in
    usage-snapshot.py / pr-merge-reminder.py / post-pr-merge-project.py /
    post-merge-tile-checkpoint.py / post-pr-merge-pull.py /
    post-pr-merge-reclaim.py (dev-env#532, ADR-050 Amendment 10).
    """
    return scan_top_level(command, _check_push_stmt)


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


def has_push_error(output: str) -> bool:
    """Return True if push output shows an obvious failure.

    git reports push failures with `error:` / `fatal:` lines on stderr. Before
    #380 this guard read the legacy `output` field, which is always empty on the
    real payload, so the guard was a no-op — a failed journal push could still
    arm the archive reminder. The shared `read_command_output` helper now feeds
    it the real stdout/stderr.
    """
    lower = output.lower()
    return "error:" in lower or "fatal:" in lower


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    command = (data.get("tool_input") or {}).get("command", "")
    output = read_command_output(data)

    # Must be a git push
    if not is_git_push_command(command):
        sys.exit(0)

    # Must reference engineering-journal
    if "engineering-journal" not in command and "engineering_journal" not in command:
        sys.exit(0)

    # Must not show an obvious error
    if has_push_error(output):
        sys.exit(0)

    # Confirm the pushed commit contains a stub file
    if not most_recent_commit_has_stub(JOURNAL_REPO):
        sys.exit(0)

    # Write sentinel — the Stop hook will consume it and issue the reminder
    try:
        SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        SENTINEL.write_text("1")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
