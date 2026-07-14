#!/usr/bin/env python3
"""Unit tests for _worktree_canon.py (dev-env#454 / ADR-073).

`_worktree_canon.py` extracts the `_WORKTREE_RE` regex that post-tool-use.py and
reconcile-project-board.py both independently defined. Pins the shared regex match AND
the two callers' divergent no-match contracts side by side, so the reconciliation itself —
not just each function's happy path — is covered here (each script's own test file still
covers the same functions through its module-attribute indirection, unchanged by this
extraction).

dev-env#760 adds a second recognized convention (`<repo>-worktrees/<name>`, a sibling
directory, alongside the original nested `.claude/worktrees/<name>`) — see the
"sibling-directory convention" test group below. The pre-existing "sibling worktree not
matched" test pins a *different*, still-unmatched shape (`dev-env-188`, no `-worktrees`
marker) — the two are not the same case; see that test's own comment.

Usage:
    py -3 claude/scripts/tests/test_worktree_canon.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _worktree_canon  # noqa: E402

canonical_root_from_worktree = _worktree_canon.canonical_root_from_worktree
canonical_repo_root = _worktree_canon.canonical_repo_root

WT_FWD = "C:/Users/brown/Git/dev-env/.claude/worktrees/sweet-mendel-8e98d1"
WT_BACK = r"C:\Users\brown\Git\dev-env\.claude\worktrees\sweet-mendel-8e98d1"
CANON_WIN = "C:/Users/brown/Git/dev-env"
CANON_POSIX = "/home/user/dev-env"
WT_POSIX = CANON_POSIX + "/.claude/worktrees/foo-123"

# dev-env#760 sibling-directory convention fixtures — real shape confirmed via
# `git worktree list` (dev-env-worktrees/adr-096-correction).
SIBLING_FWD = "C:/Users/brown/Git/dev-env-worktrees/adr-096-correction"
SIBLING_BACK = r"C:\Users\brown\Git\dev-env-worktrees\adr-096-correction"


# --- shared-regex match cases (both functions must agree when there IS a match) ------


def test_forward_slash_match() -> str:
    assert canonical_root_from_worktree(WT_FWD) == CANON_WIN
    assert canonical_repo_root(WT_FWD) == CANON_WIN
    return "forward-slash worktree cwd -> canonical root, both functions agree"


def test_backslash_match() -> str:
    assert canonical_root_from_worktree(WT_BACK) == r"C:\Users\brown\Git\dev-env"
    assert canonical_repo_root(WT_BACK) == r"C:\Users\brown\Git\dev-env"
    return "backslash worktree cwd -> canonical root (separator preserved), both functions agree"


def test_subdir_beyond_worktree_name() -> str:
    deep = WT_FWD + "/claude/scripts"
    assert canonical_root_from_worktree(deep) == CANON_WIN
    assert canonical_repo_root(deep) == CANON_WIN
    return "a cwd nested below the worktree name still resolves to the canonical root"


def test_posix_path_match() -> str:
    assert canonical_root_from_worktree(WT_POSIX) == CANON_POSIX
    assert canonical_repo_root(WT_POSIX) == CANON_POSIX
    return "POSIX worktree path -> canonical root, both functions agree"


# --- sibling-directory convention (dev-env#760) ---------------------------------------


def test_sibling_convention_forward_slash_match() -> str:
    assert canonical_root_from_worktree(SIBLING_FWD) == CANON_WIN
    assert canonical_repo_root(SIBLING_FWD) == CANON_WIN
    return "forward-slash <repo>-worktrees/<name> cwd -> canonical root, both functions agree"


def test_sibling_convention_backslash_match() -> str:
    assert canonical_root_from_worktree(SIBLING_BACK) == r"C:\Users\brown\Git\dev-env"
    assert canonical_repo_root(SIBLING_BACK) == r"C:\Users\brown\Git\dev-env"
    return "backslash <repo>-worktrees/<name> cwd -> canonical root (separator preserved), both functions agree"


def test_sibling_convention_subdir_beyond_worktree_name() -> str:
    deep = SIBLING_FWD + "/claude/scripts"
    assert canonical_root_from_worktree(deep) == CANON_WIN
    assert canonical_repo_root(deep) == CANON_WIN
    return "a cwd nested below the sibling worktree name still resolves to the canonical root"


def test_sibling_convention_repo_name_with_hyphen() -> str:
    # The repo name itself containing a hyphen (engineering-journal) must not confuse the
    # non-greedy canonical-root capture into stopping at the wrong "-" component.
    cwd = "C:/Users/brown/Git/engineering-journal-worktrees/compose-2026-07-14"
    expected = "C:/Users/brown/Git/engineering-journal"
    assert canonical_root_from_worktree(cwd) == expected
    assert canonical_repo_root(cwd) == expected
    return "a hyphenated repo name resolves to the correct (full) canonical root"


# --- divergent no-match contracts (the reconciliation pin) ---------------------------


def test_no_match_returns_none_for_from_worktree() -> str:
    got = canonical_root_from_worktree(CANON_WIN)
    assert got is None, f"expected None, got {got!r}"
    return "canonical_root_from_worktree returns None on no-match (post-tool-use.py's contract)"


def test_no_match_passes_through_for_repo_root() -> str:
    got = canonical_repo_root(CANON_WIN)
    assert got == CANON_WIN, f"expected passthrough, got {got!r}"
    return "canonical_repo_root passes the input through unchanged on no-match (reconcile-project-board.py's contract)"


def test_sibling_worktree_not_matched_by_regex() -> str:
    # A BARE <repo>-<suffix> sibling (dev-env-188) has no "-worktrees" marker segment, so
    # it's still ambiguous from the path string alone (is "-188" a worktree suffix, or an
    # unrelated repo?) and the pure regex misses it by design, even after dev-env#760 added
    # recognition of the *marked* `<repo>-worktrees/<name>` sibling-directory shape (see the
    # "sibling-directory convention" test group above, which this case is deliberately NOT
    # part of) — post-tool-use.py's canonical_root_via_git (which stays local to that file,
    # not shared here) handles this bare-suffix case via git instead.
    sibling = "C:/Users/brown/Git/dev-env-188"
    assert canonical_root_from_worktree(sibling) is None
    assert canonical_repo_root(sibling) == sibling
    return "bare-suffix sibling path -> None / passthrough per each contract (git fallback is out of scope here)"


def test_empty_and_none_contracts() -> str:
    assert canonical_root_from_worktree("") is None
    assert canonical_root_from_worktree(None) is None
    assert canonical_repo_root("") == ""
    assert canonical_repo_root(None) == ""
    return "empty/None input: the None-contract stays None, the passthrough-contract yields ''"


def main() -> int:
    tests = [
        ("forward-slash worktree match (both functions)", test_forward_slash_match),
        ("backslash worktree match (both functions)", test_backslash_match),
        ("subdir beyond worktree name (both functions)", test_subdir_beyond_worktree_name),
        ("POSIX worktree path match (both functions)", test_posix_path_match),
        ("sibling-directory convention: forward-slash match (dev-env#760)", test_sibling_convention_forward_slash_match),
        ("sibling-directory convention: backslash match (dev-env#760)", test_sibling_convention_backslash_match),
        ("sibling-directory convention: subdir beyond worktree name (dev-env#760)", test_sibling_convention_subdir_beyond_worktree_name),
        ("sibling-directory convention: hyphenated repo name (dev-env#760)", test_sibling_convention_repo_name_with_hyphen),
        ("no-match -> None (canonical_root_from_worktree)", test_no_match_returns_none_for_from_worktree),
        ("no-match -> passthrough (canonical_repo_root)", test_no_match_passes_through_for_repo_root),
        ("bare-suffix sibling worktree still not matched by regex", test_sibling_worktree_not_matched_by_regex),
        ("empty/None input contracts", test_empty_and_none_contracts),
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
