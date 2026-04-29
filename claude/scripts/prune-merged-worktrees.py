#!/usr/bin/env python3
"""Remove claude/* worktrees whose branches have been merged into origin/main.

Safe: skips the current worktree, dirty worktrees, and any non-claude/* branch.
Uses git branch -d (not -D) and git worktree remove (no --force).

Usage:
  python claude/scripts/prune-merged-worktrees.py [--dry-run]
"""
import subprocess
import sys
from pathlib import Path


REPO = "C:/Users/brown/Git/dev-env"
BRANCH_PREFIX = "claude/"


def run(args: list[str], cwd: str = REPO, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30, check=check)


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


def is_merged(branch: str) -> bool:
    # Regular merge: commit is an ancestor of origin/main
    r = run(["git", "merge-base", "--is-ancestor", branch, "origin/main"])
    if r.returncode == 0:
        return True
    # Squash merge: commit SHA diverges from main — ask GitHub instead
    r = run(["gh", "pr", "list", "--repo", "brownm09/dev-env",
             "--head", branch, "--state", "merged", "--json", "number", "--limit", "1"])
    if r.returncode == 0 and r.stdout.strip() not in ("", "[]"):
        return True
    return False


def is_dirty(path: str) -> bool:
    r = run(["git", "status", "--porcelain"], cwd=path)
    return bool(r.stdout.strip())


def current_worktree_path() -> str:
    r = run(["git", "rev-parse", "--show-toplevel"])
    return str(Path(r.stdout.strip()).resolve())


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[dry-run] no changes will be made")

    # Fetch origin/main so merge checks are accurate
    print("Fetching origin/main…")
    run(["git", "fetch", "origin", "main"], check=True)

    result = run(["git", "worktree", "list", "--porcelain"])
    if result.returncode != 0:
        print("ERROR: git worktree list failed:", result.stderr, file=sys.stderr)
        sys.exit(1)

    worktrees = parse_worktrees(result.stdout)
    cwd = current_worktree_path()

    pruned: list[str] = []
    skipped: list[tuple[str, str]] = []

    for wt in worktrees:
        branch = wt["branch"]
        path = str(Path(wt["path"]).resolve())

        if not branch.startswith(BRANCH_PREFIX):
            skipped.append((path, f"branch '{branch}' not in {BRANCH_PREFIX}* prefix"))
            continue

        if path == cwd:
            skipped.append((path, "current worktree"))
            continue

        if not is_merged(branch):
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
