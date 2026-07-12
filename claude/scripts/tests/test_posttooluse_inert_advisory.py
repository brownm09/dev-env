#!/usr/bin/env python3
"""Unit tests for posttooluse-inert-advisory.py — the Stop-hook safety net that
surfaces inert PostToolUse hooks (ADR-053 / ADR-055).

In background / `spawn_task`-launched sessions, every PostToolUse hook is silently
inert (upstream Claude Code limitation; ADR-053). This Stop hook reads the
just-ended transcript and, when a dev-env (project #3) `gh issue/pr create` or
`gh pr merge` ran but **no** PostToolUse hook left an `attachment` record all
session, surfaces the manual fallback. Because a Stop hook's exit-0 stdout is
invisible to Claude, the advisory is now delivered on **exit-2 stderr** (the one
Stop channel that reaches the model — ADR-091/103), blocking the stop once so it is
actually seen; a `stop_hook_active` loop guard plus a per-session sentinel keep it
to one fire, and `mark_resolved` runs *after* the emission so a failed delivery is
retried (dev-env#629, dev-env#740).

The pure helpers are exercised offline against synthetic transcript records (no
stdin, no network, no gh, no disk). A behavioral layer additionally drives the real
hook end-to-end over stdin via subprocess (HOME/USERPROFILE pointed at a temp dir so
the sentinel + transcript-locate resolve under the tmp scratch, mirroring
test_journal_stop_check.py): an inert session blocks (exit 2, advisory on stderr,
empty stdout); a `stop_hook_active` continuation and a healthy session (a PostToolUse
attachment present) each exit 0; and the per-session sentinel suppresses a second
fire (proving mark_resolved ran on the blocking Stop).

Usage:
    py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py

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
SCRIPT = REPO_ROOT / "claude" / "scripts" / "posttooluse-inert-advisory.py"

# The script imports _winsubp / _hookout (siblings in scripts/); make them
# resolvable when exec_module runs the module body.
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
    # dev-env#634, ADR-050 Amendment 17: a --subject value containing a decoy
    # dev-env PR URL must not be mistaken for a genuine self-identifying
    # signal either. mask_prose_flag_values blinds the whole quoted span
    # before url_m is computed, so this also falls back to cwd-based dev-env
    # detection.
    assert _devenv_merge_pr(
        f'gh pr merge 42 --subject "see {DEVENV_PR_URL} for context"', DEVENV_CWD,
    ) == "42"
    assert _devenv_merge_pr(
        f'gh pr merge 42 --subject "see {DEVENV_PR_URL} for context"', "/other",
    ) is None
    # A real --repo flag must still resolve correctly alongside a quoted
    # URL-shaped decoy.
    assert _devenv_merge_pr(
        f'gh pr merge 42 --repo brownm09/dev-env --subject "see {DEVENV_PR_URL} for context"',
        "/other",
    ) == "42"
    assert _devenv_merge_pr(f"gh pr merge --auto {DEVENV_PR_URL}", DEVENV_CWD) is None
    assert _devenv_merge_pr("gh pr merge 7 --squash", "/some/other/repo") is None
    # dev-env#650, ADR-050 Amendment 19: a --subject/--body value containing a
    # bare decoy number ("resolves 42 items") must not be mistaken for the
    # real positional PR number either. mask_quoted_spans blinds the whole
    # quoted span before num_m is computed, so with no real number and no
    # other self-identifying signal this falls through to None (is_devenv is
    # True from cwd, but neither num_m nor url_m resolves).
    assert _devenv_merge_pr(
        'gh pr merge --subject "resolves 42 items"', DEVENV_CWD,
    ) is None
    assert _devenv_merge_pr(
        'gh pr merge --body "fixes 99 bugs now"', DEVENV_CWD,
    ) is None
    # A real positional number must still resolve correctly alongside a
    # quoted bare-number decoy elsewhere in the same args.
    assert _devenv_merge_pr(
        'gh pr merge 390 --subject "resolves 42 items"', DEVENV_CWD,
    ) == "390"
    return "_devenv_merge_pr: URL/number/--repo/-R/mid-word-guard/quoted-decoy/quoted-url-decoy/bare-number-decoy/--auto/cwd scoping all resolve correctly (dev-env#616, #626, #634, #650)"


def test_devenv_merge_pr_repo_after_quoted_separator_not_dropped() -> str:
    # dev-env#660, ADR-050 Amendment 20: _MERGE_ARGS_RE's own args-region
    # boundary (not the searches WITHIN it, already fixed by #626/#634) ran
    # against the RAW command with no quote-awareness -- its negated class
    # `[^\n;|&]*` stops at ANY single '&'/'|', so a --subject value containing
    # one (even an ordinary commit subject, not a deliberately crafted string)
    # truncated `args` before a later real --repo/PR-number was ever reached.
    # Confirmed live before the fix: _devenv_merge_pr('gh pr merge 42 --subject
    # "part1 && part2" --repo brownm09/dev-env', "/other") returned None
    # instead of "42" -- the explicit --repo naming dev-env was silently lost,
    # and a non-dev-env cwd then correctly (but for the wrong reason) fell
    # through to "not dev-env".
    assert _devenv_merge_pr(
        'gh pr merge 42 --subject "part1 && part2" --repo brownm09/dev-env', "/other",
    ) == "42"
    # An ordinary bare '&' (no doubling needed) triggers the same truncation
    # in _MERGE_ARGS_RE specifically (unlike pre-merge-findings-gate.py's own
    # &&-only split pattern).
    assert _devenv_merge_pr(
        'gh pr merge 42 --subject "R&D tracking" --repo brownm09/dev-env', "/other",
    ) == "42"
    # A REAL top-level && chaining a sibling command must still bound the args
    # region -- the fix must not simply widen the boundary unconditionally.
    assert _devenv_merge_pr(
        "gh pr merge 42 --repo brownm09/dev-env && rm -rf /", "/other",
    ) == "42"
    # Review finding on PR #668: a quoted && decoy AND a real trailing &&
    # chain combined in the SAME command -- the boundary-finder must pick the
    # FIRST unmasked separator, not be shadowed by the earlier masked decoy.
    assert _devenv_merge_pr(
        'gh pr merge 42 --subject "a && b" --repo brownm09/dev-env && rm -rf /', "/other",
    ) == "42"
    return "_devenv_merge_pr: --repo/PR-number after a quoted &&/bare-& value no longer silently dropped (dev-env#660)"


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
    # Claude Code pipes hook output as cp1252; a char outside it (an arrow, an
    # em-dash) makes the exit-2 stderr write raise and the advisory vanish through
    # the outer guard. main() also routes it through _hookout.ascii_sanitize, but
    # keeping format_advisory ASCII/cp1252-encodable keeps the sanitizer a no-op.
    for actions in (
        [{"action": "create", "label": f"issue {DEVENV_ISSUE_URL}"}],
        [{"action": "merge", "label": "PR #241"}],
    ):
        msg = format_advisory(actions)
        msg.encode("cp1252")  # raises UnicodeEncodeError on a non-cp1252 char
        assert msg.isascii(), f"advisory must be ASCII, got non-ASCII: {msg!r}"
    return "advisory is ASCII / cp1252-encodable (won't vanish under the cp1252 stderr pipe)"


# --- behavioral: real hook over stdin via subprocess (HOME-isolated sentinel) --

def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


INERT_RECORDS = [
    bash_use("t1", "gh issue create --repo brownm09/dev-env --title x"),
    tool_result("t1", DEVENV_ISSUE_URL),
    attachment("hook_success", "Stop"),  # Stop fires even when inert; no PostToolUse
]
HEALTHY_RECORDS = INERT_RECORDS + [attachment("hook_blocking_error", "PostToolUse")]


def _run_hook(home, records, *, stop_hook_active=False, session_id="sess-e2e-1"):
    """Drive the real hook once against a planted transcript. (rc, stdout, stderr).

    HOME/USERPROFILE point at *home* so _hookutil.SCRATCH (the sentinel dir) and any
    find_transcript fallback resolve under the tmp dir. The scratch dir is
    pre-created because mark_resolved's `SCRATCH.mkdir(exist_ok=True)` does not create
    parents (matching production, where ~/.claude already exists)."""
    home = Path(home)
    (home / ".claude" / "scratch").mkdir(parents=True, exist_ok=True)
    tpath = home / "transcript.jsonl"
    tpath.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
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


def test_e2e_inert_blocks_on_stderr() -> str:
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, INERT_RECORDS, stop_hook_active=False)
    assert rc == 2, f"inert session must block (exit 2), got {rc} (stderr={err!r})"
    assert "[posttooluse-inert]" in err, err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e inert + not-continuation -> exit 2, advisory on stderr, empty stdout"


def test_e2e_stop_hook_active_does_not_block() -> str:
    # A continuation Stop (the loop guard): even with the inert signature present and
    # no sentinel yet, do NOT re-block -- exit 0 with no advisory.
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, INERT_RECORDS, stop_hook_active=True)
    assert rc == 0, f"stop_hook_active continuation must not block, got {rc} (stderr={err!r})"
    assert "[posttooluse-inert]" not in err, f"must not re-emit on a continuation, got {err!r}"
    return "e2e inert + stop_hook_active=true -> exit 0 (loop guard), no re-block"


def test_e2e_healthy_session_silent() -> str:
    # A PostToolUse attachment present => dispatch worked => never inert => silent.
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, HEALTHY_RECORDS, stop_hook_active=False)
    assert rc == 0, f"healthy session must exit 0, got {rc} (stderr={err!r})"
    assert "[posttooluse-inert]" not in err and out.strip() == "", (out, err)
    return "e2e healthy (PostToolUse attachment present) -> exit 0, silent"


def test_e2e_sentinel_suppresses_second_fire() -> str:
    # First Stop blocks (exit 2) and mark_resolved writes the sentinel AFTER the
    # emission; a second Stop in the same session finds the sentinel and exits 0 --
    # proving the advisory fires at most once and mark_resolved ran on the block.
    with tempfile.TemporaryDirectory() as home:
        rc1, _out1, err1 = _run_hook(home, INERT_RECORDS, stop_hook_active=False)
        rc2, out2, err2 = _run_hook(home, INERT_RECORDS, stop_hook_active=False)
    assert rc1 == 2 and "[posttooluse-inert]" in err1, (rc1, err1)
    assert rc2 == 0, f"second Stop must be suppressed by the sentinel, got {rc2} (stderr={err2!r})"
    assert "[posttooluse-inert]" not in err2, f"must not re-fire, got {err2!r}"
    return "e2e first Stop blocks + sets sentinel; second Stop -> exit 0 (fires at most once)"


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
        ("_devenv_merge_pr: repo/number after quoted separator not dropped (dev-env#660)", test_devenv_merge_pr_repo_after_quoted_separator_not_dropped),
        ("detect: unrelated command ignored", test_detect_unrelated_command_ignored),
        ("_is_devenv_cwd", test_is_devenv_cwd),
        ("should_emit: inert create -> advise", test_should_emit_inert_create),
        ("should_emit: healthy create -> silent", test_should_emit_healthy_create_silent),
        ("should_emit: no action -> silent", test_should_emit_no_action_silent),
        ("should_emit: inert merge -> advise", test_should_emit_inert_merge),
        ("format_advisory mentions fallback", test_format_advisory_mentions_fallback),
        ("advisory is cp1252-safe (no vanish under cp1252 stderr)", test_advisory_is_cp1252_safe),
        # behavioral: real hook over stdin (exit-2 stderr migration, dev-env#740)
        ("e2e inert blocks on stderr (exit 2)", test_e2e_inert_blocks_on_stderr),
        ("e2e stop_hook_active does not block", test_e2e_stop_hook_active_does_not_block),
        ("e2e healthy session silent", test_e2e_healthy_session_silent),
        ("e2e sentinel suppresses second fire", test_e2e_sentinel_suppresses_second_fire),
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
