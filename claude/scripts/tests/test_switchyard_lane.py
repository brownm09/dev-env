#!/usr/bin/env python3
"""Drift-guard and argument handling for `switchyard-lane.js` (dev-env#1406).

Required when changing `claude/scripts/switchyard-lane.js`, or when switchyard's
`src/store.ts` predicate or `src/admission.ts` cap groups move.
Run: py -3 claude/scripts/tests/test_switchyard_lane.py

Covers only the branches that run before the store is opened -- everything up to and
including the `better-sqlite3` require. That is the whole of the guard, and it needs
nothing but a fixture switchyard checkout: two `.ts` files in a temp directory. The two
answer branches (`LANE FREE` / `LANE HELD`) read a live `~/.switchyard/state.db` and a
real `better-sqlite3`, neither of which a CI runner has, so they stay verified by hand
and are recorded on the PR that lands a change.

Calibration references, in the sense ADR-144 asks for:
  known-good -- a fixture carrying every literal and a well-formed cap block must get
    PAST both guards. Observable because it then fails on the absent `better-sqlite3`
    with that module named, which no drift refusal mentions.
  known-bad  -- each predicate literal removed in turn, each cap group removed, an
    unreadable cap value, and a cap block whose `perRepo` lives only in the
    `awaitingMerge` group. The last one is the regression test for the unanchored
    `/perRepo:\\s*(\\d+)/` this script used to carry: that regex read the awaiting-merge
    cap as the lane's, so the fixture would sail past the cap guard instead of refusing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# .../claude/scripts/tests/test_switchyard_lane.py -> parents[3] == repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "switchyard-lane.js"

NODE = shutil.which("node")

# The four literals `STORE_EXPECTED` pins, each exactly as switchyard's src/store.ts writes it.
STORE_LITERALS = [
    "const OPEN_ATTEMPT = \"status IN ('dispatched', 'running')\"",
    "a.status = 'interrupted'",
    "e.orphan_attempt = a.number AND e.status = 'open'",
    "c.left_behind = 0",
]

STORE_TS = """\
const OPEN_ATTEMPT = "status IN ('dispatched', 'running')";

const OPEN_OR_ORPHAN = `(a.${OPEN_ATTEMPT}
  OR (a.status = 'interrupted'
      AND EXISTS (SELECT 1 FROM escalations e WHERE e.run_id = a.run_id
                  AND e.orphan_attempt = a.number AND e.status = 'open')))`;

