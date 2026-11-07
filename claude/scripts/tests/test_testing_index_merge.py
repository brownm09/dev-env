#!/usr/bin/env python3
"""Two PRs that each add a test must merge cleanly, in either order (dev-env#1309).

Required when changing how a new test is indexed: the two parity gates, or the
`## Testing` introduction in the root CLAUDE.md.
Run: py -3 claude/scripts/tests/test_testing_index_merge.py

Builds a throwaway repository from the real indexed files and the real parity gates,
then adds two tests on two branches the documented way: a `Required when` line and a
`Run:` line at the top of the test file, with no edit to `CLAUDE.md`, `docs/TESTING.md`
or `claude/scripts/tests/README.md`. It merges them both ways with `git merge`, and
with `git merge-tree` in a bare clone. `git merge-tree` is the closest local stand-in
for GitHub's merge, which has no working tree (ADR-074 Amendment 1).

A green run proves little unless the harness can also go red, so the old way is the
known-bad reference: two branches that each append item 118 must conflict, and the
gates must refuse a numbered item or a new test with no entry.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_FILES = ("CLAUDE.md", "docs/TESTING.md", "claude/scripts/tests/README.md")
PARITY_GATES = ("test_testing_index_parity.py", "test_readme_index_parity.py")
# Git variables a hook environment can set; any of them would aim git at the real repo.
GIT_ENV_TO_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def _env():
    return {k: v for k, v in os.environ.items() if k not in GIT_ENV_TO_DROP}


def _git(cwd, *args, check=True):
    # encoding= because these files hold em dashes and the locale here is cp1252.
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, encoding="utf-8", errors="replace",
        env=_env(), timeout=120,
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

        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "-q", str(cls.repo)],
            check=True, capture_output=True, env=_env(),
        )
        _git(cls.repo, "checkout", "-q", "-b", "main")
        _git(cls.repo, "config", "user.email", "test@example.com")
        _git(cls.repo, "config", "user.name", "Test")
        cls._commit("base")

        cls._branch("new-a", {"claude/scripts/tests/test_fixture_a.py": _new_test_body("test_fixture_a")})
        cls._branch("new-b", {"claude/scripts/tests/test_fixture_b.py": _new_test_body("test_fixture_b")})
        cls._branch("old-a", cls._numbered_item("Fixture A"))
        cls._branch("old-b", cls._numbered_item("Fixture B"))
        cls._branch("no-entry", {"claude/scripts/tests/test_fixture_bare.py": '"""No entry."""\n'})

    @classmethod
    def tearDownClass(cls):
        # git marks object files read-only, which rmtree can't delete on Windows.
        def force_remove(func, path, _exc):
            os.chmod(path, 0o700)
            func(path)
        shutil.rmtree(cls.repo, onerror=force_remove)
        cls._tmp.cleanup()

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
    def _numbered_item(cls, title):
        """The old way: the next number, appended to the end of both lists."""
        item = f"118. **{title}** — required when changing a fixture. Run: `true`\n"
        claude_md = (cls.repo / "CLAUDE.md").read_text(encoding="utf-8")
        marker = "\n## Experiments\n"
        if marker not in claude_md:
            raise AssertionError("CLAUDE.md lost its ## Experiments heading; fix this fixture")
        testing_md = (cls.repo / "docs/TESTING.md").read_text(encoding="utf-8")
        return {
            "CLAUDE.md": claude_md.replace(marker, item + marker, 1),
            "docs/TESTING.md": testing_md.rstrip("\n") + "\n\n" + item,
        }

    def _merge_both(self, first, second):
        """Merge two branches onto main in a throwaway branch; return the second merge."""
        scratch = f"merge-{first}-{second}"
        _git(self.repo, "checkout", "-q", "-f", "-B", scratch, "main")
        _git(self.repo, "merge", "-q", "--no-edit", first)
        result = _git(self.repo, "merge", "-q", "--no-edit", second, check=False)
        self.addCleanup(lambda: _git(self.repo, "merge", "--abort", check=False))
        return result

    def _run_gate(self, gate):
        """Run a parity gate from the checked-out tree, so it reads that tree."""
        return subprocess.run(
            [sys.executable, str(self.repo / "claude/scripts/tests" / gate)],
            cwd=str(self.repo), capture_output=True, encoding="utf-8", errors="replace",
            env=_env(), timeout=120,
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
        bare = Path(self._tmp.name) / "bare.git"
        if not bare.exists():
            _git(self._tmp.name, "clone", "-q", "--bare", str(self.repo), str(bare))
        for first, second in (("new-a", "new-b"), ("new-b", "new-a")):
            with self.subTest(order=f"{first} then {second}"):
                result = _git(bare, "merge-tree", "--write-tree", first, second, check=False)
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

    def test_gate_refuses_a_new_numbered_item(self):
        _git(self.repo, "checkout", "-q", "-f", "old-a")
        run = self._run_gate("test_testing_index_parity.py")
        self.assertNotEqual(run.returncode, 0, "item 118 passed the parity gate")
        self.assertIn("Required when", run.stdout + run.stderr,
                      "the failure must say what to do instead of taking a number")

    def test_gate_refuses_a_new_test_with_no_entry(self):
        _git(self.repo, "checkout", "-q", "-f", "no-entry")
        run = self._run_gate("test_readme_index_parity.py")
        self.assertNotEqual(run.returncode, 0, "a test with no entry passed the README gate")
        self.assertIn("test_fixture_bare.py", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
