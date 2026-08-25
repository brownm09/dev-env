#!/usr/bin/env python3
"""Unit + behavioral tests for stop-journal-stub-checkpoint.py (ADR-100).

`stop-journal-stub-checkpoint.py` is a Stop hook that scans the just-ended
session transcript and blocks the stop (exit 2) when a report / analysis /
verification session (a genuine user prompt with a report/verify keyword AND at
least SUBSTANTIVE_THRESHOLD substantive tool calls) is ending with no
engineering-journal stub, no PR opened/merged this session, is not a /review
session, and carries no "skip journal" override.

Two layers, mirroring this repo's hook-test convention:

  * Pure-helper tests exercise the detection/decision core offline (no stdin,
    network, gh, or disk): report-intent detection (user-typed text only —
    NOT a keyword in assistant text / tool_result / isMeta / isCompactSummary /
    a slash-command wrapper), the substantive-tool count (which tools count,
    the 4-vs-5 boundary), PR-create/merge detection (anchored, --help-only and
    heredoc/subshell mentions excluded), stub-write detection (Write/Edit
    file_path or a Bash reference), the /review exemption, the skip override
    (and that a tool_result / compact-summary mention does NOT waive), the
    evaluate() composition, the reminder's cp1252-encodability, and that
    malformed records don't disable the gate.

  * A behavioral layer drives the real hook end-to-end over stdin via subprocess
    against a synthetic transcript, with HOME/USERPROFILE pointed at a temp dir
    so the once-per-session sentinel is isolated from the real ~/.claude/scratch.
    It pins: fire -> exit 2 with the reminder on stderr and empty stdout (Claude
    Code shows a Stop hook's stderr on exit 2, not stdout); wrote-stub / opened-PR
    / /review / skip / no-intent / intent-below-threshold -> exit 0; the
    stop_hook_active loop-guard -> exit 0; and that the sentinel suppresses a
    second fire in the same session.

main()'s own stdin read / sentinel-path plumbing beyond the end-to-end runs is
not separately unit-tested (pure-helper convention).

Usage:
    py -3 claude/scripts/tests/test_stop_journal_stub_checkpoint.py

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
SCRIPT = REPO_ROOT / "claude" / "scripts" / "stop-journal-stub-checkpoint.py"

# The script imports _hookutil / _hookio (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("stop_journal_stub_checkpoint", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)  # safe: main() is guarded by __main__


# --- transcript-record builders ------------------------------------------------

def _asst_bash(tid, command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": tid, "input": {"command": command}}]}}


def _asst_tool(tid, name, inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": tid, "input": inp}]}}


def _asst_read(tid, path):
    return _asst_tool(tid, "Read", {"file_path": path})


def _asst_grep(tid, pattern):
    return _asst_tool(tid, "Grep", {"pattern": pattern})


def _asst_write(tid, path):
    return _asst_tool(tid, "Write", {"file_path": path, "content": ""})


def _asst_edit(tid, path):
    return _asst_tool(tid, "Edit", {"file_path": path, "old_string": "a", "new_string": "b"})


def _tool_result(tid, output):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": output}]}}


def _asst_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _user_str(text):
    return {"type": "user", "message": {"content": text}}


def _user_text_item(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _user_meta(text):
    return {"type": "user", "isMeta": True, "message": {"content": text}}


def _user_compact(text):
    return {"type": "user", "isCompactSummary": True, "message": {"content": text}}


def _sidechain(rec):
    """Return a copy of *rec* with isSidechain: True set -- models a
    subagent's own tool_use record for the isSidechain-exclusion tests
    (dev-env#1023, ADR-100 Amendment 2)."""
    out = dict(rec)
    out["isSidechain"] = True
    return out


def _user_review(url):
    return _user_str(
        f"<command-name>/review</command-name>\n"
        f"<command-message>review</command-message>\n"
        f"<command-args>{url}</command-args>"
    )


# --- shared fixtures -----------------------------------------------------------

# The motivating #770 verification session: a verify/production-fix user prompt,
# 5 substantive tool calls (3 Bash + 1 Read + 1 Grep — none of them gh pr
# create/merge), no stub, not /review, no skip -> evaluate == (True, False).
_REPORT_NO_STUB = [
    _user_str("Verify PR #770's production fix actually resolved the logbook regression."),
    _asst_bash("t1", "gh pr checks 770"),
    _tool_result("t1", "All checks passing"),
    _asst_bash("t2", "gh run view 123 --log"),
    _tool_result("t2", "deploy succeeded"),
    _asst_read("t3", "C:/Users/brown/Git/lifting-logbook/src/logbook.ts"),
    _tool_result("t3", "...code..."),
    _asst_bash("t4", "curl -s https://app.example.com/health"),
    _tool_result("t4", '{"status":"ok"}'),
    _asst_grep("t5", "regression"),
    _tool_result("t5", "no matches"),
    _asst_text("Verified: the production fix in PR #770 resolved the regression; health green."),
]

# Same session, but a stub was written -> resolved, no fire.
_REPORT_WITH_STUB = _REPORT_NO_STUB[:-1] + [
    _asst_write("t6", "C:/Users/brown/Git/engineering-journal/sessions/lifting-logbook/2026-07-10_140000.stub.md"),
    _tool_result("t6", "File created"),
    _asst_text("Wrote the verification stub."),
]

# Same session, but a PR was opened (pr-merge-reminder.py already nudged) -> resolved.
_REPORT_THEN_PR = _REPORT_NO_STUB[:-1] + [
    _asst_bash("t6", "gh pr create --fill --head fix/regression"),
    _tool_result("t6", "https://github.com/brownm09/lifting-logbook/pull/771"),
    _asst_text("Opened PR #771."),
]


# ---------------------------------------------------------------------------
# report_intent
# ---------------------------------------------------------------------------

def test_report_intent_report_group():
    recs = [_user_str("Write an audit report of the auth module.")]
    assert hook.report_intent(recs) is True
    return "report_intent: report/audit keyword in a user prompt -> True"


def test_report_intent_verify_group():
    recs = [_user_str("Please verify the production deploy went out cleanly.")]
    assert hook.report_intent(recs) is True
    return "report_intent: verify/production-deploy keyword -> True"


def test_report_intent_production_fix():
    recs = [_user_str("Did the production fix land correctly?")]
    assert hook.report_intent(recs) is True
    return "report_intent: 'production fix' verification sense -> True"


def test_report_intent_text_item_form():
    recs = [_user_text_item("investigate the latency spike in checkout")]
    assert hook.report_intent(recs) is True
    return "report_intent: list content {type:text} form -> True"


def test_report_intent_not_in_assistant_text():
    recs = [_user_str("rename this helper"), _asst_text("Here is my analysis of the change.")]
    assert hook.report_intent(recs) is False
    return "report_intent: keyword only in assistant text -> False"


def test_report_intent_not_in_tool_result():
    recs = [_user_str("rename this helper"), _tool_result("t1", "audit findings summary: ok")]
    assert hook.report_intent(recs) is False
    return "report_intent: keyword only in a tool_result -> False"


def test_report_intent_not_in_ismeta():
    recs = [_user_meta("investigate the flaky test")]
    assert hook.report_intent(recs) is False
    return "report_intent: keyword in an isMeta record -> False"


def test_report_intent_not_in_compact_summary():
    recs = [_user_compact("Earlier the user asked me to audit the module.")]
    assert hook.report_intent(recs) is False
    return "report_intent: keyword in an isCompactSummary record -> False"


def test_report_intent_not_in_command_wrapper():
    recs = [_user_str("<command-name>/deploy-check</command-name>\n<command-args>verify prod</command-args>")]
    assert hook.report_intent(recs) is False
    return "report_intent: keyword inside a <command-name> wrapper -> False"


def test_report_intent_absent():
    recs = [_user_str("help me rename this function and run the tests")]
    assert hook.report_intent(recs) is False
    return "report_intent: no report/verify keyword -> False"


# ---------------------------------------------------------------------------
# substantive_tool_count
# ---------------------------------------------------------------------------

def test_substantive_counts_each_tool():
    tools = ["Bash", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"]
    recs = [_asst_tool(f"t{i}", name, {}) for i, name in enumerate(tools)]
    assert hook.substantive_tool_count(recs) == 9
    return "substantive_tool_count: counts each of the 9 substantive tools -> 9"


def test_substantive_ignores_bookkeeping_delegation_and_text():
    """Real-shaped exclusion check -- NOT a hand-built TodoWrite fixture. This
    harness has no TodoWrite tool at all (0 occurrences across every transcript
    on the machine, confirmed live). Its real task-list tool is TaskCreate/
    TaskUpdate and its real subagent-spawning tool is "Agent" (bare "Task" also
    never appears). dev-env#1020, mirroring the identical dev-env#1002
    correction in journal-stop-check.py (ADR-091 Amendment 3)."""
    recs = [
        _asst_tool("t1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _asst_tool("t2", "TaskUpdate", {"taskId": "1", "status": "in_progress"}),
        _asst_tool("t3", "Agent", {"prompt": "investigate the flaky test", "subagent_type": "Explore"}),
        _asst_tool("t4", "mcp__ccd_session__spawn_task", {"title": "x"}),
        _asst_text("some analysis text"),
    ]
    assert hook.substantive_tool_count(recs) == 0
    return "substantive_tool_count: TaskCreate/TaskUpdate/Agent/spawn_task/text not counted -> 0"


def test_substantive_parallel_in_one_record():
    rec = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "id": "a", "input": {}},
        {"type": "tool_use", "name": "Grep", "id": "b", "input": {}},
    ]}}
    assert hook.substantive_tool_count([rec]) == 2
    return "substantive_tool_count: two parallel tool_use in one record -> 2"


