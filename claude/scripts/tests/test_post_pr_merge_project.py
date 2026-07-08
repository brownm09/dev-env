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

dev-env#489: gh's success marker does not always survive to this hook's captured
output when gh exits abruptly right after a worktree's local-cleanup git
subprocess fails ("main is already checked out") — even though gh prints the
marker before that failure. `main()` now falls back to a live `gh pr view`
confirmation (`_hookio.confirm_merge_via_gh`, gated by `_hookio.
should_confirm_via_gh`) when the marker is absent and the exit code is
non-zero, since a missed move-to-Done has no other backstop. `should_confirm_via_gh`
is covered in `test_hookio.py`; `confirm_merge_via_gh` itself is not (it shells
out to `gh pr view`).

dev-env#557: `main()` gates that live-confirmation fallback behind a new
`_hookio.is_merge_help_only(command)` check (`if is_merge_help_only(command):
sys.exit(0)`, right after `if not merge_succeeded(output):`, before computing
`exit_code`) — `gh pr merge --help` textually satisfies `merge_succeeded`'s
own upstream `scan_top_level` gate but can never complete a real merge, and
without this guard the live `gh pr view` fallback resolves with no PR number
against cwd's checked-out branch, misattributing an unrelated already-merged
PR to the harmless `--help` invocation (a confirmed live incident). `main()`'s
stdin/live-`gh` plumbing is not driven end-to-end here (pure-helper
convention, and this hook's `main()` requires a `.claude/hook-config.json`
fixture to reach the guard at all) — `is_merge_help_only` itself is
exhaustively tested in `test_hookio.py`. The composition test below instead
pins that `merge_succeeded` (the predicate the guard sits behind) returns
False for exactly the `--help` shape that `is_merge_help_only` returns True
for, proving the two predicates line up the way `main()`'s guard depends on,
while a genuine unresolved-marker real-merge scenario is unaffected.

dev-env#559: `repo` previously always came from cwd's own `.claude/hook-config.json`
via `load_config(cwd)`, never from the merge command itself — a `gh pr merge
<cross-repo URL>` run from an unrelated cwd (no `cd`-chain, no `--repo` flag)
silently resolved to cwd's own repo, fetched the WRONG PR's body via `get_pr_body`,
and moved an unrelated same-numbered issue to Done on cwd's own project board.
`extract_repo_from_command` parses the owner/repo out of a PR URL argument
(mirroring `extract_pr_number_from_command`); `main()` also skips the whole
operation when that parsed repo does not match cwd's config, since
`find_project_item`/`move_to_done` use cwd's own project-board fields
(`project_number`/`project_node_id`/etc.), which do not apply to a different
repo regardless of which PR's body was fetched.

These tests exercise the pure helpers offline (no network, no gh). The live gh
calls (`get_pr_body`, `find_project_item`, `move_to_done`, `confirm_merge_via_gh`)
are intentionally not tested.

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
extract_repo_from_command = ppmp.extract_repo_from_command

# is_merge_help_only lives in _hookio (a sibling); SCRIPT.parent already on
# sys.path via the insert above.
from _hookio import is_merge_help_only  # noqa: E402


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


def test_cmd_flag_before_arg() -> str:
    assert extract_pr_number_from_command("gh pr merge --squash 380") == 380
    return "gh pr merge --squash 380 (flag before positional) -> 380"


def test_cmd_url_in_flag_value_not_hijacked() -> str:
    # A /pull/N URL inside a --subject value must NOT override the merged number
    # (the whole-command search bug the review caught).
    cmd = 'gh pr merge 380 --squash --subject "see https://github.com/o/r/pull/200"'
    assert extract_pr_number_from_command(cmd) == 380
    return "URL in --subject value does not hijack the number -> 380"


def test_cmd_chained_url_ignored() -> str:
    # A /pull/N URL in a chained sibling command must not be picked up.
    cmd = "echo https://github.com/o/r/pull/55 && gh pr merge 380 --squash"
    assert extract_pr_number_from_command(cmd) == 380
    return "chained-command URL ignored (statement-scoped) -> 380"


