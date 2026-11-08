#!/usr/bin/env python3
"""Remove claude/* worktrees whose branches have been merged into the default branch.

Each repo's default branch is discovered (see _default_branch.py), so "main" and "origin/main"
below stand for that branch in a repo that names it differently (dev-env#1088). A worktree on
the default branch is never removed, even with --include-named, because it is merged by definition.

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
  python claude/scripts/prune-merged-worktrees.py [--dry-run] [--full] [--state-file PATH] --scan-dir /path/to/dir

  An undefined flag or stray argument exits 2 before any git command; --help prints usage
  and exits 0 (dev-env#1310).

  --repo-path  Target a specific repo's worktrees (defaults to the dev-env repo).
               Example: --repo-path C:/Users/brown/Git/lifting-logbook
  --scan-dir   Discover and prune all git repos directly under the given directory.
               Skips repos with no GitHub remote or no claude/* worktrees.
               Example: --scan-dir C:/Users/brown/Git
               Lists only the skipped worktrees and fetch failures that are new or changed
               since the last --scan-dir run; a missing or unreadable state file gives the
               full report. Skips for the primary or current worktree or a live session are
               counted but not listed, since they come and go without being a finding
               (dev-env#1307, ADR-161).
  --full       With --scan-dir: print the full report, and neither read nor update the
               stored state.
  --state-file PATH
               With --scan-dir: where the last run's findings are stored (default:
               ~/.claude/scratch/last-run/prune-merged-worktrees.json). --dry-run compares
               against it but doesn't update it.
  --liveness-window-min N
               Skip any worktree whose Claude transcript, or whose own git state (index,
               HEAD, FETCH_HEAD, ORIG_HEAD), was written within the last N minutes.
               Defaults to 1440 (24h). See ADR-051 and its Amendment 1.
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
import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

from _default_branch import FAILED, TIMED_OUT, fetch_default_branch
from _last_run import STATE_DIR, LastRun
from _repo_scan import find_git_repos
from _worktree_liveness import git_activity_skip_reason, liveness_minutes_arg, worktree_session_is_live
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

DEFAULT_STATE_FILE = STATE_DIR / "prune-merged-worktrees.json"
SKIPPED_SECTION = "skipped"
FETCH_SECTION = "fetch"
REMOTE_SECTION = "remote"

SKIP_PRIMARY_OR_CURRENT = "primary or current worktree"
SKIP_LIVE_SESSION = "active Claude session (recent transcript activity)"
# Which worktree is current changes every run, and a session's worktree is live for a day and
# then isn't. Neither says anything about the worktree, so neither counts as a change.
_PASSING_SKIPS = frozenset({SKIP_PRIMARY_OR_CURRENT, SKIP_LIVE_SESSION})


def _nonempty(value: str) -> str:
    """Reject an empty path: an unset shell variable must not select a repo nobody named."""
    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line, exiting on --help or on anything undefined (dev-env#1310).

    This must run before the first git command: this script deletes worktrees, so a flag it
    does not understand has to stop it. Abbreviations are off so a shortened flag is an error
    rather than a guess. The flag descriptions stay ASCII so --help can't fail on a console's
    code page; the module docstring has the full detail.
    """
    parser = argparse.ArgumentParser(
        description="Remove worktrees whose branches are merged into origin/main.",
        allow_abbrev=False,
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    parser.add_argument("--repo-path", metavar="PATH", type=_nonempty,
                        help="target this repo (default: the dev-env repo)")
    parser.add_argument("--scan-dir", metavar="DIR", type=_nonempty,
                        help="target every git repo directly under DIR")
    parser.add_argument("--liveness-window-min", metavar="N", dest="liveness_window_seconds",
                        type=liveness_minutes_arg, default=LIVENESS_WINDOW_SECONDS,
                        help="skip worktrees with a Claude transcript or their own git state written "
                             f"in the last N minutes (default: {LIVENESS_WINDOW_SECONDS // 60})")
    parser.add_argument("--include-named", action="store_true",
                        help="also prune merged worktrees on branches outside claude/*")
    parser.add_argument("--full", action="store_true",
                        help="with --scan-dir: print the full report, and neither read nor update the "
                             "stored state (by default only what changed since the last run is listed)")
    parser.add_argument("--state-file", metavar="PATH", type=_nonempty, default=str(DEFAULT_STATE_FILE),
                        help="with --scan-dir: where the last run's findings are stored")
    return parser.parse_args(argv)


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


def is_merged(branch: str, gh_repo: str, repo: str, base: str, skip_pr_fallback: bool = False) -> bool:
    # Regular merge: commit is an ancestor of origin/<base>
    r = run(["git", "merge-base", "--is-ancestor", branch, f"origin/{base}"], cwd=repo)
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
    # --no-optional-locks: a plain status can rewrite the index, and the index's mtime is what
    # git_activity_skip_reason reads, so a dry run would otherwise delay the next real run.
    r = run(["git", "--no-optional-locks", "status", "--porcelain"], cwd=path)
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
    try:
        fetch = run(["git", "fetch", "origin", branch], cwd=repo)
    except subprocess.TimeoutExpired:
        return None
    if fetch.returncode != 0:
        return None
    r = run(["git", "rev-list", "--count", f"origin/{branch}..{branch}"], cwd=repo)
    if r.returncode != 0 or not r.stdout.strip().isdigit():
        return None
    return int(r.stdout.strip())


CONFIG_FILE = ".claude/hook-config.json"


def diff_files(branch: str, repo: str, base: str) -> list[str]:
    """Files changed on branch relative to origin/<base>, via git diff --name-only.

    Uses the three-dot form (origin/main...branch, i.e. diff against the merge-base, not
    origin/main's current tip) — origin/main normally moves on while a worktree sits idle,
    so two-dot (direct tip-to-tip) would silently change semantics. Returns [] on any git
    failure -- the caller treats an empty list as "no files to prove ephemeral", which the
    empty-patterns guard already makes safe: it can only ever suppress a prune, never force
    one.
    """
    r = run(["git", "diff", "--name-only", f"origin/{base}...{branch}"], cwd=repo)
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


def skip_findings(skipped: list[tuple[str, str]], prior: dict) -> dict:
    """{path: reason} for the last-run state. A passing skip isn't a finding, but it says
    nothing new either, so a path already on record keeps its entry: a not-merged worktree
    that gets a session for a day isn't resolved, and isn't new again the day after."""
    findings = {}
    for path, reason in skipped:
        if reason not in _PASSING_SKIPS:
            findings[path] = reason
        elif path in prior:
            findings[path] = prior[path]
    return findings


def scan_result(repos: int, pruned: int, skipped: int, fetch_failed: int, dry_run: bool,
                last_run: "LastRun | None", errors: int = 0) -> str:
    """The machine-readable last line of a --scan-dir run, which the routine reads.

    fetch_failed counts every repo whose fetch failed or timed out (dev-env#1087); errors, the
    repos where an unexpected exception stopped the repo or one of its worktrees (dev-env#1344),
    appears only when there is one."""
    line = (
        f"RESULT: repos_scanned={repos} pruned={pruned} skipped={skipped} "
        f"fetch_failed={fetch_failed} dry_run={'true' if dry_run else 'false'}"
    )
    if errors:
        line += f" errors={errors}"
    if last_run is not None:
        line += (
            f" new_skipped={last_run.changed[SKIPPED_SECTION]}"
            f" no_longer_skipped={last_run.gone[SKIPPED_SECTION]}"
            f" new_fetch_failed={last_run.changed[FETCH_SECTION]}"
            f" new_remote_failed={last_run.changed[REMOTE_SECTION]}"
            f" baseline={'true' if last_run.is_baseline else 'false'}"
        )
    return line


def prune_one(
    repo: str, dry_run: bool, liveness_window_seconds: int, include_named: bool = False,
    last_run: "LastRun | None" = None,
) -> "tuple[int, int, str | None, list[str]]":
    """Prune merged claude/* worktrees in a single repo.

    Returns (pruned, skipped, fetch_problem, errors), where fetch_problem is None, FAILED or
    TIMED_OUT, and errors lists the worktrees an unexpected exception stopped (dev-env#1344).

    include_named (opt-in, default False): when True, a worktree whose branch does not start
    with BRANCH_PREFIX is no longer unconditionally skipped by the prefix guard below -- it
    falls through to the same is_merged()/ephemeral-diff/is_dirty() checks claude/* branches
    already go through. Default False preserves the exact pre-existing behavior for every
    caller that doesn't pass --include-named. See ADR-078.

    last_run: when given and not a baseline, print only what changed since the last run, with
    no per-repo header or summary, so a repo with nothing new prints nothing (dev-env#1307).
    Every line that can still print names a full path or the repo, so no header is needed. A
    repo whose GitHub remote can't be read is a finding of its own; one that returns early
    after that keeps its stored findings.
    """
    verbose = last_run is None or last_run.is_baseline

    try:
        gh_repo = detect_gh_repo(repo)
    except RuntimeError as exc:
        remote_new = last_run.record(repo, REMOTE_SECTION, {"origin": str(exc)})[0] if last_run else []
        if verbose or remote_new:
            print(f"  SKIP {repo}: {exc}")
        return 0, 0, None, []
    if last_run is not None and last_run.record(repo, REMOTE_SECTION, {})[1] and not verbose:
        print(f"  GitHub remote resolves again: {repo}")

    # Read once per repo, not once per worktree — every not-yet-merged claude/* worktree in
    # this repo would otherwise re-read and re-validate the same config file (the repos this
    # feature targets are exactly the ones with many stale worktrees, where that redundancy is
    # largest).
    ephemeral_patterns = load_ephemeral_patterns(repo)

    if verbose:
        print(f"\nRepo: {gh_repo} ({repo})")

    # Fetch the default branch so merge checks are accurate
    default_branch, fetch_problem, fetch_detail, branch_found = fetch_default_branch(repo)
    # A worktree on any of these is the branch's real home, never pruned. When the default
    # branch could not be found, protect both common names rather than only the guess.
    protected_branches = {default_branch} if branch_found else {"main", "master"}
    fetch_new = fetch_back = []
    if last_run is not None:
        fetch_new, fetch_back = last_run.record(
            repo, FETCH_SECTION, {f"origin {default_branch}": fetch_problem} if fetch_problem else {}
        )
    if fetch_problem and (verbose or fetch_new):
        print(f"  WARNING: fetch {fetch_problem} in {repo} — merge checks may use stale "
              f"origin/{default_branch}: {fetch_detail}")
    if fetch_back and not verbose:
        print(f"  fetch works again: {repo}")

    result = run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        print(f"  ERROR: git worktree list failed in {repo}: {result.stderr}", file=sys.stderr)
        return 0, 0, fetch_problem, []

    worktrees = parse_worktree_porcelain(result.stdout)
    primary = primary_worktree_path(worktrees)
    cwd = str(Path(os.getcwd()).resolve())

    # Identify the squatter (if any) using the topology helper — handles bare/detached
    # canonicals that legitimately yield a secondary worktree on main (dev-env#399, ADR-058).
    # A bare/detached primary can't hold a working-tree checkout of main itself, so a
    # secondary worktree on main there is the real home of main and must NOT be parked.
    # main_squatter() returns None in that case; the naive `branch == "main"` check did not.
    squatter = main_squatter(worktrees, default_branch)
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
    errors: list[str] = []

    for wt in worktrees:
        branch = wt["branch"]
        path = str(Path(wt["path"]).resolve())

        # An unexpected error is this worktree's alone: the repo's other worktrees are still
        # handled and counted (dev-env#1344).
        try:
            # Always skip the primary worktree and wherever this process is running
            if path == primary or path == cwd:
                skipped.append((path, SKIP_PRIMARY_OR_CURRENT))
                continue

            # Skip a worktree with a live Claude session (recent transcript activity). The
            # cwd guard above only covers THIS process; an out-of-process routine cannot see
            # another worktree's active session except via its transcript mtime — removing a
            # live worktree severs the session mid-task (dev-env#383, ADR-051). Additive: this
            # only ever adds a skip, never removes more than the merged/clean checks below.
            if worktree_session_is_live(path, window_seconds=liveness_window_seconds):
                skipped.append((path, SKIP_LIVE_SESSION))
                continue

            # The transcript check above can't see a session started in another folder that works
            # here through `git worktree add` or `git -C` (dev-env#1330, ADR-051 Amendment 1).
            activity = git_activity_skip_reason(path, window_seconds=liveness_window_seconds)
            if activity:
                skipped.append((path, activity))
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
                    print(f"  [dry-run] would park stale {default_branch} checkout off {default_branch}: {path} -> {park}")
                    continue
                r = run(["git", "-C", path, "checkout", "-b", park], cwd=repo)
                if r.returncode != 0:
                    skipped.append((path, f"park off {default_branch} failed (branch {park} may already exist): {r.stderr.strip()}"))
                    continue
                pruned.append(path)
                print(f"  parked off {default_branch}: {path} ({park}) — freed the {default_branch} ref")
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

            # Past the squatter check, a worktree on the default branch is that branch's real home
            # (the primary is bare or detached). It is merged by definition, so --include-named
            # would otherwise remove it and delete the branch.
            if branch in protected_branches:
                skipped.append((path, f"on the default branch '{branch}'"))
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
                    skipped.append((path, f"not merged into origin/{default_branch} (detached HEAD, commit unresolvable)"))
                    continue

            if not is_merged(merge_ref, gh_repo, repo, default_branch, skip_pr_fallback=is_detached):
                if not (ephemeral_patterns and files_are_all_ephemeral(diff_files(merge_ref, repo, default_branch), ephemeral_patterns)):
                    skipped.append((path, f"not merged into origin/{default_branch}"))
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
                print(f"  WARNING: branch delete failed for {branch} in {repo}: {r.stderr.strip()}")

            pruned.append(path)
            print(f"  pruned: {path} ({branch})")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {path}: {type(exc).__name__}: {exc} -- left as it was")
            traceback.print_exc()
            errors.append(path)

    skip_new: list[str] = []
    skip_gone: list[str] = []
    if last_run is not None:
        prior = last_run.prior(repo, SKIPPED_SECTION)
        findings = skip_findings(skipped, prior)
        # An errored worktree wasn't looked at, so whatever was on record for it still stands.
        findings.update({path: prior[path] for path in errors if path in prior})
        skip_new, skip_gone = last_run.record(repo, SKIPPED_SECTION, findings)

    if verbose:
        suffix = f" [fetch {fetch_problem} — results may use stale origin/{default_branch}]" if fetch_problem else ""
        if errors:
            suffix = f", errors {len(errors)}{suffix}"
        print(f"  Done — pruned {len(pruned)}, skipped {len(skipped)}{suffix}")
        if skipped:
            for path, reason in skipped:
                print(f"    skipped {path}: {reason}")
    else:
        reasons = dict(skipped)
        for path in skip_new:
            print(f"    skipped {path}: {reasons[path]}")
        for path in skip_gone:
            print(f"    no longer skipped: {path}")

    return len(pruned), len(skipped), fetch_problem, errors


def main() -> None:
    args = _parse_args(sys.argv[1:])
    dry_run = args.dry_run
    scan_dir = str(Path(args.scan_dir).resolve()) if args.scan_dir is not None else None
    liveness_window_seconds = args.liveness_window_seconds
    include_named = args.include_named

    if dry_run:
        print("[dry-run] no changes will be made")

    if scan_dir:
        last_run = None if args.full else LastRun(args.state_file)
        repos = find_git_repos(scan_dir)
        if repos is None:
            # find_git_repos already printed the WARNING with the underlying OSError.
            print(f"Scan of {scan_dir} failed - nothing was pruned")
            print(scan_result(0, 0, 0, 0, dry_run, last_run))
            sys.exit(1)
        if not repos:
            print(f"No git repos found under {scan_dir}")
            print(scan_result(0, 0, 0, 0, dry_run, last_run))
            sys.exit(0)
        print(f"Found {len(repos)} repos under {scan_dir}")
        total_pruned = total_skipped = 0
        fetch_problem_repos: dict[str, list[str]] = {FAILED: [], TIMED_OUT: []}
        error_repos: list[str] = []
        for repo in repos:
            # Any other git call can also time out (`git status` on a large worktree, `gh pr
            # list`): one repo must never stop the sweep of the rest (dev-env#1087).
            try:
                p, s, fetch_problem, errors = prune_one(repo, dry_run, liveness_window_seconds, include_named, last_run)
            except Exception as exc:  # noqa: BLE001
                # Lines already printed for this repo record what it changed; its counts are lost.
                print(f"  ERROR: {repo}: {type(exc).__name__}: {exc} -- its counts are not in the total")
                traceback.print_exc()
                error_repos.append(repo)
                continue
            total_pruned += p
            total_skipped += s
            if fetch_problem:
                fetch_problem_repos[fetch_problem].append(repo)
            if errors:
                error_repos.append(repo)
        summary = f"\nTotal — pruned {total_pruned}, skipped {total_skipped}"
        list_names = last_run is None or last_run.is_baseline
        for problem, problem_repos in fetch_problem_repos.items():
            if problem_repos:
                summary += f", fetch {problem} in {len(problem_repos)} repo(s)"
                if list_names:
                    summary += f": {', '.join(problem_repos)}"
        if error_repos:
            summary += f", error in {len(error_repos)} repo(s): {', '.join(error_repos)}"
        print(summary)
        if last_run is not None and not dry_run:
            last_run.save()
        fetch_problems = sum(len(r) for r in fetch_problem_repos.values())
        print(scan_result(len(repos), total_pruned, total_skipped, fetch_problems, dry_run, last_run,
                          errors=len(error_repos)))
        if error_repos:
            sys.exit(1)
    else:
        repo = str(Path(args.repo_path).resolve()) if args.repo_path is not None else _DEFAULT_REPO
        if prune_one(repo, dry_run, liveness_window_seconds, include_named)[3]:
            sys.exit(1)


if __name__ == "__main__":
    main()
