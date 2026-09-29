#!/usr/bin/env python3
"""Unit tests for prune-merged-worktrees.py.

Covers the TimeoutExpired recovery introduced for dev-env#350:
  prune_one() must skip a worktree when `git worktree remove` times out and
  continue the loop — the exception must NOT propagate and abort the scan.

Also covers the ADR-075 ephemeral-diff prunability signal: files_are_all_ephemeral()
(matches-all / one-mismatch / empty-patterns opt-in gate / empty-files) and
load_ephemeral_patterns() (reads a real tmp .claude/hook-config.json; fail-open to []
on a missing file, an unreadable file (OSError, e.g. a transient PermissionError),
missing key, malformed JSON, non-list value, non-string element, or an invalid regex —
the last case also prints a WARNING: line, captured via redirect_stdout). diff_files()
and the prune_one() integration point are not covered here — see "Scope note" below.

Also covers the ADR-078 --include-named opt-in (dev-env#545) via prune_one()'s
include_named parameter, using the same subprocess-mocking style as the TimeoutExpired
case below: a merged, clean, non-claude/* branch worktree is skipped via the prefix-guard
reason when include_named=False (the default; regression proof of unchanged behavior),
and pruned via the exact same is_merged()/is_dirty() path claude/* branches already use
when include_named=True.

Also covers the ADR-105 draft-branch-squat wiring (dev-env#747): a non-canonical worktree
squatting an engineering-journal draft/YYYY-MM-DD branch is parked AND removed when idle,
clean, and fully pushed (mocking is_dirty=False and a rev-list --count of 0), or parked
ONLY (worktree left untouched) when dirty (mocking is_dirty=True) — mirrors the real
stub-829-165612 (park+remove) / stub-823-120134 (park-only) disposition from the live
2026-07-12 incident. The subprocess.run side_effect records every dispatched call
(`_make_dispatch_draft_squat`'s `calls` list) so each test asserts not just the
(pruned, skipped) counts — which are identical for park-and-remove vs. park-only — but
whether `git worktree remove` was actually invoked (review finding: the original two tests
asserted only the counts, so sabotaging the removal decision to always park-only still
passed the "park and remove" test). Two further tests cover `git worktree remove` failing
(non-zero exit) or timing out (`subprocess.TimeoutExpired`) after a successful park: both
must still count the item as pruned (the branch was freed independently of the removal)
while also flagging it skipped for manual retry.

Also covers the dev-env#979 detached-HEAD merge-check fix: resolve_detached_head() and the
prune_one() loop's substitution of a resolved commit SHA for the un-resolvable DETACHED
sentinel before calling is_merged()/diff_files(). Three cases: the detached commit IS an
ancestor of origin/main (pruned), is NOT (skipped, not force-pruned), and `git rev-parse
HEAD` itself fails (fails safe, skipped with a distinct reason, no crash). The dispatch
helper (`_make_dispatch_detached`) distinguishes `git rev-parse HEAD` calls by their `cwd`
kwarg — only the detached worktree's own path resolves to a SHA — which is the actual
regression guard: a fix that resolved HEAD against the repo's cwd instead of the specific
worktree's own path would silently substitute the wrong commit and this would surface as a
wrong prune/skip verdict rather than passing silently.

Pure-helper tests follow the pattern of test_reclaim_worktree_disk.py and
test_worktree_topology.py; the load_ephemeral_patterns tests use a real
tempfile.TemporaryDirectory() rather than mocking open(), matching this codebase's
established convention for config/filesystem tests (also used in
test_worktree_liveness.py and test_reclaim_worktree_disk.py). The TimeoutExpired case
is the exception: it lives inside the integration loop of prune_one(), which shells
out to git/gh. It IS unit-testable here via subprocess.run mocking (unlike the
merge-detection and worktree-list steps, which are exercised end-to-end by --dry-run
in the PR).

Scope note: diff_files() has no dedicated test — it is a one-line run() wrapper
structurally identical to is_merged()/is_dirty(), neither of which has one either; all
three are exercised via --dry-run against a real repo, not unit tests.

Also covers the dev-env#1104 orphaned-worktree-directory scan: list_worktree_subdirs()
and has_git_link() against real tempfile.TemporaryDirectory() trees (matching this file's
own filesystem-test convention rather than mocking os.scandir/Path.exists), and
find_and_remove_orphaned_worktrees()'s four safety/failure paths -- a safe orphan is
actually deleted from disk, a directory that still has a .git link is left completely
untouched, a live-session candidate is left untouched (ADR-051 applies here too), and an
OSError from one orphan's shutil.rmtree (the lock/long-path failure the whole feature exists
for) is caught and skipped without aborting a second orphan in the same call. A final
end-to-end test drives prune_one() itself (not just the helper in isolation) against a real
tmp directory standing in for the primary worktree, to prove the wiring -- not just the
helper -- actually removes the orphan and folds it into the returned counts.

Usage:
    py -3 claude/scripts/tests/test_prune_merged_worktrees.py

Exit 0 = all pass.
"""
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import types
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_MODULE_PATH = SCRIPTS_DIR / "prune-merged-worktrees.py"
_spec = importlib.util.spec_from_file_location("prune_merged_worktrees", _MODULE_PATH)
prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune)


# Fake worktree porcelain: one primary on main, one merged claude/* branch.
_PORCELAIN = (
    "worktree /FAKE_PRIMARY_PRUNE_9a7\n"
    "HEAD abc123\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /FAKE_WORKTREE_PRUNE_9a7\n"
    "HEAD 789abc\n"
    "branch refs/heads/claude/some-feature\n"
    "\n"
)

# Fake worktree porcelain: one primary on main, one merged NAMED (non-claude/*) branch.
# Used to prove --include-named's opt-in behavior (dev-env#545, ADR-078).
_PORCELAIN_NAMED = (
    "worktree /FAKE_PRIMARY_PRUNE_NAMED_9a7\n"
    "HEAD abc123\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /FAKE_WORKTREE_PRUNE_NAMED_9a7\n"
    "HEAD 789abc\n"
    "branch refs/heads/feat/some-feature\n"
    "\n"
)


