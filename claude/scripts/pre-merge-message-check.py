#!/usr/bin/env python3
"""Claude Code PreToolUse hook — user message queue check before gh pr merge.

Reads C:/Users/brown/.claude/merge-queue.md before any `gh pr merge` command.
If the file has non-whitespace content, the merge is blocked (exit 2) and the
queued messages are surfaced on stderr so Claude can act on them.

Intended use: in bypass / autonomous sessions where the user cannot interrupt
to deliver feedback mid-run, they write to merge-queue.md. The hook ensures
that feedback is seen and acted upon at the natural merge checkpoint, before
the merge executes. Claude clears the queue after acknowledging the messages,
then re-attempts the merge.

Fails OPEN: any I/O or JSON error exits 0 so a misconfigured queue file never
permanently wedges a merge.

Stdin JSON shape (PreToolUse): {"tool_name":"Bash","tool_input":{"command":...},"cwd":...}

Exit 2 — block the merge and show queued messages (queue has content).
Exit 0 — allow (queue absent, empty, whitespace-only, or any error).
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import sys

_QUEUE_FILE = "C:/Users/brown/.claude/merge-queue.md"
_GH_PR_MERGE_RE = re.compile(r"(?:^|&&|\|+|;|\n)\s*gh\s+pr\s+merge\b")


def _read_queue():
    """Return queue file content, or '' on any error."""
    try:
        with open(_QUEUE_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


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
    if not _GH_PR_MERGE_RE.search(command):
        sys.exit(0)

    content = _read_queue().strip()
    if not content:
        sys.exit(0)

    sys.stderr.write(
        "[merge-queue] BLOCKED: the user has queued a message for you to read "
        "before this merge.\n\n"
        "=== Queued message ===\n"
        f"{content}\n"
        "=== End of message ===\n\n"
        f"Read the message above, act on it, then clear {_QUEUE_FILE} "
        "(write an empty file or delete its contents) and re-run `gh pr merge`.\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
