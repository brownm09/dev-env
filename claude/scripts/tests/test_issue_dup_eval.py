#!/usr/bin/env python3
"""Calibrate the duplicate-issue retrieval experiment's scorer (dev-env#1260).

Known-good and known-bad rankers go through score.py's real path, the frozen outcome map must reach
every verdict and hold at each threshold, the committed result must reproduce from committed data,
and committed files must hold no text from a private repo. Standard library only; the embedding
stack isn't needed here.
"""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXPERIMENT = REPO_ROOT / "experiments" / "2026-11-issue-dup-retrieval"
ADR = REPO_ROOT / "docs" / "adr" / "157-duplicate-issue-retrieval-experiment.md"
sys.path.insert(0, str(EXPERIMENT))

import dup_eval as de  # noqa: E402
import keyword_arm  # noqa: E402
import retry_round  # noqa: E402
import score  # noqa: E402


def load_json(name):
    with open(EXPERIMENT / name, encoding="utf-8") as f:
        return json.load(f)


FIXTURE_INPUTS = [
    {"id": "A:r#5", "stratum": "A", "repo": "r", "query_issue": 5, "created_before": "t", "targets": [1]},
    {"id": "A:r#6", "stratum": "A", "repo": "r", "query_issue": 6, "created_before": "t", "targets": [2]},
    {"id": "B:r#3", "stratum": "B", "repo": "r", "host_issue": 3, "created_before": "t", "targets": [3]},
]


def run_score(vector_ranks, keyword_lists, generations=("g1", "g2", "g3")):
    """Run score.main on fixture files through its real path, and return its summary."""
    keyword = {
        g: {
            i["id"]: {"parity": keyword_lists[i["id"]], "default": keyword_lists[i["id"]]}
            for i in FIXTURE_INPUTS
        }
        for g in generations
    }
    return run_score_files(vector_ranks, keyword)


