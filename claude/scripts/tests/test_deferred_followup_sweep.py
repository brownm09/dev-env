#!/usr/bin/env python3
"""Tests for deferred-followup-sweep.py. Fixtures are cut down from the real references in
ADR-153: switchyard PR 13 and #12 (must report), lifting-logbook PR 115 and #106 (must not)."""
import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

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
        "__typename": "Issue",
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
        self.assertEqual(counts["from_evidence"], 1)

    def test_lifting_logbook_115_reports_nothing_and_counts_it_as_not_judged(self):
        follow_ups = {
            (LOGBOOK, 106): issue(LOGBOOK, 106, "2026-05-01T04:48:29Z", closer={"number": 132, "body": PR_132_BODY})
        }
        prs = [(LOGBOOK, 115, "2026-04-30T01:47:20Z", PR_115_BODY, [])]
        findings, counts = sweep_module.sweep(prs, follow_ups)
        self.assertEqual(findings, [])
        self.assertEqual(
            counts, {"deferrals": 1, "from_evidence": 0, "open": 0, "closed_after_merge": 1, "reported": 0}
        )


class Extraction(unittest.TestCase):
    def extract(self, body, comments=(), repo="me/mine"):
        return sweep_module.extract_deferrals(body, list(comments), repo)

    def test_reads_the_evidence_cell_only_and_skips_the_block_issue(self):
        self.assertEqual(self.extract(PR_13_BODY, repo=SWITCHYARD), {(SWITCHYARD, 12)})

    def test_reads_a_block_in_a_comment_and_a_full_url(self):
        comment = PR_13_BODY.replace("Deferred to #12, and #3", "Deferred to https://github.com/o/r/issues/12, and #3")
        self.assertEqual(self.extract("", [comment], SWITCHYARD), {("o/r", 12)})

    def test_ignores_deferred_from(self):
        self.assertEqual(self.extract("Follow-ups deferred from #651.", repo=LOGBOOK), set())

    def test_empty_inputs_find_nothing(self):
        self.assertEqual(sweep_module.extract_deferrals(None, [], LOGBOOK), set())

    def test_a_not_verifiable_cell_with_only_context_is_not_a_deferral(self):
        body = PR_13_BODY.replace("Deferred to #12, and #3 is amended to link it.", "Needs a live run; context in #44.")
        self.assertEqual(self.extract(body, repo=SWITCHYARD), set())

    def test_a_later_review_of_the_same_issue_replaces_the_earlier_one(self):
        rescored = PR_13_BODY.replace("| not verifiable |", "| met |")
        self.assertEqual(self.extract(PR_13_BODY, [rescored], SWITCHYARD), set())

    def test_prose_takes_only_references_after_the_deferral_word(self):
        self.assertEqual(self.extract("Closes #5; criterion 3 deferred to #6 and #7."), {("me/mine", 6), ("me/mine", 7)})

    def test_prose_leaves_out_this_prs_own_closing_issue(self):
        self.assertEqual(self.extract("Closes #5 (nothing deferred)"), set())
        self.assertEqual(self.extract("Nothing deferred; closes #5"), set())
        self.assertEqual(self.extract("Closes #5\n\nPart of #5 is deferred"), set())

    def test_prose_keeps_a_deferral_to_an_issue_it_says_it_does_not_close(self):
        self.assertEqual(self.extract("This doesn't close #12. Deferred to #12."), {("me/mine", 12)})

    def test_short_form_resolves_against_the_repo_owner(self):
        self.assertEqual(self.extract("Criterion 3 deferred to dev-env#12"), {("me/dev-env", 12)})

    def test_link_text_is_not_a_second_reference(self):
        self.assertEqual(self.extract("deferred to [#12](https://github.com/other/repo/issues/12)"), {("other/repo", 12)})

    def test_a_full_reference_before_the_deferral_word_keeps_its_repo(self):
        self.assertEqual(self.extract("o/r#12 is deferred"), {("o/r", 12)})


