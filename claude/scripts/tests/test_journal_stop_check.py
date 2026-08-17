#!/usr/bin/env python3
"""Unit + behavioral tests for journal-stop-check.py (ADR-091).

journal-stop-check.py is a Stop hook. Its Check 1 (stub-push sentinel) emits a
CLAUDE-facing archive instruction -- "call ccd_session_mgmt__archive_session ...
Then stop." -- which must reach Claude's context. For a Stop hook, exit-0 stdout
is NOT added to Claude's context (only UserPromptSubmit / UserPromptExpansion /
SessionStart are), so the reminder is now emitted on STDERR with exit 2 (blocking
the stop), mirroring stop-tile-enumeration-gate.py (ADR-088). Its Checks 2-3
(stale-draft / unmerged-branch advisories) stay NON-blocking but now ride the
_hookout systemMessage channel (exit 0) rather than plain stdout: a Stop hook's
exit-0 stdout is invisible (transcript-only), so the former print() surfaced
nothing (ADR-103, dev-env#740). They point at work for a later, dedicated session
and must not block.

The stub-push sentinel is scoped to the pushing session's own session_id
(dev-env#980, ADR-091 Amendment 2): a global sentinel previously let any
concurrent session's Stop consume it, producing both false-positive archive
instructions and missed reminders. `consume_stub_pushed_sentinel(session_id,
sentinel=...)` and the behavioral `_run_hook`/`_sentinel_path` fixtures below
were updated accordingly; see test_e2e_cross_session_sentinel_not_consumed and
test_e2e_no_session_id_never_blocks for the direct regression coverage.

Three layers, mirroring this repo's hook-test convention:

  * Pure / fixture-helper tests exercise the changed surface offline:
    - archive_reminder_message(): ASCII / cp1252-encodable (so the exit-2 stderr
      text cannot vanish under Claude Code's cp1252 hook-output pipe on Windows),
      names the archive MCP tool + the list_sessions lookup, and (ADR-091
      Amendment 3, dev-env#1002) unconditionally names the explicit-agreement/
      never-speculative invariant -- this does NOT depend on any transcript scan,
      so it survives a detection miss in in_flight_work_note().
    - parse_stop_hook_active(): True only when the payload sets the flag; False on
      false / missing / empty / malformed / non-dict stdin (never suppresses a
      genuine first Stop).
    - parse_session_id(): the payload's session_id, or "" on missing/empty/
      malformed/non-dict stdin (dev-env#980).
    - consume_stub_pushed_sentinel(session_id, sentinel=tmp): an explicit
      sentinel override behaves as before (present -> reminder + delete; second
      read -> None; absent -> None). Without an override, the path is derived
      from session_id; a falsy session_id with no override returns None without
      touching the filesystem.
    - pending_task_count() / open_background_agent_count() / format_in_flight_note()
      / parse_transcript_path() / in_flight_work_note(): the count-derived half of
      the in-flight-work caveat (dev-env#1002, ADR-091 Amendment 3) appended to the
      archive reminder when the session's own transcript still shows pending/
      in_progress tasks tracked via TaskCreate/TaskUpdate (NOT TodoWrite -- this
      harness has no TodoWrite tool at all, confirmed live) or an unresolved
      backgrounded Agent call. Both counters skip isSidechain records so a
      subagent's own task/agent activity is never attributed to the main
      session -- see the code comments and ADR-091 Amendment 3's "Correction
      during /review before merge" section for exact semantics and the real-data
      verification behind each design choice.

  * A behavioral layer drives the real hook end-to-end over stdin via subprocess,
    with HOME/USERPROFILE pointed at a temp dir so the per-session sentinel
    resolves under the tmp scratch (isolated from the real ~/.claude/scratch):
    - flag present (own session_id) + stop_hook_active=false -> exit 2, reminder
      on stderr, EMPTY stdout (Claude Code shows a Stop hook's stderr on exit 2,
      not stdout), and the flag is consumed.
    - no flag -> exit 0 (advisory path; the git advisory calls fail closed against
      the nonexistent tmp journal repo, so output is empty).
    - no flag + a planted stale (uncomposed, pre-today) stub in the tmp journal repo
      -> exit 0 with the Checks 2-3 advisory delivered as a `{"systemMessage": ...}`
      JSON object on STDOUT and empty stderr (the re-pin of the corrected channel:
      systemMessage, not the former invisible plain-stdout print -- dev-env#740).
    - flag present (own session_id) + stop_hook_active=true -> exit 0 (loop guard:
      no re-block) and the flag is PRESERVED (not consumed without delivery).
    - flag present under a DIFFERENT session_id than the one in the payload ->
      exit 0 (no block) and that other session's flag is left untouched
      (dev-env#980's actual regression case).
    - flag present but the payload carries no session_id at all -> exit 0 (no
      block), regardless of what flags exist on disk.
    - flag present + a planted transcript with a pending task (TaskCreate/
      TaskUpdate) -> exit 2, stderr is byte-exact: base reminder + task-count
      phrase (dev-env#1002).
    - flag present + a planted transcript with an unresolved backgrounded Agent
      call (including the omitted-run_in_background-flag shape) -> exit 2,
      stderr is byte-exact: base reminder + agent-count phrase (dev-env#1002).
    - flag present + a planted transcript with a RESOLVED backgrounded Agent call
      (a matching completion notification is present) -> exit 2, stderr is
      byte-identical to the unmodified archive_reminder_message() text -- the
      no-false-positive regression case (dev-env#1002).

main()'s advisory branches (stale drafts / unmerged branches / orphan cleanup) run
subprocess git against the journal repo and are not unit-tested here (pure-helper
convention) -- the end-to-end no-flag run exercises their fail-closed path.

Usage:
    py -3 claude/scripts/tests/test_journal_stop_check.py

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
SCRIPT = REPO_ROOT / "claude" / "scripts" / "journal-stop-check.py"

# The script imports _winsubp (sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))
import _hookutil  # noqa: E402  -- needs the sys.path.insert above

_spec = importlib.util.spec_from_file_location("journal_stop_check", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
jsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jsc)  # safe: main() is guarded by __main__

# stub-push-archive-reminder.py -- loaded too, only for the cross-file
# SENTINEL_PREFIX parity test below (dev-env#980 review finding: nothing
# previously caught the two literals drifting apart, which would silently
# and completely kill the archive-reminder mechanism this PR fixes).
_WRITER_SCRIPT = REPO_ROOT / "claude" / "scripts" / "stub-push-archive-reminder.py"
_writer_spec = importlib.util.spec_from_file_location("stub_push_archive_reminder", _WRITER_SCRIPT)
assert _writer_spec and _writer_spec.loader, f"cannot load module spec from {_WRITER_SCRIPT}"
spar = importlib.util.module_from_spec(_writer_spec)
_writer_spec.loader.exec_module(spar)  # safe: main() is guarded by __main__


# --- transcript-record builders (dev-env#1002; mirrors
# test_stop_experiment_verdict_gate.py's builder conventions) -----------------

def _asst_tool(tid, name, inp, sidechain=False):
    rec = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": tid, "input": inp}]}}
    if sidechain:
        rec["isSidechain"] = True
    return rec


def _user_str(text):
    return {"type": "user", "message": {"content": text}}


def _tool_result(tool_use_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}}


def _task_create_result(tool_use_id, task_num, subject="Do the thing"):
    """The tool_result a real TaskCreate call produces -- the ONLY place the
    assigned task id appears (never in the tool_use's own input). Confirmed
    live: "Task #N created successfully: <subject>" (dev-env#1002 review
    finding)."""
    return _tool_result(tool_use_id, f"Task #{task_num} created successfully: {subject}")


def _agent_notification_text(tool_use_id, status="completed"):
    return (
        "<task-notification>\n"
        "<task-id>agent-1</task-id>\n"
        f"<tool-use-id>{tool_use_id}</tool-use-id>\n"
        "<output-file>out.txt</output-file>\n"
        f"<status>{status}</status>\n"
        "</task-notification>"
    )


def _queue_operation_notification(tool_use_id, status="completed"):
    """Real shape confirmed live: a queue-operation record's top-level
    "content" field is a bare string carrying the marker (dev-env#1002 review
    finding -- half of real completions live ONLY in this and the attachment
    shape below, never in a type=="user" record)."""
    return {
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": "2026-08-16T00:00:00.000Z",
        "sessionId": "s1",
        "content": _agent_notification_text(tool_use_id, status),
    }


def _attachment_notification(tool_use_id, status="completed"):
    """Real shape confirmed live: an attachment record's nested
    attachment.prompt field is a bare string carrying the marker
    (dev-env#1002 review finding)."""
    return {
        "type": "attachment",
        "isSidechain": False,
        "attachment": {
            "type": "queued_command",
            "prompt": _agent_notification_text(tool_use_id, status),
        },
    }


def test_sentinel_prefix_matches_writer_module():
    assert jsc.SENTINEL_PREFIX == spar.SENTINEL_PREFIX, (
        f"journal-stop-check.py's SENTINEL_PREFIX ({jsc.SENTINEL_PREFIX!r}) must match "
        f"stub-push-archive-reminder.py's ({spar.SENTINEL_PREFIX!r}) or the reader can never "
        f"find a sentinel the writer wrote -- a silent, total failure of the whole mechanism "
        f"(dev-env#980 review finding)"
    )
    return "reader and writer SENTINEL_PREFIX literals match (guards against silent drift)"


# --- pure: _SAFE_SESSION_ID (dev-env#980 review finding: unsanitized session_id
# in a filesystem path this hook unlink()'s) -------------------------------------

def test_safe_session_id_accepts_uuid_shape():
    assert jsc._SAFE_SESSION_ID.match("a1b2c3d4-e5f6-4789-a012-3456789abcde")
    return "a real UUID-shaped session_id matches"


def test_safe_session_id_rejects_path_traversal():
    for bad in ("../../etc/passwd", "a/b", "a\\b", "a b", "a.b", ""):
        assert not jsc._SAFE_SESSION_ID.match(bad), f"{bad!r} must not match"
    return "path separators, whitespace, dots, and empty string are all rejected"


# --- pure: archive_reminder_message() ------------------------------------------

def test_archive_message_is_cp1252_encodable():
    msg = jsc.archive_reminder_message()
    assert msg.isascii(), f"archive reminder must be ASCII, got: {msg!r}"
    msg.encode("cp1252")  # must not raise -- Claude Code pipes hook output as cp1252 on Windows
    return "archive reminder is ASCII / cp1252-safe (cannot vanish on the exit-2 stderr path)"


def test_archive_message_names_tool_and_lookup():
    msg = jsc.archive_reminder_message()
    assert "ccd_session_mgmt__archive_session" in msg, msg
    assert "list_sessions" in msg, msg
    return "archive reminder names the archive MCP tool + the list_sessions lookup"


def test_archive_message_names_explicit_agreement_requirement():
    # dev-env#1002 review finding: this invariant must be UNCONDITIONAL (it does
    # not depend on the transcript scan in_flight_work_note() runs) so it survives
    # every detection-miss degrade path. An earlier version put this clause inside
    # format_in_flight_note() instead, so it silently vanished whenever the
    # best-effort scan came up empty -- reverting the message verbatim to the
    # exact unconditional-archive bug this fix exists to prevent.
    msg = jsc.archive_reminder_message()
    assert "explicit" in msg.lower(), msg
    assert "speculatively" in msg.lower(), msg
    return "archive reminder unconditionally states the explicit-agreement/never-speculative invariant"


# --- pure: parse_stop_hook_active() --------------------------------------------

def test_parse_stop_hook_active_true():
    assert jsc.parse_stop_hook_active(json.dumps({"stop_hook_active": True})) is True
    return "stop_hook_active:true -> True"


def test_parse_stop_hook_active_false():
    assert jsc.parse_stop_hook_active(json.dumps({"stop_hook_active": False})) is False
    return "stop_hook_active:false -> False"


def test_parse_stop_hook_active_missing():
    assert jsc.parse_stop_hook_active(json.dumps({"session_id": "x"})) is False
    return "flag absent -> False"


def test_parse_stop_hook_active_empty_and_malformed():
    assert jsc.parse_stop_hook_active("") is False
    assert jsc.parse_stop_hook_active("not json{") is False
    assert jsc.parse_stop_hook_active("[]") is False  # non-dict JSON -> .get raises -> False
    return "empty / malformed / non-dict stdin -> False (never suppresses a genuine first Stop)"


# --- pure: parse_session_id() (dev-env#980) -------------------------------------

def test_parse_session_id_present():
    assert jsc.parse_session_id(json.dumps({"session_id": "abc-123"})) == "abc-123"
    return "session_id present -> returned"


def test_parse_session_id_missing():
    assert jsc.parse_session_id(json.dumps({"stop_hook_active": False})) == ""
    return "session_id absent -> ''"


def test_parse_session_id_empty_and_malformed():
    assert jsc.parse_session_id("") == ""
    assert jsc.parse_session_id("not json{") == ""
    assert jsc.parse_session_id("[]") == ""  # non-dict JSON -> .get raises -> ""
    return "empty / malformed / non-dict stdin -> '' (never raises)"


# --- pure: parse_transcript_path() (dev-env#1002) --------------------------------

def test_parse_transcript_path_present():
    assert jsc.parse_transcript_path(json.dumps({"transcript_path": "/tmp/x.jsonl"})) == "/tmp/x.jsonl"
    return "transcript_path present -> returned"


def test_parse_transcript_path_missing():
    assert jsc.parse_transcript_path(json.dumps({"session_id": "s1"})) == ""
    return "transcript_path absent -> ''"


def test_parse_transcript_path_empty_and_malformed():
    assert jsc.parse_transcript_path("") == ""
    assert jsc.parse_transcript_path("not json{") == ""
    assert jsc.parse_transcript_path("[]") == ""  # non-dict JSON -> .get raises -> ""
    return "empty / malformed / non-dict stdin -> '' (never raises)"


# --- fixture: consume_stub_pushed_sentinel(session_id, sentinel=tmp, scratch=tmp) --
# The *sentinel* override path (explicit fixture injection) and the
# *session_id*-derives-a-path production path are exercised separately --
# passing session_id alongside an explicit sentinel override would be dead
# (the sentinel branch short-circuits before session_id is ever read), so
# the override tests below omit it (dev-env#980 review finding).

def test_consume_present_returns_and_deletes():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed-s1.flag"
        flag.write_text("1")
        msg = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        assert msg == jsc.archive_reminder_message()
        assert not flag.exists(), "sentinel must be consumed (deleted) on read"
    return "present flag (explicit sentinel override) -> reminder returned + flag deleted (one-shot)"


def test_consume_is_one_shot():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed-s1.flag"
        flag.write_text("1")
        first = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        second = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        assert first is not None and second is None
    return "second read after consume -> None (block fires at most once)"


def test_consume_absent_returns_none():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed-s1.flag"  # never created
        assert jsc.consume_stub_pushed_sentinel(sentinel=flag) is None
    return "absent flag -> None"


def test_consume_derives_path_from_session_id():
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        derived = _hookutil.sentinel_path(jsc.SENTINEL_PREFIX, "s1", scratch=scratch)
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text("1")
        # No explicit sentinel override -- must derive the same path itself,
        # via the injectable *scratch* param (not a monkeypatch of
        # _hookutil.SCRATCH -- dev-env#980 review finding).
        msg = jsc.consume_stub_pushed_sentinel(session_id="s1", scratch=scratch)
        assert msg == jsc.archive_reminder_message()
        assert not derived.exists()
    return "no sentinel override -> path derived from session_id via _hookutil.sentinel_path(scratch=...)"


def test_consume_empty_session_id_no_override_returns_none_without_touching_disk():
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        # Plant a file at what an empty-session_id path WOULD resolve to, to
        # prove it is never even looked at.
        degenerate = scratch / f"{jsc.SENTINEL_PREFIX}.flag"
        degenerate.write_text("1")
        result = jsc.consume_stub_pushed_sentinel(session_id="", scratch=scratch)
        assert result is None
        assert degenerate.exists(), "a falsy session_id must never touch any file, degenerate or not"
    return "falsy session_id + no override -> None, filesystem untouched (dev-env#980 guard)"


def test_consume_unsafe_session_id_no_override_returns_none_without_touching_disk():
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        result = jsc.consume_stub_pushed_sentinel(session_id="../escape", scratch=scratch)
        assert result is None
    return "session_id outside _SAFE_SESSION_ID + no override -> None (dev-env#980 review finding)"


# --- pure: pending_task_count() (dev-env#1002; TaskCreate/TaskUpdate -- NOT
# TodoWrite, which does not exist in this harness at all, confirmed live. See
# ADR-091 Amendment 3 "Correction during /review before merge".) -----------------

def test_pending_task_count_empty_records():
    assert jsc.pending_task_count([]) == 0
    return "no records -> 0"


def test_pending_task_count_create_defaults_pending():
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
    ]
    assert jsc.pending_task_count(recs) == 1
    return "a created task with no TaskUpdate defaults to pending -> counted"


def test_pending_task_count_update_to_in_progress_counts():
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
        _asst_tool("u1", "TaskUpdate", {"taskId": "1", "status": "in_progress"}),
    ]
    assert jsc.pending_task_count(recs) == 1
    return "task updated to in_progress -> still counted"