def run_score_files(vector_ranks, keyword):
    vector = {
        "ranks": vector_ranks,
        "pairs": {
            "duplicate": [["r", 1, 5, 0.9]],
            "known_negative": [["r", 7, 8, 0.5]],
            "easy_negative": [["r", 7, 9, 0.2]],
            "auditor_near_dup": [["r", 7, 10, 0.6]],
        },
        "cross_repo": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for name, data in (("labels", {"inputs": FIXTURE_INPUTS}), ("vector", vector), ("keyword", keyword)):
            paths[name] = os.path.join(tmp, f"{name}.json")
            with open(paths[name], "w", encoding="utf-8") as f:
                json.dump(data, f)
        out = os.path.join(tmp, "scored.json")
        with contextlib.redirect_stdout(io.StringIO()):
            score.main(paths["labels"], paths["vector"], paths["keyword"], out)
        with open(out, encoding="utf-8") as f:
            return json.load(f)["summary"]


GOOD_RANKS = {i["id"]: i["targets"] + [999] for i in FIXTURE_INPUTS}
BAD_RANKS = {i["id"]: [998, 999] for i in FIXTURE_INPUTS}
GOOD_LISTS = {i["id"]: [i["targets"] + [999]] for i in FIXTURE_INPUTS}
BAD_LISTS = {i["id"]: [[], [998]] for i in FIXTURE_INPUTS}


class ScorePathCalibration(unittest.TestCase):
    """The verdict's instrument, calibrated through the path that produces it."""

    def test_known_good_rankers_score_one(self):
        recall = run_score(GOOD_RANKS, GOOD_LISTS)["recall_at_10"]["pooled"]
        self.assertEqual(recall, {"vector": 1.0, "keyword": 1.0})

    def test_known_bad_rankers_score_zero(self):
        recall = run_score(BAD_RANKS, BAD_LISTS)["recall_at_10"]["pooled"]
        self.assertEqual(recall, {"vector": 0.0, "keyword": 0.0})

    def test_good_vector_against_bad_keyword_counts_every_family(self):
        summary = run_score(GOOD_RANKS, BAD_LISTS)
        self.assertEqual(summary["gap_pooled"], 1.0)
        self.assertEqual(summary["family_sign_test"]["wins"], 3)
        # 3 wins out of 3 is p = 0.125, so a large gain on too few families stays inconclusive.
        self.assertEqual(summary["verdict"], "inconclusive")

    def test_equal_arms_read_as_refuted(self):
        self.assertEqual(run_score(GOOD_RANKS, GOOD_LISTS)["verdict"], "refuted")

    def test_partial_keyword_file_is_rejected(self):
        with self.assertRaises(SystemExit):
            run_score(GOOD_RANKS, GOOD_LISTS, generations=("g1", "g2"))

    def test_target_below_the_budget_is_a_miss(self):
        ranked = list(range(1000, 1000 + de.K_PRIMARY)) + [7]
        self.assertFalse(de.hit_at(ranked, [7], de.K_PRIMARY))
        self.assertTrue(de.hit_at(ranked, [7], de.K_PRIMARY + 1))

    def test_no_generations_raises_rather_than_scoring(self):
        with self.assertRaises(ValueError):
            de.majority([])


class RetryPathCalibration(unittest.TestCase):
    """The re-run's verdict path: a retry round, merged, then score.py's real path (dev-env#1282)."""

    INPUTS = {i["id"]: i for i in FIXTURE_INPUTS}

    def run_round(self, replacement_hits):
        """Every first-round query returns nothing, and the replacement returns the target or not."""
        round1 = {
            g: {i["id"]: {"parity": [[], []], "default": [[], []]} for i in FIXTURE_INPUTS}
            for g in ("g1", "g2", "g3")
        }
        final = {}
        for g, by_input in round1.items():
            final[g] = {}
            for iid, per_scope in by_input.items():
                results = self.INPUTS[iid]["targets"] if replacement_hits else [999]
                final[g][iid] = retry_round.merge_round(per_scope, {0: {"parity": results, "default": results}})
        return run_score_files(GOOD_RANKS, final)

    def test_known_good_replacement_scores_one(self):
        self.assertEqual(self.run_round(True)["recall_at_10"]["pooled"]["keyword"], 1.0)

    def test_known_bad_replacement_scores_zero(self):
        self.assertEqual(self.run_round(False)["recall_at_10"]["pooled"]["keyword"], 0.0)

    def test_merge_fills_only_the_named_slot_and_keeps_default_results(self):
        merged = retry_round.merge_round({"parity": [[1], []], "default": [[1], [2]]}, {1: {"parity": [3]}})
        self.assertEqual(merged, {"parity": [[1], [3]], "default": [[1], [2]]})
        merged = retry_round.merge_round({"parity": [[]], "default": [[]]}, {0: {"parity": [3], "default": [4]}})
        self.assertEqual(merged, {"parity": [[3]], "default": [[4]]})

    def test_merge_leaves_the_round_one_lists_alone(self):
        original = {"parity": [[]]}
        retry_round.merge_round(original, {0: {"parity": [3]}})
        self.assertEqual(original, {"parity": [[]]})

    def test_writer_sees_no_results_labels_or_targets(self):
        text = retry_round.retry_text("d07", "dev-env", "Draft text", ["kept words", "empty words"], [[610, 734], []])
        self.assertIn("kept words -> returned results", text)
        self.assertIn("empty words -> returned nothing", text)
        for forbidden in ("610", "734", "target", "stratum", "A:dev-env", "created_before"):
            self.assertNotIn(forbidden, text)

    def test_nothing_to_retry_writes_no_section(self):
        self.assertIsNone(retry_round.retry_text("d07", "dev-env", "Draft", ["q"], [[1]]))

    def test_replacement_count_must_match_empty_queries(self):
        for replacements, empty in ((["a", "b"], 1), (["a"], 2), ([], 1)):
            with self.assertRaises(SystemExit):
                retry_round.check_replacements("d07", replacements, empty)

    def test_blank_or_qualified_replacement_is_rejected(self):
        for query in ("  ", "hook in:title", "hook label:bug"):
            with self.assertRaises(SystemExit):
                retry_round.check_replacements("d07", [query], 1)

    def test_generation_check_rejects_unknown_ids_and_dropped_drafts(self):
        round1 = {"A:r#5": {"parity": [[], [1]]}, "A:r#6": {"parity": [[2]]}}
        reverse = {"A:r#5": "d00", "A:r#6": "d01"}
        retry_round.check_all_replacements({"d00": ["words"]}, reverse, round1)
        for raw in ({}, {"d00": ["words"], "d99": ["words"]}):
            with self.assertRaises(SystemExit):
                retry_round.check_all_replacements(raw, reverse, round1)

    def test_failed_search_raises_and_never_reads_as_empty(self):
        failed = mock.Mock(returncode=1, stderr=b"HTTP 502", stdout=b"")
        with mock.patch.object(keyword_arm.subprocess, "run", return_value=failed), mock.patch.object(keyword_arm.time, "sleep"):
            with self.assertRaises(SystemExit):
                keyword_arm.search("dev-env", "words", "parity", "t")

    def test_sanitizer_removes_the_phrase_that_selects_stratum_b(self):
        self.assertEqual(de.sanitize_query("Finding\nAdded here rather than opening a new issue.\nMore"), "Finding\nMore")

    def test_evaluation_set_keeps_the_frozen_inputs_and_adds_fresh_ones_apart(self):
        frozen = load_json("labels.json")["inputs"]
        merged = load_json("rerun/labels_all.json")["inputs"]
        self.assertEqual([i for i in merged if i in frozen], frozen)
        fresh = [i for i in merged if i not in frozen]
        self.assertTrue(fresh, "the re-run registered at least one fresh input")
        self.assertTrue(all(i["created_before"] > "2026-11-06" for i in fresh))


class CommittedResult(unittest.TestCase):
    def test_score_reproduces_the_committed_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "scored.json")
            with contextlib.redirect_stdout(io.StringIO()):
                score.main(
                    str(EXPERIMENT / "labels.json"),
                    str(EXPERIMENT / "vector_results.json"),
                    str(EXPERIMENT / "keyword_results.json"),
                    out,
                )
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f), load_json("scored.json"))

    def test_frozen_map_reading_is_pinned(self):
        # The audit's threat sweep overrides this reading: see results.md.
        self.assertEqual(load_json("scored.json")["summary"]["verdict"], "supported")


