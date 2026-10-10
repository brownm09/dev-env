#!/usr/bin/env python3
"""Tests for run-hook-tests.py -- the CI/local test-suite runner (dev-env #721,
ADR-103).

Exercises the pure helpers offline (no subprocess, no network, no disk beyond
``tempfile`` fixtures): ``discover_python_tests`` / ``discover_bash_tests``
(glob + naming-convention filtering), ``runner_skip_reason`` / ``SKIP_TESTS``
(the documented whole-file skip list), ``_command_for`` (interpreter argv, incl.
the bash-missing and non-test-suffix cases), ``classify_result`` (the
pass / self-skip / fail mapping, incl. non-zero-exit winning over a SKIP marker),
and ``run_with_retries`` (dev-env#994, ADR-134 -- the file-level retry helper,
exercised with a canned zero-arg callable so no real subprocess is spawned).

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


def test_classify_per_case_skip_is_not_a_whole_file_skip():
    # dev-env#1138: a file that skips ONE case and passes the rest must classify
    # "pass". The per-case marker is "SKIPPED  <name>" / "  SKIP  <name> -- ..."
    # (no colon), and the skip stays visible in the file's own summary line.
    out = (
        "PASS: scan: healthy tree is clean\n"
        "SKIPPED  scan: workspace junctions skipped\n"
        "      junctions are Windows-only\n"
        "  SKIP  test_somewhere -- platform-gated\n"
        "\nTests: 25 passed, 1 skipped, 0 failed"
    )
    assert mod.classify_result(0, out) == "pass"
    # ...whereas the colon form is the whole-file signal, even mid-output.
    assert mod.classify_result(0, out + "\nSKIP: scan: workspace junctions") == "skip"


def test_case_skips_reads_the_files_own_summary_line():
    assert mod.case_skips("Tests: 25 passed, 1 skipped, 0 failed") == 1
    assert mod.case_skips("noise\n\nTests: 62 passed, 3 skipped, 0 failed (0.4s)\n") == 3
    assert mod.case_skips("Tests: 5 passed, 0 skipped, 0 failed") == 0
    # The LAST summary line wins (a file that prints a sub-summary first).
    assert mod.case_skips("Tests: 1 passed, 9 skipped, 0 failed\nTests: 2 passed, 4 skipped, 0 failed") == 4


def test_case_skips_is_zero_without_a_skipped_figure():
    # Bash gates print "Tests: N passed, N failed" (no skipped figure); empty / None
    # output and an indented or mid-line "Tests:" must not be misread either.
    assert mod.case_skips("Tests: 3 passed, 0 failed") == 0
    assert mod.case_skips("") == 0
    assert mod.case_skips(None) == 0
    assert mod.case_skips("  Tests: 1 passed, 7 skipped, 0 failed") == 0


def _colon_skip_literals(path):
    """Line numbers of string literals in ``path`` the runner would read as ``SKIP:``.

    AST-based so a comment that quotes the marker, like ``# Not "SKIP:" --``, is
    ignored, and reuses the runner's own regex so the two cannot drift apart.
    """
    import ast
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), str(path))
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and mod._SELF_SKIP_RE.search(n.value)
    ]


def test_colon_skip_lint_flags_a_known_bad_producer_and_passes_a_known_good_one():
    # The lint is only meaningful if it can fail: known-bad = the exact line this
    # PR removed from test_worktree_npm_install.py; known-good = its replacement
    # plus the explanatory comment that quotes the marker.
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "test_bad.py"
        bad.write_text('print(f"SKIP: {name}")\n', encoding="utf-8")
        good = Path(d) / "test_good.py"
        good.write_text(
            '# Not "SKIP:" -- the runner reads that as a whole-file skip.\n'
            'print(f"SKIPPED  {name}")\n',
            encoding="utf-8",
        )
        assert _colon_skip_literals(bad) == [1]
        assert _colon_skip_literals(good) == []


def test_no_python_test_prints_a_per_case_colon_skip_marker():
    # dev-env#1138: two files in a row (test_scratch_rm_allow.py, then
    # test_worktree_npm_install.py) printed a per-case "SKIP:" and silently turned
    # a passing file into a whole-file skip. Python tests have no whole-file skip
    # (that signal belongs to the bash gates), so none may contain the marker --
    # except this file, whose fixtures spell it out on purpose.
    files = mod.discover_python_tests(mod.TESTS_DIRS)
    assert files, "discovered 0 Python tests -- the scan below would pass vacuously"
    offenders = {
        p.name: lines
        for p in files
        if p.name != Path(__file__).name
        for lines in [_colon_skip_literals(p)]
        if lines
    }
    assert not offenders, (
        f"per-case skips must not start a line with 'SKIP:' (the runner reads it as "
        f"a whole-file skip; print 'SKIPPED  <name>' instead): {offenders}"
    )


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


def test_run_one_converts_oserror_to_fail():
    # dev-env#994 / PR review: an OSError at subprocess-spawn time (e.g. Windows
    # WinError 1450 "Insufficient system resources" under CI resource
    # contention) must convert to a normal "fail" status, not propagate and
    # crash the runner -- that's what makes it retriable by run_with_retries
    # instead of aborting the whole suite. A nonexistent bash_bin triggers a
    # real OSError (FileNotFoundError) at spawn time with no mocking needed.
    bogus_bash = str(Path(os.sep) / "no" / "such" / "bash-binary-xyz")
    status, seconds, output = mod._run_one(Path("dummy_test.sh"), bogus_bash, 5)
    assert status == "fail", (status, output)
    assert "OSError" in output, output


# ---------------------------------------------------------------------------
# run_with_retries (dev-env#994, ADR-134)
# ---------------------------------------------------------------------------

class _Canned:
    """Zero-arg callable that returns each of *results* in order, then raises
    IndexError if called too many times -- a call-count-verifiable stand-in
    for `functools.partial(_run_one, path, bash_bin, timeout)` with no real
    subprocess spawning."""
    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._results.pop(0)


def test_run_with_retries_pass_first_try_zero_retries_used():
    run_one = _Canned(("pass", 0.5, "ok"))
    status, elapsed, output, retries = mod.run_with_retries(run_one, 3)
    assert (status, elapsed, output, retries) == ("pass", 0.5, "ok", 0)
    assert run_one.calls == 1, run_one.calls


def test_run_with_retries_skip_first_try_never_retried():
    # A self-skip must never be re-attempted, regardless of max_retries.
    run_one = _Canned(("skip", 0.0, "SKIP: bash interpreter not found on PATH"))
    status, elapsed, output, retries = mod.run_with_retries(run_one, 5)
    assert status == "skip" and retries == 0, (status, retries)
    assert run_one.calls == 1, run_one.calls


def test_run_with_retries_fails_once_then_passes():
    run_one = _Canned(("fail", 0.2, "boom"), ("pass", 0.3, "ok"))
    status, elapsed, output, retries = mod.run_with_retries(run_one, 2)
    assert (status, elapsed, output, retries) == ("pass", 0.3, "ok", 1)
    assert run_one.calls == 2, run_one.calls


def test_run_with_retries_fails_through_max_retries():
    run_one = _Canned(("fail", 0.1, "a"), ("fail", 0.1, "b"), ("fail", 0.1, "c"))
    status, elapsed, output, retries = mod.run_with_retries(run_one, 2)
    assert (status, retries) == ("fail", 2), (status, retries)
    assert run_one.calls == 3, run_one.calls  # 1 + max_retries


def test_run_with_retries_max_retries_zero_single_attempt():
    run_one = _Canned(("fail", 0.1, "boom"))
    status, elapsed, output, retries = mod.run_with_retries(run_one, 0)
    assert (status, retries) == ("fail", 0), (status, retries)
    assert run_one.calls == 1, run_one.calls  # no retry loop entered


def test_run_with_retries_returns_final_attempt_output_not_first():
    # An eventual pass' output must not be the first (failed) attempt's, and
    # vice versa -- pins the "always the FINAL attempt's" contract.
    run_one = _Canned(("fail", 1.0, "first attempt output"), ("pass", 2.0, "second attempt output"))
    status, elapsed, output, retries = mod.run_with_retries(run_one, 1)
    assert (elapsed, output) == (2.0, "second attempt output"), (elapsed, output)


def test_run_with_retries_on_attempt_called_per_attempt_with_final_flag():
    run_one = _Canned(("fail", 0.1, "a"), ("fail", 0.1, "b"), ("pass", 0.1, "c"))
    seen = []
    mod.run_with_retries(run_one, 5, on_attempt=lambda *args: seen.append(args))
    assert [(a[0], a[1], a[4]) for a in seen] == [
        (0, "fail", False),
        (1, "fail", False),
        (2, "pass", True),
    ], seen


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
