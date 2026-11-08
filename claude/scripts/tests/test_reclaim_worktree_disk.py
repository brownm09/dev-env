#!/usr/bin/env python3
"""Tests for reclaim-worktree-disk.py: pure logic, plus its command line.

The test_cli_* tests (dev-env#1310) run the real script against a throwaway repo from
_throwaway_repo.py, to pin that --help, an undefined flag and a stray argument change nothing,
with a control run proving that worktree really is reclaimable. They also check the argument
lists the three hooks really pass, because those hooks discard the script's stderr.

The rest cover the parts that decide *whether* and *what* to reclaim — independent of git
and the filesystem layout of real worktrees:

  1. is_idle_eligible() decision table (merged / commits-ahead combinations).
  2. find_reclaim_dirs() — discovers top-level and nested node_modules/.turbo,
     does not descend into a reclaim dir, and skips .git.
  3. dir_size_bytes() — sums file sizes under a tree.
  4. reclaim_worktree() — dry-run reports bytes without deleting; real run deletes
     the reclaim dirs and leaves everything else (including .git) intact.

Beyond the control run's single eligible worktree, the eligibility guards that depend on git
(dirty check, primary/cwd exclusion, merge detection) are exercised end-to-end by the --dry-run
verification in the PR, not here.

Usage:
    py -3 claude/scripts/tests/test_reclaim_worktree_disk.py

Exit 0 = all pass.
"""
import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import types
import unittest.mock
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
            if not r.stdout.isascii():
                raise AssertionError(f"{flag}: usage is not ASCII, so a console code page could break it")
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


def test_is_dirty_does_not_refresh_the_index() -> str:
    """The prune reads the index's mtime as activity, so reclaim's scan must not renew it."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = ThrowawayRepo(Path(tmp))
        index = repo.admin_dir / "index"
        stamp = repo.age_git_state()
        (repo.worktree / ".gitignore").touch()
        if rwd.is_dirty(str(repo.worktree)):
            raise AssertionError("touching a file without changing it should read as clean")
        if abs(index.stat().st_mtime - stamp) > 1:
            raise AssertionError("is_dirty rewrote the index")
        repo._git(repo.worktree, "status", "--porcelain")
        if abs(index.stat().st_mtime - stamp) <= 1:
            raise AssertionError("plain git status left the index alone, so this test proves nothing")
    return "is_dirty leaves the index's mtime alone where a plain git status moves it"


def test_cli_control_dry_run_keeps_and_plain_run_reclaims() -> str:
    """Proves the two tests above can fail: this same worktree really is eligible."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = ThrowawayRepo(Path(tmp))
        r = repo.run_script(MODULE_PATH, "--dry-run")
        if r.returncode != 0 or "would reclaim" not in r.stdout or not repo.node_modules.exists():
            raise AssertionError(f"--dry-run: exit {r.returncode}, should report and keep\n{r.stdout}\n{r.stderr}")
        repo.assert_targets_repo(r)
        if not repo.fetch_head.exists():
            raise AssertionError("--dry-run wrote no FETCH_HEAD, so its absence proves nothing above")
        r = repo.run_script(MODULE_PATH)
        if r.returncode != 0 or repo.node_modules.exists():
            raise AssertionError(f"plain run: exit {r.returncode}, should reclaim\n{r.stdout}\n{r.stderr}")
    return "--dry-run keeps node_modules; a plain run deletes it"


