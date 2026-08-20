#!/usr/bin/env python3
"""Claude Code PostToolUse hook — advisory reminder when a durable memory file
is written without a link that immortalizes it into the repo instructions.

Fires only for the Write tool, only for a `.md` file inside a `.../memory/`
directory (excluding the MEMORY.md index), and only when the written body lacks
any immortalization link — a GitHub issue/PR ref (`#123`), an ADR ref
(`ADR-048`), the string `CLAUDE.md`, or the phrase "Documented in repo". When
all of those hold it emits a one-line stderr reminder and exits 2 so Claude sees
it and files the issue. The tool has already run, so exit 2 here *surfaces* the
reminder — it does not block the write (same convention as post-tool-use.py and
pr-merge-reminder.py). Every other case exits 0 silently.

Per ADR-048 (extends ADR-038): a durable user/feedback/project memory must be
paired, in the same session, with a GitHub issue that drives it into the
instructions (CLAUDE.md / project docs), linked from the memory body and the
MEMORY.md pointer. Transient/session-local notes are exempt — the link-absence
heuristic keeps this nudge quiet on writes that already carry a link, and the
agent (not this hook) judges whether a given note is durable.

The link check is an intentionally permissive proxy (plain substring / loose
regex), so it errs toward staying silent — the safe direction for a non-blocking
nudge. Do not "tighten" it into a stricter check: a missed nudge is harmless (the
agent still follows the rule), whereas a noisier one trains the reader to ignore it.

Stdin JSON shape (PostToolUse):
  {"tool_name": "Write", "tool_input": {"file_path": "...", "content": "..."}, ...}

Exit 0 — not a relevant write, or the memory body already carries a link; silent.
Exit 2 — durable memory written with no immortalization link; reminder on stderr.
"""
import json
import re
import sys

from _hookio import read_tool_input_field
import _hookutil

# An immortalization link anywhere in the written body suppresses the nudge.
# Intentionally permissive (substring / loose regex): over-suppression is the safe
# direction for a non-blocking advisory — see the module docstring before tightening.
_LINK_PATTERNS = (
    re.compile(r"#\d+"),            # GitHub issue / PR reference
    re.compile(r"ADR-\d+", re.I),  # ADR reference
)
_LINK_SUBSTRINGS = ("CLAUDE.md", "Documented in repo")


def should_advise_memory_write(tool_name: str, file_path: str, content: str) -> bool:
    """True when a durable memory file is written with no immortalization link.

    Pure and offline so it can be unit-tested without a Claude Code session.
    The hook never decides whether a note is *durable* — it only flags the
    absence of any link, leaving the durability judgment to the agent.
    """
    if tool_name != "Write":
        return False
    norm = (file_path or "").replace("\\", "/")
    if "/memory/" not in norm:
        return False
    base = norm.rsplit("/", 1)[-1]
    if base == "MEMORY.md" or not base.endswith(".md"):
        return False
    body = content or ""
    if any(p.search(body) for p in _LINK_PATTERNS):
        return False
    if any(s in body for s in _LINK_SUBSTRINGS):
        return False
    return True


ADVISORY = (
    "[memory-hook] A memory file was written with no link that immortalizes it "
    "into the instructions.\n"
    "  If this is a durable user/feedback/project rule, in this same session:\n"
    "    1. file a GitHub issue whose job is to port it into the instructions "
    "(CLAUDE.md / project docs),\n"
    "    2. link that issue from both the memory body and the MEMORY.md pointer,\n"
    "    3. prefer to make the instruction edit now and close the issue.\n"
    "  Transient/session-local notes are exempt. See ADR-048 (extends ADR-038)."
)


def main() -> None:
    _hookutil.record_heartbeat("memory-write-advisory")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        # A valid-JSON-but-non-dict top-level payload (a list, string, number,
        # or null) would otherwise crash the very next line (dev-env#1031/
        # #1033, mirroring usage-snapshot.py's dev-env#1028 post-review fix).
        sys.exit(0)

    # dev-env#1031/#1033 (/review finding on PR #1035): read_tool_input_field()
    # never raises on a present-but-non-dict tool_input (dev-env#1028's
    # payload shape). The pre-fix `data.get("tool_input", {}) or {}` chain
    # only substitutes `{}` for a FALSY non-dict tool_input (None, "", 0) --
    # a TRUTHY non-dict value (e.g. a non-empty string) survives `or {}`
    # unchanged and crashes on the next `.get()`. This hook reads TWO
    # tool_input fields (file_path, content), not just "command" -- the
    # motivating second caller that got `read_tool_input_field` hoisted out
    # of `pre-tool-use-worktree-path-check.py`'s own former local copy and
    # into `_hookio.py` as a shared helper.
    if should_advise_memory_write(
        data.get("tool_name", ""),
        read_tool_input_field(data, "file_path"),
        read_tool_input_field(data, "content"),
    ):
        print(ADVISORY, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # safe-exit guard: never block or crash a Write
        sys.exit(0)
