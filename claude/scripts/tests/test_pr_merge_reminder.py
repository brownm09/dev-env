#!/usr/bin/env python3
"""Unit tests for pr-merge-reminder.py.

Tests the pure predicate functions and the _create_shard_step helper introduced
in dev-env#403 (add open-PR shard instruction to gh pr create reminder).

Subprocess calls (_open_pr_for_cwd) are not exercised here.

Usage:
    py -3 claude/scripts/tests/test_pr_merge_reminder.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "pr-merge-reminder.py"

sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("pr_merge_reminder", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
pmr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pmr)

is_pr_create_command = pmr.is_pr_create_command
is_pr_merge_command = pmr.is_pr_merge_command
is_git_push_command = pmr.is_git_push_command
_create_shard_step = pmr._create_shard_step


# ---------------------------------------------------------------------------
# is_pr_create_command
# ---------------------------------------------------------------------------

def test_create_simple() -> str:
    assert is_pr_create_command("gh pr create --fill")
    return "bare gh pr create -> match"


def test_create_with_cd_prefix() -> str:
    assert is_pr_create_command("cd /some/path && gh pr create --fill")
    return "cd ... && gh pr create -> match"


def test_create_inside_subshell_not_matched() -> str:
    assert not is_pr_create_command("echo $(gh pr create --fill)")
    return "gh pr create inside $() subshell -> no match"


def test_create_inside_double_quotes_not_matched() -> str:
    assert not is_pr_create_command('echo "gh pr create --fill"')
    return "gh pr create inside double quotes -> no match"


def test_create_in_heredoc_not_matched() -> str:
    cmd = "git commit -m <<'EOF'\ngh pr create --fill\nEOF"
    assert not is_pr_create_command(cmd)
    return "gh pr create inside heredoc -> no match"


def test_merge_not_matched_as_create() -> str:
    assert not is_pr_create_command("gh pr merge --squash")
    return "gh pr merge -> not a create match"


# ---------------------------------------------------------------------------
# is_pr_merge_command
# ---------------------------------------------------------------------------

def test_merge_simple() -> str:
    assert is_pr_merge_command("gh pr merge 54 --squash --delete-branch")
    return "bare gh pr merge -> match"


def test_create_not_matched_as_merge() -> str:
    assert not is_pr_merge_command("gh pr create --fill")
    return "gh pr create -> not a merge match"


# ---------------------------------------------------------------------------
# is_git_push_command
# ---------------------------------------------------------------------------

def test_push_simple() -> str:
    assert is_git_push_command("git push -u origin feat/foo")
    return "bare git push -> match"


def test_push_inside_subshell_not_matched() -> str:
    assert not is_git_push_command("echo $(git push)")
    return "git push inside $() -> no match"


# ---------------------------------------------------------------------------
# _create_shard_step
# ---------------------------------------------------------------------------

def test_shard_step_with_url_in_output() -> str:
    output = (
        "\nhttps://github.com/brownm09/dev-env/pull/403\n"
    )
    step = _create_shard_step(output)
    assert "403" in step, f"PR number not in step: {step!r}"
    assert "https://github.com/brownm09/dev-env/pull/403" in step
    assert "open-prs/403.json" in step
    assert "3a" in step
    assert "3b" in step
    return "URL in output -> shard step includes PR number, URL, filename"


def test_shard_step_no_url_in_output() -> str:
    step = _create_shard_step("")
    assert "<N>" in step or "open-prs" in step
    assert "3a" in step
    assert "3b" in step
    assert "pr (int)" in step or "Fields:" in step
    return "empty output -> generic shard step with field hints"


def test_shard_step_url_with_trailing_dot() -> str:
    output = "https://github.com/brownm09/dev-env/pull/99."
    step = _create_shard_step(output)
    assert "pull/99" in step
    assert "pull/99." not in step, "trailing dot should be stripped"
    return "trailing dot on PR URL is stripped"


def test_shard_step_merge_url_not_matched() -> str:
    # URLs for issues or other paths must not trigger the PR shard step.
    output = "https://github.com/brownm09/dev-env/issues/403"
    step = _create_shard_step(output)
    assert "<N>" in step or "open-prs" in step
    assert "403" not in step or "open-prs/403" not in step, (
        f"issue URL should not produce PR-specific shard step: {step!r}"
    )
    return "issue URL does not trigger PR-specific shard step"


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        ("bare gh pr create -> match", test_create_simple),
        ("cd ... && gh pr create -> match", test_create_with_cd_prefix),
        ("gh pr create in $() -> no match", test_create_inside_subshell_not_matched),
        ("gh pr create in quotes -> no match", test_create_inside_double_quotes_not_matched),
        ("gh pr create in heredoc -> no match", test_create_in_heredoc_not_matched),
        ("gh pr merge not a create", test_merge_not_matched_as_create),
        ("bare gh pr merge -> match", test_merge_simple),
        ("gh pr create not a merge", test_create_not_matched_as_merge),
        ("bare git push -> match", test_push_simple),
        ("git push in $() -> no match", test_push_inside_subshell_not_matched),
        ("shard step: URL in output", test_shard_step_with_url_in_output),
        ("shard step: no URL -> generic hint", test_shard_step_no_url_in_output),
        ("shard step: trailing dot stripped", test_shard_step_url_with_trailing_dot),
        ("shard step: issue URL not matched", test_shard_step_merge_url_not_matched),
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