def test_substantive_boundary_4_and_5():
    four = [_asst_read(f"t{i}", f"f{i}.ts") for i in range(4)]
    five = [_asst_read(f"t{i}", f"f{i}.ts") for i in range(5)]
    assert hook.substantive_tool_count(four) == 4
    assert hook.substantive_tool_count(five) == 5
    return "substantive_tool_count boundary: 4 reads -> 4, 5 reads -> 5"


def test_substantive_ignores_sidechain_records():
    """A subagent's own tool_use calls (isSidechain: true) must not count
    toward the main session's substantive-work total (dev-env#1023, ADR-100
    Amendment 2) -- mirrors journal-stop-check.py's isSidechain-filtered
    counters (ADR-091 Amendment 3)."""
    recs = [_sidechain(_asst_read(f"t{i}", f"f{i}.ts")) for i in range(6)]
    assert hook.substantive_tool_count(recs) == 0
    return "substantive_tool_count: isSidechain records excluded -> 0"


def test_substantive_mixed_sidechain_and_main():
    """Only non-isSidechain tool_use calls count; a large volume of
    isSidechain calls in the same transcript is ignored entirely."""
    recs = (
        [_asst_read(f"m{i}", f"main{i}.ts") for i in range(3)]
        + [_sidechain(_asst_read(f"s{i}", f"sub{i}.ts")) for i in range(10)]
    )
    assert hook.substantive_tool_count(recs) == 3
    return "substantive_tool_count: 3 main + 10 isSidechain reads -> 3 (isSidechain never counted)"


