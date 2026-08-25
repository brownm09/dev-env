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


_TILE_SHARD = "sessions/dev-env/tiles/870.json"


def _asst_write(path, tid="w1"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write", "id": tid, "input": {"file_path": path}}]}}


def _asst_shard_write(tid="ts1"):
    """A tile-shard write, the artifact trigger 3b (ADR-118, dev-env#870) requires.

    Defined here rather than beside that trigger's own tests because a spawned tile now
    obliges a shard write, so every *fully-compliant* fixture in this file needs one --
    including the module-level `_MERGED_DEFERRAL_QUESTION_OTHERWISE_RESOLVED` built above
    those tests. Adding trigger 3b turned several previously-passing e2e fixtures red
    exactly as intended: they spawned a tile and wrote no shard, which is now the
    violation. They were fixed by making the session compliant, never by weakening the
    assertion."""
    return _asst_bash(tid, f"git add {_TILE_SHARD}")


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


def test_gh_api_get_no_put_flag_not_merged():
    # dev-env#992: session_merged_prs now gates the REST-merge branch on
    # _hookio.is_rest_merge_command (requires a same-segment -X PUT/--method PUT
    # flag), not the old bare has_api verb-only check. A gh api call to the
    # identical .../pulls/N/merge path with NO method flag -- gh's default verb
    # is GET, which is GitHub's own documented read-only "check if merged"
    # endpoint -- must not be treated as a completed merge, even if the output
    # happens to contain "merged":true (the real GET endpoint never returns
    # this body -- 204/404 only -- so this is a synthetic worst-case output,
    # not a realistic one; the test still pins that the METHOD check, not just
    # the output shape, is what gates this branch).
    calls = [("gh api repos/brownm09/dev-env/pulls/42/merge",
              '{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}')]
    assert gate.session_merged_prs(calls) == set()
    return "gh api .../pulls/42/merge with NO -X PUT flag -> not merged (dev-env#992)"


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
    # Post-dev-env#685 the URL fallback IS masked, but with mask_prose_flag_values
    # (which masks only --subject/--body/-t/-b values) -- so a BARE quoted PR URL
    # used AS the repo signal (not preceded by a prose flag) is untouched and
    # must keep resolving exactly as before. Assertion unchanged from the
    # pre-#685 "stays unmasked" behavior for this bare-URL shape.
    segment = 'gh pr merge "https://github.com/brownm09/dev-env/pull/42" --squash'
    assert gate._explicit_repo(segment) == "brownm09/dev-env"
    return "bare quoted PR URL fallback still resolves under mask_prose_flag_values (dev-env#685)"


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
    # Mirrors test_explicit_repo_url_fallback_stays_unmasked: post-dev-env#685 the
    # URL check masks with mask_prose_flag_values, which leaves a BARE quoted PR
    # URL (not preceded by --subject/--body) matchable. Assertion unchanged.
    segment = 'gh pr merge "https://github.com/brownm09/dev-env/pull/42" --squash'
    assert gate._target_pr(segment) == 42
    return "bare quoted PR URL fallback still resolves under mask_prose_flag_values (dev-env#685)"


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
# _target_pr / _explicit_repo URL-decoy masking (dev-env#685, ADR-111)
#
# This file's own _PR_URL_RE was the LAST member of the repo-target family
# still searched entirely unmasked. Because the URL check runs FIRST (ahead of
# the positional fallback) in _target_pr, a decoy /pull/N URL in a --subject
# value won even when a REAL positional number was also present -- a strictly
# more severe shape than the bare-number decoy (dev-env#650) above. Routing the
# check through _repo_target lets the call site mask --subject/--body values
# with mask_prose_flag_values first, closing the decoy while leaving a bare
# quoted URL (never preceded by --subject/--body) matchable (see the two
# "url_fallback_stays_..." tests above, whose assertions are unchanged).
# ---------------------------------------------------------------------------

def test_target_pr_url_decoy_in_subject_masked():
    segment = 'gh pr merge --squash --subject "see https://github.com/other/repo/pull/99 for context"'
    assert gate._target_pr(segment) is None
    return "URL decoy in --subject -> None (no real PR number in the command; dev-env#685)"


def test_target_pr_real_number_survives_url_decoy_in_subject():
    # The MORE SEVERE dev-env#685 case: the URL check runs first, so before the
    # fix this decoy won even though a real positional 380 is also present.
    segment = 'gh pr merge 380 --subject "see https://github.com/other/repo/pull/99 for context"'
    assert gate._target_pr(segment) == 380
    return "real positional 380 wins over a URL decoy in --subject (URL check masked; dev-env#685)"


def test_explicit_repo_url_decoy_in_subject_masked():
    segment = 'gh pr merge --squash --subject "see https://github.com/other/repo/pull/99 for context"'
    assert gate._explicit_repo(segment) is None
    return "URL decoy in --subject -> None repo (no real --repo or bare URL; dev-env#685)"


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
    assert fire is None and resolved is True
    return "tile spawned + table heading present -> (False, resolved=True)"


def test_evaluate_tile_table_resolved_with_skip():
    records = _SPAWNED_NO_TABLE + [_user_str("skip tiles")]
    fire, resolved = gate.evaluate_tile_table(records)
    assert fire is None and resolved is True
    return "tile spawned + 'skip tiles' override -> (False, resolved=True)"


def test_evaluate_tile_table_noop_without_spawn():
    records = [_asst_bash("t1", "npm test"), _tool_result("t1", "ok")]
    fire, resolved = gate.evaluate_tile_table(records)
    assert fire is None and resolved is False
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
    assert fire_table is None and resolved_table is False
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
        _asst_shard_write(),
        _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e merged + spawn_task tile + shard + table -> exit 0 (allowed)"


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
        _asst_shard_write(),
        _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e dangling issue + spawn_task tile + shard + table -> exit 0 (allowed)"


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
    # The shard write is required alongside the table since trigger 3b (ADR-118,
    # dev-env#870): a spawned tile must leave BOTH artifacts to reach exit 0.
    records = _SPAWNED_NO_TABLE + [
        _asst_shard_write(),
        _asst_text(_TABLE_HEADING + "\n| Tile | Issue | Status | Next |\n")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e tile spawned + table heading + shard write -> exit 0 (allowed)"


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


def test_e2e_fully_compliant_session_sets_all_blocking_sentinels_and_stays_allowed():
    # A session where every blocking condition is resolved in one turn (the spawn
    # resolves triggers 1/2 via enumeration_recorded; the table heading resolves
    # trigger 3; the shard write resolves trigger 3b) sets all four of those
    # sentinels, and a second Stop with the same transcript stays allowed (stable,
    # no spurious re-fire). Trigger 4's sentinel is deliberately NOT asserted here:
    # it is advisory and its FIRED mark is only set at emission.
    records = _MERGED_NO_ENUM + _ISSUE_CREATED_NO_ENUM + [
        _asst_spawn(),
        _asst_shard_write(),
        _asst_text(_TABLE_HEADING + "\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc1, _, err1 = _run_hook(records, home, session_id="sess-full")
        pr_set = _sentinel_file(home, gate._TRIGGER_PR, "sess-full").exists()
        issue_set = _sentinel_file(home, gate._TRIGGER_ISSUE, "sess-full").exists()
        table_set = _sentinel_file(home, gate._TRIGGER_TABLE, "sess-full").exists()
        shard_set = _sentinel_file(home, gate._TRIGGER_SHARD, "sess-full").exists()
        rc2, out2, err2 = _run_hook(records, home, session_id="sess-full")
    assert rc1 == 0, f"expected exit 0 (fully resolved), got {rc1} (stderr={err1!r})"
    assert pr_set and issue_set and table_set and shard_set, \
        "all four blocking sentinels must be set once fully resolved"
    assert rc2 == 0, f"second Stop expected exit 0 (stable), got {rc2} (stderr={err2!r})"
    return ("fully-compliant session -> all four blocking per-trigger sentinels set; "
            "a later Stop with the same transcript stays allowed")


def test_e2e_stale_merged_text_does_not_block_a_later_distinct_trigger():
    # Review of PR #693: the pre-filter's "merged"/"issue create"/"spawn_task"
    # substring checks are gated on each trigger's OWN already_done state, since
    # a transcript signal never disappears once written -- without the gate, a
    # session with an early merge would carry "merged" in its transcript for the
    # rest of the session, defeating the pre-filter's short-circuit for every
    # later Stop even after the merge trigger resolves. Three turns: turn 1
    # merges (fires+resolves trigger 1); turn 2 is a pure continuation with no
    # new signal at all (must stay allowed); turn 3 -- much later, with trigger
    # 1's stale "merged" text still present throughout -- spawns a tile with no
    # table (trigger 3 must still fire, proving the stale signal never poisons
    # detection of a later, genuinely new, different trigger).
    records_turn1 = _MERGED_NO_ENUM
    records_turn2 = _MERGED_NO_ENUM + [
        _asst_bash("t2", "npm test"), _tool_result("t2", "All tests passed")]
    records_turn3 = records_turn2 + [
        _asst_spawn(), _asst_text("Filed a follow-up tile for the flaky test.")]
    with tempfile.TemporaryDirectory() as home:
        rc1, _, err1 = _run_hook(records_turn1, home, session_id="sess-stale-merged")
        rc2, out2, err2 = _run_hook(records_turn2, home, session_id="sess-stale-merged")
        rc3, out3, err3 = _run_hook(records_turn3, home, session_id="sess-stale-merged")
    assert rc1 == 2, f"turn 1 (merge, no enum) expected exit 2, got {rc1} (stderr={err1!r})"
    assert rc2 == 0, f"turn 2 (no new signal) expected exit 0, got {rc2} (stderr={err2!r})"
    assert rc3 == 2, (
        "turn 3 (tile spawned, no table) expected exit 2 despite turn 1's stale "
        f"'merged' text still being present, got {rc3} (stderr={err3!r})")
    assert "Tiles spawned this session" in err3
    assert "#599" not in err3, "trigger 1 already resolved -- must not re-fire"
    return ("e2e stale 'merged' text from an already-resolved trigger 1 does not "
            "block detection of trigger 3 arising three turns later")


# ---------------------------------------------------------------------------
# deferral-question trigger (new ADR, dev-env#772)
# ---------------------------------------------------------------------------

# A minimal "PR merged, deferral question asked, nothing tiled" session --
# the motivating incident's shape (a known follow-up asked about, not tiled).
_MERGED_DEFERRAL_QUESTION = [
    _asst_bash("t1", "gh pr merge 762 --squash --delete-branch"),
    _tool_result("t1", "Squashed and merged pull request #762 (PR9)"),
    _asst_text("Merged PR #762. PR10 is next -- let me know if you want me "
               "to start it now or leave it for a fresh session."),
]

# The same session, but with OTHER genuine follow-ups properly tiled AND
# tabled -- fully resolves triggers 1/3 (2 doesn't apply, no issue created)
# via a real spawn_task + the table heading, leaving ONLY the deferral
# trigger (4) live. This is the actual motivating incident's shape: a spawn
# without a table heading would otherwise (correctly) arm trigger 3 too,
# which is not what these tests are isolating.
_MERGED_DEFERRAL_QUESTION_OTHERWISE_RESOLVED = _MERGED_DEFERRAL_QUESTION + [
    _asst_spawn(),
    _asst_shard_write(),
    _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"
               "| other follow-up | #761 | open | click the chip |\n"),
]


def test_deferral_question_detected_let_me_know():
    assert gate.deferral_question_present(_MERGED_DEFERRAL_QUESTION)
    return "'let me know if you want me to start it now' -> deferral_question_present True"


def test_deferral_question_detected_should_i_start():
    records = [_asst_text("Should I start implementing this now?")]
    assert gate.deferral_question_present(records)
    return "'Should I start implementing this now?' -> deferral_question_present True"


def test_deferral_question_detected_want_me_to_now():
    records = [_asst_text("Want me to implement this now, or should it wait?")]
    assert gate.deferral_question_present(records)
    return "'Want me to implement this now' -> deferral_question_present True"


def test_deferral_question_detected_apostrophe_d_like_contraction():
    # Regression pin (review finding): a first draft's regex put the
    # required space BEFORE the alternation group (`you (?:want|'d like)
    # me to`), which only matches the unnatural "you 'd like" (with a
    # space before the apostrophe) -- the real contraction "you'd like"
    # (no space) is one of the two phrasings ADR-109 documents this
    # trigger as targeting and was silently undetectable.
    records = [_asst_text("Let me know if you'd like me to start it now.")]
    assert gate.deferral_question_present(records), (
        "the natural contraction \"you'd like\" (no space before the apostrophe) must match")
    return "\"let me know if you'd like me to\" (natural contraction) -> detected"


def test_deferral_question_not_detected_unrelated_design_question():
    # "Should I use approach A or B" is a genuine design question, not one of
    # the bounded verb phrasings (start/begin/implement/proceed/go
    # ahead/do this/tackle) -- must not match.
    records = [_asst_text("Should I use approach A or approach B for the cache layer?")]
    assert not gate.deferral_question_present(records)
    return "unrelated design question ('should I use...') -> NOT detected"


def test_deferral_question_ignores_user_record():
    records = [_user_str("let me know if you want me to start it now")]
    assert not gate.deferral_question_present(records)
    return "deferral phrase in a USER record -> NOT present (assistant-only scope)"


def test_deferral_question_ignores_tool_result():
    records = [_tool_result("t1", "let me know if you want me to start it now")]
    assert not gate.deferral_question_present(records)
    return "deferral phrase in a tool_result -> NOT present (assistant-text-only scope)"


def test_evaluate_deferral_fires_after_merge():
    fire, resolved = gate.evaluate_deferral(_MERGED_DEFERRAL_QUESTION)
    assert fire is True and resolved is False
    return "merged + deferral phrase + no skip -> (True, resolved=False) [FIRE]"


def test_evaluate_deferral_fires_after_issue_create():
    records = [
        _asst_bash("i1", 'gh issue create --title "PR10 sub-issue" --body "part of #717"'),
        _tool_result("i1", "https://github.com/brownm09/dev-env/issues/768"),
        _asst_text("Filed the sub-issue. Should I start implementing it now?"),
    ]
    fire, resolved = gate.evaluate_deferral(records)
    assert fire is True and resolved is False
    return "issue created + deferral phrase + no skip -> (True, resolved=False) [FIRE]"


def test_evaluate_deferral_resolved_with_skip():
    records = _MERGED_DEFERRAL_QUESTION + [_user_str("skip tiles")]
    fire, resolved = gate.evaluate_deferral(records)
    assert fire is None and resolved is True
    return "merged + deferral phrase + 'skip tiles' override -> (False, resolved=True)"


def test_evaluate_deferral_not_resolved_by_unrelated_enumeration():
    # THE fix this trigger's design specifically targets: an unrelated
    # spawn_task / enumeration elsewhere in the session must NOT resolve
    # this trigger -- the deferred item itself was never tiled. Reproduces
    # the actual motivating incident's shape (genuine tiles spawned in the
    # same session the deferral question was asked about a DIFFERENT item).
    records = _MERGED_DEFERRAL_QUESTION + [
        _asst_spawn("s1"), _asst_text("Also filed a tile for the flaky test.")]
    assert gate.enumeration_recorded(records), (
        "sanity check: the spawn DOES satisfy enumeration_recorded")
    fire, resolved = gate.evaluate_deferral(records)
    assert fire is True and resolved is False
    return ("merged + deferral phrase + an UNRELATED spawn_task elsewhere -> still "
            "(True, resolved=False) -- enumeration_recorded is deliberately NOT "
            "accepted as resolution here (see evaluate_deferral's docstring)")


def test_evaluate_deferral_noop_without_merge_or_issue():
    records = [_asst_text("Should I start implementing this now?")]
    fire, resolved = gate.evaluate_deferral(records)
    assert fire is None and resolved is False
    return "deferral phrase with no merge/issue-create context -> (False, resolved=False)"


def test_evaluate_deferral_noop_without_phrase():
    fire, resolved = gate.evaluate_deferral(_MERGED_NO_ENUM)
    assert fire is None and resolved is False
    return "merged, no deferral phrase -> (False, resolved=False)"


def test_format_deferral_reminder_is_cp1252_encodable():
    msg = gate.format_deferral_reminder()
    assert msg.isascii(), "reminder must be ASCII (Claude Code pipes hook output as cp1252)"
    msg.encode("cp1252")  # must not raise
    assert "tile-now discipline" in msg and "spawn_task" in msg
    return "format_deferral_reminder is ASCII/cp1252-encodable"


def test_deferral_trigger_independent_of_resolved_merge_trigger():
    # Merge trigger (1) resolves via a genuine spawn; the deferral trigger is
    # a fully separate evaluation and still fires on its own phrase.
    records = _MERGED_DEFERRAL_QUESTION_OTHERWISE_RESOLVED
    fire_pr, resolved_pr = gate.evaluate(records)
    fire_defer, resolved_defer = gate.evaluate_deferral(records)
    assert fire_pr is None and resolved_pr is True
    assert fire_defer is True and resolved_defer is False
    return "spawn resolves the merge trigger (1) but the deferral trigger (4) still fires"


# ---------------------------------------------------------------------------
# behavioral layer -- deferral-question trigger (new ADR, dev-env#772)
# ---------------------------------------------------------------------------

def test_e2e_deferral_only_fires_advisory_systemmessage_exit_0():
    # Merge trigger (1) is silently resolved by a genuine spawn (enumeration),
    # and trigger 3 is resolved by the table heading -- reproduces the
    # motivating incident exactly: other real follow-ups were correctly
    # tiled AND tabled, yet the deferral question about a DIFFERENT item
    # still surfaces, via the non-blocking advisory channel.
    records = _MERGED_DEFERRAL_QUESTION_OTHERWISE_RESOLVED
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0 (advisory only, not blocking), got {rc} (stderr={err!r})"
    assert err.strip() == "", f"stderr must be empty on the advisory path, got {err!r}"
    payload = json.loads(out)
    assert payload.get("systemMessage"), f"expected a systemMessage payload, got {out!r}"
    assert "tile-now discipline" in payload["systemMessage"]
    return ("e2e merge resolved via spawn (trigger 1 silent) + deferral phrase about a "
            "DIFFERENT item -> exit 0 with a systemMessage advisory, not a block")


def test_e2e_blocking_trigger_takes_precedence_over_deferral_advisory():
    # Merge trigger (1) is UN-enumerated (no spawn at all) -- it blocks via
    # exit 2. The deferral phrase is ALSO present. Only one exit code is
    # possible per invocation (the channel is coupled to it, _hookout.py) --
    # the harder blocking enforcement wins this turn; the advisory is
    # skipped, not lost forever (a persisting condition can still surface on
    # a later Stop once trigger 1 resolves).
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_MERGED_DEFERRAL_QUESTION, home)
    assert rc == 2, f"expected exit 2 (blocking trigger wins), got {rc} (stderr={err!r})"
    assert "#762" in err  # trigger 1's reminder names the merged PR
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return ("e2e merge un-enumerated (blocking) + deferral phrase present -> exit 2 "
            "names the merge trigger; the advisory is not ALSO emitted this turn")


def test_e2e_no_deferral_phrase_no_systemmessage():
    # Cleanly resolved (spawn + shard + table heading), no deferral phrase anywhere.
    records = _MERGED_NO_ENUM + [
        _asst_spawn(),
        _asst_shard_write(),
        _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    assert out.strip() == "", (
        f"expected no systemMessage when no deferral phrase present, got {out!r}")
    return "e2e cleanly-resolved session with no deferral phrase -> exit 0, no systemMessage"


def test_e2e_deferral_should_i_phrasing_after_issue_create():
    # Exercises the "should i " pre-filter substring / regex branch and the
    # issue-create scoping path end-to-end (the pure-test layer above covers
    # both already; this proves main()'s pre-filter doesn't exclude them).
    # The issue is resolved via an explicit close (trigger 2) and the spawn
    # is tabled (trigger 3), leaving ONLY the deferral trigger live.
    records = [
        _asst_bash("i1", 'gh issue create --title "PR10 sub-issue" --body "part of #717"'),
        _tool_result("i1", "https://github.com/brownm09/dev-env/issues/768"),
        _asst_text("Filed the sub-issue. Should I start implementing it now?"),
        _asst_bash("i2", "gh issue close 768"),
        _tool_result("i2", "Closed issue #768"),
        _asst_spawn(),
        _asst_shard_write(),
        _asst_text("### Tiles spawned this session\n| Tile | Issue | Status | Next |\n"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0 (advisory only), got {rc} (stderr={err!r})"
    payload = json.loads(out)
    assert payload.get("systemMessage"), f"expected a systemMessage payload, got {out!r}"
    return "e2e issue-create + 'should I start...now' phrasing -> exit 0 with advisory"


def test_e2e_deferral_sentinel_suppresses_refire():
    records = _MERGED_DEFERRAL_QUESTION_OTHERWISE_RESOLVED
    with tempfile.TemporaryDirectory() as home:
        rc1, out1, _ = _run_hook(records, home, session_id="sess-defer-refire")
        rc2, out2, _ = _run_hook(records, home, session_id="sess-defer-refire")
    assert rc1 == 0 and json.loads(out1).get("systemMessage"), (
        f"first run expected an advisory, got rc={rc1} out={out1!r}")
    assert rc2 == 0 and out2.strip() == "", (
        f"second run expected a silent exit 0 (sentinel), got rc={rc2} out={out2!r}")
    return "e2e deferral sentinel: first fire emits the advisory, second stays silent"


def test_e2e_deferral_advisory_resurfaces_after_blocking_trigger_resolves():
    # Regression pin (review finding, independently confirmed by two review
    # passes): the deferral trigger's advisory must not be permanently lost
    # when a blocking trigger co-fires the same turn. Turn 1: an
    # UN-enumerated merge (blocks via trigger 1) with the deferral phrase
    # also present -- the advisory is preempted this turn (exit 2, trigger
    # 1's reminder only), but the fix requires the defer- sentinel to stay
    # UNSET here (a first draft set it unconditionally in the same pass as
    # fire_defer, silently consuming trigger 4's one fire for the rest of
    # the session without ever having delivered it). Turn 2: the SAME
    # transcript, now with trigger 1 resolved via a spawn + table heading --
    # the deferral phrase (and the underlying merge context) is still
    # present, so the advisory must now surface via the systemMessage
    # channel, exactly as the module docstring and ADR-109 both promise
    # ("can still surface on a later Stop once the harder trigger
    # resolves").
    records_turn1 = _MERGED_DEFERRAL_QUESTION
    records_turn2 = _MERGED_DEFERRAL_QUESTION_OTHERWISE_RESOLVED
    with tempfile.TemporaryDirectory() as home:
        rc1, out1, err1 = _run_hook(records_turn1, home, session_id="sess-defer-resurface")
        defer_set_after_turn1 = _sentinel_file(
            home, gate._TRIGGER_DEFER, "sess-defer-resurface").exists()
        rc2, out2, err2 = _run_hook(records_turn2, home, session_id="sess-defer-resurface")
    assert rc1 == 2, f"turn 1 (blocking merge trigger) expected exit 2, got {rc1} (stderr={err1!r})"
    assert "#762" in err1
    assert not defer_set_after_turn1, (
        "the defer- sentinel must NOT be set after turn 1 -- the advisory was preempted by "
        "the blocking trigger and never actually delivered; marking it here would silently "
        "and permanently suppress trigger 4 for the rest of the session")
    assert rc2 == 0, f"turn 2 (trigger 1 resolved) expected exit 0, got {rc2} (stderr={err2!r})"
    payload2 = json.loads(out2)
    assert payload2.get("systemMessage"), (
        f"turn 2 expected the deferral advisory to resurface, got {out2!r}")
    return ("e2e: a blocking trigger co-firing with the deferral trigger does NOT permanently "
            "consume trigger 4's fire -- the advisory resurfaces once the blocking trigger "
            "resolves on a later Stop")


# ---------------------------------------------------------------------------
# trigger 3b: tile spawned without its shard (ADR-118, dev-env#870)
# ---------------------------------------------------------------------------

# A tile spawned with no shard write anywhere -- the state trigger 3b exists to catch.
_SPAWNED_NO_SHARD = [
    _asst_spawn("s1"),
    _asst_text("### Tiles spawned this session\n\n| Tile | Issue |\n"),
]


def test_tile_shard_write_present_via_write_tool():
    # The write recipe explicitly offers the Write tool as an equal alternative to the
    # shell serializer, so a Write file_path must count as evidence.
    assert gate.tile_shard_write_present([_asst_write(
        "C:/Users/brown/Git/engineering-journal/" + _TILE_SHARD)])
    return "a Write tool_use naming a tiles/<N>.json path -> shard write present"


def test_tile_shard_write_present_via_bash_command():
    assert gate.tile_shard_write_present([_asst_bash("t1", f"git add {_TILE_SHARD}")])
    return "a Bash command naming a tiles/<N>.json path -> shard write present"


def test_tile_shard_write_present_via_bash_output_only():
    # THE load-bearing case. The documented recipe writes shards through a serializer
    # SCRIPT, and a multi-tile session naturally writes one script that emits all of
    # them -- so the command text names only the script, and the shard paths appear
    # solely in its OUTPUT. Observed live in the session that shipped the reader: three
    # shards written by one `py -3 <script>` call whose command contained no tiles/ path
    # at all. An input-only scan would have blocked that session for a write it did
    # correctly perform.
    records = [
        _asst_bash("t1", "py -3 C:/Users/brown/scratch/write_tile_shards.py"),
        _tool_result("t1", r"wrote C:\Users\brown\Git\engineering-journal\sessions"
                           r"\dev-env\tiles\870.json  (4321 chars of prompt)"),
    ]
    assert gate.tile_shard_write_present(records)
    return "a shard path appearing only in Bash OUTPUT still counts (serializer-script recipe)"


def test_tile_shard_write_present_false_without_evidence():
    assert not gate.tile_shard_write_present(_SPAWNED_NO_SHARD)
    return "a spawn with no tiles/<N>.json anywhere -> no shard write present"


def test_tile_shard_write_present_ignores_non_numeric_and_other_dirs():
    # `tiles/index.json` is not a shard (the filename IS the issue key), and an
    # open-prs path must not be mistaken for one.
    records = [
        _asst_bash("t1", "git add sessions/dev-env/tiles/index.json"),
        _asst_bash("t2", "git add sessions/dev-env/open-prs/884.json"),
    ]
    assert not gate.tile_shard_write_present(records)
    return "non-numeric tiles/ filenames and open-prs/ paths are not shard-write evidence"


def test_evaluate_tile_shard_fires_without_shard():
    fire, resolved = gate.evaluate_tile_shard(_SPAWNED_NO_SHARD)
    assert fire is True and resolved is False
    return "tile spawned, no shard write -> fires"


def test_evaluate_tile_shard_resolved_with_shard():
    records = _SPAWNED_NO_SHARD + [_asst_bash("t9", f"git add {_TILE_SHARD}")]
    fire, resolved = gate.evaluate_tile_shard(records)
    assert fire is None and resolved is True
    return "tile spawned + shard written -> resolved, sentinel set"


def test_evaluate_tile_shard_resolved_with_skip_override():
    records = _SPAWNED_NO_SHARD + [_user_str("skip tiles for this one")]
    fire, resolved = gate.evaluate_tile_shard(records)
    assert fire is None and resolved is True
    return "an explicit 'skip tiles' user override waives the shard trigger"


def test_evaluate_tile_shard_noop_without_spawn():
    fire, resolved = gate.evaluate_tile_shard(_MERGED_NO_ENUM)
    assert fire is None and resolved is False
    return "no spawn this session -> (False, False), so a later spawn is still caught"


def test_shard_and_table_triggers_are_independent():
    # A session that spawns a tile and emits the table but writes NO shard must resolve
    # trigger 3 while trigger 3b still fires -- they guard different losses (the table
    # tells the user a tile exists; the shard is what lets the tile be re-spawned).
    fire_table, resolved_table = gate.evaluate_tile_table(_SPAWNED_NO_SHARD)
    fire_shard, resolved_shard = gate.evaluate_tile_shard(_SPAWNED_NO_SHARD)
    assert (fire_table, resolved_table) == (None, True), "table present -> trigger 3 resolved"
    assert (fire_shard, resolved_shard) == (True, False), "shard absent -> trigger 3b fires"
    return "table and shard are independent bars on the same spawn (3 resolves, 3b fires)"


def test_shard_trigger_not_satisfied_by_enumeration_text_alone():
    # Mirrors the trigger-3 asymmetry: prescribed enumeration TEXT satisfies triggers
    # 1/2, but must not by itself satisfy the shard bar -- only a real write does.
    records = [_asst_spawn("s1"),
               _asst_text("### Tiles spawned this session\n\nFollow-ups considered: "
                          "one item -> tiled (#870)")]
    fire, _resolved = gate.evaluate_tile_shard(records)
    assert fire is True
    return "enumeration text alone does not satisfy the shard trigger -- only a write does"


def test_format_shard_reminder_is_ascii_and_actionable():
    text = gate.format_shard_reminder()
    assert text.isascii(), "exit-2 stderr is cp1252-decoded on Windows"
    assert "tiles/<issue-number>.json" in text
    assert "TARGET project" in text, "filing under the wrong project is the common mistake"
    assert "never" in text and "echo" in text, "must warn against interpolating the prompt"
    assert "skip tiles" in text
    return "the shard reminder is ASCII, names the path, the target-project rule, and the escape hatch"


# --- trigger-table structural gate (dev-env#696) ------------------------------
# These pin the invariants the _TriggerSpec table exists to guarantee. Before it,
# a trigger's identity lived in five hand-synchronized sites and two of them
# disagreed on shape (a (None, False) skip-default and an `is not None` fired
# test for triggers 1/2, against (False, False) and bare truthiness for the
# rest). Nothing failed when they drifted -- that is what these tests fix.


def test_triggers_tuple_is_derived_from_spec_table():
    assert gate._TRIGGERS == tuple(spec.key for spec in gate._TRIGGER_SPECS)
    # every sentinel suffix distinct: two specs sharing one would make each
    # silently resolve the other's condition
    assert len(set(gate._TRIGGERS)) == len(gate._TRIGGERS)
    expected = {
        gate._TRIGGER_PR, gate._TRIGGER_ISSUE, gate._TRIGGER_TABLE,
        gate._TRIGGER_SHARD, gate._TRIGGER_DEFER,
    }
    assert set(gate._TRIGGERS) == expected
    return "_TRIGGERS derives from _TRIGGER_SPECS; %d distinct keys" % len(gate._TRIGGERS)


def test_every_evaluator_honors_the_payload_contract():
    # An empty transcript has no signal for ANY trigger, so every evaluator must
    # report not-firing the same way: payload is None (never False, never 0).
    for spec in gate._TRIGGER_SPECS:
        payload, resolved = spec.evaluate([])
        assert payload is None, "%s: no-signal payload must be None, got %r" % (spec.key, payload)
        assert resolved is False, "%s: no-signal resolved must be False, got %r" % (spec.key, resolved)
    return "all %d evaluators return (None, False) on an empty transcript" % len(gate._TRIGGER_SPECS)


def test_every_formatter_takes_one_payload_and_is_ascii():
    # Uniform (payload) -> str, so main()'s message loop can call every formatter
    # identically. ASCII because Claude Code pipes hook output as cp1252 on
    # Windows -- a non-ASCII char would raise and the whole message would vanish.
    for spec in gate._TRIGGER_SPECS:
        text = spec.formatter(599)
        assert isinstance(text, str) and text, "%s: formatter returned %r" % (spec.key, text)
        assert text.isascii(), "%s: formatter emitted non-ASCII" % spec.key
    return "all %d formatters accept one payload and return ASCII" % len(gate._TRIGGER_SPECS)


def test_exactly_one_advisory_trigger():
    # main() delivers at most one advisory per Stop (emit_advisory is NoReturn).
    # A second non-blocking spec would silently make that a real limitation
    # rather than a theoretical one -- fail here so it is a deliberate decision.
    advisory = [spec.key for spec in gate._TRIGGER_SPECS if not spec.blocking]
    assert advisory == [gate._TRIGGER_DEFER], "expected only the deferral trigger advisory, got %r" % advisory
    return "exactly one advisory trigger (%s); the rest block via exit 2" % gate._TRIGGER_DEFER


def test_prefilter_is_a_superset_of_each_evaluator():
    # The prefilter exists to skip the transcript parse. It must never skip a
    # turn its evaluator would have fired on, so for a transcript that DOES fire
    # a trigger, that trigger's prefilter must return True.
    ctx_for = lambda text: gate._PrefilterCtx(
        text=text,
        lower=text.lower(),
        has_merge_or_issue_signal=True,
    )
    firing = {
        gate._TRIGGER_PR: _MERGED_NO_ENUM,
        gate._TRIGGER_ISSUE: _ISSUE_CREATED_NO_ENUM,
        gate._TRIGGER_TABLE: _SPAWNED_NO_SHARD,
        gate._TRIGGER_SHARD: _SPAWNED_NO_SHARD,
        gate._TRIGGER_DEFER: _MERGED_DEFERRAL_QUESTION,
    }
    checked = 0
    for spec in gate._TRIGGER_SPECS:
        records = firing[spec.key]
        payload, _resolved = spec.evaluate(records)
        if payload is None:
            continue  # fixture does not fire this trigger; nothing to assert
        text = chr(10).join(json.dumps(r) for r in records)
        assert spec.prefilter(ctx_for(text)), (
            "%s: evaluator fires but prefilter would have skipped the parse" % spec.key
        )
        checked += 1
    assert checked >= 4, "expected at least 4 firing fixtures, checked %d" % checked
    return "prefilter admits every transcript its evaluator fires on (%d checked)" % checked


def main():
    tests = [
        ("direct merge marker detected", test_direct_merge_marker_detected),
        ("gh api merge detected", test_gh_api_merge_detected),
        ("gh api GET (no -X PUT) not merged", test_gh_api_get_no_put_flag_not_merged),
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
        ("_target_pr: URL decoy in --subject masked (dev-env#685)", test_target_pr_url_decoy_in_subject_masked),
        ("_target_pr: real number survives URL decoy in --subject (dev-env#685)", test_target_pr_real_number_survives_url_decoy_in_subject),
        ("_explicit_repo: URL decoy in --subject masked (dev-env#685)", test_explicit_repo_url_decoy_in_subject_masked),
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
        ("shard write present: via Write tool", test_tile_shard_write_present_via_write_tool),
        ("shard write present: via Bash command", test_tile_shard_write_present_via_bash_command),
        ("shard write present: via Bash OUTPUT only (serializer recipe)", test_tile_shard_write_present_via_bash_output_only),
        ("shard write present: false without evidence", test_tile_shard_write_present_false_without_evidence),
        ("shard write present: ignores non-numeric / open-prs paths", test_tile_shard_write_present_ignores_non_numeric_and_other_dirs),
        ("evaluate_tile_shard: fires without shard", test_evaluate_tile_shard_fires_without_shard),
        ("evaluate_tile_shard: resolved with shard", test_evaluate_tile_shard_resolved_with_shard),
        ("evaluate_tile_shard: resolved with skip override", test_evaluate_tile_shard_resolved_with_skip_override),
        ("evaluate_tile_shard: no-op without spawn", test_evaluate_tile_shard_noop_without_spawn),
        ("shard vs table triggers are independent", test_shard_and_table_triggers_are_independent),
        ("shard trigger not satisfied by enumeration text", test_shard_trigger_not_satisfied_by_enumeration_text_alone),
        ("format_shard_reminder: ASCII + actionable", test_format_shard_reminder_is_ascii_and_actionable),
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
        ("e2e fully-compliant session sets all blocking sentinels, stays allowed", test_e2e_fully_compliant_session_sets_all_blocking_sentinels_and_stays_allowed),
        ("e2e stale 'merged' text does not block a later distinct trigger", test_e2e_stale_merged_text_does_not_block_a_later_distinct_trigger),
        # --- deferral-question trigger (new ADR, dev-env#772) ---
        ("deferral_question: detects 'let me know if you want me to'", test_deferral_question_detected_let_me_know),
        ("deferral_question: detects 'should I start...now'", test_deferral_question_detected_should_i_start),
        ("deferral_question: detects 'want me to...now'", test_deferral_question_detected_want_me_to_now),
        ("deferral_question: detects natural \"you'd like\" contraction (regression pin)", test_deferral_question_detected_apostrophe_d_like_contraction),
        ("deferral_question: unrelated design question NOT detected", test_deferral_question_not_detected_unrelated_design_question),
        ("deferral_question: ignores user record", test_deferral_question_ignores_user_record),
        ("deferral_question: ignores tool_result", test_deferral_question_ignores_tool_result),
        ("evaluate_deferral: fires after merge", test_evaluate_deferral_fires_after_merge),
        ("evaluate_deferral: fires after issue create", test_evaluate_deferral_fires_after_issue_create),
        ("evaluate_deferral: resolved with skip override", test_evaluate_deferral_resolved_with_skip),
        ("evaluate_deferral: NOT resolved by unrelated enumeration (regression pin)", test_evaluate_deferral_not_resolved_by_unrelated_enumeration),
        ("evaluate_deferral: no-op without merge/issue context", test_evaluate_deferral_noop_without_merge_or_issue),
        ("evaluate_deferral: no-op without phrase", test_evaluate_deferral_noop_without_phrase),
        ("format_deferral_reminder: cp1252-encodable", test_format_deferral_reminder_is_cp1252_encodable),
        ("interaction: deferral trigger independent of resolved merge trigger", test_deferral_trigger_independent_of_resolved_merge_trigger),
        ("e2e deferral-only fires advisory systemMessage, exit 0", test_e2e_deferral_only_fires_advisory_systemmessage_exit_0),
        ("e2e blocking trigger takes precedence over deferral advisory", test_e2e_blocking_trigger_takes_precedence_over_deferral_advisory),
        ("e2e no deferral phrase -> no systemMessage", test_e2e_no_deferral_phrase_no_systemmessage),
        ("e2e deferral 'should i' phrasing after issue create", test_e2e_deferral_should_i_phrasing_after_issue_create),
        ("e2e deferral sentinel suppresses re-fire", test_e2e_deferral_sentinel_suppresses_refire),
        ("e2e deferral advisory resurfaces after blocking trigger resolves (regression pin)", test_e2e_deferral_advisory_resurfaces_after_blocking_trigger_resolves),
        ("trigger table: _TRIGGERS derived from _TRIGGER_SPECS (dev-env#696)", test_triggers_tuple_is_derived_from_spec_table),
        ("trigger table: every evaluator honors the (payload, resolved) contract (dev-env#696)", test_every_evaluator_honors_the_payload_contract),
        ("trigger table: every formatter takes one payload, returns ASCII (dev-env#696)", test_every_formatter_takes_one_payload_and_is_ascii),
        ("trigger table: exactly one advisory trigger (dev-env#696)", test_exactly_one_advisory_trigger),
        ("trigger table: prefilter is a superset of each evaluator (dev-env#696)", test_prefilter_is_a_superset_of_each_evaluator),
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