class OutcomeMap(unittest.TestCase):
    """The design has to be able to produce the opposite conclusion."""

    def test_supported(self):
        self.assertEqual(de.decide(0.20, 0.01, 0.0, 0.20)[0], "supported")

    def test_refuted(self):
        self.assertEqual(de.decide(0.05, 0.5, 0.0, 0.0)[0], "refuted")
        self.assertEqual(de.decide(-0.30, 1.0, -0.4, -0.3)[0], "refuted")

    def test_between_the_lines_is_inconclusive(self):
        self.assertEqual(de.decide(0.10, 0.01, 0.0, 0.10)[0], "inconclusive")

    def test_scope_disagreement_is_inconclusive_in_both_directions(self):
        self.assertEqual(de.decide(0.20, 0.01, 0.0, 0.05)[0], "inconclusive")
        self.assertEqual(de.decide(0.0, 0.5, 0.0, 0.20)[0], "inconclusive")

    def test_no_gain_line_is_inclusive(self):
        self.assertEqual(de.decide(0.05, 0.5, 0.0, 0.15)[0], "refuted")
        self.assertEqual(de.decide(0.0501, 0.5, 0.0, 0.15)[0], "inconclusive")

    def test_default_scope_line_on_the_no_gain_branch(self):
        self.assertEqual(de.decide(0.05, 0.5, 0.0, 0.1501)[0], "inconclusive")

    def test_win_bar_is_inclusive(self):
        self.assertEqual(de.decide(0.15, 0.01, 0.0, 0.15)[0], "supported")
        self.assertEqual(de.decide(0.1499, 0.01, 0.0, 0.15)[0], "inconclusive")

    def test_significance_line_is_inclusive(self):
        self.assertEqual(de.decide(0.20, 0.05, 0.0, 0.20)[0], "supported")
        self.assertEqual(de.decide(0.20, 0.0501, 0.0, 0.20)[0], "inconclusive")

    def test_stratum_b_guard_is_inclusive(self):
        self.assertEqual(de.decide(0.20, 0.01, -0.15, 0.20)[0], "supported")
        self.assertEqual(de.decide(0.20, 0.01, -0.1501, 0.20)[0], "inconclusive")

    def test_default_scope_line_on_the_win_branch(self):
        self.assertEqual(de.decide(0.20, 0.01, 0.0, 0.05)[0], "inconclusive")
        self.assertEqual(de.decide(0.20, 0.01, 0.0, 0.0501)[0], "supported")


