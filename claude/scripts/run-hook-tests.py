#!/usr/bin/env python3
"""run-hook-tests.py -- discover and run the dev-env hook/script test suite.

Discovers every ``claude/scripts/tests/test_*.py`` plus the bash self-tests
(``claude/scripts/tests/*.sh`` and ``claude/hooks/tests/*.sh``), runs each as a
subprocess, and reports pass / fail / skip. Exit code is 0 iff every test
passed; a *self-skip* -- a bash gate that prints a leading ``SKIP:`` line and
exits 0 because an optional tool (shellcheck, an authenticated gh) is absent --
counts as non-failing.

This is the engine behind ``.github/workflows/hook-tests.yml`` (windows-latest,
``pull_request``). It is equally runnable by hand:

    py -3 claude/scripts/run-hook-tests.py            # run the whole suite
    py -3 claude/scripts/run-hook-tests.py --list     # just list what would run
    py -3 claude/scripts/run-hook-tests.py --timeout 600

Discovery is glob-based on purpose: a new ``test_*.py`` or a new ``*.sh`` in
either dedicated test directory is picked up automatically, so the runner never
drifts from the ``## Testing`` list the way a hand-maintained command list would.
Python tests are named ``test_*.py`` (so the shared ``_hook_wiring.py`` helper is
never mistaken for a test); a leading underscore also excludes any future shared
``.sh`` helper.

**Runner-level skips** (``SKIP_TESTS``): a small, documented set of tests that
cannot run *faithfully* under a non-interactive CI runner. Skipping here does not
weaken the test -- run it locally on Windows instead. Currently only
``test_pyw_stdio.py`` (it spawns real ``pythonw.exe`` to probe Windows-subsystem
stdio behavior under parent-supplied pipes -- a GUI-subsystem probe that is
unreliable with no attached console; see the hook-reliability plan gotcha #7 and
the test's own docstring). This is distinct from a self-skip: a runner-skip never
launches the subprocess at all.

Pure helpers (``discover_python_tests`` / ``discover_bash_tests`` /
``runner_skip_reason`` / ``classify_result``) are unit-tested offline in
``tests/test_run_hook_tests.py``; the acceptance test for the end-to-end runner
is the first green CI run on the PR that adds it.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# repo root == .../claude/scripts/run-hook-tests.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]
# Both dedicated test directories. Python (`test_*.py`) and bash (`*.sh`) gates
# are discovered from the SAME set, so a test can never be silently missed by
# living in the "wrong" one -- e.g. a hook's Python test placed under
# claude/hooks/tests (which already hosts test-pre-push-lockfile.sh).
TESTS_DIRS = (
    REPO_ROOT / "claude" / "scripts" / "tests",
    REPO_ROOT / "claude" / "hooks" / "tests",
)

# Whole-file skips that cannot run faithfully in CI. Keyed by basename; the value
# is the human-readable reason printed in the summary. Keep this list tiny and
# documented -- every entry is a test that is still expected to pass *locally*.
SKIP_TESTS = {
    "test_pyw_stdio.py": (
        "spawns real pythonw.exe (pyw -3) to probe Windows-subsystem stdio "
        "behavior under parent-supplied pipes; that GUI-subsystem probe is "
        "unreliable on a non-interactive CI runner (no attached console). "
        "Run locally: py -3 claude/scripts/tests/test_pyw_stdio.py"
    ),
}

# A self-skipping bash gate prints a leading "SKIP:" line and exits 0. This is the
# whole-gate skip signal (a gate that self-skips one optional sub-check but still
# runs must NOT print "SKIP:" -- it would be mislabeled a whole-file skip). It can
# never mask a *failure*: exit 0 is required, and classify_result checks the exit
# code first.
_SELF_SKIP_RE = re.compile(r"(?m)^\s*SKIP:")

DEFAULT_TIMEOUT_SECONDS = 300


def _discover(dirs, pattern, name_ok):
    """Sorted files matching ``pattern`` across ``dirs`` (non-recursive).

    Both dedicated test directories are searched so a test is never silently
    missed by directory asymmetry. Deduplicated by resolved path in case the
    dirs ever overlap; sorted by ``(name, path)`` for deterministic ordering.
    """
    found = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.glob(pattern):
            if p.is_file() and name_ok(p.name):
                found.setdefault(p.resolve(), p)
    return sorted(found.values(), key=lambda p: (p.name, str(p)))


def discover_python_tests(dirs):
    """Sorted ``test_*.py`` files across the given dedicated test directories.

    The ``test_`` prefix is the naming convention every Python test in the repo
    follows, and it deliberately excludes shared helpers like ``_hook_wiring.py``.
    Searched across *both* test dirs (mirroring ``discover_bash_tests``) so a
    Python test under either is always run.
    """
    return _discover(dirs, "test_*.py", lambda _n: True)


def discover_bash_tests(dirs):
    """Sorted ``*.sh`` files across the given dedicated test directories.

    Every ``.sh`` in these directories is a test/gate by convention. A leading
    underscore is excluded so a future shared ``_helper.sh`` would not be run as a
    test.
    """
    return _discover(dirs, "*.sh", lambda n: not n.startswith("_"))


def runner_skip_reason(path: Path):
    """Return the runner-skip reason for ``path`` (by basename), or ``None``."""
    return SKIP_TESTS.get(path.name)


def suite_discovery_error(py_tests):
    """Error message if discovery found no Python tests, else ``None``.

    A CI gate whose whole purpose is to run the suite must never report success
    while running nothing. Zero Python tests means a broken ``REPO_ROOT`` (a
    moved script -> wrong ``parents[2]``) or a renamed/relocated test dir; the
    repo always has 50+ ``test_*.py``. Guarding on the Python set (bash tests can
    legitimately be absent in a stripped checkout) turns that silent-green hole
    into a loud failure.
    """
    if not py_tests:
        return (
            "discovered 0 Python test files under "
            f"{', '.join(str(d) for d in TESTS_DIRS)} -- REPO_ROOT or the test "
            "glob is broken; refusing to report success on an empty suite"
        )
    return None


def classify_result(returncode: int, output: str) -> str:
    """Map a finished subprocess to ``"pass"`` / ``"skip"`` / ``"fail"``.

    Non-zero exit is always a fail. On a clean (exit-0) run, a leading
    ``SKIP:`` line means the test self-skipped for a missing optional tool --
    non-failing, but reported distinctly so an environment gap is visible rather
    than silently counted as a pass.
    """
    if returncode != 0:
        return "fail"
    if _SELF_SKIP_RE.search(output or ""):
        return "skip"
    return "pass"


def _command_for(path: Path, bash_bin):
    """Argv to run one test file, or ``None`` if its interpreter is unavailable."""
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    if path.suffix == ".sh":
        if not bash_bin:
            return None
        return [bash_bin, str(path)]
    return None


def _run_one(path: Path, bash_bin, timeout: int):
    """Run one test file. Returns ``(status, seconds, output)``."""
    cmd = _command_for(path, bash_bin)
    if cmd is None:
        return "skip", 0.0, "SKIP: bash interpreter not found on PATH"
    start = time.monotonic()
    try:
        # Deliberate encoding tradeoff: capture as utf-8/replace so a chatty test
        # can never crash the capture, and DON'T force the child's stdio encoding
        # (no PYTHONUTF8/PYTHONIOENCODING) -- that would perturb the cp1252 runtime
        # several tests assert against. A Windows Python child writes its pipe in
        # cp1252, so a non-ASCII byte in a *failing* test's dump may render as
        # U+FFFD; harmless, since classification is exit-code-based and the SKIP
        # marker is ASCII. `timeout` kills only the direct child, not any
        # grandchildren it spawned (no Windows process-group kill); acceptable
        # because CI job teardown reaps everything at timeout-minutes.
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        elapsed = time.monotonic() - start
        output = (proc.stdout or "") + (proc.stderr or "")
        return classify_result(proc.returncode, output), elapsed, output
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        partial = ""
        if exc.stdout:
            partial += exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
        if exc.stderr:
            partial += exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", "replace")
        return "fail", elapsed, f"TIMEOUT after {timeout}s\n{partial}"


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Discover and run the dev-env hook/script test suite."
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-test timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS}).",
    )
    ap.add_argument(
        "--list",
        action="store_true",
        help="List the tests that would run (and any runner-skips), then exit.",
    )
    return ap.parse_args(argv)


def _make_stdout_crash_proof():
    """Ensure printing captured test output never crashes the runner.

    On Windows/CI the runner's own stdout defaults to cp1252; a failing test's
    captured output can contain bytes that decoded (errors="replace") to U+FFFD
    or other non-cp1252 characters. Printing those to a cp1252 stream raises
    ``UnicodeEncodeError``, which would abort the runner mid-suite and mask every
    later test. Reconfiguring to UTF-8/replace makes the runner's *own* output
    robust; it does NOT touch the test subprocesses' stdio, which keep their
    native (cp1252) encoding via their pipes -- the runtime the tests target.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a reconfigurable TextIOWrapper
            pass


