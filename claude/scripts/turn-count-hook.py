#!/usr/bin/env python3
"""
UserPromptSubmit hook: warn when a session is accumulating significant context.

Two independent signals:

  1. Context token size (PRIMARY) — reads the transcript JSONL and checks the
     total input tokens on the last assistant turn. Fires at CONTEXT_WARN_TOKENS
     and repeats every CONTEXT_REPEAT_TOKENS after that. State is persisted in
     ~/.claude/scratch/ctx-warn-<session_id>.txt so warnings don't repeat until
     the next threshold boundary is crossed.

  2. User prompt count (SECONDARY, less accurate) — counts UserPromptSubmit
     events via a scratch file. Fires at PROMPT_THRESHOLD and repeats every
     PROMPT_REPEAT_INTERVAL after that. Useful as an early heads-up but prone
     to false positives since one prompt can span many cheap tool calls.

Exit 0 always — never block the user's prompt.
Stdout is injected as context Claude sees before processing the user's message.
"""

import json
import os
import sys
import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"

# --- Signal 1: context token size ---
CONTEXT_WARN_TOKENS = 40_000   # first warning
CONTEXT_REPEAT_TOKENS = 20_000 # repeat interval after first warning

# --- Signal 2: user prompt count ---
PROMPT_THRESHOLD = 8           # first warning
PROMPT_REPEAT_INTERVAL = 4     # repeat interval after first warning

CONFIG_FILE = ".claude/hook-config.json"
COUNTER_MAX_AGE_DAYS = 30


def load_prompt_threshold(cwd: str) -> int:
    path = os.path.join(cwd, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        return int(config.get("turn_threshold", PROMPT_THRESHOLD))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return PROMPT_THRESHOLD


def cleanup_stale_counters() -> None:
    cutoff = time.time() - COUNTER_MAX_AGE_DAYS * 86400
    try:
        for f in SCRATCH.glob("turn-count-*.txt"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        for f in SCRATCH.glob("ctx-warn-*.txt"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


def get_current_context_tokens(transcript_path_str: str) -> int | None:
    """
    Parse the transcript JSONL and return total context tokens from the last
    assistant turn: input_tokens + cache_read_input_tokens + cache_creation_input_tokens.
    Returns None if the transcript cannot be read or has no assistant turns yet.
    """
    if not transcript_path_str:
        return None
    path = Path(transcript_path_str)
    if not path.exists():
        return None

    last_usage = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "assistant":
                    msg = record.get("message", {})
                    usage = msg.get("usage", {})
                    if usage:
                        last_usage = usage
    except OSError:
        return None

    if last_usage is None:
        return None

    return (
        last_usage.get("input_tokens", 0)
        + last_usage.get("cache_read_input_tokens", 0)
        + last_usage.get("cache_creation_input_tokens", 0)
    )


def check_context_tokens(transcript_path_str: str, session_id: str) -> str | None:
    """
    Return a warning string if context token threshold is crossed since the last
    warning, else None. Persists the last-warned level to avoid re-firing on the
    same threshold boundary across multiple prompts.
    """
    tokens = get_current_context_tokens(transcript_path_str)
    if tokens is None:
        return None

    warn_file = SCRATCH / f"ctx-warn-{session_id}.txt"
    try:
        last_warned = int(warn_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        last_warned = 0

    next_threshold = CONTEXT_WARN_TOKENS if last_warned == 0 else last_warned + CONTEXT_REPEAT_TOKENS

    if tokens < next_threshold:
        return None

    try:
        warn_file.write_text(str(next_threshold))
    except Exception:
        pass

    return (
        f"[session-size] Context is at {tokens:,} tokens "
        f"(threshold: {next_threshold:,}). "
        f"Consider running /compact or /clear if the task scope has shifted — "
        f"accumulated context increases cost on every subsequent turn."
    )


def check_prompt_count(session_id: str, cwd: str) -> str | None:
    """
    Increment and persist the prompt counter. Return a soft warning string if
    the prompt threshold is exceeded, else None.
    """
    threshold = load_prompt_threshold(cwd)
    counter_file = SCRATCH / f"turn-count-{session_id}.txt"
    try:
        count = int(counter_file.read_text().strip()) + 1
    except (FileNotFoundError, ValueError):
        count = 1
    try:
        counter_file.write_text(str(count))
    except Exception:
        pass

    if count >= threshold and (count - threshold) % PROMPT_REPEAT_INTERVAL == 0:
        return (
            f"[prompt-count] Session has {count} user prompts "
            f"(threshold: {threshold}). "
            f"Note: prompt count is a weak signal — check context token size "
            f"for a more accurate read on session weight."
        )
    return None


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
    transcript_path = data.get("transcript_path", "")

    messages = []

    ctx_warn = check_context_tokens(transcript_path, session_id)
    if ctx_warn:
        messages.append(ctx_warn)

    prompt_warn = check_prompt_count(session_id, cwd)
    if prompt_warn:
        messages.append(prompt_warn)

    if messages:
        print("\n".join(messages))

    sys.exit(0)


if __name__ == "__main__":
    main()
