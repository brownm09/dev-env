#!/usr/bin/env python3
"""Tests for _journal_compose_force.py pure helpers (dev-env#631, ADR-094).

Exercises resolve_force, marker_path_for/marker_dir, build_marker,
write_marker/read_marker (a real tmp-file round trip -- the only impure
surface, matching test_hookutil.py's precedent of using real tempfile
fixtures for file I/O), and is_marker_fresh offline. `now`/`resolved_at` are
always explicit datetime values passed in by the test, never the real
clock, so these tests are deterministic regardless of when they run.
"""
import datetime
import importlib.util
import os
import sys
import tempfile

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "_journal_compose_force.py")
sys.path.insert(0, os.path.dirname(_SCRIPT))
spec = importlib.util.spec_from_file_location("_journal_compose_force", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

resolve_force = mod.resolve_force
marker_dir = mod.marker_dir
marker_path_for = mod.marker_path_for
build_marker = mod.build_marker
write_marker = mod.write_marker
read_marker = mod.read_marker
is_marker_fresh = mod.is_marker_fresh
MAX_MARKER_AGE_SECONDS = mod.MAX_MARKER_AGE_SECONDS
MARKER_DIR_ENV = mod.MARKER_DIR_ENV


# ---------------------------------------------------------------------------
# resolve_force
# ---------------------------------------------------------------------------

def test_resolve_force_bare():
    assert resolve_force("--force") is True

def test_resolve_force_date_before():
    assert resolve_force("2026-07-09 --force") is True

def test_resolve_force_date_after():
    assert resolve_force("--force 2026-07-09") is True

def test_resolve_force_multi_whitespace():
    assert resolve_force("2026-07-09   --force") is True

def test_resolve_force_absent():
    assert resolve_force("2026-07-09") is False

def test_resolve_force_empty_string():
    assert resolve_force("") is False

def test_resolve_force_none_safe():
    assert resolve_force(None) is False

def test_resolve_force_forceful_not_matched():
    # A longer flag must not be mistaken for --force.
    assert resolve_force("--forceful") is False

def test_resolve_force_force_push_not_matched():
    assert resolve_force("--force-push") is False

def test_resolve_force_substring_in_prose_not_matched():
    # "--force" only counts as a standalone token, not embedded in a longer
    # run of non-whitespace text.
    assert resolve_force("please--force-nothing") is False


# ---------------------------------------------------------------------------
# marker_dir / marker_path_for
# ---------------------------------------------------------------------------

def _with_marker_dir(value, fn):
    original = os.environ.get(MARKER_DIR_ENV)
    if value is None:
        os.environ.pop(MARKER_DIR_ENV, None)
    else:
        os.environ[MARKER_DIR_ENV] = value
    try:
        return fn()
    finally:
        if original is None:
            os.environ.pop(MARKER_DIR_ENV, None)
        else:
            os.environ[MARKER_DIR_ENV] = original

def test_marker_dir_default_when_unset():
    result = _with_marker_dir(None, marker_dir)
    assert result == mod._DEFAULT_MARKER_DIR

def test_marker_dir_env_override():
    result = _with_marker_dir("C:/tmp/override", marker_dir)
    assert result == "C:/tmp/override"

def test_marker_path_for_filename_shape():
    result = _with_marker_dir("C:/tmp/override", lambda: marker_path_for("2026-07-09"))
    assert result == os.path.join("C:/tmp/override", "journal-compose-force-2026-07-09.json")


# ---------------------------------------------------------------------------
# build_marker
# ---------------------------------------------------------------------------

def test_build_marker_shape():
    now = datetime.datetime(2026, 7, 9, 12, 0, 0)
    marker = build_marker(True, "--force", now)
    assert marker == {
        "force": True,
        "raw_arguments": "--force",
        "resolved_at": "2026-07-09T12:00:00",
    }

def test_build_marker_force_false_coerced_bool():
    now = datetime.datetime(2026, 7, 9, 12, 0, 0)
    marker = build_marker(0, "", now)  # falsy non-bool input
    assert marker["force"] is False

def test_build_marker_none_raw_args():
    now = datetime.datetime(2026, 7, 9, 12, 0, 0)
    marker = build_marker(False, None, now)
    assert marker["raw_arguments"] == ""


# ---------------------------------------------------------------------------
# write_marker / read_marker round trip (real tmp files)
# ---------------------------------------------------------------------------

def test_write_read_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "nested", "journal-compose-force-2026-07-09.json")
        now = datetime.datetime(2026, 7, 9, 12, 0, 0)
        marker = build_marker(True, "--force", now)
        write_marker(path, marker)
        assert read_marker(path) == marker

def test_write_marker_overwrites():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "marker.json")
        now = datetime.datetime(2026, 7, 9, 12, 0, 0)
        write_marker(path, build_marker(True, "--force", now))
        write_marker(path, build_marker(False, "", now))
        assert read_marker(path)["force"] is False

def test_read_marker_missing_file():
    assert read_marker("C:/nonexistent/path/marker.json") is None

