#!/usr/bin/env python3
"""Unit tests for sweep-scratch-debris.py (dev-env#768).

Exercises find_stale/sweep offline against real tempfile.TemporaryDirectory()
fixtures (no real ~/.claude/scratch) -- matching test_hookutil.py's precedent
for cleanup-style helpers. Also pins that none of the module docstring's
deliberately-excluded singleton/branch-scoped filenames match any
KNOWN_PATTERNS glob, as a structural safety net against a future edit
accidentally widening a prefix to catch one of them.

Usage:
    py -3 claude/scripts/tests/test_sweep_scratch_debris.py

Exit 0 = all pass.
"""
import fnmatch
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "sweep-scratch-debris.py"
sys.path.insert(0, str(SCRIPT.parent))  # for the script's own `import _hookutil`

_spec = importlib.util.spec_from_file_location("sweep_scratch_debris", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
ssd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ssd)  # safe: main() is guarded by __main__


def _make_stale(path: Path, age_days: int) -> None:
    path.write_text("x")
    past = time.time() - age_days * 86400
    os.utime(path, (past, past))


def test_known_patterns_is_nonempty_list_of_pairs() -> str:
    assert isinstance(ssd.KNOWN_PATTERNS, list) and ssd.KNOWN_PATTERNS, "KNOWN_PATTERNS must be a non-empty list"
    for entry in ssd.KNOWN_PATTERNS:
        assert isinstance(entry, tuple) and len(entry) == 2, f"expected (prefix, ext) pair, got {entry!r}"
        prefix, ext = entry
        assert isinstance(prefix, str) and prefix, f"prefix must be a non-empty str: {entry!r}"
        assert isinstance(ext, str) and ext.startswith("."), f"ext must start with '.': {entry!r}"
    return f"KNOWN_PATTERNS has {len(ssd.KNOWN_PATTERNS)} (prefix, ext) pairs, all well-formed"


def test_excluded_singletons_never_match_any_known_pattern() -> str:
    # Structural safety net for the module docstring's "deliberately excluded"
    # list -- if a future edit widens a prefix to accidentally catch one of
    # these, this test fails loudly instead of silently sweeping a live
    # lock file, a per-hook heartbeat, or a branch-scoped baseline snapshot.
    excluded = [
        "awake.lock", "awake.pid", "awake.log", "awake.log.1",
        "hook-heartbeat/journal-onboard-check.ts",  # per-hook heartbeat, not per-session
        "token-sessions.jsonl", "session-mode-prompt.log",
        "baseline_dev-env_fix-scratch-state-gc.json",
    ]
    for name in excluded:
        for prefix, ext in ssd.KNOWN_PATTERNS:
            pattern = f"{prefix}*{ext}"
            assert not fnmatch.fnmatch(name, pattern), (
                f"excluded filename {name!r} must not match KNOWN_PATTERNS entry {pattern!r}"
            )
    return "none of the documented excluded singleton/branch-scoped files match any KNOWN_PATTERNS glob"


def test_find_stale_returns_only_old_matching_files() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        old = scratch / "journal_onboard_old.flag"
        fresh = scratch / "journal_onboard_fresh.flag"
        other_ext = scratch / "journal_onboard_old.txt"
        other_prefix = scratch / "other_old.flag"
        _make_stale(old, 31)
        _make_stale(fresh, 1)
        _make_stale(other_ext, 31)
        _make_stale(other_prefix, 31)

        result = ssd.find_stale("journal_onboard_", ".flag", scratch, 30)
        assert result == [old], f"expected only {old}, got {result}"
    return "find_stale matches only the given prefix+ext, older than max_age_days"


def test_sweep_dry_run_does_not_delete() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        old = scratch / "session_mode_ack_old.txt"
        _make_stale(old, 31)

        results = ssd.sweep(scratch, 30, apply=False)
        assert results["session_mode_ack_*.txt"] == (1, 1), f"got {results['session_mode_ack_*.txt']}"
        assert old.exists(), "dry run (apply=False) must not delete anything"
    return "sweep(apply=False) reports counts/bytes but leaves files in place"


def test_sweep_apply_deletes_stale_keeps_fresh() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        old = scratch / "disk_space_check_sess_act.flag"
        fresh = scratch / "disk_space_check_sess_warn.flag"
        _make_stale(old, 31)
        _make_stale(fresh, 1)

        results = ssd.sweep(scratch, 30, apply=True)
        assert results["disk_space_check_*.flag"][0] == 1, f"expected 1 removed, got {results}"
        assert not old.exists(), "stale file must be deleted when apply=True"
        assert fresh.exists(), "fresh file must survive"
    return "sweep(apply=True) deletes only the stale matches, keeps fresh ones"


def test_sweep_across_multiple_families_independently() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _make_stale(scratch / "journal_onboard_a.flag", 31)
        _make_stale(scratch / "journal_onboard_b.flag", 31)
        _make_stale(scratch / "turn-count-x.txt", 31)

        results = ssd.sweep(scratch, 30, apply=True)
        assert results["journal_onboard_*.flag"][0] == 2
        assert results["turn-count-*.txt"][0] == 1
        # Untouched families report zero, not an error.
        assert results["bash_state_*.json"] == (0, 0)
    return "sweep tracks every KNOWN_PATTERNS family independently in one pass"


def test_sweep_no_crash_on_missing_scratch_dir() -> str:
    with tempfile.TemporaryDirectory() as root:
        nonexistent = Path(root) / "does-not-exist"
        results = ssd.sweep(nonexistent, 30, apply=True)
        assert all(count == 0 for count, _ in results.values())
    return "sweep does not raise when the scratch directory is absent"


def main() -> int:
    tests = [
        ("KNOWN_PATTERNS is a non-empty list of (prefix, ext) pairs", test_known_patterns_is_nonempty_list_of_pairs),
        ("excluded singletons never match a KNOWN_PATTERNS glob", test_excluded_singletons_never_match_any_known_pattern),
        ("find_stale: only old matching files", test_find_stale_returns_only_old_matching_files),
        ("sweep: dry run does not delete", test_sweep_dry_run_does_not_delete),
        ("sweep: apply deletes stale, keeps fresh", test_sweep_apply_deletes_stale_keeps_fresh),
        ("sweep: multiple families tracked independently", test_sweep_across_multiple_families_independently),
        ("sweep: no crash on missing scratch dir", test_sweep_no_crash_on_missing_scratch_dir),
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
