#!/usr/bin/env python3
"""Unit tests for _auto_resume.py, session-start-auto-resume.py and auto-resume-launcher.py.

dev-env#1182 / ADR-152. These cover the decisions that keep auto-resume from doing
harm: how a transcript tail is classified (a clean, waiting or user-stopped turn
must never read as interrupted), the opt-in rules (fail closed), the attempt cap,
the hook's settle re-check (a turn the user started meanwhile cancels the wake),
and the launcher's candidate filter (running sessions, interruptions after this app
start, and stale ones are never opened). The tail shapes are taken from real
transcripts cut off by a reboot on 2026-11-01.

Not covered: the PowerShell process query, ``os.startfile`` on the deep link and the
model wake itself. Those were verified live (see docs/TESTING.md item 108).

Usage:
    py -3 claude/scripts/tests/test_auto_resume.py

Exit 0 = all pass.
"""
import importlib
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _heartbeat_isolation import isolate_heartbeat  # noqa: E402

isolate_heartbeat()

import _auto_resume as ar  # noqa: E402

hook = importlib.import_module("session-start-auto-resume")
launcher = importlib.import_module("auto-resume-launcher")

TS = "2026-11-01T16:13:42.762Z"


def _asst(blocks, stop="tool_use", uuid="a1", **kw):
    return {"type": "assistant", "uuid": uuid, "timestamp": TS,
            "message": {"stop_reason": stop, "content": blocks}, **kw}


def _user(content, uuid="u1", **kw):
    return {"type": "user", "uuid": uuid, "timestamp": TS, "message": {"content": content}, **kw}


NOISE = [{"type": "api-request"}, {"type": "bridge-session"}, {"type": "custom-title"}]


def test_tool_use_without_result_is_interrupted() -> str:
    v = ar.classify([_asst([{"type": "tool_use", "name": "Bash"}])] + NOISE)
    assert v["state"] == "interrupted" and "Bash" in v["detail"], v
    return "a trailing Bash tool_use with no result is interrupted (dev-env-sync shape)"


def test_unanswered_tool_result_is_interrupted() -> str:
    v = ar.classify([_asst([{"type": "tool_use", "name": "Bash"}]),
                     _user([{"type": "tool_result", "content": "ok"}], uuid="u9")] + NOISE)
    assert v["state"] == "interrupted" and v["uuid"] == "u9", v
    return "a tool_result followed only by api-request is interrupted (hook-dispatcher shape)"


def test_end_turn_is_clean() -> str:
    v = ar.classify([_asst([{"type": "text", "text": "done"}], stop="end_turn")])
    assert v["state"] == "clean", v
    return "an end_turn reply is clean"


def test_stop_hook_summary_is_clean() -> str:
    v = ar.classify([_asst([{"type": "tool_use", "name": "Bash"}]),
                     {"type": "system", "subtype": "stop_hook_summary"}])
    assert v["state"] == "clean", v
    return "a stop_hook_summary after the last message means the turn finished"


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


def test_opt_in_rules() -> str:
    cfg = dict(ar.DEFAULTS, enabled=True, include=["pinned", r"cwd:C:\Git\dev-env*"],
               exclude=["session:local_x"])
    assert ar.opted_in(cfg, {"isStarred": True})
    assert ar.opted_in(cfg, {"cwd": r"c:\git\DEV-ENV\.claude\worktrees\w"})
    assert not ar.opted_in(cfg, {"cwd": r"C:\Git\other"})
    assert not ar.opted_in(cfg, {"isStarred": True, "sessionId": "local_x"})
    assert not ar.opted_in(cfg, {"isStarred": True, "isArchived": True})
    assert not ar.opted_in(dict(cfg, enabled=False), {"isStarred": True})
    return "pinned / cwd-glob (case-insensitive) include; exclude, archived and disabled win"


def test_missing_or_bad_config_is_disabled() -> str:
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "c.json"
        bad.write_text("{not json", encoding="utf-8")
        assert ar.load_config(Path(d) / "missing.json")["enabled"] is False
        assert ar.load_config(bad)["enabled"] is False
        bad.write_text(json.dumps({"enabled": True, "include": "all"}), encoding="utf-8")
        assert ar.load_config(bad)["enabled"] is False
    return "missing, unparseable or malformed config fails closed"


def test_attempt_cap() -> str:
    cfg = dict(ar.DEFAULTS, maxAttempts=2, maxAgeHours=1)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "attempts.json"
        now = 1_000_000.0
        assert ar.attempts_left("s", cfg, now=now, path=p)
        ar.record_attempt("s", cfg, now=now, path=p)
        ar.record_attempt("s", cfg, now=now + 1, path=p)
        assert not ar.attempts_left("s", cfg, now=now + 2, path=p)
        assert ar.attempts_left("s", cfg, now=now + 3700, path=p)
    return "two wakes exhaust the cap; attempts older than maxAgeHours stop counting"


def _hook_fixture(d: Path, entries):
    t = d / "sess.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    ar.ATTEMPTS_PATH = d / "attempts.json"
    ar.meta_for = lambda sid, sessions=None: {"cliSessionId": sid, "sessionId": "local_t"}
    ar.boot_time = lambda: None
    return {"session_id": "sess", "transcript_path": str(t), "source": "resume", "cwd": str(d)}


