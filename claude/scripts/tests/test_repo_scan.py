#!/usr/bin/env python3
"""Unit tests for _repo_scan.py — shared find_git_repos() directory-scan helper.

_repo_scan.py is the shared module backing --scan-dir mode in prune-merged-worktrees.py,
reclaim-worktree-disk.py, and reconcile-project-board.py, extracted from three
near-identical copies (dev-env#471, ADR-070's deferred follow-up). See ADR-072.

Exercises the pure, filesystem-only helper offline (real tmp dirs, no subprocess, no
mocking) — matching the fixture-only convention of test_reclaim_worktree_disk.py and
test_worktree_topology.py. find_git_repos() catches the broad OSError (see _repo_scan.py)
so that FileNotFoundError, NotADirectoryError, and PermissionError are all handled
identically; the two cases below that are reliably triggerable without mocking
(a missing path, and a path that is a file) each exercise a different OSError subtype
through the same except-and-return-None branch. PermissionError itself is not separately
exercised, since reliably triggering one portably (esp. on Windows) would require mocking
os.scandir for no additional branch coverage.

Usage:
    py -3 claude/scripts/tests/test_repo_scan.py

Exit 0 = all pass.
"""
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _repo_scan


def _make_repo(root: Path, name: str) -> None:
    """A primary repo: .git is a directory."""
    (root / name / ".git").mkdir(parents=True)


def _make_worktree(root: Path, name: str) -> None:
    """A git worktree: .git is a file, not a directory."""
    d = root / name
    d.mkdir(parents=True)
    (d / ".git").write_text("gitdir: /somewhere/else\n", encoding="utf-8")


def test_finds_primary_repos_skips_worktrees_and_non_repos() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_repo(root, "repo-a")
        _make_repo(root, "repo-b")
        _make_worktree(root, "worktree-c")
        (root / "not-a-repo").mkdir()  # plain dir, no .git at all
        (root / "some-file.txt").write_text("x", encoding="utf-8")  # a file, not a dir

        found = _repo_scan.find_git_repos(str(root))

        expected = {str((root / "repo-a").resolve()), str((root / "repo-b").resolve())}
        got = {str(Path(p).resolve()) for p in found}
        if got != expected:
            raise AssertionError(f"found {sorted(got)}, expected {sorted(expected)}")
    return "found only primary repos (.git dir); skipped worktree (.git file), plain dir, and a file"


def test_case_insensitive_sort_order() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("Zebra", "apple", "Banana"):
            _make_repo(root, name)

        found = _repo_scan.find_git_repos(str(root))
        names = [Path(p).name for p in found]
        if names != ["apple", "Banana", "Zebra"]:
            raise AssertionError(f"order {names}, expected case-insensitive alphabetical")
    return "entries sorted case-insensitively by name (apple, Banana, Zebra)"


def test_nonexistent_scan_dir_returns_none() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "does-not-exist"
        result = _repo_scan.find_git_repos(str(missing))
        if result is not None:
            raise AssertionError(f"expected None for a missing scan_dir, got {result!r}")
    return "a nonexistent scan_dir returns None (not [], and does not raise)"


def test_scan_dir_is_a_file_returns_none() -> str:
    """A scan_dir that exists but is a file (not a directory) raises NotADirectoryError,
    a sibling OSError subclass to PermissionError/FileNotFoundError — must be caught too."""
    with tempfile.TemporaryDirectory() as tmp:
        a_file = Path(tmp) / "not-a-directory.txt"
        a_file.write_text("x", encoding="utf-8")
        result = _repo_scan.find_git_repos(str(a_file))
        if result is not None:
            raise AssertionError(f"expected None when scan_dir is a file, got {result!r}")
    return "a scan_dir that is a file (NotADirectoryError) returns None, does not raise"


def test_empty_existing_dir_returns_empty_list() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        result = _repo_scan.find_git_repos(tmp)
        if result != []:
            raise AssertionError(f"expected [] for an empty existing dir, got {result!r}")
        if result is None:
            raise AssertionError("an empty existing dir must return [], not None")
    return "an empty but readable scan_dir returns [] — distinct from the None (unreadable) case"


def main() -> int:
    tests = [
        ("finds primary repos; skips worktrees/non-repos", test_finds_primary_repos_skips_worktrees_and_non_repos),
        ("case-insensitive sort order", test_case_insensitive_sort_order),
        ("nonexistent scan_dir -> None", test_nonexistent_scan_dir_returns_none),
        ("scan_dir is a file -> None", test_scan_dir_is_a_file_returns_none),
        ("empty existing dir -> [] (not None)", test_empty_existing_dir_returns_empty_list),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
