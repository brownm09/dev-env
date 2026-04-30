#!/usr/bin/env python3
"""PostCompact hook — emit a status line so compaction is visible in all environments."""
import json
import sys


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        return
    data = json.loads(raw)
    trigger = data.get("trigger", "unknown")   # "manual" | "auto"
    summary = data.get("summary", "")
    tokens = data.get("context_tokens", None)

    if trigger == "manual":
        label = "[compact]"
    elif trigger == "auto":
        label = "[auto-compact]"
    else:
        label = f"[compact/{trigger}]"
    if tokens is not None:
        print(f"{label} done -- context now {tokens:,} tokens", file=sys.stderr)
    else:
        print(f"{label} done", file=sys.stderr)

    if summary:
        first_line = summary.splitlines()[0].strip()[:120]
        print(f"  summary: {first_line}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