# ---------------------------------------------------------------------------
# opened_or_merged_pr
# ---------------------------------------------------------------------------

def test_pr_create_detected():
    recs = [_asst_bash("t1", "gh pr create --fill --head feat/x")]
    assert hook.opened_or_merged_pr(recs) is True
    return "opened_or_merged_pr: gh pr create -> True"


def test_pr_merge_detected():
    recs = [_asst_bash("t1", "gh pr merge 12 --squash --delete-branch")]
    assert hook.opened_or_merged_pr(recs) is True
    return "opened_or_merged_pr: gh pr merge -> True"


def test_pr_create_in_heredoc_not_matched():
    recs = [_asst_bash("t1", "git commit -F - <<'EOF'\nrun gh pr create later\nEOF")]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: gh pr create inside a heredoc body -> False"


def test_pr_create_in_subshell_not_matched():
    recs = [_asst_bash("t1", 'echo "$(gh pr create --fill)"')]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: gh pr create inside a $() subshell -> False"


def test_pr_create_help_only_not_matched():
    recs = [_asst_bash("t1", "gh pr create --help")]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: gh pr create --help only -> False"


def test_gh_pr_checks_not_matched():
    recs = [_asst_bash("t1", "gh pr checks 770"), _asst_bash("t2", "gh pr view 770")]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: gh pr checks / view -> False"


def test_pr_absent():
    recs = [_asst_bash("t1", "npm test")]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: no PR command -> False"


