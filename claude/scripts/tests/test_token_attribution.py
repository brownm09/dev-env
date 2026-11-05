#!/usr/bin/env python3
"""Tests for _token_pricing.py and token-report.py --attribution (dev-env#1214, #1122).

The attribution fixture is a small transcript whose expected dollars are worked out
by hand here, so a pricing or attribution slip changes a pinned number.

Usage:
    py -3 claude/scripts/tests/test_token_attribution.py

Exit 0 = all pass.
"""

import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _token_pricing as tp  # noqa: E402

_spec = importlib.util.spec_from_file_location("token_report", SCRIPTS / "token-report.py")
assert _spec and _spec.loader
tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tr)  # safe: main() is guarded by __main__

SENTINEL = "SENTINEL-PRIVATE-TEXT-7f3a"
SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
UNTIL = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()


def _assistant(message_id, ts, usage, model="claude-opus-5-5", tool_uses=()):
    content = [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in tool_uses]
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"id": message_id, "model": model, "usage": usage, "content": content},
    }


def _tool_result(tool_use_id, text):
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]},
    }


def _usage(cache_read=0, write_1h=0, write_5m=0, output=0, input_tokens=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": write_1h + write_5m,
        "cache_creation": {
            "ephemeral_1h_input_tokens": write_1h,
            "ephemeral_5m_input_tokens": write_5m,
        },
    }


def _write(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _fixture(root: Path) -> list[Path]:
    """One main session and one subagent, with hand-computable costs.

    Main session, all Opus 5.5 ($0.20 read, $8 hourly write, $20 output per million):
      call 1, 09:00: 200k hourly write                  -> $1.60
      call 2, 09:01: 300k read, 500k output             -> $0.06 + $10.00
      call 3, 11:01: 500k hourly write after a 2h idle  -> $4.00, a re-cache
    Subagent on Sonnet 5.5: 1M 5-minute write at $2.50  -> $2.50
    A call outside the window must not count, and a repeated message id counts once.
    """
    main = _write(root / "project" / "session.jsonl", [
        _assistant("m1", "2026-11-01T09:00:00Z", _usage(write_1h=200_000),
                   tool_uses=[("t1", "Read", {"file_path": SENTINEL})]),
        _tool_result("t1", "\n".join([SENTINEL] * 400)),
        _assistant("m2", "2026-11-01T09:01:00Z", _usage(cache_read=300_000, output=100_000)),
        _assistant("m2", "2026-11-01T09:01:00Z", _usage(cache_read=300_000, output=500_000)),
        _assistant("m3", "2026-11-01T11:01:00Z", _usage(write_1h=500_000)),
        _assistant("old", "2026-10-30T09:00:00Z", _usage(cache_read=9_000_000)),
        _assistant("syn", "2026-11-01T11:02:00Z", _usage(), model=tp.SYNTHETIC_MODEL),
    ])
    sub = _write(root / "project" / "session" / "subagents" / "agent-1.jsonl", [
        _assistant("s1", "2026-11-01T09:00:30Z", _usage(write_5m=1_000_000), model="claude-sonnet-5-5"),
    ])
    return [main, sub]


def _row(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_current_model_gets_its_own_prices() -> str:
    assert tp.get_pricing("claude-opus-5-5")["input"] == 4.00
    assert tp.get_pricing("claude-opus-5-5-20260901")["input"] == 4.00
    assert tp.get_pricing("claude-opus-5")["input"] == 5.00
    assert tp.get_pricing("claude-sonnet-5-5")["cache_read"] == 0.10
    assert tp.get_pricing("claude-sonnet-5")["cache_read"] == 0.20
    return "a longer model id is never claimed by a shorter one that it contains"


def test_unknown_model_is_unpriced() -> str:
    assert tp.get_pricing("some-unknown-model") is None
    assert tp.get_pricing("") is None
    assert tp.compute_cost({"input_tokens": 1_000_000}, None) == 0.0
    return "an unknown model gets None, not Sonnet 4.6 rates, and costs 0.0"


def test_haiku_long_prompt_tier() -> str:
    assert tp.get_pricing("claude-haiku-5-5", 100_000)["input"] == 0.10
    assert tp.get_pricing("claude-haiku-5-5", 100_001)["input"] == 0.50
    return "Haiku 5.5 switches price tier above 100k prompt tokens"


def test_flat_usage_keeps_empty_records_empty() -> str:
    assert tp.flat_usage({}) == {}
    assert tp.flat_usage(_usage(write_1h=5))["cache_write_1h_input_tokens"] == 5
    assert tp.flat_usage({"input_tokens": 1})["cache_write_1h_input_tokens"] == 0
    return "the nested 1-hour count flattens, and empty stays falsy for callers' checks"


def test_cache_write_tiers_are_priced_separately() -> str:
    prices = tp.get_pricing("claude-opus-5-5")
    usage = tp.flat_usage(_usage(write_1h=1_000_000, write_5m=1_000_000))
    assert tp.cost_buckets(usage, prices)["cache_write"] == 13.0
    return "1M hourly at $8 plus 1M 5-minute at $5 is $13"


def test_attribution_pins_expected_dollars() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = tr.attribute_spend(_fixture(Path(d)), SINCE, UNTIL)
    assert report["total_cost_usd"] == 18.16, report["total_cost_usd"]
    assert report["calls"] == 4, report["calls"]
    assert report["tokens"]["output_tokens"] == 500_000, report["tokens"]
    assert _row(report["by_model"], "claude-opus-5-5")["cost_usd"] == 15.66
    assert _row(report["by_scope"], "Subagents")["cost_usd"] == 2.50
    assert _row(report["by_bucket"], "cache_write")["cost_usd"] == 8.10
    return "4 in-window calls cost $18.16, excluding the old call and the repeated id"


def test_attribution_bands_and_recache() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = tr.attribute_spend(_fixture(Path(d)), SINCE, UNTIL)
    bands = report["by_context_band"]
    assert _row(bands, "200k to 400k")["count"] == 2, bands
    assert _row(bands, "400k to 600k")["count"] == 1, bands
    gaps = report["mid_session_recache_by_gap"]
    assert _row(gaps, "over 60 minutes") == {"name": "over 60 minutes", "cost_usd": 4.0,
                                             "share_pct": 22.0, "count": 1}, gaps
    assert _row(gaps, "under 5 minutes")["count"] == 0, gaps
    return "calls sort by context size, and only the post-idle write is a re-cache"


def test_attribution_starting_context_and_large_read() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = tr.attribute_spend(_fixture(Path(d)), SINCE, UNTIL)
    assert report["starting_context_median"] == 200_000, report["starting_context_median"]
    assert report["main_sessions"] == 1, report["main_sessions"]
    # Floor: min(context, 200k) at $0.20 per million across 3 main calls.
    assert _row(report["starting_context_floor"], "Re-reading the starting context")["cost_usd"] == 0.12
    reads = _row(report["tool_results_carried"], "Full file reads over 350 lines")
    assert reads["count"] == 1, report["tool_results_carried"]
    assert reads["cost_usd"] > 0, reads
    return "a 400-line full read lands in the large-read row, and the floor is priced as cache reads"


def test_small_and_ranged_reads_are_not_large() -> str:
    small = tr._classify_tool_result("Read", {"file_path": "x"}, "a\nb")
    ranged = tr._classify_tool_result("Read", {"file_path": "x", "limit": 900}, "\n" * 900)
    piped = tr._classify_tool_result("Bash", {"command": "cat x | head"}, "\n" * 900)
    shell = tr._classify_tool_result("Bash", {"command": "cat x"}, "\n" * 900)
    assert (small, ranged, piped) == ("Other reads", "Other reads", "Shell output")
    assert shell == "Shell file reads over 350 lines", shell
    return "only unranged reads and unpiped shell reads over 350 lines count as large"


def test_unknown_model_is_reported_not_priced() -> str:
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d) / "s.jsonl", [
            _assistant("u1", "2026-11-01T09:00:00Z", _usage(cache_read=1_000), model="claude-future-9"),
        ])
        report = tr.attribute_spend([path], SINCE, UNTIL)
        rendered = tr.render_attribution(report, "2026-11-01", "2026-11-01")
    assert report["total_cost_usd"] == 0.0, report
    assert report["unpriced_models"] == {"claude-future-9": 1_000}, report
    assert "claude-future-9" in rendered, rendered
    return "an unknown model is listed in the report and excluded from cost"