class Helpers(unittest.TestCase):
    def test_round_robin_alternates_and_dedupes(self):
        self.assertEqual(de.round_robin([[1, 2, 3], [4, 1], []]), [1, 4, 2, 3])
        self.assertEqual(de.round_robin([]), [])

    def test_sign_test_exact_values(self):
        self.assertEqual(de.sign_test_one_sided(0, 0), 1.0)
        self.assertAlmostEqual(de.sign_test_one_sided(5, 0), 1 / 32)
        self.assertAlmostEqual(de.sign_test_one_sided(5, 1), 7 / 64)

    def test_family_discordance_counts_a_family_once(self):
        rows = [
            {"cluster": "x", "v_hit": True, "k_hit": False},
            {"cluster": "x", "v_hit": True, "k_hit": False},
            {"cluster": "y", "v_hit": False, "k_hit": True},
            {"cluster": "z", "v_hit": True, "k_hit": True},
        ]
        self.assertEqual(de.cluster_discordance(rows), (1, 1))

    def test_sanitize_removes_references_and_declarations(self):
        text = (
            "Hook fails on a clean tree\n"
            "Duplicate of #610, filed earlier.\n"
            "See dev-env#627 and https://github.com/brownm09/dev-env/issues/734 for context, also #12.\n"
            "The guard reads config"
        )
        out = de.sanitize_query(text)
        self.assertNotIn("610", out)
        self.assertNotIn("627", out)
        self.assertNotIn("734", out)
        self.assertNotIn("#12", out)
        self.assertIn("Hook fails on a clean tree", out)
        self.assertIn("The guard reads config", out)

    def test_draft_text_for_each_stratum(self):
        repos = {"r": {
            5: {"title": "Title", "body": "Body see #4", "comments": {"nodes": []}},
            3: {"title": "Host", "body": "", "comments": {"nodes": [{"createdAt": "t", "body": "Finding"}]}},
        }}
        a = {"id": "A", "stratum": "A", "repo": "r", "query_issue": 5}
        b = {"id": "B", "stratum": "B", "repo": "r", "host_issue": 3, "comment_created_at": "t"}
        self.assertEqual(de.draft_text(a, repos), "Title\n\nBody see")
        self.assertEqual(de.draft_text(b, repos), "Finding")

    def test_empty_or_missing_draft_raises(self):
        repos = {"r": {3: {"title": "Host", "body": "", "comments": {"nodes": [
            {"createdAt": "t", "body": "Duplicate of #1"},
        ]}}}}
        empty = {"id": "B", "stratum": "B", "repo": "r", "host_issue": 3, "comment_created_at": "t"}
        missing = dict(empty, comment_created_at="other")
        with self.assertRaises(SystemExit):
            de.draft_text(empty, repos)
        with self.assertRaises(SystemExit):
            de.draft_text(missing, repos)