def test_pr_create_sidechain_not_counted():
    """A subagent running gh pr create (isSidechain: true) must not satisfy
    opened_or_merged_pr() (dev-env#1023, ADR-100 Amendment 2)."""
    recs = [_sidechain(_asst_bash("t1", "gh pr create --fill --head feat/x"))]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: isSidechain gh pr create -> False (excluded)"


def test_pr_merge_sidechain_not_counted():
    recs = [_sidechain(_asst_bash("t1", "gh pr merge 12 --squash --delete-branch"))]
    assert hook.opened_or_merged_pr(recs) is False
    return "opened_or_merged_pr: isSidechain gh pr merge -> False (excluded)"


# ---------------------------------------------------------------------------
# wrote_stub
# ---------------------------------------------------------------------------

def test_wrote_stub_write():
    recs = [_asst_write("t1", "sessions/dev-env/2026-07-10_140000.stub.md")]
    assert hook.wrote_stub(recs) is True
    return "wrote_stub: Write to a *.stub.md file_path -> True"


def test_wrote_stub_edit():
    recs = [_asst_edit("t1", "sessions/dev-env/2026-07-10_140000.stub.md")]
    assert hook.wrote_stub(recs) is True
    return "wrote_stub: Edit to a *.stub.md file_path -> True"


def test_wrote_stub_backslash_path():
    recs = [_asst_write("t1", "C:\\Users\\brown\\Git\\engineering-journal\\sessions\\dev-env\\2026-07-10_140000.stub.md")]
    assert hook.wrote_stub(recs) is True
    return "wrote_stub: Write to a backslash *.stub.md path -> True"


def test_wrote_stub_non_stub_md_no_match():
    recs = [_asst_write("t1", "sessions/dev-env/reports/2026-07-10-audit.md")]
    assert hook.wrote_stub(recs) is False
    return "wrote_stub: Write to a non-stub .md -> False"


def test_wrote_stub_bash_reference():
    recs = [_asst_bash("t1", "git add sessions/dev-env/2026-07-10_120000.stub.md")]
    assert hook.wrote_stub(recs) is True
    return "wrote_stub: Bash git add of a *.stub.md path -> True"


def test_wrote_stub_bash_no_ref():
    recs = [_asst_bash("t1", "npm test")]
    assert hook.wrote_stub(recs) is False
    return "wrote_stub: Bash with no stub reference -> False"


def test_wrote_stub_sidechain_write_not_counted():
    """A subagent writing the stub file (isSidechain: true) must not satisfy
    wrote_stub() (dev-env#1023, ADR-100 Amendment 2) -- this hook's isSidechain
    filter is applied uniformly across all three functions, including this
    existence check; see wrote_stub()'s own docstring for the accepted
    trade-off (a hypothetical subagent-written stub would be missed, at the
    same one-dismissable-nudge cost as any other false positive here)."""
    recs = [_sidechain(_asst_write("t1", "sessions/dev-env/2026-07-10_140000.stub.md"))]
    assert hook.wrote_stub(recs) is False
    return "wrote_stub: isSidechain Write to a *.stub.md -> False (excluded)"


def test_wrote_stub_sidechain_bash_not_counted():
    recs = [_sidechain(_asst_bash("t1", "git add sessions/dev-env/2026-07-10_120000.stub.md"))]
    assert hook.wrote_stub(recs) is False
    return "wrote_stub: isSidechain Bash git-add of a *.stub.md -> False (excluded)"


# ---------------------------------------------------------------------------
# is_review_only_session
# ---------------------------------------------------------------------------

def test_review_only_detected():
    recs = [_user_review("https://github.com/brownm09/dev-env/pull/9")]
    assert hook.is_review_only_session(recs) is True
    return "is_review_only_session: /review command wrapper -> True"


def test_review_prose_not_matched():
    recs = [_user_str("should I run /review on this PR before merging?")]
    assert hook.is_review_only_session(recs) is False
    return "is_review_only_session: prose mentioning /review (no wrapper) -> False"


def test_review_absent():
    recs = [_user_str("verify the deploy")]
    assert hook.is_review_only_session(recs) is False
    return "is_review_only_session: no /review invocation -> False"


# ---------------------------------------------------------------------------
# skip_override
# ---------------------------------------------------------------------------

def test_skip_override_user_string():
    recs = [_user_str("skip the journal for this one, please")]
    assert hook.skip_override(recs) is True
    return "skip_override: user 'skip the journal' string -> True"