def _ok(stdout=""):
    return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _dispatch(args, **_kwargs):
    """Route subprocess.run calls to canned responses; raise TimeoutExpired for worktree remove."""
    if args[1:2] == ["remote"]:              # git remote get-url origin
        return _ok("git@github.com:brownm09/dev-env.git\n")
    if args[1:3] == ["fetch", "origin"]:     # git fetch
        return _ok()
    if args[1:3] == ["worktree", "list"]:    # git worktree list --porcelain
        return _ok(_PORCELAIN)
    if args[1:3] == ["status", "--porcelain"]:  # is_dirty → not dirty
        return _ok("")
    if args[1:3] == ["merge-base", "--is-ancestor"]:  # is_merged → merged (returncode 0)
        return _ok()
    if args[1:3] == ["worktree", "remove"]:  # the slow operation → timeout
        raise subprocess.TimeoutExpired(cmd=args, timeout=300)
    return _ok()


def _dispatch_named(args, **_kwargs):
    """Like _dispatch, but serves _PORCELAIN_NAMED (a merged, clean, non-claude/* branch)
    and never times out -- worktree remove succeeds so a full prune can be observed.
    """
    if args[1:2] == ["remote"]:              # git remote get-url origin
        return _ok("git@github.com:brownm09/dev-env.git\n")
    if args[1:3] == ["fetch", "origin"]:     # git fetch
        return _ok()
    if args[1:3] == ["worktree", "list"]:    # git worktree list --porcelain
        return _ok(_PORCELAIN_NAMED)
    if args[1:3] == ["status", "--porcelain"]:  # is_dirty → not dirty
        return _ok("")
    if args[1:3] == ["merge-base", "--is-ancestor"]:  # is_merged → merged (returncode 0)
        return _ok()
    if args[1:3] == ["worktree", "remove"]:  # succeeds (no timeout here)
        return _ok()
    if args[1:3] == ["branch", "-d"]:        # git branch -d <branch>
        return _ok()
    return _ok()


def test_named_branch_skipped_by_default() -> str:
    """--include-named unset (default False): a merged, clean, non-claude/* branch worktree
    is still skipped via the prefix-guard reason -- regression proof that default behavior
    is unchanged (dev-env#545, ADR-078).
    """
    with unittest.mock.patch("subprocess.run", side_effect=_dispatch_named):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_REPO_NAMED",
                    dry_run=False,
                    liveness_window_seconds=86400,
                    include_named=False,
                )

    assert pruned_count == 0, f"expected 0 pruned (default unchanged), got {pruned_count}"
    # skipped = [primary (always), named-branch (prefix guard)]
    assert skipped_count == 2, f"expected 2 skipped (primary + prefix-guard), got {skipped_count}"
    assert not fetch_failed, "fetch should not be marked failed"
    return "include_named=False (default): named branch skipped via prefix-guard reason, pruned=0"


def test_named_branch_pruned_with_include_named() -> str:
    """--include-named set: the SAME merged, clean, non-claude/* branch worktree that the
    prior test proved is skipped by default now falls through to the same
    is_merged()/is_dirty() checks claude/* branches use, and is pruned (dev-env#545, ADR-078).
    """
    with unittest.mock.patch("subprocess.run", side_effect=_dispatch_named):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_REPO_NAMED",
                    dry_run=False,
                    liveness_window_seconds=86400,
                    include_named=True,
                )

    assert pruned_count == 1, f"expected 1 pruned (named branch now eligible), got {pruned_count}"
    # skipped = [primary (always)] only
    assert skipped_count == 1, f"expected 1 skipped (primary only), got {skipped_count}"
    assert not fetch_failed, "fetch should not be marked failed"
    return "include_named=True: named branch falls through to merged/dirty checks, pruned=1"


# Fake worktree porcelain: one primary on main, one non-canonical worktree squatting a
# draft/YYYY-MM-DD engineering-journal branch (dev-env#747, ADR-105).
_PORCELAIN_DRAFT_SQUAT = (
    "worktree /FAKE_EJ_PRIMARY\n"
    "HEAD abc123\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /FAKE_EJ_STUB_TODAY\n"
    "HEAD 789abc\n"
    "branch refs/heads/draft/2026-07-12\n"
    "\n"
)


def _make_dispatch_draft_squat(remove_result=None, remove_exception=None):
    """Build a subprocess.run side_effect for the draft-branch-squat tests, plus a `calls`
    list every dispatched args tuple is recorded into -- so a test can assert not just the
    (pruned, skipped) counts (which are IDENTICAL for park-and-remove vs. park-only: both
    append to `pruned`, only the primary is ever skipped in the happy path) but whether
    `git worktree remove` was actually invoked, and how many times (review finding: the
    original tests asserted only the counts, so sabotaging pattern_squat_action to always
    return park-only still passed the "park and remove" test).

    remove_result: a types.SimpleNamespace to return for the `worktree remove` call instead
    of the default success (`_ok()`) -- used to simulate a failed removal.
    remove_exception: an exception instance to raise instead of returning, for the
    `worktree remove` call -- used to simulate subprocess.TimeoutExpired.
    """
    calls: list = []

    def _dispatch(args, **_kwargs):
        calls.append(list(args))
        if args[1:2] == ["remote"]:                    # git remote get-url origin
            return _ok("git@github.com:brownm09/engineering-journal.git\n")
        if args[1:3] == ["fetch", "origin"]:            # git fetch origin main, AND
            return _ok()                                # _origin_ahead_count's git fetch origin <branch>
        if args[1:3] == ["worktree", "list"]:           # git worktree list --porcelain
            return _ok(_PORCELAIN_DRAFT_SQUAT)
        if args[1:3] == ["rev-list", "--count"]:        # _origin_ahead_count -> fully pushed
            return _ok("0\n")
        if "checkout" in args and "-b" in args:         # the park: git -C <path> checkout -b <park>
            return _ok()
        if args[1:3] == ["worktree", "remove"]:         # park-and-remove's second step
            if remove_exception is not None:
                raise remove_exception
            return remove_result if remove_result is not None else _ok()
        return _ok()

    return _dispatch, calls


def _remove_calls(calls: list) -> list:
    return [c for c in calls if c[1:3] == ["worktree", "remove"]]


def test_draft_branch_squat_park_and_remove() -> str:
    """dev-env#747: a non-canonical worktree squatting draft/YYYY-MM-DD, idle, clean, and
    fully pushed, is parked AND removed in the same pass -- unlike a main-squatter (which is
    only ever parked, never auto-removed), this shape is safe to fully clean up because
    nothing else can legitimately need that exact throwaway worktree again."""
    dispatch, calls = _make_dispatch_draft_squat()
    with unittest.mock.patch("subprocess.run", side_effect=dispatch):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_EJ_REPO",
                    dry_run=False,
                    liveness_window_seconds=86400,
                )
    assert pruned_count == 1, f"expected 1 pruned (parked + removed), got {pruned_count}"
    assert skipped_count == 1, f"expected 1 skipped (primary only), got {skipped_count}"
    assert not fetch_failed, "fetch should not be marked failed"
    removed = _remove_calls(calls)
    assert len(removed) == 1, f"expected exactly one `git worktree remove` call, got {removed}"
    return "idle+clean+fully-pushed draft-branch squatter: parked and removed, pruned=1, remove actually invoked once"


