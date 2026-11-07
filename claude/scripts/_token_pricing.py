#!/usr/bin/env python3
"""Claude model prices, cost arithmetic and response counting, shared by the token scripts (dev-env#1122).

Prices are USD per million tokens, from the "Model pricing" table of
https://platform.claude.com/docs/en/about-claude/pricing.md, read 2026-11-05.
`cache_write` is the 5-minute tier and `cache_write_1h` the 1-hour tier.

An unknown model gets None from get_pricing, never another model's prices, so a
new model shows up as unpriced instead of being quietly costed at the wrong rate.
See docs/adr/154-token-attribution-and-shared-pricing.md.
"""

import re

PRICING = {
    "claude-fable-5-1":  {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50, "cache_write_1h": 20.00},
    "claude-mythos-5-1": {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50, "cache_write_1h": 20.00},
    "claude-fable-5":    {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50, "cache_write_1h": 20.00},
    "claude-mythos-5":   {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50, "cache_write_1h": 20.00},
    "claude-opus-5-5":   {"input": 4.00,  "output": 20.00, "cache_read": 0.20, "cache_write": 5.00,  "cache_write_1h": 8.00},
    "claude-opus-5":     {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-opus-4-8":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-opus-4-6":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-opus-4-5":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-opus-4-1":   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75, "cache_write_1h": 30.00},
    "claude-opus-4":     {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75, "cache_write_1h": 30.00},
    "claude-sonnet-5-5": {"input": 2.00,  "output": 10.00, "cache_read": 0.10, "cache_write": 2.50,  "cache_write_1h": 4.00},
    "claude-sonnet-5":   {"input": 2.00,  "output": 10.00, "cache_read": 0.20, "cache_write": 2.50,  "cache_write_1h": 4.00},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75,  "cache_write_1h": 6.00},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75,  "cache_write_1h": 6.00},
    "claude-sonnet-4":   {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75,  "cache_write_1h": 6.00},
    "claude-haiku-5-5":  {"input": 0.10,  "output": 0.50,  "cache_read": 0.01, "cache_write": 0.125, "cache_write_1h": 0.20},
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "cache_read": 0.10, "cache_write": 1.25,  "cache_write_1h": 2.00},
    "claude-3-5-haiku":  {"input": 0.80,  "output": 4.00,  "cache_read": 0.08, "cache_write": 1.00,  "cache_write_1h": 1.60},
}

# Haiku 5.5 is the one model priced by prompt length. Session totals can't see the
# length of each prompt, so only a per-call caller (the attribution report) passes it.
HAIKU_5_5_LONG_PROMPT_TOKENS = 100_000
HAIKU_5_5_LONG_PROMPT = {"input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625, "cache_write_1h": 1.00}

# Claude Code's model name on the error and notice records it writes itself. They
# carry no billed tokens, so they must not stand in for a session's model.
SYNTHETIC_MODEL = "<synthetic>"

# A price-table key, optionally dated or carrying a bracket suffix such as [1m]. The
# whole id must match, so a successor like claude-opus-5-6 can't take claude-opus-5's row.
_MODEL_ID = re.compile(r"(?P<key>[a-z0-9-]+?)(?:-\d{8})?(?:\[[0-9a-z]+\])?")

_COUNT_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def get_pricing(model: str, prompt_tokens: int = 0) -> dict | None:
    """Return the price row for a possibly dated model id, or None if it is unknown."""
    match = _MODEL_ID.fullmatch(model) if isinstance(model, str) else None
    key = match.group("key") if match else None
    if key == "claude-haiku-5-5" and prompt_tokens > HAIKU_5_5_LONG_PROMPT_TOKENS:
        return HAIKU_5_5_LONG_PROMPT
    return PRICING.get(key)


def _count(value) -> int:
    """Return a token count, or 0 for anything that isn't one."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _flat_counts(usage: dict) -> dict:
    nested = usage.get("cache_creation")
    hourly = nested.get("ephemeral_1h_input_tokens") if isinstance(nested, dict) else 0
    return {
        **{key: _count(usage.get(key)) for key in _COUNT_KEYS},
        "cache_write_1h_input_tokens": _count(hourly),
    }


def flat_usage(usage) -> dict:
    """Copy a transcript usage record with its nested 1-hour write count as a flat key.

    Flat keys are what the scripts' running totals sum. An empty or non-object record
    comes back empty, so their `if usage:` checks keep working, and a count that isn't
    an integer becomes 0, so one odd value can't lose a session's record. Some records
    hold all-zero top-level counts with the real ones under `iterations`.
    """
    if not usage or not isinstance(usage, dict):
        return {}
    flat = _flat_counts(usage)
    iterations = usage.get("iterations")
    if isinstance(iterations, list) and not any(flat[key] for key in _COUNT_KEYS):
        parts = [_flat_counts(part) for part in iterations if isinstance(part, dict)]
        flat = {key: sum(part[key] for part in parts) for key in flat}
    return {**usage, **flat}


def has_tokens(usage: dict) -> bool:
    """Return False for a flat record with no tokens, which is no API charge at all."""
    return any(usage.get(key) for key in _COUNT_KEYS)


class ResponseTotals:
    """A transcript's token totals, counting each API response once (dev-env#1223).

    One response spans several records that repeat its usage, so a message id is one
    response and its last record with tokens wins, because that one has the final output
    count. A record with no id is a response of its own. The rule matches
    token-report.py's read_transcript, and a test holds the two together.
    """

    def __init__(self) -> None:
        self._usage_by_response: dict = {}
        self.model = ""

    def add(self, msg: dict) -> None:
        """Add one assistant record's message. Its id is transcript data, so it may be any type."""
        usage = flat_usage(msg.get("usage"))
        if not usage or not has_tokens(usage) or msg.get("model") == SYNTHETIC_MODEL:
            return
        try:
            self._usage_by_response[msg.get("id") or object()] = usage
        except TypeError:  # an unhashable id, which read_transcript skips too
            return
        if msg.get("model"):
            self.model = msg["model"]

    @property
    def turn_count(self) -> int:
        return len(self._usage_by_response)

    def totals(self) -> dict:
        usages = self._usage_by_response.values()
        return {
            key: sum(usage[key] for usage in usages)
            for key in (*_COUNT_KEYS, "cache_write_1h_input_tokens")
        }


def is_standard_rate(usage: dict) -> bool:
    """Return False for fast mode or US-only inference, which the table doesn't price."""
    return (
        usage.get("speed") in (None, "standard")
        and usage.get("inference_geo") in (None, "", "global", "not_available")
    )


def cost_buckets(usage: dict, prices: dict) -> dict:
    """Return USD by billing bucket. `usage` must be flat; see flat_usage."""
    writes = usage.get("cache_creation_input_tokens", 0)
    # Real records can show a flat write count of 0 beside a non-zero nested 1-hour count.
    writes_1h = min(usage.get("cache_write_1h_input_tokens", 0), writes)
    return {
        "input": usage.get("input_tokens", 0) * prices["input"] / 1_000_000,
        "output": usage.get("output_tokens", 0) * prices["output"] / 1_000_000,
        "cache_read": usage.get("cache_read_input_tokens", 0) * prices["cache_read"] / 1_000_000,
        "cache_write": (
            (writes - writes_1h) * prices["cache_write"]
            + writes_1h * prices["cache_write_1h"]
        ) / 1_000_000,
    }


def compute_cost(usage: dict, prices: dict | None) -> float:
    """Return total USD for flat usage. An unpriced model (prices None) costs 0.0."""
    if prices is None:
        return 0.0
    return sum(cost_buckets(usage, prices).values())
