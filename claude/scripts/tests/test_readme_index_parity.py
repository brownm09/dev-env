#!/usr/bin/env python3
"""Parity gate: every file in an indexed directory has a README row, and vice versa.

`claude/scripts/tests/README.md` (dev-env#822) and `claude/scripts/README.md`
(dev-env#830) are hand-maintained per-file navigational indexes of directories whose
tooling never reads them: `run-hook-tests.py` discovers tests by glob, and Claude Code
loads scripts by their `settings.json` wiring -- so a file with no README row still
works, the suite stays green, and the index rots silently. Both READMEs state the
contract in their own words ("a new test file is picked up automatically by the runner.
This README is not. Add a row below in the same PR that adds a test file."), but nothing
enforced it, and by the time this gate was written both had drifted: missing rows plus
header counts that were stale and, in one file, self-contradicting (dev-env#901).

This is the directory<->README analogue of `test_testing_index_parity.py` (Testing item
76), which gates `CLAUDE.md` <-> `docs/TESTING.md`. Pure offline file parse -- no
subprocess, network, or git. Auto-discovered by `run-hook-tests.py`, so it also gates CI
on every PR. Each README + directory is parsed once in `setUpClass`, matching the sibling.

Five checks per indexed directory:
  1. every indexable file (a top-level `.py`/`.sh`/`.ps1`, excluding `README.md`,
     subdirectories, and a documented per-directory exemption set) appears in some
     table's FIRST column; in `claude/scripts/tests/` a new test may instead carry its
     own entry, a `Required when` line and a `Run:` line near its top (dev-env#1309), so
     adding a test edits no shared file;
  2. every first-column filename exists on disk (catches a rename or delete that updates
     the file but not its row, leaving an orphan pointer);
  3. each exempt file both exists on disk and appears somewhere in the README -- so an
     exemption can never silently hide a file's deletion;
  4. each header count sentence occurs exactly once and equals the live directory count;
  5. every "### Section (N)" heading's N equals that section's first-column entry count,
     AND the number of such numbered headings matches the per-directory expectation (so a
     heading reworded past the "(N)" form cannot silently drop out of gating).

The gate is fail-closed throughout: a count sentence reworded past its regex fails check 4
loudly (found 0), a duplicated count fails it too (found >1), and a numbered heading that
loses its "(N)" fails check 5's heading-count assertion -- none of them pass on a count the
gate can no longer see. That is the correct direction for a gate whose whole job is
catching silent drift.

Known latent gap (does not manifest today): neither parser strips fenced ``` code blocks,
so a future README that put a "| `foo.py` |"-shaped line inside a fence could mis-parse it
as a table row. Both READMEs currently contain zero code fences; ADR-122 records this as an
accepted scope gap rather than carrying a fence state machine for a condition that does not
occur -- the same call ADR-116/ADR-121 made only after fence-blindness actually bit them.
"""
import os
import re
import unittest
from pathlib import Path

# .../claude/scripts/tests/test_readme_index_parity.py -> parents[3] == repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

# A backticked filename token (.py / .sh / .ps1) as it appears in the README tables.
FILENAME_RE = re.compile(r"`([A-Za-z0-9_.\-]+\.(?:py|sh|ps1))`")
# A "## Title (N)" / "### Title (N)" heading carrying a self-declared entry count.
SECTION_COUNT_RE = re.compile(r"^#{2,3} .+ \((\d+)\)\s*$")
# Any markdown heading -- used to bound where a section's rows end.
HEADING_RE = re.compile(r"^#{1,6} ")
# Files a README is expected to index. Restricting to these extensions matches each
# README's own declared scope and keeps a stray non-script artifact from being demanded
# a row; both directories currently contain nothing else anyway.
INDEXABLE_EXTS = (".py", ".sh", ".ps1")
# How far down a test's own entry may start: a docstring's or header comment's opening.
OWN_ENTRY_LINES = 10
REQUIRED_WHEN_RE = re.compile(r"^[#\s]*Required when\b", re.MULTILINE)


