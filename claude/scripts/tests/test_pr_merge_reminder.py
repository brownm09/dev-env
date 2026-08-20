#!/usr/bin/env python3
"""Unit tests for pr-merge-reminder.py.

Tests the pure predicate functions, the _create_shard_step helper (dev-env#403),
and the push-scoping behavior added in dev-env#442 / ADR-065: _effective_push_dir
scopes the open-PR lookup to the repo a `cd <path> && git push` actually targets,
so a cross-repo push is evaluated against THAT repo, not the session cwd.

dev-env#485 dropped the `exit_code` parameter from `_is_successful_merge_call`
entirely: `exit_code == 0 OR marker` fired on any exit-0 command matching
"gh pr merge" as a substring, including `gh pr merge --help`. It now gates
solely on the success marker, mirroring post-pr-merge-project.py's
`merge_succeeded()` and usage-snapshot.py's `merge_confirmed()`.

dev-env#494 extracted main()'s message dispatch into `_build_messages()` so
each message is gated on its own condition: a chained command matching both
is_create and is_merge (e.g. `gh pr create --fill && gh pr merge --auto`)
must still get the create reminder even when the merge sub-check is
incomplete (a queued --auto, or --help) — previously the shared early exit
inside the is_merge branch suppressed the create message too.

The create/push gate is `exit_code == 0 or merge_ok`, NOT `is_merge` alone —
an earlier draft of the #494 fix used `is_merge or exit_code == 0`, which
reintroduced a false positive: `is_merge` is a static text match, true even
when `&&` short-circuited before merge ever ran (create itself failing), so
that draft fired a false "PR created" reminder. `merge_ok` (the confirmed-
merge marker) is used instead because a completed merge is independent proof
create already succeeded, valid evidence even when the chain's aggregate
exit code is non-zero (the #275 worktree case, chained with a preceding
create).

The live _open_pr_for_cwd subprocess boundary is not exercised here (repo
convention: no subprocess mocks). The _build_messages cases below avoid ever
reaching that call by only combining is_push=True with a failing
create_push_ok or with is_create/is_merge also true — both conditions
short-circuit before _open_pr_for_cwd would be invoked.

dev-env#504 added a live `gh pr view` fallback (dev-env#489's fix, already
wired into post-pr-merge-project.py) for when gh's marker doesn't survive a
worktree merge's local-cleanup failure. `main()` resolves that live check
itself and passes the result into `_build_messages` as `live_confirmed` —
the function itself never shells out, so its existing direct-call tests
above are unaffected (they all omit the parameter, which defaults to None).
The three `live_confirmed` tests below exercise only the pure override
logic, not the live call.

dev-env#557 added `and not is_merge_help_only(command)` into that same
`main()`-inline `if (is_merge and not _is_successful_merge_call(output) and
should_confirm_via_gh(exit_code, output)):` condition that gates whether
`main()` even attempts the live `gh pr view` call — a `gh pr merge --help`
command can categorically never attempt a real merge, so it must never pay
for (or be misattributed by) that live confirmation. Since this condition is
inline in `main()` rather than its own function, the composition test below
directly evaluates the same boolean expression `main()` evaluates, pinning
that a `--help` command with no marker and a non-zero exit code — which
would otherwise satisfy every other clause — is excluded once
`is_merge_help_only` is added, while an equivalent non-help unresolved merge
still satisfies the full condition (the live check is still attempted for
that case, matching dev-env#504's existing behavior unchanged).

dev-env#646 (ADR-050 Amendment 18) added `_effective_create_repo`, the
`is_create` branch's own counterpart to `_effective_merge_repo`: an explicit
`--repo`/`-R` flag now overrides cwd for the `gh pr create` reminder too, the
same flag-first precedence Amendment 14/15 already established for `gh pr
merge`. Unlike the merge path, an unflagged create command still falls back
to plain cwd (no cd-chain-aware dir), matching this branch's pre-existing
behavior exactly.

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
_effective_merge_repo = pmr._effective_merge_repo
_effective_create_repo = pmr._effective_create_repo
_build_messages = pmr._build_messages

# read_command_output, effective_merge_dir, should_confirm_via_gh, and
# is_merge_help_only live in _hookio (a sibling). SCRIPT.parent already on
# sys.path, so import them directly.
from _hookio import (  # noqa: E402
    effective_merge_dir,
    is_absolute_path,
    is_merge_help_only,
    read_command_output,
    should_confirm_via_gh,
)


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


def test_push_with_cd_prefix_matched() -> str:
    assert is_git_push_command("cd /Git/engineering-journal && git push")
    return "cd <dir> && git push -> matched (_PUSH_RE's optional cd-prefix branch)"


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
# _is_successful_merge_call  (dev-env#485 — marker-only, no exit-code branch)
# ---------------------------------------------------------------------------

def test_merge_call_marker_present_fires() -> str:
    # The marker is what confirms a completed merge; the exit code is no
    # longer consulted at all — true whether it came from a clean exit
    # (canonical checkout) or a worktree's non-zero cleanup failure (#275).
    assert _is_successful_merge_call("Squashed and merged pull request #419")
    return "'Squashed and merged' marker present -> fires"


def test_merge_call_failed_no_marker() -> str:
    # A genuine merge failure (no success marker) must not fire.
    assert not _is_successful_merge_call("X Pull request #419 is not mergeable")
    return "no success marker -> no-op"


def test_merge_call_clean_exit_no_marker_does_not_fire() -> str:
    # dev-env#485 regression: `gh pr merge --help` (or a queued --auto) exits 0
    # but prints no success marker. The old exit_code==0 OR marker gate fired
    # on this shape; gating on the marker alone fixes it.
    assert not _is_successful_merge_call("some other output with no merge marker")
    return "no marker at all (e.g. --help output) -> does not fire (dev-env#485)"


# ---------------------------------------------------------------------------
# live_confirmed gate composition (dev-env#557)
#
# main()'s live-gh-pr-view-attempt condition is inline, not its own function:
#   is_merge and not _is_successful_merge_call(output)
#       and should_confirm_via_gh(exit_code, output)
#       and not is_merge_help_only(command)
# These tests evaluate that exact expression directly (mirroring what main()
# computes) to pin that adding `is_merge_help_only` excludes a --help command
# that would otherwise satisfy every other clause, while an equivalent
# non-help unresolved merge still satisfies the full condition unchanged.
# ---------------------------------------------------------------------------

def _live_confirm_attempted(command: str, output: str, exit_code: int) -> bool:
    """Re-derive main()'s inline live-confirmation-attempt condition."""
    is_merge = is_pr_merge_command(command)
    return (
        is_merge
        and not _is_successful_merge_call(output)
        and should_confirm_via_gh(exit_code, output)
        and not is_merge_help_only(command)
    )


