#!/usr/bin/env python3
"""Unit tests for post-merge-tile-checkpoint.py's merge-detection predicate.

`post-merge-tile-checkpoint.py` is a PostToolUse hook that, after a successful
`gh pr merge`, emits a blocking reminder to spawn follow-up tiles via spawn_task
(ADR-046 enforcement). The detection of "was this a successful merge?" is
extracted into the pure `is_successful_merge()` predicate so it can be exercised
offline (no subprocess, no network), matching the repo's fixture-only convention.

dev-env#485 removed the `exit_code` parameter entirely: `exit_code == 0 OR
marker` fired on any exit-0 command matching "gh pr merge" as a substring,
including `gh pr merge --help`. The predicate now gates solely on the success
marker, matching post-pr-merge-project.py's `merge_succeeded()`.

dev-env#529 (ADR-050 Amendment 9) converged the command-shape check itself
from a raw `"gh pr merge" not in command` substring test onto the
`scan_top_level`-anchored predicate already used by usage-snapshot.py /
pr-merge-reminder.py / post-pr-merge-project.py. The three
heredoc/quote/subshell tests below pin the false-positive shapes that
substring test was blind to (dev-env#499's original repro class) but the
anchored predicate correctly rejects.

dev-env#557: `main()` adds a second guard — `if is_merge_help_only(command):
sys.exit(0)`, right after the existing `if not scan_top_level(command,
_check_merge_stmt): sys.exit(0)` line, before computing `exit_code` — so a
`gh pr merge --help` command (which passes that scan_top_level check, since
it *is* a `gh pr merge` invocation) never reaches the live `gh pr view`
fallback that would otherwise misattribute an unrelated already-merged PR
(dev-env#485's original repro was this same `--help` shape; #557 found the
fallback path around it). `is_merge_help_only` itself is exhaustively tested
in `test_hookio.py`; the composition test below pins that `is_successful_merge`
(the predicate `main()`'s guard sits behind) returns False for exactly the
`--help` shape `is_merge_help_only` returns True for.

The main() I/O (stdin read, stderr write, sys.exit) is not covered — pure-helper
convention.

Usage:
    py -3 claude/scripts/tests/test_post_merge_tile_checkpoint.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-merge-tile-checkpoint.py"

# The script imports _hookio (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_merge_tile_checkpoint", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
pmtc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pmtc)  # safe: main() is guarded by __main__
is_successful_merge = pmtc.is_successful_merge

# is_merge_help_only lives in _hookio (a sibling); SCRIPT.parent already on
# sys.path via the insert above.
from _hookio import is_merge_help_only  # noqa: E402


def test_clean_merge_with_marker_fires() -> str:
    # The success marker is what confirms a completed merge; the exit code is
    # no longer consulted at all (dev-env#485) — true whether it came from a
    # clean canonical-checkout exit or a worktree's non-zero cleanup failure
    # (issue #275).
    assert is_successful_merge(
        "gh pr merge 415 --squash --delete-branch",
        "Squashed and merged pull request #415",
    )
    return "'Squashed and merged' marker -> fires"


def test_failed_merge_no_marker_ignored() -> str:
    # A genuine merge failure (no success marker) must not fire.
    assert not is_successful_merge(
        "gh pr merge 415 --squash",
        "X Pull request #415 is not mergeable",
    )
    return "gh pr merge failed (no success marker) -> no-op"


def test_non_merge_commands_ignored() -> str:
    assert not is_successful_merge("gh pr create --fill", "")
    assert not is_successful_merge("npm test", "")
    assert not is_successful_merge("git push origin main", "")
    return "non-merge commands -> no-op"


def test_help_invocation_no_marker_ignored() -> str:
    # dev-env#485 regression: `gh pr merge --help` exits 0 but prints no
    # success marker. The old exit_code==0 OR marker gate fired here; gating
    # on the marker alone fixes it.
    assert not is_successful_merge(
        "gh pr merge --help",
        "FLAGS\n      --admin   Use administrator privileges to merge a pull request",
    )
    return "gh pr merge --help (exit 0, no marker) -> no-op (dev-env#485)"


def test_rest_merge_fallback_with_marker_fires() -> str:
    # dev-env#986: the two-step REST merge fallback bypasses `gh pr merge`
    # entirely (e.g. during a GitHub GraphQL rate-limit outage).
    assert is_successful_merge(
        "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash",
        '{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}',
    )
    return "REST merge fallback + \"merged\":true -> fires (dev-env#986)"


def test_rest_merge_fallback_without_marker_ignored() -> str:
    assert not is_successful_merge(
        "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash",
        '{"message":"Merge already in progress"}',
    )
    return "REST merge call without \"merged\":true -> no-op"


# ---------------------------------------------------------------------------
# command-shape anchoring (dev-env#529, ADR-050 Amendment 9)
#
# Each command below contains the literal substring "gh pr merge" but not as
# a genuine top-level invocation. Paired with an output that DOES carry a
# real success marker, isolating the command-shape check: the old crude
# `"gh pr merge" not in command` substring test would have proceeded past
# this check straight to the (passing) marker check and fired -- a false
# positive. The scan_top_level-anchored check returns False before the
# marker is ever consulted.
# ---------------------------------------------------------------------------

def test_merge_text_in_heredoc_body_not_matched() -> str:
    command = "git commit -F - <<'EOF'\ngh pr merge --squash --delete-branch\nEOF"
    assert not is_successful_merge(command, "Squashed and merged pull request #415")
    return "'gh pr merge' text inside a heredoc body -> no match (dev-env#529)"


def test_merge_text_inside_double_quotes_not_matched() -> str:
    # The && inside the quoted commit message would, without quote-tracking,
    # wrongly carve out a second top-level segment starting with "gh pr
    # merge" -- the dev-env#499 false-positive class scan_top_level exists
    # to prevent.
    command = 'git commit -m "gh pr create --fill && gh pr merge --auto"'
    assert not is_successful_merge(command, "Squashed and merged pull request #415")
    return "'gh pr merge' text inside a double-quoted commit message -> no match (dev-env#529)"


def test_merge_text_inside_subshell_not_matched() -> str:
    command = "echo $(gh pr create --fill && gh pr merge --auto)"
    assert not is_successful_merge(command, "Squashed and merged pull request #415")
    return "'gh pr merge' text inside a $() subshell -> no match (dev-env#529)"


# ---------------------------------------------------------------------------
# is_merge_help_only composition (dev-env#557)
# ---------------------------------------------------------------------------

def test_help_command_not_successful_merge_and_is_help_only() -> str:
    command = "gh pr merge --help"
    output = "FLAGS\n      --admin   Use administrator privileges to merge a pull request"
    assert not is_successful_merge(command, output), "no success marker -> not is_successful_merge"
    assert is_merge_help_only(command), "gh pr merge --help -> is_merge_help_only True"
    return "gh pr merge --help: is_successful_merge False, is_merge_help_only True -> guard fires (dev-env#557)"


def test_unresolved_real_merge_is_not_help_only() -> str:
    # A genuine merge with no marker (e.g. dev-env#489's lost-marker shape) and
    # a non-zero exit must NOT be classified as help-only -- the live gh-pr-view
    # fallback must still be attempted for this shape, unchanged.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    assert not is_successful_merge(command, output)
    assert not is_merge_help_only(command), "bare merge, no --help -> guard must not suppress it"
    return "unresolved real merge (no marker, non-help) -> is_merge_help_only False (fallback unaffected)"


def main() -> int:
    tests = [
        ("merge marker present -> fires", test_clean_merge_with_marker_fires),
        ("failed merge with no marker ignored", test_failed_merge_no_marker_ignored),
        ("non-merge commands ignored", test_non_merge_commands_ignored),
        ("gh pr merge --help (no marker) ignored (dev-env#485)", test_help_invocation_no_marker_ignored),
        ("REST merge fallback + \"merged\":true -> fires (dev-env#986)", test_rest_merge_fallback_with_marker_fires),
        ("REST merge fallback without marker ignored (dev-env#986)", test_rest_merge_fallback_without_marker_ignored),
        ("'gh pr merge' text in heredoc body ignored (dev-env#529)", test_merge_text_in_heredoc_body_not_matched),
        ("'gh pr merge' text in double quotes ignored (dev-env#529)", test_merge_text_inside_double_quotes_not_matched),
        ("'gh pr merge' text in $() subshell ignored (dev-env#529)", test_merge_text_inside_subshell_not_matched),
        ("gh pr merge --help: guard fires (dev-env#557)", test_help_command_not_successful_merge_and_is_help_only),
        ("unresolved real merge: guard does not suppress fallback", test_unresolved_real_merge_is_not_help_only),
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
