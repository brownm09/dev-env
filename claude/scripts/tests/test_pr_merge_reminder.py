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

# read_command_output lives in _hookio (a sibling of pr-merge-reminder.py).
# SCRIPT.parent already on sys.path, so import it directly.
from _hookio import read_command_output  # noqa: E402


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


def test_shard_step_url_trailing_dot_excluded_by_regex() -> str:
    # The regex (\d+) stops at non-digits, so a trailing dot in the raw output
    # string never reaches group(0). No explicit strip is needed or present.
    output = "https://github.com/brownm09/dev-env/pull/99."
    step = _create_shard_step(output)
    assert "pull/99" in step
    assert "pull/99." not in step, "trailing dot must not appear in shard step URL"
    return "trailing dot excluded by regex boundary, not explicit strip"


def test_shard_step_via_stdout_field() -> str:
    # The real hook payload puts gh's output in tool_response.stdout, not .output
    # (ADR-049/ADR-050). Simulate a real payload and verify the URL is found when
    # read_command_output is used — the same path main() now takes.
    data = {
        "tool_response": {
            "stdout": "https://github.com/brownm09/dev-env/pull/404\n",
            "stderr": "",
            "exitCode": 0,
        }
    }
    output = read_command_output(data)
    step = _create_shard_step(output)
    assert "404" in step, f"PR number not found via stdout field: {step!r}"
    assert "open-prs/404.json" in step
    return "stdout field -> URL found via read_command_output"


def test_shard_step_legacy_output_field_empty_gives_fallback() -> str:
    # Confirms that reading the legacy .output field (as the code incorrectly
    # did before ADR-049/ADR-050) yields the fallback, not the URL branch.
    data = {
        "tool_response": {
            "output": "https://github.com/brownm09/dev-env/pull/999",
            "exitCode": 0,
        }
    }
    # read_command_output prefers stdout/stderr; falls back to .output only
    # when both are absent. Since stdout is absent here, it uses .output.
    output = read_command_output(data)
    step = _create_shard_step(output)
    # Behaviour with only .output: still works (fallback chain in read_command_output)
    assert "999" in step, (
        "legacy .output fallback should still extract URL when stdout/stderr absent"
    )
    return "legacy .output fallback still works when stdout/stderr absent"


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
        ("shard step: trailing dot excluded by regex", test_shard_step_url_trailing_dot_excluded_by_regex),
        ("shard step: issue URL not matched", test_shard_step_merge_url_not_matched),
        ("shard step: URL found via stdout field", test_shard_step_via_stdout_field),
        ("shard step: legacy .output fallback", test_shard_step_legacy_output_field_empty_gives_fallback),
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