def test_live_confirm_not_attempted_for_help_command() -> str:
    # gh pr merge --help: is_merge True, no marker, non-zero/-1 exit would
    # satisfy should_confirm_via_gh -- but is_merge_help_only excludes it.
    command = "gh pr merge --help"
    output = "FLAGS\n      --admin   Use administrator privileges to merge a pull request"
    assert not _live_confirm_attempted(command, output, -1)
    return "gh pr merge --help (no marker, exit -1) -> live confirmation NOT attempted (dev-env#557)"


def test_live_confirm_still_attempted_for_unresolved_real_merge() -> str:
    # The dev-env#489/#504 case this fallback exists for: a genuine merge with
    # a lost marker and non-zero exit must still attempt the live check.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    assert _live_confirm_attempted(command, output, 1)
    return "unresolved real merge (no marker, non-help, exit 1) -> live confirmation still attempted (unchanged)"


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
    # is_absolute_path (not os.path.isabs) so this "was it resolved?" check is
    # itself version-agnostic — os.path.isabs("\\base\\sub\\repo") is False on
    # 3.13, which broke this line independently of the fix (dev-env#732).
    assert is_absolute_path(out), f"relative target not resolved: {out!r}"
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
# effective_merge_dir  (dev-env#446 / ADR-067)
#
# The full test suite for effective_merge_dir lives in test_hookio.py; these
# cases focus on the merge-reminder context (reminder shows the resolved dir,
# not raw cwd, when a cd-chain is present).
# ---------------------------------------------------------------------------

def test_merge_dir_bare_merge_is_cwd() -> str:
    out = effective_merge_dir("gh pr merge --squash --delete-branch", "/session/cwd")
    assert out == "/session/cwd", f"bare merge should return cwd, got {out!r}"
    return "bare gh pr merge -> session cwd (reminder shows cwd, unchanged)"


def test_merge_dir_cd_chain_redirects_for_reminder() -> str:
    # `cd <dev-env> && gh pr merge` from a lifting-logbook session should report
    # /Git/dev-env in the reminder, not /Git/lifting-logbook.
    out = effective_merge_dir(
        "cd /Git/dev-env && gh pr merge --squash --delete-branch",
        "/Git/lifting-logbook",
    )
    assert out == "/Git/dev-env", f"expected /Git/dev-env, got {out!r}"
    return "cd <dev-env> && gh pr merge from lb cwd -> dev-env dir in reminder"


# ---------------------------------------------------------------------------
# _effective_merge_repo  (dev-env#470)
# ---------------------------------------------------------------------------

def test_merge_repo_explicit_flag_overrides_cwd() -> str:
    # The dev-env#470 repro: an explicit --repo run from an unrelated cwd with
    # no cd-chain must report the flag's repo, not cwd.
    out = _effective_merge_repo(
        "gh pr merge 110 --repo brownm09/engineering-journal --squash --delete-branch",
        "C:\\Users\\brown\\Git\\dev-env",
    )
    assert out == "brownm09/engineering-journal", f"got {out!r}"
    return "gh pr merge --repo other/repo from unrelated cwd -> that repo, not cwd"


def test_merge_repo_explicit_flag_overrides_cd_chain() -> str:
    # --repo is the highest-confidence signal (ADR-067 resolution order) — it
    # wins even when a cd-chain prefix is also present.
    out = _effective_merge_repo(
        "cd /Git/other-repo && gh pr merge 5 --repo brownm09/engineering-journal",
        "/Git/lifting-logbook",
    )
    assert out == "brownm09/engineering-journal", f"got {out!r}"
    return "cd <other-repo> && gh pr merge --repo X -> X, not the cd-chain dir"


