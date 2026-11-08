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

Four checks per indexed directory:
  1. every indexable file (a top-level `.py`/`.sh`/`.ps1`, excluding `README.md`,
     subdirectories, and a documented per-directory exemption set) appears in some
     table's FIRST column, or carries its own entry near its top: in
     `claude/scripts/tests/` a `Required when` line and a `Run:` line on a file the
     runner discovers (dev-env#1309), and in `claude/scripts/` a `Purpose:` line
     (dev-env#1311), so adding a test or a script edits no shared file;
  2. every first-column filename exists on disk (catches a rename or delete that updates
     the file but not its row, leaving an orphan pointer);
  3. each exempt file both exists on disk and appears somewhere in the README -- so an
     exemption can never silently hide a file's deletion;
  4. each README has no more first-column names than it had when its rows were frozen,
     since a new row is a conflict waiting for the next PR that adds one. A count, so
     removing a file leaves room for one row. Neither README carries a file count or a
     "### Section (N)" count any more, because every new file changed them.

Known latent gap (does not manifest today): neither parser strips fenced ``` code blocks,
so a future README that put a "| `foo.py` |"-shaped line inside a fence could mis-parse it
as a table row. Both READMEs currently contain zero code fences; ADR-122 records this as an
accepted scope gap rather than carrying a fence state machine for a condition that does not
occur -- the same call ADR-116/ADR-121 made only after fence-blindness actually bit them.

A second accepted gap: the `Purpose:` check reads text, not syntax, so a code line in a
script's first 10 lines that starts with `Purpose:` (an annotated variable `Purpose: int = 3`)
would count as an entry. No script does this, and telling a docstring line from a code line
would mean parsing three languages for a case that hasn't occurred.
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
# Files a README is expected to index. Restricting to these extensions matches each
# README's own declared scope and keeps a stray non-script artifact from being demanded
# a row; both directories currently contain nothing else anyway.
INDEXABLE_EXTS = (".py", ".sh", ".ps1")
# How far down a test's own entry may start: a docstring's or header comment's opening.
OWN_ENTRY_LINES = 10
# What may come before an entry on its line: a comment mark, or a docstring's opening quotes.
ENTRY_PREFIX = r"^[#\s\"']*"
REQUIRED_WHEN_RE = re.compile(ENTRY_PREFIX + r"Required when[ \t]+\S", re.MULTILINE)
# A script's entry may also open a PowerShell `<#` block or a prefixed docstring (`r"""`),
# and needs text after it that isn't the docstring's own closing quotes.
PURPOSE_RE = re.compile(
    r"^[ \t]*(?:#+|<#|[rRuU]?(?:\"\"\"|'''))?[ \t]*Purpose:[ \t]+[^\s\"']", re.MULTILINE
)
# Each README's first-column names when its rows were frozen (dev-env#1309, #1311).
FROZEN_TEST_ROWS = 118
FROZEN_SCRIPT_ROWS = 115


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


def runner_discovers(name):
    """Whether `run-hook-tests.py` runs this file; a support module can't index itself."""
    if name.startswith("_"):
        return False
    return name.endswith(".sh") or (name.startswith("test_") and name.endswith(".py"))


def _head(path):
    """A file's first OWN_ENTRY_LINES lines, where its own entry must start."""
    # errors="replace": a file in another encoding must fail this check, not crash the gate.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return "\n".join(text.splitlines()[:OWN_ENTRY_LINES])


def has_own_entry(path):
    """True if a test file's opening lines say when to run it and how to run this file."""
    if not runner_discovers(path.name):
        return False
    head = _head(path)
    run_re = re.compile(
        ENTRY_PREFIX + rf"Run: .*(?<![\w.-]){re.escape(path.name)}(?!\w)", re.MULTILINE
    )
    return bool(REQUIRED_WHEN_RE.search(head) and run_re.search(head))


def has_purpose_line(path):
    """True if a script's opening lines say what it does. A comment or docstring line,
    so it changes nothing about how a hook, module or `.sh`/`.ps1` script runs."""
    return bool(PURPOSE_RE.search(_head(path)))


class DirSpec:
    """One indexed directory, its README, its first-column exemptions, the frozen number
    of README rows, and the check that a file without a row carries its own entry, with
    the instruction that says how to write one."""

    def __init__(self, label, directory, readme, exemptions, frozen_rows, own_entry,
                 entry_hint):
        self.label = label
        self.directory = REPO_ROOT / directory
        self.readme = REPO_ROOT / readme
        self.exemptions = exemptions
        self.frozen_rows = frozen_rows
        self.own_entry = own_entry
        self.entry_hint = entry_hint

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
        frozen_rows=FROZEN_TEST_ROWS,
        own_entry=has_own_entry,
        entry_hint=(
            f"in each test's first {OWN_ENTRY_LINES} lines, start one line with "
            f"`Required when` and what it covers, and another with `Run:` and a command "
            f"naming that file on the same line (dev-env#1309). A support module the "
            f"runner doesn't discover goes in this gate's exemptions, and in the README's "
            f"second column, instead"
        ),
    ),
    DirSpec(
        label="claude/scripts",
        directory="claude/scripts",
        readme="claude/scripts/README.md",
        exemptions=set(),
        frozen_rows=FROZEN_SCRIPT_ROWS,
        own_entry=has_purpose_line,
        entry_hint=(
            f"in each script's first {OWN_ENTRY_LINES} lines, in its docstring or a "
            f"comment, start one line with `Purpose:` and what the script does "
            f"(dev-env#1311)"
        ),
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
                "indexed": first_column_names(text),
            }

    def test_every_indexable_file_has_a_first_column_row(self):
        for spec in SPECS:
            with self.subTest(dir=spec.label):
                snap = self.snap[spec.label]
                missing = sorted(
                    f for f in snap["files"]
                    if f not in snap["indexed"] and f not in spec.exemptions
                    and not spec.own_entry(spec.directory / f)
                )
                self.assertEqual(
                    missing, [],
                    f"{spec.label}: files with neither a first-column row in "
                    f"{spec.readme.name} nor their own entry -- {spec.entry_hint}: {missing}",
                )

    def test_frozen_rows_do_not_grow(self):
        for spec in SPECS:
            with self.subTest(dir=spec.label):
                rows = len(self.snap[spec.label]["indexed"])
                # A non-empty parse is part of the check: zero rows would pass the ceiling.
                self.assertGreater(rows, 0, f"{spec.readme.name} parsed to zero rows")
                self.assertLessEqual(
                    rows, spec.frozen_rows,
                    f"{spec.label}: {spec.readme.name} has {rows} rows, past the frozen "
                    f"{spec.frozen_rows}. A new row conflicts with the next PR that adds "
                    f"one: remove it, and {spec.entry_hint}",
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


class TestHasPurposeLine(unittest.TestCase):
    """Known-good and known-bad shapes for a script's own entry (dev-env#1311)."""

    def _check(self, name, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            return has_purpose_line(path)

    def test_accepts_each_kind_of_script(self):
        purpose = "Purpose: does foo."
        cases = {
            ("hook.py", "docstring"): f'#!/usr/bin/env python3\n"""Foo hook.\n\n{purpose}\n"""\n',
            ("_mod.py", "first line of a docstring"): f'"""{purpose}\n"""\n',
            ("tool.py", "comment"): f"# {purpose}\nimport sys\n",
            ("tool.sh", "comment after a shebang"): f"#!/usr/bin/env bash\n# {purpose}\nset -e\n",
            ("tool.ps1", "comment after #Requires"):
                f"#Requires -RunAsAdministrator\n# {purpose}\nparam()\n",
            ("tool.ps1", "inside a comment block"): f"<#\n.SYNOPSIS\n{purpose}\n#>\n",
            ("tool.ps1", "on a comment block's opener"): f"<# {purpose} #>\nparam()\n",
            ("tool.py", "raw docstring"): f'r"""{purpose}\n"""\n',
            ("tool.py", "single-quoted docstring"): f"'''{purpose}\n'''\n",
            ("tool.py", "tab-indented docstring line"): f'"""Foo.\n\n\t{purpose}\n"""\n',
            ("tool.py", "CRLF"): f'"""Foo.\r\n\r\n{purpose}\r\n"""\r\n',
            ("tool.py", "byte order mark"): f"﻿# {purpose}\n",
            ("tool.py", "last allowed line"): "\n" * 9 + f"# {purpose}\n",
        }
        for (name, label), content in cases.items():
            with self.subTest(label, name=name):
                self.assertTrue(self._check(name, content))

    def test_refuses_what_is_not_an_entry(self):
        cases = {
            "no Purpose line": '"""Does foo."""\n',
            "nothing after Purpose:": "# Purpose:\n",
            "only a docstring's closing quotes after Purpose:": '"""Purpose: """\n',
            "no colon": "# Purpose does foo.\n",
            "past the first ten lines": "\n" * 10 + "# Purpose: does foo.\n",
            "mentioned mid-sentence": "# Its Purpose: does foo.\n",
            "a test's entry": "# Required when changing foo.py.\n# Run: py -3 foo.py\n",
            "another encoding": "# Purpose: does foo.\n".encode("utf-16"),
        }
        for label, content in cases.items():
            with self.subTest(label):
                self.assertFalse(self._check("tool.py", content))


if __name__ == "__main__":
    unittest.main()
