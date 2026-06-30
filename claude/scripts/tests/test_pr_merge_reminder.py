#!/usr/bin/env python3
"""Unit tests for pr-merge-reminder.py.

Tests the pure predicate functions, the _create_shard_step helper (dev-env#403),
and the push-scoping behavior added in dev-env#442 / ADR-065: _effective_push_dir
scopes the open-PR lookup to the repo a `cd <path> && git push` actually targets,
so a cross-repo push is evaluated against THAT repo, not the session cwd.

The live _open_pr_for_cwd subprocess boundary is not exercised here (repo
convention: no subprocess mocks).

Usage:
    py -3 claude/scripts/tests/test_pr_merge_reminder.py

Exit 0 = all pass.
"""

import importlib.util
import os
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
_is_successful_merge_call = pmr._is_successful_merge_call
_effective_push_dir = pmr._effective_push_dir

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
# _is_successful_merge_call
# ---------------------------------------------------------------------------

def test_merge_call_clean_exit() -> str:
    assert _is_successful_merge_call(0, "")
    return "exit 0 + empty output -> fires"


def test_merge_call_worktree_nonzero_with_marker() -> str:
    # Worktree merges exit non-zero on local cleanup; the stdout success marker
    # confirms the remote merge actually completed (issue #275 behaviour).
    assert _is_successful_merge_call(
        1, "Squashed and merged pull request #419"
    )
    return "exit 1 + 'Squashed and merged' marker -> fires"


def test_merge_call_failed_no_marker() -> str:
    # A genuine merge failure (non-zero, no success marker) must not fire.
    assert not _is_successful_merge_call(
        1, "X Pull request #419 is not mergeable"
    )
    return "exit 1 + no success marker -> no-op"


def test_merge_call_exit_zero_trumps_no_marker() -> str:
    # exit 0 is sufficient even when no success text appears (dry-run / quiet mode).
    assert _is_successful_merge_call(0, "some other output with no merge line")
    return "exit 0 + no marker -> fires (exit code is authoritative)"


# ---------------------------------------------------------------------------
# _effective_push_dir  (dev-env#442 / ADR-065)
# ---------------------------------------------------------------------------

def test_push_dir_bare_push_is_cwd() -> str:
    assert _effective_push_dir("git push -u origin feat/foo", "/session/cwd") == "/session/cwd"
    return "bare git push -> session cwd"


def test_push_dir_cd_chain_redirects() -> str:
    # `cd <other-repo> && git push` is the cross-repo shape behind the false positive.
    out = _effective_push_dir("cd /Git/dev-env && git push", "/Git/lifting-logbook")
    assert out == "/Git/dev-env", f"expected /Git/dev-env, got {out!r}"
    return "cd <repo> && git push -> that repo, not session cwd"


def test_push_dir_cd_chain_multi_segment() -> str:
    # The push is usually the tail of a longer add/commit/push chain.
    out = _effective_push_dir(
        'cd /Git/engineering-journal && git add . && git commit -m "x" && git push',
        "/Git/lifting-logbook",
    )
    assert out == "/Git/engineering-journal", f"got {out!r}"
    return "cd <ej> && ... && git push -> the ej dir (then _open_pr_for_cwd skips it)"


def test_push_dir_quoted_path() -> str:
    out = _effective_push_dir('cd "/Git/dir with spaces" && git push', "/base")
    assert out == "/Git/dir with spaces", f"got {out!r}"
    return "quoted cd path -> unquoted target dir"


def test_push_dir_relative_resolved_against_cwd() -> str:
    out = _effective_push_dir("cd sub/repo && git push", "/base")
    assert os.path.isabs(out), f"relative target not resolved: {out!r}"
    assert os.path.basename(out) == "repo"
    assert out == os.path.normpath(os.path.join("/base", "sub/repo"))
    return "relative cd path -> normalized join under cwd"


def test_push_dir_semicolon_chain() -> str:
    out = _effective_push_dir("cd /Git/dev-env ; git push", "/base")
    assert out == "/Git/dev-env", f"got {out!r}"
    return "cd <repo> ; git push -> that repo (semicolon chain)"


def test_push_dir_cd_after_push_ignored() -> str:
    # A cd appearing only AFTER the push does not govern it -> fall back to cwd.
    out = _effective_push_dir("git push && cd /Git/elsewhere", "/base")
    assert out == "/base", f"cd after push must not redirect: {out!r}"
    return "cd after the push -> cwd (push region excludes it)"


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
        ("merge call: exit 0 fires", test_merge_call_clean_exit),
        ("merge call: exit 1 + marker fires (worktree case)", test_merge_call_worktree_nonzero_with_marker),
        ("merge call: exit 1 no marker -> no-op", test_merge_call_failed_no_marker),
        ("merge call: exit 0 trumps no marker", test_merge_call_exit_zero_trumps_no_marker),
        ("push dir: bare push -> cwd", test_push_dir_bare_push_is_cwd),
        ("push dir: cd <repo> && push -> that repo", test_push_dir_cd_chain_redirects),
        ("push dir: cd <ej> && ... && push -> ej dir", test_push_dir_cd_chain_multi_segment),
        ("push dir: quoted cd path", test_push_dir_quoted_path),
        ("push dir: relative path resolved vs cwd", test_push_dir_relative_resolved_against_cwd),
        ("push dir: semicolon chain", test_push_dir_semicolon_chain),
        ("push dir: cd after push ignored", test_push_dir_cd_after_push_ignored),
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