def test_merge_repo_short_flag_form() -> str:
    # dev-env#616: gh's -R shorthand for --repo was not recognized -- a
    # `-R owner/repo` merge command fell through to effective_merge_dir's
    # cwd/cd-chain resolution instead of the flag's own explicit repo.
    out = _effective_merge_repo(
        "gh pr merge 611 -R brownm09/dev-env --squash",
        "/Git/lifting-logbook",
    )
    assert out == "brownm09/dev-env", f"got {out!r}"
    return "-R flag (gh's --repo shorthand) resolves identically to --repo (dev-env#616)"


def test_merge_repo_dash_r_mid_word_not_matched() -> str:
    # dev-env#626 / review finding on PR #623: the (?<!\S) lookbehind requires
    # -R to start a standalone token; a coincidental mid-word "-R" must fall
    # back to effective_merge_dir, not be mistaken for the flag.
    out = _effective_merge_repo(
        "gh pr merge 42 xx-R brownm09/dev-env --squash",
        "/Git/lifting-logbook",
    )
    assert out == "/Git/lifting-logbook", f"got {out!r}"
    return "mid-word '-R' not matched -> falls back to effective_merge_dir (dev-env#626)"


def test_merge_repo_dash_r_inside_quoted_subject_not_matched() -> str:
    # dev-env#626, ADR-050 Amendment 15: mask_quoted_spans blinds a --subject
    # value's quoted content before the repo-flag regex runs, so a
    # legitimately space-separated "-R other/repo" substring inside it can no
    # longer be mistaken for the flag -- falls back to effective_merge_dir.
    out = _effective_merge_repo(
        'gh pr merge 42 --subject "see -R other/repo for context"',
        "/Git/lifting-logbook",
    )
    assert out == "/Git/lifting-logbook", f"got {out!r}"
    return "quoted --subject decoy '-R other/repo' -> falls back to effective_merge_dir (dev-env#626)"


def test_merge_repo_flag_survives_alongside_quoted_decoy() -> str:
    # A real, unquoted --repo flag must still resolve correctly even when a
    # quoted --subject value elsewhere in the same command contains a decoy.
    out = _effective_merge_repo(
        'gh pr merge 42 --repo brownm09/dev-env --subject "see -R other/repo for context"',
        "/Git/lifting-logbook",
    )
    assert out == "brownm09/dev-env", f"got {out!r}"
    return "real --repo flag resolves correctly alongside a quoted decoy (dev-env#626)"


def test_merge_repo_no_flag_falls_back_to_effective_merge_dir() -> str:
    # No --repo flag -> delegate to effective_merge_dir unchanged (bare merge
    # returns cwd; a cd-chain prefix still redirects).
    bare = _effective_merge_repo("gh pr merge --squash --delete-branch", "/session/cwd")
    assert bare == "/session/cwd", f"got {bare!r}"
    chained = _effective_merge_repo(
        "cd /Git/dev-env && gh pr merge --squash --delete-branch", "/Git/lifting-logbook"
    )
    assert chained == "/Git/dev-env", f"got {chained!r}"
    return "no --repo flag -> falls back to effective_merge_dir (cwd / cd-chain)"


# ---------------------------------------------------------------------------
# _effective_merge_repo — REST merge fallback shape (dev-env#986, dev-env#991)
# ---------------------------------------------------------------------------

def test_merge_repo_rest_shape_uses_cwd_not_trailing_cd() -> str:
    # No `gh pr merge` token is present, so effective_merge_dir would search
    # the ENTIRE command for a cd-chain and wrongly treat a `cd` occurring
    # AFTER the REST call as governing it (ADR-050 Amendment 23's documented
    # gap). _effective_merge_repo must use cwd directly for this shape instead.
    out = _effective_merge_repo(
        "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash "
        "&& cd /Git/other-repo && npm test",
        "/session/cwd",
    )
    assert out == "/session/cwd", f"expected /session/cwd (not the trailing cd), got {out!r}"
    return "REST merge + trailing cd -> cwd, not the trailing cd's target (dev-env#986/#991)"


def test_merge_repo_rest_shape_placeholder_still_uses_cwd() -> str:
    # gh api's own {owner}/{repo} URL templating (unquoted) is a documented,
    # expected form of this command (ADR-050 Amendment 23) -- must resolve
    # the same as any other REST shape, not error or fall through differently.
    out = _effective_merge_repo(
        "gh api -X PUT repos/{owner}/{repo}/pulls/42/merge -f merge_method=squash",
        "/session/cwd",
    )
    assert out == "/session/cwd", f"got {out!r}"
    return "REST merge with unquoted {owner}/{repo} placeholder -> cwd (dev-env#986/#991)"


# ---------------------------------------------------------------------------
# _effective_create_repo  (dev-env#646, ADR-050 Amendment 18)
# ---------------------------------------------------------------------------

