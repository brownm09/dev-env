#!/usr/bin/env python3
"""Remove claude/* worktrees whose branches have been merged into origin/main.

Also parks non-primary worktrees accidentally checked out on main back onto their own
claude/<slug> branch (recreated at the worktree's current commit) — e.g. after
`gh pr merge --delete-branch` from a worktree checks main out there while the canonical
is momentarily off main. Squatting main locks the ref: it blocks gh's local post-merge
checkout for every other worktree's merge and stops the canonical ~/Git/dev-env from
returning to main, leaving newly-merged hooks/scripts inert in the live ~/.claude/
(dev-env#396, ADR-058). Parking is non-destructive — `git checkout -b` frees the ref
without changing any working-tree files, so it frees main even for a dirty squatter that
the old `git worktree remove` refused. The freed worktree is removed on a later run by
the normal merged-branch path once it is idle and clean.

Safe: skips the current worktree, dirty worktrees, live-session worktrees (ADR-051), and
      any non-claude/* branch (unless that branch is main, which is parked off — main
      cannot have unmerged work by definition).
Uses git branch -d (not -D), git worktree remove (no --force), and git checkout -b (parking).

Auto-detects the GitHub repo slug from the remote URL, so the script works
correctly in any repo — not just brownm09/dev-env.

Usage:
  python claude/scripts/prune-merged-worktrees.py [--dry-run] [--repo-path /path/to/repo]
  python claude/scripts/prune-merged-worktrees.py [--dry-run] --scan-dir /path/to/dir

  --repo-path  Target a specific repo's worktrees (defaults to the dev-env repo).
               Example: --repo-path C:/Users/brown/Git/lifting-logbook
  --scan-dir   Discover and prune all git repos directly under the given directory.
               Skips repos with no GitHub remote or no claude/* worktrees.
               Example: --scan-dir C:/Users/brown/Git
  --liveness-window-min N
               Skip any worktree whose Claude transcript was written within the last N
               minutes (an active session). Defaults to 1440 (24h). See ADR-051.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import os
import re
import subprocess
import sys
from pathlib import Path

from _worktree_liveness import parse_liveness_window_seconds, worktree_session_is_live
from _worktree_topology import main_squatter, park_branch_for, parse_worktree_porcelain


# Default: the repo that owns this script (dev-env). Override with --repo-path.
_DEFAULT_REPO = str(Path(__file__).resolve().parents[2])
BRANCH_PREFIX = "claude/"

# Skip a worktree whose Claude session wrote its transcript within this window — removing
# a live session's worktree severs it mid-task (dev-env#384). 24h, not the shorter reclaim
# window: `git worktree remove` is destructive, so the long guard is warranted; the only
# cost is a merged worktree lingering up to a day longer. Override with --liveness-window-min.
LIVENESS_WINDOW_SECONDS = 24 * 60 * 60


def _repo_from_args() -> str:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--repo-path":
            if i + 1 < len(sys.argv):
                return str(Path(sys.argv[i + 1]).resolve())
            sys.exit("--repo-path requires an argument")
    return _DEFAULT_REPO


def _scan_dir_from_args() -> str | None:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--scan-dir":
            if i + 1 < len(sys.argv):
                return str(Path(sys.argv[i + 1]).resolve())
            sys.exit("--scan-dir requires an argument")
    return None


def _liveness_window_seconds_from_args() -> int:
    try:
        return parse_liveness_window_seconds(sys.argv[1:], LIVENESS_WINDOW_SECONDS)
    except ValueError as exc:
        sys.exit(str(exc))


def run(args: list[str], cwd: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=check)


def find_git_repos(scan_dir: str) -> list[str]:
    """Return paths of primary git repos (with a .git directory) directly under scan_dir."""
    repos = []
    try:
        entries = sorted(os.scandir(scan_dir), key=lambda e: e.name.lower())
    except PermissionError as exc:
        print(f"WARNING: cannot scan {scan_dir}: {exc}", file=sys.stderr)
        return repos
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        git_path = Path(entry.path) / ".git"
        # .git is a directory for primary repos; a file for git worktrees — skip worktrees
        if git_path.is_dir():
            repos.append(entry.path)
    return repos


def detect_gh_repo(repo: str) -> str:
    """Return 'owner/repo' derived from the origin remote URL of repo."""
    r = run(["git", "remote", "get-url", "origin"], cwd=repo)
    url = r.stdout.strip()
    # Matches both https://github.com/owner/repo(.git) and git@github.com:owner/repo(.git)
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    raise RuntimeError(f"Cannot parse GitHub repo from remote URL: {url!r}")


def is_merged(branch: str, gh_repo: str, repo: str) -> bool:
    # Regular merge: commit is an ancestor of origin/main
    r = run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=repo)
    if r.returncode == 0:
        return True
    # Squash merge: commit SHA diverges from main — ask GitHub instead
    r = run(["gh", "pr", "list", "--repo", gh_repo,
             "--head", branch, "--state", "merged", "--json", "number", "--limit", "1"], cwd=repo)
    if r.returncode == 0 and r.stdout.strip() not in ("", "[]"):
        return True
    return False


def is_dirty(path: str) -> bool:
    if not Path(path).exists():
        return True
    r = run(["git", "status", "--porcelain"], cwd=path)
    return bool(r.stdout.strip())


def primary_worktree_path(worktrees: list[dict]) -> str:
    """The primary worktree is always the first entry from 'git worktree list'."""
    return str(Path(worktrees[0]["path"]).resolve()) if worktrees else ""


def prune_one(repo: str, dry_run: bool, liveness_window_seconds: int) -> tuple[int, int, bool]:
    """Prune merged claude/* worktrees in a single repo. Returns (pruned, skipped, fetch_failed)."""
    try:
        gh_repo = detect_gh_repo(repo)
    except RuntimeError as exc:
        print(f"  SKIP {repo}: {exc}")
        return 0, 0, False

    print(f"\nRepo: {gh_repo} ({repo})")

    # Fetch origin/main so merge checks are accurate
    fetch_failed = False
    r = run(["git", "fetch", "origin", "main"], cwd=repo)
    if r.returncode != 0:
        fetch_failed = True
        print(f"  WARNING: fetch failed — merge checks may use stale origin/main: {r.stderr.strip()}")

    result = run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        print(f"  ERROR: git worktree list failed: {result.stderr}", file=sys.stderr)
        return 0, 0, fetch_failed

    worktrees = parse_worktree_porcelain(result.stdout)
    primary = primary_worktree_path(worktrees)
    cwd = str(Path(os.getcwd()).resolve())

    # Identify the squatter (if any) using the topology helper — handles bare/detached
    # canonicals that legitimately yield a secondary worktree on main (dev-env#399, ADR-058).
    # A bare/detached primary can't hold a working-tree checkout of main itself, so a
    # secondary worktree on main there is the real home of main and must NOT be parked.
    # main_squatter() returns None in that case; the naive `branch == "main"` check did not.
    squatter = main_squatter(worktrees)
    squatter_path = str(Path(squatter["path"]).resolve()) if squatter else None

    pruned: list[str] = []
    skipped: list[tuple[str, str]] = []

    for wt in worktrees:
        branch = wt["branch"]
        path = str(Path(wt["path"]).resolve())

        # Always skip the primary worktree and wherever this process is running
        if path == primary or path == cwd:
            skipped.append((path, "primary or current worktree"))
            continue

        # Skip a worktree with a live Claude session (recent transcript activity). The
        # cwd guard above only covers THIS process; an out-of-process routine cannot see
        # another worktree's active session except via its transcript mtime — removing a
        # live worktree severs the session mid-task (dev-env#383, ADR-051). Additive: this
        # only ever adds a skip, never removes more than the merged/clean checks below.
        if worktree_session_is_live(path, window_seconds=liveness_window_seconds):
            skipped.append((path, "active Claude session (recent transcript activity)"))
            continue

        # Non-primary worktree squatting main: park it back onto its own claude/<slug>
        # branch (recreated at HEAD) to free the main ref. Non-destructive — `git
        # checkout -b` changes no working-tree files, so it frees main even for a dirty
        # squatter that `git worktree remove` (no --force) would refuse. The freed
        # worktree is removed on a later run via the normal merged path once idle+clean
        # (dev-env#396, ADR-058). The ADR-051 liveness guard above already spared a live
        # squatter, so parking only ever moves an idle one.
        if path == squatter_path:
            park = park_branch_for(path)
            if dry_run:
                pruned.append(path)
                print(f"  [dry-run] would park stale main checkout off main: {path} -> {park}")
                continue
            r = run(["git", "-C", path, "checkout", "-b", park], cwd=repo)
            if r.returncode != 0:
                skipped.append((path, f"park off main failed (branch {park} may already exist): {r.stderr.strip()}"))
                continue
            pruned.append(path)
            print(f"  parked off main: {path} ({park}) — freed the main ref")
            continue

        if not branch.startswith(BRANCH_PREFIX):
            skipped.append((path, f"branch '{branch}' not in {BRANCH_PREFIX}* prefix"))
            continue

        if not is_merged(branch, gh_repo, repo):
            skipped.append((path, "not merged into origin/main"))
            continue

        if is_dirty(path):
            skipped.append((path, "has uncommitted changes"))
            continue

        if dry_run:
            pruned.append(path)
            print(f"  [dry-run] would remove: {path} ({branch})")
            continue

        # Use a generous timeout: git worktree remove runs an internal untracked-file scan
        # that is slow when node_modules is present. With timeout=30 the scan aborts the
        # entire run (dev-env#350); 300s lets even a 1 GB worktree complete. TimeoutExpired
        # is caught so one slow removal skips that worktree and continues the scan.
        try:
            r = run(["git", "worktree", "remove", path], cwd=repo, timeout=300)
        except subprocess.TimeoutExpired:
            skipped.append((path, "git worktree remove timed out — worktree may be large; retry manually"))
            continue
        if r.returncode != 0:
            skipped.append((path, f"worktree remove failed: {r.stderr.strip()}"))
            continue

        r = run(["git", "branch", "-d", branch], cwd=repo)
        if r.returncode != 0:
            # Worktree already gone; branch delete failure is non-fatal
            print(f"  WARNING: branch delete failed for {branch}: {r.stderr.strip()}")

        pruned.append(path)
        print(f"  pruned: {path} ({branch})")

    suffix = " [fetch failed — results may use stale origin/main]" if fetch_failed else ""
    print(f"  Done — pruned {len(pruned)}, skipped {len(skipped)}{suffix}")
    if skipped:
        for path, reason in skipped:
            print(f"    skipped {path}: {reason}")

    return len(pruned), len(skipped), fetch_failed


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    scan_dir = _scan_dir_from_args()
    liveness_window_seconds = _liveness_window_seconds_from_args()

    if dry_run:
        print("[dry-run] no changes will be made")

    if scan_dir:
        repos = find_git_repos(scan_dir)
        if not repos:
            print(f"No git repos found under {scan_dir}")
            sys.exit(0)
        print(f"Found {len(repos)} repos under {scan_dir}")
        total_pruned = total_skipped = 0
        fetch_failed_repos: list[str] = []
        for repo in repos:
            p, s, ff = prune_one(repo, dry_run, liveness_window_seconds)
            total_pruned += p
            total_skipped += s
            if ff:
                fetch_failed_repos.append(repo)
        summary = f"\nTotal — pruned {total_pruned}, skipped {total_skipped}"
        if fetch_failed_repos:
            summary += f", fetch failed in {len(fetch_failed_repos)} repo(s): {', '.join(fetch_failed_repos)}"
        print(summary)
    else:
        repo = _repo_from_args()
        prune_one(repo, dry_run, liveness_window_seconds)


if __name__ == "__main__":
    main()
