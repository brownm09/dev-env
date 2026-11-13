#!/usr/bin/env python3
"""Reclaim regenerable disk artifacts (node_modules, .turbo) from idle worktrees.

Claude-managed worktrees each carry a full copy of the project's node_modules.
On a large monorepo that is 1-2 GB per worktree; with dozens of stale worktrees
accumulating between weekly prune runs, C: can fill to saturation (dev-env#306:
476G used, 0 bytes free — ENOSPC blocked npm install, builds, git, even writing a
plan file).

node_modules and .turbo are fully regenerable: the worktree-npm-install.py hook
(ADR-016) reinstalls node_modules on the next prompt in any Claude-managed
worktree. So stripping them from idle worktrees is safe and self-healing — the
bulk of the duplicated dependencies is reclaimed without removing the worktree
itself, and a worktree that is picked up again simply reinstalls on first use.

Eligibility (a worktree is stripped only when ALL hold):
  - it is not the primary worktree,
  - it is not the protected cwd (the active session's worktree),
  - its working tree is clean (no uncommitted or untracked changes), AND
  - its branch is merged into origin/main OR has zero commits ahead of origin/main.

"main" and "origin/main" here stand for each repo's discovered default branch (see
_default_branch.py, dev-env#1088).

Dirty worktrees and worktrees with unpushed commits ahead of main are never
touched — only artifacts that are trivially regenerable AND belong to idle work
are removed.

This complements prune-merged-worktrees.py: that script removes the worktree
*directory* once its branch is merged; this script reclaims the heavy regenerable
artifacts from worktrees that are idle but not yet eligible for removal.

Usage:
  python claude/scripts/reclaim-worktree-disk.py [--dry-run] [--repo-path /path/to/repo]
  python claude/scripts/reclaim-worktree-disk.py [--dry-run] --scan-dir /path/to/dir
  python claude/scripts/reclaim-worktree-disk.py --scan-dir /path --min-free-gb 10
  python claude/scripts/reclaim-worktree-disk.py --scan-dir /path --protect-cwd /path/to/active/worktree

  An undefined flag or stray argument exits 2 before any git command; --help prints usage
  and exits 0 (dev-env#1310).

  --repo-path     Target a specific repo's worktrees (defaults to the dev-env repo).
  --scan-dir      Discover and process all git repos directly under the given directory.
  --dry-run       Report what would be reclaimed without deleting anything.
  --min-free-gb N No-op unless free space on the target drive is below N GB. Lets a
                  threshold hook invoke this cheaply without always doing work.
  --protect-cwd P Never strip this worktree (defaults to os.getcwd()). A hook that
                  spawns this script detached passes the active session's worktree so
                  the dir in use is never touched even in --scan-dir mode.
  --liveness-window-min N
                  Skip any worktree whose Claude transcript was written within the last N
                  minutes (an active session), protecting live sessions in *other*
                  worktrees that --protect-cwd cannot reach. Defaults to 360 (6h). ADR-051.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from _default_branch import fetch_default_branch
from _repo_scan import find_git_repos
from _worktree_liveness import liveness_minutes_arg, worktree_session_is_live
from _worktree_topology import parse_worktree_porcelain


# Default: the repo that owns this script (dev-env). Override with --repo-path.
_DEFAULT_REPO = str(Path(__file__).resolve().parents[2])

# Regenerable directory names stripped from eligible worktrees. node_modules can be
# reinstalled (worktree-npm-install.py / npm ci); .turbo is a rebuildable cache.
RECLAIM_DIR_NAMES = ("node_modules", ".turbo")

# Skip a worktree whose Claude session wrote its transcript within this window — stripping
# node_modules out from under a live build/dev-server breaks it (dev-env#384). 6h, shorter
# than prune's 24h: stripping is self-healing (worktree-npm-install reinstalls on next use)
# and only disrupts a process running *now*, so the short window keeps disk reclamation
# aggressive against ENOSPC (ADR-037/045) and matches the 6-hourly routine cadence.
# --protect-cwd only shields THIS session; this guard shields live sessions in *other*
# worktrees the routine cannot otherwise see. Override with --liveness-window-min.
LIVENESS_WINDOW_SECONDS = 6 * 60 * 60


def _nonempty(value: str) -> str:
    """Reject an empty path: an unset shell variable must not select a repo nobody named."""
    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _finite_gb(value: str) -> float:
    """Parse --min-free-gb. NaN compares false to everything, which would disable the gate."""
    try:
        gb = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(gb):
        raise argparse.ArgumentTypeError("must be a finite number")
    return gb


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line, exiting on --help or on anything undefined (dev-env#1310).

    This must run before the first git command: this script deletes directories, so a flag it
    does not understand has to stop it. Abbreviations are off so a shortened flag is an error
    rather than a guess. The flag descriptions stay ASCII so --help can't fail on a console's
    code page; the module docstring has the full detail.
    """
    parser = argparse.ArgumentParser(
        description="Delete node_modules and .turbo from idle Claude worktrees.",
        allow_abbrev=False,
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would be reclaimed, delete nothing")
    parser.add_argument("--repo-path", metavar="PATH", type=_nonempty,
                        help="target this repo (default: the dev-env repo)")
    parser.add_argument("--scan-dir", metavar="DIR", type=_nonempty,
                        help="target every git repo directly under DIR")
    parser.add_argument("--min-free-gb", metavar="N", type=_finite_gb,
                        help="do nothing unless the target drive has under N GB free")
    parser.add_argument("--protect-cwd", metavar="PATH",
                        help="never strip this worktree (default: the current directory)")
    parser.add_argument("--liveness-window-min", metavar="N", dest="liveness_window_seconds",
                        type=liveness_minutes_arg, default=LIVENESS_WINDOW_SECONDS,
                        help="skip worktrees with a Claude transcript written in the last N minutes "
                             f"(default: {LIVENESS_WINDOW_SECONDS // 60})")
    return parser.parse_args(argv)


def run(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def detect_gh_repo(repo: str) -> str:
    """Return 'owner/repo' derived from the origin remote URL of repo."""
    r = run(["git", "remote", "get-url", "origin"], cwd=repo)
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    raise RuntimeError(f"Cannot parse GitHub repo from remote URL: {url!r}")


def is_merged(branch: str, gh_repo: str, repo: str, base: str) -> bool:
    # Regular merge: commit is an ancestor of origin/<base>.
    r = run(["git", "merge-base", "--is-ancestor", branch, f"origin/{base}"], cwd=repo)
    if r.returncode == 0:
        return True
    # Squash merge: commit SHA diverges from main — ask GitHub instead.
    r = run(["gh", "pr", "list", "--repo", gh_repo,
             "--head", branch, "--state", "merged", "--json", "number", "--limit", "1"], cwd=repo)
    if r.returncode == 0 and r.stdout.strip() not in ("", "[]"):
        return True
    return False


def commits_ahead(branch: str, repo: str, base: str) -> int | None:
    """Number of commits `branch` is ahead of origin/<base>, or None if undeterminable."""
    r = run(["git", "rev-list", "--count", f"origin/{base}..{branch}"], cwd=repo)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def is_dirty(path: str) -> bool:
    if not Path(path).exists():
        return True
    # --no-optional-locks: a plain status can rewrite the index, which the prune reads as recent
    # activity in the worktree (ADR-051 Amendment 1).
    r = run(["git", "--no-optional-locks", "status", "--porcelain"], cwd=path)
    return bool(r.stdout.strip())


def primary_worktree_path(worktrees: list[dict]) -> str:
    """The primary worktree is always the first entry from 'git worktree list'."""
    return str(Path(worktrees[0]["path"]).resolve()) if worktrees else ""


def is_claude_managed_worktree(path: str) -> bool:
    """True when path lives under a `.claude/worktrees/` directory.

    These are the worktrees whose node_modules is auto-reinstalled by
    worktree-npm-install.py (ADR-016) on next use — so stripping their
    regenerable artifacts is self-healing. Manual sibling clones / worktrees
    outside `.claude/worktrees/` are excluded: nothing auto-reinstalls them, so
    we leave their node_modules in place. Mirrors the path test in
    worktree-npm-install.py: `.claude` and `worktrees` as consecutive components.
    """
    parts = Path(path).parts
    try:
        idx = parts.index(".claude")
    except ValueError:
        return False
    return idx + 1 < len(parts) and parts[idx + 1] == "worktrees"


def is_idle_eligible(merged: bool, ahead: int | None) -> bool:
    """Eligible to strip when the branch is merged OR has zero commits ahead of main.

    Pure decision helper (caller supplies dirty/primary/cwd guards separately).
    `ahead is None` (undeterminable, e.g. detached HEAD or missing origin/main) is
    treated as NOT zero-ahead — conservative: do not strip when ahead-count is unknown
    unless the branch is independently known to be merged.
    """
    if merged:
        return True
    return ahead == 0


def dir_size_bytes(path: Path) -> int:
    """Total size in bytes of all files under path (best-effort; unreadable files skipped)."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def find_reclaim_dirs(worktree: str) -> list[Path]:
    """Top-level and nested node_modules/.turbo directories within a worktree.

    Walks the tree but does NOT descend into a reclaim dir once found (the bytes of
    nested node_modules inside a node_modules are already counted by dir_size_bytes),
    and skips the .git directory. Returns directories in a delete-safe order.
    """
    found: list[Path] = []
    root = Path(worktree)
    for dirpath, dirnames, _files in os.walk(root, followlinks=False):
        # Never descend into .git.
        if ".git" in dirnames:
            dirnames.remove(".git")
        hits = [d for d in dirnames if d in RECLAIM_DIR_NAMES]
        for d in hits:
            found.append(Path(dirpath) / d)
        # Do not descend into reclaim dirs — their contents are reclaimed wholesale.
        for d in hits:
            dirnames.remove(d)
    return found


def reclaim_worktree(worktree: str, dry_run: bool) -> int:
    """Delete reclaim dirs in a worktree. Returns bytes reclaimed (or that would be).

    Totals are best-effort under concurrent runs: the 6-hourly routine and the
    <10 GB threshold hook can both target the same worktree. Size is tallied
    before rmtree, so two overlapping runs may each count the same bytes; the
    loser's rmtree raises OSError (already-deleted) and is logged, never fatal.
    """
    reclaimed = 0
    for d in find_reclaim_dirs(worktree):
        size = dir_size_bytes(d)
        if dry_run:
            reclaimed += size
            continue
        try:
            shutil.rmtree(d, ignore_errors=False)
            reclaimed += size
        except OSError as exc:
            print(f"    WARNING: failed to remove {d}: {exc}", file=sys.stderr)
    return reclaimed


def _fmt_gb(n_bytes: int) -> str:
    return f"{n_bytes / (1024 ** 3):.2f} GB"


def free_gb(path: str) -> float:
    """Free space in GB on the drive containing path."""
    return shutil.disk_usage(path).free / (1024 ** 3)


def reclaim_one(repo: str, dry_run: bool, protect_cwd: str, liveness_window_seconds: int) -> "tuple[int, list[str]]":
    """Reclaim from idle worktrees in a single repo. Returns (total bytes reclaimed, the
    worktrees an unexpected exception stopped) (dev-env#1344)."""
    try:
        gh_repo = detect_gh_repo(repo)
    except RuntimeError as exc:
        print(f"  SKIP {repo}: {exc}")
        return 0, []

    print(f"\nRepo: {gh_repo} ({repo})")

    default_branch, fetch_problem, fetch_detail, _ = fetch_default_branch(repo)
    if fetch_problem:
        print(f"  WARNING: fetch {fetch_problem} — merge/ahead checks may use stale origin/{default_branch}: {fetch_detail}")

    result = run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        print(f"  ERROR: git worktree list failed: {result.stderr}", file=sys.stderr)
        return 0, []

    worktrees = parse_worktree_porcelain(result.stdout)
    primary = primary_worktree_path(worktrees)

    total = 0
    skipped: list[tuple[str, str]] = []
    errors: list[str] = []

    for wt in worktrees:
        branch = wt["branch"]
        path = str(Path(wt["path"]).resolve())

        # An unexpected error is this worktree's alone: the repo's other worktrees are still
        # handled and counted (dev-env#1344).
        try:
            if path == primary or path == protect_cwd:
                skipped.append((path, "primary or protected worktree"))
                continue
            # Skip a worktree with a live Claude session (recent transcript activity).
            # --protect-cwd only shields THIS session; stripping node_modules out from under a
            # build/dev-server running in *another* worktree breaks it (dev-env#383, ADR-051).
            # Additive: only ever adds a skip, never reclaims more than the checks below.
            if worktree_session_is_live(path, window_seconds=liveness_window_seconds):
                skipped.append((path, "active Claude session (recent transcript activity)"))
                continue
            if not is_claude_managed_worktree(path):
                skipped.append((path, "not under .claude/worktrees/ (no auto-reinstall safety net)"))
                continue
            if is_dirty(path):
                skipped.append((path, "has uncommitted changes"))
                continue
            if not branch or branch == "<detached>":
                # No resolvable branch ref: ahead/merged checks would evaluate the
                # range against the primary repo's HEAD (run cwd=repo), silently
                # misclassifying. Conservative — never strip when the branch is unknown.
                skipped.append((path, "no resolvable branch (detached or unnamed)"))
                continue

            # Compute the cheap local ahead-count first and short-circuit: an idle
            # worktree (zero commits ahead of origin/main) is eligible without the
            # network `gh pr list` fallback that is_merged() may trigger. is_merged()
            # is only needed to catch squash-merged branches that still show as ahead.
            ahead = commits_ahead(branch, repo, default_branch)
            merged = ahead != 0 and is_merged(branch, gh_repo, repo, default_branch)
            if not is_idle_eligible(merged, ahead):
                skipped.append((path, f"{ahead if ahead is not None else '?'} commit(s) ahead, not merged"))
                continue

            bytes_here = reclaim_worktree(path, dry_run)
            total += bytes_here
            verb = "[dry-run] would reclaim" if dry_run else "reclaimed"
            print(f"  {verb} {_fmt_gb(bytes_here)} from {path} ({branch})")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {path}: {type(exc).__name__}: {exc} -- stopped partway, so check its state; "
                  f"its bytes are not in the total")
            traceback.print_exc()
            errors.append(path)

    error_note = f", errors {len(errors)}" if errors else ""
    print(f"  Done — {'would reclaim' if dry_run else 'reclaimed'} {_fmt_gb(total)}, skipped {len(skipped)}{error_note}")
    for path, reason in skipped:
        print(f"    skipped {path}: {reason}")

    return total, errors


def main() -> None:
    # A path or branch name the stream can't encode must print escaped, not raise after the
    # worktree was already counted (dev-env#1358).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = _parse_args(sys.argv[1:])
    dry_run = args.dry_run
    scan_dir = str(Path(args.scan_dir).resolve()) if args.scan_dir else None
    single_repo = str(Path(args.repo_path).resolve()) if args.repo_path else _DEFAULT_REPO
    protect_cwd = str(Path(args.protect_cwd or os.getcwd()).resolve())
    min_free = args.min_free_gb
    liveness_window_seconds = args.liveness_window_seconds

    if dry_run:
        print("[dry-run] no changes will be made")

    # Threshold gate: when --min-free-gb is set, do nothing unless the drive is below it.
    if min_free is not None:
        probe = scan_dir or single_repo
        current_free = free_gb(probe)
        if current_free >= min_free:
            print(f"Free space {current_free:.2f} GB >= threshold {min_free:.2f} GB — nothing to do.")
            sys.exit(0)
        print(f"Free space {current_free:.2f} GB < threshold {min_free:.2f} GB — reclaiming.")

    if scan_dir:
        repos = find_git_repos(scan_dir)
        if not repos:
            print(f"No git repos found under {scan_dir}")
            sys.exit(0)
        print(f"Found {len(repos)} repos under {scan_dir}")
        grand_total = 0
        error_repos: list[str] = []
        for repo in repos:
            # One repo's hung git call or unexpected error must not stop the sweep (dev-env#1087).
            try:
                reclaimed, errors = reclaim_one(repo, dry_run, protect_cwd, liveness_window_seconds)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR: {repo}: {type(exc).__name__}: {exc} -- its bytes are not in the total")
                traceback.print_exc()
                error_repos.append(repo)
                continue
            grand_total += reclaimed
            if errors:
                error_repos.append(repo)
        summary = f"\nTotal — {'would reclaim' if dry_run else 'reclaimed'} {_fmt_gb(grand_total)}"
        if error_repos:
            summary += f", error in {len(error_repos)} repo(s): {', '.join(error_repos)}"
        print(summary)
        if error_repos:
            sys.exit(1)
    else:
        _, errors = reclaim_one(single_repo, dry_run, protect_cwd, liveness_window_seconds)
        if errors:
            sys.exit(1)


if __name__ == "__main__":
    main()