def main(argv=None) -> int:
    _make_stdout_crash_proof()
    args = _parse_args(argv)
    bash_bin = shutil.which("bash")

    py_tests = discover_python_tests(TESTS_DIRS)
    sh_tests = discover_bash_tests(TESTS_DIRS)
    all_tests = py_tests + sh_tests

    runner_skipped = [p for p in all_tests if runner_skip_reason(p)]
    to_run = [p for p in all_tests if not runner_skip_reason(p)]

    print(
        f"Discovered {len(py_tests)} Python + {len(sh_tests)} bash test files "
        f"({len(runner_skipped)} runner-skipped).",
        flush=True,
    )
    discovery_error = suite_discovery_error(py_tests)
    if discovery_error:
        print(f"ERROR: {discovery_error}", flush=True)
        return 1
    if bash_bin is None and sh_tests:
        print("WARNING: bash not found on PATH -- bash tests will be skipped.", flush=True)

    if args.list:
        for p in to_run:
            print(f"  run   {p.name}")
        for p in runner_skipped:
            print(f"  skip  {p.name}  (runner: {runner_skip_reason(p)})")
        return 0

    passed = failed = skipped = 0
    failures = []
    suite_start = time.monotonic()

    for p in runner_skipped:
        print(f"SKIP  {p.name:<42}  (runner: {runner_skip_reason(p)})", flush=True)
        skipped += 1

    for p in to_run:
        status, elapsed, output = _run_one(p, bash_bin, args.timeout)
        if status == "pass":
            print(f"PASS  {p.name:<42}  ({elapsed:5.1f}s)", flush=True)
            passed += 1
        elif status == "skip":
            first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "SKIP")
            print(f"SKIP  {p.name:<42}  (env: {first})", flush=True)
            skipped += 1
        else:  # fail
            print(f"FAIL  {p.name:<42}  ({elapsed:5.1f}s)", flush=True)
            print("      " + "-" * 66, flush=True)
            for line in (output or "").rstrip().splitlines():
                print(f"      | {line}", flush=True)
            print("      " + "-" * 66, flush=True)
            failed += 1
            failures.append(p.name)

    total = time.monotonic() - suite_start
    print("", flush=True)
    print(
        f"Suite: {passed} passed, {skipped} skipped, {failed} failed ({total:.1f}s)",
        flush=True,
    )
    # Also emit the CLAUDE.md test-integrity summary shape for PR-body use.
    print(f"Tests: {passed} passed, {skipped} skipped, {failed} failed ({total:.1f}s)", flush=True)
    if failures:
        print("Failed: " + ", ".join(failures), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
