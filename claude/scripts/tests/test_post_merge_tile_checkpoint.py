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


def main() -> int:
    tests = [
        ("merge marker present -> fires", test_clean_merge_with_marker_fires),
        ("failed merge with no marker ignored", test_failed_merge_no_marker_ignored),
        ("non-merge commands ignored", test_non_merge_commands_ignored),
        ("gh pr merge --help (no marker) ignored (dev-env#485)", test_help_invocation_no_marker_ignored),
        ("'gh pr merge' text in heredoc body ignored (dev-env#529)", test_merge_text_in_heredoc_body_not_matched),
        ("'gh pr merge' text in double quotes ignored (dev-env#529)", test_merge_text_inside_double_quotes_not_matched),
        ("'gh pr merge' text in $() subshell ignored (dev-env#529)", test_merge_text_inside_subshell_not_matched),
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
