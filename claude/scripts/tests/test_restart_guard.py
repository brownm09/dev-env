#!/usr/bin/env python3
"""Tests for restart-guard.py, _busy_sessions.py and register-restart-guard.ps1 (dev-env#1398, ADR-165).

Required when changing `restart-guard.py`, `_busy_sessions.py` or `register-restart-guard.ps1`.
Run: py -3 claude/scripts/tests/test_restart_guard.py

Everything runs on fixtures: temp session files with a fake process-creation lookup, temp
transcripts built the way test_auto_resume.py builds them, a dict-backed stand-in for the
active-hours registry values, and a temp backup anchor. Nothing reads this machine's sessions,
writes the registry or creates the guard's window.

Scope gap: whether Windows lists the off-screen window on its "apps are preventing restart" screen,
and what Windows Update does when active hours move, can only be seen on a real machine. See the ADR's
verification steps.
"""
import datetime as dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import _busy_sessions as bs  # noqa: E402

_spec = importlib.util.spec_from_file_location("restart_guard", SCRIPTS / "restart-guard.py")
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

TS = "2026-11-22T16:13:42.762Z"


def _asst(blocks, stop="tool_use", uuid="a1"):
    return {"type": "assistant", "uuid": uuid, "timestamp": TS, "message": {"stop_reason": stop, "content": blocks}}


def _user(content, uuid="u1"):
    return {"type": "user", "uuid": uuid, "timestamp": TS, "message": {"content": content}}


END = [_asst([{"type": "text", "text": "Tests are running."}], stop="end_turn", uuid="end"),
       {"type": "system", "subtype": "stop_hook_summary"}]
BACKGROUND = [_asst([{"type": "tool_use", "id": "toolu_bg1", "name": "Bash",
                      "input": {"run_in_background": True, "description": "full tests"}}]),
              _user([{"type": "tool_result", "content": "running in background"}])] + END
CLEAN = [_user("hi"), _asst([{"type": "text", "text": "hello"}], stop="end_turn")] + END[1:]
STOPPED = [_user("go"), _user([{"type": "text", "text": "[Request interrupted by user]"}], uuid="u2")]
API_ERROR = [_user("go"), {**_asst([{"type": "text", "text": "overloaded"}], stop="stop_sequence"), "isApiErrorMessage": True}]


class Machine:
    """A temp ~/.claude with session files, transcripts and a fake process table."""

    def __init__(self):
        root = Path(tempfile.mkdtemp())
        self.sessions = root / "sessions"
        self.projects = root / "projects"
        self.sessions.mkdir()
        self.projects.mkdir()
        self.procs: dict[int, int] = {}

    def add(self, pid, status, transcript=None, alive=True, start=1000, name=None):
        sid = f"0000000{pid}-0000-0000-0000-000000000000"[-36:]
        cwd = r"C:\Users\x\Git\repo"
        (self.sessions / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "sessionId": sid, "cwd": cwd, "procStart": str(start),
            "status": status, "name": name or f"session {pid}"}), encoding="utf-8")
        if alive:
            self.procs[pid] = start
        if transcript is not None:
            d = self.projects / bs.encode_project_slug(cwd)
            d.mkdir(exist_ok=True)
            (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(e) for e in transcript) + "\n", encoding="utf-8")
        return sid

    def detector(self):
        return bs.Detector(self.sessions, self.projects, lambda pid: self.procs.get(pid))


