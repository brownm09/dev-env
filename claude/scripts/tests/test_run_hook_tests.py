#!/usr/bin/env python3
"""Tests for run-hook-tests.py -- the CI/local test-suite runner (dev-env #721,
ADR-103).

Exercises the pure helpers offline (no subprocess, no network, no disk beyond
``tempfile`` fixtures): ``discover_python_tests`` / ``discover_bash_tests``
(glob + naming-convention filtering), ``runner_skip_reason`` / ``SKIP_TESTS``
(the documented whole-file skip list), ``_command_for`` (interpreter argv, incl.
the bash-missing and non-test-suffix cases), and ``classify_result`` (the
pass / self-skip / fail mapping, incl. non-zero-exit winning over a SKIP marker).

``main`` / ``_run_one`` (which shell out) are not covered here -- the end-to-end
acceptance test for the runner is the first green CI run on the PR that adds it,
per ADR-103's Enforcement & migration section (the same pure-helper convention as
the rest of this suite).

Run: py -3 claude/scripts/tests/test_run_hook_tests.py
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "run-hook-tests.py"
sys.path.insert(0, str(SCRIPT.parent))
_spec = importlib.util.spec_from_file_location("run_hook_tests", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _touch(dirpath, name):
    p = Path(dirpath) / name
    p.write_text("# fixture\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# discover_python_tests
# ---------------------------------------------------------------------------

def test_discover_python_tests_matches_prefix_only():
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "test_alpha.py")
        _touch(d, "test_beta.py")
        _touch(d, "notatest.py")       # no test_ prefix
        _touch(d, "_hook_wiring.py")   # shared helper, underscore
        _touch(d, "test_gamma.txt")    # not .py
        _touch(d, "README.md")
        got = [p.name for p in mod.discover_python_tests([Path(d)])]
    assert got == ["test_alpha.py", "test_beta.py"], got


def test_discover_python_tests_sorted():
    with tempfile.TemporaryDirectory() as d:
        for name in ("test_z.py", "test_a.py", "test_m.py"):
            _touch(d, name)
        got = [p.name for p in mod.discover_python_tests([Path(d)])]
    assert got == ["test_a.py", "test_m.py", "test_z.py"], got


def test_discover_python_tests_missing_dir_is_empty():
    assert mod.discover_python_tests([Path(os.sep) / "no" / "such" / "dir"]) == []


def test_discover_python_tests_spans_multiple_dirs_and_skips_missing():
    # dev-env#730 review (A-1): Python tests must be found in BOTH test dirs, so a
    # test_*.py under claude/hooks/tests is never silently missed.
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        _touch(d1, "test_a.py")
        _touch(d2, "test_b.py")
        missing = Path(os.sep) / "no" / "such" / "dir"
        got = [p.name for p in mod.discover_python_tests([Path(d1), missing, Path(d2)])]
    assert got == ["test_a.py", "test_b.py"], got


# ---------------------------------------------------------------------------
# discover_bash_tests
# ---------------------------------------------------------------------------

def test_discover_bash_tests_globs_sh_excluding_underscore():
    with tempfile.TemporaryDirectory() as d:
        _touch(d, "test-foo.sh")
        _touch(d, "check-bar.sh")      # a non-test- prefixed gate still counts
        _touch(d, "run-shellcheck.sh")
        _touch(d, "_shared.sh")        # shared helper, excluded
        _touch(d, "notsh.txt")
        got = [p.name for p in mod.discover_bash_tests([Path(d)])]
    assert got == ["check-bar.sh", "run-shellcheck.sh", "test-foo.sh"], got


def test_discover_bash_tests_spans_multiple_dirs_and_skips_missing():
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        _touch(d1, "test-a.sh")
        _touch(d2, "test-b.sh")
        missing = Path(os.sep) / "no" / "such" / "dir"
        got = [p.name for p in mod.discover_bash_tests([Path(d1), missing, Path(d2)])]
    assert got == ["test-a.sh", "test-b.sh"], got


# ---------------------------------------------------------------------------
# SKIP_TESTS / runner_skip_reason
# ---------------------------------------------------------------------------

def test_skip_list_is_exactly_the_documented_entry():
    # Pinning the list makes any future runner-skip a deliberate, test-visible
    # change rather than a silent one.
    assert set(mod.SKIP_TESTS) == {"test_pyw_stdio.py"}, set(mod.SKIP_TESTS)
    assert mod.SKIP_TESTS["test_pyw_stdio.py"].strip(), "reason must be non-empty"


def test_runner_skip_reason_by_basename():
    assert mod.runner_skip_reason(Path("claude/scripts/tests/test_pyw_stdio.py"))
    assert mod.runner_skip_reason(Path("test_pyw_stdio.py"))
    assert mod.runner_skip_reason(Path("claude/scripts/tests/test_hookout.py")) is None


# ---------------------------------------------------------------------------
# suite_discovery_error (dev-env#730 review B-1: zero-discovery silent-green guard)
# ---------------------------------------------------------------------------

def test_suite_discovery_error_flags_empty():
    msg = mod.suite_discovery_error([])
    assert msg is not None and "0 Python test files" in msg, msg


def test_suite_discovery_error_ok_when_nonempty():
    assert mod.suite_discovery_error([Path("test_x.py")]) is None


# ---------------------------------------------------------------------------
# classify_result
# ---------------------------------------------------------------------------

def test_classify_pass_on_clean_exit():
    assert mod.classify_result(0, "Tests: 5 passed, 0 skipped, 0 failed") == "pass"
    assert mod.classify_result(0, "") == "pass"


def test_classify_fail_on_nonzero_exit():
    assert mod.classify_result(1, "boom") == "fail"
    assert mod.classify_result(2, "assert failed") == "fail"


def test_classify_self_skip_on_leading_skip_marker():
    assert mod.classify_result(0, "SKIP: gh not authenticated") == "skip"
    # Indented / not-first-line SKIP still counts (multiline anchor).
    assert mod.classify_result(0, "banner line\n  SKIP: shellcheck not found") == "skip"


def test_classify_nonzero_exit_beats_skip_marker():
    # A test that printed SKIP: but still exited non-zero is a real failure.
    assert mod.classify_result(2, "SKIP: something\nthen it crashed") == "fail"


# ---------------------------------------------------------------------------
# _command_for
# ---------------------------------------------------------------------------

def test_command_for_python_uses_current_interpreter():
    p = Path("claude/scripts/tests/test_x.py")
    assert mod._command_for(p, "bash") == [sys.executable, str(p)]


def test_command_for_bash_uses_bash_bin():
    p = Path("claude/scripts/tests/test-x.sh")
    assert mod._command_for(p, "/usr/bin/bash") == ["/usr/bin/bash", str(p)]


def test_command_for_bash_missing_returns_none():
    assert mod._command_for(Path("test-x.sh"), None) is None


def test_command_for_unknown_suffix_returns_none():
    assert mod._command_for(Path("notes.txt"), "bash") is None


# ---------------------------------------------------------------------------
# _run_one -- only the no-subprocess (bash-missing) branch (dev-env#730 review B-4)
# ---------------------------------------------------------------------------

def test_run_one_skips_without_shelling_out_when_bash_missing():
    # cmd is None short-circuits before any subprocess.run, so this branch is
    # pure and covered here even though the rest of _run_one shells out.
    status, seconds, output = mod._run_one(Path("gate.sh"), None, 300)
    assert status == "skip", status
    assert seconds == 0.0, seconds
    assert output.startswith("SKIP:"), output


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
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
