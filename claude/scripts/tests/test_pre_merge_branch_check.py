#!/usr/bin/env python3
"""Unit tests for pre-merge-branch-check.py.

Exercises the pure helpers offline (no subprocess, no stdin): `is_pr_merge_command()`
(built on the shared `_hookio.scan_top_level`, matching the identically-named
predicate in pre-merge-message-check.py / pre-merge-numbering-check.py —
dev-env#519) and the new `build_message()` formatter added for dev-env#573's
drift-warning integration. `current_branch()` / `current_repo_root()` shell
out to git and are not covered here (pure-helper convention).

Usage:
    py -3 claude/scripts/tests/test_pre_merge_branch_check.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import importlib

pre_merge_branch_check = importlib.import_module("pre-merge-branch-check")


def test_detects_bare_merge() -> str:
    assert pre_merge_branch_check.is_pr_merge_command("gh pr merge --squash --delete-branch")
    return "a bare `gh pr merge` invocation is detected"


def test_detects_chained_merge() -> str:
    assert pre_merge_branch_check.is_pr_merge_command("cd /repo && gh pr merge 42")
    return "a cd-chained `gh pr merge` is detected"


def test_ignores_merge_mentioned_in_heredoc() -> str:
    command = "cat <<'EOF'\nremember to gh pr merge later\nEOF"
    assert not pre_merge_branch_check.is_pr_merge_command(command)
    return "`gh pr merge` mentioned only inside a heredoc body is not detected (dev-env#499)"


def test_ignores_unrelated_command() -> str:
    assert not pre_merge_branch_check.is_pr_merge_command("gh pr view 42")
    return "an unrelated gh command is not detected"


def test_build_message_no_drift() -> str:
    msg = pre_merge_branch_check.build_message("feat/x", "C:/repo", None)
    assert msg.startswith("[merge-branch-check] merging from: feat/x (repo: C:/repo)")
    assert "dev-env#573" in msg
    assert "\n" not in msg
    return "build_message is a single line with no drift warning appended"


def test_build_message_with_drift() -> str:
    msg = pre_merge_branch_check.build_message("main", "C:/repo", "⚠ [cwd-drift] mismatch")
    assert "⚠ [cwd-drift] mismatch" in msg
    assert msg.index("merging from:") < msg.index("⚠ [cwd-drift]")
    return "build_message appends the drift warning on its own line when present"


def test_build_message_none_values_show_placeholders() -> str:
    msg = pre_merge_branch_check.build_message(None, None, None)
    assert "merging from: <detached HEAD or unknown> (repo: <unknown>)" in msg, msg
    return "build_message renders placeholders for None branch/repo_root"


def main() -> int:
    tests = [
        ("is_pr_merge_command: bare", test_detects_bare_merge),
        ("is_pr_merge_command: chained", test_detects_chained_merge),
        ("is_pr_merge_command: ignores heredoc mention", test_ignores_merge_mentioned_in_heredoc),
        ("is_pr_merge_command: ignores unrelated command", test_ignores_unrelated_command),
        ("build_message: no drift", test_build_message_no_drift),
        ("build_message: with drift", test_build_message_with_drift),
        ("build_message: None placeholders", test_build_message_none_values_show_placeholders),
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