def indexable_files(directory):
    """Top-level script files a README is expected to index: `.py`/`.sh`/`.ps1`,
    excluding `README.md` itself and any subdirectory (`tests/`, gitignored
    `__pycache__/`)."""
    return {
        name
        for name in os.listdir(directory)
        if name != "README.md"
        and name.endswith(INDEXABLE_EXTS)
        and (directory / name).is_file()
    }


def first_column_names(readme_text):
    """Every filename appearing in the FIRST cell of a table data row. A row may name
    more than one file in that cell (e.g. a shared module's two tests) -- all count."""
    names = set()
    for line in readme_text.splitlines():
        if not line.startswith("| "):
            continue
        first_cell = line.split("|")[1]
        names.update(FILENAME_RE.findall(first_cell))
    return names


def section_entry_counts(readme_text):
    """Map each "## / ### Title (N)" heading line to (declared N, actual entry count),
    where the actual count is the number of first-column filename entries between that
    heading and the next heading of any level."""
    lines = readme_text.splitlines()
    result = {}
    for i, line in enumerate(lines):
        m = SECTION_COUNT_RE.match(line)
        if not m:
            continue
        actual = 0
        for row in lines[i + 1:]:
            if HEADING_RE.match(row):
                break
            if row.startswith("| "):
                actual += len(FILENAME_RE.findall(row.split("|")[1]))
        result[line.strip()] = (int(m.group(1)), actual)
    return result


def has_own_entry(path):
    """True if a test file's opening lines say when to run it and how to run this file."""
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:OWN_ENTRY_LINES])
    run_re = re.compile(rf"^[#\s]*Run: .*{re.escape(path.name)}", re.MULTILINE)
    return bool(REQUIRED_WHEN_RE.search(head) and run_re.search(head))


class DirSpec:
    """One indexed directory, its README, its first-column exemptions, the header count
    sentences to gate, the expected number of "(N)" section headings, and whether a file
    may carry its own entry instead of a row. Each count check is
    (label, regex, expected_fn) where expected_fn maps the live indexable-file set to the
    integer the header must state."""

    def __init__(self, label, directory, readme, exemptions, count_checks,
                 numbered_sections, allows_own_entry=False):
        self.label = label
        self.directory = REPO_ROOT / directory
        self.readme = REPO_ROOT / readme
        self.exemptions = exemptions
        self.count_checks = count_checks
        # How many "### Title (N)" headings this README is expected to carry. Gating the
        # *count* of numbered headings is what makes check 5 fail closed: a heading
        # reworded past the "(N)" form drops the section count below this number.
        self.numbered_sections = numbered_sections
        self.allows_own_entry = allows_own_entry

    def text(self):
        return self.readme.read_text(encoding="utf-8")


SPECS = [
    DirSpec(
        label="claude/scripts/tests",
        directory="claude/scripts/tests",
        readme="claude/scripts/tests/README.md",
        # _hook_wiring.py, _heartbeat_isolation.py and _throwaway_repo.py are test-support
        # infrastructure, not tests, so they are indexed in the SECOND column (with a "--" first
        # cell) rather than as test files. Check 3 still requires each to appear somewhere in
        # the README.
        exemptions={"_hook_wiring.py", "_heartbeat_isolation.py", "_throwaway_repo.py"},
        # No header counts here: every new test changed them, so any two test-adding PRs
        # conflicted on that line (dev-env#1309).
        count_checks=[],
        numbered_sections=0,  # this README's section headings carry no "(N)"
        allows_own_entry=True,
    ),
    DirSpec(
        label="claude/scripts",
        directory="claude/scripts",
        readme="claude/scripts/README.md",
        exemptions=set(),
        count_checks=[
            ("top-level-files",
             re.compile(r"\*\*(\d+) files\*\* at the top level"),
             lambda files: len(files)),
        ],
        numbered_sections=5,  # five "### Title (N)" domain sections
    ),
]


class TestReadmeIndexParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Parse each README and list each directory ONCE, matching the sibling
        # test_testing_index_parity.py's setUpClass rather than re-reading per method.
        cls.snap = {}
        for spec in SPECS:
            text = spec.text()
            cls.snap[spec.label] = {
                "files": indexable_files(spec.directory),
                "text": text,
                "sections": section_entry_counts(text),
                "indexed": first_column_names(text),
            }

    def test_every_indexable_file_has_a_first_column_row(self):
        for spec in SPECS:
            with self.subTest(dir=spec.label):
                snap = self.snap[spec.label]
                missing = sorted(
                    f for f in snap["files"]
                    if f not in snap["indexed"] and f not in spec.exemptions
                    and not (spec.allows_own_entry and has_own_entry(spec.directory / f))
                )
                remedy = (
                    f"put `Required when ...` and `Run: ...` lines in each file's first "
                    f"{OWN_ENTRY_LINES} lines (dev-env#1309)"
                    if spec.allows_own_entry else
                    f"add a row for each in {spec.readme.name} in this same PR (the README "
                    f"is not glob-discovered; dev-env#901)"
                )
                self.assertEqual(
                    missing, [],
                    f"{spec.label}: files present on disk but absent from every table's "
                    f"first column -- {remedy}: {missing}",
                )

    def test_every_first_column_name_exists_on_disk(self):
        for spec in SPECS:
            with self.subTest(dir=spec.label):
                snap = self.snap[spec.label]
                orphans = sorted(n for n in snap["indexed"] if n not in snap["files"])
                self.assertEqual(
                    orphans, [],
                    f"{spec.label}: {spec.readme.name} rows name files that no longer "
                    f"exist -- a rename or delete updated the file but not its row: "
                    f"{orphans}",
                )

    def test_exempt_files_are_real_and_indexed(self):
        for spec in SPECS:
            snap = self.snap[spec.label]
            for exempt in sorted(spec.exemptions):
                with self.subTest(dir=spec.label, file=exempt):
                    self.assertTrue(
                        (spec.directory / exempt).is_file(),
                        f"{spec.label}: exemption {exempt} names no real file -- drop "
                        f"the stale exemption from this gate",
                    )
                    self.assertIn(
                        f"`{exempt}`", snap["text"],
                        f"{spec.label}: exempt file {exempt} appears nowhere in "
                        f"{spec.readme.name} -- the exemption must not hide its deletion",
                    )

    def test_header_counts_match_directory(self):
        for spec in SPECS:
            snap = self.snap[spec.label]
            for label, regex, expected_fn in spec.count_checks:
                with self.subTest(dir=spec.label, count=label):
                    matches = regex.findall(snap["text"])
                    self.assertEqual(
                        len(matches), 1,
                        f"{spec.label}: expected exactly one {label} count sentence in "
                        f"{spec.readme.name}, found {len(matches)} -- a header reworded "
                        f"past its regex (0) or a stray duplicate copy (>1) both fail "
                        f"closed here on purpose",
                    )
                    self.assertEqual(
                        int(matches[0]), expected_fn(snap["files"]),
                        f"{spec.label}: header states {matches[0]} for {label} but the "
                        f"directory holds {expected_fn(snap['files'])} -- update the "
                        f"header count",
                    )

    def test_section_counts_match_entries(self):
        for spec in SPECS:
            sections = self.snap[spec.label]["sections"]
            with self.subTest(dir=spec.label, check="numbered-heading-count"):
                self.assertEqual(
                    len(sections), spec.numbered_sections,
                    f"{spec.label}: found {len(sections)} '### Title (N)' headings but "
                    f"expected {spec.numbered_sections} -- a heading reworded past the "
                    f"'(N)' form silently drops out of gating (fail-closed guard). Update "
                    f"numbered_sections here only if a section was deliberately "
                    f"added/removed",
                )
            for heading, (declared, actual) in sections.items():
                with self.subTest(dir=spec.label, heading=heading):
                    self.assertEqual(
                        declared, actual,
                        f"{spec.label}: heading {heading!r} declares {declared} but the "
                        f"section indexes {actual} files -- update the (N) in the heading",
                    )


if __name__ == "__main__":
    unittest.main()