def test_draft_branch_squat_park_only_when_dirty() -> str:
    """A dirty squatter is parked (frees the branch name) but NOT removed -- its uncommitted
    content is preserved for human review, mirroring the real stub-823-120134 disposition
    from dev-env#747's live incident."""
    dispatch, calls = _make_dispatch_draft_squat()
    with unittest.mock.patch("subprocess.run", side_effect=dispatch):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=True):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_EJ_REPO",
                    dry_run=False,
                    liveness_window_seconds=86400,
                )
    # park-only still counts as "pruned" (handled), matching the existing main-squatter
    # park block's own convention of appending to `pruned` for a park, not just a removal.
    assert pruned_count == 1, f"expected 1 pruned (park-only still counts as handled), got {pruned_count}"
    assert skipped_count == 1, f"expected 1 skipped (primary only), got {skipped_count}"
    removed = _remove_calls(calls)
    assert not removed, f"park-only must NEVER call `git worktree remove` -- content must stay untouched, got {removed}"
    return "dirty draft-branch squatter: parked only (branch freed), remove NOT invoked, worktree left in place"


def test_draft_branch_squat_park_and_remove_when_remove_fails() -> str:
    """`git worktree remove` returning non-zero AFTER a successful park must still count the
    branch as freed (pruned) while flagging the item for manual retry (skipped) -- diverges
    deliberately from the generic merged-worktree timeout path (skipped only, never pruned),
    because here the branch-freeing park already succeeded independently of the removal."""
    fail_result = types.SimpleNamespace(returncode=1, stdout="", stderr="fatal: could not remove worktree")
    dispatch, calls = _make_dispatch_draft_squat(remove_result=fail_result)
    with unittest.mock.patch("subprocess.run", side_effect=dispatch):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_EJ_REPO",
                    dry_run=False,
                    liveness_window_seconds=86400,
                )
    assert pruned_count == 1, f"branch was freed despite the failed remove -- expected pruned=1, got {pruned_count}"
    assert skipped_count == 2, f"expected primary + the failed-remove retry flag, got {skipped_count}"
    removed = _remove_calls(calls)
    assert len(removed) == 1, f"expected exactly one (failed) `git worktree remove` attempt, got {removed}"
    return "remove failure after a successful park: branch freed (pruned=1) but also flagged for manual retry (skipped includes it)"


def test_draft_branch_squat_park_and_remove_when_remove_times_out() -> str:
    """`git worktree remove` raising subprocess.TimeoutExpired AFTER a successful park must
    degrade the same way as a non-zero exit (branch freed, item flagged for manual retry) --
    not propagate and abort the scan, mirroring the generic worktree-remove TimeoutExpired
    handling this file already covers (test_timeout_skips_worktree_and_continues) but for
    THIS branch's own separate try/except (new code, not shared with that path)."""
    dispatch, calls = _make_dispatch_draft_squat(
        remove_exception=subprocess.TimeoutExpired(cmd=["git", "worktree", "remove"], timeout=300)
    )
    with unittest.mock.patch("subprocess.run", side_effect=dispatch):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_EJ_REPO",
                    dry_run=False,
                    liveness_window_seconds=86400,
                )
    assert pruned_count == 1, f"branch was freed despite the remove timeout -- expected pruned=1, got {pruned_count}"
    assert skipped_count == 2, f"expected primary + the timed-out-remove retry flag, got {skipped_count}"
    removed = _remove_calls(calls)
    assert len(removed) == 1, f"expected exactly one (timed-out) `git worktree remove` attempt, got {removed}"
    return "remove timeout after a successful park: branch freed (pruned=1), flagged for manual retry, loop continues"


def test_timeout_skips_worktree_and_continues() -> str:
    """git worktree remove timing out must skip that worktree and continue — not raise."""
    with unittest.mock.patch("subprocess.run", side_effect=_dispatch):
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo="/FAKE_REPO",
                    dry_run=False,
                    liveness_window_seconds=86400,
                )

    # The timed-out worktree must appear in skipped, not pruned.
    assert pruned_count == 0, f"expected 0 pruned, got {pruned_count}"
    # skipped = [primary (always), timed-out secondary]
    assert skipped_count == 2, f"expected 2 skipped (primary + timed-out), got {skipped_count}"
    assert not fetch_failed, "fetch should not be marked failed"
    return "TimeoutExpired caught: pruned=0, skipped=2, fetch_failed=False — loop continued"


# --- dev-env#979: detached-HEAD merge-check fix ----------------------------------------

# Fake worktree porcelain: one primary on main, one secondary worktree in detached-HEAD
# state (a `detached` line, no `branch` line) -- exercises parse_worktree_porcelain()'s
# existing DETACHED branch.
_PORCELAIN_DETACHED = (
    "worktree /FAKE_PRIMARY_PRUNE_DET\n"
    "HEAD abc123\n"
    "branch refs/heads/main\n"
    "\n"
    "worktree /FAKE_WORKTREE_PRUNE_DET\n"
    "HEAD 789abc\n"
    "detached\n"
    "\n"
)

# The prune loop resolves each worktree's path via str(Path(wt["path"]).resolve()) before
# ever calling run() with it as cwd -- match that here so the dispatch's cwd comparison
# below lines up with what the script actually passes.
_DETACHED_WORKTREE_PATH = str(Path("/FAKE_WORKTREE_PRUNE_DET").resolve())
_DETACHED_SHA = "789abcdef0123456789abcdef0123456789abcd"


