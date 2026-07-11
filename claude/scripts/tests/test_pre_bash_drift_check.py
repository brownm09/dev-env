#!/usr/bin/env python3
"""Unit tests for pre-bash-drift-check.py.

dev-env#682: this hook extends ADR-085's cwd/branch drift detection to every
Bash call, gated by elapsed time since the last recorded state rather than by
command content (the three existing checkpoint hooks' gate). Exercises the
pure `should_check_drift()` gating function and `build_message()` formatter
offline. `main()`'s stdin plumbing and `_bash_state.current_repo_state()`'s
git subprocess call are not covered here (pure-helper convention, matching
pre-commit-branch-check.py / pre-pr-create-check.py / pre-merge-branch-check.py's
own test files — `format_drift_warning` itself is already fully covered by
test_bash_state.py and is not re-tested here).

Usage:
    py -3 claude/scripts/tests/test_pre_bash_drift_check.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import importlib

pre_bash_drift_check = importlib.import_module("pre-bash-drift-check")


def test_should_check_none_age_returns_false() -> str:
    assert pre_bash_drift_check.should_check_drift(None, 60.0) is False
    return "should_check_drift(None, ...) is False - no prior state file yet"


def test_should_check_below_threshold_returns_false() -> str:
    assert pre_bash_drift_check.should_check_drift(30.0, 60.0) is False
    return "should_check_drift is False when age is below min_gap"


def test_should_check_at_threshold_returns_false() -> str:
    assert pre_bash_drift_check.should_check_drift(60.0, 60.0) is False
    return "should_check_drift is False at the exact boundary (strict >, cheaper side wins)"


def test_should_check_above_threshold_returns_true() -> str:
    assert pre_bash_drift_check.should_check_drift(60.1, 60.0) is True
    return "should_check_drift is True once age exceeds min_gap"


def test_should_check_negative_age_returns_false() -> str:
    assert pre_bash_drift_check.should_check_drift(-5.0, 60.0) is False
    return "should_check_drift is False for a negative age (future/skewed mtime)"


def test_build_message_wraps_with_tag() -> str:
    got = pre_bash_drift_check.build_message("some warning text")
    assert got == "[bash-drift-check] some warning text", got
    return "build_message prefixes the drift warning with a [bash-drift-check] tag"


def main() -> int:
    tests = [
        ("should_check_drift: None age -> False", test_should_check_none_age_returns_false),
        ("should_check_drift: below threshold -> False", test_should_check_below_threshold_returns_false),
        ("should_check_drift: at threshold -> False", test_should_check_at_threshold_returns_false),
        ("should_check_drift: above threshold -> True", test_should_check_above_threshold_returns_true),
        ("should_check_drift: negative age -> False", test_should_check_negative_age_returns_false),
        ("build_message: wraps with [bash-drift-check] tag", test_build_message_wraps_with_tag),
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
