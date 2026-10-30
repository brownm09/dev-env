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

Plus one journal trigger (dev-env#1160, ADR-150), computed from the same
transcript pass as signal 1 so it costs no extra process or scan:

  3. Context growth band — cumulative context *growth* since the session's
     first turn (the sum of every increase between consecutive assistant
     turns, so a compaction's drop never subtracts and post-compaction work
     still counts). The first band is STUB_FIRST_TOKENS of growth, then every
     STUB_STEP_TOKENS more; each band nudges Claude to update the session's
     journal stub exactly once (highest band fired persists in
     ~/.claude/scratch/ctx-stub-<session_id>.txt). Growth, not absolute size,
     because this machine's session-start context is already ~130-185k.

Exit 0 always — never block the user's prompt.
Stdout is injected as context Claude sees before processing the user's message.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from _hookio import load_hook_input
import _hookutil

SCRATCH = Path.home() / ".claude" / "scratch"

# --- Signal 1: context token size ---
CONTEXT_WARN_TOKENS = 40_000   # first warning
CONTEXT_REPEAT_TOKENS = 20_000 # repeat interval after first warning

# --- Signal 2: user prompt count ---
PROMPT_THRESHOLD = 8           # first warning
PROMPT_REPEAT_INTERVAL = 4     # repeat interval after first warning

# --- Journal trigger: context growth bands (ADR-150 has the corpus behind these) ---
STUB_FIRST_TOKENS = 150_000    # growth since the first turn before the first nudge
STUB_STEP_TOKENS = 100_000     # further growth per additional nudge

_STUB_PATH_RE = re.compile(r"\.stub\.md$", re.IGNORECASE)

CONFIG_FILE = ".claude/hook-config.json"
COUNTER_MAX_AGE_DAYS = 30


def load_prompt_threshold(cwd: str) -> int:
    """Return turn threshold from project hook-config.json, or the default."""
    path = os.path.join(cwd, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        return int(config.get("turn_threshold", PROMPT_THRESHOLD))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return PROMPT_THRESHOLD


def load_stub_bands(cwd: str) -> tuple[int, int]:
    """Return (first, step) growth bands from project hook-config.json keys
    ``journal_stub_context_first_tokens`` / ``journal_stub_context_step_tokens``.
    A missing, unreadable, non-integer, or non-positive value falls back to that
    key's default independently — a step of 0 would never advance a band."""
    config: dict = {}
    try:
        with open(os.path.join(cwd, CONFIG_FILE), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            config = loaded
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    def positive(key: str, default: int) -> int:
        try:
            value = int(config.get(key, default))
        except (TypeError, ValueError, OverflowError):  # OverflowError: JSON Infinity
            return default
        return value if value > 0 else default

    return (
        positive("journal_stub_context_first_tokens", STUB_FIRST_TOKENS),
        positive("journal_stub_context_step_tokens", STUB_STEP_TOKENS),
    )


def cleanup_stale_counters() -> None:
    """Delete counter and ctx-warn files older than COUNTER_MAX_AGE_DAYS.

    Delegates to the shared ``_hookutil.cleanup_stale_sentinels`` helper — one
    call per prefix, both ``.txt`` — rather than re-implementing the
    glob/cutoff/unlink loop. This is the Phase E SSOT migration dev-env#768's
    review deferred to "the Phase E SSOT-consolidation track": ``ext=".txt"``
    (added in that PR) made the shared helper shape-compatible, and this PR
    (dev-env#780) folds the already-working cleanup onto it. ``max_age_days`` is
    passed explicitly so the retention stays COUNTER_MAX_AGE_DAYS regardless of
    ``_hookutil.MAX_AGE_DAYS``."""
    _hookutil.cleanup_stale_sentinels("turn-count-", ext=".txt", max_age_days=COUNTER_MAX_AGE_DAYS)
    _hookutil.cleanup_stale_sentinels("ctx-warn-", ext=".txt", max_age_days=COUNTER_MAX_AGE_DAYS)
    _hookutil.cleanup_stale_sentinels("ctx-stub-", ext=".txt", max_age_days=COUNTER_MAX_AGE_DAYS)


def get_context_stats(transcript_path_str: str) -> tuple[int, int] | None:
    """
    Parse the transcript JSONL once and return ``(current, growth)``:

    * current — total context tokens on the last assistant turn:
      input_tokens + cache_read_input_tokens + cache_creation_input_tokens.
    * growth — the sum of every increase in that total between consecutive
      assistant turns. A compaction's drop is never subtracted, so work done
      after a compaction keeps adding to the same running figure.

    Sidechain (subagent) records are skipped: their much smaller context would
    otherwise read as a drop and then a spurious rise back to the main thread's.
    So are zero-usage records: Claude Code writes synthetic assistant turns
    ("No response requested.", a usage-limit notice, an API error) with every
    usage field 0, and counting one would add the whole context back as growth.
    Returns None if the transcript cannot be read or has no assistant turns yet.

    transcript_path_str comes from the documented `transcript_path` field in the
    UserPromptSubmit hook payload (see code.claude.com/docs/en/hooks.md).
    """
    if not transcript_path_str:
        return None
    path = Path(transcript_path_str)
    if not path.exists():
        return None

    current = None
    growth = 0
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
                if not isinstance(record, dict) or record.get("isSidechain"):
                    continue
                if record.get("type") == "assistant":
                    msg = record.get("message", {})
                    usage = msg.get("usage", {}) if isinstance(msg, dict) else {}
                    if isinstance(usage, dict) and usage:
                        parts = [
                            usage.get("input_tokens", 0),
                            usage.get("cache_read_input_tokens", 0),
                            usage.get("cache_creation_input_tokens", 0),
                        ]
                        # One malformed record must not raise past this loop and
                        # silence every warning for the rest of the session.
                        if not all(isinstance(p, int) for p in parts):
                            continue
                        tokens = sum(parts)
                        if tokens == 0:
                            continue
                        if current is not None and tokens > current:
                            growth += tokens - current
                        current = tokens
    except OSError:
        return None

    if current is None:
        return None
    return current, growth


def check_context_tokens(tokens: int, session_id: str) -> str | None:
    """
    Return a warning string if context token threshold is crossed since the last
    warning, else None. Persists the last-warned level to avoid re-firing on the
    same threshold boundary across multiple prompts.
    """

    warn_file = SCRATCH / f"ctx-warn-{session_id}.txt"
    try:
        last_warned = int(warn_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        last_warned = 0

    next_threshold = CONTEXT_WARN_TOKENS if last_warned == 0 else last_warned + CONTEXT_REPEAT_TOKENS

    if tokens < next_threshold:
        return None

    # Advance through all crossed thresholds so a single heavy prompt doesn't
    # produce consecutive same-token warnings on back-to-back turns.
    initial_threshold = next_threshold
    while tokens >= next_threshold + CONTEXT_REPEAT_TOKENS:
        next_threshold += CONTEXT_REPEAT_TOKENS
    caught_up = next_threshold > initial_threshold

    try:
        warn_file.write_text(str(next_threshold))
    except Exception:
        pass

    clause = " (catching up — context crossed prior boundaries)" if caught_up else ""
    return (
        f"[session-size] Context is at {tokens:,} tokens "
        f"(threshold: {next_threshold:,}{clause}). "
        f"Consider running /compact or /clear if the task scope has shifted — "
        f"accumulated context increases cost on every subsequent turn."
    )


def find_session_stub(transcript_path_str: str) -> str | None:
    """Return the ``file_path`` of the most recent Write/Edit of a ``*.stub.md``
    in this session's transcript, or None. A compaction keeps appending to the
    same JSONL, so a stub written before it is still found. Reads from the end
    and stops at the first match, so it walks the whole file only when the
    session has written no stub; called only when a band fires. A write whose
    tool_result is an error (blocked, denied, failed) is skipped; reading
    backwards meets each result before the tool_use it answers."""
    failed_ids: set = set()
    try:
        for rec in _hookutil.iter_records_reverse(Path(transcript_path_str)):
            if rec.get("isSidechain"):
                continue
            if rec.get("type") == "user":
                for item in _hookutil._content_items(rec):
                    if (
                        isinstance(item, dict)
                        and item.get("type") == "tool_result"
                        and item.get("is_error")
                    ):
                        failed_ids.add(item.get("tool_use_id"))
                continue
            if rec.get("type") != "assistant":
                continue
            for item in reversed(_hookutil._content_items(rec)):
                if (
                    isinstance(item, dict)
                    and item.get("type") == "tool_use"
                    and item.get("name") in ("Write", "Edit")
                    and item.get("id") not in failed_ids
                ):
                    inp = item.get("input")
                    path = inp.get("file_path", "") if isinstance(inp, dict) else ""
                    if isinstance(path, str) and _STUB_PATH_RE.search(path):
                        return path
    except (OSError, ValueError):
        return None
    return None


def check_stub_band(
    growth: int, session_id: str, cwd: str, transcript_path_str: str
) -> str | None:
    """
    Return a journal-stub nudge the first time context growth reaches a new
    band, else None. Band k (k >= 1) starts at first + (k - 1) * step tokens of
    growth. The highest band already nudged persists per session, so a band
    fires once, and growth that jumps several bands in one turn nudges once.
    """
    # fullmatch, not match: `$` would also accept a trailing newline.
    if not isinstance(session_id, str) or not _hookutil._SAFE_SESSION_ID.fullmatch(session_id):
        return None
    first, step = load_stub_bands(cwd)
    if growth < first:
        return None
    band = 1 + (growth - first) // step

    state_file = SCRATCH / f"ctx-stub-{session_id}.txt"
    try:
        last_band = int(state_file.read_text().strip())
    except (OSError, ValueError):
        last_band = 0
    if band <= last_band:
        return None
    try:
        state_file.write_text(str(band))
    except OSError:
        pass

    threshold = first + (band - 1) * step
    stub = find_session_stub(transcript_path_str)
    if stub:
        where = f"Update this session's stub in place with the Edit tool: {stub}"
    else:
        where = (
            "No stub was written in this session yet: create "
            "sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md and its manifest shard "
            "with the Write tool, per the Stub file workflow in ~/.claude/CLAUDE.md "
            "(Engineering Journal)"
        )
    return (
        f"[journal-stub] Context has grown {growth:,} tokens since this session's "
        f"first turn (band {band}, at {threshold:,}). Record the work done since "
        f"the stub's last update so it survives a crash, compaction, or app "
        f"restart. {where}. Never write a stub through a shell heredoc, echo, or "
        f"redirect (ADR-129); commit it with an explicit pathspec and push. If "
        f"the stub already covers everything since its last update, no action "
        f"is needed."
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
    _hookutil.record_heartbeat("turn-count-hook")
    cleanup_stale_counters()

    data = load_hook_input() or {}

    raw_session_id = data.get("session_id")
    # The id becomes part of scratch filenames, so an unsafe one falls back.
    session_id = (
        raw_session_id
        if isinstance(raw_session_id, str) and _hookutil._SAFE_SESSION_ID.fullmatch(raw_session_id)
        else "unknown"
    )
    cwd = data.get("cwd", os.getcwd())
    transcript_path = data.get("transcript_path", "")

    messages = []

    stats = get_context_stats(transcript_path)
    if stats is not None:
        tokens, growth = stats
        ctx_warn = check_context_tokens(tokens, session_id)
        if ctx_warn:
            messages.append(ctx_warn)
        # The raw id, not the "unknown" fallback: sessions without an id must
        # not share one band-state file and suppress each other's nudges.
        stub_nudge = check_stub_band(growth, raw_session_id, cwd, transcript_path)
        if stub_nudge:
            messages.append(stub_nudge)

    prompt_warn = check_prompt_count(session_id, cwd)
    if prompt_warn:
        messages.append(prompt_warn)

    if messages:
        print("\n".join(messages))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
