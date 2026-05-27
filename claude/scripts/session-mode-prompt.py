#!/usr/bin/env python3
"""
session-mode-prompt.py

On the first user prompt of a new session, block and ask the user to confirm
their permission mode. Addresses sessions spawned from an active bypass-permissions
session that may have inherited the wrong defaultMode from settings.json.

Uses a short-lived marker file (2-minute cooldown) so the re-submit after the
user reviews/adjusts the mode passes through without blocking again.

Suppressed for automated sessions whose prompt begins with an XML tag (e.g.
<scheduled-task>, <ci-monitor-event>). These are machine-generated triggers
where no human is present to answer a blocking prompt.

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
MARKER_PREFIX = "session_mode_ack_"
LOG_PATH = "C:/Users/brown/.claude/scratch/session-mode-prompt.log"
COOLDOWN_SECS = 120  # covers the re-submit window after user sees the prompt

# Automated triggers use XML-tagged prompts; human prompts never start with <tag>.
# Matches lowercase-initial tags only — all current triggers use lowercase; update if that changes.
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")


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
    session_id = data.get("session_id", "unknown")
    event["session_id"] = session_id
    prompt = data.get("prompt", "")
    event["prompt_prefix"] = prompt[:80]
    event["permission_mode"] = data.get("permission_mode", "")

    # Per-session marker: each session gets its own cooldown file so sessions
    # opening within COOLDOWN_SECS of each other don't suppress each other's banners.
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    marker = os.path.join(MARKER_DIR, f"{MARKER_PREFIX}{safe_id}.txt")
    marker_exists = os.path.exists(marker)
    event["marker_exists"] = marker_exists
    if marker_exists:
        try:
            age = now - os.path.getmtime(marker)
            event["marker_age_sec"] = round(age, 2)
            if age < COOLDOWN_SECS:
                event["stage"] = "cooldown_passthrough"
                event["exit"] = 0
                _log(event)
                sys.exit(0)
        except Exception as e:
            event["marker_stat_error"] = repr(e)

    # Prune stale per-session markers (older than COOLDOWN_SECS) to keep scratch/ clean.
    try:
        for fname in os.listdir(MARKER_DIR):
            if fname.startswith(MARKER_PREFIX) and fname.endswith(".txt"):
                fpath = os.path.join(MARKER_DIR, fname)
                try:
                    if now - os.path.getmtime(fpath) >= COOLDOWN_SECS:
                        os.remove(fpath)
                except Exception:
                    pass
    except Exception:
        pass

    if _AUTOMATED_PREFIX.match(prompt):
        event["stage"] = "automated_suppressed"
        event["exit"] = 0
        _log(event)
        sys.exit(0)

    try:
        with open(marker, "w") as f:
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

    try:
        sys.stdout.write(banner)
        sys.stdout.flush()
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