def test_create_repo_explicit_flag_overrides_cwd() -> str:
    # The dev-env#646 repro: `gh pr create --repo other/repo` run from an
    # unrelated cwd (e.g. a lifting-logbook session opening a dev-env PR) must
    # report the flag's repo, not cwd -- the is_create branch previously had
    # no repo-flag resolution at all and unconditionally reported cwd.
    out = _effective_create_repo(
        "gh pr create --repo brownm09/dev-env --title 'x' --head docs/foo",
        "C:\\Users\\brown\\Git\\lifting-logbook",
    )
    assert out == "brownm09/dev-env", f"got {out!r}"
    return "gh pr create --repo other/repo from unrelated cwd -> that repo, not cwd (dev-env#646)"


def test_create_repo_short_flag_form() -> str:
    # gh's -R shorthand for --repo must resolve identically for create, same
    # as it already does for merge (dev-env#616).
    out = _effective_create_repo(
        "gh pr create -R brownm09/dev-env --fill",
        "/Git/lifting-logbook",
    )
    assert out == "brownm09/dev-env", f"got {out!r}"
    return "-R flag (gh's --repo shorthand) resolves identically to --repo for create"


def test_create_repo_no_flag_falls_back_to_cwd() -> str:
    # No --repo flag -> cwd, unchanged from this branch's pre-existing
    # behavior (unlike the merge path, there is no cd-chain-aware dir to fall
    # back to here -- see _effective_create_repo's own docstring).
    out = _effective_create_repo("gh pr create --fill", "/session/cwd")
    assert out == "/session/cwd", f"got {out!r}"
    return "no --repo flag -> falls back to cwd (unchanged)"


def test_create_repo_flag_survives_alongside_quoted_decoy() -> str:
    # A real, unquoted --repo flag must still resolve correctly even when a
    # quoted --body value elsewhere in the same command contains a decoy --
    # mirrors the merge-side dev-env#626 regression coverage for this same
    # mask_quoted_spans-protected regex.
    out = _effective_create_repo(
        'gh pr create --repo brownm09/dev-env --body "see -R other/repo for context"',
        "/Git/lifting-logbook",
    )
    assert out == "brownm09/dev-env", f"got {out!r}"
    return "real --repo flag resolves correctly alongside a quoted decoy (dev-env#646)"


# ---------------------------------------------------------------------------
# Chained create+merge cross-contamination (dev-env#667, ADR-111)
#
# Pre-consolidation both _effective_*_repo searched the WHOLE masked command for
# the --repo flag, so whichever --repo appeared textually FIRST won for BOTH
# functions -- regardless of which statement it belonged to. Scoping each
# resolver to its own invocation's args (merge_args / create_args, via the
# shared _repo_target module) fixes the cross-contamination.
# ---------------------------------------------------------------------------

def test_chained_create_merge_no_cross_contamination() -> str:
    cmd = (
        "gh pr create --repo brownm09/repo-a --fill && "
        "gh pr merge 5 --repo brownm09/repo-b --squash --delete-branch"
    )
    assert _effective_create_repo(cmd, "/session/cwd") == "brownm09/repo-a", "create scoped"
    assert _effective_merge_repo(cmd, "/session/cwd") == "brownm09/repo-b", "merge scoped"
    return "chained create+merge with different --repo values resolve independently (dev-env#667)"


def test_chained_create_merge_reversed_order() -> str:
    # Reversing statement order reverses which flag is textually first; each
    # resolver must still pick its OWN statement's flag, proving statement-scoping
    # rather than position-in-string.
    cmd = (
        "gh pr merge 5 --repo brownm09/repo-b --squash && "
        "gh pr create --repo brownm09/repo-a --fill"
    )
    assert _effective_create_repo(cmd, "/session/cwd") == "brownm09/repo-a", "create scoped"
    assert _effective_merge_repo(cmd, "/session/cwd") == "brownm09/repo-b", "merge scoped"
    return "chained order reversed -> each resolver still picks its own statement's flag (dev-env#667)"


# ---------------------------------------------------------------------------
# _build_messages  (dev-env#494 — chained create+merge must not suppress an
# independently-successful create when the merge sub-check is incomplete)
# ---------------------------------------------------------------------------

def test_build_messages_chained_create_and_queued_auto_still_creates() -> str:
    # gh pr create --fill && gh pr merge --auto, where --auto only queues the
    # merge (no success marker yet, exit 0) -- the create half still
    # succeeded and must still get its reminder. This is the dev-env#494 repro.
    messages = _build_messages(
        command="gh pr create --fill && gh pr merge --auto",
        cwd="/session/cwd",
        exit_code=0,
        output="https://github.com/brownm09/dev-env/pull/500\n",
        is_create=True,
        is_merge=True,
        is_push=False,
    )
    assert len(messages) == 1, f"expected exactly the create message, got {messages!r}"
    assert "gh pr create detected" in messages[0]
    return "chained create + queued --auto (no marker) -> create reminder still fires (dev-env#494)"


