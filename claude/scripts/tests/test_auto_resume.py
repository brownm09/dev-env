#!/usr/bin/env python3
"""Unit tests for _auto_resume.py, session-start-auto-resume.py and auto-resume-launcher.py.

dev-env#1182 / ADR-152. These cover the decisions that keep auto-resume from doing
harm:
- How a transcript tail is classified. A clean, waiting, user-stopped or /compact-ed
  turn must never read as interrupted.
- The opt-in rules, which fail closed: no app metadata means not opted in, and a bad
  config is disabled.
- The per-session attempt cap.
- The hook's guards against a double send: the interrupted message must predate the
  hook, and any turn activity during the settle wait cancels.
- The launcher's process parsing, candidate filter and retry plan.

The tail shapes are taken from real transcripts: two cut off by a reboot on
2026-11-01, and one that was only compacted.

Not covered here: the PowerShell process query, ``os.startfile`` on the deep link and
the model wake itself. Those are verified live (docs/TESTING.md item 108).

Usage:
    py -3 claude/scripts/tests/test_auto_resume.py

Exit 0 = all pass.
"""
import importlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _heartbeat_isolation import isolate_heartbeat  # noqa: E402

isolate_heartbeat()

import _auto_resume as ar  # noqa: E402

hook = importlib.import_module("session-start-auto-resume")
launcher = importlib.import_module("auto-resume-launcher")

TS = "2026-11-01T16:13:42.762Z"
T0 = ar.parse_ts(TS)
META = {"sessionId": "local_t", "cliSessionId": "sess", "isArchived": False, "isStarred": False}


def _asst(blocks, stop="tool_use", uuid="a1", **kw):
    return {"type": "assistant", "uuid": uuid, "timestamp": TS,
            "message": {"stop_reason": stop, "content": blocks}, **kw}


def _user(content, uuid="u1", **kw):
    return {"type": "user", "uuid": uuid, "timestamp": TS, "message": {"content": content}, **kw}


NOISE = [{"type": "api-request"}, {"type": "bridge-session"}, {"type": "custom-title"}]
CUT = [_asst([{"type": "tool_use", "name": "Bash"}])]


def test_tool_use_without_result_is_interrupted() -> str:
    v = ar.classify(CUT + NOISE)
    assert v["state"] == "interrupted" and "Bash" in v["detail"], v
    return "a trailing Bash tool_use with no result is interrupted (dev-env-sync shape)"


def test_unanswered_tool_result_is_interrupted() -> str:
    v = ar.classify(CUT + [_user([{"type": "tool_result", "content": "ok"}], uuid="u9")] + NOISE)
    assert v["state"] == "interrupted" and v["uuid"] == "u9", v
    return "a tool_result followed only by api-request is interrupted (hook-dispatcher shape)"


def test_finished_turns_are_clean() -> str:
    a = ar.classify([_asst([{"type": "text", "text": "done"}], stop="end_turn")])
    b = ar.classify(CUT + [{"type": "system", "subtype": "stop_hook_summary"}])
    assert a["state"] == b["state"] == "clean", (a, b)
    return "an end_turn reply, or any message followed by stop_hook_summary, is clean"


def test_compact_and_local_commands_are_not_interrupted() -> str:
    done = _asst([{"type": "text", "text": "PR opened"}], stop="end_turn")
    compact = [done, {"type": "system", "subtype": "compact_boundary"},
               _user("summary of the conversation", isCompactSummary=True),
               _user("<command-name>/compact</command-name>"),
               _user("<local-command-stdout>Compacted </local-command-stdout>")]
    model = [done, _user("<command-name>/model</command-name>"),
             _user([{"type": "text", "text": "<local-command-stdout>Set model</local-command-stdout>"}])]
    a, b = ar.classify(compact), ar.classify(model)
    assert a["state"] == b["state"] == "clean", (a, b)
    return "a /compact tail (compact_boundary, summary, command entries) and a /model tail are clean"


