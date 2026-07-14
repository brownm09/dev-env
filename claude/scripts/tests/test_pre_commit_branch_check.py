#!/usr/bin/env python3
"""Unit tests for pre-commit-branch-check.py.

Exercises the pure helpers offline (no subprocess, no stdin): the existing
`is_git_commit_command()` detector and the new `build_message()` formatter
added for dev-env#573's drift-warning integration. `current_branch()` /
`current_repo_root()` shell out to git and are not covered here (pure-helper
convention, matches this repo's other hooks).

Usage:
    py -3 claude/scripts/tests/test_pre_commit_branch_check.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import importlib

pre_commit_branch_check = importlib.import_module("pre-commit-branch-check")


def test_detects_bare_git_commit() -> str:
    assert pre_commit_branch_check.is_git_commit_command("git commit -m 'x'")
    return "bare `git commit` is detected"


def test_detects_chained_git_commit() -> str:
    assert pre_commit_branch_check.is_git_commit_command("git add -A && git commit -m 'x'")
    return "a chained `... && git commit` is detected"


def test_ignores_non_commit_command() -> str:
    assert not pre_commit_branch_check.is_git_commit_command("git status")
    return "an unrelated git command is not detected"


def test_detects_powershell_conditional_brace_commit() -> str:
    # dev-env#620: PowerShell 5.1 has no && (the tool's own description confirms
    # it's a parser error there), so its documented "run B only if A succeeds"
    # idiom is `A; if ($?) { B }` -- the added `{` anchor alternative catches
    # this exactly like the bash brace-group equivalent.
    assert pre_commit_branch_check.is_git_commit_command('git add -A; if ($?) { git commit -m "x" }')
    return "PowerShell 'A; if ($?) { git commit ... }' idiom is now detected (dev-env#620)"


def test_detects_bash_brace_group_commit() -> str:
    assert pre_commit_branch_check.is_git_commit_command('{ git commit -m "x"; }')
    return "bash brace-group '{ git commit ...; }' idiom is now detected too"


def test_build_message_no_drift() -> str:
    msg = pre_commit_branch_check.build_message("feat/x", None)
    assert msg == "[branch-check] committing to: feat/x", msg
    return "build_message is unchanged from the pre-existing format when there is no drift"


def test_build_message_with_drift() -> str:
    msg = pre_commit_branch_check.build_message("main", "⚠ [cwd-drift] ...")
    assert msg.startswith("[branch-check] committing to: main\n"), msg
    assert "⚠ [cwd-drift]" in msg
    return "build_message appends the drift warning on its own line when present"


def test_build_message_none_branch_shows_placeholder() -> str:
    msg = pre_commit_branch_check.build_message(None, None)
    assert "committing to: <detached HEAD or unknown>" in msg, msg
    return "build_message renders a placeholder for a None branch (detached HEAD / git failure)"


def main() -> int:
    tests = [
        ("is_git_commit_command: bare", test_detects_bare_git_commit),
        ("is_git_commit_command: chained", test_detects_chained_git_commit),
        ("is_git_commit_command: ignores non-commit", test_ignores_non_commit_command),
        ("is_git_commit_command: PowerShell conditional-brace idiom (dev-env#620)", test_detects_powershell_conditional_brace_commit),
        ("is_git_commit_command: bash brace-group idiom", test_detects_bash_brace_group_commit),
        ("build_message: no drift", test_build_message_no_drift),
        ("build_message: with drift", test_build_message_with_drift),
        ("build_message: None branch placeholder", test_build_message_none_branch_shows_placeholder),
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