def test_build_messages_chained_create_and_help_shaped_merge_still_creates() -> str:
    # The --help-shaped case from the issue: no marker, exit 0.
    messages = _build_messages(
        command="gh pr create --fill && gh pr merge --help",
        cwd="/session/cwd",
        exit_code=0,
        output="Merge a pull request\n\nUSAGE\n  gh pr merge [<number> | <url> | <branch>] [flags]",
        is_create=True,
        is_merge=True,
        is_push=False,
    )
    assert len(messages) == 1, f"expected exactly the create message, got {messages!r}"
    assert "gh pr create detected" in messages[0]
    return "chained create + --help-shaped merge (no marker) -> create reminder still fires (dev-env#494)"


def test_build_messages_chained_create_and_successful_merge_both_fire() -> str:
    messages = _build_messages(
        command="gh pr create --fill && gh pr merge --squash --delete-branch",
        cwd="/session/cwd",
        exit_code=0,
        output=(
            "https://github.com/brownm09/dev-env/pull/500\n"
            "Squashed and merged pull request #500"
        ),
        is_create=True,
        is_merge=True,
        is_push=False,
    )
    assert len(messages) == 2, f"expected both messages, got {len(messages)}: {messages!r}"
    assert any("gh pr create detected" in m for m in messages)
    assert any("gh pr merge detected" in m for m in messages)
    return "chained create + successful merge -> both reminders fire"


def test_build_messages_single_create_failure_no_message() -> str:
    messages = _build_messages(
        command="gh pr create --fill",
        cwd="/session/cwd",
        exit_code=1,
        output="",
        is_create=True,
        is_merge=False,
        is_push=False,
    )
    assert messages == [], f"failed single create must not fire, got {messages!r}"
    return "single failed gh pr create (exit != 0) -> no message (unchanged)"


def test_build_messages_single_create_success_fires() -> str:
    messages = _build_messages(
        command="gh pr create --fill",
        cwd="/session/cwd",
        exit_code=0,
        output="https://github.com/brownm09/dev-env/pull/501\n",
        is_create=True,
        is_merge=False,
        is_push=False,
    )
    assert len(messages) == 1
    assert "gh pr create detected" in messages[0]
    return "single successful gh pr create -> create reminder fires (unchanged)"


def test_build_messages_single_merge_no_marker_no_message() -> str:
    messages = _build_messages(
        command="gh pr merge --squash",
        cwd="/session/cwd",
        exit_code=0,
        output="X Pull request #1 is not mergeable",
        is_create=False,
        is_merge=True,
        is_push=False,
    )
    assert messages == [], f"merge with no marker must not fire, got {messages!r}"
    return "single gh pr merge, no marker -> no message (unchanged)"


def test_build_messages_single_merge_worktree_nonzero_exit_still_fires() -> str:
    # dev-env#275: a worktree merge exits non-zero on local cleanup despite a
    # real remote merge. Marker present -> must still fire regardless of
    # exit_code (unchanged from before this fix).
    messages = _build_messages(
        command="gh pr merge --squash --delete-branch",
        cwd="/session/cwd",
        exit_code=1,
        output="Squashed and merged pull request #419",
        is_create=False,
        is_merge=True,
        is_push=False,
    )
    assert len(messages) == 1
    assert "gh pr merge detected" in messages[0]
    return "single gh pr merge, marker present, nonzero exit (worktree #275) -> fires (unchanged)"


def test_build_messages_single_push_failure_no_message() -> str:
    # exit_ok is False here, so the `is_push and ... and exit_ok` condition
    # short-circuits before _open_pr_for_cwd is ever called.
    messages = _build_messages(
        command="git push",
        cwd="/session/cwd",
        exit_code=1,
        output="",
        is_create=False,
        is_merge=False,
        is_push=True,
    )
    assert messages == [], f"failed single push must not fire, got {messages!r}"
    return "single failed git push (exit != 0) -> no message, no subprocess call (unchanged)"


def test_build_messages_create_fails_merge_never_ran_no_message() -> str:
    # gh pr create --fill && gh pr merge --auto, where create ITSELF fails.
    # bash's && short-circuits: gh pr merge never runs, so is_merge is still
    # True (static text match) but merge_ok is False (no marker) and the
    # overall exit_code correctly reflects create's own failure. Must not
    # fire a false "PR created" reminder for a PR that doesn't exist -- this
    # is the regression an earlier `is_merge or exit_code == 0` draft of the
    # #494 fix introduced.
    messages = _build_messages(
        command="gh pr create --fill && gh pr merge --auto",
        cwd="/session/cwd",
        exit_code=1,
        output="pull request create failed: no commits between main and branch",
        is_create=True,
        is_merge=True,
        is_push=False,
    )
    assert messages == [], f"create failure (merge never ran) must not fire, got {messages!r}"
    return "chained create fails, merge never ran (no marker, exit != 0) -> no message"