def test_skip_override_no_stub():
    recs = [_user_str("no stub needed here")]
    assert hook.skip_override(recs) is True
    return "skip_override: user 'no stub' -> True"


def test_skip_override_text_item():
    recs = [_user_text_item("skip journal please")]
    assert hook.skip_override(recs) is True
    return "skip_override: 'skip journal' in a text-item -> True"


def test_skip_override_tool_result_not_counted():
    recs = [_user_str("verify the deploy"), _tool_result("t1", "note: skip journal step")]
    assert hook.skip_override(recs) is False
    return "skip_override: phrase only in a tool_result -> False"


def test_skip_override_compact_summary_not_counted():
    recs = [_user_compact("Earlier the user said skip journal.")]
    assert hook.skip_override(recs) is False
    return "skip_override: phrase in an isCompactSummary -> False"


def test_skip_override_absent():
    recs = [_user_str("verify the deploy")]
    assert hook.skip_override(recs) is False
    return "skip_override: no skip instruction -> False"


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def test_evaluate_fire():
    fire, resolved = hook.evaluate(_REPORT_NO_STUB)
    assert fire is True and resolved is False
    return "evaluate: report + work + no stub/PR/review/skip -> (True, False) [FIRE]"


def test_evaluate_wrote_stub_resolved():
    fire, resolved = hook.evaluate(_REPORT_WITH_STUB)
    assert fire is False and resolved is True
    return "evaluate: stub written -> (False, True) [resolved]"


def test_evaluate_pr_resolved():
    fire, resolved = hook.evaluate(_REPORT_THEN_PR)
    assert fire is False and resolved is True
    return "evaluate: PR opened -> (False, True) [resolved]"


def test_evaluate_skip_resolved():
    recs = _REPORT_NO_STUB + [_user_str("actually, skip journal for this session")]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is True
    return "evaluate: skip override -> (False, True) [resolved]"


def test_evaluate_review_resolved():
    recs = _REPORT_NO_STUB + [_user_review("https://github.com/brownm09/dev-env/pull/9")]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is True
    return "evaluate: /review session -> (False, True) [resolved]"


def test_evaluate_no_intent_noop():
    recs = [_user_str("rename this helper")] + [_asst_read(f"t{i}", f"f{i}.ts") for i in range(6)]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is False
    return "evaluate: substantive work but no report intent -> (False, False) [no-op]"


def test_evaluate_intent_below_threshold_noop():
    recs = [_user_str("verify the deploy")] + [_asst_read(f"t{i}", f"f{i}.ts") for i in range(3)]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is False
    return "evaluate: intent but count < 5 -> (False, False) [no-op]"


def test_evaluate_task_bookkeeping_alone_does_not_cross_threshold():
    """A report-intent session that creates/updates many tasks (bookkeeping) but
    fewer than SUBSTANTIVE_THRESHOLD real investigative calls must not fire --
    TaskCreate/TaskUpdate volume alone cannot substitute for real work
    (dev-env#1020): 8 TaskCreate + 8 TaskUpdate calls, only 3 real reads."""
    recs = [_user_str("verify the deploy went out cleanly")]
    for i in range(8):
        recs.append(_asst_tool(f"c{i}", "TaskCreate", {"subject": f"s{i}", "description": "d", "activeForm": "x-ing"}))
        recs.append(_asst_tool(f"u{i}", "TaskUpdate", {"taskId": str(i), "status": "completed"}))
    recs += [_asst_read(f"r{i}", f"f{i}.ts") for i in range(3)]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is False
    return "evaluate: 8 TaskCreate + 8 TaskUpdate + 3 reads (< 5 real calls) -> (False, False) [no-op]"


def test_evaluate_sidechain_only_work_does_not_cross_threshold():
    """A report-intent session whose ONLY tool activity is a subagent's
    (isSidechain) work -- 10 isSidechain reads, 0 main-session reads -- must
    not fire (dev-env#1023, ADR-100 Amendment 2). The delegated legwork was
    already invisible to substantive_tool_count() before this fix (subagent
    activity is never recorded in a main-session transcript on this harness --
    see ADR-100 Amendment 2's live-transcript findings); this test pins that
    the explicit isSidechain filter does not change that outcome."""
    recs = [_user_str("Verify the production deploy went out cleanly.")]
    recs += [_sidechain(_asst_read(f"s{i}", f"f{i}.ts")) for i in range(10)]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is False
    return "evaluate: report intent + 10 isSidechain-only reads (0 main-session work) -> (False, False) [no-op]"