def test_pending_task_count_update_to_completed_excludes():
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
        _asst_tool("u1", "TaskUpdate", {"taskId": "1", "status": "completed"}),
    ]
    assert jsc.pending_task_count(recs) == 0
    return "task updated to completed -> excluded"


def test_pending_task_count_update_to_deleted_excludes():
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
        _asst_tool("u1", "TaskUpdate", {"taskId": "1", "status": "deleted"}),
    ]
    assert jsc.pending_task_count(recs) == 0
    return "task updated to deleted -> excluded (this harness's full observed status vocabulary)"


def test_pending_task_count_folds_across_sequence_not_last_wins():
    # Two tasks: #1 finished, #2 still pending. TodoWrite's "last call replaces
    # the whole list" model does NOT apply here -- TaskCreate/TaskUpdate must
    # fold status per-id across the WHOLE sequence (dev-env#1002 review finding).
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "a", "description": "d", "activeForm": "a-ing"}),
        _task_create_result("c1", "1"),
        _asst_tool("c2", "TaskCreate", {"subject": "b", "description": "d", "activeForm": "b-ing"}),
        _task_create_result("c2", "2"),
        _asst_tool("u1", "TaskUpdate", {"taskId": "1", "status": "in_progress"}),
        _asst_tool("u2", "TaskUpdate", {"taskId": "1", "status": "completed"}),
    ]
    assert jsc.pending_task_count(recs) == 1
    return "task #1 completed, task #2 never explicitly updated -> 1, folded per-id not last-call-wins"