def test_build_messages_chained_create_and_worktree_merge_nonzero_exit_both_fire() -> str:
    # dev-env#275 chained with a preceding create: the merge completes
    # remotely (marker present) but the worktree's local cleanup exits
    # non-zero, dragging the chain's aggregate exit_code negative. A
    # confirmed merge is independent proof create already succeeded, so both
    # messages must still fire -- unlike a plain `exit_code == 0` gate, which
    # would incorrectly suppress the create message here.
    messages = _build_messages(
        command="gh pr create --fill && gh pr merge --squash --delete-branch",
        cwd="/session/cwd",
        exit_code=1,
        output=(
            "https://github.com/brownm09/dev-env/pull/500\n"
            "Squashed and merged pull request #500"
        ),
        is_create=True,
        is_merge=True,
        is_push=False,
    )
    assert len(messages) == 2, f"expected both messages, got {len(messages)}: {messages!r}"
    assert any("gh pr create detected" in m for m in messages)
    assert any("gh pr merge detected" in m for m in messages)
    return "chained create + worktree-merge success (marker, nonzero exit) -> both fire (#275)"


def test_build_messages_push_suppressed_when_also_create() -> str:
    # is_push is only actionable when the command is NOT also a create/merge
    # (unchanged from before this fix) -- `not (is_create or is_merge)` is
    # False here, so this also never reaches _open_pr_for_cwd.
    messages = _build_messages(
        command="gh pr create --fill && git push",
        cwd="/session/cwd",
        exit_code=0,
        output="https://github.com/brownm09/dev-env/pull/500\n",
        is_create=True,
        is_merge=False,
        is_push=True,
    )
    assert len(messages) == 1
    assert "gh pr create detected" in messages[0]
    assert not any("git push detected" in m for m in messages)
    return "is_push suppressed when is_create also true -> only create message (unchanged)"


# ---------------------------------------------------------------------------
# _build_messages: live_confirmed override (dev-env#504 — main() resolves the
# live gh-pr-view fallback itself and passes the result in; this function
# never shells out, so these tests stay offline like every other case above.
# ---------------------------------------------------------------------------

def test_build_messages_live_confirmed_true_overrides_marker_less_merge() -> str:
    # Simulates main()'s live confirmation succeeding after the marker was
    # lost on the dev-env#489 worktree-cleanup failure shape — merge_ok must
    # be forced True even though the marker-based check alone says False.
    messages = _build_messages(
        command="gh pr merge --squash --delete-branch",
        cwd="/session/cwd",
        exit_code=1,
        output="failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'",
        is_create=False,
        is_merge=True,
        is_push=False,
        live_confirmed=True,
    )
    assert len(messages) == 1
    assert "gh pr merge detected" in messages[0]
    return "live_confirmed=True overrides marker-less merge_ok -> merge reminder fires (dev-env#504)"


def test_build_messages_live_confirmed_false_stays_unfired() -> str:
    # main() attempted the live check and it came back negative (genuinely
    # not merged, or gh pr view itself failed) -> no message, same as if the
    # marker check alone had run.
    messages = _build_messages(
        command="gh pr merge --squash --delete-branch",
        cwd="/session/cwd",
        exit_code=1,
        output="failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'",
        is_create=False,
        is_merge=True,
        is_push=False,
        live_confirmed=False,
    )
    assert messages == [], f"live_confirmed=False must not fire, got {messages!r}"
    return "live_confirmed=False -> no message (live check attempted, not confirmed)"


def test_build_messages_live_confirmed_default_none_unchanged() -> str:
    # Omitting the parameter entirely (every pre-existing test call above, and
    # main()'s own "never attempted" case) must behave exactly as before this
    # change -- marker-only.
    messages = _build_messages(
        command="gh pr merge --squash",
        cwd="/session/cwd",
        exit_code=1,
        output="X Pull request #1 is not mergeable",
        is_create=False,
        is_merge=True,
        is_push=False,
    )
    assert messages == [], "default live_confirmed=None must not change prior marker-only behavior"
    return "live_confirmed omitted -> unchanged marker-only behavior"


# ---------------------------------------------------------------------------
# build_messages — REST merge fallback shape (dev-env#986, dev-env#991)
# ---------------------------------------------------------------------------

def test_build_messages_rest_merge_with_marker_fires() -> str:
    # gh pr merge itself is unavailable during a GraphQL outage -- is_merge is
    # False for this command shape (is_pr_merge_command never matches "gh
    # api"), so merge_ok must come entirely from the REST OR-branch.
    messages = _build_messages(
        command="gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash",
        cwd="/session/cwd",
        exit_code=0,
        output='{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}',
        is_create=False,
        is_merge=False,
        is_push=False,
    )
    assert len(messages) == 1, f"expected one merge reminder, got {messages!r}"
    assert "gh pr merge detected" in messages[0]
    return "REST merge fallback + \"merged\":true -> fires (dev-env#986/#991)"


def test_build_messages_rest_merge_without_marker_no_message() -> str:
    messages = _build_messages(
        command="gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash",
        cwd="/session/cwd",
        exit_code=0,
        output='{"message":"Merge already in progress"}',
        is_create=False,
        is_merge=False,
        is_push=False,
    )
    assert messages == [], f"REST call without \"merged\":true must not fire, got {messages!r}"
    return "REST merge call without \"merged\":true -> no message"


