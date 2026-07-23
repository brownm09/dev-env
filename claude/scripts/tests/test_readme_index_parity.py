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
on every PR.

Five checks per indexed directory:
  1. every indexable file (a top-level `.py`/`.sh`/`.ps1`, excluding `README.md`,
     subdirectories, and a documented per-directory exemption set) appears in some
     table's FIRST column;
  2. every first-column filename exists on disk (catches a rename or delete that updates
     the file but not its row, leaving an orphan pointer);
  3. each exempt file both exists on disk and appears somewhere in the README -- so an
     exemption can never silently hide a file's deletion;
  4. the header's stated file counts equal the live directory counts;
  5. every "### Section (N)" heading's N equals that section's first-column row count
     (only `claude/scripts/README.md` numbers its sections; the check is a no-op where
     no "(N)" headings exist).

The gate is fail-closed: if a count sentence is reworded past its regex, check 4 fails
loudly ("could not find the ... count sentence") rather than passing on a count it can no
longer see -- the correct direction for a gate whose whole job is catching silent drift.
"""
import fnmatch
import os
import re
import unittest
from pathlib import Path

# .../claude/scripts/tests/test_readme_index_parity.py -> parents[3] == repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

# A backticked filename token (.py / .sh / .ps1) as it appears in the README tables.
FILENAME_RE = re.compile(r"`([A-Za-z0-9_.\-]+\.(?:py|sh|ps1))`")
# A "## Title (N)" / "### Title (N)" heading carrying a self-declared row count.
SECTION_COUNT_RE = re.compile(r"^#{2,3} .+ \((\d+)\)\s*$")
# Any markdown heading -- used to bound where a section's rows end.
HEADING_RE = re.compile(r"^#{1,6} ")
# Files a README is expected to index. Restricting to these extensions matches each
# README's own declared scope and keeps a stray non-script artifact from being demanded
# a row; both directories currently contain nothing else anyway.
INDEXABLE_EXTS = (".py", ".sh", ".ps1")


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


def section_row_counts(readme_text):
    """Map each "## / ### Title (N)" heading line to (declared N, actual row count),
    where the actual count is the number of first-column filenames between that heading
    and the next heading of any level."""
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


def _count(pattern, files):
    return sum(1 for f in files if fnmatch.fnmatch(f, pattern))


class DirSpec:
    """One indexed directory, its README, its first-column exemptions, and the header
    count sentences to gate. Each count check is (label, regex, expected_fn) where
    expected_fn maps the live indexable-file set to the integer the header must state."""

    def __init__(self, label, directory, readme, exemptions, count_checks):
        self.label = label
        self.directory = REPO_ROOT / directory
        self.readme = REPO_ROOT / readme
        self.exemptions = exemptions
        self.count_checks = count_checks

    def text(self):
        return self.readme.read_text(encoding="utf-8")


SPECS = [
    DirSpec(
        label="claude/scripts/tests",
        directory="claude/scripts/tests",
        readme="claude/scripts/tests/README.md",
        # _hook_wiring.py is test-support infrastructure, not a test, so it is indexed in
        # the SECOND column (with a "--" first cell) rather than as a test file. Check 3
        # still requires it to appear somewhere in the README.
        exemptions={"_hook_wiring.py"},
        count_checks=[
            ("test_*.py",
             re.compile(r"(\d+) `test_\*\.py` files"),
             lambda files: _count("test_*.py", files)),
            ("bash-gate",
             re.compile(r"(\d+) bash gates"),
             lambda files: _count("*.sh", files)),
            ("total-files",
             re.compile(r"(\d+) files total"),
             lambda files: len(files)),
        ],
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
    ),
]


class TestReadmeIndexParity(unittest.TestCase):
    def test_every_indexable_file_has_a_first_column_row(self):
        for spec in SPECS:
            with self.subTest(dir=spec.label):
                files = indexable_files(spec.directory)
                indexed = first_column_names(spec.text())
                missing = sorted(
                    f for f in files if f not in indexed and f not in spec.exemptions
                )
                self.assertEqual(
                    missing, [],
                    f"{spec.label}: files present on disk but absent from every table's "
                    f"first column -- add a row for each in {spec.readme.name} in this "
                    f"same PR (the README is not glob-discovered; dev-env#901): {missing}",
                )

    def test_every_first_column_name_exists_on_disk(self):
        for spec in SPECS:
            with self.subTest(dir=spec.label):
                files = indexable_files(spec.directory)
                indexed = first_column_names(spec.text())
                orphans = sorted(n for n in indexed if n not in files)
                self.assertEqual(
                    orphans, [],
                    f"{spec.label}: {spec.readme.name} rows name files that no longer "
                    f"exist -- a rename or delete updated the file but not its row: "
                    f"{orphans}",
                )

    def test_exempt_files_are_real_and_indexed(self):
        for spec in SPECS:
            for exempt in sorted(spec.exemptions):
                with self.subTest(dir=spec.label, file=exempt):
                    self.assertTrue(
                        (spec.directory / exempt).is_file(),
                        f"{spec.label}: exemption {exempt} names no real file -- drop "
                        f"the stale exemption from this gate",
                    )
                    self.assertIn(
                        f"`{exempt}`", spec.text(),
                        f"{spec.label}: exempt file {exempt} appears nowhere in "
                        f"{spec.readme.name} -- the exemption must not hide its deletion",
                    )

    def test_header_counts_match_directory(self):
        for spec in SPECS:
            files = indexable_files(spec.directory)
            text = spec.text()
            for label, regex, expected_fn in spec.count_checks:
                with self.subTest(dir=spec.label, count=label):
                    m = regex.search(text)
                    self.assertIsNotNone(
                        m,
                        f"{spec.label}: could not find the {label} count sentence in "
                        f"{spec.readme.name} (header reworded past its regex? update the "
                        f"count_checks pattern -- the gate fails closed here on purpose)",
                    )
                    self.assertEqual(
                        int(m.group(1)), expected_fn(files),
                        f"{spec.label}: header states {m.group(1)} for {label} but the "
                        f"directory holds {expected_fn(files)} -- update the header count",
                    )

    def test_section_counts_match_rows(self):
        for spec in SPECS:
            for heading, (declared, actual) in section_row_counts(spec.text()).items():
                with self.subTest(dir=spec.label, heading=heading):
                    self.assertEqual(
                        declared, actual,
                        f"{spec.label}: heading {heading!r} declares {declared} rows but "
                        f"the section holds {actual} -- update the (N) in the heading",
                    )


if __name__ == "__main__":
    unittest.main()
