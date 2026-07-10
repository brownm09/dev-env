#!/usr/bin/env python3
"""Unit + behavioral tests for stop-tile-enumeration-gate.py (ADR-088).

`stop-tile-enumeration-gate.py` is a Stop hook that scans the just-ended
session transcript and blocks the stop (exit 2) when a PR reached MERGED state
this session by ANY path (a `gh pr merge` you ran, a pure `gh api` merge, or
auto-merge observed via `gh pr view`) but NO tile-enumeration artifact was
recorded — the state-keyed enforcement analog of pre-merge-findings-gate, and
the auto-merge-aware complement to the command-keyed post-merge-tile-checkpoint.py
(ADR-060).

Two layers, mirroring this repo's hook-test convention:

  * Pure-helper tests exercise the detection/decision core offline (no stdin,
    network, gh, or disk): merged-state detection (direct marker / `gh api`
    merge / auto-merge correlated with an in-session action), the non-matches
    that must NOT count (an unrelated old merged PR merely inspected, a queued
    `--auto`, `gh pr merge --help`, a `gh pr merge` mentioned only inside a
    heredoc body — the dev-env#499 anchoring class), enumeration detection
    (spawn_task / prescribed text) INCLUDING the bare-"no follow-ups" rejection
    that is the lifting-logbook#700 skip, the "skip tiles" user override (and
    that a tool_result merely containing the phrase does NOT waive), the
    `evaluate()` composition, iter_bash_calls id-pairing, and the reminder's
    cp1252-encodability.

  * A behavioral layer drives the real hook end-to-end over stdin via subprocess
    against a synthetic transcript, with HOME/USERPROFILE pointed at a temp dir
    so the per-trigger sentinels are isolated from the real ~/.claude/scratch.
    It pins: merged-no-enum -> exit 2 with the reason on stderr and empty stdout
    (Claude Code shows a Stop hook's stderr on exit 2, not stdout); merged+enum
    and no-merge -> exit 0; the stop_hook_active loop-guard -> exit 0; that a
    trigger's own sentinel suppresses a second fire of THAT trigger in the same
    session; and (ADR-097, dev-env#677) that one trigger's sentinel does NOT
    suppress a sibling trigger whose condition arises later in the same
    session, verified both behaviorally and by directly inspecting the
    per-trigger sentinel files on disk.

main()'s own stdin read / sentinel-path plumbing beyond the end-to-end runs is
not separately unit-tested (pure-helper convention).

Usage:
    py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "stop-tile-enumeration-gate.py"

# The script imports _hookutil / _hookio (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("stop_tile_enumeration_gate", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)  # safe: main() is guarded by __main__


# --- transcript-record builders ------------------------------------------------

def _asst_bash(tid, command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": tid, "input": {"command": command}}]}}


def _tool_result(tid, output):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": output}]}}


def _asst_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _asst_spawn(tid="s1"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__ccd_session__spawn_task", "id": tid,
         "input": {"title": "Follow-up"}}]}}


def _asst_spawn_other_namespace(tid="s1"):
    # A differently-namespaced spawn_task tool_use (review of PR #674): the
    # real detector, _SPAWN_TASK_RE, is a deliberately bare/namespace-agnostic
    # "spawn_task" match ("Bare verb so any namespacing hits"), so a session
    # under a hypothetical renamed/rehosted MCP server must still be caught.
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__other_namespace__spawn_task", "id": tid,
         "input": {"title": "Follow-up"}}]}}


def _user_str(text):
    return {"type": "user", "message": {"content": text}}


def _user_text_item(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


# A minimal "PR #599 merged, nothing enumerated" session.
_MERGED_NO_ENUM = [
    _asst_bash("t1", "gh pr merge 599 --squash --delete-branch"),
    _tool_result("t1", "Squashed and merged pull request #599 (State-keyed gate)"),
    _asst_text("Merged PR #599 and moved the board item to Done."),
]


# ---------------------------------------------------------------------------
# merged-state detection
# ---------------------------------------------------------------------------

def test_direct_merge_marker_detected():
    calls = [("gh pr merge 599 --squash --delete-branch",
              "Squashed and merged pull request #599 (Title)")]
    assert gate.session_merged_prs(calls) == {599}
    return "gh pr merge + success marker -> merged {599}"


def test_gh_api_merge_detected():
    calls = [("gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash",
              '{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}')]
    assert gate.session_merged_prs(calls) == {42}
    return "gh api .../pulls/42/merge + \"merged\":true -> merged {42}"


def test_auto_merge_correlated_detected():
    # Enqueue --auto (no marker), then observe MERGED via gh pr view -> auto-merge.
    calls = [
        ("gh pr merge 700 --auto --squash",
         "! Pull request #700 will be automatically merged when all requirements are met"),
        ("gh pr view 700 --json state,number",
         '{"number":700,"state":"MERGED"}'),
    ]
    assert gate.session_merged_prs(calls) == {700}
    return "auto-merge: --auto enqueue + later gh pr view MERGED -> merged {700}"


def test_observed_merged_not_acted_on_ignored():
    # Inspecting an unrelated old merged PR (never created/merged this session)
    # must NOT be counted as an in-session merge -> no false fire.
    calls = [("gh pr view 123 --json state,number",
              '{"number":123,"state":"MERGED"}')]
    assert gate.session_merged_prs(calls) == set()
    return "gh pr view MERGED for a PR not acted on this session -> not merged"


def test_auto_flag_enqueue_alone_not_merged():
    # A queued --auto with no later MERGED confirmation is not a completed merge.
    calls = [("gh pr merge 700 --auto --squash",
              "Pull request #700 will be automatically merged when all requirements are met")]
    assert gate.session_merged_prs(calls) == set()
    return "gh pr merge --auto enqueue alone (no MERGED confirmation) -> not merged"


def test_merge_help_not_merged():
    calls = [("gh pr merge --help",
              "FLAGS\n      --admin   Use administrator privileges to merge a pull request")]
    assert gate.session_merged_prs(calls) == set()
    return "gh pr merge --help (no marker, no PR) -> not merged (dev-env#485 shape)"


def test_merge_text_in_heredoc_not_matched():
    # 'gh pr merge' inside a heredoc body is not a top-level invocation; paired
    # with a real marker to isolate the command-shape anchoring (dev-env#499).
    command = "git commit -F - <<'EOF'\ngh pr merge 5 --squash\nEOF"
    calls = [(command, "Squashed and merged pull request #5")]
    assert gate.session_merged_prs(calls) == set()
    return "'gh pr merge' in a heredoc body + real marker -> not merged (anchored)"


def test_merge_text_in_subshell_not_matched():
    command = "echo $(gh pr merge 5 --squash)"
    calls = [(command, "Squashed and merged pull request #5")]
    assert gate.session_merged_prs(calls) == set()
    return "'gh pr merge' inside a $() subshell + real marker -> not merged (anchored)"


def test_pr_url_positional_merge_detected():
    calls = [("gh pr merge https://github.com/brownm09/dev-env/pull/88 --squash",
              "Squashed and merged pull request #88")]
    assert gate.session_merged_prs(calls) == {88}
    return "gh pr merge <pull-URL> + marker -> merged {88}"


# ---------------------------------------------------------------------------
# enumeration detection
# ---------------------------------------------------------------------------

def test_enum_spawn_task():
    assert gate.enumeration_recorded([_asst_spawn()])
    return "a spawn_task tool_use -> enumeration recorded"


def test_enum_followups_considered_text():
    assert gate.enumeration_recorded(
        [_asst_text("Follow-ups considered: the idle worktree -> tiled (task_ab12).")])
    return "'Follow-ups considered: ...' text -> enumeration recorded"


def test_enum_not_tiled_text():
    assert gate.enumeration_recorded(
        [_asst_text("Considered the flaky test -> not tiled, because I filed #12 for it.")])
    return "'-> not tiled, because ...' text -> enumeration recorded"


def test_enum_arrow_tiled_ascii_and_unicode():
    assert gate.enumeration_recorded([_asst_text("stale doc -> tiled (task_9)")])
    assert gate.enumeration_recorded([_asst_text("stale doc → tiled (task_9)")])
    return "both '-> tiled' and 'U+2192 tiled' forms -> enumeration recorded"


def test_bare_no_followups_not_enumeration():
    # THE #700 skip: a bare assertion with no scan. Must NOT satisfy the gate.
    assert not gate.enumeration_recorded(
        [_asst_text("The finalization work surfaced no new follow-ups.")])
    return "bare 'no follow-ups' assertion -> NOT enumeration (lifting-logbook#700)"


def test_no_enumeration_plain_summary():
    assert not gate.enumeration_recorded([_asst_text("Merged and moved the board item to Done.")])
    return "a plain summary with no enumeration markers -> NOT enumeration"


# ---------------------------------------------------------------------------
# skip override
# ---------------------------------------------------------------------------

def test_skip_override_user_string():
    assert gate.skip_override([_user_str("go ahead and skip tiles for this one")])
    return "user string 'skip tiles' -> override"


def test_skip_override_user_text_item():
    assert gate.skip_override([_user_text_item("no tiles needed here")])
    return "user text-item 'no tiles' -> override"


def test_skip_override_dont_spawn():
    assert gate.skip_override([_user_str("don't spawn tiles please")])
    return "user 'don't spawn tiles' -> override"


def test_skip_override_toolresult_not_counted():
    # 'skip tiles' appearing in tool_result OUTPUT (not user-typed text) must not waive.
    assert not gate.skip_override([_tool_result("t1", "log line mentioning skip tiles verbatim")])
    return "'skip tiles' inside a tool_result -> NOT an override (only user text counts)"


def test_skip_override_absent():
    assert not gate.skip_override([_user_str("please merge and clean up")])
    return "no skip phrase in user text -> no override"


# ---------------------------------------------------------------------------
# evaluate() composition
# ---------------------------------------------------------------------------

def test_evaluate_merged_no_enum_fires():
    fire, resolved = gate.evaluate(_MERGED_NO_ENUM)
    assert fire == 599 and resolved is False
    return "merged + no enumeration + no skip -> (599, resolved=False) [FIRE]"


def test_evaluate_merged_with_enum_resolved():
    records = _MERGED_NO_ENUM + [
        _asst_text("Follow-ups considered: worktree cleanup -> not tiled, because in-scope.")]
    fire, resolved = gate.evaluate(records)
    assert fire is None and resolved is True
    return "merged + enumeration -> (None, resolved=True)"


def test_evaluate_merged_with_skip_resolved():
    records = _MERGED_NO_ENUM + [_user_str("skip tiles")]
    fire, resolved = gate.evaluate(records)
    assert fire is None and resolved is True
    return "merged + 'skip tiles' override -> (None, resolved=True)"


def test_evaluate_no_merge_noop():
    records = [_asst_bash("t1", "npm test"), _tool_result("t1", "All tests passed")]
    fire, resolved = gate.evaluate(records)
    assert fire is None and resolved is False
    return "no merge this session -> (None, resolved=False) [stay unresolved]"


def test_evaluate_picks_lowest_pr_deterministically():
    records = [
        _asst_bash("t1", "gh pr merge 42 --squash"),
        _tool_result("t1", "Squashed and merged pull request #42"),
        _asst_bash("t2", "gh pr merge 7 --squash"),
        _tool_result("t2", "Squashed and merged pull request #7"),
        _asst_text("both merged."),
    ]
    fire, _ = gate.evaluate(records)
    assert fire == 7
    return "two merges, no enum -> fires on the lowest PR number deterministically"


# ---------------------------------------------------------------------------
# iter_bash_calls id-pairing + reminder encodability
# ---------------------------------------------------------------------------

def test_iter_bash_calls_pairs_by_id():
    # Two parallel Bash calls; results arrive out of order. Pairing is by
    # tool_use_id, so neither crosses.
    records = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "a", "input": {"command": "CMD_A"}},
            {"type": "tool_use", "name": "Bash", "id": "b", "input": {"command": "CMD_B"}}]}},
        _tool_result("b", "OUT_B"),
        _tool_result("a", "OUT_A"),
    ]
    calls = gate.iter_bash_calls(records)
    assert ("CMD_A", "OUT_A") in calls
    assert ("CMD_B", "OUT_B") in calls
    return "iter_bash_calls pairs command<->output by tool_use_id (parallel-safe)"


def test_reminder_is_cp1252_encodable():
    msg = gate.format_reminder(599)
    assert msg.isascii(), "reminder must be ASCII (Claude Code pipes hook output as cp1252)"
    msg.encode("cp1252")  # must not raise
    assert "#599" in msg and "skip tiles" in msg
    return "format_reminder is ASCII/cp1252-encodable and names the PR"


# ---------------------------------------------------------------------------
# review-fix regressions (PR #604)
# ---------------------------------------------------------------------------

def test_a1_observed_prefers_json_number():
    # A1: the observed-MERGED PR number comes from the authoritative JSON
    # "number", so a `gh pr view` with no positional arg (infers from branch)
    # still resolves the PR — no reliance on a positional that could be a flag.
    calls = [
        ("gh pr merge 700 --auto --squash",
         "Pull request #700 will be automatically merged when all requirements are met"),
        ("gh pr view --json state,number", '{"number":700,"state":"MERGED"}'),
    ]
    assert gate.session_merged_prs(calls) == {700}
    return "observed MERGED with no positional arg -> number from JSON \"number\" (A1)"


def test_a2_compact_summary_does_not_waive():
    # A2: a compact summary is a synthetic user-type record, not a fresh
    # instruction. It restating "skip tiles" must NOT waive the gate.
    records = _MERGED_NO_ENUM + [
        {"type": "user", "isCompactSummary": True,
         "message": {"content": "Earlier the user mused we might skip tiles on some PRs."}}]
    fire, resolved = gate.evaluate(records)
    assert fire == 599 and resolved is False
    return "compact-summary restating 'skip tiles' -> does NOT waive (A2)"


def test_a2_ismeta_does_not_waive():
    records = _MERGED_NO_ENUM + [
        {"type": "user", "isMeta": True,
         "message": {"content": "<local-command-stdout>no tiles</local-command-stdout>"}}]
    fire, _ = gate.evaluate(records)
    assert fire == 599
    return "isMeta record mentioning 'no tiles' -> does NOT waive (A2)"


def test_a2_genuine_user_skip_still_waives():
    # Guard the other direction: a real user message still waives.
    records = _MERGED_NO_ENUM + [_user_str("skip tiles for this one")]
    fire, resolved = gate.evaluate(records)
    assert fire is None and resolved is True
    return "genuine user 'skip tiles' still waives (A2 preserves the real override)"


def test_a3_malformed_records_do_not_disable_gate():
    # A3: non-dict lines and non-dict message fields must neither raise nor
    # silently disable a session that should fire.
    junk = [None, 123, "a string", [], {"type": "user", "message": "not-a-dict"},
            {"type": "assistant", "message": ["also-not-a-dict"]}, {"no_type": True}]
    records = junk[:3] + _MERGED_NO_ENUM + junk[3:]
    fire, resolved = gate.evaluate(records)
    assert fire == 599 and resolved is False
    # the individual helpers must also not raise on the junk
    assert gate.session_merged_prs(gate.iter_bash_calls(records)) == {599}
    assert gate.skip_override(records) is False
    assert gate.enumeration_recorded(records) is False
    return "malformed/non-dict records -> gate still evaluates, no crash (A3)"


def test_a4_cross_repo_same_number_not_merged():
    # A4: session creates its own PR #50 (never merges it), then inspects an
    # unrelated already-merged PR #50 in a DIFFERENT repo -> must NOT fire.
    calls = [
        ("gh pr create --fill", "https://github.com/brownm09/dev-env/pull/50"),
        ("gh pr view 50 --repo other/proj --json state,number",
         '{"number":50,"state":"MERGED"}'),
    ]
    assert gate.session_merged_prs(calls) == set()
    return "cross-repo same PR number (foreign inspected MERGED) -> not merged (A4)"


def test_a4_same_repo_auto_merge_still_detected():
    # A4 preserves the true positive: same number, explicit MATCHING repo.
    calls = [
        ("gh pr create --fill", "https://github.com/brownm09/dev-env/pull/50"),
        ("gh pr view 50 --repo brownm09/dev-env --json state,number",
         '{"number":50,"state":"MERGED"}'),
    ]
    assert gate.session_merged_prs(calls) == {50}
    return "same PR number, matching explicit repo -> merged (A4 keeps true positive)"


# ---------------------------------------------------------------------------
# _explicit_repo direct coverage (dev-env#634, ADR-050 Amendment 17)
#
# _explicit_repo's own _REPO_FLAG_RE never received PR #623's (?<!\S)
# lookbehind (it already recognized -R before that PR, so #623's audit
# classified it as out of scope) and was never made quote-aware the way
# ADR-050 Amendment 15 made the four sibling _REPO_FLAG_RE sites -- a
# strictly larger, pre-existing gap than dev-env#626 itself, on top of the
# same quote-unawareness. Previously exercised only indirectly via
# session_merged_prs's A4 cases above (both bare, unquoted --repo values);
# these are the function's first direct tests.
# ---------------------------------------------------------------------------

def test_explicit_repo_dash_r_mid_word_not_matched():
    assert gate._explicit_repo("gh pr merge 42 xx-R brownm09/other-repo") is None
    return "mid-word '-R' (not a standalone token) -> None, not falsely matched (dev-env#634)"


def test_explicit_repo_dash_r_inside_quoted_subject_not_matched():
    # A --subject value containing a legitimately space-separated
    # "-R other/repo" substring must not be mistaken for the flag either --
    # mask_quoted_spans blinds the whole quoted span before the regex runs.
    segment = 'gh pr merge 42 --subject "see -R other/repo for context"'
    assert gate._explicit_repo(segment) is None
    return "quoted --subject decoy '-R other/repo' -> None, not falsely matched (dev-env#634)"


def test_explicit_repo_flag_survives_alongside_quoted_decoy():
    # A real, unquoted --repo flag must still resolve correctly even when a
    # quoted --subject value elsewhere in the same segment contains a decoy.
    segment = (
        'gh pr merge 42 --repo brownm09/dev-env --subject '
        '"see -R other/repo for context"'
    )
    assert gate._explicit_repo(segment) == "brownm09/dev-env"
    return "real --repo flag resolves correctly alongside a quoted decoy (dev-env#634)"


def test_explicit_repo_dash_r_shorthand_still_resolves():
    # Regression guard: the new (?<!\S) lookbehind + masking must not break
    # the real -R shorthand this function already supported (dev-env#616).
    assert gate._explicit_repo("gh pr merge 42 -R brownm09/dev-env") == "brownm09/dev-env"
    return "-R shorthand still resolves after the lookbehind + masking fix"


def test_explicit_repo_url_fallback_stays_unmasked():
    # The PR-URL fallback is deliberately out of dev-env#634's scope (point 4
    # scopes the URL-regex analog fix to the four files ADR-050 Amendment 15
    # already touched, not this hook's own _PR_URL_RE) -- a quoted PR URL
    # used AS the repo signal must keep resolving exactly as before.
    segment = 'gh pr merge "https://github.com/brownm09/dev-env/pull/42" --squash'
    assert gate._explicit_repo(segment) == "brownm09/dev-env"
    return "quoted PR URL fallback still resolves (out of dev-env#634's URL-regex scope)"


# ---------------------------------------------------------------------------
# _target_pr direct coverage (dev-env#650, ADR-050 Amendment 19)
#
# _target_pr's own _POS_NUM_RE positional-integer fallback was never made
# quote-aware -- a --subject/--body value containing a legitimately
# space-separated bare number ("resolves 42 items") was indistinguishable
# from a real positional PR-number argument, the same quoted-value blind spot
# Amendment 15 closed for _REPO_FLAG_RE. Previously exercised only indirectly
# via session_merged_prs (test_pr_url_positional_merge_detected etc, all with
# no decoy); these are the function's first direct tests, mirroring the
# _explicit_repo direct-coverage section above.
# ---------------------------------------------------------------------------

def test_target_pr_bare_number_decoy_in_subject_not_matched():
    segment = 'gh pr view --subject "resolves 42 items"'
    assert gate._target_pr(segment) is None
    return "bare number decoy inside quoted --subject -> None, not falsely matched (dev-env#650)"


def test_target_pr_real_number_survives_alongside_bare_number_decoy():
    segment = 'gh pr merge 380 --subject "resolves 42 items"'
    assert gate._target_pr(segment) == 380
    return "real positional number resolves correctly alongside a quoted bare-number decoy (dev-env#650)"


def test_target_pr_url_fallback_stays_unmasked():
    # Mirrors test_explicit_repo_url_fallback_stays_unmasked: the _PR_URL_RE
    # check is deliberately out of both dev-env#634's and dev-env#650's scope.
    segment = 'gh pr merge "https://github.com/brownm09/dev-env/pull/42" --squash'
    assert gate._target_pr(segment) == 42
    return "quoted PR URL fallback still resolves (out of dev-env#634/#650's URL-regex scope)"


def test_target_pr_via_session_merged_prs_with_decoy():
    # Integration-level proof the fix reaches session_merged_prs, via the
    # auto-merge acted-on/observed correlation path (the one path that
    # actually calls _target_pr on the command -- a direct-marker merge
    # resolves its number from the OUTPUT text via merge_pr_number_from_output
    # first, never reaching _target_pr at all). The decoy is placed BEFORE
    # the real positional number: re.search finds the leftmost match, so a
    # decoy placed after the real number would pass even pre-fix (verified
    # while writing this test) -- ordering the decoy first is what actually
    # exercises the masking fix.
    calls = [
        ('gh pr merge --auto --subject "resolves 42 items" 380 --squash',
         "! Pull request #380 will be automatically merged when all requirements are met"),
        ("gh pr view 380 --json state,number", '{"number":380,"state":"MERGED"}'),
    ]
    assert gate.session_merged_prs(calls) == {380}
    return "session_merged_prs: auto-merge correlation resolves the real PR despite a leading --subject bare-number decoy (dev-env#650)"


# ---------------------------------------------------------------------------
# dangling-created-issue detection (ADR-092, dev-env#638)
# ---------------------------------------------------------------------------

# A minimal "issue #630 created, nothing enumerated, never resolved" session.
_ISSUE_CREATED_NO_ENUM = [
    _asst_bash("i1", 'gh issue create --title "Bug" --body "desc"'),
    _tool_result("i1", "https://github.com/brownm09/dev-env/issues/630"),
    _asst_text("Filed issue #630 for the bug."),
]


def test_session_created_issues_detects_url_in_output():
    calls = [('gh issue create --title "x"', "https://github.com/brownm09/dev-env/issues/630")]
    assert gate.session_created_issues(calls) == {630: "brownm09/dev-env"}
    return "gh issue create + issue URL in output -> created {630: repo}"


def test_session_created_issues_help_only_yields_nothing():
    # gh issue create --help prints no issue URL -- no explicit --help guard is
    # needed here (unlike post-tool-use.py/dev-env#636) because the absence of
    # a URL already means "nothing created," which is the correct outcome.
    calls = [("gh issue create --help", "USAGE\n  gh issue create [flags]\n...")]
    assert gate.session_created_issues(calls) == {}
    return "gh issue create --help (no URL in output) -> created {} (dev-env#636 interaction)"


def test_session_created_issues_empty_when_none_created():
    calls = [("npm test", "All tests passed")]
    assert gate.session_created_issues(calls) == {}
    return "no gh issue create this session -> created {}"


def test_session_created_issues_in_heredoc_not_matched():
    # 'gh issue create' inside a heredoc body is not a top-level invocation.
    command = "git commit -F - <<'EOF'\ngh issue create --title x\nEOF"
    calls = [(command, "https://github.com/brownm09/dev-env/issues/630")]
    assert gate.session_created_issues(calls) == {}
    return "'gh issue create' in a heredoc body -> not created (anchored, dev-env#499 class)"


def test_session_resolved_via_merged_pr_closes_keyword():
    calls = [('gh pr create --title "x" --body "Closes #630"',
              "https://github.com/brownm09/dev-env/pull/640")]
    assert gate.session_resolved_issue_numbers(calls, {640}) == {630}
    return "'Closes #630' in a merged PR's create command -> resolved {630}"


def test_session_resolved_via_fixes_and_resolves_keywords():
    calls_fixes = [('gh pr create --body "Fixes #10"', "https://github.com/x/y/pull/1")]
    assert gate.session_resolved_issue_numbers(calls_fixes, {1}) == {10}
    calls_resolves = [('gh pr create --body "Resolves #11"', "https://github.com/x/y/pull/2")]
    assert gate.session_resolved_issue_numbers(calls_resolves, {2}) == {11}
    return "'Fixes #N' / 'Resolves #N' keywords also resolve (GitHub's documented keyword set)"


def test_session_resolved_case_insensitive_and_past_tense():
    calls = [('gh pr create --body "closed #630, fixed #631, resolved #632"',
              "https://github.com/x/y/pull/1")]
    assert gate.session_resolved_issue_numbers(calls, {1}) == {630, 631, 632}
    return "lowercase past-tense forms (closed/fixed/resolved) all match, case-insensitive"


def test_session_not_resolved_if_pr_never_merged():
    # The Closes-keyword text is present, but this PR number is NOT in
    # merged_prs -- GitHub only auto-closes on merge, never on mere creation.
    calls = [('gh pr create --title "x" --body "Closes #630"',
              "https://github.com/brownm09/dev-env/pull/640")]
    assert gate.session_resolved_issue_numbers(calls, set()) == set()
    return "'Closes #630' in a PR that never merged -> not resolved (no auto-close without merge)"


def test_session_resolved_via_heredoc_body_in_pr_create():
    # This repo's own documented commit/PR-body idiom: the Closes keyword
    # lives inside a $(cat <<'EOF' ...) heredoc body, which split_top_level
    # keeps as part of the pr-create segment's own text (not split out).
    command = (
        'gh pr create --title "x" --body "$(cat <<\'EOF\'\n'
        "## Summary\nCloses #630\nEOF\n"
        ')"'
    )
    calls = [(command, "https://github.com/brownm09/dev-env/pull/640")]
    assert gate.session_resolved_issue_numbers(calls, {640}) == {630}
    return "Closes #N inside a heredoc PR body -> resolved (this repo's own PR-body idiom)"


def test_session_resolved_unrelated_chained_segment_not_leaked():
    # A Closes-style mention on a DIFFERENT, unrelated top-level segment must
    # not leak into the pr-create segment's own resolution -- mirrors
    # session_merged_prs's per-segment scoping discipline.
    command = 'echo "reminder: closes #999 later" && gh pr create --title "x" --body "no keyword here"'
    calls = [(command, "https://github.com/brownm09/dev-env/pull/640")]
    assert gate.session_resolved_issue_numbers(calls, {640}) == set()
    return "Closes-style text on an unrelated chained segment -> not leaked into resolution"


def test_session_resolved_via_explicit_issue_close():
    calls = [("gh issue close 630", "Closed issue #630 (Bug)")]
    assert gate.session_resolved_issue_numbers(calls, set()) == {630}
    return "gh issue close 630 -> resolved {630} (no merged PR needed)"


def test_session_resolved_via_explicit_issue_close_url_form():
    # Review of PR #639 (confirmed independently by both reviewers): the
    # issue number in a URL is preceded by '/', never whitespace, so it
    # previously never satisfied _POS_NUM_RE's (?<!\S) boundary -- a session
    # that copy-pastes the URL gh issue create itself just printed (a very
    # natural close-issue flow) was silently misread as still dangling.
    calls = [("gh issue close https://github.com/brownm09/dev-env/issues/630",
              "Closed issue #630 (Bug)")]
    assert gate.session_resolved_issue_numbers(calls, set()) == {630}
    return "gh issue close <url> -> resolved {630} (dev-env#639 review fix)"


# ---------------------------------------------------------------------------
# _closed_issue_number direct coverage (dev-env#650, ADR-050 Amendment 19)
#
# _closed_issue_number shares _target_pr's exact _POS_NUM_RE positional-
# integer fallback (the literal same compiled regex object) but was not named
# in dev-env#650's own three-site audit -- found by grepping this file for
# _POS_NUM_RE usages while fixing the two sites the issue did name, per the
# issue's own closing suggestion ("worth grepping the rest of
# claude/scripts/*.py... before assuming these three are exhaustive").
# Previously exercised only indirectly via session_resolved_issue_numbers
# (test_session_resolved_via_explicit_issue_close et al, all with no decoy);
# these are the function's first direct tests, mirroring _target_pr's own
# direct-coverage section above.
# ---------------------------------------------------------------------------

def test_closed_issue_number_bare_number_decoy_not_matched():
    segment = 'gh issue close --comment "resolves 42 items"'
    assert gate._closed_issue_number(segment) is None
    return "bare number decoy inside quoted --comment -> None, not falsely matched (dev-env#650)"


def test_closed_issue_number_real_number_survives_alongside_decoy():
    # Flag-before-positional-arg (like _target_pr's test_cmd_flag_before_arg
    # analog): the decoy comes first, the real target issue number last.
    segment = 'gh issue close --comment "resolves 42 items" 630'
    assert gate._closed_issue_number(segment) == 630
    return "real target issue number resolves correctly alongside a leading bare-number decoy (dev-env#650)"


def test_closed_issue_number_via_session_resolved_with_decoy():
    # Integration-level proof the fix reaches session_resolved_issue_numbers.
    calls = [('gh issue close --comment "resolves 42 items" 630', "Closed issue #630 (Bug)")]
    assert gate.session_resolved_issue_numbers(calls, set()) == {630}
    return "session_resolved_issue_numbers: real issue resolves correctly despite a leading bare-number decoy (dev-env#650)"


def test_session_resolved_via_gh_pr_edit_closes_keyword():
    # The "create the PR, then attach the Closes keyword afterward" flow
    # (review of PR #639) -- gh pr edit's target PR number is read via the
    # same _target_pr helper session_merged_prs already uses for merge/view.
    calls = [
        ("gh pr create --title x --body y", "https://github.com/brownm09/dev-env/pull/640"),
        ('gh pr edit 640 --body "Closes #630"', ""),
    ]
    assert gate.session_resolved_issue_numbers(calls, {640}) == {630}
    return "gh pr edit 640 --body 'Closes #630' (640 merged) -> resolved {630}"


def test_session_resolved_via_gh_pr_edit_pr_url_target():
    calls = [
        ('gh pr edit https://github.com/brownm09/dev-env/pull/640 --body "Fixes #630"', ""),
    ]
    assert gate.session_resolved_issue_numbers(calls, {640}) == {630}
    return "gh pr edit <pull-URL> --body 'Fixes #630' (640 merged) -> resolved {630}"


def test_session_resolved_via_gh_pr_edit_target_not_merged():
    calls = [('gh pr edit 640 --body "Closes #630"', "")]
    assert gate.session_resolved_issue_numbers(calls, set()) == set()
    return "gh pr edit targeting an unmerged PR -> not resolved (no auto-close without merge)"


def test_session_resolved_issue_close_in_heredoc_not_matched():
    command = "git commit -F - <<'EOF'\ngh issue close 630\nEOF"
    calls = [(command, "some output")]
    assert gate.session_resolved_issue_numbers(calls, set()) == set()
    return "'gh issue close' in a heredoc body -> not resolved (anchored, dev-env#499 class)"


def test_session_unresolved_created_issues_dangling():
    calls = gate.iter_bash_calls(_ISSUE_CREATED_NO_ENUM)
    merged = gate.session_merged_prs(calls)
    assert gate.session_unresolved_created_issues(calls, merged) == {630}
    return "issue created, never resolved -> unresolved {630}"


def test_session_unresolved_created_issues_resolved_via_merge():
    records = [
        _asst_bash("i1", 'gh issue create --title "Bug"'),
        _tool_result("i1", "https://github.com/brownm09/dev-env/issues/630"),
        _asst_bash("p1", 'gh pr create --title "Fix" --body "Closes #630"'),
        _tool_result("p1", "https://github.com/brownm09/dev-env/pull/640"),
        _asst_bash("m1", "gh pr merge 640 --squash"),
        _tool_result("m1", "Squashed and merged pull request #640"),
    ]
    calls = gate.iter_bash_calls(records)
    merged = gate.session_merged_prs(calls)
    assert merged == {640}
    assert gate.session_unresolved_created_issues(calls, merged) == set()
    return "issue created, closed via a merged PR's Closes keyword -> unresolved {} (fully resolved)"


def test_session_unresolved_created_issues_resolved_via_explicit_close():
    records = _ISSUE_CREATED_NO_ENUM + [
        _asst_bash("c1", "gh issue close 630 --comment done"),
        _tool_result("c1", "Closed issue #630"),
    ]
    calls = gate.iter_bash_calls(records)
    merged = gate.session_merged_prs(calls)
    assert gate.session_unresolved_created_issues(calls, merged) == set()
    return "issue created then explicitly closed -> unresolved {} (fully resolved)"


def test_session_unresolved_created_issues_none_created():
    calls = [("npm test", "All tests passed")]
    assert gate.session_unresolved_created_issues(calls, set()) == set()
    return "no issue created this session -> unresolved {} (nothing to check)"


# ---------------------------------------------------------------------------
# evaluate_issues() composition
# ---------------------------------------------------------------------------

def test_evaluate_issues_dangling_fires():
    fire, resolved = gate.evaluate_issues(_ISSUE_CREATED_NO_ENUM)
    assert fire == 630 and resolved is False
    return "issue created + no enumeration + no skip -> (630, resolved=False) [FIRE]"


def test_evaluate_issues_with_enum_resolved():
    records = _ISSUE_CREATED_NO_ENUM + [
        _asst_text("Follow-ups considered: issue #630 -> tiled (task_ab12).")]
    fire, resolved = gate.evaluate_issues(records)
    assert fire is None and resolved is True
    return "issue created + enumeration -> (None, resolved=True)"


def test_evaluate_issues_with_skip_resolved():
    records = _ISSUE_CREATED_NO_ENUM + [_user_str("skip tiles")]
    fire, resolved = gate.evaluate_issues(records)
    assert fire is None and resolved is True
    return "issue created + 'skip tiles' override -> (None, resolved=True)"


def test_evaluate_issues_no_issue_noop():
    records = [_asst_bash("t1", "npm test"), _tool_result("t1", "All tests passed")]
    fire, resolved = gate.evaluate_issues(records)
    assert fire is None and resolved is False
    return "no issue created this session -> (None, resolved=False) [stay unresolved]"


def test_evaluate_issues_created_and_resolved_sets_sentinel():
    # Review of PR #639: distinct from "nothing created" -- everything
    # created this session was resolved (explicit close, no merge anywhere)
    # with no enumeration needed. Must return resolved=True (not False) so
    # main() marks the sentinel and later Stops in this session skip the
    # re-scan; before this fix both cases collapsed to (None, False) and a
    # create-then-close session with no merge never set the sentinel, paying
    # the full scan on every subsequent turn indefinitely.
    records = _ISSUE_CREATED_NO_ENUM + [
        _asst_bash("c1", "gh issue close 630"), _tool_result("c1", "Closed issue #630")]
    fire, resolved = gate.evaluate_issues(records)
    assert fire is None and resolved is True
    return "issue created + explicitly closed, no merge anywhere -> (None, resolved=True) [sentinel set]"


def test_evaluate_issues_picks_lowest_deterministically():
    records = [
        _asst_bash("i1", 'gh issue create --title "A"'),
        _tool_result("i1", "https://github.com/brownm09/dev-env/issues/42"),
        _asst_bash("i2", 'gh issue create --title "B"'),
        _tool_result("i2", "https://github.com/brownm09/dev-env/issues/7"),
        _asst_text("both filed."),
    ]
    fire, _ = gate.evaluate_issues(records)
    assert fire == 7
    return "two dangling issues, no enum -> fires on the lowest issue number deterministically"


def test_evaluate_issues_bare_no_followups_not_enumeration():
    # Mirrors the #700 skip for the merged-PR trigger: a bare assertion must
    # not satisfy the issue trigger either (enumeration_recorded is shared).
    records = _ISSUE_CREATED_NO_ENUM + [
        _asst_text("The finalization work surfaced no new follow-ups.")]
    fire, resolved = gate.evaluate_issues(records)
    assert fire == 630 and resolved is False
    return "bare 'no follow-ups' -> does NOT satisfy the issue trigger either (shared #700 guard)"


def test_format_issue_reminder_is_cp1252_encodable():
    msg = gate.format_issue_reminder(630)
    assert msg.isascii(), "reminder must be ASCII (Claude Code pipes hook output as cp1252)"
    msg.encode("cp1252")  # must not raise
    assert "#630" in msg and "skip tiles" in msg
    return "format_issue_reminder is ASCII/cp1252-encodable and names the issue"


# ---------------------------------------------------------------------------
# combined merged-PR + dangling-issue: both triggers share one enumeration
# ---------------------------------------------------------------------------

def test_combined_merged_pr_and_dangling_issue_both_fire_independently():
    # A session that merges a PR AND creates a separate, still-dangling issue,
    # with NO enumeration recorded for either -- both evaluate() and
    # evaluate_issues() must independently detect and fire on their own
    # trigger (review of PR #639: the original comment here incorrectly
    # described the enumerated/resolved case, which is the NEXT test below).
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM
    fire_pr, resolved_pr = gate.evaluate(records)
    fire_issue, resolved_issue = gate.evaluate_issues(records)
    assert fire_pr == 599 and resolved_pr is False
    assert fire_issue == 630 and resolved_issue is False
    return "merged PR (no enum) + dangling issue (no enum), same session -> both fire independently"


def test_combined_one_enumeration_satisfies_both():
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM + [
        _asst_text("Follow-ups considered: both items -> tiled.")]
    fire_pr, resolved_pr = gate.evaluate(records)
    fire_issue, resolved_issue = gate.evaluate_issues(records)
    assert fire_pr is None and resolved_pr is True
    assert fire_issue is None and resolved_issue is True
    return "one enumeration covering both -> both evaluate() and evaluate_issues() resolved"


# ---------------------------------------------------------------------------
# tiles-spawned-without-a-table trigger (ADR-094 addendum, dev-env#656)
# ---------------------------------------------------------------------------

_TABLE_HEADING = "### Tiles spawned this session"

# A minimal "one tile spawned, no table" session.
_SPAWNED_NO_TABLE = [
    _asst_spawn("s1"),
    _asst_text("Filed a follow-up tile for the flaky test."),
]


def test_session_spawned_tiles_true_on_real_spawn():
    assert gate.session_spawned_tiles([_asst_spawn()])
    return "a spawn_task tool_use -> session_spawned_tiles True"


def test_session_spawned_tiles_false_without_spawn():
    assert not gate.session_spawned_tiles([_asst_text("no tiles here")])
    return "no spawn_task tool_use -> session_spawned_tiles False"


def test_session_spawned_tiles_detects_other_namespace():
    # Review of PR #674: _SPAWN_TASK_RE is deliberately namespace-agnostic
    # ("Bare verb so any namespacing hits"); pin that session_spawned_tiles
    # (and therefore both enumeration_recorded and evaluate_tile_table, which
    # now share this single source of truth) honors that, not just the
    # standard mcp__ccd_session__ prefix.
    assert gate.session_spawned_tiles([_asst_spawn_other_namespace()])
    return "differently-namespaced spawn_task tool_use -> session_spawned_tiles True"


def test_enumeration_recorded_delegates_to_session_spawned_tiles():
    # enumeration_recorded's tool_use check now delegates to
    # session_spawned_tiles (review of PR #674) -- pin they can't drift by
    # exercising the SAME differently-namespaced spawn through both.
    assert gate.enumeration_recorded([_asst_spawn_other_namespace()])
    return "differently-namespaced spawn -> enumeration_recorded also True (delegation)"


def test_table_marker_present_true_on_heading():
    assert gate.table_marker_present([_asst_text(_TABLE_HEADING + "\n\n| Tile | Issue |\n")])
    return "assistant text with the '### Tiles spawned this session' heading -> present"


def test_table_marker_present_case_and_heading_level_insensitive():
    assert gate.table_marker_present([_asst_text("###### tiles SPAWNED this SESSION")])
    return "different heading level + case -> still matches (lenient on level/case)"


def test_table_marker_present_false_without_heading():
    assert not gate.table_marker_present([_asst_text("Spawned a tile for the flaky test.")])
    return "assistant text mentioning tiles but no heading -> not present"


def test_table_marker_present_prose_mention_not_anchored_does_not_match():
    # The heading phrase mentioned mid-sentence (not at the start of a line)
    # must NOT satisfy the marker -- only a real heading (its own line) does.
    assert not gate.table_marker_present(
        [_asst_text("The hook wants a ### Tiles spawned this session heading here.")])
    return "heading phrase mid-sentence (not line-anchored) -> NOT a match"


def test_table_marker_present_ignores_user_record():
    # A user message (or a tool_result echoing CLAUDE.md text) containing the
    # heading must never satisfy the gate -- only assistant text counts.
    assert not gate.table_marker_present([_user_str(_TABLE_HEADING)])
    return "heading text in a USER record -> NOT present (assistant-only scope)"


def test_evaluate_tile_table_fires_without_marker():
    fire, resolved = gate.evaluate_tile_table(_SPAWNED_NO_TABLE)
    assert fire is True and resolved is False
    return "tile spawned + no table + no skip -> (True, resolved=False) [FIRE]"


def test_evaluate_tile_table_resolved_with_marker():
    records = _SPAWNED_NO_TABLE + [
        _asst_text(_TABLE_HEADING + "\n| Tile | Issue | Status | Next |\n")]
    fire, resolved = gate.evaluate_tile_table(records)
    assert fire is False and resolved is True
    return "tile spawned + table heading present -> (False, resolved=True)"


def test_evaluate_tile_table_resolved_with_skip():
    records = _SPAWNED_NO_TABLE + [_user_str("skip tiles")]
    fire, resolved = gate.evaluate_tile_table(records)
    assert fire is False and resolved is True
    return "tile spawned + 'skip tiles' override -> (False, resolved=True)"


def test_evaluate_tile_table_noop_without_spawn():
    records = [_asst_bash("t1", "npm test"), _tool_result("t1", "ok")]
    fire, resolved = gate.evaluate_tile_table(records)
    assert fire is False and resolved is False
    return "no tile spawned this session -> (False, resolved=False) [stay unresolved]"


def test_format_table_reminder_is_cp1252_encodable():
    msg = gate.format_table_reminder()
    assert msg.isascii(), "reminder must be ASCII (Claude Code pipes hook output as cp1252)"
    msg.encode("cp1252")  # must not raise
    assert "Tiles spawned this session" in msg and "skip tiles" in msg
    return "format_table_reminder is ASCII/cp1252-encodable and names the exact heading"


# --- trigger interactions: a spawn satisfies enumeration (1/2) but not (3) -----

def test_spawn_resolves_merge_trigger_but_table_trigger_still_fires():
    # A spawned tile satisfies enumeration_recorded, so the merge trigger
    # resolves silently -- but the table trigger is a STRICTER, separate bar
    # and still fires because no table heading was ever emitted.
    records = _MERGED_NO_ENUM + [_asst_spawn()]
    fire_pr, resolved_pr = gate.evaluate(records)
    fire_table, resolved_table = gate.evaluate_tile_table(records)
    assert fire_pr is None and resolved_pr is True
    assert fire_table is True and resolved_table is False
    return "spawn resolves the merge trigger (1) but the table trigger (3) still fires"


def test_spawn_only_no_merge_no_issue_table_trigger_fires():
    # The genuinely new enforcement surface: no merge, no issue, just a
    # spawned tile with no table -- only trigger 3 can catch this.
    fire_pr, resolved_pr = gate.evaluate(_SPAWNED_NO_TABLE)
    fire_issue, resolved_issue = gate.evaluate_issues(_SPAWNED_NO_TABLE)
    fire_table, resolved_table = gate.evaluate_tile_table(_SPAWNED_NO_TABLE)
    assert fire_pr is None and resolved_pr is False
    assert fire_issue is None and resolved_issue is False
    assert fire_table is True and resolved_table is False
    return "spawn-only session (no merge/issue) -> only the table trigger fires"


def test_merge_no_spawn_table_trigger_is_noop():
    fire_pr, _ = gate.evaluate(_MERGED_NO_ENUM)
    fire_table, resolved_table = gate.evaluate_tile_table(_MERGED_NO_ENUM)
    assert fire_pr == 599
    assert fire_table is False and resolved_table is False
    return "merged PR, no tile spawned -> table trigger is a no-op (nothing to table)"


def test_combined_all_three_triggers_fire_independently():
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM + [_asst_spawn()]
    fire_pr, resolved_pr = gate.evaluate(records)
    fire_issue, resolved_issue = gate.evaluate_issues(records)
    fire_table, resolved_table = gate.evaluate_tile_table(records)
    # the spawn satisfies enumeration_recorded, resolving triggers 1 and 2...
    assert fire_pr is None and resolved_pr is True
    assert fire_issue is None and resolved_issue is True
    # ...but the table trigger is independent and still fires.
    assert fire_table is True and resolved_table is False
    return ("merged PR + dangling issue + spawned tile, no table -> triggers 1/2 "
            "resolved by the spawn, trigger 3 still fires")


# ---------------------------------------------------------------------------
# behavioral layer — real hook over stdin via subprocess (HOME-isolated sentinel)
# ---------------------------------------------------------------------------

def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _run_hook(records, home, *, stop_hook_active=False, session_id="sess-test"):
    """Drive the real hook once. Returns (exit_code, stdout, stderr)."""
    home = Path(home)
    (home / ".claude").mkdir(parents=True, exist_ok=True)  # so the sentinel mkdir works
    tpath = home / "transcript.jsonl"
    tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    payload = json.dumps({
        "session_id": session_id,
        "transcript_path": str(tpath),
        "stop_hook_active": stop_hook_active,
        "hook_event_name": "Stop",
    })
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=payload,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_e2e_merged_no_enum_blocks_on_stderr():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_MERGED_NO_ENUM, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[tile-enumeration-gate]" in err and "#599" in err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e merged + no enum -> exit 2, reason on stderr, empty stdout"


def test_e2e_merged_with_enum_allows():
    # A spawn_task tile satisfies enumeration_recorded (resolving trigger 1),
    # but is no longer sufficient alone once trigger 3 (ADR-094 addendum,
    # dev-env#656) exists -- the tile-table heading is also required for a
    # genuinely fully-compliant session, since the bare spawn still leaves
    # trigger 3 unsatisfied (see test_spawn_resolves_merge_trigger_but_
    # table_trigger_still_fires for the isolated interaction this covers).
    records = _MERGED_NO_ENUM + [
        _asst_spawn(),
        _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e merged + spawn_task tile + table -> exit 0 (allowed)"


def test_e2e_no_merge_allows():
    records = [_asst_bash("t1", "npm test"), _tool_result("t1", "ok")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no merge -> exit 0 (allowed)"


def test_e2e_stop_hook_active_allows():
    # Loop guard: even a merged-no-enum session must not re-block once Claude is
    # already continuing from a prior Stop block.
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_MERGED_NO_ENUM, home, stop_hook_active=True)
    assert rc == 0, f"expected exit 0 with stop_hook_active, got {rc} (stderr={err!r})"
    return "e2e stop_hook_active=true -> exit 0 (loop guard)"


def test_e2e_sentinel_suppresses_refire():
    with tempfile.TemporaryDirectory() as home:
        rc1, _, _ = _run_hook(_MERGED_NO_ENUM, home, session_id="sess-refire")
        rc2, out2, _ = _run_hook(_MERGED_NO_ENUM, home, session_id="sess-refire")
    assert rc1 == 2, f"first run expected exit 2, got {rc1}"
    assert rc2 == 0, f"second run expected exit 0 (sentinel), got {rc2}"
    return "e2e once-per-session sentinel: first fire exit 2, second exit 0"


# ---------------------------------------------------------------------------
# behavioral layer — dangling-created-issue trigger (ADR-092, dev-env#638)
# ---------------------------------------------------------------------------

def test_e2e_dangling_issue_no_enum_blocks_on_stderr():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_ISSUE_CREATED_NO_ENUM, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[tile-enumeration-gate]" in err and "#630" in err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e dangling issue + no enum -> exit 2, reason on stderr, empty stdout"


def test_e2e_dangling_issue_with_enum_allows():
    # Same rationale as test_e2e_merged_with_enum_allows: a bare spawn_task
    # tile resolves trigger 2 (enumeration_recorded) but, since trigger 3
    # (ADR-094 addendum, dev-env#656) exists, a genuinely fully-compliant
    # session also needs the tile-table heading.
    records = _ISSUE_CREATED_NO_ENUM + [
        _asst_spawn(),
        _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e dangling issue + spawn_task tile + table -> exit 0 (allowed)"


def test_e2e_issue_resolved_via_merge_allows():
    records = [
        _asst_bash("i1", 'gh issue create --title "Bug"'),
        _tool_result("i1", "https://github.com/brownm09/dev-env/issues/630"),
        _asst_bash("p1", 'gh pr create --title "Fix" --body "Closes #630"'),
        _tool_result("p1", "https://github.com/brownm09/dev-env/pull/640"),
        _asst_bash("m1", "gh pr merge 640 --squash"),
        _tool_result("m1", "Squashed and merged pull request #640"),
        _asst_text("Follow-ups considered: none -> not tiled, because fully resolved."),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e issue resolved via a same-session merged PR + enum -> exit 0 (allowed)"


def test_e2e_issue_explicit_close_no_enum_still_allows():
    # Explicit close alone resolves the issue -- no enumeration needed since
    # there is nothing dangling once resolved (mirrors the merged-PR path's
    # own "resolved without enumeration" case being a no-op, not a block).
    records = _ISSUE_CREATED_NO_ENUM + [
        _asst_bash("c1", "gh issue close 630"), _tool_result("c1", "Closed issue #630")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e issue explicitly closed (no enum needed, nothing dangling) -> exit 0"


def test_e2e_no_issue_created_allows():
    records = [_asst_bash("t1", "npm test"), _tool_result("t1", "ok")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no issue created -> exit 0 (allowed, pre-filter and evaluators agree)"


def test_e2e_combined_merged_pr_and_dangling_issue_both_messages():
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "#599" in err and "#630" in err, f"expected both PR and issue named, got {err!r}"
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e merged PR + dangling issue, no enum -> exit 2 with BOTH reminders combined"


def test_e2e_dangling_issue_sentinel_suppresses_refire():
    with tempfile.TemporaryDirectory() as home:
        rc1, _, _ = _run_hook(_ISSUE_CREATED_NO_ENUM, home, session_id="sess-issue-refire")
        rc2, out2, _ = _run_hook(_ISSUE_CREATED_NO_ENUM, home, session_id="sess-issue-refire")
    assert rc1 == 2, f"first run expected exit 2, got {rc1}"
    assert rc2 == 0, f"second run expected exit 0 (sentinel), got {rc2}"
    return "e2e dangling-issue sentinel: first fire exit 2, second exit 0"


# ---------------------------------------------------------------------------
# behavioral layer — tiles-spawned-without-a-table trigger (ADR-094 addendum)
# ---------------------------------------------------------------------------

def test_e2e_spawn_no_table_blocks_on_stderr():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_SPAWNED_NO_TABLE, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[tile-enumeration-gate]" in err and "Tiles spawned this session" in err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e tile spawned + no table -> exit 2, reason on stderr, empty stdout"


def test_e2e_spawn_with_table_allows():
    records = _SPAWNED_NO_TABLE + [
        _asst_text(_TABLE_HEADING + "\n| Tile | Issue | Status | Next |\n")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e tile spawned + table heading -> exit 0 (allowed)"


def test_e2e_spawn_with_skip_allows():
    records = _SPAWNED_NO_TABLE + [_user_str("skip tiles")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e tile spawned + 'skip tiles' override -> exit 0 (allowed)"


def test_e2e_combined_all_three_no_table_blocks_naming_table_only():
    # Merge + dangling issue are both resolved by the spawn (enumeration),
    # but the table trigger still fires -- only the table reminder appears.
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM + [_asst_spawn()]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "Tiles spawned this session" in err
    assert "#599" not in err and "#630" not in err, (
        f"merge/issue triggers should be silently resolved by the spawn, got {err!r}")
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return ("e2e merged PR + dangling issue + spawn (no table) -> exit 2 naming "
            "ONLY the table trigger")


def test_e2e_spawn_table_sentinel_suppresses_refire():
    with tempfile.TemporaryDirectory() as home:
        rc1, _, _ = _run_hook(_SPAWNED_NO_TABLE, home, session_id="sess-table-refire")
        rc2, out2, _ = _run_hook(_SPAWNED_NO_TABLE, home, session_id="sess-table-refire")
    assert rc1 == 2, f"first run expected exit 2, got {rc1}"
    assert rc2 == 0, f"second run expected exit 0 (sentinel), got {rc2}"
    return "e2e tile-table sentinel: first fire exit 2, second exit 0"


def test_e2e_spawn_other_namespace_no_table_still_blocks():
    # Review of PR #674: the pre-filter's bare "spawn_task" substring must
    # not exclude a spawn recorded under a namespace other than
    # mcp__ccd_session__ -- proves the pre-filter is a true superset of what
    # session_spawned_tiles (the real detector) can match, end-to-end through
    # main()'s pre-filter + full evaluation, not just the pure helper.
    records = [_asst_spawn_other_namespace(), _asst_text("Filed a follow-up.")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "Tiles spawned this session" in err
    return "e2e differently-namespaced spawn + no table -> exit 2 (pre-filter doesn't exclude it)"


# ---------------------------------------------------------------------------
# per-trigger sentinel independence (ADR-097, dev-env#677)
#
# Before this fix, all three triggers shared ONE sentinel file: whichever
# trigger fired or resolved FIRST set it, and every later Stop in the session
# skipped evaluating ALL THREE triggers -- including one whose own condition
# had not even occurred yet. These tests drive the hook across TWO separate
# Stop calls with the same session_id and HOME (so sentinel state persists
# between them, exactly like two Stops within one real session), and also
# inspect the per-trigger sentinel files directly on disk.
# ---------------------------------------------------------------------------

def _sentinel_file(home, trigger, session_id):
    return Path(home) / ".claude" / "scratch" / f"tile-enumeration-gate-{trigger}{session_id}.flag"


def test_e2e_later_trigger_still_fires_after_sibling_sentinel_set():
    # THE dev-env#677 bug, reproduced: turn 1 merges a PR with no enumeration
    # (fires + resolves trigger 1 only). Turn 2 -- a genuinely later, separate
    # Stop -- spawns a tile with no table. Under the old shared sentinel, turn
    # 1 having already fired would suppress evaluating trigger 3 entirely in
    # turn 2, silently missing it. Per-trigger sentinels fix this: trigger 1's
    # sentinel must not suppress trigger 3's own, independent evaluation.
    records_turn1 = _MERGED_NO_ENUM
    records_turn2 = _MERGED_NO_ENUM + [
        _asst_spawn(), _asst_text("Filed a follow-up tile for the flaky test.")]
    with tempfile.TemporaryDirectory() as home:
        rc1, _, err1 = _run_hook(records_turn1, home, session_id="sess-677")
        rc2, out2, err2 = _run_hook(records_turn2, home, session_id="sess-677")
    assert rc1 == 2, f"turn 1 (merge, no enum) expected exit 2, got {rc1} (stderr={err1!r})"
    assert "#599" in err1
    assert rc2 == 2, (
        "turn 2 (tile spawned later, no table) expected exit 2 -- the "
        f"dev-env#677 bug would wrongly return 0 here, got {rc2} (stderr={err2!r})")
    assert "Tiles spawned this session" in err2
    assert "#599" not in err2, "trigger 1 already resolved this session -- must not re-fire"
    assert out2.strip() == "", f"stdout must be empty on exit 2, got {out2!r}"
    return ("e2e dev-env#677 regression: trigger 1 firing in turn 1 does NOT "
            "suppress trigger 3 firing later in turn 2 (per-trigger sentinels)")


def test_e2e_partial_session_only_sets_the_fired_triggers_sentinel():
    # A merge-only session (no issue, no tile) fires and resolves ONLY
    # trigger 1 -- its sentinel file must exist, but triggers 2/3's must NOT,
    # since neither has a condition to resolve yet and must stay open for one
    # that could still arise later this session.
    with tempfile.TemporaryDirectory() as home:
        rc, _, _ = _run_hook(_MERGED_NO_ENUM, home, session_id="sess-partial")
        pr_set = _sentinel_file(home, gate._TRIGGER_PR, "sess-partial").exists()
        issue_set = _sentinel_file(home, gate._TRIGGER_ISSUE, "sess-partial").exists()
        table_set = _sentinel_file(home, gate._TRIGGER_TABLE, "sess-partial").exists()
    assert rc == 2
    assert pr_set, "trigger 1 fired -- its own sentinel must be set"
    assert not issue_set, "trigger 2 never had a condition this session -- must stay unset"
    assert not table_set, "trigger 3 never had a condition this session -- must stay unset"
    return "merge-only session -> ONLY the pr- sentinel is set; issue-/table- stay open"


def test_e2e_fully_compliant_session_sets_all_three_sentinels_and_stays_allowed():
    # A session where all three conditions are resolved in one turn (the spawn
    # resolves triggers 1/2 via enumeration_recorded; the table heading
    # resolves trigger 3) sets ALL THREE sentinels, and a second Stop with the
    # same transcript stays allowed (stable, no spurious re-fire).
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM + [
        _asst_spawn(),
        _asst_text(_TABLE_HEADING + "\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc1, _, err1 = _run_hook(records, home, session_id="sess-full")
        pr_set = _sentinel_file(home, gate._TRIGGER_PR, "sess-full").exists()
        issue_set = _sentinel_file(home, gate._TRIGGER_ISSUE, "sess-full").exists()
        table_set = _sentinel_file(home, gate._TRIGGER_TABLE, "sess-full").exists()
        rc2, out2, err2 = _run_hook(records, home, session_id="sess-full")
    assert rc1 == 0, f"expected exit 0 (fully resolved), got {rc1} (stderr={err1!r})"
    assert pr_set and issue_set and table_set, "all three sentinels must be set once fully resolved"
    assert rc2 == 0, f"second Stop expected exit 0 (stable), got {rc2} (stderr={err2!r})"
    return ("fully-compliant session -> all three per-trigger sentinels set; "
            "a later Stop with the same transcript stays allowed")


def main():
    tests = [
        ("direct merge marker detected", test_direct_merge_marker_detected),
        ("gh api merge detected", test_gh_api_merge_detected),
        ("auto-merge correlated detected", test_auto_merge_correlated_detected),
        ("observed-MERGED not-acted-on ignored", test_observed_merged_not_acted_on_ignored),
        ("--auto enqueue alone not merged", test_auto_flag_enqueue_alone_not_merged),
        ("gh pr merge --help not merged", test_merge_help_not_merged),
        ("merge text in heredoc not matched", test_merge_text_in_heredoc_not_matched),
        ("merge text in subshell not matched", test_merge_text_in_subshell_not_matched),
        ("PR-URL positional merge detected", test_pr_url_positional_merge_detected),
        ("enum: spawn_task", test_enum_spawn_task),
        ("enum: 'Follow-ups considered'", test_enum_followups_considered_text),
        ("enum: '-> not tiled, because'", test_enum_not_tiled_text),
        ("enum: '-> tiled' ascii+unicode", test_enum_arrow_tiled_ascii_and_unicode),
        ("bare 'no follow-ups' NOT enum (#700)", test_bare_no_followups_not_enumeration),
        ("plain summary NOT enum", test_no_enumeration_plain_summary),
        ("skip override: user string", test_skip_override_user_string),
        ("skip override: user text item", test_skip_override_user_text_item),
        ("skip override: don't spawn tiles", test_skip_override_dont_spawn),
        ("skip override: tool_result not counted", test_skip_override_toolresult_not_counted),
        ("skip override absent", test_skip_override_absent),
        ("evaluate merged+no-enum fires", test_evaluate_merged_no_enum_fires),
        ("evaluate merged+enum resolved", test_evaluate_merged_with_enum_resolved),
        ("evaluate merged+skip resolved", test_evaluate_merged_with_skip_resolved),
        ("evaluate no-merge no-op", test_evaluate_no_merge_noop),
        ("evaluate picks lowest PR", test_evaluate_picks_lowest_pr_deterministically),
        ("iter_bash_calls pairs by id", test_iter_bash_calls_pairs_by_id),
        ("reminder cp1252-encodable", test_reminder_is_cp1252_encodable),
        ("A1 observed prefers JSON number", test_a1_observed_prefers_json_number),
        ("A2 compact-summary does not waive", test_a2_compact_summary_does_not_waive),
        ("A2 isMeta does not waive", test_a2_ismeta_does_not_waive),
        ("A2 genuine user skip still waives", test_a2_genuine_user_skip_still_waives),
        ("A3 malformed records do not disable", test_a3_malformed_records_do_not_disable_gate),
        ("A4 cross-repo same number not merged", test_a4_cross_repo_same_number_not_merged),
        ("A4 same-repo auto-merge detected", test_a4_same_repo_auto_merge_still_detected),
        ("_explicit_repo: mid-word '-R' not matched (dev-env#634)", test_explicit_repo_dash_r_mid_word_not_matched),
        ("_explicit_repo: '-R' inside quoted --subject not matched (dev-env#634)", test_explicit_repo_dash_r_inside_quoted_subject_not_matched),
        ("_explicit_repo: --repo flag survives alongside quoted decoy (dev-env#634)", test_explicit_repo_flag_survives_alongside_quoted_decoy),
        ("_explicit_repo: -R shorthand still resolves", test_explicit_repo_dash_r_shorthand_still_resolves),
        ("_explicit_repo: quoted PR URL fallback stays unmasked", test_explicit_repo_url_fallback_stays_unmasked),
        ("_target_pr: bare number decoy inside quoted --subject not matched (dev-env#650)", test_target_pr_bare_number_decoy_in_subject_not_matched),
        ("_target_pr: real number survives alongside bare-number decoy (dev-env#650)", test_target_pr_real_number_survives_alongside_bare_number_decoy),
        ("_target_pr: quoted PR URL fallback stays unmasked (dev-env#650)", test_target_pr_url_fallback_stays_unmasked),
        ("_target_pr: session_merged_prs resolves real PR despite leading decoy (dev-env#650)", test_target_pr_via_session_merged_prs_with_decoy),
        ("e2e merged+no-enum blocks on stderr", test_e2e_merged_no_enum_blocks_on_stderr),
        ("e2e merged+enum allows", test_e2e_merged_with_enum_allows),
        ("e2e no-merge allows", test_e2e_no_merge_allows),
        ("e2e stop_hook_active allows", test_e2e_stop_hook_active_allows),
        ("e2e sentinel suppresses re-fire", test_e2e_sentinel_suppresses_refire),
        # --- dangling-created-issue trigger (ADR-092, dev-env#638) ---
        ("session_created_issues: detects URL in output", test_session_created_issues_detects_url_in_output),
        ("session_created_issues: --help yields nothing", test_session_created_issues_help_only_yields_nothing),
        ("session_created_issues: empty when none created", test_session_created_issues_empty_when_none_created),
        ("session_created_issues: heredoc not matched", test_session_created_issues_in_heredoc_not_matched),
        ("resolved: via merged PR 'Closes #N'", test_session_resolved_via_merged_pr_closes_keyword),
        ("resolved: 'Fixes #N' / 'Resolves #N' keywords", test_session_resolved_via_fixes_and_resolves_keywords),
        ("resolved: case-insensitive past-tense forms", test_session_resolved_case_insensitive_and_past_tense),
        ("resolved: not resolved if PR never merged", test_session_not_resolved_if_pr_never_merged),
        ("resolved: Closes keyword inside heredoc PR body", test_session_resolved_via_heredoc_body_in_pr_create),
        ("resolved: unrelated chained segment not leaked", test_session_resolved_unrelated_chained_segment_not_leaked),
        ("resolved: via explicit gh issue close", test_session_resolved_via_explicit_issue_close),
        ("resolved: via explicit gh issue close (URL form)", test_session_resolved_via_explicit_issue_close_url_form),
        ("_closed_issue_number: bare number decoy not matched (dev-env#650)", test_closed_issue_number_bare_number_decoy_not_matched),
        ("_closed_issue_number: real number survives alongside decoy (dev-env#650)", test_closed_issue_number_real_number_survives_alongside_decoy),
        ("_closed_issue_number: session_resolved_issue_numbers resolves despite leading decoy (dev-env#650)", test_closed_issue_number_via_session_resolved_with_decoy),
        ("resolved: via gh pr edit Closes keyword", test_session_resolved_via_gh_pr_edit_closes_keyword),
        ("resolved: via gh pr edit PR-URL target", test_session_resolved_via_gh_pr_edit_pr_url_target),
        ("resolved: gh pr edit target not merged", test_session_resolved_via_gh_pr_edit_target_not_merged),
        ("resolved: gh issue close in heredoc not matched", test_session_resolved_issue_close_in_heredoc_not_matched),
        ("unresolved: dangling issue", test_session_unresolved_created_issues_dangling),
        ("unresolved: resolved via merge", test_session_unresolved_created_issues_resolved_via_merge),
        ("unresolved: resolved via explicit close", test_session_unresolved_created_issues_resolved_via_explicit_close),
        ("unresolved: none created", test_session_unresolved_created_issues_none_created),
        ("evaluate_issues: dangling fires", test_evaluate_issues_dangling_fires),
        ("evaluate_issues: with enum resolved", test_evaluate_issues_with_enum_resolved),
        ("evaluate_issues: with skip resolved", test_evaluate_issues_with_skip_resolved),
        ("evaluate_issues: no issue no-op", test_evaluate_issues_no_issue_noop),
        ("evaluate_issues: created+resolved sets sentinel", test_evaluate_issues_created_and_resolved_sets_sentinel),
        ("evaluate_issues: picks lowest deterministically", test_evaluate_issues_picks_lowest_deterministically),
        ("evaluate_issues: bare 'no follow-ups' NOT enum", test_evaluate_issues_bare_no_followups_not_enumeration),
        ("format_issue_reminder: cp1252-encodable", test_format_issue_reminder_is_cp1252_encodable),
        ("combined: merged PR + dangling issue fire independently", test_combined_merged_pr_and_dangling_issue_both_fire_independently),
        ("combined: one enumeration satisfies both", test_combined_one_enumeration_satisfies_both),
        ("e2e dangling issue+no-enum blocks on stderr", test_e2e_dangling_issue_no_enum_blocks_on_stderr),
        ("e2e dangling issue+enum allows", test_e2e_dangling_issue_with_enum_allows),
        ("e2e issue resolved via merge allows", test_e2e_issue_resolved_via_merge_allows),
        ("e2e issue explicit close (no enum) allows", test_e2e_issue_explicit_close_no_enum_still_allows),
        ("e2e no issue created allows", test_e2e_no_issue_created_allows),
        ("e2e combined merged PR + dangling issue: both messages", test_e2e_combined_merged_pr_and_dangling_issue_both_messages),
        ("e2e dangling-issue sentinel suppresses re-fire", test_e2e_dangling_issue_sentinel_suppresses_refire),
        # --- tiles-spawned-without-a-table trigger (ADR-094 addendum, dev-env#656) ---
        ("session_spawned_tiles: true on real spawn", test_session_spawned_tiles_true_on_real_spawn),
        ("session_spawned_tiles: false without spawn", test_session_spawned_tiles_false_without_spawn),
        ("session_spawned_tiles: detects other namespace", test_session_spawned_tiles_detects_other_namespace),
        ("enumeration_recorded: delegates to session_spawned_tiles", test_enumeration_recorded_delegates_to_session_spawned_tiles),
        ("table_marker_present: true on heading", test_table_marker_present_true_on_heading),
        ("table_marker_present: case/level insensitive", test_table_marker_present_case_and_heading_level_insensitive),
        ("table_marker_present: false without heading", test_table_marker_present_false_without_heading),
        ("table_marker_present: prose mention not anchored", test_table_marker_present_prose_mention_not_anchored_does_not_match),
        ("table_marker_present: ignores user record", test_table_marker_present_ignores_user_record),
        ("evaluate_tile_table: fires without marker", test_evaluate_tile_table_fires_without_marker),
        ("evaluate_tile_table: resolved with marker", test_evaluate_tile_table_resolved_with_marker),
        ("evaluate_tile_table: resolved with skip", test_evaluate_tile_table_resolved_with_skip),
        ("evaluate_tile_table: no-op without spawn", test_evaluate_tile_table_noop_without_spawn),
        ("format_table_reminder: cp1252-encodable", test_format_table_reminder_is_cp1252_encodable),
        ("interaction: spawn resolves merge trigger, table trigger still fires", test_spawn_resolves_merge_trigger_but_table_trigger_still_fires),
        ("interaction: spawn-only, no merge/issue -> table trigger fires", test_spawn_only_no_merge_no_issue_table_trigger_fires),
        ("interaction: merge, no spawn -> table trigger no-op", test_merge_no_spawn_table_trigger_is_noop),
        ("interaction: all three triggers fire independently", test_combined_all_three_triggers_fire_independently),
        ("e2e spawn+no-table blocks on stderr", test_e2e_spawn_no_table_blocks_on_stderr),
        ("e2e spawn+table allows", test_e2e_spawn_with_table_allows),
        ("e2e spawn+skip allows", test_e2e_spawn_with_skip_allows),
        ("e2e combined all three, no table: names only the table trigger", test_e2e_combined_all_three_no_table_blocks_naming_table_only),
        ("e2e spawn-table sentinel suppresses re-fire", test_e2e_spawn_table_sentinel_suppresses_refire),
        ("e2e other-namespace spawn + no table still blocks", test_e2e_spawn_other_namespace_no_table_still_blocks),
        # --- per-trigger sentinel independence (ADR-097, dev-env#677) ---
        ("e2e dev-env#677: later trigger still fires after sibling sentinel set", test_e2e_later_trigger_still_fires_after_sibling_sentinel_set),
        ("e2e partial session only sets the fired trigger's sentinel", test_e2e_partial_session_only_sets_the_fired_triggers_sentinel),
        ("e2e fully-compliant session sets all three sentinels, stays allowed", test_e2e_fully_compliant_session_sets_all_three_sentinels_and_stays_allowed),
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
