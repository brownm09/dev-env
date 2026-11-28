#!/usr/bin/env python3
"""Tests for replay-exit-code-presence.py (dev-env#1251).

Required when changing claude/scripts/replay-exit-code-presence.py.
Run: py -3 claude/scripts/tests/test_replay_exit_code_presence.py

The replay is the evidence for removing the hooks' exit-code branches, so its
counter is calibrated both ways here: a synthetic transcript holds a result WITH
``exitCode`` (must count 1) and results without it (must count 0), plus an
error result, a result older than the window, and a merge that printed gh's
marker.

Usage:
    py -3 claude/scripts/tests/test_replay_exit_code_presence.py
"""
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "replay-exit-code-presence.py"
sys.path.insert(0, str(SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("replay_exit_code_presence", SCRIPT)
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)

NOW = datetime.now(timezone.utc)


def _pair(i, command, result, age_days=1):
    ts = (NOW - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    use = {"type": "assistant", "timestamp": ts, "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": command}}]}}
    res = {"type": "user", "timestamp": ts, "toolUseResult": result, "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x"}]}}
    return [json.dumps(use), json.dumps(res)]


def _corpus(tmp):
    lines = []
    lines += _pair(1, "gh pr merge 5 --squash", {"stdout": "", "stderr": ""})
    lines += _pair(2, "gh pr merge 6 --squash", {"stdout": "", "stderr": "", "exitCode": 0})
    lines += _pair(3, "gh pr merge 7", {"stdout": "", "stderr": "✓ Squashed and merged pull request #7 (t)"})
    lines += _pair(4, "gh pr merge 8", "Error: Exit code 1\nboom")
    lines += _pair(5, "git -C repo push origin b", {"stdout": "", "stderr": "done"})
    lines += _pair(6, "gh pr merge 9", {"stdout": "", "stderr": "", "exitCode": 1}, age_days=90)
    lines += _pair(7, "ls", {"stdout": "a", "stderr": ""})
    lines.append("not json")
    path = Path(tmp) / "proj" / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_counts_by_class_within_window() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _corpus(tmp)
        classes = replay.analyze(Path(tmp), days=60)["classes"]
    merge = classes["gh pr merge"]
    assert merge == {"results": 4, "dict_results": 3, "with_exitCode": 1,
                     "with_merge_marker": 1, "error_results": 1}, merge
    assert classes["git push"] == {"results": 1, "dict_results": 1}, classes["git push"]
    assert classes["other"] == {"results": 1, "dict_results": 1}, classes["other"]
    return "merge: 4 results, 1 with exitCode, 1 marker, 1 error; 90-day-old result excluded"


def test_wider_window_includes_old_result() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _corpus(tmp)
        merge = replay.analyze(Path(tmp), days=120)["classes"]["gh pr merge"]
    assert merge["results"] == 5 and merge["with_exitCode"] == 2, merge
    return "--days 120 picks up the 90-day-old result"


def test_classify() -> str:
    assert replay.classify("cd x && gh.exe pr merge 3") == "gh pr merge"
    assert replay.classify("git -c a=b push") == "git push"
    assert replay.classify("gh issue create -t x") == "gh create"
    assert replay.classify("git status") == "other"
    return "merge / push / create / other classified"


def main() -> int:
    tests = [
        ("counts by class within the window", test_counts_by_class_within_window),
        ("a wider window includes the old result", test_wider_window_includes_old_result),
        ("classify", test_classify),
    ]
    failed = 0
    for name, fn in tests:
        try:
            print(f"PASS: {name}\n      {fn()}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL: {name}\n      {type(exc).__name__}: {exc}")
    print(f"\nTests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
