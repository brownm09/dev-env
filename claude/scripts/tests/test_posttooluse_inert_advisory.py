#!/usr/bin/env python3
"""Unit tests for posttooluse-inert-advisory.py — the Stop-hook safety net that
surfaces inert PostToolUse hooks (ADR-053 / ADR-055).

In background / `spawn_task`-launched sessions, every PostToolUse hook is silently
inert (upstream Claude Code limitation; ADR-053). This Stop hook reads the
just-ended transcript and, when a dev-env (project #3) `gh issue/pr create` or
`gh pr merge` ran but **no** PostToolUse hook left an `attachment` record all
session, emits a one-line advisory pointing at the manual fallback. It never
blocks (stdout, exit 0).

These tests exercise the pure helpers offline against synthetic transcript
records (no stdin, no network, no gh, no disk) — matching the repo's fixture-only
convention. The thin I/O in `main()` (stdin parse, transcript locate, sentinel
write) is not covered.

Usage:
    py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "posttooluse-inert-advisory.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable when
# exec_module runs the module body.
sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("posttooluse_inert_advisory", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

posttooluse_attachment_present = mod.posttooluse_attachment_present
iter_bash_calls = mod.iter_bash_calls
detect_board_actions = mod.detect_board_actions
should_emit = mod.should_emit
_result_text = mod._result_text
_is_devenv_cwd = mod._is_devenv_cwd
_devenv_merge_pr = mod._devenv_merge_pr
format_advisory = mod.format_advisory

DEVENV_ISSUE_URL = "https://github.com/brownm09/dev-env/issues/390"
DEVENV_PR_URL = "https://github.com/brownm09/dev-env/pull/241"
OTHER_REPO_URL = "https://github.com/brownm09/lifting-logbook/issues/5"
DEVENV_CWD = "C:/Users/brown/Git/dev-env/.claude/worktrees/kind-proskuriakova-142429"
# The harmless issue-#275 worktree merge tail (PR merged; local branch undeletable).
WORKTREE_MERGE_TAIL = (
    "Exit code 1\nfailed to delete local branch config/x: failed to run git: "
    "error: Cannot delete branch 'config/x' checked out at 'C:/Users/brown/Git/dev-env'"
)


# --- record builders -----------------------------------------------------------

def bash_use(tid: str, command: str, cwd: str = DEVENV_CWD) -> dict:
    return {
        "type": "assistant",
        "cwd": cwd,
        "message": {
            "content": [
                {"type": "tool_use", "id": tid, "name": "Bash",
                 "input": {"command": command}}
            ]
        },
    }


def tool_result(tid: str, output: str, tur: dict | None = None) -> dict:
    rec = {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tid, "content": output}]
        },
    }
    if tur is not None:
        rec["toolUseResult"] = tur
    return rec


def attachment(inner_type: str, hook_event: str) -> dict:
    return {"type": "attachment",
            "attachment": {"type": inner_type, "hookEvent": hook_event}}


# --- posttooluse_attachment_present -------------------------------------------

def test_attachment_present_hook_success() -> str:
    recs = [attachment("hook_success", "PostToolUse")]
    assert posttooluse_attachment_present(recs) is True
    return "hook_success/PostToolUse attachment -> present (exit-0 hook fired)"


def test_attachment_present_blocking_error() -> str:
    # post-tool-use.py exits 2 -> hook_blocking_error, hookEvent PostToolUse.
    recs = [attachment("hook_blocking_error", "PostToolUse")]
    assert posttooluse_attachment_present(recs) is True
    return "hook_blocking_error/PostToolUse attachment -> present (exit-2 hook fired)"


def test_attachment_absent_other_events() -> str:
    recs = [
        attachment("hook_success", "Stop"),
        attachment("hook_system_message", "UserPromptSubmit"),
    ]
    assert posttooluse_attachment_present(recs) is False
    return "only Stop/UserPromptSubmit attachments -> PostToolUse absent (the inert signature)"


def test_attachment_absent_none() -> str:
    assert posttooluse_attachment_present([]) is False
    assert posttooluse_attachment_present([{"type": "assistant"}]) is False
    return "no attachment records -> absent (no crash)"


# --- iter_bash_calls -----------------------------------------------------------

def test_pairs_by_id() -> str:
    recs = [
        bash_use("t1", "gh issue create --repo brownm09/dev-env --title x"),
        tool_result("t1", DEVENV_ISSUE_URL),
    ]
    calls = iter_bash_calls(recs)
    assert calls == [("gh issue create --repo brownm09/dev-env --title x",
                      DEVENV_ISSUE_URL, DEVENV_CWD)], f"got {calls!r}"
    return "tool_use paired with its tool_result output by tool_use_id (+cwd)"


def test_pairs_parallel_not_mismatched() -> str:
    # Two parallel calls; id-pairing must not cross the wires.
    recs = [
        bash_use("a", "echo A"),
        bash_use("b", "echo B"),
        tool_result("b", "B-out"),
        tool_result("a", "A-out"),
    ]
    calls = dict((c, o) for c, o, _ in iter_bash_calls(recs))
    assert calls == {"echo A": "A-out", "echo B": "B-out"}, f"got {calls!r}"
    return "parallel tool calls pair by id, not by adjacency"


def test_unmatched_result_skipped() -> str:
    recs = [tool_result("orphan", "no command for me")]
    assert iter_bash_calls(recs) == []
    return "tool_result with no matching tool_use is skipped"


# --- _result_text --------------------------------------------------------------

def test_result_text_string_content() -> str:
    item = {"type": "tool_result", "tool_use_id": "t", "content": DEVENV_PR_URL}
    assert _result_text(item, {}) == DEVENV_PR_URL
    return "string tool_result content is returned verbatim"


def test_result_text_list_content() -> str:
    item = {"type": "tool_result", "tool_use_id": "t",
            "content": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]}
    assert _result_text(item, {}) == "line1\nline2"
    return "list tool_result content joins its text parts"


def test_result_text_tooluseresult_fallback() -> str:
    # Empty per-id content -> fall back to the record's structured stdout+stderr.
    item = {"type": "tool_result", "tool_use_id": "t", "content": ""}
    rec = {"toolUseResult": {"stdout": "out", "stderr": "warn"}}
    assert _result_text(item, rec) == "out\nwarn"
    return "empty content falls back to toolUseResult stdout+stderr"


# --- detect_board_actions ------------------------------------------------------

def test_detect_issue_create() -> str:
    calls = [("gh issue create --repo brownm09/dev-env --title x", DEVENV_ISSUE_URL, DEVENV_CWD)]
    actions = detect_board_actions(calls)
    assert actions == [{"action": "create", "label": f"issue {DEVENV_ISSUE_URL}"}], actions
    return "gh issue create + dev-env issue URL -> create action"


def test_detect_pr_create() -> str:
    calls = [("gh pr create --fill", DEVENV_PR_URL, DEVENV_CWD)]
    actions = detect_board_actions(calls)
    assert actions == [{"action": "create", "label": f"PR {DEVENV_PR_URL}"}], actions
    return "gh pr create + dev-env PR URL -> create action"


def test_detect_create_other_repo_ignored() -> str:
    # A create whose URL is for a *different* repo is post-tool-use.py's silent
    # path even in a healthy session — must not be treated as a dev-env action.
    calls = [("gh issue create --repo brownm09/lifting-logbook --title x",
              OTHER_REPO_URL, DEVENV_CWD)]
    assert detect_board_actions(calls) == []
    return "create with a non-dev-env URL -> no action (different-repo false-positive guard)"


def test_detect_create_no_url_ignored() -> str:
    # A failed create produces no URL; nothing to add to the board.
    calls = [("gh issue create --title x", "could not create issue", DEVENV_CWD)]
    assert detect_board_actions(calls) == []
    return "create with no GitHub URL (failed create) -> no action"


def test_detect_merge_by_url() -> str:
    calls = [(f"gh pr merge {DEVENV_PR_URL} --squash --delete-branch", "merged", DEVENV_CWD)]
    actions = detect_board_actions(calls)
    assert actions == [{"action": "merge", "label": "PR #241"}], actions
    return "gh pr merge <dev-env PR URL> + clean output -> merge action"


def test_detect_merge_worktree_tail_still_counts() -> str:
    # The issue-#275 worktree cleanup failure is NOT a merge failure.
    calls = [(f"gh pr merge {DEVENV_PR_URL} --squash --delete-branch",
              WORKTREE_MERGE_TAIL, DEVENV_CWD)]
    actions = detect_board_actions(calls)
    assert actions == [{"action": "merge", "label": "PR #241"}], actions
    return "worktree cleanup-failure tail still counts as a completed merge (#275)"


def test_detect_merge_hard_fail_ignored() -> str:
    calls = [(f"gh pr merge {DEVENV_PR_URL} --squash",
              "Pull request is not mergeable: merge conflict", DEVENV_CWD)]
    assert detect_board_actions(calls) == []
    return "merge with a hard failure (not mergeable) -> no action"


def test_detect_merge_bare_number_devenv_cwd() -> str:
    calls = [("gh pr merge 390 --squash --delete-branch", "merged", DEVENV_CWD)]
    actions = detect_board_actions(calls)
    assert actions == [{"action": "merge", "label": "PR #390"}], actions
    return "bare `gh pr merge <N>` from a dev-env cwd -> merge action"


def test_detect_merge_bare_number_other_cwd_ignored() -> str:
    calls = [("gh pr merge 390 --squash", "merged", "C:/Users/brown/Git/lifting-logbook")]
    assert detect_board_actions(calls) == []
    return "bare `gh pr merge <N>` from a non-dev-env cwd -> no action (unknown repo)"


def test_detect_merge_auto_ignored() -> str:
    # A queued --auto only enables auto-merge; even a healthy session would not
    # Done-move it yet (cf. post-pr-merge-project.py's marker gate). Don't advise.
    calls = [(f"gh pr merge --auto {DEVENV_PR_URL} --squash", "merged", DEVENV_CWD)]
    assert detect_board_actions(calls) == []
    return "gh pr merge --auto (queued, not completed) -> no action"


def test_detect_merge_explicit_other_repo_ignored() -> str:
    # The reviewer's repro: a non-dev-env merge with a dev-env URL buried in --body
    # must not be misattributed to dev-env. --repo is the authority, not the URL.
    calls = [(
        "gh pr merge 42 --repo brownm09/lifting-logbook "
        f'--body "see {DEVENV_PR_URL}"', "merged", DEVENV_CWD,
    )]
    assert detect_board_actions(calls) == []
    return "merge --repo <other> with a dev-env URL in --body -> no action (no hijack)"


def test_detect_merge_chained_url_not_hijacked() -> str:
    # A /pull/N URL in a chained sibling command (after &&) is outside the merge
    # invocation's arg span and must not hijack the PR number.
    calls = [(
        f"gh pr merge 241 --squash && echo {DEVENV_PR_URL.replace('241','999')}",
        "merged", DEVENV_CWD,
    )]
    actions = detect_board_actions(calls)
    assert actions == [{"action": "merge", "label": "PR #241"}], actions
    return "chained `&& echo .../pull/999` does not hijack the merged PR number"


def test_devenv_merge_pr_direct() -> str:
    assert _devenv_merge_pr(f"gh pr merge {DEVENV_PR_URL} --squash", DEVENV_CWD) == "241"
    assert _devenv_merge_pr("gh pr merge 390 --squash", DEVENV_CWD) == "390"
    assert _devenv_merge_pr("gh pr merge 42 --repo brownm09/dev-env", "/other") == "42"
    assert _devenv_merge_pr("gh pr merge 42 --repo brownm09/lifting-logbook", DEVENV_CWD) is None
    # dev-env#616: gh's -R shorthand for --repo must resolve identically to
    # the long form -- both the dev-env-repo and other-repo cases.
    assert _devenv_merge_pr("gh pr merge 42 -R brownm09/dev-env", "/other") == "42"
    assert _devenv_merge_pr("gh pr merge 42 -R brownm09/lifting-logbook", DEVENV_CWD) is None
    # dev-env#626 / review finding on PR #623: a coincidental mid-word "-R"
    # (not a standalone token) must not be mistaken for the flag -- falls
    # back to cwd-based dev-env detection instead.
    assert _devenv_merge_pr("gh pr merge 42 xx-R brownm09/dev-env", DEVENV_CWD) == "42"
    assert _devenv_merge_pr("gh pr merge 42 xx-R brownm09/dev-env", "/other") is None
    # dev-env#626, ADR-050 Amendment 15: the (?<!\S) lookbehind alone can't
    # distinguish "whitespace inside a quoted value" from "whitespace between
    # top-level tokens" -- a --subject value containing a legitimately
    # space-separated "-R other/repo" substring must not be mistaken for the
    # flag either. mask_quoted_spans blinds the whole quoted span first, so
    # this also falls back to cwd-based dev-env detection.
    assert _devenv_merge_pr(
        'gh pr merge 42 --subject "see -R other/repo for context"', DEVENV_CWD,
    ) == "42"
    assert _devenv_merge_pr(
        'gh pr merge 42 --subject "see -R other/repo for context"', "/other",
    ) is None
    # A real --repo flag must still resolve correctly alongside a quoted decoy.
    assert _devenv_merge_pr(
        'gh pr merge 42 --repo brownm09/dev-env --subject "see -R other/repo for context"',
        "/other",
    ) == "42"
    assert _devenv_merge_pr(f"gh pr merge --auto {DEVENV_PR_URL}", DEVENV_CWD) is None
    assert _devenv_merge_pr("gh pr merge 7 --squash", "/some/other/repo") is None
    return "_devenv_merge_pr: URL/number/--repo/-R/mid-word-guard/quoted-decoy/--auto/cwd scoping all resolve correctly (dev-env#616, #626)"


def test_detect_unrelated_command_ignored() -> str:
    calls = [("git status", "clean", DEVENV_CWD), ("npm test", "ok", DEVENV_CWD)]
    assert detect_board_actions(calls) == []
    return "non create/merge commands -> no action"


def test_is_devenv_cwd() -> str:
    assert _is_devenv_cwd("C:/Users/brown/Git/dev-env") is True
    assert _is_devenv_cwd(DEVENV_CWD) is True
    assert _is_devenv_cwd("C:/Users/brown/Git/dev-env-188") is True  # sibling worktree
    assert _is_devenv_cwd("C:/Users/brown/Git/lifting-logbook") is False
    return "dev-env canonical / worktree / sibling cwd -> True; other repo -> False"


# --- should_emit (the combined predicate) --------------------------------------

def test_should_emit_inert_create() -> str:
    # A dev-env create with zero PostToolUse attachments -> inert -> advise.
    recs = [
        bash_use("t1", "gh issue create --repo brownm09/dev-env --title x"),
        tool_result("t1", DEVENV_ISSUE_URL),
        attachment("hook_success", "Stop"),  # Stop fires even when inert
    ]
    actions = should_emit(recs)
    assert actions == [{"action": "create", "label": f"issue {DEVENV_ISSUE_URL}"}], actions
    return "dev-env create + no PostToolUse attachment -> advise (inert detected)"


def test_should_emit_healthy_create_silent() -> str:
    # Same create, but a PostToolUse attachment is present -> dispatch worked.
    recs = [
        bash_use("t1", "gh issue create --repo brownm09/dev-env --title x"),
        tool_result("t1", DEVENV_ISSUE_URL),
        attachment("hook_blocking_error", "PostToolUse"),  # post-tool-use.py fired
    ]
    assert should_emit(recs) is None
    return "dev-env create + PostToolUse attachment present -> silent (healthy session)"


def test_should_emit_no_action_silent() -> str:
    # No board action -> nothing to advise, even with zero PostToolUse attachments.
    recs = [bash_use("t1", "git status"), tool_result("t1", "clean")]
    assert should_emit(recs) is None
    return "no board action -> silent even when no PostToolUse attachment exists"


def test_should_emit_inert_merge() -> str:
    recs = [
        bash_use("t1", f"gh pr merge {DEVENV_PR_URL} --squash --delete-branch"),
        tool_result("t1", WORKTREE_MERGE_TAIL),
        attachment("hook_system_message", "UserPromptSubmit"),
    ]
    actions = should_emit(recs)
    assert actions == [{"action": "merge", "label": "PR #241"}], actions
    return "dev-env merge + no PostToolUse attachment -> advise (inert detected)"


def test_format_advisory_mentions_fallback() -> str:
    msg = format_advisory([{"action": "create", "label": f"issue {DEVENV_ISSUE_URL}"}])
    assert msg.startswith("[posttooluse-inert]")
    assert "ADR-053" in msg
    assert "GitHub Project" in msg and "Fallback" in msg
    assert DEVENV_ISSUE_URL in msg
    return "advisory tags itself, cites ADR-053, names the action, points to the fallback"


def test_advisory_is_cp1252_safe() -> str:
    # Claude Code pipes hook stdout as cp1252; a char outside it (an arrow, an
    # em-dash) makes print() raise and the advisory vanish through the exit-0
    # guard. Pin the advisory ASCII/cp1252-encodable so that can't regress.
    for actions in (
        [{"action": "create", "label": f"issue {DEVENV_ISSUE_URL}"}],
        [{"action": "merge", "label": "PR #241"}],
    ):
        msg = format_advisory(actions)
        msg.encode("cp1252")  # raises UnicodeEncodeError on a non-cp1252 char
        assert msg.isascii(), f"advisory must be ASCII, got non-ASCII: {msg!r}"
    return "advisory is ASCII / cp1252-encodable (won't vanish under cp1252 stdout)"


def main() -> int:
    tests = [
        ("attachment present: hook_success/PostToolUse", test_attachment_present_hook_success),
        ("attachment present: hook_blocking_error/PostToolUse", test_attachment_present_blocking_error),
        ("attachment absent: only Stop/UserPromptSubmit", test_attachment_absent_other_events),
        ("attachment absent: no records", test_attachment_absent_none),
        ("iter_bash_calls pairs by id", test_pairs_by_id),
        ("iter_bash_calls: parallel not mismatched", test_pairs_parallel_not_mismatched),
        ("iter_bash_calls: unmatched result skipped", test_unmatched_result_skipped),
        ("_result_text: string content", test_result_text_string_content),
        ("_result_text: list content", test_result_text_list_content),
        ("_result_text: toolUseResult fallback", test_result_text_tooluseresult_fallback),
        ("detect: issue create", test_detect_issue_create),
        ("detect: pr create", test_detect_pr_create),
        ("detect: create other-repo ignored", test_detect_create_other_repo_ignored),
        ("detect: create no-URL ignored", test_detect_create_no_url_ignored),
        ("detect: merge by URL", test_detect_merge_by_url),
        ("detect: merge worktree tail still counts (#275)", test_detect_merge_worktree_tail_still_counts),
        ("detect: merge hard-fail ignored", test_detect_merge_hard_fail_ignored),
        ("detect: merge bare number + dev-env cwd", test_detect_merge_bare_number_devenv_cwd),
        ("detect: merge bare number + other cwd ignored", test_detect_merge_bare_number_other_cwd_ignored),
        ("detect: merge --auto ignored", test_detect_merge_auto_ignored),
        ("detect: merge --repo other ignored (no URL hijack)", test_detect_merge_explicit_other_repo_ignored),
        ("detect: merge chained URL not hijacked", test_detect_merge_chained_url_not_hijacked),
        ("_devenv_merge_pr direct cases", test_devenv_merge_pr_direct),
        ("detect: unrelated command ignored", test_detect_unrelated_command_ignored),
        ("_is_devenv_cwd", test_is_devenv_cwd),
        ("should_emit: inert create -> advise", test_should_emit_inert_create),
        ("should_emit: healthy create -> silent", test_should_emit_healthy_create_silent),
        ("should_emit: no action -> silent", test_should_emit_no_action_silent),
        ("should_emit: inert merge -> advise", test_should_emit_inert_merge),
        ("format_advisory mentions fallback", test_format_advisory_mentions_fallback),
        ("advisory is cp1252-safe (no vanish under cp1252 stdout)", test_advisory_is_cp1252_safe),
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