def test_cmd_branch_name_no_number() -> str:
    # Merging by branch name names no PR number -> None (falls back to output).
    assert extract_pr_number_from_command("gh pr merge my-feature-2 --squash") is None
    return "merge by branch name -> None (digit inside name is not a token)"


# --- extract_repo_from_command (dev-env#559) ------------------------------

def test_repo_from_cross_repo_url() -> str:
    # The #559 repro: a PR URL naming a repo different from cwd's own config.
    cmd = 'gh pr merge "https://github.com/brownm09/dev-env/pull/554" --squash --delete-branch'
    assert extract_repo_from_command(cmd) == "brownm09/dev-env"
    return "gh pr merge <cross-repo pull-url> -> owner/repo parsed from the URL"


def test_repo_from_bare_number_is_none() -> str:
    # A bare positional number names no repo -- caller falls back to config.
    assert extract_repo_from_command("gh pr merge 380 --squash") is None
    return "gh pr merge <number> (no URL) -> None"


def test_repo_from_bare_form_is_none() -> str:
    assert extract_repo_from_command("gh pr merge --squash --delete-branch") is None
    return "bare gh pr merge --squash --delete-branch -> None"


def test_repo_from_cd_prefixed_url() -> str:
    cmd = "cd /c/Users/brown/Git/dev-env && gh pr merge https://github.com/brownm09/dev-env/pull/412 --squash"
    assert extract_repo_from_command(cmd) == "brownm09/dev-env"
    return "cd ... && gh pr merge <pull-url> -> owner/repo parsed"


def test_repo_from_chained_command_ignored() -> str:
    # A /pull/N URL in a chained sibling command must not be picked up --
    # mirrors extract_pr_number_from_command's identical statement-scoping.
    cmd = "echo https://github.com/o/r/pull/55 && gh pr merge 380 --squash"
    assert extract_repo_from_command(cmd) is None
    return "chained-command URL ignored (statement-scoped) -> None"


def test_repo_from_url_case_preserved() -> str:
    # Extraction preserves the URL's own casing; callers compare case-insensitively.
    cmd = 'gh pr merge "https://github.com/BrownM09/Dev-Env/pull/554" --squash'
    assert extract_repo_from_command(cmd) == "BrownM09/Dev-Env"
    return "mixed-case owner/repo in URL -> parsed verbatim (caller lower()s to compare)"


def test_repo_from_repo_flag_wins_over_subject_url() -> str:
    # Review finding on PR #572: an explicit --repo flag must win over an
    # unrelated PR URL mentioned in a --subject value, or a legitimate
    # same-repo merge would falsely mismatch and skip the Done-move.
    cmd = (
        'gh pr merge --repo brownm09/dev-env 380 --subject '
        '"duplicate of https://github.com/other/repo/pull/1"'
    )
    assert extract_repo_from_command(cmd) == "brownm09/dev-env"
    return "--repo flag wins over an unrelated URL in --subject value -> correct repo, not hijacked"


def test_repo_from_repo_flag_no_url() -> str:
    cmd = "gh pr merge 42 --repo brownm09/engineering-journal --squash"
    assert extract_repo_from_command(cmd) == "brownm09/engineering-journal"
    return "--repo flag, no PR URL -> flag's repo"


def test_repo_from_repo_flag_short_form() -> str:
    # dev-env#616: gh's -R shorthand for --repo was not recognized, so a
    # `-R owner/repo` merge command fell through to None and silently
    # resolved against cwd's own config instead of the command's actual
    # target repo (the filed incident: a dev-env merge run with `-R
    # brownm09/dev-env` from a lifting-logbook cwd moved a lifting-logbook
    # issue's board item instead of dev-env's).
    cmd = "gh pr merge 611 -R brownm09/dev-env --squash"
    assert extract_repo_from_command(cmd) == "brownm09/dev-env"
    return "-R flag (gh's --repo shorthand) resolves identically to --repo (dev-env#616)"