def test_pending_task_count_unresolved_create_not_counted():
    # A TaskCreate tool_use with no matching tool_result -- its assigned id can
    # never be learned from this transcript, so it cannot be tracked.
    recs = [_asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"})]
    assert jsc.pending_task_count(recs) == 0
    return "TaskCreate with no paired tool_result -> 0 (assigned task id unknowable, degrades safely)"


def test_pending_task_count_isSidechain_create_excluded():
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}, sidechain=True),
        _task_create_result("c1", "1"),
    ]
    assert jsc.pending_task_count(recs) == 0
    return "isSidechain TaskCreate -> excluded (a subagent's own task list, not the main session's)"


def test_pending_task_count_isSidechain_update_excluded():
    # A main-session-created task whose ONLY status update comes from a
    # sidechain record must not have that update applied.
    recs = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
        _asst_tool("u1", "TaskUpdate", {"taskId": "1", "status": "completed"}, sidechain=True),
    ]
    assert jsc.pending_task_count(recs) == 1
    return "isSidechain TaskUpdate -> not applied, main-session-created task stays at its pending default"


def test_pending_task_count_malformed_records_do_not_raise():
    recs = [
        None, "str", 123, [], {"type": "weird"},
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
    ]
    assert jsc.pending_task_count(recs) == 1
    return "malformed/non-dict records mixed in -> does not raise, real task still counted"