# ---------------------------------------------------------------------------
# format_reminder / robustness
# ---------------------------------------------------------------------------

def test_reminder_is_cp1252_encodable():
    msg = hook.format_reminder()
    assert msg.isascii(), "reminder must be ASCII (Claude Code pipes hook output as cp1252)"
    msg.encode("cp1252")  # must not raise
    assert "[journal-stub-checkpoint]" in msg
    assert "skip journal" in msg
    assert "no stub is needed" in msg
    return "format_reminder: ASCII/cp1252-encodable, carries prefix + dismissal text"


def test_reminder_includes_cwd():
    msg = hook.format_reminder("C:/Users/brown/Git/lifting-logbook")
    assert "cwd: C:/Users/brown/Git/lifting-logbook" in msg
    return "format_reminder: includes the cwd context line when given"


def test_malformed_records_do_not_disable():
    recs = [None, "str", 123, [], {"type": "weird"}] + list(_REPORT_NO_STUB)
    fire, resolved = hook.evaluate(recs)
    assert fire is True and resolved is False
    return "evaluate: malformed/non-dict records around FIRE fixture -> still (True, False)"


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


def test_e2e_fire_blocks_on_stderr():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_REPORT_NO_STUB, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[journal-stub-checkpoint]" in err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e report + no stub -> exit 2, reason on stderr, empty stdout"


def test_e2e_wrote_stub_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_REPORT_WITH_STUB, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e report + stub written -> exit 0 (allowed)"


def test_e2e_pr_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_REPORT_THEN_PR, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e report + PR opened -> exit 0 (allowed)"