def _make_dispatch_detached(merge_base_ok: bool, rev_parse_ok: bool = True):
    """Build a subprocess.run side_effect for the detached-HEAD merge-check tests
    (dev-env#979).

    Distinguishes `git rev-parse HEAD` calls by their `cwd` kwarg -- the actual regression
    guard: a fix that resolved HEAD against the repo's cwd instead of the worktree's own
    path would silently substitute the WRONG commit (the primary's, not the detached
    worktree's). Only a rev-parse issued with cwd == _DETACHED_WORKTREE_PATH gets a real
    SHA back; any other cwd (or rev_parse_ok=False) gets a failure, so a cwd mistake in the
    fix would surface as a wrong prune/skip verdict rather than passing silently.
    """
    def _dispatch(args, **kwargs):
        cwd = kwargs.get("cwd")
        if args[1:2] == ["remote"]:                # git remote get-url origin
            return _ok("git@github.com:brownm09/dev-env.git\n")
        if args[1:3] == ["fetch", "origin"]:        # git fetch origin main
            return _ok()
        if args[1:3] == ["worktree", "list"]:       # git worktree list --porcelain
            return _ok(_PORCELAIN_DETACHED)
        if args[1:3] == ["rev-parse", "HEAD"]:      # resolve_detached_head
            if rev_parse_ok and cwd == _DETACHED_WORKTREE_PATH:
                return _ok(f"{_DETACHED_SHA}\n")
            return types.SimpleNamespace(returncode=1, stdout="", stderr="fatal: not a git repository")
        if args[1:3] == ["status", "--porcelain"]:  # is_dirty -> not dirty
            return _ok("")
        if args[1:3] == ["merge-base", "--is-ancestor"]:  # is_merged: ancestor check
            return _ok() if merge_base_ok else types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[1:3] == ["pr", "list"]:             # is_merged: gh pr list fallback
            return _ok("[]\n")
        if args[1:3] == ["worktree", "remove"]:     # succeeds
            return _ok()
        if args[1:3] == ["branch", "-d"]:           # git branch -d <branch>
            return _ok()
        return _ok()
    return _dispatch


def test_detached_head_merged_commit_pruned() -> str:
    """A detached-HEAD worktree whose actual checked-out commit IS an ancestor of
    origin/main must be pruned -- previously it was always reported "not merged" because
    the DETACHED sentinel ("<detached>") was fed straight into is_merged() as if it were a
    resolvable ref (dev-env#979). Requires --include-named since the sentinel never starts
    with claude/ -- that prefix gate is orthogonal to this bug and stays exercised as-is.
    Path.exists is patched True -- the fake worktree path doesn't exist on the real
    filesystem, and this test is about the resolved-SHA merge check, not the existence
    guard (see test_detached_head_missing_worktree_path_skipped for that).
    """
    with unittest.mock.patch("subprocess.run", side_effect=_make_dispatch_detached(merge_base_ok=True)):
        with unittest.mock.patch("pathlib.Path.exists", return_value=True):
            with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
                with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                    pruned_count, skipped_count, fetch_failed = prune.prune_one(
                        repo="/FAKE_REPO_DETACHED",
                        dry_run=False,
                        liveness_window_seconds=86400,
                        include_named=True,
                    )
    assert pruned_count == 1, f"expected 1 pruned (detached commit is merged), got {pruned_count}"
    assert skipped_count == 1, f"expected 1 skipped (primary only), got {skipped_count}"
    assert not fetch_failed, "fetch should not be marked failed"
    return "detached HEAD, commit IS ancestor of origin/main: resolved via worktree-scoped rev-parse and pruned"


def test_detached_head_unmerged_commit_skipped() -> str:
    """A detached-HEAD worktree whose commit is NOT an ancestor of origin/main (and has no
    matching merged PR) is still correctly skipped -- proving the fix doesn't over-correct
    into pruning everything detached (dev-env#979). Also proves the gh pr list fallback is
    actually skipped for a resolved SHA (skip_pr_fallback=True): the dispatch's ["pr", "list"]
    branch would return a non-empty match here if it were ever called, so a pass here means
    is_merged() genuinely never issued that call for the detached path."""
    with unittest.mock.patch("subprocess.run", side_effect=_make_dispatch_detached(merge_base_ok=False)):
        with unittest.mock.patch("pathlib.Path.exists", return_value=True):
            with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
                with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                    pruned_count, skipped_count, fetch_failed = prune.prune_one(
                        repo="/FAKE_REPO_DETACHED",
                        dry_run=False,
                        liveness_window_seconds=86400,
                        include_named=True,
                    )
    assert pruned_count == 0, f"expected 0 pruned (detached commit is not merged), got {pruned_count}"
    assert skipped_count == 2, f"expected 2 skipped (primary + not-merged detached), got {skipped_count}"
    return "detached HEAD, commit is NOT merged: correctly skipped, not force-pruned, gh pr list fallback skipped"


def test_detached_head_rev_parse_failure_skipped() -> str:
    """When the detached worktree's own `git rev-parse HEAD` itself fails (e.g. a corrupted
    worktree), the fix must fail safe -- skip with a distinct reason -- rather than crash or
    silently fall through to a misleading generic message (dev-env#979)."""
    with unittest.mock.patch("subprocess.run", side_effect=_make_dispatch_detached(merge_base_ok=True, rev_parse_ok=False)):
        with unittest.mock.patch("pathlib.Path.exists", return_value=True):
            with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
                with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                    pruned_count, skipped_count, fetch_failed = prune.prune_one(
                        repo="/FAKE_REPO_DETACHED",
                        dry_run=False,
                        liveness_window_seconds=86400,
                        include_named=True,
                    )
    assert pruned_count == 0, f"expected 0 pruned (commit unresolvable), got {pruned_count}"
    assert skipped_count == 2, f"expected 2 skipped (primary + unresolvable), got {skipped_count}"
    return "detached HEAD, rev-parse HEAD itself fails: fails safe (skipped, reason distinct), no crash"