def test_waiting_tools_are_not_interrupted() -> str:
    for name in ("AskUserQuestion", "ExitPlanMode"):
        v = ar.classify([_asst([{"type": "tool_use", "name": name}])])
        assert v["state"] == "waiting_on_user", (name, v)
    return "a turn paused on AskUserQuestion / ExitPlanMode is waiting_on_user"


def test_user_stop_is_not_interrupted() -> str:
    v = ar.classify([_user([{"type": "text", "text": "[Request interrupted by user]"}])])
    w = ar.classify([_user([{"type": "tool_result",
                             "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}])])
    assert v["state"] == w["state"] == "user_stopped", (v, w)
    return "a stop the user pressed is user_stopped, as text or inside a tool_result"


def test_sidechain_meta_and_api_error_handling() -> str:
    v = ar.classify([_asst([{"type": "text", "text": "x"}], stop="end_turn"),
                     _user("caveat", isMeta=True),
                     _asst([{"type": "tool_use", "name": "Read"}], isSidechain=True)])
    e = ar.classify([_asst([{"type": "text", "text": "overloaded"}], stop="stop_sequence",
                           isApiErrorMessage=True)])
    assert v["state"] == "clean" and e["state"] == "api_error", (v, e)
    return "sidechain and isMeta entries are ignored; an API-error reply is api_error, not resumed"


def test_empty_and_unanswered_prompt() -> str:
    assert ar.classify(NOISE)["state"] == "empty"
    assert ar.classify([_user("please do X")])["state"] == "interrupted"
    return "no messages is empty; a prompt with no reply is interrupted"


def test_read_tail_grows_past_a_huge_last_line() -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        big = _user([{"type": "tool_result", "content": "x" * (600 * 1024)}], uuid="big")
        p.write_text(json.dumps(CUT[0]) + "\n" + json.dumps(big) + "\n", encoding="utf-8")
        v = ar.classify(ar.read_tail(p))
    assert v["state"] == "interrupted" and v["uuid"] == "big", v
    return "a last line larger than the first 512 KB window is still read and classified"


def test_opt_in_rules() -> str:
    cfg = dict(ar.DEFAULTS, enabled=True, include=["pinned", r"cwd:C:\Git\dev-env*"],
               exclude=["session:local_x"])
    base = {"sessionId": "local_a", "isArchived": False}
    assert ar.opted_in(cfg, {**base, "isStarred": True})
    assert ar.opted_in(cfg, {**base, "cwd": r"c:\git\DEV-ENV\.claude\worktrees\w"})
    assert not ar.opted_in(cfg, {**base, "cwd": r"C:\Git\other"})
    assert not ar.opted_in(cfg, {**base, "isStarred": True, "sessionId": "local_x"})
    assert not ar.opted_in(cfg, {**base, "isStarred": True, "isArchived": True})
    assert not ar.opted_in(dict(cfg, enabled=False), {**base, "isStarred": True})
    return "pinned / cwd-glob (case-insensitive) include; exclude, archived and disabled win"


def test_no_app_metadata_is_never_opted_in() -> str:
    cfg = dict(ar.DEFAULTS, enabled=True, include=["all"])
    assert not ar.opted_in(cfg, {})
    assert not ar.opted_in(cfg, {"cliSessionId": "sess"})
    assert not ar.opted_in(cfg, {"sessionId": "cse_remote", "isArchived": False})
    assert not ar.opted_in(cfg, {"sessionId": "local_a"})  # archived state unknown
    return "include=all still skips a session with no desktop metadata or unknown archived state"


def test_bad_config_is_disabled_with_a_reason() -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.json"
        assert ar.load_config(Path(d) / "missing.json") == ar.DEFAULTS
        cases = ["{not json", json.dumps({"enabled": True, "include": "all"}),
                 json.dumps({"enabled": True, "include": ["pinned,cwd:C:\\x"]}),
                 json.dumps({"enabled": True, "include": ["all"], "maxAgeHours": "12h"}),
                 json.dumps({"enabled": True, "include": ["all"], "settleSeconds": 600})]
        for raw in cases:
            p.write_text(raw, encoding="utf-8")
            cfg = ar.load_config(p)
            assert cfg["enabled"] is False and cfg.get("invalid"), (raw, cfg)
        p.write_text(json.dumps({"enabled": True, "include": ["all"]}), encoding="utf-8")
        assert ar.load_config(p)["enabled"] is True
    return ("unparseable, non-list, unknown-rule ('pinned,cwd:...'), non-numeric and over-limit "
            "configs are disabled with a reason; a valid one is enabled")


def test_attempt_cap_is_per_session_and_app_instance() -> str:
    cfg = dict(ar.DEFAULTS, maxAttempts=2, maxAgeHours=12)
    with tempfile.TemporaryDirectory() as d:
        root, now = Path(d), 1_000_000.0
        assert ar.attempts_left("s", cfg, since=now - 10, now=now, root=root)
        ar.record_attempt("s", cfg, now=now, root=root)
        ar.record_attempt("s", cfg, now=now + 1, root=root)
        assert not ar.attempts_left("s", cfg, since=now - 10, now=now + 2, root=root)
        assert ar.attempts_left("other", cfg, since=now - 10, now=now + 2, root=root)
        # A new app instance (an app restart or a reboot) starts a fresh count.
        assert ar.attempts_left("s", cfg, since=now + 600, now=now + 700, root=root)
        assert ar.attempts_left("s", cfg, since=0, now=now + 13 * 3600, root=root)
        assert sorted(f.name for f in root.iterdir()) == ["s.json"]
    return ("two wakes exhaust one session's cap for one app instance; a new instance or "
            "maxAgeHours resets it; one file per session")


def _bg_launch(tool_id="toolu_bg1", name="Bash", **inp):
    return _asst([{"type": "tool_use", "id": tool_id, "name": name,
                   "input": {"run_in_background": True, "description": "full tests", **inp}}])


def _notice(tool_id="toolu_bg1", status="completed", summary="done"):
    return _user(f"<task-notification>\n<tool-use-id>{tool_id}</tool-use-id>\n<status>{status}"
                 f"</status>\n<summary>{summary}</summary>\n</task-notification>",
                 uuid=f"n-{tool_id}", origin={"kind": "task-notification"})


STOP = "Background shell command didn't finish before the previous session ended"
END = [_asst([{"type": "text", "text": "Tests are running in the background."}], stop="end_turn",
             uuid="end"), {"type": "system", "subtype": "stop_hook_summary"}]


def test_waiting_on_background() -> str:
    launch = [_bg_launch(), _user([{"type": "tool_result", "content": "running in background"}])]
    a = ar.classify(launch + END)
    b = ar.classify(launch + END + [_notice(), _asst([{"type": "text", "text": "ok"}], stop="end_turn")])
    monitor = [_bg_launch("toolu_m", name="Monitor", timeout_ms=1_800_000)] + END
    c = ar.classify(monitor, restarted_at=T0 + 600)        # restart inside the 30 min timeout
    c_late = ar.classify(monitor, restarted_at=T0 + 3600)  # it may simply have timed out
    c_none = ar.classify(monitor)
    assert c_late["state"] == c_none["state"] == "clean", (c_late, c_none)
    d = ar.classify([_bg_launch("toolu_a", name="Agent")] + END)
    # A stop notice the session already dealt with (a later prompt) resolves the dead task.
    handled = ar.classify(launch + END + [_notice(status="stopped", summary=STOP),
                                          _user("rerun it", uuid="u3"),
                                          _asst([{"type": "text", "text": "rerun"}], stop="end_turn")])
    assert handled["state"] == "clean", handled
    e = ar.classify([_asst([{"type": "tool_use", "id": "toolu_f", "name": "Bash",
                             "input": {"command": "ls"}}])] + END)
    # A notification that arrives mid-turn is a queued_command attachment, not a user entry.
    queued = {"type": "attachment", "attachment": {"type": "queued_command", "commandMode":
              "task-notification", "prompt":
              "<task-notification>\n<tool-use-id>toolu_bg1</tool-use-id>\n<status>completed</status>"}}
    f = ar.classify(launch + [queued] + END)
    assert f["state"] == "clean", f
    assert a["state"] == "waiting_on_background" and "full tests" in a["detail"], a
    assert b["state"] == "clean", b
    assert c["state"] == d["state"] == "waiting_on_background", (c, d)
    assert e["state"] == "clean", e
    return ("a finished turn whose background Bash / Agent never reported back, or whose Monitor "
            "was still inside its timeout at the restart, is waiting_on_background; a completion "
            "(user entry or mid-turn queued attachment), a stop notice already dealt with, a "
            "Monitor past its timeout, or a foreground call is clean")


def test_stop_notice_turn_is_set_aside() -> str:
    reply = [_asst([{"type": "text", "text": "No response requested."}], stop="end_turn", uuid="r"),
             {"type": "system", "subtype": "stop_hook_summary"}]
    stop = _notice(status="stopped", summary=STOP)
    cut = ar.classify(CUT + [{"type": "queue-operation"}, stop] + reply)
    bg = ar.classify([_bg_launch()] + END + [stop] + reply)
    typed = ar.classify(CUT + [stop] + reply + [_user("hi", uuid="u2"),
                                                _asst([{"type": "text", "text": "hello"}], stop="end_turn")])
    woken = ar.classify(CUT + [stop] + reply + [_notice("hook", summary="Stop hook feedback"),
                                                _asst([{"type": "text", "text": "done"}], stop="end_turn")])
    acted = ar.classify(CUT + [stop, _asst([{"type": "tool_use", "name": "Bash"}], uuid="x")])
    assert cut["state"] == "interrupted" and cut["stopNotice"] and cut["uuid"] == "a1", cut
    assert bg["state"] == "waiting_on_background" and bg["stopNotice"], bg
    assert typed["state"] == "clean" and woken["state"] == "clean", (typed, woken)
    assert acted["state"] == "interrupted" and not acted["stopNotice"] and acted["uuid"] == "x", acted
    return ("a CLI stop notice and its 'No response requested' turn are set aside, revealing the "
            "interruption; a prompt, a wake or a tool call after it means the session moved on")


CFG_ALL = dict(ar.DEFAULTS, enabled=True, include=["all"], settleSeconds=0)


def _decide(d: Path, entries, cfg=CFG_ALL, sleep=lambda s: None, at=T0 + 60, meta=META, **payload):
    t = d / "sess.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    p = {"session_id": "sess", "transcript_path": str(t), "source": "resume", **payload}
    with mock.patch.object(ar, "ATTEMPTS_DIR", d / "attempts"), \
            mock.patch.object(ar, "meta_for", lambda sid: dict(meta)), \
            mock.patch.object(ar, "boot_time", lambda: T0 - 3600):
        return hook.decide(p, cfg, sleep=lambda s: sleep(t), now=lambda: at,
                           instance_start=lambda: T0 - 7200)


def test_synthetic_placeholder_after_restart() -> str:
    # The real 17:09 shape (dev-env-sync): a dangling tool_result, the CLI's synthetic
    # "No response requested." placeholder, then its stop notice for a dead background task.
    synthetic = {"type": "assistant", "uuid": "syn", "timestamp": TS,
                 "message": {"model": "<synthetic>", "stop_reason": "stop_sequence",
                             "content": [{"type": "text", "text": "No response requested."}]}}
    tail = CUT + [_user([{"type": "tool_result", "content": "ok"}], uuid="u9"),
                  {"type": "api-request"}, {"type": "mode"},
                  {"type": "queue-operation", "operation": "enqueue"},
                  {"type": "queue-operation", "operation": "dequeue"}, synthetic,
                  _notice(status="stopped", summary=STOP)]
    v = ar.classify(tail)
    assert v["state"] == "interrupted" and v["uuid"] == "u9" and v["stopNotice"], v
    assert not ar.turn_activity([synthetic, {"type": "file-history-delta"}])
    return ("the CLI's synthetic placeholder reply and stop notice don't hide a dangling "
            "tool_result, and the placeholder alone doesn't count as a turn starting")


def test_hook_wakes_interrupted_session() -> str:
    with tempfile.TemporaryDirectory() as d:
        decision, info = _decide(Path(d), CUT)
    assert decision == "wake" and "[auto-resume]" in info["message"], (decision, info)
    return "an opted-in, recent, interrupted resume decides wake with an [auto-resume] message"


def test_hook_ignores_a_message_newer_than_itself() -> str:
    with tempfile.TemporaryDirectory() as d:
        decision, _ = _decide(Path(d), [_user("new prompt")], at=T0 + 1)
    assert decision == "not_interrupted", decision
    return "a prompt written after the hook started (the one that launched this CLI) is not interrupted"


def _append(entry):
    def write(t):
        with open(t, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return write


def test_hook_cancels_on_turn_activity_during_settle() -> str:
    for entry in (_user("hello", uuid="u-new"), {"type": "api-request"},
                  {"type": "attachment", "uuid": "x"}):
        with tempfile.TemporaryDirectory() as d:
            decision, _ = _decide(Path(d), CUT, sleep=_append(entry))
        assert decision == "activity_during_settle", (entry, decision)
    with tempfile.TemporaryDirectory() as d:
        decision, _ = _decide(Path(d), CUT, sleep=_append({"type": "custom-title"}))
    assert decision == "wake", decision
    return ("a new message, api-request or attachment during the settle cancels the wake; an app "
            "bookkeeping line (custom-title) does not")


def _append_all(entries):
    def write(t):
        with open(t, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
    return write


def test_hook_wakes_through_the_clis_stop_notice_turn() -> str:
    stop = _notice(status="stopped", summary=STOP)
    turn = [{"type": "queue-operation"}, stop, {"type": "api-request"},
            _asst([{"type": "text", "text": "No response requested."}], stop="end_turn", uuid="r"),
            {"type": "system", "subtype": "stop_hook_summary"}]
    with tempfile.TemporaryDirectory() as d:
        woke = _decide(Path(d), CUT, sleep=_append_all(turn))
    with tempfile.TemporaryDirectory() as d:
        bg = _decide(Path(d), [_bg_launch()] + END, sleep=_append_all(turn))
    with tempfile.TemporaryDirectory() as d:
        typed = _decide(Path(d), CUT, sleep=_append_all(turn + [_user("hi", uuid="u2")]))
    assert woke[0] == "wake" and bg[0] == "wake" and "rerun" in bg[1]["message"], (woke, bg)
    assert typed[0] == "activity_during_settle", typed
    return ("the CLI's stop-notice turn during the settle doesn't cancel the wake (and a lost "
            "background task gets a 'rerun it' message); a user prompt during it still does")


def test_hook_attempts_reset_with_the_app_instance() -> str:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cfg = dict(CFG_ALL, maxAttempts=1)
        ar.record_attempt("sess", cfg, now=T0 - 9000, root=d / "attempts")  # before T0 - 7200
        fresh = _decide(d, CUT, cfg=cfg)[0]
        ar.record_attempt("sess", cfg, now=T0 + 30, root=d / "attempts")    # this instance
        spent = _decide(d, CUT, cfg=cfg)[0]
    assert (fresh, spent) == ("wake", "attempts_exhausted"), (fresh, spent)
    return "a wake from an earlier app instance doesn't count; one in this instance does"


def test_hook_skips() -> str:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        got = (
            _decide(d, CUT, source="startup")[0],
            _decide(d, CUT, at=T0 + 13 * 3600)[0],
            _decide(d, CUT, cfg=dict(CFG_ALL, include=["pinned"]))[0],
            _decide(d, CUT, meta={})[0],
            _decide(d, CUT, cfg=dict(CFG_ALL, enabled=False))[0],
            _decide(d, CUT, cfg=dict(CFG_ALL, enabled=False, invalid="bad"))[0],
        )
    want = ("not_a_resume", "too_old", "not_opted_in", "not_opted_in", "disabled", "config_invalid")
    assert got == want, got
    return ("startup source, past maxAgeHours, no matching rule, no desktop metadata, disabled "
            "and invalid config all skip")


def test_only_cli_written_notices_are_stop_notices() -> str:
    phrase = f"<task-notification><status>stopped</status><summary>{STOP}</summary>"
    quoted = _user([{"type": "tool_result", "content": phrase}], uuid="q")  # tool output quoting it
    done = _notice(status="completed", summary="Agent finished; it mentioned: " + STOP)
    grouped = _notice(status="stopped", summary="2 background agents " + STOP[len("Background shell command "):])
    assert not ar.is_stop_notice(quoted) and not ar.is_stop_notice(done), (quoted, done)
    assert ar.is_stop_notice(grouped)
    v = ar.classify(CUT + [quoted, _asst([{"type": "text", "text": "done"}], stop="end_turn")])
    assert v["state"] == "clean" and not v["stopNotice"], v
    return ("a tool result or completed notice that quotes the stop sentence is not a stop notice "
            "(a finished turn stays clean); a grouped 'N background agents' notice is one")


def test_completion_matched_by_task_id() -> str:
    agent = [_bg_launch("toolu_ag", name="Agent"),
             _user([{"type": "tool_result", "tool_use_id": "toolu_ag",
                     "content": [{"type": "text", "text": "Async agent launched.\nagentId: a52e6362f (x)"}]}])]
    by_task = _user("<task-notification>\n<task-id>a52e6362f</task-id>\n<status>completed</status>",
                    uuid="n", origin={"kind": "task-notification"})
    grouped = _notice(status="stopped", summary="2 background agents " + STOP[len("Background shell command "):])
    grouped["message"]["content"] = grouped["message"]["content"].replace(
        "<tool-use-id>toolu_bg1</tool-use-id>", "<task-id>a52e6362f</task-id>")
    moved = [_user("carry on", uuid="u5"), _asst([{"type": "text", "text": "ok"}], stop="end_turn")]
    a = ar.classify(agent + END + [by_task] + moved)
    b = ar.classify(agent + END + [grouped] + moved)
    c = ar.classify(agent + END)
    enqueued = {"type": "queue-operation", "operation": "enqueue",
                "content": "<task-notification>\n<task-id>a52e6362f</task-id>\n<status>completed</status>"}
    q = ar.classify(agent + [enqueued] + END)  # finished, notification not yet delivered
    typed = {"type": "queue-operation", "operation": "enqueue", "content": "please also check X"}
    assert a["state"] == b["state"] == q["state"] == "clean", (a, b, q)
    assert c["state"] == "waiting_on_background", c
    assert ar.is_human_activity(typed) and not ar.is_human_activity(enqueued)
    return ("an agent completion or a grouped stop notice that names only the task ID (from the "
            "launch's 'agentId:' result) marks the task done")


def test_monitor_default_and_ceiling() -> str:
    t = T0
    no_timeout = [_bg_launch("toolu_m", name="Monitor")] + END
    huge = [_bg_launch("toolu_m", name="Monitor", timeout_ms=10 * 3600 * 1000)] + END
    assert ar.classify(no_timeout, restarted_at=t + 200)["state"] == "waiting_on_background"
    assert ar.classify(no_timeout, restarted_at=t + 400)["state"] == "clean"  # 300 s default
    assert ar.classify(huge, restarted_at=t + 2000)["state"] == "clean"       # capped at 30 min
    return "a Monitor's deadline uses the 300 s default and the 30 min ceiling"


def test_stop_notice_trim_edges() -> str:
    stop = _notice(status="stopped", summary=STOP)
    reply = [_asst([{"type": "text", "text": "No response requested."}], stop="end_turn", uuid="r"),
             {"type": "system", "subtype": "stop_hook_summary"}]
    two_turns = ar.classify(CUT + [stop] + reply + [dict(stop, uuid="s2")] + reply)
    meta = ar.classify(CUT + [stop, _user("<system-reminder>caveat</system-reminder>", isMeta=True)] + reply)
    typed = ar.classify(CUT + [stop, {"type": "attachment", "attachment": {
        "type": "queued_command", "commandMode": "prompt", "prompt": "please continue"}}] + reply)
    assert two_turns["state"] == meta["state"] == "interrupted", (two_turns, meta)
    assert two_turns["stopNotice"] and meta["stopNotice"]
    assert not typed["stopNotice"], typed
    return ("several notice-only turns and an isMeta line inside one are still set aside; a prompt "
            "typed mid-turn (queued_command) is not")


def test_consecutive_wakes_guard_crosses_instances() -> str:
    wake = _user("<task-notification>\n<summary>Stop hook feedback</summary>\n</task-notification>\n"
                 "[auto-resume] Your previous turn was cut off", uuid="w",
                 origin={"kind": "task-notification"})
    loop = [_user("do the thing", uuid="h")] + [wake, *CUT] * 3
    assert ar.consecutive_wakes(loop) == 3
    assert ar.consecutive_wakes(loop + [_user("try again", uuid="h2")] + CUT) == 0
    with tempfile.TemporaryDirectory() as d:
        decision = _decide(Path(d), loop)[0]
    assert decision == "attempts_exhausted", decision
    return ("three wakes with no human prompt in between stop further wakes, whatever the app "
            "instance; a human prompt resets the count")


def test_settle_with_stop_notice_turn() -> str:
    stop = _notice(status="stopped", summary=STOP)
    running = [stop, {"type": "api-request"}]
    finished = running + [_asst([{"type": "text", "text": "No response requested."}], stop="end_turn",
                                uuid="r"), {"type": "system", "subtype": "stop_hook_summary"}]
    with tempfile.TemporaryDirectory() as d:
        still = _decide(Path(d), CUT, sleep=_append_all(finished))
    # The CLI's notice turn still running across both waits: give up, don't wake mid-turn.
    calls = []

    def once(t):
        if not calls:
            _append_all(running)(t)
        calls.append(1)
    with tempfile.TemporaryDirectory() as d:
        busy = _decide(Path(d), CUT, sleep=once)[0]
    with tempfile.TemporaryDirectory() as d:
        acted = _decide(Path(d), CUT, sleep=_append_all(
            [stop, _asst([{"type": "tool_use", "name": "Bash", "id": "x"}], uuid="x")]))[0]
    with tempfile.TemporaryDirectory() as d:
        typed = _decide(Path(d), CUT, sleep=_append_all([stop, {"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt", "prompt": "hi"}}]))[0]
    assert still[0] == "wake", still
    assert (busy, acted, typed) == ("cli_turn_running", "activity_during_settle",
                                    "activity_during_settle"), (busy, acted, typed)
    return ("a finished CLI notice turn still wakes; one still running gets one more wait, then no "
            "wake; a tool call or a prompt typed during it cancels")


def test_read_since_partial_lines() -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        first = json.dumps(CUT[0]) + "\n"
        half = json.dumps(_user("typed", uuid="u7"))
        p.write_bytes((first + half[:10]).encode())
        offset = ar.file_size(p)                          # taken mid-line
        new, partial = ar.read_since(p, offset)
        assert partial and new == [], (new, partial)
        with open(p, "ab") as f:
            f.write((half[10:] + "\n").encode())
        new, partial = ar.read_since(p, offset)
    assert not partial and [e.get("uuid") for e in new] == ["u7"], (new, partial)
    return ("a line half-written at the offset is read whole once finished; a line still being "
            "written reports partial (treated as activity)")


def test_launcher_process_parsing() -> str:
    procs = [
        {"path": r"C:\Program Files\WindowsApps\Claude_4.4_x64__pz\app\Claude.exe", "cmd": '"Claude.exe"',
         "created": "2026-11-01T16:18:35.0000000Z"},
        {"path": r"C:\Program Files\WindowsApps\Claude_4.4_x64__pz\app\Claude.exe",
         "cmd": "Claude.exe --type=gpu-process", "created": "2026-11-01T16:18:36.0000000Z"},
        {"path": r"C:\Users\b\AppData\Local\Claude-Data\c\claude-code\2.1\x\claude.exe",
         "cmd": "claude.exe --resume=4973F611-F060-47AC-8CEC-AED3911B1DF1 --model=x",
         "created": "2026-11-01T16:43:08.0000000Z"},
        {"path": r"C:\Users\b\.local\bin\claude.exe",
         "cmd": "claude --resume 11111111-2222-3333-4444-555555555555",
         "created": "2026-11-01T10:00:00.0000000Z"},
    ]
    assert ar.app_start(procs) == ar.parse_ts("2026-11-01T16:18:35Z")
    assert ar.running_cli_ids(procs) == {"4973f611-f060-47ac-8cec-aed3911b1df1",
                                               "11111111-2222-3333-4444-555555555555"}
    assert ar.app_start(procs[2:]) is None
    return ("the app start comes only from WindowsApps\\Claude_*\\app\\Claude.exe; any claude.exe "
            "--resume counts as running, including a native CLI install")


def test_launcher_candidates_filter() -> str:
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "projects" / "p"
        proj.mkdir(parents=True)

        def tx(cli, entries):
            (proj / f"{cli}.jsonl").write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        ids = {n: f"{n}0000000-0000-0000-0000-000000000000"[:36] for n in "abcdef"}
        tx(ids["a"], CUT)                                                     # opens
        tx(ids["b"], CUT)                                                     # already running
        tx(ids["c"], [_asst([{"type": "text", "text": "x"}], stop="end_turn")])  # clean
        tx(ids["d"], CUT)                                                     # not opted in
        tx(ids["e"], [dict(CUT[0], timestamp="2026-11-01T17:00:00Z")])       # after app start
        tx(ids["f"], CUT)                                                     # not a local session
        sessions = [{"sessionId": f"local_{n}", "cliSessionId": ids[n], "isStarred": n != "d",
                     "isArchived": False} for n in "abcde"]
        sessions.append({"sessionId": "cse_f", "cliSessionId": ids["f"], "isStarred": True,
                         "isArchived": False})
        cfg = dict(ar.DEFAULTS, enabled=True, include=["pinned"])
        started = ar.parse_ts("2026-11-01T16:18:35Z")
        with mock.patch.object(ar, "ATTEMPTS_DIR", Path(d) / "attempts"):
            got = launcher.candidates(cfg, sessions, started, {ids["b"]}, started + 120,
                                      projects=Path(d) / "projects")
    assert [c["sessionId"] for c in got] == ["local_a"], got
    return ("only an opted-in local session interrupted before this app start, with no running "
            "CLI, is opened")


def test_launcher_retry_plan() -> str:
    found = [{"sessionId": "local_a"}, {"sessionId": "local_b"}]
    todo, done = launcher.plan_opens(found, {"opens": {"local_a": launcher.MAX_OPENS}})
    assert [c["sessionId"] for c in todo] == ["local_b"] and not done, todo
    todo, done = launcher.plan_opens(found, {"opens": {"local_a": 3, "local_b": 3}})
    assert todo == [] and done
    assert launcher.plan_opens([], {}) == ([], True)
    return "a session is re-opened on later runs until MAX_OPENS; the instance is done when none remain"


def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
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
