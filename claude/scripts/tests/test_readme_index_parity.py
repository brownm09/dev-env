#!/usr/bin/env python3
"""Parity gate: every file in an indexed directory has a README row, and vice versa.

`claude/scripts/tests/README.md` (dev-env#822) and `claude/scripts/README.md`
(dev-env#830) are hand-maintained per-file navigational indexes of directories whose
tooling never reads them: `run-hook-tests.py` discovers tests by glob, and Claude Code
loads scripts by their `settings.json` wiring -- so a file with no README row still
works, the suite stays green, and the index rots silently. When this gate was written both
READMEs stated the contract in their own words ("a new test file is picked up automatically
by the runner. This README is not. Add a row below in the same PR that adds a test file."),
but nothing enforced it, and both had drifted: missing rows plus header counts that were
stale and, in one file, self-contradicting (dev-env#901).

This is the directory<->README analogue of `test_testing_index_parity.py` (Testing item
76), which gates `CLAUDE.md` <-> `docs/TESTING.md`. Pure offline file parse -- no
subprocess, network, or git. Auto-discovered by `run-hook-tests.py`, so it also gates CI
on every PR. Each README + directory is parsed once in `setUpClass`, matching the sibling.

Six checks per indexed directory:
  1. every indexable file (a top-level `.py`/`.sh`/`.ps1`, excluding `README.md`,
     subdirectories, and a documented per-directory exemption set) appears in some
     table's FIRST column; in `claude/scripts/tests/` a file the runner discovers must
     instead carry its own entry, a `Required when` line and a `Run:` line near its top
     (dev-env#1309), so adding a test edits no shared file;
  2. every first-column filename exists on disk (catches a rename or delete that updates
     the file but not its row, leaving an orphan pointer);
  3. each exempt file both exists on disk and appears somewhere in the README -- so an
     exemption can never silently hide a file's deletion;
  4. each header count sentence occurs exactly once and equals the live directory count;
  5. every "### Section (N)" heading's N equals that section's first-column entry count,
     AND the number of such numbered headings matches the per-directory expectation (so a
     heading reworded past the "(N)" form cannot silently drop out of gating);
  6. where a README's rows are frozen (`claude/scripts/tests/`), it has no more first-column
     names than it had when they were frozen, since a new row is a conflict waiting for the
     next PR that adds one. A count, so removing a test leaves room for one row.

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
import tempfile
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
# What may come before an entry on its line: a comment mark, or a docstring's opening quotes.
ENTRY_PREFIX = r"^[#\s\"']*"
REQUIRED_WHEN_RE = re.compile(ENTRY_PREFIX + r"Required when[ \t]+\S", re.MULTILINE)
# The tests README's first-column names when its rows were frozen (dev-env#1309).
FROZEN_TEST_ROWS = 118


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


def runner_discovers(name):
    """Whether `run-hook-tests.py` runs this file; a support module can't index itself."""
    if name.startswith("_"):
        return False
    return name.endswith(".sh") or (name.startswith("test_") and name.endswith(".py"))


def has_own_entry(path):
    """True if a test file's opening lines say when to run it and how to run this file."""
    if not runner_discovers(path.name):
        return False
    # errors="replace": a file in another encoding must fail this check, not crash the gate.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    head = "\n".join(text.splitlines()[:OWN_ENTRY_LINES])
    run_re = re.compile(
        ENTRY_PREFIX + rf"Run: .*(?<![\w.-]){re.escape(path.name)}(?!\w)", re.MULTILINE
    )
    return bool(REQUIRED_WHEN_RE.search(head) and run_re.search(head))