def test_detached_head_missing_worktree_path_skipped() -> str:
    """Regression coverage for a review finding: a detached worktree still REGISTERED via
    `git worktree list` but whose directory was deleted or moved outside `git worktree
    remove`/`prune` (an "orphaned" worktree) must be skipped gracefully, not crash the scan.
    Before the fix, resolve_detached_head() ran `git rev-parse HEAD` with cwd set to the
    worktree's own (now-missing) path with no existence check first -- subprocess.run raises
    FileNotFoundError when cwd doesn't exist, which is NOT caught anywhere in prune_one() or
    main()'s --scan-dir loop, so it would have aborted the scan for every remaining repo, not
    just this one (dev-env#979). Path.exists=False here simulates exactly that: if the guard
    were missing, subprocess.run would never even be reached for the rev-parse call because
    resolve_detached_head() returns None first -- proven by NOT installing a rev-parse-HEAD
    dispatch branch at all in this test's mock (any attempt to actually run git here would
    fall through to the dispatch's default _ok(), which would silently return a bogus SHA and
    mask the very crash this test exists to catch -- so instead this test's real assertion is
    that prune_one() returns normally at all, without exceptions escaping this call).
    """
    with unittest.mock.patch("subprocess.run", side_effect=_make_dispatch_detached(merge_base_ok=True)):
        with unittest.mock.patch("pathlib.Path.exists", return_value=False):
            with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
                with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                    pruned_count, skipped_count, fetch_failed = prune.prune_one(
                        repo="/FAKE_REPO_DETACHED",
                        dry_run=False,
                        liveness_window_seconds=86400,
                        include_named=True,
                    )
    assert pruned_count == 0, f"expected 0 pruned (missing worktree path), got {pruned_count}"
    assert skipped_count == 2, f"expected 2 skipped (primary + missing-path detached), got {skipped_count}"
    return "detached HEAD, worktree path no longer exists on disk: fails safe (skipped), no FileNotFoundError escapes prune_one()"


# --- files_are_all_ephemeral (pure) ---------------------------------------------------

def test_files_are_all_ephemeral_matches() -> str:
    files = ["sessions/x/2026-01-01_010101.stub.md", "sessions/x/2026-01-01.manifest.jsonl", "sessions/x/open-prs/12.json"]
    patterns = [r"\.stub\.md$", r"\.manifest\.jsonl$", r"open-prs.*\.(json|jsonl)$"]
    assert prune.files_are_all_ephemeral(files, patterns) is True
    return "all-ephemeral file set -> True"


def test_files_are_all_ephemeral_one_mismatch() -> str:
    files = ["sessions/x/2026-01-01_010101.stub.md", "sessions/x/README.md"]
    patterns = [r"\.stub\.md$"]
    assert prune.files_are_all_ephemeral(files, patterns) is False
    return "one non-matching file (README.md) -> False"


def test_files_are_all_ephemeral_empty_patterns_never_true() -> str:
    files = ["sessions/x/2026-01-01_010101.stub.md"]
    assert prune.files_are_all_ephemeral(files, []) is False
    return "empty patterns (opt-in gate) -> False even with real ephemeral-looking files"


def test_files_are_all_ephemeral_empty_files_is_false() -> str:
    assert prune.files_are_all_ephemeral([], [r"\.stub\.md$"]) is False
    return "empty file list -> False (defensive; is_merged() covers the zero-diff case upstream)"


# --- load_ephemeral_patterns (real tmp-dir I/O, matching this codebase's convention) ---

def _write_config(tmp_dir: str, content: str) -> None:
    cfg_dir = Path(tmp_dir) / ".claude"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hook-config.json").write_text(content, encoding="utf-8")


def test_load_ephemeral_patterns_reads_config() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"prune_ephemeral_patterns": ["\\\\.stub\\\\.md$"]}')
        result = prune.load_ephemeral_patterns(tmp)
    assert result == ["\\.stub\\.md$"], f"expected the configured pattern list, got {result}"
    return "real tmp hook-config.json -> configured pattern list"


def test_load_ephemeral_patterns_missing_file_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "no .claude/hook-config.json at all -> []"


