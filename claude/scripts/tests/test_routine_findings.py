#!/usr/bin/env python3
"""Tests for routine-findings.py (dev-env#1308, ADR-160).

Required when changing claude/scripts/routine-findings.py, the block that calls it in
claude/routines/*/SKILL.md, or claude/skills/sync-routine-worktree/SKILL.md.
Run: py -3 claude/scripts/tests/test_routine_findings.py

Runs the real script as a subprocess, for both modes, against a temp findings directory.
The last test checks the routines themselves: every routine that mentions a push
notification must call the script.
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _heartbeat_isolation import isolate_heartbeat

isolate_heartbeat()

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "claude" / "scripts" / "routine-findings.py"
sys.path.insert(0, str(SCRIPT.parent))
INACTIVE = "Mobile push not sent (Remote Control inactive)."


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "findings"
        self.env = dict(os.environ, ROUTINE_FINDINGS_DIR=str(self.base))

    def save(self, push_result, routine="prune-stale-worktrees", message="3 branches not merged"):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "save", "--routine", routine, "--message", message,
             "--push-result", push_result],
            env=self.env, capture_output=True, encoding="utf-8",
        )

    def hook(self, prompt="hello"):
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps({"prompt": prompt}), env=self.env, capture_output=True, encoding="utf-8",
        )

    def pending(self):
        return sorted((self.base / "pending").glob("*.json"))

    def shown(self):
        return sorted((self.base / "shown").glob("*.json"))

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class SaveTests(Base):
    def test_failed_push_saves_the_finding(self):
        result = self.save(INACTIVE)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = self.pending()
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["routine"], "prune-stale-worktrees")
        self.assertEqual(record["message"], "3 branches not merged")
        self.assertEqual(record["push_result"], INACTIVE)

    def test_delivered_push_saves_nothing(self):
        for delivered in ("Mobile push requested.", "Terminal notification sent. Mobile push requested."):
            result = self.save(delivered)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("nothing saved", result.stdout)
        self.assertEqual(self.pending(), [])

    def test_every_other_result_counts_as_not_delivered(self):
        others = [
            "",
            "Terminal notification sent. Mobile push not sent (Remote Control inactive).",
            "Push not sent — mobile push is disabled in /config.",
            "Not sent — this terminal is active, so your output here already reaches the user.",
            "Some wording a later release introduces.",
        ]
        for other in others:
            self.assertEqual(self.save(other).returncode, 0)
        self.assertEqual(len(self.pending()), len(others))

    def test_unsavable_finding_exits_nonzero(self):
        self.base.parent.mkdir(exist_ok=True)
        self.base.write_text("a file where the directory should be", encoding="utf-8")
        result = self.save(INACTIVE)
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not save", result.stderr)

    def test_routine_name_cannot_escape_pending(self):
        self.assertEqual(self.save(INACTIVE, routine="../../evil").returncode, 0)
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["pending"])


class HookTests(Base):
    def test_shows_a_finding_once(self):
        self.save(INACTIVE)
        first = self.context(self.hook())
        self.assertIn("prune-stale-worktrees", first)
        self.assertIn("3 branches not merged", first)
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(self.shown()), 1)

        second = self.hook()
        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "")

    def test_nothing_pending_is_silent(self):
        result = self.hook()
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_automated_prompt_leaves_findings_for_a_person(self):
        self.save(INACTIVE)
        result = self.hook(prompt='<scheduled-task name="prune-stale-worktrees">')
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(len(self.pending()), 1)
        self.assertIn("3 branches not merged", self.context(self.hook()))

    def test_output_is_ascii_and_keeps_the_message(self):
        self.save(INACTIVE, message="café — done")
        result = self.hook()
        self.assertTrue(result.stdout.isascii())
        self.assertIn("café — done", self.context(result))

    def test_overflow_stays_pending_oldest_first(self):
        spec = importlib.util.spec_from_file_location("routine_findings", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        start = datetime.datetime(2026, 11, 7, tzinfo=datetime.timezone.utc)
        for i in range(module.MAX_SHOWN + 2):
            module.save_finding("r", f"finding-{i:02d}.", INACTIVE, self.base,
                                now=start + datetime.timedelta(minutes=i))
        first = self.context(self.hook())
        self.assertIn("finding-00.", first)
        self.assertNotIn(f"finding-{module.MAX_SHOWN:02d}.", first)
        self.assertIn("2 more will follow", first)
        self.assertEqual(len(self.pending()), 2)
        self.assertIn(f"finding-{module.MAX_SHOWN:02d}.", self.context(self.hook()))
        self.assertEqual(self.pending(), [])

    def test_long_message_is_cut_with_a_pointer(self):
        self.save(INACTIVE, message="x" * 1000)
        text = self.context(self.hook())
        self.assertNotIn("x" * 301, text)
        self.assertIn("full text:", text)
        record = json.loads(self.shown()[0].read_text(encoding="utf-8"))
        self.assertEqual(len(record["message"]), 1000)

    def test_unreadable_finding_is_reported_not_dropped(self):
        (self.base / "pending").mkdir(parents=True)
        (self.base / "pending" / "20260101T000000000000Z-r-1.json").write_text("{not json", encoding="utf-8")
        self.assertIn("unreadable finding", self.context(self.hook()))

    def test_bad_stdin_fails_open(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], input="not json", env=self.env,
                                capture_output=True, encoding="utf-8")
        self.assertEqual((result.returncode, result.stdout), (0, ""))


class RoutineCoverageTests(unittest.TestCase):
    def test_every_routine_that_pushes_calls_the_script(self):
        routines = sorted((REPO / "claude" / "routines").glob("*/SKILL.md"))
        pushing = [p for p in routines if "push notification" in p.read_text(encoding="utf-8").lower()]
        self.assertGreater(len(pushing), 0, "found no routine that pushes, so this test checked nothing")
        missing = [p.parent.name for p in pushing if "routine-findings.py" not in p.read_text(encoding="utf-8")]
        self.assertEqual(missing, [])

    def test_the_shared_abort_path_calls_the_script(self):
        skill = REPO / "claude" / "skills" / "sync-routine-worktree" / "SKILL.md"
        self.assertIn("routine-findings.py", skill.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