class DirSpec:
    """One indexed directory, its README, its first-column exemptions, the header count
    sentences to gate, the expected number of "(N)" section headings, and whether a file
    may carry its own entry instead of a row, with the frozen number of rows that goes with
    that. Each count check is (label, regex, expected_fn) where expected_fn maps the live
    indexable-file set to the integer the header must state."""

    def __init__(self, label, directory, readme, exemptions, count_checks,
                 numbered_sections, frozen_rows=None):
        self.label = label
        self.directory = REPO_ROOT / directory
        self.readme = REPO_ROOT / readme
        self.exemptions = exemptions
        self.count_checks = count_checks
        # How many "### Title (N)" headings this README is expected to carry. Gating the
        # *count* of numbered headings is what makes check 5 fail closed: a heading
        # reworded past the "(N)" form drops the section count below this number.
        self.numbered_sections = numbered_sections
        self.frozen_rows = frozen_rows
        self.allows_own_entry = frozen_rows is not None

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
        frozen_rows=FROZEN_TEST_ROWS,
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
                    f"in each test's first {OWN_ENTRY_LINES} lines, start one line with "
                    f"`Required when` and what it covers, and another with `Run:` and a "
                    f"command naming that file on the same line (dev-env#1309). A support "
                    f"module the runner doesn't discover goes in this gate's exemptions, "
                    f"and in the README's second column, instead"
                    if spec.allows_own_entry else
                    f"add a row for each in {spec.readme.name} in this same PR (the README "
                    f"is not glob-discovered; dev-env#901)"
                )
                self.assertEqual(
                    missing, [],
                    f"{spec.label}: files present on disk but absent from every table's "
                    f"first column -- {remedy}: {missing}",
                )

    def test_frozen_rows_do_not_grow(self):
        for spec in SPECS:
            if spec.frozen_rows is None:
                continue
            with self.subTest(dir=spec.label):
                rows = len(self.snap[spec.label]["indexed"])
                # A non-empty parse is part of the check: zero rows would pass the ceiling.
                self.assertGreater(rows, 0, f"{spec.readme.name} parsed to zero rows")
                self.assertLessEqual(
                    rows, spec.frozen_rows,
                    f"{spec.label}: {spec.readme.name} has {rows} rows, past the frozen "
                    f"{spec.frozen_rows}. A new row conflicts with the next PR that adds "
                    f"one: remove it, and put `Required when ...` and `Run: ...` lines in "
                    f"the test's first {OWN_ENTRY_LINES} lines instead (dev-env#1309)",
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


class TestHasOwnEntry(unittest.TestCase):
    """Known-good and known-bad shapes for a test's own entry (dev-env#1309)."""

    def _check(self, name, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            return has_own_entry(path)

    def test_accepts_the_documented_shapes(self):
        run = "Run: py -3 claude/scripts/tests/test_foo.py"
        cases = {
            "docstring": f'"""What it covers.\n\nRequired when changing foo.py.\n{run}\n"""\n',
            "first line of a docstring": f'"""Required when changing foo.py.\n{run}\n"""\n',
            "comment": f"#!/usr/bin/env python3\n# Required when changing foo.py.\n# {run}\n",
            "indented": f'"""Covers foo.\n\n    Required when changing foo.py.\n    {run}\n"""\n',
            "CRLF": f'"""Covers foo.\r\n\r\nRequired when changing foo.py.\r\n{run}\r\n"""\r\n',
            "byte order mark": f"﻿# Required when changing foo.py.\n# {run}\n",
            "last allowed line": "\n" * 8 + f"# Required when changing foo.py.\n# {run}\n",
        }
        for label, content in cases.items():
            with self.subTest(label):
                self.assertTrue(self._check("test_foo.py", content))
        self.assertTrue(self._check(
            "check-foo.sh",
            "#!/bin/bash\n# Required when changing foo.\n# Run: bash claude/scripts/tests/check-foo.sh\n",
        ))

    def test_refuses_what_is_not_an_entry(self):
        required = "# Required when changing foo.py.\n"
        cases = {
            "no Required when": "# Run: py -3 test_foo.py\n",
            "no Run": required,
            "nothing after Required when": "# Required when\n# Run: py -3 test_foo.py\n",
            "Run names a longer file": required + "# Run: py -3 old_test_foo.py\n",
            "Run names a longer extension": required + "# Run: py -3 test_foo.pyc\n",
            "Run wrapped onto another line": required + "# Run: py -3\n#   test_foo.py\n",
            "past the first ten lines": "\n" * 9 + required + "# Run: py -3 test_foo.py\n",
            "mentioned mid-sentence": "# Not Required when x. See Run: py -3 test_foo.py\n",
        }
        for label, content in cases.items():
            with self.subTest(label):
                self.assertFalse(self._check("test_foo.py", content))

    def test_refuses_a_file_the_runner_does_not_discover(self):
        for name in ("_foo.py", "foo.py", "foo.ps1", "_foo.sh"):
            with self.subTest(name):
                entry = f"# Required when changing foo.py.\n# Run: py -3 {name}\n"
                self.assertFalse(self._check(name, entry))

    def test_another_encoding_fails_without_crashing_the_gate(self):
        utf16 = "# Required when changing foo.py.\n# Run: py -3 test_foo.py\n".encode("utf-16")
        self.assertFalse(self._check("test_foo.py", utf16))


if __name__ == "__main__":
    unittest.main()