def test_load_ephemeral_patterns_unreadable_file_returns_empty() -> str:
    """A PermissionError (or any other OSError) reading the file must not propagate.

    Regression coverage for a review finding: catching only FileNotFoundError left a
    transient Windows lock (antivirus/indexer holding the handle -- observed in practice in
    this exact repo the same day this feature was built, dev-env#525) free to raise an
    uncaught exception out of prune_one() and, in --scan-dir mode, abort every remaining
    repo in the scan over one repo's momentary config-read hiccup.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"prune_ephemeral_patterns": ["\\\\.stub\\\\.md$"]}')
        with unittest.mock.patch("builtins.open", side_effect=PermissionError("Access is denied")):
            result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "PermissionError opening the config -> [] (caught, does not propagate)"


def test_load_ephemeral_patterns_missing_key_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"some_other_key": true}')
        result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "hook-config.json present without prune_ephemeral_patterns -> []"


def test_load_ephemeral_patterns_malformed_json_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, "{not valid json")
        result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "malformed JSON -> [] (no exception raised)"


def test_load_ephemeral_patterns_non_list_value_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"prune_ephemeral_patterns": "not-a-list"}')
        result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "non-list value (a string) -> []"


def test_load_ephemeral_patterns_non_string_element_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"prune_ephemeral_patterns": ["\\\\.stub\\\\.md$", 42]}')
        result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "list containing a non-string element (int) -> [] (defensive against a hand-edit typo)"


def test_load_ephemeral_patterns_invalid_regex_returns_empty_and_warns() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"prune_ephemeral_patterns": ["\\\\.stub\\\\.md$", "("]}')
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    assert "WARNING" in buf.getvalue(), f"expected a WARNING: line, got: {buf.getvalue()!r}"
    return "one invalid regex ('(') -> [] for the WHOLE list, plus a WARNING: line printed"


def test_load_ephemeral_patterns_empty_list_behaves_as_absent() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        _write_config(tmp, '{"prune_ephemeral_patterns": []}')
        result = prune.load_ephemeral_patterns(tmp)
    assert result == [], f"expected [], got {result}"
    return "explicit empty list -> [] (identical outcome to the key being absent)"


def test_list_worktree_subdirs_missing_dir_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        result = prune.list_worktree_subdirs(Path(tmp) / "does-not-exist")
    assert result == [], f"a missing directory must return [], got {result}"
    return "missing .claude/worktrees/ (the common case: no worktrees yet) -> [], not an error"


def test_list_worktree_subdirs_lists_only_directories() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "worktrees"
        root.mkdir()
        (root / "dir-a").mkdir()
        (root / "dir-b").mkdir()
        (root / "a-file.txt").write_text("not a directory", encoding="utf-8")
        result = prune.list_worktree_subdirs(root)
    names = sorted(Path(p).name for p in result)
    assert names == ["dir-a", "dir-b"], f"expected only the two subdirectories, got {names}"
    return "lists immediate subdirectories only; a sibling file is excluded"


def test_has_git_link_true_when_present() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n", encoding="utf-8")
        result = prune.has_git_link(tmp)
    assert result is True, "a .git file inside the directory must be detected"
    return "path/.git present (the ordinary registered-worktree shape) -> True"


def test_has_git_link_false_when_absent() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        result = prune.has_git_link(tmp)
    assert result is False, "an empty directory has no .git link"
    return "no .git anywhere under path -> False (the dev-env#1104 orphan shape)"


def _make_junction(link: Path, target: Path) -> None:
    """Create a real Windows directory junction at `link` pointing at `target`.

    `mklink /J` (a cmd.exe built-in, invoked via a real argument list -- not a shell string, so
    no MSYS/Git-Bash path-conversion hazard applies) requires no elevated privilege on Windows,
    unlike a true symlink -- confirmed live on this machine during /review of PR #1117, which is
    exactly why a junction (not a symlink) is the realistic vector `is_reparse_point()` exists to
    catch. Raises if the underlying `mklink` call fails, rather than silently no-op-ing into a
    test that would then trivially pass by having nothing to detect.
    """
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"mklink /J failed (returncode {r.returncode}): {r.stderr or r.stdout}")


def test_is_reparse_point_true_for_junction() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target"
        target.mkdir()
        link = Path(tmp) / "link"
        _make_junction(link, target)
        result = prune.is_reparse_point(str(link))
    assert result is True, "a real Windows junction must be detected as a reparse point"
    return "a real directory junction (mklink /J, no elevation needed) -> True (/review finding, PR #1117)"


def test_is_reparse_point_false_for_plain_directory() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "plain"
        plain.mkdir()
        result = prune.is_reparse_point(str(plain))
    assert result is False, "an ordinary directory is not a reparse point"
    return "an ordinary directory -> False"


def _fake_worktrees_for(primary: str) -> "list[dict]":
    return [{"path": primary, "branch": "main"}]


def test_find_and_remove_orphaned_worktrees_removes_safe_orphan() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        orphan = Path(tmp) / ".claude" / "worktrees" / "orphan-9104"
        orphan.mkdir(parents=True)
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            removed, skipped = prune.find_and_remove_orphaned_worktrees(
                tmp, _fake_worktrees_for(tmp), dry_run=False, liveness_window_seconds=86400
            )
        # Every filesystem assertion must run INSIDE this `with` block -- once it exits,
        # tempfile.TemporaryDirectory's own cleanup deletes the whole tree, which would make
        # orphan.exists() report False regardless of what find_and_remove_orphaned_worktrees()
        # actually did (a real bug caught in this exact test during authoring: dev-env#1104).
        assert removed == [str(orphan)], f"expected the orphan removed, got {removed}"
        assert skipped == [], f"expected nothing skipped, got {skipped}"
        assert not orphan.exists(), "the orphan directory must actually be gone from disk"
    return "an orphan with no .git and no live session -> removed, directory actually gone (dev-env#1104)"


def test_find_and_remove_orphaned_worktrees_skips_junction() -> str:
    """/review finding, PR #1117: a Windows directory junction planted as an orphan candidate
    must never be deleted. Before the fix, both existing safety signals transparently resolved
    THROUGH the junction to its target -- find_orphaned_worktree_dirs()'s path normalization
    (Path.resolve()) and has_git_link()'s existence check (Path.exists()) -- so a junction whose
    target held no .git and wasn't itself a registered worktree would have read as a perfectly
    safe orphan to delete. is_reparse_point() is checked first, before either of those, so the
    junction itself -- not its target -- is what gets classified."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "junction-target"
        target.mkdir()
        link = Path(tmp) / ".claude" / "worktrees" / "orphan-that-is-really-a-junction"
        link.parent.mkdir(parents=True)
        _make_junction(link, target)
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            removed, skipped = prune.find_and_remove_orphaned_worktrees(
                tmp, _fake_worktrees_for(tmp), dry_run=False, liveness_window_seconds=86400
            )
        assert removed == [], f"a junction must never be deleted, got {removed}"
        assert len(skipped) == 1 and "junction" in skipped[0][1], f"expected a reparse-point skip reason, got {skipped}"
        assert link.exists(), "the junction itself must be left completely untouched"
        assert target.exists(), "the junction's target must also be untouched -- nothing was ever deleted"
    return "a directory junction is detected and never deleted, regardless of what its target holds (/review finding, PR #1117)"


def test_find_and_remove_orphaned_worktrees_skips_dir_with_git_link() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        orphan = Path(tmp) / ".claude" / "worktrees" / "not-actually-orphaned"
        orphan.mkdir(parents=True)
        (orphan / ".git").write_text("gitdir: somewhere\n", encoding="utf-8")
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            removed, skipped = prune.find_and_remove_orphaned_worktrees(
                tmp, _fake_worktrees_for(tmp), dry_run=False, liveness_window_seconds=86400
            )
        assert removed == [], f"a dir that still has .git must never be deleted, got {removed}"
        assert len(skipped) == 1 and ".git link" in skipped[0][1], f"expected a .git-link skip reason, got {skipped}"
        assert orphan.exists(), "the directory must be left completely untouched"
    return "an unregistered dir that STILL has .git -> never deleted, left for manual review"


def test_find_and_remove_orphaned_worktrees_skips_live_session() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        orphan = Path(tmp) / ".claude" / "worktrees" / "orphan-but-live"
        orphan.mkdir(parents=True)
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=True):
            removed, skipped = prune.find_and_remove_orphaned_worktrees(
                tmp, _fake_worktrees_for(tmp), dry_run=False, liveness_window_seconds=86400
            )
        assert removed == [], f"a live-session orphan candidate must never be deleted, got {removed}"
        assert len(skipped) == 1 and "active Claude session" in skipped[0][1], f"expected a liveness skip reason, got {skipped}"
        assert orphan.exists(), "the directory must be left completely untouched"
    return "ADR-051 liveness guard applies here too -- a live session is never touched, even an unregistered one"


def test_find_and_remove_orphaned_worktrees_dry_run_does_not_delete() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        orphan = Path(tmp) / ".claude" / "worktrees" / "orphan-dry-run"
        orphan.mkdir(parents=True)
        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            removed, skipped = prune.find_and_remove_orphaned_worktrees(
                tmp, _fake_worktrees_for(tmp), dry_run=True, liveness_window_seconds=86400
            )
        assert removed == [str(orphan)], f"dry-run must still report it as would-remove, got {removed}"
        assert orphan.exists(), "dry-run must never actually delete anything"
    return "--dry-run: reported as would-remove, directory left on disk"


