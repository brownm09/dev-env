#!/usr/bin/env python3
"""Claude model prices and cost arithmetic, shared by the token scripts (dev-env#1122).

Prices are USD per million tokens, from the "Model pricing" table of
https://platform.claude.com/docs/en/about-claude/pricing.md, read 2026-11-05.
`cache_write` is the 5-minute tier and `cache_write_1h` the 1-hour tier.

An unknown model gets None from get_pricing, never another model's prices, so a
new model shows up as unpriced instead of being quietly costed at the wrong rate.
"""

PRICING = {
    "claude-fable-5-1":  {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50, "cache_write_1h": 20.00},
    "claude-opus-5-5":   {"input": 4.00,  "output": 20.00, "cache_read": 0.20, "cache_write": 5.00,  "cache_write_1h": 8.00},
    "claude-opus-5":     {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-sonnet-5-5": {"input": 2.00,  "output": 10.00, "cache_read": 0.10, "cache_write": 2.50,  "cache_write_1h": 4.00},
    "claude-sonnet-5":   {"input": 2.00,  "output": 10.00, "cache_read": 0.20, "cache_write": 2.50,  "cache_write_1h": 4.00},
    "claude-haiku-5-5":  {"input": 0.10,  "output": 0.50,  "cache_read": 0.01, "cache_write": 0.125, "cache_write_1h": 0.20},
    "claude-opus-4-8":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-opus-4-7":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_read": 0.30, "cache_write": 3.75,  "cache_write_1h": 6.00},
    "claude-opus-4-6":   {"input": 5.00,  "output": 25.00, "cache_read": 0.50, "cache_write": 6.25,  "cache_write_1h": 10.00},
    "claude-haiku-4-5":  {"input": 1.00,  "output": 5.00,  "cache_read": 0.10, "cache_write": 1.25,  "cache_write_1h": 2.00},
}

# Haiku 5.5 is the one model priced by prompt length. Session totals can't see the
# length of each prompt, so only a per-call caller (the attribution report) passes it.
HAIKU_5_5_LONG_PROMPT_TOKENS = 100_000
HAIKU_5_5_LONG_PROMPT = {"input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625, "cache_write_1h": 1.00}

# Claude Code's model name on the error and notice records it writes itself. They
# carry no billed tokens, so they must not stand in for a session's model.
SYNTHETIC_MODEL = "<synthetic>"

# Longest first, so "claude-opus-5" can't claim a "claude-opus-5-5" model id.
_KEYS_LONGEST_FIRST = sorted(PRICING, key=len, reverse=True)


def get_pricing(model: str, prompt_tokens: int = 0) -> dict | None:
    """Return the price row for a possibly dated model id, or None if it is unknown."""
    for key in _KEYS_LONGEST_FIRST:
        if key in model:
            if key == "claude-haiku-5-5" and prompt_tokens > HAIKU_5_5_LONG_PROMPT_TOKENS:
                return HAIKU_5_5_LONG_PROMPT
            return PRICING[key]
    return None


def flat_usage(usage: dict) -> dict:
    """Copy a transcript usage record with its nested 1-hour write count as a flat key.

    Flat keys are what the scripts' running totals sum. An empty record stays empty,
    so their `if usage:` checks keep working.
    """
    if not usage:
        return usage
    nested = usage.get("cache_creation") or {}
    return {**usage, "cache_write_1h_input_tokens": nested.get("ephemeral_1h_input_tokens", 0)}


def cost_buckets(usage: dict, prices: dict) -> dict:
    """Return USD by billing bucket. `usage` must be flat; see flat_usage."""
    writes = usage.get("cache_creation_input_tokens", 0)
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
