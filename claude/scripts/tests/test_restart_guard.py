#!/usr/bin/env python3
"""Tests for restart-guard.py, _busy_sessions.py and register-restart-guard.ps1 (dev-env#1398, ADR-165).

Required when changing `restart-guard.py`, `_busy_sessions.py` or `register-restart-guard.ps1`.
Run: py -3 claude/scripts/tests/test_restart_guard.py

Everything runs on fixtures: temp session files with a fake process-creation lookup, temp
transcripts built the way test_auto_resume.py builds them, a dict-backed stand-in for the
active-hours registry values, and a temp backup anchor and state file. Nothing reads this machine's
sessions, writes the registry or creates the guard's window. The one exception is
`test_an_exited_process_is_not_alive`, which starts and waits for a short Python child process.

Scope gap: whether Windows asks the guard's window and lists it on its "apps are preventing restart"
screen, and what Windows Update does when active hours move, can only be seen on a real machine.
`test_window_is_one_windows_asks` pins only the window style that measurement found. See the ADR's
verification steps.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
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
_REAL_LOG = rg.log


def _no_real_log(event, *args, **fields):
    raise AssertionError(f"a test reached the real log in Documents\\LOGS ({event}); pass a logf or patch rg.log")


def setUpModule():
    rg.log = _no_real_log


def tearDownModule():
    rg.log = _REAL_LOG


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


def temp_dir(test: unittest.TestCase) -> Path:
    d = tempfile.TemporaryDirectory()
    test.addCleanup(d.cleanup)
    return Path(d.name)


class Machine:
    """A temp ~/.claude with session files, transcripts and a fake process table."""

    def __init__(self, root: Path):
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

    def detector(self, clock=None):
        return bs.Detector(self.sessions, self.projects, lambda pid: self.procs.get(pid),
                           **({"clock": clock} if clock else {}))


class TestBusySessions(unittest.TestCase):
    def setUp(self):
        self.m = Machine(temp_dir(self))

    def test_busy_set(self):
        m = self.m
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
        self.m.add(21, "busy", start=1000)
        self.m.procs[21] = 2000  # same pid, a different process
        self.assertEqual(self.m.detector().busy(), [])

    def test_malformed_entries_are_skipped(self):
        m = self.m
        (m.sessions / "bad.json").write_text("{", encoding="utf-8")
        (m.sessions / "nopid.json").write_text('{"status": "busy"}', encoding="utf-8")
        (m.sessions / "oddstart.json").write_text('{"pid": 3, "procStart": 2907518, "status": "busy"}', encoding="utf-8")
        m.procs[3] = 2907518
        self.assertEqual(m.detector().busy(), [])

    def test_transcript_verdict_is_cached_until_the_file_changes(self):
        self.m.add(31, "idle", BACKGROUND)
        det = self.m.detector()
        calls = []
        real = bs._auto_resume.classify
        bs._auto_resume.classify = lambda entries, *a, **k: calls.append(1) or real(entries, *a, **k)
        try:
            det.busy()
            det.busy()
            self.assertEqual(len(calls), 1)
            path = next(self.m.projects.glob("*/*.jsonl"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title"}) + "\n")
            det.busy()
            self.assertEqual(len(calls), 2)
        finally:
            bs._auto_resume.classify = real

    def test_caches_drop_sessions_that_ended(self):
        self.m.add(32, "idle", BACKGROUND)
        det = self.m.detector()
        det.busy()
        self.assertEqual(len(det._cache), 1)
        (self.m.sessions / "32.json").unlink()
        det.busy()
        self.assertEqual(det._cache, {})

    def test_a_missing_transcript_is_searched_rarely(self):
        self.m.add(33, "idle")  # no transcript at the direct path
        now = [0.0]
        det = self.m.detector(clock=lambda: now[0])
        calls = []
        real = bs._auto_resume.find_transcript
        bs._auto_resume.find_transcript = lambda *a, **k: calls.append(1) or real(*a, **k)
        try:
            det.busy()
            det.busy()
            self.assertEqual(len(calls), 1, "a miss is remembered")
            now[0] = bs.FIND_RETRY_SECONDS + 1
            det.busy()
            self.assertEqual(len(calls), 2, "and searched again later")
        finally:
            bs._auto_resume.find_transcript = real

    @unittest.skipUnless(os.name == "nt", "process_created reads Windows process times")
    def test_an_exited_process_is_not_alive(self):
        self.assertIsNotNone(bs.process_created(os.getpid()))
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()  # Popen still holds a handle, so the exited process can still be opened
        self.assertIsNone(bs.process_created(child.pid))

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
        self.assertTrue(rg.query_end_session_answer(self.BUSY, block, rg.ENDSESSION_CLOSEAPP),
                        "Restart Manager asking the guard to close is never refused")
        self.assertTrue(rg.query_end_session_answer(self.BUSY, rg.DEFAULTS, 0), "report-only never refuses")
        self.assertTrue(rg.query_end_session_answer(self.BUSY, {**block, "enabled": False}, 0))

    def test_window_is_one_windows_asks(self):
        # Measured on build 26300: Windows never sent WM_QUERYENDSESSION to an off-screen tool window;
        # an off-screen owned window was asked and blocked the restart.
        self.assertTrue(rg.GUARD_STYLE & rg.WS_VISIBLE)
        self.assertFalse(rg.GUARD_EX_STYLE & rg.WS_EX_TOOLWINDOW)


class TestConfig(unittest.TestCase):
    def _cfg(self, text):
        p = temp_dir(self) / "c.json"
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
        return {k: self.values.get(k) for k in rg.NAMES}

    def set(self, name, value):
        self.values[name] = value
        self.writes += 1

    def delete(self, name):
        self.values.pop(name, None)
        self.writes += 1


def hours(start, end):
    return {"ActiveHoursStart": start, "ActiveHoursEnd": end}


class TestActiveHours(unittest.TestCase):
    NOON = dt.datetime(2026, 11, 22, 12, 30)

    def setUp(self):
        d = temp_dir(self)
        self.anchor = d / "LOGS" / rg.ANCHOR_NAME
        self.state = d / "restart-guard-state.json"
        self.events = []
        self.hours = FakeHours()
        self.on = {**rg.DEFAULTS, "deferUpdates": True}

    def step(self, busy, cfg=None, now=None):
        return rg.update_active_hours(busy, cfg or self.on, self.hours, self.anchor, now or self.NOON,
                                      lambda e, **f: self.events.append(e), self.state)

    def anchor_data(self):
        return rg.read_anchor(self.anchor)

    def test_window_covers_now(self):
        self.assertEqual(rg.window_for(self.NOON), (10, 4))
        self.assertEqual(rg.window_for(dt.datetime(2026, 11, 22, 1, 0)), (23, 17))

    def test_report_only_writes_nothing(self):
        self.assertEqual(self.step(True, rg.DEFAULTS), "report")
        self.assertEqual(self.hours.writes, 0)
        self.assertFalse(self.anchor.exists())
        self.assertFalse(self.state.exists(), "report-only doesn't start the cap clock")

    def test_defer_then_restore(self):
        self.assertEqual(self.step(True), "deferred")
        self.assertEqual(self.hours.read(), hours(10, 4))
        self.assertEqual(self.anchor_data(), {"values": hours(8, 2), "window": (10, 4)})
        self.assertEqual(self.step(True), "unchanged", "a second busy step in the same hour writes nothing")
        later = self.NOON + dt.timedelta(hours=1)
        self.assertEqual(self.step(True, now=later), "deferred")
        self.assertEqual(self.anchor_data(), {"values": hours(8, 2), "window": (11, 5)},
                         "the captured values stay; only the window moves")
        self.assertEqual(self.step(False), "restored")
        self.assertEqual(self.hours.read(), hours(8, 2))
        self.assertEqual(self.anchor_data(), {"values": hours(8, 2), "window": None})
        self.assertTrue(self.state.exists(), "idle at noon is inside active hours: the cap clock keeps running")
        writes = self.hours.writes
        self.assertEqual(self.step(False), "skip", "a second idle step writes nothing")
        self.assertEqual(self.hours.writes, writes)
        night = dt.datetime(2026, 11, 23, 3, 0)
        self.assertEqual(self.step(False, now=night), "skip")
        self.assertFalse(self.state.exists(), "idle when Windows could restart resets the cap clock")

    def test_idle_with_no_deferral_never_writes(self):
        self.step(True)
        self.step(False)
        self.hours.values = hours(9, 1)  # you change active hours in Settings, between deferrals
        for _ in range(3):
            self.assertEqual(self.step(False), "skip")
        self.assertEqual(self.hours.read(), hours(9, 1), "the old capture is never put back")

    def test_a_change_made_elsewhere_is_left_alone(self):
        self.step(True)
        self.hours.values = hours(9, 1)  # changed during the deferral
        self.assertEqual(self.step(False), "released")
        self.assertEqual(self.hours.read(), hours(9, 1))
        self.assertIn("active_hours_changed_elsewhere", self.events)
        self.assertEqual(self.anchor_data()["window"], None)

    def test_a_change_made_while_busy_is_the_value_restored(self):
        self.step(True)
        self.hours.values = hours(7, 23)
        self.assertEqual(self.step(True, now=self.NOON + dt.timedelta(hours=1)), "deferred")
        self.assertEqual(self.anchor_data()["values"], hours(7, 23))
        self.assertEqual(self.step(False), "restored")
        self.assertEqual(self.hours.read(), hours(7, 23))

    def test_each_deferral_captures_afresh(self):
        self.step(True)
        self.step(False)
        self.hours.values = hours(6, 0)
        self.step(True)
        self.assertEqual(self.anchor_data()["values"], hours(6, 0))
        self.step(False)
        self.assertEqual(self.hours.read(), hours(6, 0))

    def test_unknown_holds(self):
        self.step(True)
        writes = self.hours.writes
        self.assertEqual(self.step(None), "hold")
        self.assertEqual(self.step(None, now=self.NOON + dt.timedelta(hours=1)), "hold",
                         "an unknown step doesn't move the window either")
        self.assertEqual(self.hours.writes, writes)
        self.assertEqual(self.step(None, now=self.NOON + dt.timedelta(hours=24)), "cap",
                         "the cap still ends a deferral while the busy check is failing")
        self.assertEqual(self.hours.read(), hours(8, 2))

    def test_turning_defer_updates_off_ends_the_deferral(self):
        self.step(True)
        self.assertEqual(self.step(True, rg.DEFAULTS), "restored")
        self.assertEqual(self.hours.read(), hours(8, 2))
        self.assertFalse(self.state.exists())
        self.assertEqual(self.step(True, rg.DEFAULTS), "report")

    def test_cap(self):
        self.step(True)
        late = self.NOON + dt.timedelta(hours=24)
        self.assertEqual(self.step(True, now=late), "cap")
        self.assertEqual(self.events.count("active_hours_cap"), 1)
        self.assertEqual(self.hours.read(), hours(8, 2))
        self.assertEqual(self.step(True, now=late + dt.timedelta(minutes=1)), "cap")
        self.assertEqual(self.events.count("active_hours_cap"), 1, "the cap is logged once, not every poll")

    def test_a_daytime_idle_gap_does_not_reset_the_cap(self):
        self.step(True)
        self.step(False, now=self.NOON + dt.timedelta(hours=5))  # 17:30, inside 8-2
        self.assertEqual(self.step(True, now=self.NOON + dt.timedelta(hours=24)), "cap")

    def test_outside_active_hours(self):
        self.assertEqual([h for h in range(24) if rg.outside_active_hours(h, hours(8, 2))], [2, 3, 4, 5, 6, 7])
        self.assertEqual([h for h in range(24) if rg.outside_active_hours(h, {})],
                         list(range(0, 8)) + list(range(17, 24)))

    def test_unusable_live_values_are_not_captured(self):
        self.hours.values = hours(24, 2)
        self.assertEqual(self.step(True), "error")
        self.assertEqual(self.hours.writes, 0)
        self.assertFalse(self.anchor.exists())

    def test_no_change_without_a_backup(self):
        self.anchor.parent.mkdir(parents=True)
        self.anchor.mkdir()  # a directory where the file should go: the write fails
        self.assertEqual(self.step(True), "error")
        self.assertEqual(self.hours.writes, 0)

    def test_no_change_over_a_corrupt_anchor(self):
        self.anchor.parent.mkdir(parents=True)
        self.anchor.write_text("{", encoding="utf-8")
        self.assertEqual(self.step(True), "error")
        self.assertEqual(self.step(False), "error")
        self.assertEqual(self.hours.writes, 0, "restore couldn't use that anchor, so nothing may change")

    def test_restore_removes_values_that_were_absent(self):
        self.hours.values = {}
        self.assertEqual(self.step(True), "deferred")
        self.assertEqual(self.step(False), "restored")
        self.assertEqual(self.hours.read(), hours(None, None))


class TestRestoreCommand(unittest.TestCase):
    def setUp(self):
        d = temp_dir(self)
        self.anchor = d / rg.ANCHOR_NAME
        self.state = d / "state.json"
        self.hours = FakeHours()

    def run_restore(self, running=False):
        return rg.restore_command(lambda: running, self.hours, self.anchor, self.state, lambda *a, **k: None)

    def defer(self):
        rg.update_active_hours(True, {**rg.DEFAULTS, "deferUpdates": True}, self.hours, self.anchor,
                               dt.datetime(2026, 11, 22, 12, 30), lambda *a, **k: None, self.state)

    def test_refuses_while_the_guard_runs(self):
        self.defer()
        self.assertEqual(self.run_restore(running=True), 1)
        self.assertEqual(self.hours.read(), hours(10, 4))
        self.assertTrue(self.state.exists(), "the cap clock isn't reset either")

    def test_restores_an_outstanding_deferral(self):
        self.defer()
        self.assertEqual(self.run_restore(), 0)
        self.assertEqual(self.hours.read(), hours(8, 2))
        self.assertFalse(self.state.exists())
        writes = self.hours.writes
        self.assertEqual(self.run_restore(), 0, "a second restore converges")
        self.assertEqual(self.hours.writes, writes)

    def test_without_a_backup_changes_nothing(self):
        self.assertEqual(self.run_restore(), 0)
        self.assertEqual(self.hours.writes, 0)

    def test_refuses_a_corrupt_anchor(self):
        self.anchor.write_text("{", encoding="utf-8")
        self.assertEqual(self.run_restore(), 1)
        self.assertEqual(self.hours.writes, 0)


class Switchable:
    """A detector whose answer the test sets: a list of Busy, or an exception to raise."""

    def __init__(self):
        self.answer = []
        self.calls = 0

    def busy(self):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class TestGuard(unittest.TestCase):
    BUSY = [bs.Busy("a", "n", "working")]

    def setUp(self):
        d = temp_dir(self)
        cfg = d / "restart-guard.json"
        cfg.write_text(json.dumps({"enabled": True, "block": True, "deferUpdates": True}), encoding="utf-8")
        self.events = []
        saved = rg.log
        rg.log = lambda e, **f: self.events.append(e)
        self.addCleanup(setattr, rg, "log", saved)
        self.detector = Switchable()
        self.guard = rg.Guard(detector=self.detector, config_path=cfg, state_path=d / "state.json",
                              anchor_path=d / rg.ANCHOR_NAME, logs_dir=d)
        self.guard.hours = FakeHours()

    def window(self):
        return rg.read_anchor(self.guard.anchor)["window"]

    def test_a_failing_busy_check_blocks_nothing_and_leaves_active_hours_alone(self):
        self.detector.answer = self.BUSY
        self.guard.poll()
        moved = self.guard.hours.read()
        self.assertNotEqual(moved, hours(8, 2), "fixture: a deferral is in place")
        writes = self.guard.hours.writes
        self.detector.answer = RuntimeError("session files unreadable")
        self.guard.poll()
        self.guard.poll()
        self.assertTrue(self.guard.on_query_end_session(0), "a broken check must let the restart through")
        self.assertEqual(self.guard.hours.read(), moved, "the deferral is neither ended nor moved")
        self.assertEqual(self.guard.hours.writes, writes)
        self.assertEqual(self.events.count("busy_check_error"), 1, "a repeated error is logged once")
        self.detector.answer = []
        self.guard.poll()
        self.assertIn("recovered", self.events)

    def test_one_idle_poll_does_not_restore(self):
        self.detector.answer = self.BUSY
        self.guard.poll()
        self.detector.answer = []
        self.guard.poll()
        self.assertIsNotNone(self.window(), "one idle poll is not enough to end a deferral")
        for _ in range(rg.IDLE_POLLS - 2):
            self.guard.poll()
            self.assertIsNotNone(self.window(), "still deferring")
        self.guard.poll()
        self.assertIsNone(self.window())
        self.assertEqual(self.guard.hours.read(), hours(8, 2))

    def test_the_shutdown_answer_comes_from_the_last_poll(self):
        self.detector.answer = self.BUSY
        self.guard.poll()
        calls = self.detector.calls
        self.assertFalse(self.guard.on_query_end_session(0))
        self.assertEqual(self.detector.calls, calls, "no new check while Windows waits for the answer")
        self.assertTrue(self.guard.on_query_end_session(rg.ENDSESSION_CLOSEAPP))

    def test_layer2_changes_are_logged_once(self):
        self.detector.answer = self.BUSY
        for _ in range(3):
            self.guard.poll()
        self.assertEqual(self.events.count("active_hours"), 2, "deferred, then unchanged: not one line per poll")


class TestLogs(unittest.TestCase):
    def test_prune_logs(self):
        d = temp_dir(self)
        today = dt.date(2026, 11, 23)
        for days in (0, 14, 15, 40):
            (d / f"claude-restart-guard_{(today - dt.timedelta(days=days)).isoformat()}.jsonl").write_text("{}\n")
        (d / "claude-restart-guard_notadate.jsonl").write_text("{}\n")
        (d / "other_2020-01-01.jsonl").write_text("{}\n")
        self.assertEqual(rg.prune_logs(d, today), 2)
        self.assertEqual(sorted(p.name for p in d.iterdir()), [
            "claude-restart-guard_2026-11-09.jsonl", "claude-restart-guard_2026-11-23.jsonl",
            "claude-restart-guard_notadate.jsonl", "other_2020-01-01.jsonl"])


class TestRegisterScript(unittest.TestCase):
    TEXT = (SCRIPTS / "register-restart-guard.ps1").read_text(encoding="utf-8")

    def test_registration_settings(self):
        for needle in ("-RunLevel Limited", "-Hidden", "-MultipleInstances IgnoreNew",
                       "ExecutionTimeLimit ([TimeSpan]::Zero)", "block = $false", "deferUpdates = $false",
                       "-Encoding unicode", "UTF8Encoding($false)", "-Priority 4"):
            self.assertIn(needle, self.TEXT)
        self.assertNotIn("RunAs", self.TEXT)

    def test_task_runs_pythonw_not_the_launcher(self):
        # The launcher's job takes the guard down when a restart ends the windowless launcher.
        self.assertIn("-Execute $pythonw", self.TEXT)
        self.assertNotIn("Get-Command pyw.exe", self.TEXT)

    def test_unregister_stops_the_guard_before_restoring(self):
        block = self.TEXT[self.TEXT.index("if ($Unregister) {"):]
        block = block[:block.index("\n}\n")]
        order = [block.index(s) for s in ("Export-ScheduledTask", "Stop-Guard", "--restore", "Unregister-ScheduledTask")]
        self.assertEqual(order, sorted(order), "back up, stop, restore, then unregister")
        self.assertNotIn("pyw", block, "the undo must not need pyw.exe")
        self.assertIn("throw", block[block.index("Test-Path $ActiveHoursBackup"):block.index("--restore")],
                      "a restore that can't run must stop the undo, not be skipped")


if __name__ == "__main__":
    unittest.main(verbosity=1)
