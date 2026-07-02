#!/usr/bin/env python3
"""Tests for pre-merge-findings-gate.py's is_pr_merge_command pure helper.

Exercises the command-detection predicate offline (no disk, no network, no
gh). This is the file's *pure-helper* coverage layer, added alongside the
existing behavioral self-test `test-merge-findings-gate.sh` (which drives the
real hook end-to-end via the MERGE_GATE_TEST_JSON seam to pin the
clean-review / open-findings / disposition-recorded / no-marker / gh-failure
decision paths). `_parse_merge_target`, `_fetch_pr_json`, and the stdin/exit-2
plumbing are not covered here (pure-helper convention; `_parse_merge_target`
is already exercised by the shell test's step 7).
"""
import importlib.util
import os
import sys

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-merge-findings-gate.py")
# The script imports _winsubp and _hookio (siblings in scripts/); make them resolvable.
sys.path.insert(0, os.path.dirname(_SCRIPT))
spec = importlib.util.spec_from_file_location("pre_merge_findings_gate", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

is_pr_merge_command = mod.is_pr_merge_command


# ---------------------------------------------------------------------------
# is_pr_merge_command tests
# ---------------------------------------------------------------------------

def test_bare_merge():
    assert is_pr_merge_command("gh pr merge 999 --repo o/r --squash --delete-branch")

def test_merge_chained():
    assert is_pr_merge_command("git fetch origin && gh pr merge --squash")

def test_merge_cd_chained():
    assert is_pr_merge_command("cd C:/Users/brown/Git/dev-env && gh pr merge --squash")

def test_non_merge_gh_command():
    assert not is_pr_merge_command("gh pr view 999 --repo o/r")

def test_unrelated_command():
    assert not is_pr_merge_command("git status")

def test_merge_heredoc_body_not_matched():
    # dev-env#499: a "gh pr merge" mentioned only inside a heredoc body
    # (e.g. as prose in a commit message) must not count as a genuine
    # top-level invocation -- it must not pay this hook's live `gh pr view`
    # call for a command that never actually merges.
    command = 'git commit -m "$(cat <<\'EOF\'\nmentions gh pr merge in prose\nEOF\n)"'
    assert is_pr_merge_command(command) is False


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
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