CFG_ALL = dict(ar.DEFAULTS, enabled=True, include=["all"], settleSeconds=0)


def test_hook_wakes_interrupted_session() -> str:
    with tempfile.TemporaryDirectory() as d:
        payload = _hook_fixture(Path(d), [_asst([{"type": "tool_use", "name": "Bash"}])])
        now = lambda: ar.parse_ts(TS) + 60  # noqa: E731
        decision, info = hook.decide(payload, CFG_ALL, sleep=lambda s: None, now=now)
    assert decision == "wake" and "[auto-resume]" in info["message"], (decision, info)
    return "an opted-in, recent, interrupted resume decides wake with an [auto-resume] message"


def test_hook_cancels_when_a_turn_started_during_settle() -> str:
    with tempfile.TemporaryDirectory() as d:
        payload = _hook_fixture(Path(d), [_asst([{"type": "tool_use", "name": "Bash"}])])

        def user_types(_s):
            with open(payload["transcript_path"], "a", encoding="utf-8") as f:
                f.write(json.dumps(_user("hello", uuid="u-new")) + "\n")

        now = lambda: ar.parse_ts(TS) + 60  # noqa: E731
        decision, _ = hook.decide(payload, CFG_ALL, sleep=user_types, now=now)
    assert decision == "activity_during_settle", decision
    return "a message that arrives during the settle wait cancels the wake (no double send)"


def test_hook_skips_startup_old_and_not_opted_in() -> str:
    with tempfile.TemporaryDirectory() as d:
        payload = _hook_fixture(Path(d), [_asst([{"type": "tool_use", "name": "Bash"}])])
        nosleep = lambda s: None  # noqa: E731
        recent = lambda: ar.parse_ts(TS) + 60  # noqa: E731
        late = lambda: ar.parse_ts(TS) + 13 * 3600  # noqa: E731
        a = hook.decide(dict(payload, source="startup"), CFG_ALL, nosleep, recent)[0]
        b = hook.decide(payload, CFG_ALL, nosleep, late)[0]
        c = hook.decide(payload, dict(CFG_ALL, include=["pinned"]), nosleep, recent)[0]
        e = hook.decide(payload, dict(CFG_ALL, enabled=False), nosleep, recent)[0]
    assert (a, b, c, e) == ("not_a_resume", "too_old", "not_opted_in", "disabled"), (a, b, c, e)
    return "startup source, an interruption past maxAgeHours, no matching rule and disabled all skip"


def test_launcher_process_parsing() -> str:
    procs = [
        {"path": r"C:\Program Files\WindowsApps\Claude_1\app\Claude.exe", "cmd": '"Claude.exe"',
         "created": "2026-11-01T16:18:35.0000000Z"},
        {"path": r"C:\Program Files\WindowsApps\Claude_1\app\Claude.exe",
         "cmd": "Claude.exe --type=gpu-process", "created": "2026-11-01T16:18:36.0000000Z"},
        {"path": r"C:\Users\b\AppData\Local\Claude-Data\c\claude-code\2.1\x\claude.exe",
         "cmd": "claude.exe --resume=4973F611-F060-47AC-8CEC-AED3911B1DF1 --model=x",
         "created": "2026-11-01T16:43:08.0000000Z"},
    ]
    assert launcher.app_start(procs) == ar.parse_ts("2026-11-01T16:18:35Z")
    assert launcher.running_cli_ids(procs) == {"4973f611-f060-47ac-8cec-aed3911b1df1"}
    assert launcher.app_start(procs[2:]) is None
    return "the app start comes from the main Claude.exe; CLI --resume ids are collected lowercased"


def test_launcher_candidates_filter() -> str:
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "projects" / "p"
        proj.mkdir(parents=True)
        ar.ATTEMPTS_PATH = Path(d) / "attempts.json"

        def tx(cli, entries):
            (proj / f"{cli}.jsonl").write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        cut = [_asst([{"type": "tool_use", "name": "Bash"}])]
        ids = {n: f"{n}0000000-0000-0000-0000-000000000000"[:36] for n in "abcdef"}
        tx(ids["a"], cut)                                                   # opens
        tx(ids["b"], cut)                                                   # already running
        tx(ids["c"], [_asst([{"type": "text", "text": "x"}], stop="end_turn")])  # clean
        tx(ids["d"], cut)                                                   # not opted in
        tx(ids["e"], [dict(cut[0], timestamp="2026-11-01T17:00:00Z")])      # after app start
        sessions = [{"sessionId": f"local_{n}", "cliSessionId": ids[n], "isStarred": n != "d"}
                    for n in "abcde"]
        sessions.append({"sessionId": "cse_f", "cliSessionId": ids["f"], "isStarred": True})
        cfg = dict(ar.DEFAULTS, enabled=True, include=["pinned"])
        started = ar.parse_ts("2026-11-01T16:18:35Z")
        got = launcher.candidates(cfg, sessions, started, {ids["b"]}, started + 120,
                                  projects=Path(d) / "projects")
    assert [c["sessionId"] for c in got] == ["local_a"], got
    return ("only an opted-in local session interrupted before this app start, with no running "
            "CLI, is opened")


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