# --- pure: open_background_agent_count() (dev-env#1002) -------------------------

def test_open_background_agent_count_no_agent_calls():
    assert jsc.open_background_agent_count([]) == 0
    return "no Agent calls -> 0"


def test_open_background_agent_count_resolved_via_user_record():
    recs = [
        _asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"}),
        _user_str(_agent_notification_text("toolu_1")),
    ]
    assert jsc.open_background_agent_count(recs) == 0
    return "backgrounded call resolved via a type=='user' notification -> 0"


def test_open_background_agent_count_resolved_via_queue_operation_record():
    recs = [
        _asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"}),
        _queue_operation_notification("toolu_1"),
    ]
    assert jsc.open_background_agent_count(recs) == 0
    return ("backgrounded call resolved via a queue-operation record's content field "
            "(dev-env#1002 review finding: half of real completions live only in this "
            "or the attachment shape, never in a type=='user' record)")


def test_open_background_agent_count_resolved_via_attachment_record():
    recs = [
        _asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"}),
        _attachment_notification("toolu_1"),
    ]
    assert jsc.open_background_agent_count(recs) == 0
    return "backgrounded call resolved via an attachment record's attachment.prompt field"


def test_open_background_agent_count_unresolved():
    recs = [_asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"})]
    assert jsc.open_background_agent_count(recs) == 1
    return "one backgrounded Agent call with NO matching notification anywhere -> 1 (open)"


def test_open_background_agent_count_foreground_call_excluded():
    recs = [_asst_tool("toolu_1", "Agent", {"run_in_background": False, "prompt": "x"})]
    assert jsc.open_background_agent_count(recs) == 0
    return "explicit run_in_background:false -> 0 (blocks the turn, cannot be in flight at Stop)"


def test_open_background_agent_count_omitted_flag_counts_as_backgrounded():
    # dev-env#1002 review finding: an omitted flag is the DOCUMENTED DEFAULT for a
    # top-level spawn (pre-tool-use-nested-agent-background-guard.py's own
    # docstring) and behaves like an explicit true, never like an explicit false --
    # confirmed live. An earlier version required strict `is True`, which wrongly
    # excluded this shape (missing roughly a third of real backgrounded calls).
    recs = [_asst_tool("toolu_1", "Agent", {"prompt": "x"})]
    assert jsc.open_background_agent_count(recs) == 1
    return "run_in_background omitted -> counted as backgrounded (unresolved here -> 1, not 0)"


def test_open_background_agent_count_omitted_flag_resolves_normally():
    recs = [
        _asst_tool("toolu_1", "Agent", {"prompt": "x"}),
        _user_str(_agent_notification_text("toolu_1")),
    ]
    assert jsc.open_background_agent_count(recs) == 0
    return "run_in_background omitted + a matching notification -> 0 (resolves the same as explicit true)"


def test_open_background_agent_count_isSidechain_call_excluded():
    recs = [_asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"}, sidechain=True)]
    assert jsc.open_background_agent_count(recs) == 0
    return "isSidechain Agent call -> excluded (a subagent's own child spawn, not the main session's)"


def test_open_background_agent_count_two_calls_one_resolved():
    recs = [
        _asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"}),
        _asst_tool("toolu_2", "Agent", {"run_in_background": True, "prompt": "y"}),
        _user_str(_agent_notification_text("toolu_1")),
    ]
    assert jsc.open_background_agent_count(recs) == 1
    return "two backgrounded calls, one resolved one not -> 1"


# --- pure: format_in_flight_note() (dev-env#1002; count-derived sentence ONLY --
# the explicit-agreement invariant now lives unconditionally in
# archive_reminder_message() instead, see above) ----------------------------------

def test_format_in_flight_note_both_zero():
    assert jsc.format_in_flight_note(0, 0) == ""
    return "both zero -> ''"


def test_format_in_flight_note_tasks_only():
    note = jsc.format_in_flight_note(2, 0)
    assert note == "Caution: 2 pending/in-progress task item(s) may still be in flight."
    assert note.isascii(), f"note must be ASCII, got: {note!r}"
    note.encode("cp1252")  # must not raise
    return "tasks only -> exact expected sentence, ASCII/cp1252-safe"


def test_format_in_flight_note_agents_only():
    note = jsc.format_in_flight_note(0, 1)
    assert note == "Caution: 1 still-running background agent(s) may still be in flight."
    assert note.isascii(), f"note must be ASCII, got: {note!r}"
    note.encode("cp1252")
    return "agents only -> exact expected sentence, ASCII/cp1252-safe"


def test_format_in_flight_note_both():
    note = jsc.format_in_flight_note(3, 2)
    assert note == (
        "Caution: 3 pending/in-progress task item(s) and 2 still-running "
        "background agent(s) may still be in flight."
    )
    assert note.isascii(), f"note must be ASCII, got: {note!r}"
    note.encode("cp1252")
    return "both nonzero -> exact expected sentence (both counts named), ASCII/cp1252-safe"


# --- fixture: in_flight_work_note() (dev-env#1002) -------------------------------

def test_in_flight_work_note_via_explicit_transcript_path():
    records = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
    ]
    with tempfile.TemporaryDirectory() as d:
        tpath = Path(d) / "t.jsonl"
        tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        note = jsc.in_flight_work_note(str(tpath), "irrelevant-session-id")
    assert note == jsc.format_in_flight_note(1, 0)
    return "explicit transcript_path resolving to a real file -> note reflects its content"


def test_in_flight_work_note_falls_back_to_find_transcript():
    records = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
        _asst_tool("c2", "TaskCreate", {"subject": "y", "description": "y", "activeForm": "y-ing"}),
        _task_create_result("c2", "2"),
    ]
    with tempfile.TemporaryDirectory() as d:
        projects = Path(d) / "projects"
        session_dir = projects / "some-project"
        session_dir.mkdir(parents=True)
        tpath = session_dir / "sess-99.jsonl"
        tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        # transcript_path_str absent/unresolvable -> falls back to find_transcript(session_id)
        note = jsc.in_flight_work_note("", "sess-99", projects=projects)
    assert note == jsc.format_in_flight_note(2, 0)
    return "empty/unresolvable transcript_path_str -> falls back to _hookutil.find_transcript(session_id, projects=...)"