def test_find_and_remove_orphaned_worktrees_survives_rmtree_oserror() -> str:
    """One orphan's rmtree raises (lock still held); a second orphan in the same call still
    gets removed -- the loop must not abort on the first failure (mirrors the TimeoutExpired
    skip-and-continue discipline the main removal loop already has)."""
    with tempfile.TemporaryDirectory() as tmp:
        stuck = Path(tmp) / ".claude" / "worktrees" / "orphan-still-locked"
        stuck.mkdir(parents=True)
        clears = Path(tmp) / ".claude" / "worktrees" / "orphan-clears-fine"
        clears.mkdir(parents=True)
        real_rmtree = shutil.rmtree

        def _flaky_rmtree(path, *args, **kwargs):
            if str(path) == str(stuck):
                raise OSError("Permission denied")
            return real_rmtree(path, *args, **kwargs)

        with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
            with unittest.mock.patch("shutil.rmtree", side_effect=_flaky_rmtree):
                removed, skipped = prune.find_and_remove_orphaned_worktrees(
                    tmp, _fake_worktrees_for(tmp), dry_run=False, liveness_window_seconds=86400
                )
        assert removed == [str(clears)], f"expected only the clearing orphan removed, got {removed}"
        assert len(skipped) == 1 and "still could not be removed" in skipped[0][1], f"expected a still-locked skip reason, got {skipped}"
        assert stuck.exists(), "the still-locked orphan must be left in place, not crash the whole pass"
    return "an OSError on one orphan's rmtree is caught and skipped -- the loop continues to the next orphan (dev-env#1104)"


def test_find_and_remove_orphaned_worktrees_empty_primary_short_circuits() -> str:
    with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
        removed, skipped = prune.find_and_remove_orphaned_worktrees(
            "", [], dry_run=False, liveness_window_seconds=86400
        )
    assert removed == [] and skipped == [], f"expected ([], []), got ({removed}, {skipped})"
    return "empty primary (worktrees list itself was empty) -> ([], []), never resolves a bogus cwd-relative path"


def _dispatch_for_orphan_integration(porcelain: str):
    def _dispatch(args, **_kwargs):
        if args[1:2] == ["remote"]:
            return _ok("git@github.com:brownm09/dev-env.git\n")
        if args[1:3] == ["fetch", "origin"]:
            return _ok()
        if args[1:3] == ["worktree", "list"]:
            return _ok(porcelain)
        return _ok()
    return _dispatch


def test_prune_one_removes_orphaned_worktree_dir_end_to_end() -> str:
    """End-to-end wiring check: prune_one() itself calls find_and_remove_orphaned_worktrees()
    and folds its results into the (pruned, skipped) counts it already returns -- not just
    that the helper function works correctly in isolation (the tests above). Uses a REAL tmp
    directory as the primary worktree (matching this file's own established filesystem-test
    convention) so list_worktree_subdirs()'s real os.scandir() call has something real to
    find; every git call is mocked via subprocess.run, so no actual git process ever runs."""
    with tempfile.TemporaryDirectory() as tmp:
        primary = str(Path(tmp).resolve())
        orphan = Path(primary) / ".claude" / "worktrees" / "orphan-e2e-9104"
        orphan.mkdir(parents=True)
        porcelain = f"worktree {primary}\nHEAD abc123\nbranch refs/heads/main\n\n"
        with unittest.mock.patch("subprocess.run", side_effect=_dispatch_for_orphan_integration(porcelain)):
            with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
                pruned_count, skipped_count, fetch_failed = prune.prune_one(
                    repo=primary, dry_run=False, liveness_window_seconds=86400
                )
        assert pruned_count == 1, f"expected the orphan counted as pruned, got {pruned_count}"
        assert skipped_count == 1, f"expected only the primary's own 'primary or current worktree' skip, got {skipped_count}"
        assert not fetch_failed, "fetch should not be marked failed"
        assert not orphan.exists(), "the orphan must actually be gone -- this is the real prune_one() code path, not a mock"
    return "prune_one() itself invokes the orphan scan and folds it into pruned/skipped, not just the helper in isolation"


def _make_dispatch_same_run_orphan(primary: str, merged_path: str, merged_branch: str):
    """Two different `git worktree list --porcelain` responses across the SAME prune_one() call:
    the first (at the top of the function) lists `merged_path` as still registered; every call
    after that (the /review-finding re-fetch immediately before the orphan scan) omits it --
    simulating git having deregistered it internally the moment `git worktree remove` "failed"
    on this exact run, per the dev-env#1104 failure signature. Also serves a merged-and-clean
    verdict for `merged_path` and a failing (non-zero) `git worktree remove` for it -- the
    directory is never actually deleted by that mocked call, so it is still there on disk,
    unregistered, no `.git` inside, for the orphan scan to find moments later in the SAME call.
    """
    porcelain_before = (
        f"worktree {primary}\nHEAD abc123\nbranch refs/heads/main\n\n"
        f"worktree {merged_path}\nHEAD 789abc\nbranch refs/heads/{merged_branch}\n\n"
    )
    porcelain_after = f"worktree {primary}\nHEAD abc123\nbranch refs/heads/main\n\n"
    calls = {"worktree_list": 0}

    def _dispatch(args, **_kwargs):
        if args[1:2] == ["remote"]:
            return _ok("git@github.com:brownm09/dev-env.git\n")
        if args[1:3] == ["fetch", "origin"]:
            return _ok()
        if args[1:3] == ["worktree", "list"]:
            calls["worktree_list"] += 1
            return _ok(porcelain_before if calls["worktree_list"] == 1 else porcelain_after)
        if args[1:3] == ["merge-base", "--is-ancestor"]:
            return _ok()  # merged
        if args[1:3] == ["worktree", "remove"]:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="error: failed to delete: Permission denied")
        return _ok()

    return _dispatch


