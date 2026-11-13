#!/usr/bin/env python3
"""Tests for rerun/mine_search_behaviour.py's follow-up count (dev-env#1326).

Required when changing experiments/2026-11-issue-dup-retrieval/rerun/mine_search_behaviour.py.
Run: py -3 claude/scripts/tests/test_mine_search_behaviour.py

Whether a search was followed by another is decided by position in the episode, the same way for an
empty and a non-empty result. The old empty-result branch also counted any multi-query or chained
call in the episode, even one before the search, so these tests fail if that clause returns.
Standard library only.
"""
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "experiments" / "2026-11-issue-dup-retrieval" / "rerun"))

import mine_search_behaviour as m  # noqa: E402

ONE = 'gh issue list --repo o/r --search "alpha beta"'
MULTI = 'gh issue list --repo o/r --search "alpha"; gh issue list --repo o/r --search "beta"'
CHAINED = 'gh issue list --repo o/r --search "gamma" && gh issue create --title t'
EMPTY = "[]"
HIT = "#12\tsome title"


class Reactions(unittest.TestCase):
    def test_empty_search_with_multi_query_call_before_it_is_not_followed(self):
        run = [(MULTI, HIT), (ONE, EMPTY)]
        self.assertEqual(m.reactions(run), [(True, False)])

    def test_empty_search_with_multi_query_call_after_it_is_followed(self):
        run = [(ONE, EMPTY), (MULTI, HIT)]
        self.assertEqual(m.reactions(run), [(True, True)])

    def test_empty_search_with_chained_call_before_it_is_not_followed(self):
        run = [(CHAINED, HIT), (ONE, EMPTY)]
        self.assertEqual(m.reactions(run), [(True, False)])

    def test_nonempty_search_with_multi_query_call_after_it_is_followed(self):
        run = [(ONE, HIT), (MULTI, HIT)]
        self.assertEqual(m.reactions(run), [(False, True)])

    def test_nonempty_search_with_multi_query_call_before_it_is_not_followed(self):
        run = [(MULTI, HIT), (ONE, HIT)]
        self.assertEqual(m.reactions(run), [(False, False)])

    def test_empty_and_nonempty_agree_on_the_same_position(self):
        for result in (EMPTY, HIT):
            self.assertEqual(m.reactions([(MULTI, HIT), (ONE, result), (ONE, HIT)])[0][1], True)
            self.assertEqual(m.reactions([(MULTI, HIT), (ONE, result)])[0][1], False)

    def test_search_without_a_result_is_skipped(self):
        self.assertEqual(m.reactions([(ONE, None), (ONE, EMPTY)]), [(True, False)])


if __name__ == "__main__":
    unittest.main()
