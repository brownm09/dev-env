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
2026-07-12 incident.

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

Usage:
    py -3 claude/scripts/tests/test_prune_merged_worktrees.py

Exit 0 = all pass.
"""
import importlib.util
import io
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
    if args[1:3] == ["merge-base"]:          # is_merged → merged (returncode 0)
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
    if args[1:3] == ["merge-base"]:          # is_merged → merged (returncode 0)
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


def _dispatch_draft_squat(args, **_kwargs):
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
        return _ok()
    return _ok()


def test_draft_branch_squat_park_and_remove() -> str:
    """dev-env#747: a non-canonical worktree squatting draft/YYYY-MM-DD, idle, clean, and
    fully pushed, is parked AND removed in the same pass -- unlike a main-squatter (which is
    only ever parked, never auto-removed), this shape is safe to fully clean up because
    nothing else can legitimately need that exact throwaway worktree again."""
    with unittest.mock.patch("subprocess.run", side_effect=_dispatch_draft_squat):
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
    return "idle+clean+fully-pushed draft-branch squatter: parked and removed, pruned=1"


def test_draft_branch_squat_park_only_when_dirty() -> str:
    """A dirty squatter is parked (frees the branch name) but NOT removed -- its uncommitted
    content is preserved for human review, mirroring the real stub-823-120134 disposition
    from dev-env#747's live incident."""
    with unittest.mock.patch("subprocess.run", side_effect=_dispatch_draft_squat):
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
    return "dirty draft-branch squatter: parked only (branch freed), worktree left in place"


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


def main() -> int:
    tests = [
        ("--include-named unset: named branch still skipped (default unchanged)", test_named_branch_skipped_by_default),
        ("--include-named set: named branch now pruned", test_named_branch_pruned_with_include_named),
        ("git worktree remove timeout: skip-and-continue, not abort", test_timeout_skips_worktree_and_continues),
        ("draft-branch squat: idle+clean+fully-pushed -> park+remove", test_draft_branch_squat_park_and_remove),
        ("draft-branch squat: dirty -> park-only, contents preserved", test_draft_branch_squat_park_only_when_dirty),
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
