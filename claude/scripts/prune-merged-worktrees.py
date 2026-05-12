#!/usr/bin/env python3
"""Remove claude/* worktrees whose branches have been merged into origin/main.

Also removes non-primary worktrees accidentally checked out on main (e.g. after a
session runs 'git checkout main' as part of post-merge cleanup, locking main for
other checkouts like VSCode's branch switcher).

Safe: skips the current worktree, dirty worktrees, and any non-claude/* branch
      (unless that branch is main, which is always safe to remove from a non-primary
       worktree since main cannot have unmerged work by definition).
Uses git branch -d (not -D) and git worktree remove (no --force).

Auto-detects the GitHub repo slug from the remote URL, so the script works
correctly in any repo — not just brownm09/dev-env.

Usage:
  python claude/scripts/prune-merged-worktrees.py [--dry-run] [--repo-path /path/to/repo]

  --repo-path  Target a different repo's worktrees (defaults to the dev-env repo).
               Example: --repo-path C:/Users/brown/Git/lifting-logbook
"""
import os
import re
import subprocess
import sys
from pathlib import Path


# Default: the repo that owns this script (dev-env). Override with --repo-path.
_DEFAULT_REPO = str(Path(__file__).resolve().parents[2])
BRANCH_PREFIX = "claude/"


def _repo_from_args() -> str:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--repo-path" and i < len(sys.argv):
            return str(Path(sys.argv[i + 1]).resolve())
    return _DEFAULT_REPO


REPO = _repo_from_args()


def run(args: list[str], cwd: str = REPO, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30, check=check)


def detect_gh_repo() -> str:
    """Return 'owner/repo' derived from the origin remote URL of REPO."""
    r = run(["git", "remote", "get-url", "origin"])
    url = r.stdout.strip()
    # Matches both https://github.com/owner/repo(.git) and git@github.com:owner/repo(.git)
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    raise RuntimeError(f"Cannot parse GitHub repo from remote URL: {url!r}")


def parse_worktrees(output: str) -> list[dict]:
    worktrees: list[dict] = []
    current: dict | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = {"path": line[len("worktree "):].strip(), "branch": ""}
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):].strip()
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line == "detached" and current is not None:
            current["branch"] = "<detached>"
    if current is not None:
        worktrees.append(current)
    return worktrees


def is_merged(branch: str, gh_repo: str) -> bool:
    # Regular merge: commit is an ancestor of origin/main
    r = run(["git", "merge-base", "--is-ancestor", branch, "origin/main"])
    if r.returncode == 0:
        return True
    # Squash merge: commit SHA diverges from main — ask GitHub instead
    r = run(["gh", "pr", "list", "--repo", gh_repo,
             "--head", branch, "--state", "merged", "--json", "number", "--limit", "1"])
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
    return str(Path(worktrees[0]["path"]).resolve()) if worktrees else REPO


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[dry-run] no changes will be made")

    gh_repo = detect_gh_repo()
    print(f"Repo: {gh_repo}")

    # Fetch origin/main so merge checks are accurate
    print("Fetching origin/main…")
    run(["git", "fetch", "origin", "main"], check=True)

    result = run(["git", "worktree", "list", "--porcelain"])
    if result.returncode != 0:
        print("ERROR: git worktree list failed:", result.stderr, file=sys.stderr)
        sys.exit(1)

    worktrees = parse_worktrees(result.stdout)
    primary = primary_worktree_path(worktrees)
    cwd = str(Path(os.getcwd()).resolve())

    pruned: list[str] = []
    skipped: list[tuple[str, str]] = []

    for wt in worktrees:
        branch = wt["branch"]
        path = str(Path(wt["path"]).resolve())

        # Always skip the primary worktree and wherever this process is running
        if path == primary or path == cwd:
            skipped.append((path, "primary or current worktree"))
            continue

        # Non-primary worktrees checked out on main: always safe to remove — main
        # cannot contain unmerged work, and the checkout just locks the branch name.
        if branch == "main":
            if dry_run:
                pruned.append(path)
                print(f"  [dry-run] would remove stale main checkout: {path}")
                continue
            r = run(["git", "worktree", "remove", path])
            if r.returncode != 0:
                skipped.append((path, f"worktree remove failed: {r.stderr.strip()}"))
                continue
            pruned.append(path)
            print(f"  pruned (stale main): {path}")
            continue

        if not branch.startswith(BRANCH_PREFIX):
            skipped.append((path, f"branch '{branch}' not in {BRANCH_PREFIX}* prefix"))
            continue

        if path == cwd:
            skipped.append((path, "current worktree"))
            continue

        if not is_merged(branch, gh_repo):
            skipped.append((path, "not merged into origin/main"))
            continue

        if is_dirty(path):
            skipped.append((path, "has uncommitted changes"))
            continue

        if dry_run:
            pruned.append(path)
            print(f"  [dry-run] would remove: {path} ({branch})")
            continue

        r = run(["git", "worktree", "remove", path])
        if r.returncode != 0:
            skipped.append((path, f"worktree remove failed: {r.stderr.strip()}"))
            continue

        r = run(["git", "branch", "-d", branch])
        if r.returncode != 0:
            # Worktree already gone; branch delete failure is non-fatal
            print(f"  WARNING: branch delete failed for {branch}: {r.stderr.strip()}")

        pruned.append(path)
        print(f"  pruned: {path} ({branch})")

    print(f"\nDone — pruned {len(pruned)}, skipped {len(skipped)}")
    if skipped:
        print("Skipped:")
        for path, reason in skipped:
            print(f"  {path}: {reason}")


if __name__ == "__main__":
    main()
