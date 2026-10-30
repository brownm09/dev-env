#!/usr/bin/env python3
"""hook-timing-report.py — summarize per-hook durations from the heartbeat ledger.

Every wired hook calls ``_hookutil.record_heartbeat`` as the first statement of
``main()``. Since dev-env#848 that call also appends one line per fire to
``~/.claude/scratch/hook-heartbeat/<hook>.dur.jsonl``:

    {"t": <unix time>, "run_ms": <hook body wall time>, "startup_ms": <int or null>}

``run_ms`` runs from ``main()``'s first statement to process exit. ``startup_ms``
runs from the py launcher's process creation to that first statement, so it
covers launcher start, interpreter start and the hook's module imports. It is
sampled (one fire in ``_hookutil.STARTUP_SAMPLE_EVERY``) because measuring it
needs ctypes, so most lines carry ``null``.

This report prints one row per hook: fires, the median and 90th percentile of
each measure, and how many startup samples back the startup columns. Its job is
the before/after evidence for the hot-path gating (dev-env#849) and the hook
dispatcher (dev-env#851): the dispatcher removes a launch per hook, so the
startup columns are what it should shrink.

Read-only. The report goes to stdout; exit 0 even when there is no data (it
says so), non-zero only on a usage error. Stdlib only, no subprocess.

Usage:
    py -3 claude/scripts/hook-timing-report.py [--since-hours N] [--dir PATH]

Options:
    --since-hours N   Only fires in the last N hours (default 168, one week).
    --dir PATH        Ledger directory (default: _hookutil.HEARTBEAT_DIR).

See ADR-106 (heartbeat ledger), Amendment 2.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hookutil  # noqa: E402


def percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile of *values*, or ``None`` when empty."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(len(ordered) * pct / 100)) - 1]


def load_hook(path: Path, since: float) -> dict:
    """Run and startup samples from one ``.dur.jsonl`` file, fires at/after *since*.

    Malformed lines are skipped: the writer is best-effort and a torn line from
    a concurrent compaction is possible."""
    runs: list[int] = []
    startups: list[int] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get("t"), (int, float)):
            continue
        if rec["t"] < since:
            continue
        if isinstance(rec.get("run_ms"), int):
            runs.append(rec["run_ms"])
        if isinstance(rec.get("startup_ms"), int):
            startups.append(rec["startup_ms"])
    return {"runs": runs, "startups": startups}


def build_rows(ledger: Path, since: float) -> list[dict]:
    """One row per hook with any fire in the window, slowest median run first."""
    rows = []
    for path in sorted(ledger.glob(f"*{_hookutil.DURATION_SUFFIX}")):
        data = load_hook(path, since)
        if not data["runs"]:
            continue
        rows.append({
            "hook": path.name[: -len(_hookutil.DURATION_SUFFIX)],
            "fires": len(data["runs"]),
            "run_p50": percentile(data["runs"], 50),
            "run_p90": percentile(data["runs"], 90),
            "startup_p50": percentile(data["startups"], 50),
            "startup_p90": percentile(data["startups"], 90),
            "startup_n": len(data["startups"]),
        })
    rows.sort(key=lambda r: (-r["run_p50"], r["hook"]))
    return rows


def render(rows: list[dict], since_hours: float) -> str:
    if not rows:
        return f"No hook duration data in the last {since_hours:g} hours.\n"
    show = lambda v: "-" if v is None else str(v)  # noqa: E731
    lines = [
        f"Hook durations, last {since_hours:g} hours (ms; startup is sampled)",
        "",
        "| # | Hook | Fires | Run p50 | Run p90 | Startup p50 | Startup p90 | Startup samples |",
        "|---|------|-------|---------|---------|-------------|-------------|-----------------|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['hook']} | {r['fires']} | {r['run_p50']} | {r['run_p90']} | "
            f"{show(r['startup_p50'])} | {show(r['startup_p90'])} | {r['startup_n']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since-hours", type=float, default=168.0)
    parser.add_argument("--dir", type=Path, default=_hookutil.HEARTBEAT_DIR)
    args = parser.parse_args(argv)
    if args.since_hours <= 0:
        parser.error("--since-hours must be positive")
    if not args.dir.is_dir():
        sys.stdout.write(f"No heartbeat ledger at {args.dir}.\n")
        return 0
    rows = build_rows(args.dir, time.time() - args.since_hours * 3600)
    sys.stdout.write(render(rows, args.since_hours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
