#!/usr/bin/env python3
"""Tests for _machine_installs.py and claude/machine-installs.json (dev-env#1397).

Required when changing `_machine_installs.py`, `claude/machine-installs.json`, or adding, renaming or removing a `claude/scripts/register-*.ps1`.
Run: py -3 claude/scripts/tests/test_machine_installs.py

The parity test reads the real manifest and the real register-*.ps1 glob, so a new installer
with no manifest entry fails here. Everything else runs on fixtures: the query's JSON output, a
fake `subprocess.run`, temp config files and a temp nudge stamp. Nothing queries or changes this
machine's scheduled tasks.

Scope gap: `--install` (which runs the register scripts) and the PowerShell query itself are not
covered here; run `py -3 claude/scripts/_machine_installs.py` on the machine. The query was checked
on LETHEON on 2026-11-22 against a throwaway task with a non-ASCII path, a disabled task and a
missing name containing a quote.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import _machine_installs as mi  # noqa: E402

MANIFEST = SCRIPTS.parent / "machine-installs.json"
ACCESS_DENIED = -2147024891  # 0x80070005


def temp_dir(test: unittest.TestCase) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def row(name: str, arguments: str = r'-3 "C:\x\a.py"', enabled: bool = True) -> dict:
    return {"name": name, "found": True, "enabled": enabled, "command": "pyw.exe", "arguments": arguments}


ENTRY = {
    "name": "auto-resume",
    "register": "register-auto-resume.ps1",
    "tasks": [{"name": "ClaudeAutoResumeLauncher", "script": "auto-resume-launcher.py"}],
    "config": "auto-resume.json",
}
OUTSIDE = {
    "name": "deploy-approval-notifier",
    "register": "register-deploy-approval-notifier.ps1",
    "tasks": [{"name": "ClaudeDeployApprovalNotifier", "script": "deploy-approval-notifier.py"}],
    "outside_app": True,
}
SCRIPTS_DIR = Path(r"C:\Users\someone\.claude\scripts")


def found(script_path: str, enabled: bool = True) -> mi.TaskState:
    return mi.TaskState(True, mi.TaskInfo(enabled, "pyw.exe", f'-3 "{script_path}" --tick'), "")


class TestManifestParity(unittest.TestCase):
    def test_every_register_script_has_an_entry_and_every_entry_exists(self):
        registers = sorted(p.name for p in SCRIPTS.glob("register-*.ps1"))
        self.assertTrue(registers, "the glob found no register-*.ps1 -- the check would pass vacuously")
        installs = mi.load_manifest(MANIFEST)
        listed = sorted(e["register"] for e in installs)
        self.assertEqual(registers, listed, "each claude/scripts/register-*.ps1 needs exactly one entry in claude/machine-installs.json")
        for entry in installs:
            for task in entry["tasks"]:
                self.assertTrue((SCRIPTS / task["script"]).is_file(), f"{entry['name']}: {task['script']} does not exist")
                self.assertIn(task["name"], (SCRIPTS / entry["register"]).read_text(encoding="utf-8"),
                              f"{entry['register']} never names its task {task['name']}")

    def test_names_are_unique(self):
        names = [e["name"] for e in mi.load_manifest(MANIFEST)]
        self.assertEqual(len(names), len(set(names)))


class TestLoadManifest(unittest.TestCase):
    def _write(self, text: str) -> Path:
        path = temp_dir(self) / "m.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_unreadable_empty_and_malformed_raise(self):
        cases = {
            "missing file": temp_dir(self) / "absent.json",
            "invalid JSON": self._write("{"),
            "no installs": self._write('{"installs": []}'),
            "entry with no tasks": self._write('{"installs": [{"name": "x", "register": "r.ps1", "tasks": []}]}'),
            "task with no script": self._write('{"installs": [{"name": "x", "register": "r.ps1", "tasks": [{"name": "T"}]}]}'),
        }
        for label, path in cases.items():
            with self.subTest(label), self.assertRaises(mi.ManifestError):
                mi.load_manifest(path)


class TestParseQueryOutput(unittest.TestCase):
    def parse(self, rows, names=("T",)):
        return mi.parse_query_output(list(names), json.dumps(rows))

    def test_found_with_action_and_enabled(self):
        state = self.parse([row("T", r'-3 "C:\x\a.py" --tick', enabled=False)])["T"]
        self.assertEqual(state, mi.TaskState(True, mi.TaskInfo(False, "pyw.exe", r'-3 "C:\x\a.py" --tick'), ""))

    def test_only_not_found_is_missing(self):
        missing = self.parse([{"name": "T", "found": False, "hresult": mi.TASK_NOT_FOUND, "error": "The system cannot find the file specified."}])["T"]
        self.assertIs(missing.found, False)
        denied = self.parse([{"name": "T", "found": False, "hresult": ACCESS_DENIED, "error": "Access is denied."}])["T"]
        self.assertIsNone(denied.found, "a task that can't be read is unknown, never missing")
        self.assertIn("0x80070005", denied.detail)

    def test_no_program_unanswered_and_garbage_are_unknown(self):
        self.assertIsNone(self.parse([dict(row("T"), command="")])["T"].found)
        self.assertIsNone(self.parse([row("Other")])["T"].found)
        self.assertIsNone(mi.parse_query_output(["T"], "Get-ScheduledTask : error")["T"].found)
        self.assertIsNone(mi.parse_query_output(["T"], '{"name": "T"}')["T"].found)

    def test_escaped_non_ascii_round_trips(self):
        # The query escapes non-ASCII to \uXXXX; schtasks turned this em dash into "-".
        text = '[{"name":"T","found":true,"enabled":true,"command":"pyw.exe","arguments":"C:\\\\Ren\\u00e9\\u2014\\\\a.py"}]'
        self.assertEqual(mi.parse_query_output(["T"], text)["T"].info.arguments, "C:\\Ren\u00e9\u2014\\a.py")


class TestQueryTasks(unittest.TestCase):
    def test_one_run_for_every_name(self):
        seen = []

        def run(args, **kwargs):
            seen.append((args, kwargs["timeout"]))
            return subprocess.CompletedProcess(args, 0, json.dumps([row("A"), row("B")]).encode(), b"")

        states = mi.query_tasks(["A", "B", "A"], run)
        self.assertEqual(len(seen), 1, "one PowerShell run, so the check has one timeout")
        self.assertLess(seen[0][1], 30, "below hook-liveness-check.py's 30 s timeout")
        self.assertIn("'A', 'B'", seen[0][0][-1])
        self.assertTrue(states["A"].found and states["B"].found)

    def test_names_are_quoted_for_powershell(self):
        self.assertIn("'it''s'", mi.query_script(["it's"]))

    def test_a_failed_run_makes_every_task_unknown(self):
        def failing(args, **_):
            return subprocess.CompletedProcess(args, 1, b"", b"New-Object : Retrieving the COM class factory failed\r\n")

        def broken(*a, **k):
            raise OSError("no powershell")

        def slow(*a, **k):
            raise subprocess.TimeoutExpired("powershell", 20)

        for run in (failing, broken, slow):
            with self.subTest(run.__name__):
                states = mi.query_tasks(["A", "B"], run)
                self.assertEqual({s.found for s in states.values()}, {None})

    def test_no_names_runs_nothing(self):
        self.assertEqual(mi.query_tasks([], lambda *a, **k: self.fail("ran")), {})


class TestScriptPaths(unittest.TestCase):
    def test_quoted_bare_and_none(self):
        self.assertEqual(mi.script_paths(r'-3 "C:\a b\x.py" --tick'), [r"C:\a b\x.py"])
        self.assertEqual(mi.script_paths(r"-3 C:\a\y.py"), [r"C:\a\y.py"])
        self.assertEqual(mi.script_paths("--tick"), [])


class TestEvaluate(unittest.TestCase):
    def _one(self, state, config="ok", skips=frozenset(), entry=ENTRY):
        tasks = {t["name"]: state for t in entry["tasks"]}
        configs = {entry["config"]: config} if entry.get("config") else {}
        findings = mi.evaluate([entry], tasks, set(skips), configs, SCRIPTS_DIR)
        self.assertEqual(len(findings), 1)
        return findings[0]

    def test_ok_ignores_slash_direction_and_case(self):
        path = str(SCRIPTS_DIR / "auto-resume-launcher.py").replace("\\", "/").upper()
        self.assertEqual(self._one(found(path)).status, mi.OK)

    def test_missing_task_is_a_problem_with_its_register_command(self):
        f = self._one(mi.TaskState(False, None, "not registered (ERROR: ...)"))
        self.assertEqual(f.status, mi.PROBLEM)
        self.assertIn("ClaudeAutoResumeLauncher is not registered", f.detail)
        self.assertIn(str(SCRIPTS_DIR / "register-auto-resume.ps1"), f.fix)

    def test_task_running_another_copy_is_a_problem(self):
        f = self._one(found(r"C:\Users\someone\Git\dev-env\.claude\worktrees\x\claude\scripts\auto-resume-launcher.py"))
        self.assertEqual(f.status, mi.PROBLEM)
        self.assertIn("runs", f.detail)

    def test_disabled_task_is_a_problem(self):
        f = self._one(found(str(SCRIPTS_DIR / "auto-resume-launcher.py"), enabled=False))
        self.assertEqual(f.status, mi.PROBLEM)
        self.assertIn("disabled", f.detail)

    def test_config_missing_or_unreadable_is_a_problem(self):
        good = found(str(SCRIPTS_DIR / "auto-resume-launcher.py"))
        for cstate, words in (("missing", "is missing"), ("unreadable", "not readable")):
            with self.subTest(cstate):
                f = self._one(good, config=cstate)
                self.assertEqual(f.status, mi.PROBLEM)
                self.assertIn(words, f.detail)

    def test_config_turned_off_is_a_deliberate_skip(self):
        # register-auto-resume.ps1 -Unregister removes the task and sets "enabled": false.
        # --install must not undo that by re-running the register script.
        f = self._one(mi.TaskState(False, None, "not registered"), config="off")
        self.assertEqual(f.status, mi.SKIPPED)
        self.assertEqual(f.fix, "")
        self.assertIn('"enabled": false', f.detail)
        self.assertIn(str(SCRIPTS_DIR / "register-auto-resume.ps1"), f.detail)

    def test_skipped_install_is_not_checked(self):
        f = self._one(mi.TaskState(False, None, "not registered"), skips={"auto-resume"})
        self.assertEqual(f.status, mi.SKIPPED)

    def test_unreadable_task_is_unknown_not_missing(self):
        f = self._one(mi.TaskState(None, None, "the Task Scheduler query failed: OSError"))
        self.assertEqual(f.status, mi.UNKNOWN)

    def test_outside_app_fix_says_where_to_run_it(self):
        f = self._one(mi.TaskState(False, None, "not registered"), entry=OUTSIDE)
        self.assertIn("not a Claude desktop session", f.fix)


class TestCheck(unittest.TestCase):
    def test_reads_skips_and_configs_from_home_and_queries_only_what_it_checks(self):
        home = temp_dir(self)
        (home / "machine.json").write_text('{"skip_installs": ["deploy-approval-notifier"]}', encoding="utf-8")
        (home / "auto-resume.json").write_text('{"enabled": true}', encoding="utf-8")
        asked = []

        def query(names):
            asked.extend(names)
            return {"ClaudeAutoResumeLauncher": found(str(home / "scripts" / "auto-resume-launcher.py"))}

        findings = {f.name: f.status for f in mi.check([ENTRY, OUTSIDE], query, home)}
        self.assertEqual(findings, {"auto-resume": mi.OK, "deploy-approval-notifier": mi.SKIPPED})
        self.assertEqual(asked, ["ClaudeAutoResumeLauncher"], "a skipped install's tasks are not queried")


class TestConfigAndSkips(unittest.TestCase):
    def test_config_state(self):
        d = temp_dir(self)
        (d / "on.json").write_text('{"enabled": true}', encoding="utf-8")
        (d / "off.json").write_text('{"enabled": false}', encoding="utf-8")
        (d / "bad.json").write_text("{", encoding="utf-8")
        self.assertEqual(mi.config_state(d / "on.json"), "ok")
        self.assertEqual(mi.config_state(d / "off.json"), "off")
        self.assertEqual(mi.config_state(d / "bad.json"), "unreadable")
        self.assertEqual(mi.config_state(d / "absent.json"), "missing")

    def test_load_skips(self):
        self.assertEqual(mi.load_skips(b'{"skip_installs": ["a", 3]}'), {"a"})
        self.assertEqual(mi.load_skips(b'{"routine_host": true}'), set())
        self.assertEqual(mi.load_skips(b'{"skip_installs": "a"}'), set())
        self.assertEqual(mi.load_skips(b"{"), set())
        self.assertEqual(mi.load_skips(None), set())


class TestDailyNudge(unittest.TestCase):
    PROBLEM = [mi.Finding(mi.PROBLEM, "auto-resume", "ClaudeAutoResumeLauncher is not registered", "powershell -File x.ps1")]

    def setUp(self):
        home = temp_dir(self)
        self.stamp = home / "stamp.txt"
        self.scripts = home / "scripts"
        self.scripts.mkdir()

    def nudge(self, today, run_check):
        return mi.daily_nudge(self.stamp, today, run_check, self.scripts)

    def test_once_per_day(self):
        first = self.nudge("2026-11-22", lambda: self.PROBLEM)
        self.assertIn("auto-resume", first)
        self.assertIn("fix: powershell -File x.ps1", first)
        self.assertEqual(self.nudge("2026-11-22", lambda: self.PROBLEM), "")
        self.assertIn("auto-resume", self.nudge("2026-11-23", lambda: self.PROBLEM))

    def test_quiet_when_nothing_is_wrong_or_the_check_fails(self):
        self.assertEqual(self.nudge("2026-11-22", lambda: [mi.Finding(mi.OK, "a", "fine", "")]), "")

        def broken():
            raise mi.ManifestError("unreadable")

        self.assertEqual(self.nudge("2026-11-23", broken), "")
        self.assertEqual(self.stamp.read_text(encoding="utf-8"), "2026-11-23")

    def test_quiet_without_a_dev_env_install(self):
        self.scripts.rmdir()
        self.assertEqual(self.nudge("2026-11-22", lambda: self.PROBLEM), "")

    def test_format_is_ascii(self):
        mi.format_nudge(self.PROBLEM).encode("ascii")


if __name__ == "__main__":
    unittest.main(verbosity=1)
