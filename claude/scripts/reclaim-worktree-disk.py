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

  --repo-path     Target a specific repo's worktrees (defaults to the dev-env repo).
  --scan-dir      Discover and process all git repos directly under the given directory.
  --dry-run       Report what would be reclaimed without deleting anything.
  --min-free-gb N No-op unless free space on the target drive is below N GB. Lets a
                  threshold hook invoke this cheaply without always doing work.
  --protect-cwd P Never strip this worktree (defaults to os.getcwd()). A hook that
                  spawns this script detached passes the active session's worktree so
                  the dir in use is never touched even in --scan-dir mode.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


# Default: the repo that owns this script (dev-env). Override with --repo-path.
_DEFAULT_REPO = str(Path(__file__).resolve().parents[2])

# Regenerable directory names stripped from eligible worktrees. node_modules can be
# reinstalled (worktree-npm-install.py / npm ci); .turbo is a rebuildable cache.
RECLAIM_DIR_NAMES = ("node_modules", ".turbo")


def _arg_value(flag: str) -> str | None:
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == flag:
            if i + 1 < len(sys.argv):
                return sys.argv[i + 1]
            sys.exit(f"{flag} requires an argument")
    return None


def _repo_from_args() -> str:
    val = _arg_value("--repo-path")
    return str(Path(val).resolve()) if val else _DEFAULT_REPO


def _scan_dir_from_args() -> str | None:
    val = _arg_value("--scan-dir")
    return str(Path(val).resolve()) if val else None


def _min_free_gb_from_args() -> float | None:
    val = _arg_value("--min-free-gb")
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        sys.exit("--min-free-gb requires a numeric argument")


def _protect_cwd_from_args() -> str:
    val = _arg_value("--protect-cwd")
    base = val if val else os.getcwd()
    return str(Path(base).resolve())


def run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)


def find_git_repos(scan_dir: str) -> list[str]:
    """Return paths of primary git repos (with a .git directory) directly under scan_dir."""
    repos: list[str] = []
    try:
        entries = sorted(os.scandir(scan_dir), key=lambda e: e.name.lower())
    except (PermissionError, FileNotFoundError) as exc:
        print(f"WARNING: cannot scan {scan_dir}: {exc}", file=sys.stderr)
        return repos
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        git_path = Path(entry.path) / ".git"
        # .git is a directory for primary repos; a file for git worktrees — skip worktrees.
        if git_path.is_dir():
            repos.append(entry.path)
    return repos


def detect_gh_repo(repo: str) -> str:
    """Return 'owner/repo' derived from the origin remote URL of repo."""
    r = run(["git", "remote", "get-url", "origin"], cwd=repo)
    url = r.stdout.strip()
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


def is_merged(branch: str, gh_repo: str, repo: str) -> bool:
    # Regular merge: commit is an ancestor of origin/main.
    r = run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=repo)
    if r.returncode == 0:
        return True
    # Squash merge: commit SHA diverges from main — ask GitHub instead.
    r = run(["gh", "pr", "list", "--repo", gh_repo,
             "--head", branch, "--state", "merged", "--json", "number", "--limit", "1"], cwd=repo)
    if r.returncode == 0 and r.stdout.strip() not in ("", "[]"):
        return True
    return False


def commits_ahead(branch: str, repo: str) -> int | None:
    """Number of commits `branch` is ahead of origin/main, or None if undeterminable."""
    r = run(["git", "rev-list", "--count", f"origin/main..{branch}"], cwd=repo)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def is_dirty(path: str) -> bool:
    if not Path(path).exists():
        return True
    r = run(["git", "status", "--porcelain"], cwd=path)
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


def reclaim_one(repo: str, dry_run: bool, protect_cwd: str) -> int:
    """Reclaim from idle worktrees in a single repo. Returns total bytes reclaimed."""
    try:
        gh_repo = detect_gh_repo(repo)
    except RuntimeError as exc:
        print(f"  SKIP {repo}: {exc}")
        return 0

    print(f"\nRepo: {gh_repo} ({repo})")

    r = run(["git", "fetch", "origin", "main"], cwd=repo)
    if r.returncode != 0:
        print(f"  WARNING: fetch failed — merge/ahead checks may use stale origin/main: {r.stderr.strip()}")

    result = run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        print(f"  ERROR: git worktree list failed: {result.stderr}", file=sys.stderr)
        return 0

    worktrees = parse_worktrees(result.stdout)
    primary = primary_worktree_path(worktrees)

    total = 0
    skipped: list[tuple[str, str]] = []

    for wt in worktrees:
        branch = wt["branch"]
        path = str(Path(wt["path"]).resolve())

        if path == primary or path == protect_cwd:
            skipped.append((path, "primary or protected worktree"))
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
        ahead = commits_ahead(branch, repo)
        merged = ahead != 0 and is_merged(branch, gh_repo, repo)
        if not is_idle_eligible(merged, ahead):
            skipped.append((path, f"{ahead if ahead is not None else '?'} commit(s) ahead, not merged"))
            continue

        bytes_here = reclaim_worktree(path, dry_run)
        total += bytes_here
        verb = "[dry-run] would reclaim" if dry_run else "reclaimed"
        print(f"  {verb} {_fmt_gb(bytes_here)} from {path} ({branch})")

    print(f"  Done — {'would reclaim' if dry_run else 'reclaimed'} {_fmt_gb(total)}, skipped {len(skipped)}")
    for path, reason in skipped:
        print(f"    skipped {path}: {reason}")

    return total


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    scan_dir = _scan_dir_from_args()
    protect_cwd = _protect_cwd_from_args()
    min_free = _min_free_gb_from_args()

    if dry_run:
        print("[dry-run] no changes will be made")

    # Threshold gate: when --min-free-gb is set, do nothing unless the drive is below it.
    if min_free is not None:
        probe = scan_dir or _repo_from_args()
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
        for repo in repos:
            grand_total += reclaim_one(repo, dry_run, protect_cwd)
        print(f"\nTotal — {'would reclaim' if dry_run else 'reclaimed'} {_fmt_gb(grand_total)}")
    else:
        reclaim_one(_repo_from_args(), dry_run, protect_cwd)


if __name__ == "__main__":
    main()
