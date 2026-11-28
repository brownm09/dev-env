#!/usr/bin/env python3
"""Count how many recorded shell results carry an ``exitCode`` key (dev-env#1251).

Purpose: replay transcripts to measure whether PostToolUse payloads ever hold ``exitCode``.

Seven hooks used to read ``_hookio.read_exit_code``, assuming a PostToolUse
payload's ``tool_response`` may hold ``exitCode``. A session transcript records that same
object as the ``toolUseResult`` of the user record that carries the tool_result,
so a replay over ``~/.claude/projects/**/*.jsonl`` measures the assumption
directly instead of waiting on a forward log.

Each Bash/PowerShell call is classified by the command it ran: ``gh pr merge``,
``git push``, ``gh pr create`` / ``gh issue create``, or other. For each class the
report counts results, results whose ``toolUseResult`` is a dict (the shape a
PostToolUse payload has), how many of those carry ``exitCode``, how many print
gh's completed-merge line (``output_has_merge_marker``), and error results
(a string ``toolUseResult``: a non-zero exit, which goes to PostToolUseFailure,
not PostToolUse).

Usage:
    py -3 claude/scripts/replay-exit-code-presence.py [--days 60] [--json] [--scan-dir DIR]

Reads only; prints counts, never commands.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hookio import GH_PR_MERGE, output_has_merge_marker  # noqa: E402

DEFAULT_SCAN_DIR = Path.home() / ".claude" / "projects"

CLASSES = (
    ("gh pr merge", re.compile(GH_PR_MERGE, re.IGNORECASE)),
    ("git push", re.compile(r"\bgit(?:\.exe)?\s+(?:-[Cc]\s+\S+\s+)*push\b", re.IGNORECASE)),
    ("gh create", re.compile(r"\bgh(?:\.exe)?\s+(?:pr|issue)\s+create\b", re.IGNORECASE)),
)


def classify(command: str) -> str:
    for name, pattern in CLASSES:
        if pattern.search(command):
            return name
    return "other"


def _timestamp(record: dict) -> float | None:
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iter_results(scan_dir: Path, since: float):
    """Yield (command, toolUseResult) for each Bash/PowerShell result after *since*."""
    for path in sorted(Path(scan_dir).rglob("*.jsonl")):
        try:
            if path.stat().st_mtime < since:
                continue
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        pending: dict = {}
        with handle:
            for line in handle:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                content = (record.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") in ("Bash", "PowerShell"):
                        command = (block.get("input") or {}).get("command")
                        if isinstance(command, str):
                            pending[block.get("id")] = command
                    elif block.get("type") == "tool_result":
                        command = pending.pop(block.get("tool_use_id"), None)
                        ts = _timestamp(record)
                        if command is not None and ts is not None and ts >= since:
                            yield command, record.get("toolUseResult")


def analyze(scan_dir: Path, days: int) -> dict:
    since = time.time() - days * 86400
    counts = {name: Counter() for name, _ in CLASSES}
    counts["other"] = Counter()
    for command, result in iter_results(scan_dir, since):
        c = counts[classify(command)]
        c["results"] += 1
        if isinstance(result, dict):
            c["dict_results"] += 1
            if "exitCode" in result:
                c["with_exitCode"] += 1
            output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}"
            if output_has_merge_marker(output):
                c["with_merge_marker"] += 1
        elif isinstance(result, str):
            c["error_results"] += 1
    return {"days": days, "scan_dir": str(scan_dir), "classes": {k: dict(v) for k, v in counts.items()}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--scan-dir", type=Path, default=DEFAULT_SCAN_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = analyze(args.scan_dir, args.days)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"Last {report['days']} days under {report['scan_dir']}")
    print(f"{'class':<12} {'results':>8} {'dict':>8} {'exitCode':>9} {'marker':>8} {'errors':>8}")
    for name, c in report["classes"].items():
        print(f"{name:<12} {c.get('results', 0):>8} {c.get('dict_results', 0):>8} "
              f"{c.get('with_exitCode', 0):>9} {c.get('with_merge_marker', 0):>8} "
              f"{c.get('error_results', 0):>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
