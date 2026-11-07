#!/usr/bin/env python3
"""Two PRs that each add a test must merge cleanly, in either order (dev-env#1309).

Required when changing `test_testing_index_parity.py`, `test_readme_index_parity.py`, or
the `## Testing` introduction in the root `CLAUDE.md`.
Run: py -3 claude/scripts/tests/test_testing_index_merge.py

Builds a throwaway repository from the real indexed files and the real parity gates,
then adds two tests on two branches the documented way: a `Required when` line and a
`Run:` line at the top of the test file, with no edit to `CLAUDE.md`, `docs/TESTING.md`
or `claude/scripts/tests/README.md`. It merges them both ways with `git merge`, and
with `git merge-tree` in a bare clone. `git merge-tree` is the closest local stand-in
for GitHub's merge, which has no working tree (ADR-074 Amendment 1).

The clean merges record what git does with those two branches. They can't fail from a
change to this repository, because branches that add different files have no text to
merge. What holds the rule is everything else here: the old way must conflict, and the
gates must refuse each other way of indexing a test, so the documented way stays the
only one that passes.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _throwaway_repo import _env
from test_testing_index_parity import extract_section

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_FILES = ("CLAUDE.md", "docs/TESTING.md", "claude/scripts/tests/README.md")
PARITY_GATES = ("test_testing_index_parity.py", "test_readme_index_parity.py")
# Written out rather than computed from LAST_NUMBERED_ITEM: raising that constant to
# admit a new item must fail here, or the freeze is one more edit in the same PR.
FIRST_REFUSED_ITEM = 119
# The machine's git config can change how a merge behaves, or stop a commit outright.
GIT_ENV = {**_env(), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args, check=True):
    # encoding= because these files hold em dashes and the locale here is cp1252.
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd), capture_output=True, encoding="utf-8", errors="replace",
        env=GIT_ENV, timeout=120,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result


def _new_test_body(name):
    return (
        f'"""Fixture test {name}.\n\n'
        f"Required when changing claude/scripts/{name}.py.\n"
        f"Run: py -3 claude/scripts/tests/{name}.py\n"
        '"""\n'
    )


class TestTestingIndexMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.repo = Path(cls._tmp.name) / "repo"
        for rel in ("claude/scripts/tests", "docs"):
            (cls.repo / rel).mkdir(parents=True)
        for rel in (".gitattributes", "CLAUDE.md", "docs/TESTING.md"):
            shutil.copy2(REPO_ROOT / rel, cls.repo / rel)
        # The README gate indexes both directories, so it needs every file in them.
        for rel in ("claude/scripts", "claude/scripts/tests"):
            for path in (REPO_ROOT / rel).iterdir():
                if path.is_file():
                    shutil.copy2(path, cls.repo / rel / path.name)

        _git(cls._tmp.name, "init", "-q", str(cls.repo))
        _git(cls.repo, "checkout", "-q", "-b", "main")
        _git(cls.repo, "config", "user.email", "test@example.com")
        _git(cls.repo, "config", "user.name", "Test")
        cls._commit("base")

        cls._branch("new-a", {"claude/scripts/tests/test_fixture_a.py": _new_test_body("test_fixture_a")})
        cls._branch("new-b", {"claude/scripts/tests/test_fixture_b.py": _new_test_body("test_fixture_b")})
        cls._branch("old-a", cls._numbered_item(f"{FIRST_REFUSED_ITEM}. **Fixture A** — run `true`"))
        cls._branch("old-b", cls._numbered_item(f"{FIRST_REFUSED_ITEM}. **Fixture B** — run `true`"))
        cls._branch("indented", cls._numbered_item(f"  {FIRST_REFUSED_ITEM}. **Fixture** — run `true`"))
        cls._branch("no-bold", cls._numbered_item(f"{FIRST_REFUSED_ITEM}. Fixture — run `true`"))
        cls._branch("duplicate", cls._numbered_item(f"{FIRST_REFUSED_ITEM - 1}. **Fixture** — run `true`"))
        cls._branch("no-entry", {"claude/scripts/tests/test_fixture_bare.py": '"""No entry."""\n'})
        cls._branch("new-row", cls._new_readme_row("test_fixture_row.py"))
        _git(cls._tmp.name, "clone", "-q", "--bare", str(cls.repo), "bare.git")
        cls.bare = Path(cls._tmp.name) / "bare.git"

    @classmethod
    def _commit(cls, message):
        _git(cls.repo, "add", "-A")
        _git(cls.repo, "commit", "-q", "-m", message)

    @classmethod
    def _branch(cls, name, files):
        """Commit a file->content mapping on a new branch cut from main."""
        _git(cls.repo, "checkout", "-q", "-b", name, "main")
        for rel, text in files.items():
            (cls.repo / rel).write_text(text, encoding="utf-8", newline="\n")
        cls._commit(name)
        _git(cls.repo, "checkout", "-q", "main")

    @classmethod
    def _numbered_item(cls, line):
        """Add one line to the end of both numbered lists."""
        item = line + "\n"
        claude_md = (cls.repo / "CLAUDE.md").read_text(encoding="utf-8")
        marker = "\n## Experiments\n"
        if marker not in claude_md:
            raise AssertionError("CLAUDE.md lost its ## Experiments heading; fix this fixture")
        testing_md = (cls.repo / "docs/TESTING.md").read_text(encoding="utf-8")
        return {
            "CLAUDE.md": claude_md.replace(marker, item + marker, 1),
            "docs/TESTING.md": testing_md.rstrip("\n") + "\n\n" + item,
        }

    @classmethod
    def _new_readme_row(cls, name):
        """A new test indexed by a README row, with no entry of its own."""
        rel = "claude/scripts/tests/README.md"
        lines = (cls.repo / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        row = next((i for i, line in enumerate(lines) if line.startswith("| `test_")), None)
        if row is None:
            raise AssertionError("the test README has no test rows; fix this fixture")
        lines.insert(row, f"| `{name}` | a fixture | A row. |\n")
        return {rel: "".join(lines), f"claude/scripts/tests/{name}": '"""No entry."""\n'}

    def _merge_both(self, first, second):
        """Merge two branches onto main in a throwaway branch; return the second merge."""
        scratch = f"merge-{first}-{second}"
        _git(self.repo, "checkout", "-q", "-f", "-B", scratch, "main")
        _git(self.repo, "merge", "-q", "--no-edit", first)
        result = _git(self.repo, "merge", "-q", "--no-edit", second, check=False)
        self.addCleanup(lambda: _git(self.repo, "merge", "--abort", check=False))
        return result

    def _merge_tree(self, first, second):
        return _git(self.bare, "merge-tree", "--write-tree", first, second, check=False)

    def _run_gate(self, gate):
        """Run a parity gate from the checked-out tree, so it reads that tree."""
        return subprocess.run(
            [sys.executable, str(self.repo / "claude/scripts/tests" / gate)],
            cwd=str(self.repo), capture_output=True, encoding="utf-8", errors="replace",
            env=GIT_ENV, timeout=120,
        )

    def test_new_tests_merge_cleanly_in_either_order(self):
        for first, second in (("new-a", "new-b"), ("new-b", "new-a")):
            with self.subTest(order=f"{first} then {second}"):
                result = self._merge_both(first, second)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(_git(self.repo, "ls-files", "--unmerged").stdout, "")
                # Byte equality, not a search for "<<<<<<<": docs/TESTING.md has that text
                # in its prose. A file that matches its base can't hold a conflict, and
                # can't conflict with the next PR either.
                for rel in SHARED_FILES:
                    merged = _git(self.repo, "show", f"HEAD:{rel}").stdout
                    self.assertEqual(merged, _git(self.repo, "show", f"main:{rel}").stdout,
                                     f"{rel} changed, so a later PR could still conflict")

    def test_new_tests_merge_cleanly_with_no_working_tree(self):
        for first, second in (("new-a", "new-b"), ("new-b", "new-a")):
            with self.subTest(order=f"{first} then {second}"):
                result = self._merge_tree(first, second)
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_merged_tree_passes_both_parity_gates(self):
        result = self._merge_both("new-a", "new-b")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for gate in PARITY_GATES:
            with self.subTest(gate=gate):
                run = self._run_gate(gate)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_old_numbered_way_conflicts(self):
        """Known-bad reference: without this, the clean merges above could be a blind harness."""
        result = self._merge_both("old-a", "old-b")
        self.assertNotEqual(result.returncode, 0, "two appended items merged without a conflict")
        unmerged = _git(self.repo, "ls-files", "--unmerged").stdout
        self.assertIn("CLAUDE.md", unmerged)
        self.assertIn("docs/TESTING.md", unmerged)

    def test_old_numbered_way_conflicts_with_no_working_tree(self):
        result = self._merge_tree("old-a", "old-b")
        self.assertEqual(result.returncode, 1, f"expected a conflict, got: {result.stdout}{result.stderr}")

    def test_written_procedure_is_the_one_the_fixtures_follow(self):
        intro = extract_section((REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8"), "Testing")
        for phrase in ("edit only the test file", "`Required when ...`", "`Run: ...`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, intro)

    def test_gate_refuses_each_new_numbered_form(self):
        for branch in ("old-a", "indented", "no-bold", "duplicate"):
            with self.subTest(branch=branch):
                _git(self.repo, "checkout", "-q", "-f", branch)
                run = self._run_gate("test_testing_index_parity.py")
                self.assertNotEqual(run.returncode, 0, f"{branch} passed the parity gate")
                self.assertIn("Required when", run.stdout + run.stderr,
                              "the failure must say what to do instead of taking a number")

    def test_gate_refuses_a_new_test_with_no_entry(self):
        _git(self.repo, "checkout", "-q", "-f", "no-entry")
        run = self._run_gate("test_readme_index_parity.py")
        self.assertNotEqual(run.returncode, 0, "a test with no entry passed the README gate")
        self.assertIn("test_fixture_bare.py", run.stdout + run.stderr)

    def test_gate_refuses_a_new_readme_row(self):
        _git(self.repo, "checkout", "-q", "-f", "new-row")
        run = self._run_gate("test_readme_index_parity.py")
        self.assertNotEqual(run.returncode, 0, "a new README row passed the README gate")
        self.assertIn("Required when", run.stdout + run.stderr,
                      "the failure must say what to do instead of adding a row")


if __name__ == "__main__":
    unittest.main()