class TestBusySessions(unittest.TestCase):
    def test_busy_set(self):
        m = Machine()
        m.add(11, "busy")
        m.add(12, "idle", BACKGROUND)
        m.add(13, "idle", CLEAN)
        m.add(14, "waiting", BACKGROUND)
        m.add(15, "busy", alive=False)
        m.add(16, "idle", STOPPED)
        m.add(17, "idle", API_ERROR)
        m.add(18, "idle")  # no transcript found
        self.assertEqual(bs._auto_resume.classify(STOPPED)["state"], "user_stopped", "fixture check")
        self.assertEqual(bs._auto_resume.classify(API_ERROR)["state"], "api_error", "fixture check")
        reasons = {b.name: b.why for b in m.detector().busy()}
        self.assertEqual(reasons, {"session 11": "working", "session 12": "waiting on a background task"})

    def test_a_reused_pid_is_not_the_session(self):
        m = Machine()
        m.add(21, "busy", start=1000)
        m.procs[21] = 2000  # same pid, a different process
        self.assertEqual(m.detector().busy(), [])

    def test_malformed_entries_are_skipped(self):
        m = Machine()
        (m.sessions / "bad.json").write_text("{", encoding="utf-8")
        (m.sessions / "nopid.json").write_text('{"status": "busy"}', encoding="utf-8")
        (m.sessions / "oddstart.json").write_text('{"pid": 3, "procStart": 2907518, "status": "busy"}', encoding="utf-8")
        m.procs[3] = 2907518
        self.assertEqual(m.detector().busy(), [])

    def test_transcript_verdict_is_cached_until_the_file_changes(self):
        m = Machine()
        m.add(31, "idle", BACKGROUND)
        det = m.detector()
        calls = []
        real = bs._auto_resume.classify
        bs._auto_resume.classify = lambda entries, *a, **k: calls.append(1) or real(entries, *a, **k)
        try:
            det.busy()
            det.busy()
            self.assertEqual(len(calls), 1)
            path = next(m.projects.glob("*/*.jsonl"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title"}) + "\n")
            det.busy()
            self.assertEqual(len(calls), 2)
        finally:
            bs._auto_resume.classify = real

    def test_reason_text(self):
        one = [bs.Busy("a", "Fix the thing", "working")]
        self.assertEqual(bs.reason_text(one), "1 Claude session is still working: Fix the thing")
        many = [bs.Busy(str(i), "x" * 60, "working") for i in range(5)]
        text = bs.reason_text(many)
        self.assertTrue(text.startswith("5 Claude sessions are still working"))
        self.assertLessEqual(len(text), 200)
        self.assertTrue(text.endswith("..."))
        self.assertEqual(bs.reason_text([]), "")


class TestQueryEndSession(unittest.TestCase):
    BUSY = [bs.Busy("a", "n", "working")]

    def test_answers(self):
        block = {**rg.DEFAULTS, "block": True}
        self.assertFalse(rg.query_end_session_answer(self.BUSY, block, 0))
        self.assertTrue(rg.query_end_session_answer([], block, 0))
        self.assertTrue(rg.query_end_session_answer(self.BUSY, block, rg.ENDSESSION_CRITICAL))
        self.assertTrue(rg.query_end_session_answer(self.BUSY, rg.DEFAULTS, 0), "report-only never refuses")
        self.assertTrue(rg.query_end_session_answer(self.BUSY, {**block, "enabled": False}, 0))


class TestConfig(unittest.TestCase):
    def _cfg(self, text):
        p = Path(tempfile.mkdtemp()) / "c.json"
        if text is not None:
            p.write_text(text, encoding="utf-8")
        return rg.load_config(p)

    def test_defaults_are_report_only(self):
        for text in (None, "{", "[]"):
            with self.subTest(text):
                cfg = self._cfg(text)
                self.assertFalse(cfg["block"])
                self.assertFalse(cfg["deferUpdates"])

    def test_values_are_validated(self):
        cfg = self._cfg('{"block": "yes", "deferUpdates": true, "maxDeferHours": 500}')
        self.assertFalse(cfg["block"], "only a JSON true turns blocking on")
        self.assertTrue(cfg["deferUpdates"])
        self.assertEqual(cfg["maxDeferHours"], 24)


class FakeHours(rg.ActiveHours):
    def __init__(self, start=8, end=2):
        self.values = {"ActiveHoursStart": start, "ActiveHoursEnd": end}
        self.writes = 0

    def read(self):
        return {k: self.values.get(k) for k in ("ActiveHoursStart", "ActiveHoursEnd")}

    def set(self, name, value):
        self.values[name] = value
        self.writes += 1

    def delete(self, name):
        self.values.pop(name, None)
        self.writes += 1


class TestActiveHours(unittest.TestCase):
    NOON = dt.datetime(2026, 11, 22, 12, 30)

    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.anchor = d / "LOGS" / rg.ANCHOR_NAME
        self.deferral = d / "deferral.json"
        self.events = []
        self.hours = FakeHours()
        self.on = {**rg.DEFAULTS, "deferUpdates": True}

    def step(self, busy, cfg=None, now=None):
        return rg.update_active_hours(busy, cfg or self.on, self.hours, self.anchor, now or self.NOON,
                                      lambda e, **f: self.events.append(e), self.deferral)

    def test_window_covers_now(self):
        self.assertEqual(rg.window_for(self.NOON), (10, 4))
        self.assertEqual(rg.window_for(dt.datetime(2026, 11, 22, 1, 0)), (23, 17))

    def test_report_only_writes_nothing(self):
        self.assertEqual(self.step(True, rg.DEFAULTS), "report")
        self.assertEqual(self.hours.writes, 0)
        self.assertFalse(self.anchor.exists())

    def test_defer_then_restore(self):
        self.assertEqual(self.step(True), "deferred")
        self.assertEqual(self.hours.read(), {"ActiveHoursStart": 10, "ActiveHoursEnd": 4})
        self.assertEqual({k: v for k, v in json.loads(self.anchor.read_text()).items() if k != "capturedAt"},
                         {"ActiveHoursStart": 8, "ActiveHoursEnd": 2})
        self.assertEqual(self.step(True), "skip", "a second busy step in the same hour writes nothing")
        later = self.NOON + dt.timedelta(hours=1)
        self.assertEqual(self.step(True, now=later), "deferred")
        self.assertEqual(json.loads(self.anchor.read_text())["ActiveHoursStart"], 8, "the anchor is never overwritten")
        self.assertEqual(self.step(False), "restored")
        self.assertEqual(self.hours.read(), {"ActiveHoursStart": 8, "ActiveHoursEnd": 2})
        self.assertTrue(self.deferral.exists(), "idle at noon is inside active hours: the cap clock keeps running")
        self.assertEqual(self.step(False), "idle", "a second idle step writes nothing")
        night = dt.datetime(2026, 11, 23, 3, 0)
        self.assertEqual(self.step(False, now=night), "idle")
        self.assertFalse(self.deferral.exists(), "idle when Windows could restart resets the cap clock")

    def test_cap(self):
        self.step(True)
        late = self.NOON + dt.timedelta(hours=24)
        self.assertEqual(self.step(True, now=late), "cap")
        self.assertEqual(self.events.count("active_hours_cap"), 1)
        self.assertEqual(self.hours.read(), {"ActiveHoursStart": 8, "ActiveHoursEnd": 2})
        self.assertEqual(self.step(True, now=late + dt.timedelta(minutes=1)), "cap")
        self.assertEqual(self.events.count("active_hours_cap"), 1, "the cap is logged once, not every poll")

    def test_a_daytime_idle_gap_does_not_reset_the_cap(self):
        self.step(True)
        self.step(False, now=self.NOON + dt.timedelta(hours=5))  # 17:30, inside 8-2
        self.assertEqual(self.step(True, now=self.NOON + dt.timedelta(hours=24)), "cap")

    def test_outside_active_hours(self):
        hours = {"ActiveHoursStart": 8, "ActiveHoursEnd": 2}
        self.assertEqual([h for h in range(24) if rg.outside_active_hours(h, hours)], [2, 3, 4, 5, 6, 7])
        self.assertEqual([h for h in range(24) if rg.outside_active_hours(h, {})],
                         list(range(0, 8)) + list(range(17, 24)))

    def test_no_change_without_a_backup(self):
        self.anchor.parent.mkdir(parents=True)
        self.anchor.mkdir()  # a directory where the file should go: the write fails
        self.assertEqual(self.step(True), "error")
        self.assertEqual(self.hours.writes, 0)

    def test_no_change_over_a_corrupt_anchor(self):
        self.anchor.parent.mkdir(parents=True)
        self.anchor.write_text("{", encoding="utf-8")
        self.assertEqual(self.step(True), "error")
        self.assertEqual(self.hours.writes, 0, "restore couldn't use that anchor, so nothing may change")

    def test_restore_refuses_a_corrupt_anchor(self):
        self.anchor.parent.mkdir(parents=True)
        self.anchor.write_text("{", encoding="utf-8")
        with self.assertRaises(rg.AnchorError):
            rg.restore(self.hours, self.anchor, lambda *a, **k: None)
        self.assertEqual(self.hours.writes, 0)

    def test_restore_removes_values_that_were_absent(self):
        self.hours = FakeHours()
        self.hours.values = {}
        self.assertEqual(self.step(True), "deferred")
        self.assertEqual(self.step(False), "restored")
        self.assertEqual(self.hours.read(), {"ActiveHoursStart": None, "ActiveHoursEnd": None})


class TestFailOpen(unittest.TestCase):
    def test_a_failing_busy_check_blocks_nothing(self):
        class Broken:
            def busy(self):
                raise RuntimeError("session files unreadable")

        d = Path(tempfile.mkdtemp())
        cfg = d / "restart-guard.json"
        cfg.write_text(json.dumps({"enabled": True, "block": True, "deferUpdates": True}), encoding="utf-8")
        saved_log = rg.log
        events = []
        rg.log = lambda e, **f: events.append(e)
        try:
            guard = rg.Guard(detector=Broken(), config_path=cfg, deferral_path=d / "deferral.json")
            guard.hours = FakeHours()
            guard.anchor = d / rg.ANCHOR_NAME
            self.assertTrue(guard.cfg["block"], "the fixture config must be the one read")
            self.assertTrue(guard.on_query_end_session(0), "a broken check must let the restart through")
            self.assertEqual(guard.busy, [])
            self.assertEqual(guard.hours.writes, 0)
            self.assertIn("busy_check_error", events)
        finally:
            rg.log = saved_log


class TestRegisterScript(unittest.TestCase):
    def test_registration_settings(self):
        text = (SCRIPTS / "register-restart-guard.ps1").read_text(encoding="utf-8")
        for needle in ("-RunLevel Limited", "-Hidden", "-MultipleInstances IgnoreNew",
                       "ExecutionTimeLimit ([TimeSpan]::Zero)", "block = $false", "deferUpdates = $false",
                       "--restore", "-Encoding unicode", "UTF8Encoding($false)"):
            self.assertIn(needle, text)
        self.assertNotIn("RunAs", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