def test_read_marker_malformed_json():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "marker.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert read_marker(path) is None

def test_read_marker_non_dict_json():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "marker.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        assert read_marker(path) is None


# ---------------------------------------------------------------------------
# is_marker_fresh
# ---------------------------------------------------------------------------

def test_is_marker_fresh_recent():
    now = datetime.datetime(2026, 7, 9, 12, 0, 10)
    marker = build_marker(True, "--force", datetime.datetime(2026, 7, 9, 12, 0, 0))
    assert is_marker_fresh(marker, now) is True

def test_is_marker_fresh_exact_boundary():
    resolved_at = datetime.datetime(2026, 7, 9, 12, 0, 0)
    now = resolved_at + datetime.timedelta(seconds=MAX_MARKER_AGE_SECONDS)
    marker = build_marker(True, "--force", resolved_at)
    assert is_marker_fresh(marker, now) is True

def test_is_marker_fresh_just_past_boundary():
    resolved_at = datetime.datetime(2026, 7, 9, 12, 0, 0)
    now = resolved_at + datetime.timedelta(seconds=MAX_MARKER_AGE_SECONDS + 1)
    marker = build_marker(True, "--force", resolved_at)
    assert is_marker_fresh(marker, now) is False

def test_is_marker_fresh_future_timestamp_not_fresh():
    resolved_at = datetime.datetime(2026, 7, 9, 12, 0, 10)
    now = datetime.datetime(2026, 7, 9, 12, 0, 0)  # resolved_at is AFTER now
    marker = build_marker(True, "--force", resolved_at)
    assert is_marker_fresh(marker, now) is False

def test_is_marker_fresh_malformed_resolved_at():
    marker = {"force": True, "raw_arguments": "--force", "resolved_at": "not-a-timestamp"}
    assert is_marker_fresh(marker, datetime.datetime(2026, 7, 9)) is False

def test_is_marker_fresh_missing_resolved_at():
    marker = {"force": True, "raw_arguments": "--force"}
    assert is_marker_fresh(marker, datetime.datetime(2026, 7, 9)) is False

def test_is_marker_fresh_non_dict_marker():
    assert is_marker_fresh(None, datetime.datetime(2026, 7, 9)) is False
    assert is_marker_fresh("not-a-dict", datetime.datetime(2026, 7, 9)) is False

def test_is_marker_fresh_custom_max_age():
    resolved_at = datetime.datetime(2026, 7, 9, 12, 0, 0)
    now = resolved_at + datetime.timedelta(seconds=30)
    marker = build_marker(True, "--force", resolved_at)
    assert is_marker_fresh(marker, now, max_age_seconds=10) is False
    assert is_marker_fresh(marker, now, max_age_seconds=60) is True

def test_is_marker_fresh_tz_aware_resolved_at_not_fresh_not_raise():
    # Review finding on PR #671: a tz-aware ISO string (an ordinary
    # .isoformat() shape, not exotic) previously raised an uncaught
    # TypeError on naive-minus-aware subtraction -- propagating out of the
    # guard hook's main() as an unhandled exception, exiting non-zero-but-
    # not-2, which Claude Code treats as non-blocking and lets the gated
    # command PROCEED. Must resolve to "not fresh" (block), not raise.
    marker = {"force": True, "raw_arguments": "--force", "resolved_at": "2026-07-09T12:00:00+00:00"}
    assert is_marker_fresh(marker, datetime.datetime(2026, 7, 9, 12, 0, 10)) is False


# ---------------------------------------------------------------------------
# write_marker concurrency (review finding on PR #671)
# ---------------------------------------------------------------------------

def test_write_marker_temp_path_includes_pid():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "marker.json")
        now = datetime.datetime(2026, 7, 9, 12, 0, 0)
        write_marker(path, build_marker(True, "--force", now))
        assert read_marker(path)["force"] is True
        # the pid-suffixed temp file must not survive a successful write
        assert not os.path.exists(f"{path}.{os.getpid()}.tmp")

def test_write_marker_concurrent_pids_do_not_collide():
    # Simulates two "concurrent" writers with different PIDs via a
    # monkeypatched os.getpid: writer A's in-progress (not yet renamed) temp
    # file must not be touched by writer B's independent write-and-rename.
    # With the old shared ".tmp" name, writer B's os.replace would have
    # clobbered or torn writer A's still-in-progress temp file.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "marker.json")
        now = datetime.datetime(2026, 7, 9, 12, 0, 0)
        original_getpid = os.getpid
        try:
            os.getpid = lambda: 111
            tmp_path_a = f"{path}.111.tmp"
            with open(tmp_path_a, "w", encoding="utf-8") as f:
                f.write('{"force": true, "raw_arguments": "--force (writer A, mid-write)"}')

            os.getpid = lambda: 222
            write_marker(path, build_marker(False, "", now))  # writer B completes fully

            assert read_marker(path)["force"] is False  # writer B's result landed
            assert os.path.isfile(tmp_path_a)  # writer A's temp file untouched, not clobbered
        finally:
            os.getpid = original_getpid


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