def test_repo_from_dash_r_mid_word_not_matched() -> str:
    # dev-env#626 / review finding on PR #623: the (?<!\S) lookbehind requires
    # -R to start a standalone token, so a coincidental "-R" mid-word (e.g. a
    # PR title fragment like "xx-R") must not be mistaken for the flag.
    cmd = "gh pr merge 42 xx-R brownm09/dev-env --squash"
    assert extract_repo_from_command(cmd) is None
    return "mid-word '-R' (not a standalone token) -> None, not falsely matched (dev-env#626)"


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


# ---------------------------------------------------------------------------
# is_merge_help_only composition (dev-env#557)
#
# main()'s guard sits behind `if not merge_succeeded(output):` — these tests
# pin that merge_succeeded returns False for exactly the --help shape
# is_merge_help_only returns True for (so the guard actually fires for the
# command it's meant to catch), and that a genuine unresolved-marker,
# non-help merge still leaves is_merge_help_only False (so the guard never
# suppresses a real merge's live gh-pr-view fallback).
# ---------------------------------------------------------------------------

def test_help_command_not_merge_succeeded_and_is_help_only() -> str:
    command = "gh pr merge --help"
    output = "FLAGS\n      --admin   Use administrator privileges to merge a pull request"
    assert not merge_succeeded(output), "no success marker -> not merge_succeeded"
    assert is_merge_help_only(command), "gh pr merge --help -> is_merge_help_only True"
    return "gh pr merge --help: merge_succeeded False, is_merge_help_only True -> guard fires (dev-env#557)"


def test_unresolved_real_merge_is_not_help_only() -> str:
    # A genuine merge with no marker (e.g. dev-env#489's lost-marker shape) and
    # a non-zero exit must NOT be classified as help-only -- the live gh-pr-view
    # fallback must still be attempted for this shape, unchanged.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    assert not merge_succeeded(output)
    assert not is_merge_help_only(command), "bare merge, no --help -> guard must not suppress it"
    return "unresolved real merge (no marker, non-help) -> is_merge_help_only False (fallback unaffected)"


def main() -> int:
    tests = [
        ("command: bare number", test_cmd_bare_number),
        ("command: pull URL", test_cmd_url),
        ("command: cd-prefixed", test_cmd_with_cd_prefix),
        ("command: bare merge has no number", test_cmd_bare_no_number_is_none),
        ("command: flag before arg", test_cmd_flag_before_arg),
        ("command: URL in flag value not hijacked", test_cmd_url_in_flag_value_not_hijacked),
        ("command: chained URL ignored", test_cmd_chained_url_ignored),
        ("command: branch name -> None", test_cmd_branch_name_no_number),
        ("repo: cross-repo URL parsed (dev-env#559)", test_repo_from_cross_repo_url),
        ("repo: bare number -> None", test_repo_from_bare_number_is_none),
        ("repo: bare form -> None", test_repo_from_bare_form_is_none),
        ("repo: cd-prefixed URL parsed", test_repo_from_cd_prefixed_url),
        ("repo: chained URL ignored", test_repo_from_chained_command_ignored),
        ("repo: mixed-case URL preserved", test_repo_from_url_case_preserved),
        ("repo: --repo flag wins over subject URL", test_repo_from_repo_flag_wins_over_subject_url),
        ("repo: --repo flag, no URL", test_repo_from_repo_flag_no_url),
        ("repo: -R shorthand resolves same as --repo (dev-env#616)", test_repo_from_repo_flag_short_form),
        ("repo: mid-word '-R' not matched (dev-env#626)", test_repo_from_dash_r_mid_word_not_matched),
        ("output: squash marker", test_output_squash_marker),
        ("output: merged marker", test_output_merged_marker),
        ("output: cross-repo marker", test_output_cross_repo_marker),
        ("output: legacy URL", test_output_legacy_url),
        ("output: --auto queue is None", test_output_auto_queue_is_none),
        ("output: empty is None", test_output_empty_is_none),
        ("merge_succeeded: real markers True", test_merge_succeeded_true),
        ("merge_succeeded: excludes auto/failure", test_merge_succeeded_excludes_auto_and_failure),
        ("gh pr merge --help: guard fires (dev-env#557)", test_help_command_not_merge_succeeded_and_is_help_only),
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
