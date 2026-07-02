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
_build_messages = pmr._build_messages

# read_command_output and effective_merge_dir live in _hookio (a sibling).
# SCRIPT.parent already on sys.path, so import them directly.
from _hookio import effective_merge_dir, read_command_output  # noqa: E402


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
        ("merge call: marker present fires", test_merge_call_marker_present_fires),
        ("merge call: no marker -> no-op", test_merge_call_failed_no_marker),
        ("merge call: --help-shaped (no marker) -> no-op (dev-env#485)", test_merge_call_clean_exit_no_marker_does_not_fire),
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
        ("merge repo: no flag -> falls back to effective_merge_dir", test_merge_repo_no_flag_falls_back_to_effective_merge_dir),
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
