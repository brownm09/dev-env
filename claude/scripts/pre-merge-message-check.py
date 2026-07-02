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

Merge detection is built on `_hookio.scan_top_level` (dev-env#519), the same
quote/subshell/heredoc-aware engine `pre-merge-numbering-check.py` and
`pr-merge-reminder.py` already use — not a plain unanchored `re.search` over
the whole command string, which could spuriously fire on a `gh pr merge`
mentioned only inside a heredoc body or `$()` subshell (dev-env#499).

Stdin JSON shape (PreToolUse): {"tool_name":"Bash","tool_input":{"command":...},"cwd":...}

Exit 2 — block the merge and show queued messages (queue has content).
Exit 0 — allow (queue absent, empty, whitespace-only, or any error).
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import sys

from _hookio import scan_top_level

_QUEUE_FILE = "C:/Users/brown/.claude/merge-queue.md"
_MERGE_STMT_RE = re.compile(r"gh\s+pr\s+merge\b")


def _check_merge_stmt(token):
    return bool(_MERGE_STMT_RE.match(token.lstrip()))


def is_pr_merge_command(command):
    """True iff *command* contains a top-level `gh pr merge` -- i.e. not one
    merely mentioned inside a quoted string, $() subshell, or heredoc body
    (dev-env#499). Mirrors `pre-merge-numbering-check.py`'s identically-named
    predicate (dev-env#519).
    """
    return scan_top_level(command, _check_merge_stmt)


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
    if not is_pr_merge_command(command):
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
