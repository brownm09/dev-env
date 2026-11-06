#!/usr/bin/env python3
"""Calibrate the duplicate-issue retrieval experiment's scorer (dev-env#1260).

Known-good and known-bad rankers must land where the arithmetic says, the frozen outcome map must
be able to reach every verdict, and the committed evaluation set must be non-empty and hold no text
from a private repo. Standard library only; the embedding stack isn't needed here.
"""
import json
import os
import pathlib
import random
import sys
import unittest

EXPERIMENT = pathlib.Path(__file__).resolve().parents[3] / "experiments" / "2026-11-issue-dup-retrieval"
sys.path.insert(0, str(EXPERIMENT))

import dup_eval as de  # noqa: E402


def load_labels():
    with open(EXPERIMENT / "labels.json", encoding="utf-8") as f:
        return json.load(f)


class Calibration(unittest.TestCase):
    """The hit-test is the verdict's instrument, so it is scored against known rankers first."""

    def setUp(self):
        self.inputs = load_labels()["inputs"]

    def test_known_good_ranker_scores_one(self):
        runs = [{"ranked": i["targets"] + [99999], "targets": i["targets"]} for i in self.inputs]
        self.assertEqual(de.recall_at(runs, de.K_PRIMARY), 1.0)

    def test_known_bad_ranker_scores_zero(self):
        runs = [{"ranked": [99998, 99999], "targets": i["targets"]} for i in self.inputs]
        self.assertEqual(de.recall_at(runs, de.K_PRIMARY), 0.0)

    def test_target_below_the_budget_is_a_miss(self):
        ranked = list(range(1000, 1000 + de.K_PRIMARY)) + [7]
        self.assertFalse(de.hit_at(ranked, [7], de.K_PRIMARY))
        self.assertTrue(de.hit_at(ranked, [7], de.K_PRIMARY + 1))

    def test_random_ranker_lands_near_chance(self):
        rng = random.Random(1)
        pool = list(range(1, 501))
        runs = [{"ranked": rng.sample(pool, 500), "targets": [rng.choice(pool)]} for _ in range(2000)]
        self.assertAlmostEqual(de.recall_at(runs, 10), 10 / 500, delta=0.012)

    def test_empty_run_raises_rather_than_scoring(self):
        with self.assertRaises(ValueError):
            de.recall_at([], 10)
        with self.assertRaises(ValueError):
            de.majority([])


class OutcomeMap(unittest.TestCase):
    """The design has to be able to produce the opposite conclusion."""

    def test_supported(self):
        self.assertEqual(de.decide(0.20, 0.01, 0.0, 0.20)[0], "supported")

    def test_refuted(self):
        self.assertEqual(de.decide(0.05, 0.5, 0.0, 0.0)[0], "refuted")
        self.assertEqual(de.decide(-0.30, 1.0, -0.4, -0.3)[0], "refuted")

    def test_between_the_lines_is_inconclusive(self):
        self.assertEqual(de.decide(0.10, 0.01, 0.0, 0.10)[0], "inconclusive")

    def test_clearing_the_bar_without_the_sign_test_is_inconclusive(self):
        self.assertEqual(de.decide(0.20, 0.06, 0.0, 0.20)[0], "inconclusive")

    def test_stratum_b_loss_blocks_a_pooled_win(self):
        self.assertEqual(de.decide(0.20, 0.01, -0.16, 0.20)[0], "inconclusive")

    def test_scope_disagreement_is_inconclusive_in_both_directions(self):
        self.assertEqual(de.decide(0.20, 0.01, 0.0, 0.05)[0], "inconclusive")
        self.assertEqual(de.decide(0.0, 0.5, 0.0, 0.20)[0], "inconclusive")


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


class EvaluationSet(unittest.TestCase):
    def test_counts_are_non_vacuous(self):
        labels = load_labels()
        strata = [i["stratum"] for i in labels["inputs"]]
        self.assertGreaterEqual(strata.count("A"), 3)
        self.assertGreaterEqual(strata.count("B"), 1)
        self.assertGreaterEqual(len(labels["known_negative"]), 1)
        self.assertGreaterEqual(len(labels["easy_negative"]), 1)

    def test_targets_are_filed_before_a_stratum_a_query(self):
        for item in load_labels()["inputs"]:
            self.assertTrue(item["targets"], item["id"])
            if item["stratum"] == "A":
                self.assertNotIn(item["query_issue"], item["targets"])


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

    def test_labels_hold_only_numbers_and_structure(self):
        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertLess(len(str(key)), 30)
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
            elif isinstance(node, str):
                self.assertLess(len(node), 45, node)

        walk(load_labels())

    def test_committed_files_hold_no_private_title(self):
        """Runs where the local issue cache exists; CI can't read the private repos. The result of
        the local run is recorded in results.md."""
        cache = os.environ.get("DUP_EVAL_ISSUE_CACHE")
        if not cache:
            return
        titles = []
        for repo in self.PRIVATE_REPOS:
            with open(f"{cache}/issues_{repo}.json", encoding="utf-8") as f:
                titles += [i["title"] for i in json.load(f)]
        self.assertGreater(len(titles), 100, "an empty title list would pass vacuously")
        for path in EXPERIMENT.iterdir():
            if path.is_file() and path.suffix in {".md", ".json", ".jsonl", ".txt"}:
                self.assertEqual(de.privacy_scan(path.read_text(encoding="utf-8"), titles), [], path.name)


if __name__ == "__main__":
    unittest.main()