// openAttemptsByRepo's checks branch:
//   WHERE c.left_behind = 0
"""

ADMISSION_TS = """\
export const DEFAULT_LANE_POLICY = {
  wip: { overall: 3, overallOnSubscription: 2, perRepo: 1, repos: Object.fromEntries([]) },
  awaitingMerge: { overall: 6, perRepo: 3 },
};
"""


def run_lane(src_dir, arg="owner/repo"):
    """Run the script against a fixture checkout; returns (exit code, stdout, stderr)."""
    env = dict(os.environ)
    env["SWITCHYARD_SRC"] = str(src_dir)
    # A store path that cannot exist, so a fixture that gets past the guards still never
    # touches a real database even on a machine that has one.
    env["SWITCHYARD_DB"] = str(Path(src_dir) / "no-such-store.db")
    proc = subprocess.run(
        [NODE, str(SCRIPT), arg],
        capture_output=True,
        text=True,
        encoding="utf-8",  # never the cp1252 default -- see CLI Scripting Checklist item 6
        errors="replace",
        env=env,
        timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_fixture(root, store=STORE_TS, admission=ADMISSION_TS):
    src = Path(root) / "src"
    src.mkdir(parents=True, exist_ok=True)
    if store is not None:
        (src / "store.ts").write_text(store, encoding="utf-8")
    if admission is not None:
        (src / "admission.ts").write_text(admission, encoding="utf-8")
    return root


@unittest.skipIf(NODE is None, "node not on PATH")
class TestSwitchyardLaneGuard(unittest.TestCase):
    def test_a_complete_fixture_gets_past_both_guards(self):
        """Known-good: no drift refusal. It stops at the absent better-sqlite3 instead."""
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp)
            code, _out, err = run_lane(tmp)
            self.assertEqual(code, 2, err)
            self.assertIn("better-sqlite3", err)
            self.assertNotIn("no longer matches the predicate", err)
            self.assertNotIn("could not read the caps", err)

    def test_each_missing_predicate_literal_refuses_and_names_itself(self):
        """Known-bad: drop one literal at a time; each must be named in the refusal."""
        for literal in STORE_LITERALS:
            with self.subTest(literal=literal):
                with tempfile.TemporaryDirectory() as tmp:
                    write_fixture(tmp, store=STORE_TS.replace(literal, ""))
                    code, _out, err = run_lane(tmp)
                    self.assertEqual(code, 2, err)
                    self.assertIn("REFUSING TO ANSWER", err)
                    self.assertIn("src/store.ts no longer matches the predicate", err)
                    self.assertIn("not found: " + literal, err)

    def test_a_missing_cap_group_refuses(self):
        for group in ("wip: {", "awaitingMerge: {"):
            with self.subTest(group=group):
                with tempfile.TemporaryDirectory() as tmp:
                    write_fixture(tmp, admission=ADMISSION_TS.replace(group, "removed: ["))
                    code, _out, err = run_lane(tmp)
                    self.assertEqual(code, 2, err)
                    self.assertIn("src/admission.ts no longer has the cap groups", err)
                    self.assertIn("not found: " + group, err)

    def test_an_unreadable_cap_value_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, admission=ADMISSION_TS.replace("perRepo: 1", "perRepo: ONE"))
            code, _out, err = run_lane(tmp)
            self.assertEqual(code, 2, err)
            self.assertIn("could not read the caps", err)

    def test_the_lane_cap_is_not_read_from_the_awaiting_merge_group(self):
        """Regression for the unanchored /perRepo:\\s*(\\d+)/ (dev-env#1406 review).

        `awaitingMerge` is declared first and is the only group with a `perRepo`. An
        unanchored read finds `perRepo: 3` there and sails past the cap guard; an
        anchored one finds none in `wip` and refuses.
        """
        reordered = (
            "export const DEFAULT_LANE_POLICY = {\n"
            "  awaitingMerge: { overall: 6, perRepo: 3 },\n"
            "  wip: { overall: 3, overallOnSubscription: 2, repos: Object.fromEntries([]) },\n"
            "};\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, admission=reordered)
            code, _out, err = run_lane(tmp)
            self.assertEqual(code, 2, err)
            self.assertIn("could not read the caps", err)
            self.assertNotIn("better-sqlite3", err)

    def test_an_absent_source_file_refuses_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(tmp, store=None)
            code, _out, err = run_lane(tmp)
            self.assertEqual(code, 2, err)
            self.assertIn("could not read src/store.ts", err)
            self.assertNotIn("Traceback", err)


@unittest.skipIf(NODE is None, "node not on PATH")
class TestSwitchyardLaneArguments(unittest.TestCase):
    def test_help_prints_usage_and_exits_zero(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                with tempfile.TemporaryDirectory() as tmp:
                    write_fixture(tmp)
                    code, out, _err = run_lane(tmp, arg=flag)
                    self.assertEqual(code, 0)
                    self.assertIn("Usage: node switchyard-lane.js", out)

    def test_an_argument_that_is_not_a_repo_is_refused_not_answered(self):
        """`LANE FREE for --json` is the one output this script must never produce."""
        for arg in ("--json", "lifting-logbook", "owner/repo/extra", ""):
            with self.subTest(arg=arg):
                with tempfile.TemporaryDirectory() as tmp:
                    write_fixture(tmp)
                    code, out, err = run_lane(tmp, arg=arg)
                    self.assertEqual(code, 2, err)
                    self.assertNotIn("LANE FREE", out)
                    if arg:
                        self.assertIn("is not an owner/repo", err)


if __name__ == "__main__":
    unittest.main()
