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
import contextlib
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

from _throwaway_repo import ThrowawayRepo

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


# --- Command-line handling (dev-env#1310). An unknown flag, or --help, used to run a live
# reclaim. These run the real script against a throwaway repo.

def _assert_repo_untouched(repo: ThrowawayRepo, label: str, result) -> None:
    if not repo.node_modules.exists():
        raise AssertionError(f"{label}: node_modules was deleted\n{result.stdout}")
    if repo.fetch_head.exists():
        raise AssertionError(f"{label}: git fetch ran before the arguments were checked")


def test_cli_help_changes_nothing() -> str:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = ThrowawayRepo(Path(tmp))
        for flag in ("--help", "-h"):
            r = repo.run_script(MODULE_PATH, flag)
            _assert_repo_untouched(repo, flag, r)
            if r.returncode != 0:
                raise AssertionError(f"{flag}: exit {r.returncode}, expected 0\n{r.stderr}")
            for name in ("--dry-run", "--repo-path", "--scan-dir", "--min-free-gb",
                         "--protect-cwd", "--liveness-window-min"):
                if name not in r.stdout:
                    raise AssertionError(f"{flag}: usage does not name {name}\n{r.stdout}")
    return "--help and -h print usage, exit 0, leave node_modules and FETCH_HEAD alone"


def test_cli_rejects_unknown_arguments() -> str:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = ThrowawayRepo(Path(tmp))
        for arg in ("--bogus", "--dryrun", "stray"):
            r = repo.run_script(MODULE_PATH, arg)
            _assert_repo_untouched(repo, arg, r)
            if r.returncode == 0:
                raise AssertionError(f"{arg}: exit 0, expected non-zero\n{r.stdout}")
    return "undefined flags and a stray argument exit non-zero before any git command"


def test_cli_control_dry_run_keeps_and_plain_run_reclaims() -> str:
    """Proves the two tests above can fail: this same worktree really is eligible."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = ThrowawayRepo(Path(tmp))
        r = repo.run_script(MODULE_PATH, "--dry-run")
        if r.returncode != 0 or "would reclaim" not in r.stdout or not repo.node_modules.exists():
            raise AssertionError(f"--dry-run: exit {r.returncode}, should report and keep\n{r.stdout}\n{r.stderr}")
        r = repo.run_script(MODULE_PATH)
        if r.returncode != 0 or repo.node_modules.exists():
            raise AssertionError(f"plain run: exit {r.returncode}, should reclaim\n{r.stdout}\n{r.stderr}")
    return "--dry-run keeps node_modules; a plain run deletes it"


def test_cli_parser_accepts_existing_callers() -> str:
    # disk-space-check.py and worktree-npm-install.py
    args = rwd._parse_args(["--scan-dir", "C:/Users/brown/Git", "--min-free-gb", "10", "--protect-cwd", "C:/wt"])
    if not (args.scan_dir and args.min_free_gb == 10.0 and args.protect_cwd == "C:/wt"):
        raise AssertionError(f"hook call misparsed: {args}")
    # post-pr-merge-reclaim.py and claude/routines/reclaim-worktree-disk/SKILL.md
    args = rwd._parse_args(["--scan-dir", "C:/Users/brown/Git"])
    if not (args.scan_dir and args.min_free_gb is None and args.protect_cwd is None and not args.dry_run):
        raise AssertionError(f"routine call misparsed: {args}")
    return "hook and routine calls parse to the expected values"


def test_cli_parser_rejects_abbreviation_and_bad_values() -> str:
    # In-process only: a regression run for real, with no --repo-path, would hit dev-env.
    with contextlib.redirect_stderr(io.StringIO()):
        for argv in (["--dry"], ["--min-free-gb", "abc"]):
            try:
                rwd._parse_args(argv)
            except SystemExit as exc:
                if exc.code == 0:
                    raise AssertionError(f"{argv} exited 0") from None
            else:
                raise AssertionError(f"{argv} was accepted")
    for value in ("-5", "abc"):
        try:
            rwd._liveness_window_seconds(value)
        except SystemExit:
            continue
        raise AssertionError(f"--liveness-window-min {value} was accepted")
    if rwd._liveness_window_seconds(None) != rwd.LIVENESS_WINDOW_SECONDS:
        raise AssertionError("absent --liveness-window-min did not give the default")
    return "--dry and a non-numeric --min-free-gb rejected; bad windows exit; absent -> default"


def main() -> int:
    tests = [
        ("CLI: --help and -h change nothing", test_cli_help_changes_nothing),
        ("CLI: unknown flag or stray argument changes nothing", test_cli_rejects_unknown_arguments),
        ("CLI control: --dry-run keeps, plain run reclaims", test_cli_control_dry_run_keeps_and_plain_run_reclaims),
        ("CLI parser: existing callers still parse", test_cli_parser_accepts_existing_callers),
        ("CLI parser: abbreviation and bad values rejected", test_cli_parser_rejects_abbreviation_and_bad_values),
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