def test_build_messages_rest_merge_chained_push_no_duplicate_reminder() -> str:
    # A REST merge chained with a push in the same command is both a merge
    # (fires via the REST OR-branch) and, textually, a push -- without the
    # is_rest_merge_command(command) addition to the push-suppression check,
    # this would ALSO fire a duplicate push reminder for the same event
    # (mirrors the pre-existing is_create/is_merge suppression).
    messages = _build_messages(
        command=(
            "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash "
            "&& git push"
        ),
        cwd="/session/cwd",
        exit_code=0,
        output='{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}',
        is_create=False,
        is_merge=False,
        is_push=True,
    )
    assert len(messages) == 1, f"expected only the merge reminder, got {messages!r}"
    assert "gh pr merge detected" in messages[0]
    return "REST merge chained with push -> only the merge reminder fires, no duplicate push reminder (dev-env#986/#991)"


def test_build_messages_rest_merge_get_method_not_matched() -> str:
    # gh api's default verb is GET; GitHub's own documented read-only "check
    # if a pull request has been merged" endpoint shares the identical path
    # shape. Must not be mistaken for a completed merge even if a chained
    # command's output happens to carry "merged":true elsewhere.
    messages = _build_messages(
        command="gh api repos/brownm09/dev-env/pulls/42/merge",
        cwd="/session/cwd",
        exit_code=0,
        output='{"merged":true}',
        is_create=False,
        is_merge=False,
        is_push=False,
    )
    assert messages == [], f"GET (no PUT) must not fire, got {messages!r}"
    return "gh api GET (read-only merge-check) -> no message (dev-env#986/#991)"