def test_e2e_review_allows():
    records = _REPORT_NO_STUB + [_user_review("https://github.com/brownm09/dev-env/pull/9")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e /review session -> exit 0 (allowed)"


def test_e2e_skip_allows():
    records = _REPORT_NO_STUB + [_user_str("skip journal for this one")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e skip-journal override -> exit 0 (allowed)"


def test_e2e_no_intent_allows():
    records = [_user_str("rename this helper"), _asst_bash("t1", "npm test"), _tool_result("t1", "ok")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no report keyword -> exit 0 (pre-filter fast-exit)"


def test_e2e_intent_below_threshold_allows():
    records = [_user_str("verify the deploy"), _asst_read("t1", "a.ts"), _tool_result("t1", "x")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e intent but < 5 substantive calls -> exit 0 (allowed)"


def test_e2e_stop_hook_active_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_REPORT_NO_STUB, home, stop_hook_active=True)
    assert rc == 0, f"expected exit 0 with stop_hook_active, got {rc} (stderr={err!r})"
    return "e2e stop_hook_active=true -> exit 0 (loop guard)"


def test_e2e_sentinel_suppresses_refire():
    with tempfile.TemporaryDirectory() as home:
        rc1, _, _ = _run_hook(_REPORT_NO_STUB, home, session_id="sess-refire")
        rc2, out2, _ = _run_hook(_REPORT_NO_STUB, home, session_id="sess-refire")
    assert rc1 == 2, f"first run expected exit 2, got {rc1}"
    assert rc2 == 0, f"second run expected exit 0 (sentinel), got {rc2}"
    return "e2e once-per-session sentinel: first fire exit 2, second exit 0"


def test_e2e_sidechain_only_work_allows():
    """e2e: a report-intent session whose only tool_use activity is
    isSidechain (a subagent's own work) does not fire (dev-env#1023, ADR-100
    Amendment 2)."""
    records = [_user_str("Verify the production deploy went out cleanly.")]
    records += [_sidechain(_asst_read(f"s{i}", f"f{i}.ts")) for i in range(10)]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e report intent + isSidechain-only work -> exit 0 (allowed)"


# ---------------------------------------------------------------------------
# review-of-PR-#706 fixes: narrowed keywords, write-scoped stub, /review-*
# boundary, cwd cp1252 sanitization, and the malformed-stdin fail-open paths
# ---------------------------------------------------------------------------

def test_report_intent_analytics_not_matched():
    recs = [_user_str("add analytics tracking to the dashboard")]
    assert hook.report_intent(recs) is False
    return "report_intent: 'analytics' (product feature) -> False (narrowed analy)"


def test_report_intent_bare_deploy_not_matched():
    recs = [_user_str("the deploy broke again, fix the Dockerfile")]
    assert hook.report_intent(recs) is False
    return "report_intent: bare 'deploy' in a dev task -> False (bare deploy dropped)"


def test_report_intent_check_the_deploy_matched():
    recs = [_user_str("check the deploy once CI finishes")]
    assert hook.report_intent(recs) is True
    return "report_intent: 'check the deploy' -> True (verify/deploy group)"


def test_review_cmd_hyphen_suffix_not_matched():
    recs = [_user_str("<command-name>/review-followups</command-name>\n<command-args>x</command-args>")]
    assert hook.is_review_only_session(recs) is False
    return "is_review_only_session: /review-followups wrapper -> False (hyphen boundary)"


def test_wrote_stub_bash_read_not_matched():
    recs = [
        _asst_bash("t1", "ls sessions/dev-env/*.stub.md | sort | tail -1"),
        _asst_bash("t2", "cat sessions/dev-env/2026-07-10_090000.stub.md"),
    ]
    assert hook.wrote_stub(recs) is False
    return "wrote_stub: bare ls/cat of a *.stub.md (a read) -> False"


def test_wrote_stub_bash_redirect():
    recs = [_asst_bash("t1", "echo '{...}' > sessions/dev-env/x.stub.md")]
    assert hook.wrote_stub(recs) is True
    return "wrote_stub: redirect into a *.stub.md -> True"


def test_reminder_cwd_non_ascii_cp1252_safe():
    msg = hook.format_reminder("C:/Ünïcödé/prôj")
    assert msg.isascii(), "reminder with a non-ASCII cwd must still be ASCII after sanitization"
    msg.encode("cp1252")  # must not raise
    assert "cwd:" in msg
    return "format_reminder: non-ASCII cwd sanitized -> ASCII/cp1252-safe"


def _run_hook_raw(raw_stdin, home):
    """Drive the real hook with arbitrary raw stdin (for the fail-open paths)."""
    home = Path(home)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=raw_stdin,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_e2e_empty_stdin_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook_raw("", home)
    assert rc == 0, f"empty stdin expected exit 0, got {rc} (stderr={err!r})"
    assert out.strip() == "" and err.strip() == ""
    return "e2e empty stdin -> exit 0, no output (fail-open)"


def test_e2e_non_dict_payload_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook_raw("[1, 2, 3]", home)
    assert rc == 0, f"non-dict payload expected exit 0, got {rc} (stderr={err!r})"
    assert out.strip() == "" and err.strip() == ""
    return "e2e non-dict JSON payload -> exit 0, no output (fail-open)"


def main():
    tests = [
        # report_intent
        ("report_intent report group", test_report_intent_report_group),
        ("report_intent verify group", test_report_intent_verify_group),
        ("report_intent 'production fix'", test_report_intent_production_fix),
        ("report_intent text-item form", test_report_intent_text_item_form),
        ("report_intent not in assistant text", test_report_intent_not_in_assistant_text),
        ("report_intent not in tool_result", test_report_intent_not_in_tool_result),
        ("report_intent not in isMeta", test_report_intent_not_in_ismeta),
        ("report_intent not in compact summary", test_report_intent_not_in_compact_summary),
        ("report_intent not in command wrapper", test_report_intent_not_in_command_wrapper),
        ("report_intent absent", test_report_intent_absent),
        # substantive_tool_count
        ("substantive counts each tool", test_substantive_counts_each_tool),
        ("substantive ignores bookkeeping/delegation/text", test_substantive_ignores_bookkeeping_delegation_and_text),
        ("substantive parallel in one record", test_substantive_parallel_in_one_record),
        ("substantive boundary 4 vs 5", test_substantive_boundary_4_and_5),
        ("substantive ignores isSidechain records", test_substantive_ignores_sidechain_records),
        ("substantive mixed isSidechain and main", test_substantive_mixed_sidechain_and_main),
        # opened_or_merged_pr
        ("pr create detected", test_pr_create_detected),
        ("pr merge detected", test_pr_merge_detected),
        ("pr create in heredoc not matched", test_pr_create_in_heredoc_not_matched),
        ("pr create in subshell not matched", test_pr_create_in_subshell_not_matched),
        ("pr create --help only not matched", test_pr_create_help_only_not_matched),
        ("gh pr checks/view not matched", test_gh_pr_checks_not_matched),
        ("pr absent", test_pr_absent),
        ("pr create isSidechain not counted", test_pr_create_sidechain_not_counted),
        ("pr merge isSidechain not counted", test_pr_merge_sidechain_not_counted),
        # wrote_stub
        ("wrote_stub Write", test_wrote_stub_write),
        ("wrote_stub Edit", test_wrote_stub_edit),
        ("wrote_stub backslash path", test_wrote_stub_backslash_path),
        ("wrote_stub non-stub .md no match", test_wrote_stub_non_stub_md_no_match),
        ("wrote_stub Bash reference", test_wrote_stub_bash_reference),
        ("wrote_stub Bash no ref", test_wrote_stub_bash_no_ref),
        ("wrote_stub isSidechain write not counted", test_wrote_stub_sidechain_write_not_counted),
        ("wrote_stub isSidechain bash not counted", test_wrote_stub_sidechain_bash_not_counted),
        # is_review_only_session
        ("review only detected", test_review_only_detected),
        ("review prose not matched", test_review_prose_not_matched),
        ("review absent", test_review_absent),
        # skip_override
        ("skip override user string", test_skip_override_user_string),
        ("skip override 'no stub'", test_skip_override_no_stub),
        ("skip override text item", test_skip_override_text_item),
        ("skip override tool_result not counted", test_skip_override_tool_result_not_counted),
        ("skip override compact summary not counted", test_skip_override_compact_summary_not_counted),
        ("skip override absent", test_skip_override_absent),
        # evaluate
        ("evaluate fire", test_evaluate_fire),
        ("evaluate wrote-stub resolved", test_evaluate_wrote_stub_resolved),
        ("evaluate PR resolved", test_evaluate_pr_resolved),
        ("evaluate skip resolved", test_evaluate_skip_resolved),
        ("evaluate /review resolved", test_evaluate_review_resolved),
        ("evaluate no-intent no-op", test_evaluate_no_intent_noop),
        ("evaluate below-threshold no-op", test_evaluate_intent_below_threshold_noop),
        ("evaluate task-bookkeeping volume no-op", test_evaluate_task_bookkeeping_alone_does_not_cross_threshold),
        ("evaluate isSidechain-only work no-op", test_evaluate_sidechain_only_work_does_not_cross_threshold),
        # format_reminder / robustness
        ("reminder cp1252-encodable", test_reminder_is_cp1252_encodable),
        ("reminder includes cwd", test_reminder_includes_cwd),
        ("malformed records do not disable", test_malformed_records_do_not_disable),
        # behavioral end-to-end
        ("e2e fire blocks on stderr", test_e2e_fire_blocks_on_stderr),
        ("e2e wrote-stub allows", test_e2e_wrote_stub_allows),
        ("e2e PR allows", test_e2e_pr_allows),
        ("e2e /review allows", test_e2e_review_allows),
        ("e2e skip allows", test_e2e_skip_allows),
        ("e2e no-intent allows", test_e2e_no_intent_allows),
        ("e2e below-threshold allows", test_e2e_intent_below_threshold_allows),
        ("e2e stop_hook_active allows", test_e2e_stop_hook_active_allows),
        ("e2e sentinel suppresses re-fire", test_e2e_sentinel_suppresses_refire),
        ("e2e isSidechain-only work allows", test_e2e_sidechain_only_work_allows),
        # --- review-of-PR-#706 fixes ---
        ("report_intent 'analytics' not matched", test_report_intent_analytics_not_matched),
        ("report_intent bare 'deploy' not matched", test_report_intent_bare_deploy_not_matched),
        ("report_intent 'check the deploy' matched", test_report_intent_check_the_deploy_matched),
        ("review /review-* hyphen not matched", test_review_cmd_hyphen_suffix_not_matched),
        ("wrote_stub bash read (ls/cat) not matched", test_wrote_stub_bash_read_not_matched),
        ("wrote_stub bash redirect matched", test_wrote_stub_bash_redirect),
        ("reminder non-ASCII cwd cp1252-safe", test_reminder_cwd_non_ascii_cp1252_safe),
        ("e2e empty stdin allows", test_e2e_empty_stdin_allows),
        ("e2e non-dict payload allows", test_e2e_non_dict_payload_allows),
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
