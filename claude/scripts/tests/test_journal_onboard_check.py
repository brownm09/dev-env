#!/usr/bin/env python3
"""End-to-end subprocess tests for journal-onboard-check.py's sentinel/cleanup
wiring (dev-env#768).

Before this fix, the hook hand-rolled its own `SCRATCH / f"journal_onboard_
{session_id}.flag"` sentinel path and never swept old ones -- this single
prefix alone accounted for 986 never-cleaned files at the 2026-07-10
hook-reliability assessment. It now uses the established SENTINEL_PREFIX +
_hookutil.sentinel_path()/cleanup_stale_sentinels() pattern shared by every
other compliant hook (hook-liveness-check.py, stop-tile-enumeration-gate.py,
posttooluse-inert-advisory.py, reconcile-open-prs.py,
stop-journal-stub-checkpoint.py, token-tracker.py).

Drives the real hook over stdin via subprocess, with HOME/USERPROFILE
redirected to a disposable temp dir (mirroring test_stop_journal_stub_checkpoint.py's
pattern) so no run ever touches the real ~/.claude/scratch/. `cwd` is omitted
from every payload below -- the hook exits before calling get_repo_name (a git
subprocess) whenever cwd is empty, so these tests exercise only the
sentinel/cleanup wiring, not the journal-home-detection logic (unchanged by
this PR and not re-tested here).

Usage:
    py -3 claude/scripts/tests/test_journal_onboard_check.py

Exit 0 = all pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "journal-onboard-check.py"
SENTINEL_PREFIX = "journal_onboard_"
MAX_AGE_DAYS = 30  # mirrors _hookutil.MAX_AGE_DAYS


def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _run_hook(payload, home: Path, raw_stdin=None):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(payload)
    return subprocess.run(
        _py_cmd() + [str(SCRIPT)],
        input=stdin_text,
        capture_output=True, text=True, env=env, timeout=30,
    )


def _scratch(home: Path) -> Path:
    return home / ".claude" / "scratch"


def test_creates_sentinel_flag_for_new_session() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        proc = _run_hook({"session_id": "sess-new-1"}, home)
        assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stderr}"
        flag = _scratch(home) / f"{SENTINEL_PREFIX}sess-new-1.flag"
        assert flag.exists(), f"expected sentinel at {flag}"
    return "first invocation for a session creates journal_onboard_<sid>.flag"


def test_second_invocation_same_session_is_noop() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        first = _run_hook({"session_id": "sess-repeat"}, home)
        assert first.returncode == 0
        flag = _scratch(home) / f"{SENTINEL_PREFIX}sess-repeat.flag"
        assert flag.exists()

        second = _run_hook({"session_id": "sess-repeat"}, home)
        assert second.returncode == 0, f"second invocation must also exit 0: {second.stderr}"
        assert flag.exists(), "sentinel must still exist after a second invocation"
    return "repeat invocation for an already-acked session exits 0 without error"


def test_sweeps_stale_flag_keeps_fresh() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        scratch = _scratch(home)
        scratch.mkdir(parents=True, exist_ok=True)

        stale = scratch / f"{SENTINEL_PREFIX}old-session.flag"
        fresh = scratch / f"{SENTINEL_PREFIX}recent-session.flag"
        stale.write_text("")
        fresh.write_text("")
        past = time.time() - (MAX_AGE_DAYS + 1) * 86400
        os.utime(stale, (past, past))

        proc = _run_hook({"session_id": "sess-brand-new"}, home)
        assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stderr}"
        assert not stale.exists(), "a sentinel older than MAX_AGE_DAYS must be swept"
        assert fresh.exists(), "a fresh sentinel from another session must be kept"
    return "cleanup sweeps a >30-day-old flag from an unrelated session while keeping a fresh one"


def test_no_crash_on_missing_session_id() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        proc = _run_hook({}, Path(home_s))
        assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stderr}"
    return "missing session_id does not raise (exits 0, no sentinel written)"


def test_no_crash_on_malformed_stdin() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        proc = _run_hook(None, Path(home_s), raw_stdin="{not valid json")
        assert proc.returncode == 0, f"malformed stdin must still exit 0, got {proc.returncode}: {proc.stderr}"
    return "malformed stdin JSON does not raise (fails open, exits 0)"


def test_cleanup_still_runs_on_malformed_stdin() -> str:
    # dev-env#768 review: cleanup was previously placed after the unguarded
    # json.loads(raw) call, so a JSONDecodeError from malformed stdin
    # propagated to the __main__ guard's exit(0) before cleanup was ever
    # reached -- the sweep was silently skipped on exactly the invocations
    # this test simulates. Cleanup must now run regardless.
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        scratch = _scratch(home)
        scratch.mkdir(parents=True, exist_ok=True)
        stale = scratch / f"{SENTINEL_PREFIX}old-session.flag"
        stale.write_text("")
        past = time.time() - (MAX_AGE_DAYS + 1) * 86400
        os.utime(stale, (past, past))

        proc = _run_hook(None, home, raw_stdin="{not valid json")
        assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stderr}"
        assert not stale.exists(), "cleanup must still sweep a stale flag even when this invocation's own stdin is malformed"
    return "cleanup runs (and sweeps a stale flag) even when this invocation's own stdin is malformed"


def main() -> int:
    tests = [
        ("creates sentinel flag for a new session", test_creates_sentinel_flag_for_new_session),
        ("second invocation, same session, is a no-op", test_second_invocation_same_session_is_noop),
        ("sweeps stale flag, keeps fresh", test_sweeps_stale_flag_keeps_fresh),
        ("no crash on missing session_id", test_no_crash_on_missing_session_id),
        ("no crash on malformed stdin", test_no_crash_on_malformed_stdin),
        ("cleanup still runs on malformed stdin", test_cleanup_still_runs_on_malformed_stdin),
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