def test_in_flight_work_note_nonfile_path_falls_back_to_find_transcript():
    # dev-env#1002 review finding: a transcript_path_str that is PRESENT but does
    # not name a real file (a stale path from a moved/resumed session) must still
    # fall back to find_transcript(session_id) -- exercises candidate.is_file()
    # returning False specifically, distinct from the "path empty" branch above.
    records = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
    ]
    with tempfile.TemporaryDirectory() as d:
        projects = Path(d) / "projects"
        session_dir = projects / "some-project"
        session_dir.mkdir(parents=True)
        tpath = session_dir / "sess-77.jsonl"
        tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        stale_path = str(Path(d) / "does-not-exist" / "stale.jsonl")
        note = jsc.in_flight_work_note(stale_path, "sess-77", projects=projects)
    assert note == jsc.format_in_flight_note(1, 0)
    return "present-but-not-a-file transcript_path_str -> falls back to find_transcript (is_file()==False branch)"


def test_in_flight_work_note_neither_resolves_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        projects = Path(d) / "projects"  # never created
        note = jsc.in_flight_work_note("", "no-such-session", projects=projects)
    assert note == ""
    return "no transcript_path and no resolvable session_id -> '' (never raises)"


def test_in_flight_work_note_malformed_transcript_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        tpath = Path(d) / "bad.jsonl"
        tpath.write_text("not json at all {{{ TaskCreate", encoding="utf-8")
        note = jsc.in_flight_work_note(str(tpath), "irrelevant")
    assert note == ""
    return "unparseable transcript content -> '' (_parse_records drops the malformed line; no tasks/agents found)"