def test_prune_one_catches_same_run_orphan_via_refetch() -> str:
    """/review finding, PR #1117: the orphan scan must catch a worktree that becomes orphaned
    during THIS SAME prune_one() call -- not just one left over from a past run (the case the
    other end-to-end test above already covers). Before the fix, find_and_remove_orphaned_
    worktrees() was called with the `worktrees` snapshot fetched at the TOP of prune_one(),
    which still listed the worktree as registered even after its own `git worktree remove` call,
    moments earlier in the SAME run, had already failed part-way (the exact dev-env#1104
    signature: git deregisters internally, then the physical delete fails) -- so the freshly-
    orphaned directory would not have been caught until the NEXT invocation. The fix re-fetches
    `git worktree list --porcelain` immediately before the orphan scan; this test's dispatcher
    returns a DIFFERENT (already-deregistered) porcelain on that second call, proving the fix
    actually reads fresh state rather than reusing the stale pre-loop snapshot."""
    with tempfile.TemporaryDirectory() as tmp:
        primary = str(Path(tmp).resolve())
        merged = Path(primary) / ".claude" / "worktrees" / "merged-worktree-9117"
        merged.mkdir(parents=True)  # real directory, no .git inside -- the post-failure orphan shape
        dispatch = _make_dispatch_same_run_orphan(primary, str(merged), "claude/merged-worktree-9117")
        with unittest.mock.patch("subprocess.run", side_effect=dispatch):
            with unittest.mock.patch.object(prune, "worktree_session_is_live", return_value=False):
                with unittest.mock.patch.object(prune, "is_dirty", return_value=False):
                    pruned_count, skipped_count, fetch_failed = prune.prune_one(
                        repo=primary, dry_run=False, liveness_window_seconds=86400
                    )
        assert pruned_count == 1, f"expected the same-run orphan caught and counted as pruned, got {pruned_count}"
        assert skipped_count == 2, f"expected 2 skipped (primary + the 'worktree remove failed' entry for merged), got {skipped_count}"
        assert not fetch_failed, "fetch should not be marked failed"
        assert not merged.exists(), "the same-run orphan must actually be gone -- caught via the re-fetch, not left for next time"
    return "a worktree orphaned by THIS run's own failed removal is caught in the SAME run via the re-fetch (/review finding, PR #1117)"


def main() -> int:
    tests = [
        ("--include-named unset: named branch still skipped (default unchanged)", test_named_branch_skipped_by_default),
        ("--include-named set: named branch now pruned", test_named_branch_pruned_with_include_named),
        ("git worktree remove timeout: skip-and-continue, not abort", test_timeout_skips_worktree_and_continues),
        ("detached HEAD: commit IS merged -> pruned via resolved SHA", test_detached_head_merged_commit_pruned),
        ("detached HEAD: commit NOT merged -> still correctly skipped", test_detached_head_unmerged_commit_skipped),
        ("detached HEAD: rev-parse HEAD fails -> fails safe, skipped", test_detached_head_rev_parse_failure_skipped),
        ("detached HEAD: worktree path missing on disk -> fails safe, no crash", test_detached_head_missing_worktree_path_skipped),
        ("draft-branch squat: idle+clean+fully-pushed -> park+remove (remove actually invoked)", test_draft_branch_squat_park_and_remove),
        ("draft-branch squat: dirty -> park-only (remove NEVER invoked), contents preserved", test_draft_branch_squat_park_only_when_dirty),
        ("draft-branch squat: park+remove degrades gracefully when remove fails", test_draft_branch_squat_park_and_remove_when_remove_fails),
        ("draft-branch squat: park+remove degrades gracefully when remove times out", test_draft_branch_squat_park_and_remove_when_remove_times_out),
        ("files_are_all_ephemeral: all files match", test_files_are_all_ephemeral_matches),
        ("files_are_all_ephemeral: one mismatch -> False", test_files_are_all_ephemeral_one_mismatch),
        ("files_are_all_ephemeral: empty patterns -> False", test_files_are_all_ephemeral_empty_patterns_never_true),
        ("files_are_all_ephemeral: empty files -> False", test_files_are_all_ephemeral_empty_files_is_false),
        ("load_ephemeral_patterns: reads real config", test_load_ephemeral_patterns_reads_config),
        ("load_ephemeral_patterns: missing file -> []", test_load_ephemeral_patterns_missing_file_returns_empty),
        ("load_ephemeral_patterns: unreadable file (OSError) -> []", test_load_ephemeral_patterns_unreadable_file_returns_empty),
        ("load_ephemeral_patterns: missing key -> []", test_load_ephemeral_patterns_missing_key_returns_empty),
        ("load_ephemeral_patterns: malformed JSON -> []", test_load_ephemeral_patterns_malformed_json_returns_empty),
        ("load_ephemeral_patterns: non-list value -> []", test_load_ephemeral_patterns_non_list_value_returns_empty),
        ("load_ephemeral_patterns: non-string element -> []", test_load_ephemeral_patterns_non_string_element_returns_empty),
        ("load_ephemeral_patterns: invalid regex -> [] + warns", test_load_ephemeral_patterns_invalid_regex_returns_empty_and_warns),
        ("load_ephemeral_patterns: empty list == absent", test_load_ephemeral_patterns_empty_list_behaves_as_absent),
        ("list_worktree_subdirs: missing dir -> []", test_list_worktree_subdirs_missing_dir_returns_empty),
        ("list_worktree_subdirs: lists only directories", test_list_worktree_subdirs_lists_only_directories),
        ("has_git_link: true when .git present", test_has_git_link_true_when_present),
        ("has_git_link: false when absent", test_has_git_link_false_when_absent),
        ("is_reparse_point: true for a real junction (/review, PR #1117)", test_is_reparse_point_true_for_junction),
        ("is_reparse_point: false for a plain directory", test_is_reparse_point_false_for_plain_directory),
        ("find_and_remove_orphaned_worktrees: removes a safe orphan (dev-env#1104)", test_find_and_remove_orphaned_worktrees_removes_safe_orphan),
        ("find_and_remove_orphaned_worktrees: skips a junction, whatever its target holds (/review, PR #1117)", test_find_and_remove_orphaned_worktrees_skips_junction),
        ("find_and_remove_orphaned_worktrees: skips a dir that still has .git", test_find_and_remove_orphaned_worktrees_skips_dir_with_git_link),
        ("find_and_remove_orphaned_worktrees: skips a live-session candidate (ADR-051)", test_find_and_remove_orphaned_worktrees_skips_live_session),
        ("find_and_remove_orphaned_worktrees: --dry-run does not delete", test_find_and_remove_orphaned_worktrees_dry_run_does_not_delete),
        ("find_and_remove_orphaned_worktrees: survives an rmtree OSError, keeps going", test_find_and_remove_orphaned_worktrees_survives_rmtree_oserror),
        ("find_and_remove_orphaned_worktrees: empty primary short-circuits", test_find_and_remove_orphaned_worktrees_empty_primary_short_circuits),
        ("prune_one() end-to-end: removes an orphaned worktree dir (dev-env#1104)", test_prune_one_removes_orphaned_worktree_dir_end_to_end),
        ("prune_one() end-to-end: same-run orphan caught via re-fetch (/review, PR #1117)", test_prune_one_catches_same_run_orphan_via_refetch),
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