class EvaluationSet(unittest.TestCase):
    def test_counts_are_non_vacuous(self):
        labels = load_json("labels.json")
        strata = [i["stratum"] for i in labels["inputs"]]
        self.assertGreaterEqual(strata.count("A"), 3)
        self.assertGreaterEqual(strata.count("B"), 1)
        self.assertGreaterEqual(len(labels["known_negative"]), 1)
        self.assertGreaterEqual(len(labels["easy_negative"]), 1)

    def test_targets_are_numbered_before_a_stratum_a_query(self):
        """Issue numbers rise with filing order within a repo."""
        stratum_a = [i for i in load_json("labels.json")["inputs"] if i["stratum"] == "A"]
        self.assertTrue(stratum_a)
        for item in stratum_a:
            self.assertTrue(item["targets"], item["id"])
            for target in item["targets"]:
                self.assertLess(target, item["query_issue"], item["id"])


class Privacy(unittest.TestCase):
    """dev-env is public. Committed experiment files must not carry private issue text."""

    PRIVATE_REPOS = ("career-playbook", "cover-letter-runtime")

    def test_known_bad_fixture_is_caught(self):
        titles = ["Reject the widget when the frobnicator is stale", "short"]
        leaked = "| 3 | Reject the Widget when the frobnicator is stale | 0.81 |"
        self.assertEqual(de.privacy_scan(leaked, titles), [titles[0]])

    def test_known_good_fixture_passes(self):
        titles = ["Reject the widget when the frobnicator is stale", "short"]
        self.assertEqual(de.privacy_scan("| 3 | career-playbook#1575 | short rank | 0.81 |", titles), [])

    def _assert_short_strings(self, node, limit):
        if isinstance(node, dict):
            for key, value in node.items():
                self.assertLess(len(str(key)), 40)
                self._assert_short_strings(value, limit)
        elif isinstance(node, list):
            for value in node:
                self._assert_short_strings(value, limit)
        elif isinstance(node, str):
            self.assertLess(len(node), limit, node)

    def test_data_files_hold_only_numbers_and_short_structure(self):
        """Issue text can't pass this. Limits: IDs and timestamps fit under 45, and scored.json also
        holds one fixed reason sentence from dup_eval.decide."""
        for name, limit in (("labels.json", 45), ("keyword_results.json", 45), ("vector_results.json", 45), ("scored.json", 110)):
            self._assert_short_strings(load_json(name), limit)
        rerun = [p for p in (EXPERIMENT / "rerun").iterdir() if p.suffix == ".json"]
        self.assertTrue(rerun, "an empty file list would pass vacuously")
        for path in rerun:
            self._assert_short_strings(load_json(f"rerun/{path.name}"), 110)

    def test_committed_files_hold_no_private_title(self):
        """Needs the local issue cache, which CI can't have, because the repos are private. This
        checks full verbatim titles only. The local result is recorded in results.md."""
        cache = os.environ.get("DUP_EVAL_ISSUE_CACHE")
        if not cache:
            self.skipTest("DUP_EVAL_ISSUE_CACHE not set")
        titles = []
        for repo in self.PRIVATE_REPOS:
            with open(f"{cache}/issues_{repo}.json", encoding="utf-8") as f:
                titles += [i["title"] for i in json.load(f)]
        self.assertGreater(len(titles), 100, "an empty title list would pass vacuously")
        paths = [p for p in EXPERIMENT.rglob("*") if p.is_file() and p.suffix in {".md", ".json", ".jsonl", ".txt", ".py"}]
        self.assertGreater(len(paths), 5, "an empty file list would pass vacuously")
        for path in paths + [ADR]:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                # Decode first: json.dump escapes non-ASCII, which would hide a title.
                text = json.dumps(json.loads(text), ensure_ascii=False)
            self.assertEqual(de.privacy_scan(text, titles), [], path.name)


if __name__ == "__main__":
    unittest.main()