def test_in_flight_work_note_invalid_utf8_bytes_returns_empty():
    # dev-env#1002 review finding: the fail-open except-Exception path had no
    # coverage of an ACTUAL raise -- the malformed-transcript case above degrades
    # via normal control flow (_parse_records just drops the bad line). Writing
    # raw invalid UTF-8 bytes forces path.read_text(encoding="utf-8") to genuinely
    # raise UnicodeDecodeError, exercising the real fail-open guarantee without
    # monkeypatching anything.
    with tempfile.TemporaryDirectory() as d:
        tpath = Path(d) / "bad_bytes.jsonl"
        tpath.write_bytes(b'{"type": "assistant"}\n\xff\xfe not valid utf-8, TaskCreate\n')
        note = jsc.in_flight_work_note(str(tpath), "irrelevant")
    assert note == ""
    return "invalid UTF-8 bytes -> genuine UnicodeDecodeError caught by the blanket except -> ''"


def test_in_flight_work_note_prefilter_skips_full_parse_when_no_marker_present():
    # dev-env#1002 review finding (performance): when neither "TaskCreate",
    # "TaskUpdate", nor "run_in_background" appears anywhere in the raw text,
    # both counts are provably 0 and the full per-line json.loads pass is
    # skipped entirely, matching every sibling Stop hook's pre-filter pattern.
    records = [_asst_tool("t1", "SomeOtherTool", {"x": 1})]
    with tempfile.TemporaryDirectory() as d:
        tpath = Path(d) / "no_markers.jsonl"
        tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        note = jsc.in_flight_work_note(str(tpath), "irrelevant")
    assert note == ""
    return "no TaskCreate/TaskUpdate/run_in_background substring anywhere -> '' via the pre-filter"


# --- behavioral: real hook over stdin via subprocess (HOME-isolated sentinel) --

def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _sentinel_path(home, session_id):
    return Path(home) / ".claude" / "scratch" / f"stub-pushed-{session_id}.flag"


def _run_hook(home, *, flag_present, stop_hook_active, session_id="s1", flag_session_id=None,
              include_session_id=True, records=None):
    """Drive the real hook once. Returns (exit_code, stdout, stderr).

    *session_id* is the id sent in the Stop payload (the "current session").
    *flag_session_id* -- the id whose sentinel gets planted when flag_present
    -- defaults to *session_id* (same-session, the common case); pass a
    different value to simulate another session's still-armed sentinel
    (dev-env#980). *include_session_id=False* omits session_id from the
    payload entirely. *records* (dev-env#1002), when given, is a list of
    transcript records written as JSONL to a tmp file under *home*, with its
    path added to the payload as "transcript_path" -- when omitted (the
    default), the payload carries no such key, byte-for-byte identical to
    this helper's behavior before dev-env#1002 (every existing call site is
    unaffected).
    """
    home = Path(home)
    if flag_present:
        sentinel = _sentinel_path(home, flag_session_id if flag_session_id is not None else session_id)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("1")
    payload_dict = {"stop_hook_active": stop_hook_active, "hook_event_name": "Stop"}
    if include_session_id:
        payload_dict["session_id"] = session_id
    if records is not None:
        tpath = home / "transcript.jsonl"
        tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        payload_dict["transcript_path"] = str(tpath)
    payload = json.dumps(payload_dict)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=payload,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_e2e_flag_blocks_on_stderr_and_consumes():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False, session_id="s1")
        consumed = not _sentinel_path(home, "s1").exists()
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[journal-stop-hook]" in err and "ccd_session_mgmt__archive_session" in err, err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    assert consumed, "sentinel must be consumed on the blocking run"
    return "e2e own-session flag + not-continuation -> exit 2, reminder on stderr, empty stdout, flag consumed"


def test_e2e_flag_blocks_with_pending_tasks_note():
    records = [
        _asst_tool("c1", "TaskCreate", {"subject": "x", "description": "y", "activeForm": "x-ing"}),
        _task_create_result("c1", "1"),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False,
                                  session_id="s1", records=records)
    expected = f"[journal-stop-hook] {jsc.archive_reminder_message()} {jsc.format_in_flight_note(1, 0)}\n"
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert err == expected, f"got {err!r}, expected {expected!r}"
    return "e2e flag + transcript with a pending task -> exit 2, byte-exact base reminder + task-count phrase"


def test_e2e_flag_blocks_with_open_agent_note():
    records = [_asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"})]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False,
                                  session_id="s1", records=records)
    expected = f"[journal-stop-hook] {jsc.archive_reminder_message()} {jsc.format_in_flight_note(0, 1)}\n"
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert err == expected, f"got {err!r}, expected {expected!r}"
    return "e2e flag + transcript with an unresolved backgrounded Agent call -> exit 2, byte-exact agent-count phrase"


def test_e2e_flag_blocks_with_omitted_flag_agent_note():
    # dev-env#1002 review finding: an omitted run_in_background must count as
    # backgrounded end-to-end through main()'s real wiring, not only at the
    # pure-function level.
    records = [_asst_tool("toolu_1", "Agent", {"prompt": "x"})]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False,
                                  session_id="s1", records=records)
    expected = f"[journal-stop-hook] {jsc.archive_reminder_message()} {jsc.format_in_flight_note(0, 1)}\n"
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert err == expected, f"got {err!r}, expected {expected!r}"
    return "e2e flag + transcript with an OMITTED-flag Agent call, unresolved -> exit 2, counted as backgrounded"


