#!/usr/bin/env python3
"""End-to-end subprocess tests for journal-compose-force-resolve.py
(dev-env#631, ADR-096).

Drives the real script as a subprocess (mirroring test_canonical_mutate_guard.py's
`_run_hook` pattern) with `JOURNAL_COMPOSE_FORCE_MARKER_DIR` redirected at a
disposable temp dir, so no test run ever touches the real
`C:/Users/brown/.claude/scratch/`. The script's own logic (resolve_force,
build_marker, write_marker) is unit-tested directly in
test_journal_compose_force.py; this file exercises only the CLI-glue layer:
argv handling, stdout format, and that the marker actually lands on disk
with the expected schema.
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "journal-compose-force-resolve.py"
_SCRIPTS_DIR = str(Path(__file__).parent.parent)
sys.path.insert(0, _SCRIPTS_DIR)
import _journal_compose_force as jcf  # noqa: E402


def _run_resolve(args, marker_dir):
    env = dict(os.environ)
    env[jcf.MARKER_DIR_ENV] = marker_dir
    cmd = [sys.executable, str(MODULE_PATH)]
    if args is not None:
        cmd.append(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)


def test_no_force_prints_false_and_writes_marker():
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_resolve("2026-07-09", tmp)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "FORCE=false"
        today = datetime.date.today().isoformat()
        with open(os.path.join(tmp, f"journal-compose-force-{today}.json"), encoding="utf-8") as f:
            marker = json.load(f)
        assert marker["force"] is False
        assert marker["raw_arguments"] == "2026-07-09"


def test_force_flag_prints_true_and_writes_marker():
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_resolve("2026-07-09 --force", tmp)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "FORCE=true"
        today = datetime.date.today().isoformat()
        with open(os.path.join(tmp, f"journal-compose-force-{today}.json"), encoding="utf-8") as f:
            marker = json.load(f)
        assert marker["force"] is True
        assert marker["raw_arguments"] == "2026-07-09 --force"


def test_bare_force_prints_true():
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_resolve("--force", tmp)
        assert proc.stdout.strip() == "FORCE=true"


def test_no_arguments_at_all_prints_false():
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run_resolve(None, tmp)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "FORCE=false"


def test_second_invocation_overwrites_marker():
    with tempfile.TemporaryDirectory() as tmp:
        _run_resolve("--force", tmp)
        proc = _run_resolve("2026-07-09", tmp)
        assert proc.stdout.strip() == "FORCE=false"
        today = datetime.date.today().isoformat()
        with open(os.path.join(tmp, f"journal-compose-force-{today}.json"), encoding="utf-8") as f:
            marker = json.load(f)
        assert marker["force"] is False  # the later, force-less run wins


def test_creates_marker_dir_if_absent():
    with tempfile.TemporaryDirectory() as tmp:
        nested = os.path.join(tmp, "does", "not", "exist", "yet")
        proc = _run_resolve("--force", nested)
        assert proc.returncode == 0
        assert os.path.isdir(nested)


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
