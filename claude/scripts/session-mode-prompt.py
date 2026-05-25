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
"""

import json
import os
import re
import sys
import time

MARKER = "C:/Users/brown/.claude/scratch/session_mode_ack.txt"
COOLDOWN_SECS = 120  # covers the re-submit window after user sees the prompt

# Automated triggers use XML-tagged prompts; human prompts never start with <tag>
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")


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

    # Automated sessions (scheduled tasks, CI monitors, etc.) — no human present
    prompt = data.get("prompt", "")
    if _AUTOMATED_PREFIX.match(prompt):
        sys.exit(0)

    # Write marker before blocking so re-submit passes through
    try:
        with open(MARKER, "w") as f:
            f.write(str(now))
    except Exception as e:
        sys.stderr.write(f"session-mode-prompt: could not write marker: {e}\n")

    print("-------------------------------------------------")
    print("New session -- confirm your permission mode:")
    print("")
    print("  plan       Claude asks before making any edits  (settings default)")
    print("  bypass     Claude acts immediately without asking")
    print("  auto       Claude decides based on task risk")
    print("")
    print("Press Shift+Tab to cycle modes if needed,")
    print("then re-submit your prompt to continue.")
    print("-------------------------------------------------")
    sys.exit(2)


main()