def test_e2e_flag_blocks_with_resolved_agent_no_note():
    records = [
        _asst_tool("toolu_1", "Agent", {"run_in_background": True, "prompt": "x"}),
        _user_str(_agent_notification_text("toolu_1")),
    ]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False,
                                  session_id="s1", records=records)
    expected = f"[journal-stop-hook] {jsc.archive_reminder_message()}\n"
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert err == expected, (
        f"a RESOLVED backgrounded Agent call must produce the ORIGINAL, unmodified "
        f"reminder with no caveat appended -- got {err!r}, expected {expected!r}"
    )
    return ("e2e flag + transcript with a RESOLVED backgrounded Agent call -> stderr matches the "
            "ORIGINAL reminder exactly, no note appended (no-false-positive regression)")


def test_e2e_no_flag_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=False, stop_hook_active=False)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no flag -> exit 0 (advisory path, fail-closed against the tmp journal repo)"


def test_e2e_cross_session_sentinel_not_consumed():
    # The dev-env#980 regression: session A's push armed a sentinel; a
    # DIFFERENT session's Stop (session B) must not consume it or block.
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(
            home, flag_present=True, stop_hook_active=False,
            session_id="session-B", flag_session_id="session-A",
        )
        a_flag_survives = _sentinel_path(home, "session-A").exists()
    assert rc == 0, f"expected exit 0 (no cross-session block), got {rc} (stderr={err!r})"
    assert "ccd_session_mgmt__archive_session" not in err, err
    assert a_flag_survives, "session A's sentinel must be untouched by session B's Stop"
    return "e2e session A's flag + session B's Stop -> exit 0, no block, A's flag left intact (dev-env#980)"


def test_e2e_no_session_id_never_blocks():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(
            home, flag_present=True, stop_hook_active=False,
            flag_session_id="session-A", include_session_id=False,
        )
        a_flag_survives = _sentinel_path(home, "session-A").exists()
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    assert a_flag_survives, "no session_id in the payload -> no flag may be touched"
    return "e2e payload with no session_id -> exit 0 regardless of what flags exist on disk"


def test_e2e_stop_hook_active_allows_and_preserves_flag():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=True, session_id="s1")
        preserved = _sentinel_path(home, "s1").exists()
    assert rc == 0, f"expected exit 0 with stop_hook_active, got {rc} (stderr={err!r})"
    assert preserved, "flag must be preserved (not consumed) on a continuation so it can still deliver later"
    return "e2e own-session flag + stop_hook_active=true -> exit 0 (loop guard), flag preserved"


def test_e2e_stale_advisory_is_systemmessage():
    # The re-pin of the corrected Checks 2-3 channel (dev-env#740): plant an old,
    # uncomposed stub in the tmp journal repo (JOURNAL_REPO = <home>/Git/
    # engineering-journal) so stale_draft_artifacts() fires, then assert the
    # advisory arrives as a {"systemMessage": ...} JSON object on STDOUT (exit 0) --
    # NOT the former invisible plain-stdout print. The non-git tmp repo makes the
    # orphan-cleanup git status fail closed, so the planted stub stays "still stale".
    with tempfile.TemporaryDirectory() as home:
        stub = (
            Path(home) / "Git" / "engineering-journal" / "sessions" / "proj"
            / "2020-01-01_120000.stub.md"
        )
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("old uncomposed draft")
        rc, out, err = _run_hook(home, flag_present=False, stop_hook_active=False)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    assert err.strip() == "", f"stderr must be empty on the advisory path, got {err!r}"
    payload = json.loads(out)  # emit_advisory writes exactly one JSON object to stdout
    assert "systemMessage" in payload, f"advisory must be a systemMessage JSON, got {out!r}"
    assert "Stale draft artifact" in payload["systemMessage"], payload
    assert "2020-01-01" in payload["systemMessage"], payload
    return "e2e no flag + planted stale stub -> exit 0, Checks 2-3 delivered as a systemMessage JSON on stdout"


