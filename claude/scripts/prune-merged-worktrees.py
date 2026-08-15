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

Also parks (and, when safe, removes outright) any non-primary worktree squatting an
engineering-journal draft/YYYY-MM-DD branch — the daily Stub file workflow's own shared
canonical-only branch (claude/CLAUDE.md -> Engineering Journal -> Stub file workflow). A
squatting worktree here blocks the canonical (and every other concurrent stub-writing
session) from reaching that day's draft branch at all, not just this repo's own main
(dev-env#747, ADR-105). Checked unconditionally, across every repo this script scans --
the branch-pattern match naturally only ever fires against engineering-journal.

Safe: skips the current worktree, dirty worktrees, live-session worktrees (ADR-051), and
      any non-claude/* branch (unless that branch is main, which is parked off — main
      cannot have unmerged work by definition) unless --include-named is passed, in which
      case a non-claude/* branch falls through to the same merged/dirty/liveness checks
      instead of being unconditionally skipped (ADR-078; off by default, zero behavior
      change for existing callers). Also treats a branch as merged if its
      entire diff vs. origin/main matches a per-repo opt-in prune_ephemeral_patterns
      config (see .claude/hook-config.json; ADR-075) — an additional signal that stays
      off unless a repo explicitly configures it.
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
  --include-named
               Opt-in. Off by default (zero behavior change for existing callers). When
               passed, a worktree whose branch does NOT start with claude/ is no longer
               unconditionally skipped by the prefix guard -- it instead falls through to
               the SAME is_merged() / ephemeral-diff / is_dirty() checks claude/* branches
               already go through, and is pruned the same way (git worktree remove + git
               branch -d) if they pass. Every other protection (primary/cwd skip, live-session
               skip, main-squatter parking) still runs first, unconditionally, for named
               branches too. See ADR-078.

Per-repo opt-in config (.claude/hook-config.json):
  "prune_ephemeral_patterns": [<regex strings>]
               A branch not otherwise detected as merged is still treated as merged if
               every file in its diff vs. origin/main matches at least one of these
               regexes. For a workflow where a throwaway branch's content is folded into
               another branch and then deleted (e.g. engineering-journal's
               journal-compose, which deletes per-session scaffolding from main after
               composing it into a canonical doc), that scaffolding is safe to discard
               even though the branch itself was never merged or given its own PR.
               Absent or empty -> feature off (default, zero behavior change). See
               ADR-075.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from _repo_scan import find_git_repos
from _worktree_liveness import parse_liveness_window_seconds, worktree_session_is_live
from _worktree_topology import (
    DETACHED,
    DRAFT_BRANCH_RE,
    main_squatter,
    non_canonical_worktrees_matching,
    park_branch_for,
    parse_worktree_porcelain,
    pattern_squat_action,
)


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


def _include_named_from_args() -> bool:
    return "--include-named" in sys.argv[1:]


def run(args: list[str], cwd: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=check)


def detect_gh_repo(repo: str) -> str:
    """Return 'owner/repo' derived from the origin remote URL of repo."""
    r = run(["git", "remote", "get-url", "origin"], cwd=repo)
    url = r.stdout.strip()
    # Matches both https://github.com/owner/repo(.git) and git@github.com:owner/repo(.git)
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    raise RuntimeError(f"Cannot parse GitHub repo from remote URL: {url!r}")


def is_merged(branch: str, gh_repo: str, repo: str, skip_pr_fallback: bool = False) -> bool:
    # Regular merge: commit is an ancestor of origin/main
    r = run(["git", "merge-base", "--is-ancestor", branch, "origin/main"], cwd=repo)
    if r.returncode == 0:
        return True
    if skip_pr_fallback:
        # `branch` here is a raw commit SHA (resolved from a detached HEAD, dev-env#979) --
        # gh pr list --head matches a PR's head BRANCH NAME, which a SHA can never equal, so
        # this call is guaranteed to return no match. Skipping it avoids burning an unnecessary
        # GraphQL request against this repo's shared, exhaustible rate-limit budget (dev-env#769)
        # on every detached worktree scanned, with no loss of detection accuracy.
        return False
    # Squash merge: commit SHA diverges from main — ask GitHub instead
    r = run(["gh", "pr", "list", "--repo", gh_repo,
             "--head", branch, "--state", "merged", "--json", "number", "--limit", "1"], cwd=repo)
    if r.returncode == 0 and r.stdout.strip() not in ("", "[]"):
        return True
    return False


def resolve_detached_head(path: str, repo: str) -> "str | None":
    """Resolve a detached-HEAD worktree's actual checked-out commit SHA.

    A detached worktree's HEAD is not a shared ref (unlike refs/heads/*, which live in the
    repo's shared .git dir) -- it exists only as that worktree's own HEAD file, so it must be
    resolved with cwd set to the worktree's own path, not the canonical repo's. Returns None
    on any git failure so the caller can fail safe (dev-env#979) -- including a registered-but
    -deleted worktree directory (an "orphaned" worktree, per the worktree-recovery runbook in
    claude/CLAUDE.md): unlike every other check in the prune loop, this is the first point that
    runs git with cwd set to the worktree's OWN path rather than the canonical repo's, so a
    missing path here would otherwise raise FileNotFoundError instead of failing gracefully --
    mirroring the same guard is_dirty() already has for the identical reason.
    """
    if not Path(path).exists():
        return None
    r = run(["git", "rev-parse", "HEAD"], cwd=path)
    sha = r.stdout.strip()
    if r.returncode != 0 or not sha:
        return None
    return sha


def is_dirty(path: str) -> bool:
    if not Path(path).exists():
        return True
    r = run(["git", "status", "--porcelain"], cwd=path)
    return bool(r.stdout.strip())


def _origin_ahead_count(branch: str, repo: str) -> "int | None":
    """Commits on `branch` not yet on `origin/<branch>`, or None if unresolvable (remote
    branch missing, fetch failed). Caller treats None as "not provably fully pushed" — the
    conservative default, matching pattern_squat_action's own park-only fallback. Used for
    draft-branch-squat eligibility (dev-env#747, ADR-105), which cannot reuse is_merged()'s
    origin/main ancestor check: a composed draft branch reaches main via a fresh squash
    commit (ADR-082), never a fast-forward or matching PR head, so is_merged() would never
    fire for it.
    """
    fetch = run(["git", "fetch", "origin", branch], cwd=repo)
    if fetch.returncode != 0:
        return None
    r = run(["git", "rev-list", "--count", f"origin/{branch}..{branch}"], cwd=repo)
    if r.returncode != 0 or not r.stdout.strip().isdigit():
        return None
    return int(r.stdout.strip())


CONFIG_FILE = ".claude/hook-config.json"


def diff_files(branch: str, repo: str) -> list[str]:
    """Files changed on branch relative to origin/main, via git diff --name-only.

    Uses the three-dot form (origin/main...branch, i.e. diff against the merge-base, not
    origin/main's current tip) — origin/main normally moves on while a worktree sits idle,
    so two-dot (direct tip-to-tip) would silently change semantics. Returns [] on any git
    failure -- the caller treats an empty list as "no files to prove ephemeral", which the
    empty-patterns guard already makes safe: it can only ever suppress a prune, never force
    one.
    """
    r = run(["git", "diff", "--name-only", f"origin/main...{branch}"], cwd=repo)
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def files_are_all_ephemeral(files: list[str], patterns: list[str]) -> bool:
    """True when every file in files matches at least one regex in patterns.

    Empty patterns -> always False (an unconfigured repo's diff is never treated as
    ephemeral -- this is the opt-in gate). Empty files -> also False (nothing to prove
    ephemeral; a zero-diff branch is already caught by is_merged()'s ancestor check before
    this function is ever consulted). A malformed regex propagates re.error to the caller,
    which catches it per-repo (see load_ephemeral_patterns) so one bad pattern cannot raise
    mid-scan.
    """
    if not patterns or not files:
        return False
    compiled = [re.compile(p) for p in patterns]
    return all(any(c.search(f) for c in compiled) for f in files)


def load_ephemeral_patterns(repo: str) -> list[str]:
    """Read prune_ephemeral_patterns (list[str] of regexes) from repo's hook-config.json.

    Fail-open to [] (feature off) on: missing file, an unreadable file (any OSError -- e.g. a
    transient PermissionError from a Windows antivirus/indexer lock, which this repo has hit in
    practice), missing key, malformed JSON, or a present-but-non-list value -- matching this
    codebase's existing hook-config.json reader conventions (e.g. turn-count-hook.py's
    load_prompt_threshold). OSError is caught broadly (not just FileNotFoundError) because an
    uncaught exception here propagates out of prune_one() and, in --scan-dir mode, aborts
    scanning every remaining repo over one repo's transient config-read hiccup -- disproportionate
    for a feature that is supposed to only ever add prunability, never break the scan. An empty
    list value (key present, explicitly []) also returns [] and is indistinguishable from
    "absent" by design -- both mean "feature off", never "everything matches".

    Also validates every pattern compiles as a regex; if any does not, prints a warning and
    returns [] for the WHOLE list (not just the bad entry) -- a config with one malformed
    pattern must not partially enable the feature with silently-different semantics than
    what the user wrote.
    """
    path = os.path.join(repo, CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    patterns = config.get("prune_ephemeral_patterns", [])
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        return []
    try:
        for p in patterns:
            re.compile(p)
    except re.error as exc:
        print(f"  WARNING: invalid prune_ephemeral_patterns regex in {path}: {exc} -- "
              f"ephemeral-diff pruning disabled for this repo")
        return []
    return patterns


def primary_worktree_path(worktrees: list[dict]) -> str:
    """The primary worktree is always the first entry from 'git worktree list'."""
    return str(Path(worktrees[0]["path"]).resolve()) if worktrees else ""


def prune_one(
    repo: str, dry_run: bool, liveness_window_seconds: int, include_named: bool = False
) -> tuple[int, int, bool]:
    """Prune merged claude/* worktrees in a single repo. Returns (pruned, skipped, fetch_failed).

    include_named (opt-in, default False): when True, a worktree whose branch does not start
    with BRANCH_PREFIX is no longer unconditionally skipped by the prefix guard below -- it
    falls through to the same is_merged()/ephemeral-diff/is_dirty() checks claude/* branches
    already go through. Default False preserves the exact pre-existing behavior for every
    caller that doesn't pass --include-named. See ADR-078.
    """
    try:
        gh_repo = detect_gh_repo(repo)
    except RuntimeError as exc:
        print(f"  SKIP {repo}: {exc}")
        return 0, 0, False

    # Read once per repo, not once per worktree — every not-yet-merged claude/* worktree in
    # this repo would otherwise re-read and re-validate the same config file (the repos this
    # feature targets are exactly the ones with many stale worktrees, where that redundancy is
    # largest).
    ephemeral_patterns = load_ephemeral_patterns(repo)

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

    # Every non-canonical worktree squatting a draft/YYYY-MM-DD-shaped engineering-journal
    # branch (dev-env#747, ADR-105) — precomputed once before the loop via the shared
    # topology helper, mirroring main_squatter() above, rather than re-matching
    # DRAFT_BRANCH_RE against each worktree's branch inline inside the loop.
    draft_squatter_paths = {
        str(Path(wt["path"]).resolve())
        for wt in non_canonical_worktrees_matching(worktrees, DRAFT_BRANCH_RE)
    }

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

        # Non-primary worktree squatting a draft/YYYY-MM-DD-shaped engineering-journal
        # branch (membership in the precomputed draft_squatter_paths set above):
        # illegitimate on ANY non-canonical worktree in ANY repo, checked unconditionally
        # (before the BRANCH_PREFIX gate below, which would otherwise skip it — a draft/*
        # branch never starts with claude/). The ADR-051 liveness guard already ran for
        # this worktree earlier in this loop, so a live squatter never reaches here
        # (dev-env#747, ADR-105).
        if path in draft_squatter_paths:
            dirty = is_dirty(path)
            fully_pushed = _origin_ahead_count(branch, repo) == 0
            action = pattern_squat_action(path, branch, live=False, dirty=dirty, fully_pushed=fully_pushed)
            if dry_run:
                pruned.append(path)
                print(f"  [dry-run] would {action.kind}: {path} ({branch} -> {action.park_branch})")
                continue
            r = run(["git", "-C", path, "checkout", "-b", action.park_branch], cwd=repo)
            if r.returncode != 0:
                skipped.append((path, f"park off {branch} failed (branch {action.park_branch} may already exist): {r.stderr.strip()}"))
                continue
            if action.kind == "park-and-remove":
                try:
                    rm = run(["git", "worktree", "remove", path], cwd=repo, timeout=300)
                except subprocess.TimeoutExpired:
                    skipped.append((path, "parked, but git worktree remove timed out — retry manually"))
                    pruned.append(path)
                    continue
                if rm.returncode != 0:
                    skipped.append((path, f"parked but remove failed: {rm.stderr.strip()}"))
                    pruned.append(path)
                    continue
                print(f"  parked + removed: {path} (was {branch})")
            else:
                print(f"  parked only (left in place for review): {path} (was {branch} -> {action.park_branch})")
            pruned.append(path)
            continue

        if not branch.startswith(BRANCH_PREFIX) and not include_named:
            skipped.append((path, f"branch '{branch}' not in {BRANCH_PREFIX}* prefix"))
            continue

        # A detached-HEAD worktree has no branch ref -- is_merged()/diff_files() need an
        # actual commit to compare against origin/main, not the DETACHED sentinel, which is
        # not a resolvable git ref anywhere (dev-env#979). Resolve it once here and use the
        # resolved SHA in place of `branch` for both merge-status checks below; `branch`
        # itself is left untouched for display/prefix purposes elsewhere in the loop.
        merge_ref = branch
        is_detached = branch == DETACHED
        if is_detached:
            merge_ref = resolve_detached_head(path, repo)
            if merge_ref is None:
                skipped.append((path, "not merged into origin/main (detached HEAD, commit unresolvable)"))
                continue

        if not is_merged(merge_ref, gh_repo, repo, skip_pr_fallback=is_detached):
            if not (ephemeral_patterns and files_are_all_ephemeral(diff_files(merge_ref, repo), ephemeral_patterns)):
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
    include_named = _include_named_from_args()

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
            p, s, ff = prune_one(repo, dry_run, liveness_window_seconds, include_named)
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
        prune_one(repo, dry_run, liveness_window_seconds, include_named)


if __name__ == "__main__":
    main()