def test_exit_code_coercion_pins_accepted_tradeoff() -> str:
    """ADR-050 Amendment 28 post-review finding 6, pinned as an executable
    proof rather than left as prose alone: this file's own
    `exit_code = read_exit_code(data, default=0)` call coerces a
    present-but-non-int-coercible exitCode (e.g. `exitCode: null`) to `0` --
    indistinguishable, downstream, from a GENUINELY successful exitCode.
    Pre-fix, `data.get("tool_response", {}).get("exitCode", 0)` returned the
    raw `None` unchanged for this exact shape. The real consequence here:
    `should_confirm_via_gh(exit_code, output)` (the dev-env#489/#504 live-gh
    confirmation fallback) treats a malformed-but-present exitCode exactly
    like a confirmed-successful one, so that fallback no longer fires for
    this narrow case -- accepted, not fixed, per the same reasoning as
    post-tool-use.py's identical pin in test_post_tool_use.py.
    """
    read_exit_code = pmr.read_exit_code
    malformed = {"tool_response": {"exitCode": None, "stdout": "", "stderr": ""}}
    genuinely_successful = {"tool_response": {"exitCode": 0, "stdout": "", "stderr": ""}}
    assert read_exit_code(malformed, default=0) == 0, "malformed exitCode must coerce to the default"
    assert read_exit_code(genuinely_successful, default=0) == 0, "a real exitCode:0 payload, for comparison"
    assert read_exit_code(malformed, default=0) == read_exit_code(genuinely_successful, default=0)
    # The real downstream consequence, pinned directly: should_confirm_via_gh
    # (the dev-env#489/#504 live-gh confirmation fallback) sees the SAME
    # exit_code=0 either way, so it does NOT fire for a malformed-but-present
    # exitCode with no marker in output -- exactly as if the command had
    # genuinely succeeded. A non-zero default (the four sibling files using
    # default=-1) would fire it instead; this pin is what makes that
    # divergence concrete rather than an abstract claim.
    coerced_exit_code = read_exit_code(malformed, default=0)
    assert pmr.should_confirm_via_gh(coerced_exit_code, "") is False, (
        "a malformed exitCode coerced to 0 must NOT trigger the live-confirmation fallback"
    )
    return "exitCode:null coerces to 0 (default=0), indistinguishable from a genuine exitCode:0 -- accepted trade-off, pinned (ADR-050 Amendment 28)"


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
        ("cd ... && git push -> match", test_push_with_cd_prefix_matched),
        ("git push in $() -> no match", test_push_inside_subshell_not_matched),
        ("shard step: URL in output", test_shard_step_with_url_in_output),
        ("shard step: no URL -> generic hint", test_shard_step_no_url_in_output),
        ("shard step: trailing dot excluded by regex", test_shard_step_url_trailing_dot_excluded_by_regex),
        ("shard step: issue URL not matched", test_shard_step_merge_url_not_matched),
        ("shard step: URL found via stdout field", test_shard_step_via_stdout_field),
        ("shard step: legacy .output fallback", test_shard_step_legacy_output_field_empty_gives_fallback),
        ("merge call: marker present fires", test_merge_call_marker_present_fires),
        ("merge call: no marker -> no-op", test_merge_call_failed_no_marker),
        ("merge call: --help-shaped (no marker) -> no-op (dev-env#485)", test_merge_call_clean_exit_no_marker_does_not_fire),
        ("live_confirm: not attempted for --help command (dev-env#557)", test_live_confirm_not_attempted_for_help_command),
        ("live_confirm: still attempted for unresolved real merge", test_live_confirm_still_attempted_for_unresolved_real_merge),
        ("push dir: bare push -> cwd", test_push_dir_bare_push_is_cwd),
        ("push dir: cd <repo> && push -> that repo", test_push_dir_cd_chain_redirects),
        ("push dir: cd <ej> && ... && push -> ej dir", test_push_dir_cd_chain_multi_segment),
        ("push dir: quoted cd path", test_push_dir_quoted_path),
        ("push dir: relative path resolved vs cwd", test_push_dir_relative_resolved_against_cwd),
        ("push dir: semicolon chain", test_push_dir_semicolon_chain),
        ("push dir: cd after push ignored", test_push_dir_cd_after_push_ignored),
        ("merge dir: bare merge -> cwd (reminder unchanged)", test_merge_dir_bare_merge_is_cwd),
        ("merge dir: cd <dev-env> && merge from lb cwd -> dev-env", test_merge_dir_cd_chain_redirects_for_reminder),
        ("merge repo: --repo flag overrides cwd", test_merge_repo_explicit_flag_overrides_cwd),
        ("merge repo: --repo flag overrides cd-chain", test_merge_repo_explicit_flag_overrides_cd_chain),
        ("merge repo: -R shorthand resolves same as --repo (dev-env#616)", test_merge_repo_short_flag_form),
        ("merge repo: mid-word '-R' not matched (dev-env#626)", test_merge_repo_dash_r_mid_word_not_matched),
        ("merge repo: '-R' inside quoted --subject not matched (dev-env#626)", test_merge_repo_dash_r_inside_quoted_subject_not_matched),
        ("merge repo: --repo flag survives alongside quoted decoy (dev-env#626)", test_merge_repo_flag_survives_alongside_quoted_decoy),
        ("merge repo: no flag -> falls back to effective_merge_dir", test_merge_repo_no_flag_falls_back_to_effective_merge_dir),
        ("merge repo: REST shape + trailing cd -> cwd, not trailing cd (dev-env#986/#991)", test_merge_repo_rest_shape_uses_cwd_not_trailing_cd),
        ("merge repo: REST shape with {owner}/{repo} placeholder -> cwd (dev-env#986/#991)", test_merge_repo_rest_shape_placeholder_still_uses_cwd),
        ("create repo: --repo flag overrides cwd (dev-env#646)", test_create_repo_explicit_flag_overrides_cwd),
        ("create repo: -R shorthand resolves same as --repo", test_create_repo_short_flag_form),
        ("create repo: no flag -> falls back to cwd", test_create_repo_no_flag_falls_back_to_cwd),
        ("create repo: --repo flag survives alongside quoted decoy", test_create_repo_flag_survives_alongside_quoted_decoy),
        ("chained create+merge: no cross-contamination (dev-env#667)", test_chained_create_merge_no_cross_contamination),
        ("chained create+merge: reversed order still statement-scoped (dev-env#667)", test_chained_create_merge_reversed_order),
        ("build_messages: chained create + queued --auto -> create still fires (dev-env#494)", test_build_messages_chained_create_and_queued_auto_still_creates),
        ("build_messages: chained create + --help merge -> create still fires (dev-env#494)", test_build_messages_chained_create_and_help_shaped_merge_still_creates),
        ("build_messages: chained create + successful merge -> both fire", test_build_messages_chained_create_and_successful_merge_both_fire),
        ("build_messages: single failed create -> no message", test_build_messages_single_create_failure_no_message),
        ("build_messages: single successful create -> fires", test_build_messages_single_create_success_fires),
        ("build_messages: single merge, no marker -> no message", test_build_messages_single_merge_no_marker_no_message),
        ("build_messages: single merge, worktree nonzero exit -> fires (#275)", test_build_messages_single_merge_worktree_nonzero_exit_still_fires),
        ("build_messages: single failed push -> no message", test_build_messages_single_push_failure_no_message),
        ("build_messages: create fails, merge never ran -> no message", test_build_messages_create_fails_merge_never_ran_no_message),
        ("build_messages: chained create + worktree-merge success -> both fire (#275)", test_build_messages_chained_create_and_worktree_merge_nonzero_exit_both_fire),
        ("build_messages: push suppressed when also create", test_build_messages_push_suppressed_when_also_create),
        ("build_messages: live_confirmed=True overrides marker-less merge (dev-env#504)", test_build_messages_live_confirmed_true_overrides_marker_less_merge),
        ("build_messages: live_confirmed=False stays unfired", test_build_messages_live_confirmed_false_stays_unfired),
        ("build_messages: live_confirmed omitted -> unchanged", test_build_messages_live_confirmed_default_none_unchanged),
        ("build_messages: REST merge fallback + \"merged\":true -> fires (dev-env#986/#991)", test_build_messages_rest_merge_with_marker_fires),
        ("build_messages: REST merge fallback without marker -> no message", test_build_messages_rest_merge_without_marker_no_message),
        ("build_messages: REST merge chained with push -> no duplicate reminder (dev-env#986/#991)", test_build_messages_rest_merge_chained_push_no_duplicate_reminder),
        ("build_messages: REST merge GET (no PUT) -> no message (dev-env#986/#991)", test_build_messages_rest_merge_get_method_not_matched),
        ("exit_code coercion pins the accepted default=0 trade-off (ADR-050 Amendment 28)", test_exit_code_coercion_pins_accepted_tradeoff),
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