def main():
    tests = [
        ("archive message cp1252-encodable", test_archive_message_is_cp1252_encodable),
        ("archive message names tool + lookup", test_archive_message_names_tool_and_lookup),
        ("archive message names explicit agreement (dev-env#1002)", test_archive_message_names_explicit_agreement_requirement),
        ("parse stop_hook_active true", test_parse_stop_hook_active_true),
        ("parse stop_hook_active false", test_parse_stop_hook_active_false),
        ("parse stop_hook_active missing", test_parse_stop_hook_active_missing),
        ("parse stop_hook_active empty/malformed", test_parse_stop_hook_active_empty_and_malformed),
        ("SENTINEL_PREFIX matches writer module (dev-env#980)", test_sentinel_prefix_matches_writer_module),
        ("_SAFE_SESSION_ID accepts UUID shape (dev-env#980)", test_safe_session_id_accepts_uuid_shape),
        ("_SAFE_SESSION_ID rejects path traversal (dev-env#980)", test_safe_session_id_rejects_path_traversal),
        ("parse session_id present (dev-env#980)", test_parse_session_id_present),
        ("parse session_id missing (dev-env#980)", test_parse_session_id_missing),
        ("parse session_id empty/malformed (dev-env#980)", test_parse_session_id_empty_and_malformed),
        ("parse transcript_path present (dev-env#1002)", test_parse_transcript_path_present),
        ("parse transcript_path missing (dev-env#1002)", test_parse_transcript_path_missing),
        ("parse transcript_path empty/malformed (dev-env#1002)", test_parse_transcript_path_empty_and_malformed),
        ("consume present returns + deletes", test_consume_present_returns_and_deletes),
        ("consume is one-shot", test_consume_is_one_shot),
        ("consume absent -> None", test_consume_absent_returns_none),
        ("consume derives path from session_id (dev-env#980)", test_consume_derives_path_from_session_id),
        ("consume empty session_id -> None, disk untouched (dev-env#980)", test_consume_empty_session_id_no_override_returns_none_without_touching_disk),
        ("consume unsafe session_id -> None, disk untouched (dev-env#980)", test_consume_unsafe_session_id_no_override_returns_none_without_touching_disk),
        ("pending_task_count empty records (dev-env#1002)", test_pending_task_count_empty_records),
        ("pending_task_count create defaults pending (dev-env#1002)", test_pending_task_count_create_defaults_pending),
        ("pending_task_count update to in_progress counts (dev-env#1002)", test_pending_task_count_update_to_in_progress_counts),
        ("pending_task_count update to completed excludes (dev-env#1002)", test_pending_task_count_update_to_completed_excludes),
        ("pending_task_count update to deleted excludes (dev-env#1002)", test_pending_task_count_update_to_deleted_excludes),
        ("pending_task_count folds across sequence, not last-wins (dev-env#1002)", test_pending_task_count_folds_across_sequence_not_last_wins),
        ("pending_task_count unresolved create not counted (dev-env#1002)", test_pending_task_count_unresolved_create_not_counted),
        ("pending_task_count isSidechain create excluded (dev-env#1002)", test_pending_task_count_isSidechain_create_excluded),
        ("pending_task_count isSidechain update excluded (dev-env#1002)", test_pending_task_count_isSidechain_update_excluded),
        ("pending_task_count malformed records do not raise (dev-env#1002)", test_pending_task_count_malformed_records_do_not_raise),
        ("open_background_agent_count no Agent calls (dev-env#1002)", test_open_background_agent_count_no_agent_calls),
        ("open_background_agent_count resolved via user record (dev-env#1002)", test_open_background_agent_count_resolved_via_user_record),
        ("open_background_agent_count resolved via queue-operation record (dev-env#1002)", test_open_background_agent_count_resolved_via_queue_operation_record),
        ("open_background_agent_count resolved via attachment record (dev-env#1002)", test_open_background_agent_count_resolved_via_attachment_record),
        ("open_background_agent_count unresolved (dev-env#1002)", test_open_background_agent_count_unresolved),
        ("open_background_agent_count foreground excluded (dev-env#1002)", test_open_background_agent_count_foreground_call_excluded),
        ("open_background_agent_count omitted flag counts as backgrounded (dev-env#1002)", test_open_background_agent_count_omitted_flag_counts_as_backgrounded),
        ("open_background_agent_count omitted flag resolves normally (dev-env#1002)", test_open_background_agent_count_omitted_flag_resolves_normally),
        ("open_background_agent_count isSidechain call excluded (dev-env#1002)", test_open_background_agent_count_isSidechain_call_excluded),
        ("open_background_agent_count two calls one resolved (dev-env#1002)", test_open_background_agent_count_two_calls_one_resolved),
        ("format_in_flight_note both zero (dev-env#1002)", test_format_in_flight_note_both_zero),
        ("format_in_flight_note tasks only (dev-env#1002)", test_format_in_flight_note_tasks_only),
        ("format_in_flight_note agents only (dev-env#1002)", test_format_in_flight_note_agents_only),
        ("format_in_flight_note both (dev-env#1002)", test_format_in_flight_note_both),
        ("in_flight_work_note via explicit transcript_path (dev-env#1002)", test_in_flight_work_note_via_explicit_transcript_path),
        ("in_flight_work_note falls back to find_transcript (dev-env#1002)", test_in_flight_work_note_falls_back_to_find_transcript),
        ("in_flight_work_note non-file path falls back to find_transcript (dev-env#1002)", test_in_flight_work_note_nonfile_path_falls_back_to_find_transcript),
        ("in_flight_work_note neither resolves -> '' (dev-env#1002)", test_in_flight_work_note_neither_resolves_returns_empty),
        ("in_flight_work_note malformed transcript -> '' (dev-env#1002)", test_in_flight_work_note_malformed_transcript_returns_empty),
        ("in_flight_work_note invalid UTF-8 bytes -> '' (dev-env#1002)", test_in_flight_work_note_invalid_utf8_bytes_returns_empty),
        ("in_flight_work_note pre-filter skips full parse (dev-env#1002)", test_in_flight_work_note_prefilter_skips_full_parse_when_no_marker_present),
        ("e2e flag blocks on stderr + consumes", test_e2e_flag_blocks_on_stderr_and_consumes),
        ("e2e flag blocks with pending-tasks note (dev-env#1002)", test_e2e_flag_blocks_with_pending_tasks_note),
        ("e2e flag blocks with open-agent note (dev-env#1002)", test_e2e_flag_blocks_with_open_agent_note),
        ("e2e flag blocks with omitted-flag agent note (dev-env#1002)", test_e2e_flag_blocks_with_omitted_flag_agent_note),
        ("e2e flag blocks with resolved agent, no note (dev-env#1002)", test_e2e_flag_blocks_with_resolved_agent_no_note),
        ("e2e no flag allows", test_e2e_no_flag_allows),
        ("e2e cross-session sentinel not consumed (dev-env#980)", test_e2e_cross_session_sentinel_not_consumed),
        ("e2e no session_id never blocks (dev-env#980)", test_e2e_no_session_id_never_blocks),
        ("e2e stale advisory is a systemMessage", test_e2e_stale_advisory_is_systemmessage),
        ("e2e stop_hook_active allows + preserves flag", test_e2e_stop_hook_active_allows_and_preserves_flag),
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