class Negation(unittest.TestCase):
    def says(self, body, number=12, repo="me/mine"):
        return sweep_module.says_it_does_not_close(body, number, repo)

    def test_reports_the_negated_forms_github_still_acts_on(self):
        for body in (
            "It does not close #12 yet.",
            "This PR doesn't fix me/mine#12",
            "It doesn’t close #12",
            "It does **not** close #12",
            "This cannot close #12 yet",
            "It doesn't fully resolve #12",
            "It does not actually fix #12",
            "It doesn't close: #12",
            "It doesn't close https://github.com/me/mine/issues/12",
            "It doesn't close [#12](https://github.com/me/mine/issues/12)",
            "It does not\nclose #12",
            "It does not\r\nclose #12",
            "**Not** `Closes #12` by design",
            "It doesn't close #12.\n\n| 1 | A finding | fixed: #12 is amended |",
        ):
            with self.subTest(body):
                self.assertTrue(self.says(body))

    def test_does_not_report_a_body_that_really_closes_the_issue(self):
        for body in (
            "It doesn't close #120",
            "Tests do not fail.\n\nCloses #12",
            "Tests do not fail.\r\n\r\nCloses #12",
            "A failure doesn't block closing #12",
            "The earlier PR did not fix #12. This one does.\n\nCloses #12",
            "If this doesn't fix #12, reopen it.\n\nFixes #12",
            "Closes #12. It does not close other/repo#12",
            "Works whether the flag is set or not\n\nCloses #12",
            "It not only fixes #12",
            "Refs #12",
            None,
        ):
            with self.subTest(body):
                self.assertFalse(self.says(body))

    def test_a_renamed_owner_is_still_the_same_repo(self):
        self.assertTrue(self.says("It doesn't close brownm09/mine#12"))


class Closures(unittest.TestCase):
    MERGED = "2026-11-05T00:00:00Z"
    LATER = "2026-11-06T00:00:00Z"

    def counts(self, follow_up, closed_since=""):
        prs = [("o/r", 13, self.MERGED, "Deferred to #12", [])]
        return sweep_module.sweep(prs, {("o/r", 12): follow_up}, closed_since)

    def test_open_reports_nothing(self):
        findings, counts = self.counts(issue("o/r", 12))
        self.assertEqual((findings, counts["open"]), ([], 1))

    def test_closed_before_merge_reports_nothing(self):
        findings, counts = self.counts(issue("o/r", 12, "2026-11-04T00:00:00Z", "NOT_PLANNED"))
        self.assertEqual((findings, counts["closed_after_merge"]), ([], 0))

    def test_not_planned_and_duplicate_report(self):
        self.assertEqual(sweep_module.closure_finding(issue("o/r", 1, self.LATER, "NOT_PLANNED")), "closed as not planned")
        self.assertEqual(sweep_module.closure_finding(issue("o/r", 1, self.LATER, "DUPLICATE")), "closed as duplicate")

    def test_closed_by_hand_or_with_no_reason_is_not_judged(self):
        self.assertIsNone(sweep_module.closure_finding(issue("o/r", 1, self.LATER)))
        self.assertIsNone(sweep_module.closure_finding(issue("o/r", 1, self.LATER, None)))

    def test_closed_since_excludes_an_older_closure(self):
        follow_up = issue("o/r", 12, self.LATER, "NOT_PLANNED")
        self.assertEqual(self.counts(follow_up, "2026-11-07")[1]["closed_after_merge"], 0)
        self.assertEqual(len(self.counts(follow_up, "2026-11-06")[0]), 1)

    def test_two_names_for_one_issue_count_once(self):
        follow_up = issue("new/name", 12, self.LATER, "NOT_PLANNED")
        prs = [("o/r", 13, self.MERGED, "Deferred to old/name#12 and new/name#12", [])]
        findings, counts = sweep_module.sweep(prs, {("old/name", 12): follow_up, ("new/name", 12): follow_up})
        self.assertEqual(len(findings), 1)
        self.assertEqual((counts["deferrals"], counts["closed_after_merge"], counts["reported"]), (1, 1, 1))


def gh_result(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout.encode("utf-8"), stderr.encode("utf-8"))


