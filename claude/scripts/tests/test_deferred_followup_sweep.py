#!/usr/bin/env python3
"""Tests for deferred-followup-sweep.py. Fixtures are cut down from the real references in
ADR-153: switchyard PR 13 and #12 (must report), lifting-logbook PR 115 and #106 (must not)."""
import importlib.util
import pathlib
import sys
import unittest

_PATH = pathlib.Path(__file__).resolve().parent.parent / "deferred-followup-sweep.py"
sys.path.insert(0, str(_PATH.parent))
_SPEC = importlib.util.spec_from_file_location("deferred_followup_sweep", _PATH)
sweep_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sweep_module)

SWITCHYARD = "brownm09/switchyard"
LOGBOOK = "merickvaughn/lifting-logbook"

PR_13_BODY = """### Acceptance criteria evidence

Issue: https://github.com/brownm09/switchyard/issues/3

| # | Criterion | Evidence | Status |
|---|---|---|---|
| 1 | Shards get one class, see #99. | `classify.test.ts` | met |
| 2 | The judgment is calibrated, per #98. | Deferred to #12, and #3 is amended to link it. | not verifiable |

<!-- acceptance-criteria: issue=3 outcome=parsed total=2 met=1 not_met=0 not_verifiable=1 -->
"""
PR_21_BODY = "Refs #12. Records two decisions. It doesn't close #12, whose criteria need a live run."
PR_115_BODY = "- [ ] Clicking a completed workout navigates to the workout detail screen — deferred to #106"
PR_132_BODY = "Updates the proposal.\n\nCloses #106"


def issue(repo, number, closed_at=None, reason="COMPLETED", closer=None):
    return {
        "number": number,
        "state": "CLOSED" if closed_at else "OPEN",
        "stateReason": reason if closed_at else None,
        "closedAt": closed_at,
        "repository": {"nameWithOwner": repo},
        "timelineItems": {"nodes": [{"closer": closer}] if closed_at else []},
    }


class ReferenceCases(unittest.TestCase):
    def test_switchyard_13_reports_12(self):
        follow_ups = {
            (SWITCHYARD, 12): issue(SWITCHYARD, 12, "2026-11-05T21:07:39Z", closer={"number": 21, "body": PR_21_BODY})
        }
        prs = [(SWITCHYARD, 13, "2026-11-05T20:32:22Z", PR_13_BODY, [])]
        findings, counts = sweep_module.sweep(prs, follow_ups)
        self.assertEqual(len(findings), 1)
        self.assertIn("brownm09/switchyard#12", findings[0])
        self.assertIn("PR 21", findings[0])
        self.assertEqual(counts["closed_after_merge"], 1)

    def test_lifting_logbook_115_reports_nothing_and_counts_it_as_not_judged(self):
        follow_ups = {
            (LOGBOOK, 106): issue(LOGBOOK, 106, "2026-05-01T04:48:29Z", closer={"number": 132, "body": PR_132_BODY})
        }
        prs = [(LOGBOOK, 115, "2026-04-30T01:47:20Z", PR_115_BODY, [])]
        findings, counts = sweep_module.sweep(prs, follow_ups)
        self.assertEqual(findings, [])
        self.assertEqual(counts, {"deferrals": 1, "open": 0, "closed_after_merge": 1, "reported": 0})


class Extraction(unittest.TestCase):
    def test_reads_the_evidence_cell_only_and_skips_the_block_issue(self):
        self.assertEqual(sweep_module.extract_deferrals(PR_13_BODY, [], SWITCHYARD), {(SWITCHYARD, 12)})

    def test_reads_a_block_in_a_comment_and_a_full_url(self):
        comment = PR_13_BODY.replace("Deferred to #12, and #3", "Deferred to https://github.com/o/r/issues/12, and #3")
        self.assertEqual(sweep_module.extract_deferrals("", [comment], SWITCHYARD), {("o/r", 12)})

    def test_ignores_a_met_row_and_deferred_from(self):
        self.assertEqual(sweep_module.extract_deferrals("Follow-ups deferred from #651.", [], LOGBOOK), set())

    def test_empty_inputs_find_nothing(self):
        self.assertEqual(sweep_module.extract_deferrals(None, [], LOGBOOK), set())


class Closures(unittest.TestCase):
    MERGED = "2026-11-05T00:00:00Z"
    LATER = "2026-11-06T00:00:00Z"

    def test_open_reports_nothing(self):
        self.assertIsNone(sweep_module.closure_finding(issue("o/r", 1), self.MERGED))

    def test_closed_before_merge_reports_nothing(self):
        self.assertIsNone(sweep_module.closure_finding(issue("o/r", 1, "2026-11-04T00:00:00Z", "NOT_PLANNED"), self.MERGED))

    def test_not_planned_reports(self):
        why = sweep_module.closure_finding(issue("o/r", 1, self.LATER, "NOT_PLANNED"), self.MERGED)
        self.assertEqual(why, "closed as not planned")

    def test_closed_by_hand_is_not_judged(self):
        self.assertIsNone(sweep_module.closure_finding(issue("o/r", 1, self.LATER), self.MERGED))

    def test_negation_must_name_this_issue_right_after_the_keyword(self):
        says = sweep_module.says_it_does_not_close
        self.assertTrue(says("It does not close #468 yet.", 468))
        self.assertTrue(says("This PR doesn't fix brownm09/switchyard#12", 12))
        self.assertFalse(says("It doesn't close #120", 12))
        self.assertFalse(says("Tests do not fail.\n\nCloses #12", 12))
        self.assertFalse(says("A failure doesn't block closing #388", 388))
        self.assertFalse(says(None, 12))

    def test_closed_since_excludes_an_older_closure(self):
        follow_ups = {("o/r", 12): issue("o/r", 12, self.LATER, "NOT_PLANNED")}
        prs = [("o/r", 13, self.MERGED, "Deferred to #12", [])]
        self.assertEqual(sweep_module.sweep(prs, follow_ups, "2026-11-07")[1]["closed_after_merge"], 0)
        self.assertEqual(len(sweep_module.sweep(prs, follow_ups, "2026-11-06")[0]), 1)


if __name__ == "__main__":
    unittest.main()
