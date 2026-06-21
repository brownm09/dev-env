#!/usr/bin/env python3
"""Unit tests for post-pr-merge-project.py's PR-number extraction and merge gate.

`post-pr-merge-project.py` is a PostToolUse hook that, after a successful
`gh pr merge`, moves the linked issue's GitHub Project item to Done. Before #380
it read the legacy `tool_response["output"]` (always empty on the real payload)
and only looked for a `/pull/N` URL — which `gh pr merge` output never contains —
so it never fired; the board move was masked by GitHub's native issue-closed
automation (ADR-049 / ADR-050).

The fix: read output via the shared `read_command_output`, derive the PR number
from the command (`gh pr merge 380` / a `/pull/380` URL) with a fallback to gh's
success marker in the output, and gate the move on a confirmed merge marker so a
queued `--auto` or a failed merge does not move an issue to Done.

These tests exercise the pure helpers offline (no network, no gh). The live gh
calls (`get_pr_body`, `find_project_item`, `move_to_done`) are intentionally not
tested.

Usage:
    py -3 claude/scripts/tests/test_post_pr_merge_project.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-pr-merge-project.py"

# The script imports _winsubp and _hookio (siblings in scripts/); make resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_pr_merge_project", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
ppmp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ppmp)  # safe: main() is guarded by __main__
extract_pr_number_from_command = ppmp.extract_pr_number_from_command
extract_pr_number = ppmp.extract_pr_number
merge_succeeded = ppmp.merge_succeeded


# --- extract_pr_number_from_command --------------------------------------

def test_cmd_bare_number() -> str:
    assert extract_pr_number_from_command("gh pr merge 380 --squash --delete-branch") == 380
    return "gh pr merge 380 ... -> 380"


def test_cmd_url() -> str:
    cmd = "gh pr merge https://github.com/brownm09/dev-env/pull/380 --squash"
    assert extract_pr_number_from_command(cmd) == 380
    return "gh pr merge <pull-url> -> 380"


def test_cmd_with_cd_prefix() -> str:
    cmd = "cd /c/Users/brown/Git/dev-env && gh pr merge 412 --squash --delete-branch"
    assert extract_pr_number_from_command(cmd) == 412
    return "cd ... && gh pr merge 412 -> 412"


def test_cmd_bare_no_number_is_none() -> str:
    # The dominant workflow form names no PR — number must come from the output.
    assert extract_pr_number_from_command("gh pr merge --squash --delete-branch") is None
    return "bare gh pr merge --squash --delete-branch -> None (falls back to output)"


# --- extract_pr_number (output) ------------------------------------------

def test_output_squash_marker() -> str:
    out = "✓ Squashed and merged pull request #380 (Fix sibling hooks)"
    assert extract_pr_number(out) == 380
    return "'Squashed and merged pull request #380' -> 380"


def test_output_merged_marker() -> str:
    assert extract_pr_number("✓ Merged pull request #5 (Title)") == 5
    return "'Merged pull request #5' -> 5"


def test_output_cross_repo_marker() -> str:
    out = "✓ Rebased and merged pull request brownm09/dev-env#7"
    assert extract_pr_number(out) == 7
    return "cross-repo 'owner/repo#7' marker -> 7"


def test_output_legacy_url() -> str:
    assert extract_pr_number("https://github.com/brownm09/dev-env/pull/42") == 42
    return "legacy /pull/N URL in output -> 42"


def test_output_auto_queue_is_none() -> str:
    # A queued --auto prints "Pull request #N will be automatically merged" — no
    # action verb before "pull request", so it must NOT be read as a merge.
    out = "✓ Pull request #380 will be automatically merged when all requirements are met"
    assert extract_pr_number(out) is None
    return "queued --auto message -> None (no completed-merge number)"


def test_output_empty_is_none() -> str:
    assert extract_pr_number("") is None
    assert extract_pr_number("no pr here") is None
    return "empty / marker-less output -> None"


# --- merge_succeeded -----------------------------------------------------

def test_merge_succeeded_true() -> str:
    assert merge_succeeded("✓ Squashed and merged pull request #380 (Title)")
    assert merge_succeeded("✓ Merged pull request #1")
    assert merge_succeeded("✓ Rebased and merged pull request #2")
    return "real merge markers -> True"


def test_merge_succeeded_excludes_auto_and_failure() -> str:
    assert not merge_succeeded("✓ Pull request #380 will be automatically merged")
    assert not merge_succeeded("X Pull request #380 is not mergeable")
    assert not merge_succeeded(""), "empty output -> not a merge"
    return "queued --auto / failed / empty -> False (no premature Done move)"


def main() -> int:
    tests = [
        ("command: bare number", test_cmd_bare_number),
        ("command: pull URL", test_cmd_url),
        ("command: cd-prefixed", test_cmd_with_cd_prefix),
        ("command: bare merge has no number", test_cmd_bare_no_number_is_none),
        ("output: squash marker", test_output_squash_marker),
        ("output: merged marker", test_output_merged_marker),
        ("output: cross-repo marker", test_output_cross_repo_marker),
        ("output: legacy URL", test_output_legacy_url),
        ("output: --auto queue is None", test_output_auto_queue_is_none),
        ("output: empty is None", test_output_empty_is_none),
        ("merge_succeeded: real markers True", test_merge_succeeded_true),
        ("merge_succeeded: excludes auto/failure", test_merge_succeeded_excludes_auto_and_failure),
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
