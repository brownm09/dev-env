#!/usr/bin/env python3
"""
session-mode-prompt.py

On the first user prompt of a new session, block and ask the user to confirm
their permission mode. Addresses sessions spawned from an active bypass-permissions
session that may have inherited the wrong defaultMode from settings.json.

Per-session marker file at scratch/session_mode_ack_<session_id>.txt records
that the banner has been shown for this session; any subsequent prompt in the
same session passes through silently. Markers from other sessions are not
read, so a banner in session A does not suppress the banner in session B.
Old markers from closed sessions are orphaned but harmless (~18 bytes each).

Suppressed for automated sessions whose prompt begins with an XML tag (e.g.
<scheduled-task>, <ci-monitor-event>). These are machine-generated triggers
where no human is present to answer a blocking prompt.

Output: the banner is written to **stderr**, not stdout. Per Claude Code's
UserPromptSubmit hook contract, exit-2 blocks the prompt and stderr is what
gets surfaced to the user; stdout is silently fed back to the model as added
context. The original stdout-based version was invisible to humans.
See dev-env#264.

Debug logging: every invocation appends one JSON line to
C:/Users/brown/.claude/scratch/session-mode-prompt.log so silent failures can
be diagnosed without re-instrumenting the hook. See dev-env#261.
"""

import json
import os
import re
import sys
import time
import traceback

MARKER_DIR = "C:/Users/brown/.claude/scratch"
LOG_PATH = "C:/Users/brown/.claude/scratch/session-mode-prompt.log"

# Automated triggers use XML-tagged prompts; human prompts never start with <tag>.
# Matches lowercase-initial tags only — all current triggers use lowercase; update if that changes.
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")

# session_id is a UUID from Claude Code; sanitize defensively in case the contract changes.
_SAFE_SESSION_ID = re.compile(r"[^A-Za-z0-9_-]")


def _marker_path(session_id, event=None):
    """Return the per-session marker path. Falls back to 'unknown' when session_id is missing.

    When the fallback fires, set event["fallback_marker"]=True so a future Claude Code
    contract change that drops session_id is visible in scratch/session-mode-prompt.log
    rather than silently re-introducing cross-session contamination via session_mode_ack_unknown.txt.
    """
    safe = _SAFE_SESSION_ID.sub("", session_id or "")
    if not safe:
        safe = "unknown"
        if event is not None:
            event["fallback_marker"] = True
    return f"{MARKER_DIR}/session_mode_ack_{safe}.txt"


def _log(event):
    """Append one JSON line to the log. Never raise."""
    try:
        event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def main():
    event = {"stage": "start"}

    try:
        raw = sys.stdin.read()
    except Exception as e:
        event["stage"] = "stdin_read_failed"
        event["error"] = repr(e)
        _log(event)
        sys.exit(0)

    try:
        data = json.loads(raw)
    except Exception as e:
        event["stage"] = "json_parse_failed"
        event["error"] = repr(e)
        event["raw_len"] = len(raw)
        _log(event)
        sys.exit(0)

    now = time.time()
    session_id = data.get("session_id", "")
    prompt = data.get("prompt", "")
    event["session_id"] = session_id
    event["prompt_prefix"] = prompt[:80]
    event["permission_mode"] = data.get("permission_mode", "")

    marker_path = _marker_path(session_id, event)
    event["marker_path"] = marker_path
    marker_exists = os.path.exists(marker_path)
    event["marker_exists"] = marker_exists

    # If the banner was already shown for THIS session, pass through silently.
    if marker_exists:
        event["stage"] = "session_acked_passthrough"
        event["exit"] = 0
        _log(event)
        sys.exit(0)

    # Automated sessions (scheduled tasks, CI monitors, etc.) — no human present to answer.
    if _AUTOMATED_PREFIX.match(prompt):
        event["stage"] = "automated_suppressed"
        event["exit"] = 0
        _log(event)
        sys.exit(0)

    # Write marker BEFORE blocking so the user's re-submit passes through.
    try:
        with open(marker_path, "w") as f:
            f.write(str(now))
        event["marker_written"] = True
    except Exception as e:
        event["marker_write_error"] = repr(e)
        sys.stderr.write(f"session-mode-prompt: could not write marker: {e}\n")

    banner = (
        "-------------------------------------------------\n"
        "New session -- confirm your permission mode:\n"
        "\n"
        "  plan       Claude asks before making any edits  (settings default)\n"
        "  bypass     Claude acts immediately without asking\n"
        "  auto       Claude decides based on task risk\n"
        "\n"
        "Press Shift+Tab to cycle modes if needed,\n"
        "then re-submit your prompt to continue.\n"
        "-------------------------------------------------\n"
    )

    # Write to stderr — Claude Code surfaces stderr (not stdout) to the user on exit 2.
    try:
        sys.stderr.write(banner)
        sys.stderr.flush()
        event["stage"] = "banner_printed"
        event["exit"] = 2
        _log(event)
    except Exception as e:
        event["stage"] = "banner_print_failed"
        event["error"] = repr(e)
        event["traceback"] = traceback.format_exc()
        event["exit"] = 2
        _log(event)
        sys.stderr.write(f"session-mode-prompt: banner print failed: {e}\n")

    sys.exit(2)


main()
