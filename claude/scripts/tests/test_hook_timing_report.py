#!/usr/bin/env python3
"""Tests for hook-timing-report.py (dev-env#848, ADR-106 Amendment 2).

The report reads the ``<hook>.dur.jsonl`` files `_hookutil.record_duration`
writes beside each heartbeat and prints per-hook percentiles. Offline: every
case builds its own ledger in a temp directory.

Usage:
    py -3 claude/scripts/tests/test_hook_timing_report.py

Exit 0 = all pass.
"""
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest.mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("hook_timing_report", SCRIPTS / "hook-timing-report.py")
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)


def _write(ledger: Path, hook: str, records: list) -> None:
    ledger.mkdir(parents=True, exist_ok=True)
    lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
    (ledger / f"{hook}.dur.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(argv: list) -> tuple:
    out = io.StringIO()
    with unittest.mock.patch.object(sys, "stdout", out):
        rc = report.main(argv)
    return rc, out.getvalue()


def test_percentile_nearest_rank() -> str:
    vals = [50, 10, 40, 20, 30]
    assert report.percentile(vals, 50) == 30
    assert report.percentile(vals, 90) == 50
    assert report.percentile([7], 90) == 7
    assert report.percentile([], 50) is None
    return "percentile is nearest-rank, handles one value and none"


def test_load_hook_filters_window_and_malformed() -> str:
    now = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp)
        _write(ledger, "h", [
            {"t": now - 10, "run_ms": 5, "startup_ms": 900},
            {"t": now - 10, "run_ms": 7, "startup_ms": None},
            {"t": now - 99999, "run_ms": 1000, "startup_ms": 1000},  # outside window
            "{not json",
            ["not", "a", "dict"],
            {"run_ms": 3},  # no timestamp
        ])
        data = report.load_hook(ledger / "h.dur.jsonl", now - 3600)
    assert data == {"runs": [5, 7], "startups": [900], "oldest": now - 10}, data
    return "load_hook keeps in-window samples and skips malformed or untimestamped lines"


def test_main_renders_numbered_table_slowest_first() -> str:
    now = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp)
        _write(ledger, "fast-hook", [{"t": now, "run_ms": 3, "startup_ms": None}])
        _write(ledger, "slow-hook", [
            {"t": now - 1800, "run_ms": 80, "startup_ms": 1200},
            {"t": now, "run_ms": 80, "startup_ms": None},
        ])
        (ledger / "slow-hook.ts").write_text(str(now), encoding="utf-8")  # heartbeat, not data
        rc, out = _run(["--dir", str(ledger), "--since-hours", "1"])
    assert rc == 0, rc
    rows = [l for l in out.splitlines() if l.startswith("| ") and not l.startswith("| #")]
    assert rows[0] == "| 1 | slow-hook | 2 | 80 | 80 | 1200 | 1200 | 1 | 0.5 |", rows
    assert rows[1] == "| 2 | fast-hook | 1 | 3 | 3 | - | - | 0 | 0 |", rows
    assert len(rows) == 2, rows
    return ("main prints a #-numbered table, slowest median run first, '-' for unsampled "
            "startup, and how many hours back each hook's data reaches")


def test_main_no_data_and_missing_dir() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = _run(["--dir", tmp])
        assert rc == 0 and out.startswith("No hook duration data"), out
        rc, out = _run(["--dir", str(Path(tmp) / "absent")])
        assert rc == 0 and out.startswith("No heartbeat ledger"), out
    return "an empty or missing ledger reports so and exits 0"


def test_main_rejects_nonpositive_window() -> str:
    err = io.StringIO()
    with unittest.mock.patch.object(sys, "stderr", err):
        try:
            report.main(["--since-hours", "0"])
        except SystemExit as exc:
            assert exc.code == 2, exc.code
        else:
            raise AssertionError("--since-hours 0 must be a usage error")
    return "--since-hours 0 is a usage error (exit 2)"


def test_reads_what_hookutil_writes() -> str:
    import _hookutil
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp)
        _hookutil.record_duration("real", 42, 1100, heartbeat_dir=ledger)
        rc, out = _run(["--dir", str(ledger)])
    assert rc == 0 and "| 1 | real | 1 | 42 | 42 | 1100 | 1100 | 1 | 0 |" in out, out
    return "the report reads the exact format _hookutil.record_duration writes"


def main() -> int:
    tests = [
        ("percentile: nearest rank", test_percentile_nearest_rank),
        ("load_hook: window + malformed lines", test_load_hook_filters_window_and_malformed),
        ("main: numbered table, slowest first", test_main_renders_numbered_table_slowest_first),
        ("main: no data / missing dir", test_main_no_data_and_missing_dir),
        ("main: rejects --since-hours 0", test_main_rejects_nonpositive_window),
        ("round trip: reads _hookutil.record_duration output", test_reads_what_hookutil_writes),
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