def test_real_non_main_default_branch_is_fetched_and_reclaimed() -> str:
    """dev-env#1088: zero commits ahead of origin/master, which the script used to never fetch."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = ThrowawayRepo(Path(tmp), default_branch="master")
        r = repo.run_script(MODULE_PATH, "--dry-run")
        repo.assert_targets_repo(r)
        if "fetch failed" in r.stdout or "would reclaim" not in r.stdout:
            raise AssertionError(f"--dry-run should fetch master and see an idle worktree\n{r.stdout}")
        r = repo.run_script(MODULE_PATH)
        if repo.node_modules.exists():
            raise AssertionError(f"plain run should reclaim\n{r.stdout}\n{r.stderr}")
    return "origin/HEAD unset, ls-remote finds master, node_modules is reclaimed"


def test_scan_continues_after_fetch_timeout_and_unexpected_error() -> str:
    """dev-env#1087 known-bad references: each of these used to end the run at the first repo."""
    mod = _load_module()
    repo_a, repo_b = str(Path("/FAKE_A").resolve()), str(Path("/FAKE_B").resolve())

    def make_dispatch(fail_args: list, exception: Exception, seen: list):
        def dispatch(args, cwd=None, **_kwargs):
            seen.append(cwd)
            if cwd == repo_a and args[1:1 + len(fail_args)] == fail_args:
                raise exception
            if args[1:2] == ["remote"]:
                return types.SimpleNamespace(returncode=0, stdout="git@github.com:o/r.git\n", stderr="")
            if args[1:2] == ["symbolic-ref"]:
                return types.SimpleNamespace(returncode=0, stdout="refs/remotes/origin/main\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return dispatch

    def scan(dispatch) -> tuple:
        out, code = io.StringIO(), 0
        with unittest.mock.patch("subprocess.run", side_effect=dispatch), \
                unittest.mock.patch.object(mod, "find_git_repos", return_value=[repo_a, repo_b]), \
                unittest.mock.patch.object(sys, "argv", ["reclaim", "--scan-dir", "/FAKE_SCAN"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                mod.main()
            except SystemExit as exc:
                code = exc.code
        return out.getvalue(), code

    seen: list = []
    timeout = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=30)
    out, code = scan(make_dispatch(["fetch"], timeout, seen))
    if repo_b not in seen or "fetch timed out" not in out or code:
        raise AssertionError(f"fetch timeout: reached B={repo_b in seen}, exit {code}\n{out}")

    seen = []
    out, code = scan(make_dispatch(["worktree", "list"], OSError("boom"), seen))
    if repo_b not in seen or f"error in 1 repo(s): {repo_a}" not in out or code != 1:
        raise AssertionError(f"unexpected error: reached B={repo_b in seen}, exit {code}\n{out}")
    return "repo B reached both times; a timed-out fetch warns and exits 0, an unexpected error is named and exits 1"


def _load_hook(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reclaim_args_from(mock_call) -> list:
    """The arguments a hook passed to the reclaim script, without the interpreter and path."""
    argv = mock_call.args[0]
    if Path(argv[1]).name != "reclaim-worktree-disk.py":
        raise AssertionError(f"hook did not spawn the reclaim script: {argv}")
    return argv[2:]


def test_cli_parser_accepts_existing_callers() -> str:
    # The hooks discard the script's stderr, so a rejected flag would silently stop reclaim.
    # Capture what each hook really passes rather than copying its flags here.
    for name in ("disk-space-check", "post-pr-merge-reclaim"):
        hook = _load_hook(name)
        with unittest.mock.patch("subprocess.Popen") as popen:
            hook._spawn_reclaim("C:/wt")
        args = rwd._parse_args(_reclaim_args_from(popen.call_args))
        if not (args.scan_dir and args.protect_cwd == "C:/wt"):
            raise AssertionError(f"{name} call misparsed: {args}")
    hook = _load_hook("worktree-npm-install")
    with unittest.mock.patch("subprocess.run") as run, \
            unittest.mock.patch.object(hook, "_free_gb", return_value=1000.0):
        hook._run_reclaim_ladder("C:/wt")
    args = rwd._parse_args(_reclaim_args_from(run.call_args_list[0]))
    if not (args.scan_dir and args.min_free_gb is not None and args.protect_cwd == "C:/wt"):
        raise AssertionError(f"worktree-npm-install call misparsed: {args}")
    # claude/routines/reclaim-worktree-disk/SKILL.md
    args = rwd._parse_args(["--scan-dir", "C:/Users/brown/Git"])
    if not (args.scan_dir and args.min_free_gb is None and args.protect_cwd is None and not args.dry_run):
        raise AssertionError(f"routine call misparsed: {args}")
    if rwd._parse_args([]).liveness_window_seconds != rwd.LIVENESS_WINDOW_SECONDS:
        raise AssertionError("absent --liveness-window-min did not give the default")
    return "the three hooks' real argument lists and the routine call parse as expected"


def test_cli_parser_rejects_abbreviation_and_bad_values() -> str:
    # Parser level only: a regression that ignored an abbreviated --repo-pat or an empty
    # --repo-path, run for real, would go live on the real dev-env repo.
    with contextlib.redirect_stderr(io.StringIO()):
        for argv in (["--dry"], ["--repo-pat", "C:/x"], ["--repo-path", ""], ["--scan-dir", ""],
                     ["--min-free-gb", "abc"], ["--min-free-gb", "nan"], ["--min-free-gb", "inf"],
                     ["--liveness-window-min", "-5"], ["--liveness-window-min", "abc"]):
            try:
                rwd._parse_args(argv)
            except SystemExit as exc:
                if exc.code != 2:
                    raise AssertionError(f"{argv} exited {exc.code}, expected 2") from None
            else:
                raise AssertionError(f"{argv} was accepted")
    return "abbreviations, empty paths, non-finite --min-free-gb and bad liveness windows exit 2"


def main() -> int:
    tests = [
        ("real repo: non-main default branch fetched and reclaimed", test_real_non_main_default_branch_is_fetched_and_reclaimed),
        ("scan: fetch timeout or unexpected error -> next repo reached", test_scan_continues_after_fetch_timeout_and_unexpected_error),
        ("CLI: --help and -h change nothing", test_cli_help_changes_nothing),
        ("CLI: unknown flag or stray argument changes nothing", test_cli_rejects_unknown_arguments),
        ("CLI control: --dry-run keeps, plain run reclaims", test_cli_control_dry_run_keeps_and_plain_run_reclaims),
        ("is_dirty does not refresh the index", test_is_dirty_does_not_refresh_the_index),
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
