#!/usr/bin/env python3
"""Unit tests for _hookio.read_command_output — the shared PostToolUse output read.

Claude Code's Bash hook payload exposes a command's output under
`tool_response.stdout` / `tool_response.stderr`, NOT `output`. `post-tool-use.py`
read the legacy `output` field and therefore silently never fired (dev-env #377 /
ADR-049); the same wrong read existed in four sibling hooks (#380). The fix is the
shared `read_command_output` helper in `claude/scripts/_hookio.py`, imported by all
five hooks. These tests pin its field precedence offline (no network, no gh).

Usage:
    py -3 claude/scripts/tests/test_hookio.py

Exit 0 = all pass.
"""

import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _hookio import read_command_output  # noqa: E402

URL = "https://github.com/brownm09/dev-env/issues/377"


def test_reads_stdout() -> str:
    # The real Bash payload shape: output lives under `stdout`, not `output`.
    payload = {"tool_response": {"stdout": URL, "stderr": "", "interrupted": False}}
    assert read_command_output(payload) == URL, "stdout should be read"
    return "stdout-shaped payload -> stdout content (the #377 regression)"


def test_combines_stdout_and_stderr() -> str:
    # `gh pr merge` prints its success line to stderr; both must be captured.
    payload = {"tool_response": {"stdout": "done", "stderr": "warn"}}
    assert read_command_output(payload) == "done\nwarn", "stdout+stderr joined"
    return "stdout + stderr are both captured, newline-joined"


def test_stderr_only() -> str:
    # gh writes the merge success marker to stderr with no stdout.
    payload = {"tool_response": {"stdout": "", "stderr": "Squashed and merged pull request #380"}}
    assert read_command_output(payload) == "Squashed and merged pull request #380"
    return "stderr-only payload -> stderr content (the gh pr merge shape)"


def test_legacy_output_fallback() -> str:
    # If a build ever sends the legacy `output` field, still read it.
    payload = {"tool_response": {"output": URL}}
    assert read_command_output(payload) == URL, "legacy output fallback"
    return "legacy `output` field still works (forward/backward compatible)"


def test_stdout_preferred_over_legacy_output() -> str:
    payload = {"tool_response": {"stdout": "real", "output": "legacy"}}
    assert read_command_output(payload) == "real", "stdout wins over legacy output"
    return "stdout/stderr take precedence over the legacy output field"


def test_empty_and_malformed_payloads() -> str:
    assert read_command_output({}) == "", "missing tool_response -> ''"
    assert read_command_output({"tool_response": {}}) == "", "empty tool_response -> ''"
    assert read_command_output({"tool_response": None}) == "", "None tool_response -> ''"
    assert read_command_output({"tool_response": "x"}) == "", "non-dict tool_response -> ''"
    return "missing/empty/None/non-dict tool_response all yield '' (no crash)"


def test_old_output_read_would_have_been_empty() -> str:
    # Pin the root cause: the pre-fix read (`.get("output")`) on the real shape
    # is empty, which is exactly what silently broke the four sibling hooks.
    real_shape = {"stdout": URL, "stderr": "", "interrupted": False, "isImage": False}
    assert real_shape.get("output", "") == "", "pre-fix read must be empty on real shape"
    assert read_command_output({"tool_response": real_shape}) == URL, "fixed read recovers content"
    return "pre-fix `output` read was '' on the real payload; fixed read recovers it"


def main() -> int:
    tests = [
        ("reads command output from stdout", test_reads_stdout),
        ("combines stdout and stderr", test_combines_stdout_and_stderr),
        ("stderr-only (gh pr merge shape)", test_stderr_only),
        ("legacy output field still works", test_legacy_output_fallback),
        ("stdout preferred over legacy output", test_stdout_preferred_over_legacy_output),
        ("empty/malformed payloads yield ''", test_empty_and_malformed_payloads),
        ("pre-fix output read was empty (#377 root cause)", test_old_output_read_would_have_been_empty),
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
