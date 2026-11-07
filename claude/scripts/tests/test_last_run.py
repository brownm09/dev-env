#!/usr/bin/env python3
"""Unit tests for _last_run.py, the stored findings behind "print only what changed".

Required when changing claude/scripts/_last_run.py; also run test_reconcile_project_board.py and
test_prune_merged_worktrees.py, the two scripts that use it (dev-env#1307, ADR-160).
Run: py -3 claude/scripts/tests/test_last_run.py

Uses real files in a temp directory, never the real state under ~/.claude/scratch/last-run/.

Exit 0 = all pass.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _last_run import LastRun  # noqa: E402


def _quiet_load(path) -> LastRun:
    with contextlib.redirect_stdout(io.StringIO()):
        return LastRun(path)


def test_missing_file_is_a_baseline() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            run = LastRun(Path(tmp) / "absent.json")
        assert run.is_baseline
        assert "no usable state" in out.getvalue(), out.getvalue()
        assert run.record("r", "s", {"a": "1"}) == (["a"], [])
    return "no file: baseline, says so, and every finding reads as new"


def test_unreadable_files_are_baselines() -> str:
    bad = {
        "bad JSON": b"{not json",
        "bad UTF-8": b'{"r": "\xff"}',
        "not an object": b"[1, 2]",
        "repo not an object": b'{"r": 1}',
        "section not an object": b'{"r": {"s": 1}}',
        "value not a string": b'{"r": {"s": {"a": 1}}}',
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        for label, content in bad.items():
            path.write_bytes(content)
            assert _quiet_load(path).is_baseline, label
        # A path that exists but can't be opened as a file.
        assert _quiet_load(Path(tmp)).is_baseline, "a directory"
        # Control: the same path holding a good file is not a baseline.
        path.write_text('{"r": {"s": {"a": "1"}}}', encoding="utf-8")
        assert not _quiet_load(path).is_baseline, "control"
    return "bad JSON, bad UTF-8, four wrong shapes and a directory are baselines; a good file isn't"


def test_round_trip_reports_only_differences() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "state.json"
        first = _quiet_load(path)
        first.record("r", "s", {"same": "1", "reworded": "old", "going": "1"})
        first.save()

        second = _quiet_load(path)
        assert not second.is_baseline
        changed, gone = second.record("r", "s", {"same": "1", "reworded": "new", "added": "1"})
        assert changed == ["added", "reworded"], changed
        assert gone == ["going"], gone
        assert second.changed["s"] == 2 and second.gone["s"] == 1
    return "a saved run's unchanged key stays quiet; new, changed and gone keys are reported"


def test_unrecorded_sections_and_repos_are_kept() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        first = _quiet_load(path)
        first.record("failed-tonight", "s", {"a": "1"})
        first.record("ok", "s", {"b": "1"})
        first.save()

        second = _quiet_load(path)
        second.record("ok", "s", {})
        second.save()

        third = _quiet_load(path)
        assert third.prior("failed-tonight", "s") == {"a": "1"}, "an unrecorded repo was lost"
        assert third.prior("ok", "s") == {}, "a recorded empty section was not stored"
    return "a repo a run never recorded keeps its findings, so it isn't all new tomorrow"


def test_save_failure_warns_and_does_not_raise() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "file"
        blocker.write_text("x", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            LastRun(blocker / "state.json").save()
        assert "WARNING: could not write" in out.getvalue(), out.getvalue()
    return "a state file that can't be written warns, since the run's work is already done"


def test_saved_file_is_plain_json_with_no_tmp_left() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        run = _quiet_load(path)
        run.record("r", "s", {"a": "1"})
        run.save()
        assert json.loads(path.read_text(encoding="utf-8")) == {"r": {"s": {"a": "1"}}}
        assert [p.name for p in Path(tmp).iterdir()] == ["state.json"]
    return "the state file is readable JSON and the atomic swap leaves no temp file"


def main() -> int:
    tests = [
        ("missing file is a baseline", test_missing_file_is_a_baseline),
        ("unreadable files are baselines", test_unreadable_files_are_baselines),
        ("round trip reports only differences", test_round_trip_reports_only_differences),
        ("unrecorded repos are kept", test_unrecorded_sections_and_repos_are_kept),
        ("save failure warns, does not raise", test_save_failure_warns_and_does_not_raise),
        ("saved file is plain JSON, no temp left", test_saved_file_is_plain_json_with_no_tmp_left),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