def test_report_never_contains_transcript_text() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = tr.attribute_spend(_fixture(Path(d)), SINCE, UNTIL)
    rendered = tr.render_attribution(report, "2026-11-01", "2026-11-01")
    assert SENTINEL not in rendered, "rendered report leaked transcript text"
    assert SENTINEL not in json.dumps(report), "JSON report leaked transcript text"
    return "tool inputs and results never reach the markdown or JSON output"


def test_every_table_row_is_numbered() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = tr.attribute_spend(_fixture(Path(d)), SINCE, UNTIL)
    rendered = tr.render_attribution(report, "2026-11-01", "2026-11-01")
    headers = [line for line in rendered.splitlines() if line.startswith("| ") and "Cost" in line]
    assert headers, "no tables rendered: a vacuous pass, not a clean one"
    assert all(h.startswith("| # |") for h in headers), headers
    return f"{len(headers)} tables all lead with a # column"


def test_transcript_paths_filters_by_project() -> str:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        keep = _write(root / "keepme" / "a.jsonl", [])
        _write(root / "other" / "b.jsonl", [])
        assert tr.transcript_paths(root, 0, "KEEPME") == [keep]
        assert len(tr.transcript_paths(root, 0, None)) == 2
        assert tr.transcript_paths(root, 9_999_999_999, None) == []
    return "paths filter by case-insensitive project substring and modification time"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    assert tests, "no tests collected"
    failed = 0
    for fn in tests:
        try:
            detail = fn()
            print(f"PASS: {fn.__name__}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {fn.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
