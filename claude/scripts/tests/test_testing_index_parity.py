"""Parity gate for the ADR-114 two-file Testing split (Testing item 76).

Root CLAUDE.md's `## Testing` is a one-line-per-item index; docs/TESTING.md holds the
full per-item behavioral detail under the SAME item numbers. The merge-time
numbering-collision gate (`pre-merge-numbering-check.py`, ADR-074) guards only the
index's numbers against origin/main — it never opens docs/TESTING.md — so without this
gate, cross-file drift (an item added to one file but not the other, or the same number
titling different tests) would be caught by nothing mechanical. Added from /review
findings on PR #855.

Pure offline file parse — no subprocess, network, or git. Auto-discovered by
run-hook-tests.py, so it also gates CI on every PR.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Same shape pre-merge-numbering-check.py parses, extended to capture the bold title.
ITEM_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*", re.MULTILINE)
# The numbered list stops here (dev-env#1309, ADR-074 Amendment 1): other documents cite
# these numbers, and appending to the list made every pair of test-adding PRs conflict.
LAST_NUMBERED_ITEM = 118
# Any numbered line, bold or not, indented or not. Detail items hold numbered sub-lists,
# so this only ever counts against numbers past the last item.
ANY_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\.\s", re.MULTILINE)
FREEZE_REMEDY = (
    "a new test takes no number. Remove the item from both files and the README row, and "
    "put `Required when ...` and `Run: ...` lines in the test file's first 10 lines "
    "instead (dev-env#1309)"
)


def extract_section(text, heading):
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == f"## {heading}")
    except StopIteration:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
               len(lines))
    return "\n".join(lines[start + 1:end])


def items_of(text):
    return {int(m.group(1)): m.group(2).strip() for m in ITEM_RE.finditer(text)}


class TestTestingIndexParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        claude_md = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        testing_md = (REPO_ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
        cls.index_text = extract_section(claude_md, "Testing")
        cls.detail_text = testing_md
        cls.index = items_of(cls.index_text)
        cls.detail = items_of(cls.detail_text)

    def test_both_files_have_items(self):
        self.assertTrue(self.index, "CLAUDE.md ## Testing index parsed to zero items")
        self.assertTrue(self.detail, "docs/TESTING.md parsed to zero items")

    def test_numbering_contiguous_from_one(self):
        for name, items in (("CLAUDE.md index", self.index),
                            ("docs/TESTING.md", self.detail)):
            nums = sorted(items)
            self.assertEqual(nums, list(range(1, len(nums) + 1)),
                             f"{name} numbering not contiguous from 1: {nums}")

    def test_no_new_numbered_items(self):
        for name, text in (("CLAUDE.md index", self.index_text),
                           ("docs/TESTING.md", self.detail_text)):
            new = sorted(int(n) for n in ANY_NUMBERED_LINE_RE.findall(text)
                         if int(n) > LAST_NUMBERED_ITEM)
            self.assertEqual(new, [],
                             f"{name} has numbered lines past {LAST_NUMBERED_ITEM}: "
                             f"{FREEZE_REMEDY}")

    def test_no_item_number_is_used_twice(self):
        # items_of builds a dict, so a second item with the same number would vanish from
        # every other check here.
        for name, text in (("CLAUDE.md index", self.index_text),
                           ("docs/TESTING.md", self.detail_text)):
            nums = [int(m.group(1)) for m in ITEM_RE.finditer(text)]
            repeated = sorted(n for n in set(nums) if nums.count(n) > 1)
            self.assertEqual(repeated, [],
                             f"{name} uses these item numbers twice: {FREEZE_REMEDY}")

    def test_last_item_is_the_frozen_one(self):
        for name, items in (("CLAUDE.md index", self.index),
                            ("docs/TESTING.md", self.detail)):
            self.assertEqual(max(items), LAST_NUMBERED_ITEM,
                             f"{name} doesn't end at the frozen item {LAST_NUMBERED_ITEM}")

    def test_item_sets_match(self):
        only_index = sorted(set(self.index) - set(self.detail))
        only_detail = sorted(set(self.detail) - set(self.index))
        self.assertEqual((only_index, only_detail), ([], []),
                         "item numbers present in only one file "
                         f"(index-only={only_index}, detail-only={only_detail}) — "
                         "add the missing counterpart in the same PR (ADR-114)")

    def test_titles_match_per_number(self):
        mismatches = {n: (self.index[n], self.detail[n])
                      for n in self.index
                      if n in self.detail and self.index[n] != self.detail[n]}
        self.assertEqual(mismatches, {},
                         "same item number carries different titles across the two files")


if __name__ == "__main__":
    unittest.main()
