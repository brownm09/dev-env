#!/usr/bin/env python3
"""Pure-helper tests for token-tracker.py (ADR-103, dev-env#740).

token-tracker.py is a Stop hook. It records per-session token usage to a log file
and, when it cannot locate the session transcript, emits a user-facing diagnostic.
A Stop hook's exit-0 stdout/stderr are invisible to Claude and the user, so that
diagnostic now rides the shared `_hookout` systemMessage channel (exit 0) instead
of the former invisible `stderr`+exit0 print; the two per-turn status echoes were
dropped (they fired every turn-end and were invisible anyway, so a systemMessage in
their place would be toast-spam).

These tests exercise the pure helpers offline (no stdin, network, gh, or disk):
  * format_locate_error(session_id): ASCII / cp1252-encodable (so the systemMessage
    text can't vanish under Claude Code's cp1252 hook-output pipe on Windows) and
    names the hook + the session id.
  * get_pricing / compute_cost: the pure pricing math (a known model, the default
    fallback, and the cost arithmetic).

main()'s I/O (stdin parse, transcript locate + aggregation, log write, the
`_hookout.emit_advisory` emission) is not covered — the emission channel itself is
pinned by test_hook_output_contract.py (the gate finds ZERO output-contract offenses
in token-tracker after this migration) and by test_hookout.py.

Usage:
    py -3 claude/scripts/tests/test_token_tracker.py

Exit 0 = all pass.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "token-tracker.py"

# token-tracker imports _hookout / _hookutil (siblings in scripts/); make them
# resolvable when exec_module runs the module body.
sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("token_tracker", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
tt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tt)  # safe: main() is guarded by __main__


# --- format_locate_error() -----------------------------------------------------

def test_format_locate_error_is_cp1252_safe() -> str:
    # Claude Code pipes hook output as cp1252 on Windows; a char outside it makes the
    # emitting write raise and the diagnostic vanish. Pin it ASCII/cp1252-encodable.
    msg = tt.format_locate_error("018f0c2a-1b2c-3d4e-5f6a-7b8c9d0e1f2a")
    assert msg.isascii(), f"diagnostic must be ASCII, got non-ASCII: {msg!r}"
    msg.encode("cp1252")  # must not raise
    return "locate-error diagnostic is ASCII / cp1252-encodable (cannot vanish on the systemMessage path)"


def test_format_locate_error_names_hook_and_session() -> str:
    sid = "sess-abc123-def456"
    msg = tt.format_locate_error(sid)
    assert "token-tracker" in msg, msg
    # `{session_id!r}` wraps the id in quotes, so the raw id is a substring.
    assert sid in msg, msg
    assert "not recorded" in msg, msg
    return "diagnostic names the hook, the session id, and states usage was not recorded"


def test_format_locate_error_empty_session() -> str:
    # A missing/empty session id must still produce ASCII output, not raise.
    msg = tt.format_locate_error("")
    assert msg.isascii() and "token-tracker" in msg, msg
    return "empty session id -> still ASCII, still names the hook (no crash)"


# --- should_advise_locate_failure() (once-per-session guard) --------------------

def test_should_advise_locate_failure_once_per_session() -> str:
    # A Stop hook fires every turn-end; the diagnostic must toast at most once per
    # session, else a persistently unlocatable transcript re-spams every turn
    # (dev-env#740 review). First call advises + writes the sentinel; second returns
    # False. `scratch=tmp` isolates from the real ~/.claude/scratch.
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        first = tt.should_advise_locate_failure("sess-xyz", scratch=scratch)
        second = tt.should_advise_locate_failure("sess-xyz", scratch=scratch)
        third = tt.should_advise_locate_failure("sess-xyz", scratch=scratch)
    assert first is True, "first locate failure must advise"
    assert second is False and third is False, "later failures in the same session must not re-advise"
    return "should_advise_locate_failure: advises once per session, suppresses after (no per-turn spam)"


def test_should_advise_locate_failure_distinct_sessions() -> str:
    # Different sessions each get their own one-shot (keyed on session_id).
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        assert tt.should_advise_locate_failure("sess-A", scratch=scratch) is True
        assert tt.should_advise_locate_failure("sess-B", scratch=scratch) is True
        assert tt.should_advise_locate_failure("sess-A", scratch=scratch) is False
    return "distinct session ids each advise once (per-session, not global)"


def test_should_advise_locate_failure_empty_session_always_advises() -> str:
    # No session_id -> can't dedupe -> err toward advising (better a rare duplicate
    # toast than a silently-lost diagnostic). Must not touch the filesystem/raise.
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        assert tt.should_advise_locate_failure("", scratch=scratch) is True
        assert tt.should_advise_locate_failure("", scratch=scratch) is True
    return "empty session id -> always advises (no dedup possible, fail toward visible)"


# --- get_pricing() -------------------------------------------------------------

def test_get_pricing_known_models() -> str:
    assert tt.get_pricing("claude-sonnet-4-6") == tt.PRICING["claude-sonnet-4-6"]
    assert tt.get_pricing("claude-opus-4-6") == tt.PRICING["claude-opus-4-6"]
    # Substring match: a fully-qualified/dated model id still resolves.
    assert tt.get_pricing("claude-haiku-4-5-20251001") == tt.PRICING["claude-haiku-4-5"]
    return "get_pricing resolves known models (incl. a dated/suffixed id via substring match)"


def test_get_pricing_unknown_defaults() -> str:
    assert tt.get_pricing("some-unknown-model") == tt._DEFAULT_PRICES
    assert tt.get_pricing("") == tt._DEFAULT_PRICES
    return "get_pricing falls back to the default (sonnet) prices for an unknown/empty model"


# --- compute_cost() ------------------------------------------------------------

def test_compute_cost_arithmetic() -> str:
    prices = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}
    # 1M input @ $3 + 1M output @ $15 = $18.00
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    assert tt.compute_cost(usage, prices) == 18.0, tt.compute_cost(usage, prices)
    # All four token buckets contribute; missing keys default to 0.
    full = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
    }
    assert tt.compute_cost(full, prices) == 3.0 + 15.0 + 0.3 + 3.75, tt.compute_cost(full, prices)
    return "compute_cost sums all four token buckets at their per-million rate"


def test_compute_cost_empty_usage_is_zero() -> str:
    prices = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}
    assert tt.compute_cost({}, prices) == 0.0
    return "empty usage -> $0.00 (missing token keys default to 0)"


def main() -> int:
    tests = [
        ("format_locate_error cp1252-safe", test_format_locate_error_is_cp1252_safe),
        ("format_locate_error names hook + session", test_format_locate_error_names_hook_and_session),
        ("format_locate_error empty session", test_format_locate_error_empty_session),
        ("should_advise_locate_failure once per session", test_should_advise_locate_failure_once_per_session),
        ("should_advise_locate_failure distinct sessions", test_should_advise_locate_failure_distinct_sessions),
        ("should_advise_locate_failure empty session always advises", test_should_advise_locate_failure_empty_session_always_advises),
        ("get_pricing known models", test_get_pricing_known_models),
        ("get_pricing unknown -> default", test_get_pricing_unknown_defaults),
        ("compute_cost arithmetic", test_compute_cost_arithmetic),
        ("compute_cost empty usage -> 0", test_compute_cost_empty_usage_is_zero),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
