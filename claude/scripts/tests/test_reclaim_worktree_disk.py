#!/usr/bin/env python3
"""Unit tests for reclaim-worktree-disk.py pure logic.

Covers the parts that decide *whether* and *what* to reclaim — independent of git
and the filesystem layout of real worktrees:

  1. is_idle_eligible() decision table (merged / commits-ahead combinations).
  2. find_reclaim_dirs() — discovers top-level and nested node_modules/.turbo,
     does not descend into a reclaim dir, and skips .git.
  3. dir_size_bytes() — sums file sizes under a tree.
  4. reclaim_worktree() — dry-run reports bytes without deleting; real run deletes
     the reclaim dirs and leaves everything else (including .git) intact.

The eligibility guards that *do* depend on git (dirty check, primary/cwd exclusion,
merge detection) are exercised end-to-end by the --dry-run verification in the PR,
not here — these tests target the deterministic, side-effect-free helpers.

Usage:
    py -3 claude/scripts/tests/test_reclaim_worktree_disk.py

Exit 0 = all pass.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "reclaim-worktree-disk.py"

# The module's first line is `import _winsubp`; ensure scripts/ is importable.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("reclaim_worktree_disk", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rwd = _load_module()


def _make_worktree(root: Path) -> None:
    """Build a fake worktree tree with node_modules at two depths, .turbo, .git, and real files."""
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "node_modules").mkdir()  # must NOT be reclaimed (inside .git)
    (root / ".git" / "node_modules" / "x").write_bytes(b"0" * 100)

    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_bytes(b"0" * 1000)
    # Nested node_modules INSIDE node_modules — already counted; walker must not list it separately.
    (root / "node_modules" / "pkg" / "node_modules").mkdir()
    (root / "node_modules" / "pkg" / "node_modules" / "dep.js").write_bytes(b"0" * 500)

    (root / ".turbo").mkdir()
    (root / ".turbo" / "cache.bin").write_bytes(b"0" * 250)

    # A nested workspace package with its own node_modules (monorepo shape).
    (root / "packages" / "ui" / "node_modules").mkdir(parents=True)
    (root / "packages" / "ui" / "node_modules" / "lib.js").write_bytes(b"0" * 2000)
    (root / "packages" / "ui" / "src.ts").write_bytes(b"0" * 42)  # real source — keep

    (root / "package.json").write_bytes(b"{}")  # real file — keep


def test_is_idle_eligible_decision_table() -> str:
    cases = [
        # (merged, ahead, expected)
        (True, 0, True),     # merged, no commits ahead
        (True, 5, True),     # merged wins even if ahead-count looks nonzero
        (True, None, True),  # merged wins even if ahead undeterminable
        (False, 0, True),    # not merged but zero ahead → idle
        (False, 1, False),   # unpushed work ahead → never strip
        (False, 7, False),
        (False, None, False),  # ahead unknown and not merged → conservative skip
    ]
    for merged, ahead, expected in cases:
        got = rwd.is_idle_eligible(merged, ahead)
        if got != expected:
            raise AssertionError(
                f"is_idle_eligible(merged={merged}, ahead={ahead}) = {got}, expected {expected}"
            )
    return f"{len(cases)} eligibility combinations classified correctly"


def test_is_claude_managed_worktree() -> str:
    cases = [
        # (path, expected)
        ("C:/Users/brown/Git/lifting-logbook/.claude/worktrees/foo-bar", True),
        ("C:/Users/brown/Git/dev-env/.claude/worktrees/friendly-raman-128b07", True),
        ("C:/Users/brown/Git/dev-env", False),            # primary repo root
        ("C:/Users/brown/Git/dev-env-188", False),         # manual sibling worktree
        ("C:/Users/brown/.claude/scripts", False),         # .claude but not /worktrees
        ("C:/Users/brown/Git/repo/.claude", False),        # .claude is the last component
    ]
    for path, expected in cases:
        got = rwd.is_claude_managed_worktree(path)
        if got != expected:
            raise AssertionError(
                f"is_claude_managed_worktree({path!r}) = {got}, expected {expected}"
            )
    return f"{len(cases)} worktree paths classified by .claude/worktrees membership"


def test_find_reclaim_dirs_discovers_all_and_skips_git() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_worktree(root)
        found = {p.resolve() for p in rwd.find_reclaim_dirs(str(root))}
        expected = {
            (root / "node_modules").resolve(),
            (root / ".turbo").resolve(),
            (root / "packages" / "ui" / "node_modules").resolve(),
        }
        if found != expected:
            raise AssertionError(
                f"find_reclaim_dirs mismatch.\n  found:    {sorted(map(str, found))}\n"
                f"  expected: {sorted(map(str, expected))}"
            )
        # The nested node_modules inside node_modules must NOT be listed separately.
        nested = (root / "node_modules" / "pkg" / "node_modules").resolve()
        if nested in found:
            raise AssertionError("walker descended into a reclaim dir and listed a nested node_modules")
        # The node_modules inside .git must NOT be listed.
        git_nm = (root / ".git" / "node_modules").resolve()
        if git_nm in found:
            raise AssertionError(".git/node_modules was incorrectly listed for reclamation")
    return "found top-level + nested-workspace reclaim dirs; skipped .git and reclaim-dir descent"


def test_dir_size_bytes_sums_tree() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a").mkdir()
        (root / "a" / "f1").write_bytes(b"0" * 100)
        (root / "a" / "b").mkdir()
        (root / "a" / "b" / "f2").write_bytes(b"0" * 250)
        size = rwd.dir_size_bytes(root)
        if size != 350:
            raise AssertionError(f"dir_size_bytes = {size}, expected 350")
    return "dir_size_bytes sums all files across nested dirs"


def test_reclaim_worktree_dry_run_reports_without_deleting() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_worktree(root)
        reported = rwd.reclaim_worktree(str(root), dry_run=True)
        # Sum of reclaim-dir contents: node_modules(1000+500) + .turbo(250) + ui/node_modules(2000) = 3750
        if reported != 3750:
            raise AssertionError(f"dry-run reported {reported} bytes, expected 3750")
        # Nothing deleted.
        if not (root / "node_modules").exists():
            raise AssertionError("dry-run deleted node_modules")
        if not (root / ".turbo").exists():
            raise AssertionError("dry-run deleted .turbo")
        if not (root / "packages" / "ui" / "node_modules").exists():
            raise AssertionError("dry-run deleted nested workspace node_modules")
    return "dry-run reported 3750 bytes and deleted nothing"


def test_reclaim_worktree_deletes_only_reclaim_dirs() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_worktree(root)
        reclaimed = rwd.reclaim_worktree(str(root), dry_run=False)
        if reclaimed != 3750:
            raise AssertionError(f"reclaimed {reclaimed} bytes, expected 3750")
        # Reclaim dirs gone.
        for gone in ["node_modules", ".turbo", "packages/ui/node_modules"]:
            if (root / gone).exists():
                raise AssertionError(f"{gone} still present after reclamation")
        # Real files and .git preserved.
        if not (root / "package.json").exists():
            raise AssertionError("package.json was deleted")
        if not (root / "packages" / "ui" / "src.ts").exists():
            raise AssertionError("workspace source file was deleted")
        if not (root / ".git").exists():
            raise AssertionError(".git was deleted")
        if not (root / ".git" / "node_modules").exists():
            raise AssertionError(".git/node_modules was deleted (walker must skip .git)")
    return "deleted node_modules/.turbo (incl. nested workspace); preserved source, package.json, .git"


def main() -> int:
    tests = [
        ("is_idle_eligible decision table", test_is_idle_eligible_decision_table),
        ("is_claude_managed_worktree path gate", test_is_claude_managed_worktree),
        ("find_reclaim_dirs discovers all + skips .git", test_find_reclaim_dirs_discovers_all_and_skips_git),
        ("dir_size_bytes sums tree", test_dir_size_bytes_sums_tree),
        ("reclaim_worktree dry-run reports, deletes nothing", test_reclaim_worktree_dry_run_reports_without_deleting),
        ("reclaim_worktree deletes only reclaim dirs", test_reclaim_worktree_deletes_only_reclaim_dirs),
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
