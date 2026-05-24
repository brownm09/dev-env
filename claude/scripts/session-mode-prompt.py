#!/usr/bin/env python3
"""
session-mode-prompt.py

On the first user prompt of a new session, block and ask the user to confirm
their permission mode. Addresses sessions spawned from an active bypass-permissions
session that may have inherited the wrong defaultMode from settings.json.

Uses a short-lived marker file (2-minute cooldown) so the re-submit after the
user reviews/adjusts the mode passes through without blocking again.
"""

import json
import os
import sys
import time

MARKER = "C:/Users/brown/.claude/scratch/session_mode_ack.txt"
COOLDOWN_SECS = 120  # covers the re-submit window after user sees the prompt


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    now = time.time()

    # If user just saw the prompt and is re-submitting, let it through
    if os.path.exists(MARKER):
        age = now - os.path.getmtime(MARKER)
        if age < COOLDOWN_SECS:
            sys.exit(0)

    # Routines run in Claude-managed worktrees — no human to confirm the mode
    if ".claude/worktrees/" in os.getcwd().replace("\\", "/"):
        sys.exit(0)

    # Only fire on the first prompt of the session (no prior assistant turns)
    messages = data.get("messages", [])
    if any(m.get("role") == "assistant" for m in messages):
        sys.exit(0)

    # Write marker before blocking so re-submit passes through
    try:
        with open(MARKER, "w") as f:
            f.write(str(now))
    except Exception as e:
        sys.stderr.write(f"session-mode-prompt: could not write marker: {e}\n")

    print("─────────────────────────────────────────────────")
    print("New session — confirm your permission mode:")
    print("")
    print("  plan       Claude asks before making any edits  (settings default)")
    print("  bypass     Claude acts immediately without asking")
    print("  auto       Claude decides based on task risk")
    print("")
    print("Press Shift+Tab to cycle modes if needed,")
    print("then re-submit your prompt to continue.")
    print("─────────────────────────────────────────────────")
    sys.exit(2)


main()
