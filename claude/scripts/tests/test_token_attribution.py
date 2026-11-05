#!/usr/bin/env python3
"""Tests for _token_pricing.py and token-report.py --attribution (dev-env#1214, #1122).

The attribution fixture is a small transcript whose expected dollars are worked out
by hand here, so a pricing or attribution slip changes a pinned number.

Usage:
    py -3 claude/scripts/tests/test_token_attribution.py

Exit 0 = all pass.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _token_pricing as tp  # noqa: E402

_spec = importlib.util.spec_from_file_location("token_report", SCRIPTS / "token-report.py")
assert _spec and _spec.loader
tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tr)  # safe: main() is guarded by __main__

_spec = importlib.util.spec_from_file_location("backfill_tokens", SCRIPTS / "backfill-tokens.py")
assert _spec and _spec.loader
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)  # safe: main() is guarded by __main__

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


def _attribute(tmp, records, name="s.jsonl"):
    return tr.attribute_spend([_write(Path(tmp) / name, records)], SINCE, UNTIL)


def _compaction():
    return {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-01T09:00:00Z"}


def test_successor_models_are_unpriced() -> str:
    for model in ("claude-opus-5-6", "claude-opus-5-1", "claude-sonnet-5-6", "claude-haiku-5-55",
                  "claude-opus-4-80", "not-claude-opus-5-5-at-all", None, 5):
        assert tp.get_pricing(model) is None, model
    for model in ("claude-opus-5-5", "claude-opus-5-5-20260901", "claude-opus-5-5[1m]", "claude-opus-5-5-20260901[1m]"):
        assert tp.get_pricing(model)["input"] == 4.00, model
    return "only an exact key, with an optional date or bracket suffix, gets a price"


def test_every_price_row_keeps_its_published_ratios() -> str:
    rows = {**tp.PRICING, "long prompt": tp.HAIKU_5_5_LONG_PROMPT}
    for name, row in rows.items():
        assert row["cache_write"] == row["input"] * 1.25, name
        assert row["cache_write_1h"] == row["input"] * 2, name
        assert row["output"] == row["input"] * 5, name
    return f"{len(rows)} rows: 5-minute writes 1.25x, hourly writes 2x, output 5x input"


def test_flat_usage_survives_odd_shapes() -> str:
    assert tp.flat_usage("nope") == {} and tp.flat_usage(None) == {} and tp.flat_usage([1]) == {}
    for nested in (5, "x", [1], None, {"ephemeral_1h_input_tokens": None}, {"ephemeral_1h_input_tokens": "x"}):
        flat = tp.flat_usage({"input_tokens": 1, "cache_creation": nested})
        assert flat["cache_write_1h_input_tokens"] == 0, nested
    assert tp.flat_usage({"input_tokens": None, "output_tokens": 1})["input_tokens"] == 0
    assert tp.flat_usage({"input_tokens": True, "output_tokens": 1})["input_tokens"] == 0
    return "a wrong-shaped count becomes 0 instead of raising"


def test_flat_usage_reads_iterations_when_top_level_is_zero() -> str:
    usage = _usage()
    usage["iterations"] = [_usage(cache_read=10, write_1h=2), _usage(cache_read=5, output=1), "odd"]
    flat = tp.flat_usage(usage)
    assert flat["cache_read_input_tokens"] == 15, flat
    assert flat["cache_write_1h_input_tokens"] == 2, flat
    assert flat["output_tokens"] == 1, flat
    usage = _usage(cache_read=99)
    usage["iterations"] = [_usage(cache_read=10)]
    assert tp.flat_usage(usage)["cache_read_input_tokens"] == 99
    return "iterations fill in only when every top-level count is zero"


def test_write_tier_clamp_handles_a_zero_flat_count() -> str:
    # 54 real records in the baseline window have this shape.
    usage = tp.flat_usage({"cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}, "output_tokens": 1})
    assert tp.cost_buckets(usage, tp.get_pricing("claude-opus-5-5"))["cache_write"] == 0.0
    return "a nested hourly count can't exceed the flat write count, so the cost isn't negative"


def test_non_standard_rate_is_reported_not_priced() -> str:
    for extra in ({"speed": "fast"}, {"inference_geo": "us"}):
        with tempfile.TemporaryDirectory() as d:
            report = _attribute(d, [_assistant("f", "2026-11-01T09:00:00Z", {**_usage(cache_read=1_000), **extra})])
        assert report["total_cost_usd"] == 0.0, extra
        assert report["unpriced_models"] == {"claude-opus-5-5 (fast mode or US-only inference)": 1_000}, report
    return "fast mode and US-only calls are listed as unpriced rather than underpriced"


def test_haiku_long_prompt_tier_prices_per_call() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, [
            _assistant("h1", "2026-11-01T09:00:00Z", _usage(cache_read=2_000_000), model="claude-haiku-5-5"),
            _assistant("h2", "2026-11-01T09:01:00Z", _usage(cache_read=100_000), model="claude-haiku-5-5"),
        ])
    assert report["total_cost_usd"] == 0.10, report["total_cost_usd"]
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, [
            _assistant("h3", "2026-11-01T09:00:00Z", _usage(input_tokens=200_000), model="claude-haiku-5-5"),
        ])
    assert report["total_cost_usd"] == 0.10, report["total_cost_usd"]
    assert _row(report["by_context_band"], "200k to 400k")["count"] == 1, report["by_context_band"]
    return "2M cached at the long-prompt $0.05 is $0.10, 100k at the short $0.01 rounds to $0.00, and uncached input counts toward prompt size"


def test_message_id_counts_once_across_files() -> str:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        call = _assistant("shared", "2026-11-01T09:00:00Z", _usage(write_1h=1_000_000))
        paths = [
            _write(root / "p" / "s" / "subagents" / "agent-1.jsonl", [call]),
            _write(root / "p" / "b.jsonl", [_tool_result("x", "y"), call]),
            _write(root / "p" / "a.jsonl", [call]),
        ]
        report = tr.attribute_spend(paths, SINCE, UNTIL)
    assert report["calls"] == 1, report["calls"]
    assert report["total_cost_usd"] == 8.0, report["total_cost_usd"]
    assert _row(report["by_scope"], "Main sessions")["cost_usd"] == 8.0, report["by_scope"]
    assert report["main_sessions"] == 1, report["main_sessions"]
    return "a call copied by a resumed session or subagent is priced once, as main-session spend"


def test_all_zero_record_does_not_replace_a_real_one() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, [
            _assistant("z", "2026-11-01T09:00:00Z", _usage(cache_read=1_000_000)),
            _assistant("z", "2026-11-01T09:00:00Z", _usage()),
            _assistant("only-zero", "2026-11-01T09:00:01Z", _usage()),
        ])
    assert report["total_cost_usd"] == 0.20, report["total_cost_usd"]
    assert report["calls"] == 1, report["calls"]
    return "an all-zero tail keeps the $0.20 call, and an all-zero call isn't a call"


def test_iterations_counts_reach_the_report() -> str:
    usage = _usage()
    usage["iterations"] = [_usage(cache_read=1_000_000)]
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, [_assistant("i", "2026-11-01T09:00:00Z", usage)])
    assert report["total_cost_usd"] == 0.20, report["total_cost_usd"]
    return "a record with its counts only under iterations is priced from them"


def test_tool_result_row_pins_dollars() -> str:
    # 400 lines of 6,500 characters is 2.6M characters, or 1M estimated tokens.
    big = ("x" * 6499 + "\n") * 400
    records = [
        _assistant("a", "2026-11-01T09:00:00Z", _usage(write_1h=1_000), tool_uses=[("t1", "Read", {"file_path": "f"}), ("t2", "Read", {"file_path": "f"}), ("t3", "Read", {"file_path": "f"})]),
        _tool_result("t1", big),
        _assistant("b", "2026-11-01T09:01:00Z", _usage(cache_read=1_000, write_1h=1_000_000)),  # $8 write
        _assistant("c", "2026-11-01T09:02:00Z", _usage(cache_read=1_001_000)),  # $0.20 read
        _tool_result("t2", big),
        _compaction(),
        _assistant("d", "2026-11-01T09:03:00Z", _usage(write_1h=100)),  # t1 gone, t2 never carried
        _assistant("e", "2026-11-01T09:04:00Z", _usage(cache_read=100), tool_uses=[]),
        _tool_result("t3", big),
        _assistant("f", "2026-11-01T23:59:00Z", _usage(cache_read=100, write_1h=1_000_000)),  # $8 write
        _assistant("g", "2026-11-02T00:01:00Z", _usage(cache_read=1_000_100)),  # after the window, not charged
    ]
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, records)
    row = _row(report["tool_results_carried"], "Full file reads over 350 lines")
    assert row["cost_usd"] == 16.20, row
    assert row["count"] == 2, row
    return "t1 costs a write and one read, t2 nothing, and t3 only its write inside the window"


def test_result_from_before_the_window_charges_only_later_reads() -> str:
    big = ("x" * 6499 + "\n") * 400
    records = [
        _assistant("a", "2026-10-31T09:00:00Z", _usage(write_1h=1_000), tool_uses=[("t", "Read", {"file_path": "f"})]),
        _tool_result("t", big),
        _assistant("b", "2026-10-31T09:01:00Z", _usage(write_1h=1_000_000)),
        _assistant("c", "2026-11-01T09:00:00Z", _usage(cache_read=1_001_000)),
    ]
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, records)
    row = _row(report["tool_results_carried"], "Full file reads over 350 lines")
    assert row["cost_usd"] == 0.20, row
    assert row["count"] == 0, row
    return "a read inside the window is charged, and the earlier write and arrival aren't"


def test_recache_needs_a_lost_prefix_and_a_large_write() -> str:
    records = [
        _assistant("a", "2026-11-01T09:00:00Z", _usage(write_1h=200_000)),
        # Two hours idle but the prefix is still cached: growth, not a re-cache.
        _assistant("b", "2026-11-01T11:00:00Z", _usage(cache_read=200_000, write_1h=60_000)),
        # Prefix lost, but too small a write to count.
        _assistant("c", "2026-11-01T13:00:00Z", _usage(cache_read=1_000, write_1h=40_000)),
        _assistant("d", "2026-11-01T13:01:00Z", _usage(cache_read=41_000)),
        # Prefix lost and a large write: the only re-cache. 100k hourly is $0.80.
        _assistant("e", "2026-11-01T13:02:00Z", _usage(write_1h=100_000)),
        _compaction(),
        # A write at a compaction has its own row. 200k hourly is $1.60.
        _assistant("f", "2026-11-01T15:00:00Z", _usage(write_1h=200_000)),
    ]
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, records)
    gaps = report["mid_session_recache_by_gap"]
    assert _row(gaps, "over 60 minutes")["count"] == 0, gaps
    assert _row(gaps, "under 5 minutes")["count"] == 1, gaps
    assert _row(gaps, "under 5 minutes")["cost_usd"] == 0.80, gaps
    rows = report["cache_writes_at_compaction"]
    assert rows[0]["count"] == 1 and rows[0]["cost_usd"] == 1.60, rows
    return "growth, a small write and a compaction don't count as idle re-caching"


def test_malformed_records_are_counted_not_fatal() -> str:
    good = _assistant("g", "2026-11-01T09:00:00Z", _usage(cache_read=1_000_000))
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.jsonl"
        lines = [json.dumps(r) for r in [
            ["not", "an", "object"],
            {"type": "assistant", "timestamp": "2026-11-01T09:00:00Z", "message": {"id": ["unhashable"], "usage": _usage(cache_read=5)}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": ["x"], "name": "Read"}]}},
            {"type": "assistant", "timestamp": "2026-11-01T09:00:00Z", "message": {"id": "u", "usage": ["odd"]}},
            {"type": "assistant", "timestamp": "2026-11-01T09:00:00Z", "message": {"id": "m", "model": 5, "usage": _usage(cache_read=1)}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": [{"text": None}]}]}},
            good,
        ]]
        path.write_text("{bad json\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
        report = tr.attribute_spend([path], SINCE, UNTIL)
        rendered = tr.render_attribution(report, "2026-11-01", "2026-11-01")
    assert report["total_cost_usd"] == 0.20, report
    assert report["lines_skipped"] == 5, report["lines_skipped"]
    assert "(no model)" in report["unpriced_models"], report["unpriced_models"]
    assert "skipped 5 malformed lines" in rendered, rendered
    return "wrong shapes are skipped and counted, and the rest of the file still reports"


def test_missing_transcripts_are_stated() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = tr.attribute_spend([Path(d) / "gone.jsonl"], SINCE, UNTIL)
        empty = tr.attribute_spend([], SINCE, UNTIL)
    assert report["files_unreadable"] == 1 and report["files_read"] == 0, report
    assert "No transcripts matched" in tr.render_attribution(empty, "2026-11-01", "2026-11-01")
    return "an unreadable file is counted, and an empty match says so instead of showing $0.00"


def test_file_starting_after_the_window_is_not_read() -> str:
    with tempfile.TemporaryDirectory() as d:
        path = _write(Path(d) / "later.jsonl", [_assistant("l", "2026-11-03T09:00:00Z", _usage(cache_read=1))])
        report = tr.attribute_spend([path], SINCE, UNTIL)
    assert report["files_read"] == 0, report
    return "a transcript whose first timestamp is after the window is skipped without parsing"


def test_transcript_text_cannot_reach_output_through_prompt_or_model() -> str:
    with tempfile.TemporaryDirectory() as d:
        report = _attribute(d, [
            {"type": "user", "message": {"content": SENTINEL}},
            {"type": "user", "message": {"content": [{"type": "text", "text": SENTINEL}]}},
            _assistant("p", "2026-11-01T09:00:00Z", _usage(cache_read=1_000),
                       model=f"claude-opus-5-5 {SENTINEL} | ![x](http://attacker.example/a)"),
        ])
    rendered = tr.render_attribution(report, "2026-11-01", "2026-11-01")
    assert report["unpriced_models"] == {"(unrecognised)": 1_000}, report["unpriced_models"]
    for text in (rendered, json.dumps(report)):
        assert SENTINEL not in text, "report leaked transcript text"
        assert "attacker" not in text, "report leaked a model field"
    return "prompts never reach the output, and a model id prints only if it looks like one"


def test_project_filter_uses_the_encoded_directory_name() -> str:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        keep = _write(root / "C--Users-me-Git-dev-env--claude-worktrees-x" / "a.jsonl", [])
        _write(root / "C--Users-me-Git-other" / "b.jsonl", [])
        assert tr.transcript_paths(root, 0, "dev-env/.claude") == [keep]
        assert tr.transcript_paths(root, 0, "a.jsonl") == []
        assert tr.transcript_paths(root, 0, Path(d).name) == []
    return "a cwd substring matches the project directory, not the root path or the file name"


def _run_main(argv, root) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(tr, "TRANSCRIPT_ROOT", root), mock.patch.object(sys, "argv", ["token-report.py", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tr.main()
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


def test_command_line_window_and_project() -> str:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write(root / "keepme" / "a.jsonl", [
            _assistant("first-second", "2026-11-01T00:00:00Z", _usage(cache_read=1_000_000)),
            _assistant("last-second", "2026-11-01T23:59:59Z", _usage(cache_read=1_000_000)),
            _assistant("next-day", "2026-11-02T00:00:00Z", _usage(cache_read=1_000_000)),
        ])
        _write(root / "other" / "b.jsonl", [_assistant("o", "2026-11-01T09:00:00Z", _usage(cache_read=5_000_000))])
        window = ["--attribution", "--since", "2026-11-01", "--until", "2026-11-01"]
        code, out, _ = _run_main([*window, "--project", "keepme", "--format", "json"], root)
        assert code == 0, code
        report = json.loads(out)
        assert report["calls"] == 2, report["calls"]
        assert report["total_cost_usd"] == 0.40, report["total_cost_usd"]
        code, out, _ = _run_main(window, root)
        assert code == 0 and "$1.40 at published API prices" in out, out
        code, out, _ = _run_main([*window, "--project", "nothing-matches"], root)
        assert code == 0 and "No transcripts matched" in out, out
    return "--until is inclusive of its whole UTC day, and --project narrows the files"


def test_default_window_is_fourteen_days_ending_today_utc() -> str:
    seen = {}
    empty_report = tr.attribute_spend([], 0, 0)

    def fake_paths(_root, since, _project):
        seen["since"] = since
        return []

    def fake_attribute(_paths, _since, until):
        seen["until"] = until
        return empty_report

    with mock.patch.object(tr, "transcript_paths", fake_paths), mock.patch.object(tr, "attribute_spend", fake_attribute), \
            contextlib.redirect_stdout(io.StringIO()):
        tr.run_attribution(argparse.Namespace(since=None, until=None, project=None, format="markdown"))
    today = datetime.now(timezone.utc).date()
    assert seen["until"] == tr._utc_day_start(today + timedelta(days=1)), seen
    assert seen["until"] - seen["since"] == 14 * 86_400, seen
    return "the default covers exactly 14 UTC days, ending with today"


def test_incompatible_flags_are_rejected() -> str:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for argv in (["--attribution", "--latest"], ["--attribution", "--days", "1"], ["--attribution", "--date", "2026-11-01"],
                     ["--attribution", "--show-subagents"], ["--attribution", "--format", "summary"],
                     ["--since", "2026-11-01"], ["--until", "2026-11-01"], ["--attribution", "--since", "nope"],
                     ["--attribution", "--since", "2026-11-02", "--until", "2026-11-01"]):
            code, out, err = _run_main(argv, root)
            assert code == 2, (argv, code)
            assert err, argv
            assert "Traceback" not in err, err
    return "a flag that would be ignored, or a bad date, is a usage error rather than a wrong report"


def test_session_tables_mark_unpriced_rows() -> str:
    sessions = [
        {"session_id": "a", "estimated_cost_usd": 2.0, "tokens": {}, "turn_count": 1, "model_unpriced": False},
        {"session_id": "b", "estimated_cost_usd": 0.0, "tokens": {}, "turn_count": 1, "model_unpriced": True},
        {"session_id": "c", "estimated_cost_usd": 1.0, "tokens": {}, "turn_count": 1},
    ]
    table = tr.render_markdown(sessions)
    assert table.count("unpriced") == 2, table
    assert "$0.0000" not in table, table
    assert "**$3.0000** (excludes 1 unpriced)" in table, table
    assert "(excludes 1 unpriced)" in tr.render_summary(sessions)
    assert "unpriced" not in tr.render_summary(sessions[:1])
    return "an unpriced session reads as unpriced, not $0.0000, and the totals say what they leave out"


def test_subagent_with_no_model_is_unpriced() -> str:
    with tempfile.TemporaryDirectory() as d:
        main = Path(d) / "s.jsonl"
        _write(Path(d) / "s" / "subagents" / "agent-1.jsonl", [
            {"type": "assistant", "message": {"usage": {"input_tokens": 1_000_000}}},
        ])
        _write(Path(d) / "s" / "subagents" / "agent-2.jsonl", [
            _assistant("x", "2026-11-01T09:00:00Z", {"cache_creation_input_tokens": 1_000_000, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}),
            _assistant("y", "2026-11-01T09:00:00Z", _usage(), model=tp.SYNTHETIC_MODEL),
        ])
        first, second = tr.read_subagents(str(main))
    assert first["unpriced"] is True and first["cost"] == 0.0, first
    assert second["unpriced"] is False and second["cost"] == 8.0, second
    return "no Sonnet 4.6 default, the hourly tier is priced, and a synthetic record can't rename the model"


def test_backfill_records_model_unpriced() -> str:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        known = _write(root / "known.jsonl", [
            _assistant("k", "2026-11-01T09:00:00Z", {"cache_creation_input_tokens": 1_000_000, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}),
            _assistant("s", "2026-11-01T09:00:00Z", _usage(), model=tp.SYNTHETIC_MODEL),
        ])
        unknown = _write(root / "unknown.jsonl", [_assistant("u", "2026-11-01T09:00:00Z", _usage(cache_read=1), model="claude-future-9")])
        missing = _write(root / "missing.jsonl", [{"type": "assistant", "message": {"usage": {"input_tokens": 1}}}])
        priced = bf.process_transcript(known)
        assert priced["model"] == "claude-opus-5-5" and priced["model_unpriced"] is False, priced
        assert priced["estimated_cost_usd"] == 8.0, priced
        assert bf.process_transcript(unknown)["model_unpriced"] is True
        assert bf.process_transcript(missing)["model_unpriced"] is True
    return "backfill prices the hourly tier, skips synthetic records, and marks unknown models"


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
