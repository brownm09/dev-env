#!/usr/bin/env python3
"""
UserPromptSubmit hook: count turns in the current session and warn at threshold.

Warns at DEFAULT_THRESHOLD turns (default: 50), then every REPEAT_INTERVAL turns
after that. The threshold can be overridden per-project by adding
"turn_threshold": N to .claude/hook-config.json in the project root.

Session turn counts are stored in ~/.claude/scratch/turn-count-<session_id>.txt.
These files accumulate over time — clean them up periodically or ignore them.

Exit 0 always — never block the user's prompt.
Stdout is injected as context Claude sees before processing the user's message.
"""

import json
import os
import sys
import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"
DEFAULT_THRESHOLD = 50
REPEAT_INTERVAL = 25
CONFIG_FILE = ".claude/hook-config.json"
COUNTER_MAX_AGE_DAYS = 30


def load_threshold(cwd: str) -> int:
    """Return turn threshold from project hook-config.json, or the default."""
    path = os.path.join(cwd, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        return int(config.get("turn_threshold", DEFAULT_THRESHOLD))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return DEFAULT_THRESHOLD


def cleanup_stale_counters() -> None:
    """Delete counter files older than COUNTER_MAX_AGE_DAYS."""
    cutoff = time.time() - COUNTER_MAX_AGE_DAYS * 86400
    try:
        for f in SCRATCH.glob("turn-count-*.txt"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass  # degrade silently — cleanup is best-effort


def main() -> None:
    cleanup_stale_counters()

    raw = sys.stdin.read().strip()
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            pass

    session_id = data.get("session_id", "unknown")
    cwd = data.get("cwd", os.getcwd())

    threshold = load_threshold(cwd)

    # Read and increment counter
    counter_file = SCRATCH / f"turn-count-{session_id}.txt"
    try:
        count = int(counter_file.read_text().strip()) + 1
    except (FileNotFoundError, ValueError):
        count = 1

    try:
        counter_file.write_text(str(count))
    except Exception:
        pass  # scratch dir missing or unwritable — degrade silently

    # Warn at threshold and every REPEAT_INTERVAL turns after
    if count >= threshold and (count - threshold) % REPEAT_INTERVAL == 0:
        print(
            f"[turn-count-hook] Session is at turn {count} "
            f"(threshold: {threshold}). "
            f"If the task scope has shifted, consider running /clear or /compact "
            f"to reduce accumulated context cost."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
