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
  * read_token_log_lines(path) (dev-env#804): extracted from main()'s previously-
    inline, unguarded `TOKEN_LOG.read_text(encoding="utf-8")` (which let a non-UTF-8
    log's UnicodeDecodeError escape uncaught, silently dropping the current session's
    entire summary) so the degrade-to-fresh-log behavior is unit-testable: a missing,
    unreadable (OSError, incl. a directory path), or non-UTF-8 (UnicodeDecodeError)
    log all resolve to [], and a real log round-trips its lines unchanged (same fix
    shape as _bash_state.read_state, dev-env#801).
  * _count_turns / aggregate_session (dev-env#804): a happy-path smoke test each
    (proving the new try/except wrap didn't change normal behavior) plus the new
    resilience behavior — a file of only invalid UTF-8 bytes degrades to the
    zero/None defaults instead of raising, so a corrupted transcript no longer
    crashes main() before it can record anything for the session.

main()'s remaining I/O (stdin parse, transcript locate, the log write/rewrite, the
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


# --- read_token_log_lines() (dev-env#804) ---------------------------------------

def test_read_token_log_lines_missing_file() -> str:
    with tempfile.TemporaryDirectory() as d:
        got = tt.read_token_log_lines(Path(d) / "token-sessions.jsonl")
        assert got == [], f"expected [] for a missing file, got {got}"
    return "read_token_log_lines returns [] for a missing file"


def test_read_token_log_lines_round_trip() -> str:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "token-sessions.jsonl"
        path.write_text('{"session_id": "a"}\n{"session_id": "b"}\n', encoding="utf-8")
        got = tt.read_token_log_lines(path)
        assert got == ['{"session_id": "a"}\n', '{"session_id": "b"}\n'], got
    return "read_token_log_lines returns the file's lines with line endings kept"


def test_read_token_log_lines_non_utf8_returns_empty() -> str:
    # dev-env#804: a non-UTF-8 TOKEN_LOG made the pre-fix inline
    # read_text(encoding="utf-8") raise UnicodeDecodeError uncaught out of main(),
    # silently losing the current session's summary (never appended, never written
    # to LATEST_SESSION either).
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "token-sessions.jsonl"
        path.write_bytes(b"\xff\xfe\x00\x9d")
        got = tt.read_token_log_lines(path)
        assert got == [], f"expected [] for non-UTF-8 bytes, got {got}"
    return "read_token_log_lines returns [] on a non-UTF-8 file (UnicodeDecodeError caught, degrades to fresh log)"


def test_read_token_log_lines_directory_returns_empty() -> str:
    # IsADirectoryError is an OSError subclass -- already covered without a
    # separate .exists()/.is_dir() check (dev-env#804 review question).
    with tempfile.TemporaryDirectory() as d:
        got = tt.read_token_log_lines(Path(d))
        assert got == [], f"expected [] for a directory path, got {got}"
    return "read_token_log_lines returns [] for a directory path (IsADirectoryError caught)"


# --- _count_turns() (dev-env#804) ------------------------------------------------

def test_count_turns_reads_normal_file() -> str:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "agent-1.jsonl"
        path.write_text(
            '{"type": "assistant", "message": {"model": "claude-opus-4-6", '
            '"usage": {"input_tokens": 100, "output_tokens": 50, '
            '"cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}}\n',
            encoding="utf-8",
        )
        totals, turn_count, model = tt._count_turns(path)
        assert totals == {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        }, totals
        assert turn_count == 1, turn_count
        assert model == "claude-opus-4-6", model
    return "_count_turns extracts totals/turn_count/model from a normal JSONL file"


def test_count_turns_non_utf8_returns_zeroed_defaults() -> str:
    # dev-env#804: a non-UTF-8 subagent transcript made the pre-fix unguarded
    # open(...) + iteration raise UnicodeDecodeError, propagating out of
    # aggregate_session/main() and silently losing the whole session's summary.
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "agent-1.jsonl"
        path.write_bytes(b"\xff\xfe\x00\x9d")
        totals, turn_count, model = tt._count_turns(path)
        assert totals == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }, totals
        assert turn_count == 0, turn_count
        assert model == "claude-sonnet-4-6", model
    return "_count_turns degrades to zeroed defaults on a non-UTF-8 file (no raise)"


# --- aggregate_session() (dev-env#804) -------------------------------------------

def test_aggregate_session_reads_normal_file() -> str:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "session-abc.jsonl"
        path.write_text(
            '{"type": "assistant", "cwd": "/repo", "gitBranch": "main", '
            '"entrypoint": "cli", "timestamp": "2026-07-16T10:00:00Z", '
            '"message": {"model": "claude-opus-4-6", "usage": {"input_tokens": 100, '
            '"output_tokens": 50, "cache_read_input_tokens": 10, '
            '"cache_creation_input_tokens": 5}}}\n',
            encoding="utf-8",
        )
        data = tt.aggregate_session(path)
        assert data["tokens"] == {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 5,
        }, data["tokens"]
        assert data["turn_count"] == 1, data["turn_count"]
        assert data["model"] == "claude-opus-4-6", data["model"]
        assert data["cwd"] == "/repo", data["cwd"]
        assert data["git_branch"] == "main", data["git_branch"]
        assert data["entrypoint"] == "cli", data["entrypoint"]
        assert data["first_turn_ts"] == "2026-07-16T10:00:00Z"
        assert data["last_turn_ts"] == "2026-07-16T10:00:00Z"
        assert data["subagent_count"] == 0
    return "aggregate_session extracts all fields from a normal transcript"


def test_aggregate_session_non_utf8_returns_safe_defaults() -> str:
    # dev-env#804: a non-UTF-8 main transcript made the pre-fix unguarded
    # open(...) + iteration raise UnicodeDecodeError, propagating out of main() and
    # silently losing the whole session's summary (never appended to TOKEN_LOG,
    # never written to LATEST_SESSION).
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "session-abc.jsonl"
        path.write_bytes(b"\xff\xfe\x00\x9d")
        data = tt.aggregate_session(path)
        assert data["tokens"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }, data["tokens"]
        assert data["turn_count"] == 0
        assert data["model"] == "claude-sonnet-4-6"
        assert data["cwd"] is None
        assert data["git_branch"] is None
        assert data["entrypoint"] is None
        assert data["first_turn_ts"] is None
        assert data["last_turn_ts"] is None
        assert data["subagent_count"] == 0
    return "aggregate_session returns safe zeroed/None defaults on a non-UTF-8 transcript (no raise)"


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
        ("read_token_log_lines: missing file -> []", test_read_token_log_lines_missing_file),
        ("read_token_log_lines: round-trip", test_read_token_log_lines_round_trip),
        ("read_token_log_lines: non-UTF-8 -> []", test_read_token_log_lines_non_utf8_returns_empty),
        ("read_token_log_lines: directory -> []", test_read_token_log_lines_directory_returns_empty),
        ("_count_turns: reads normal file", test_count_turns_reads_normal_file),
        ("_count_turns: non-UTF-8 -> zeroed defaults", test_count_turns_non_utf8_returns_zeroed_defaults),
        ("aggregate_session: reads normal file", test_aggregate_session_reads_normal_file),
        ("aggregate_session: non-UTF-8 -> safe defaults", test_aggregate_session_non_utf8_returns_safe_defaults),
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