class FailedReads(unittest.TestCase):
    def follow_ups(self, *results):
        failures = {}
        with mock.patch.object(sweep_module.subprocess, "run", side_effect=results):
            found, unresolved = sweep_module.fetch_follow_ups({("o/r", 12)}, failures)
        return found, unresolved, failures

    def test_a_number_that_does_not_exist_is_unresolved(self):
        payload = {"data": {"r": {"n12": None}}, "errors": [{"type": "NOT_FOUND", "path": ["r", "n12"]}]}
        self.assertEqual(self.follow_ups(gh_result(json.dumps(payload), 1)), ({}, 1, {}))

    def test_a_repo_that_does_not_exist_is_unresolved_and_not_fatal(self):
        payload = {"data": {"r": None}, "errors": [{"type": "NOT_FOUND", "path": ["r"]}]}
        self.assertEqual(self.follow_ups(gh_result(json.dumps(payload), 1)), ({}, 1, {}))

    def test_a_partial_failure_of_another_type_is_a_failed_read(self):
        payload = {"data": {"r": {"n12": None}}, "errors": [{"type": "FORBIDDEN", "message": "no", "path": ["r", "n12"]}]}
        self.assertIn("no", self.follow_ups(gh_result(json.dumps(payload), 1))[2]["o/r"])

    def test_a_failure_with_no_data_is_a_failed_read(self):
        self.assertIn("HTTP 502", self.follow_ups(gh_result("", 1, "HTTP 502"))[2]["o/r"])

    def test_a_timeout_or_missing_gh_is_a_failed_read(self):
        self.assertIn("timed out", self.follow_ups(subprocess.TimeoutExpired("gh", 1))[2]["o/r"])
        self.assertIn("PATH", self.follow_ups(FileNotFoundError())[2]["o/r"])

    def test_a_pull_request_reference_is_neither_found_nor_unresolved(self):
        payload = {"data": {"r": {"n12": {"__typename": "PullRequest"}}}}
        self.assertEqual(self.follow_ups(gh_result(json.dumps(payload))), ({}, 0, {}))

    def fetch_prs(self, *results, repos=("a/b",)):
        failures = {}
        args = argparse.Namespace(pr=[], repo=list(repos))
        with mock.patch.object(sweep_module.subprocess, "run", side_effect=results):
            return sweep_module.fetch_prs(args, failures), failures

    def test_one_repos_failure_keeps_the_others(self):
        pr = {"number": 1, "mergedAt": "2026-01-01T00:00:00Z", "body": "", "comments": []}
        prs, failures = self.fetch_prs(gh_result("", 1, "bad"), gh_result(json.dumps([pr])), repos=("a/b", "c/d"))
        self.assertEqual([p[:2] for p in prs], [("c/d", 1)])
        self.assertEqual(list(failures), ["a/b"])

    def test_reaching_the_pr_limit_is_a_failed_read(self):
        pr = {"number": 1, "mergedAt": "2026-01-01T00:00:00Z", "body": "", "comments": []}
        with mock.patch.object(sweep_module, "PR_LIST_LIMIT", 1):
            self.assertIn("limit", self.fetch_prs(gh_result(json.dumps([pr])))[1]["a/b"])


class Main(unittest.TestCase):
    def run_main(self, *argv, prs=(), follow_ups=None, failures=None):
        def fake_prs(_args, sink):
            sink.update(failures or {})
            return list(prs)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["sweep", *argv]), mock.patch.object(sweep_module, "fetch_prs", fake_prs), mock.patch.object(
            sweep_module, "fetch_follow_ups", lambda refs, sink: (follow_ups or {}, 0)
        ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sweep_module.main()
        return code, out.getvalue(), err.getvalue()

    def test_zero_deferrals_warns(self):
        code, out, _ = self.run_main("--repo", "o/r")
        self.assertEqual(code, 0)
        self.assertIn("WARNING: no deferral was found", out)

    def test_a_found_deferral_does_not_warn(self):
        prs = [("o/r", 13, "2026-11-05T00:00:00Z", "Deferred to #12", [])]
        code, out, _ = self.run_main("--repo", "o/r", prs=prs, follow_ups={("o/r", 12): issue("o/r", 12)})
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING", out)
        self.assertIn("Deferrals found: 1 (0 from evidence tables), open: 1.", out)

    def test_a_failed_read_exits_1_and_still_prints_the_summary(self):
        code, out, err = self.run_main("--repo", "o/r", failures={"o/r": "boom"})
        self.assertEqual(code, 1)
        self.assertIn("PRs read: 0", out)
        self.assertIn("TOOL ERROR: o/r: boom", err)

    def test_closed_since_rejects_anything_but_a_date(self):
        for value in ("", "11/01/2026", "Nov 1 2026"):
            with self.subTest(value), self.assertRaises(SystemExit) as stop:
                self.run_main("--repo", "o/r", "--closed-since", value)
            self.assertEqual(stop.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
