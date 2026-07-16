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
import re
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _worktree_canon  # noqa: E402

canonical_root_from_worktree = _worktree_canon.canonical_root_from_worktree
canonical_repo_root = _worktree_canon.canonical_repo_root
match_worktree = _worktree_canon.match_worktree
worktree_root_from_path = _worktree_canon.worktree_root_from_path
is_worktree_path = _worktree_canon.is_worktree_path

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


def test_sibling_convention_bare_worktrees_dir_not_matched() -> str:
    # Review finding, dev-env#760: a directory literally named "-worktrees" with no
    # repo-name prefix at all must not match — pre-tool-use-canonical-mutate-guard.py's
    # equivalent fragment already rejected this; this pattern must agree with it.
    cwd = "C:/Foo/-worktrees/x"
    assert canonical_root_from_worktree(cwd) is None
    assert canonical_repo_root(cwd) == cwd
    return "a bare, unprefixed '-worktrees' directory does not match (dev-env#760 review finding)"


def test_nested_worktree_inside_sibling_worktree_resolves_to_inner() -> str:
    # Review finding, dev-env#760: a nested-convention worktree created inside a
    # sibling-convention worktree must resolve to ITS OWN (deeper) root, not the outer
    # sibling directory — the nested pattern is tried first for exactly this reason.
    cwd = "C:/Users/brown/Git/dev-env-worktrees/adr-096/.claude/worktrees/some-name"
    expected = "C:/Users/brown/Git/dev-env-worktrees/adr-096"
    assert canonical_root_from_worktree(cwd) == expected
    assert canonical_repo_root(cwd) == expected
    return "a nested worktree inside a sibling worktree resolves to the correct (inner) canonical root (dev-env#760 review finding)"


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


# --- dev-env#510: new SSOT functions consumed by the two PreToolUse guards -----


def test_match_worktree_exposes_both_groups() -> str:
    # The single matcher every other function is built on: group(1) is the
    # canonical-root PREFIX, group(0) is the full worktree ROOT (prefix + marker).
    m = match_worktree(WT_FWD)
    assert m is not None
    assert m.group(1) == CANON_WIN
    assert m.group(0) == WT_FWD  # bare nested root, no trailing subdir
    deep = match_worktree(WT_FWD + "/claude/scripts")
    assert deep.group(1) == CANON_WIN and deep.group(0) == WT_FWD
    ms = match_worktree(SIBLING_FWD)
    assert ms is not None
    assert ms.group(1) == CANON_WIN and ms.group(0) == SIBLING_FWD
    # no-match / empty / None
    assert match_worktree(CANON_WIN) is None
    assert match_worktree("") is None
    assert match_worktree(None) is None
    return "match_worktree exposes group(1)=canonical root, group(0)=worktree root; None on no-match/empty/None"


def test_worktree_root_from_path_returns_full_root() -> str:
    # group(0) of the shared match — the full worktree root, both conventions.
    assert worktree_root_from_path(WT_FWD) == WT_FWD
    assert worktree_root_from_path(WT_FWD + "/claude/scripts") == WT_FWD
    assert worktree_root_from_path(SIBLING_FWD) == SIBLING_FWD
    assert worktree_root_from_path(SIBLING_FWD + "/a/b") == SIBLING_FWD
    assert worktree_root_from_path(CANON_WIN) is None
    assert worktree_root_from_path("C:/Users/brown/Git/dev-env-188") is None
    assert worktree_root_from_path("") is None
    assert worktree_root_from_path(None) is None
    return "worktree_root_from_path returns the full worktree root (prefix + marker), None on no-match"


def test_worktree_root_from_path_matches_mutate_guard_fixtures() -> str:
    # Equivalence pin (dev-env#510): worktree_root_from_path returns exactly what
    # pre-tool-use-canonical-mutate-guard.py's former _NESTED_WORKTREE_ROOT_RE /
    # _SIBLING_WORKTREE_ROOT_RE produced for the ABSOLUTE cwds that hook ever
    # passes — the fixtures lifted verbatim from test_canonical_mutate_guard.py.
    cases = [
        ("C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name",
         "C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name"),
        ("C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name/some/nested/path",
         "C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name"),
        (r"C:\Users\brown\Git\dev-env\.CLAUDE\WORKTREES\Some-Worktree-Name",
         r"C:\Users\brown\Git\dev-env\.CLAUDE\WORKTREES\Some-Worktree-Name"),
        ("C:/Users/brown/Git/dev-env-worktrees/some-worktree-name",
         "C:/Users/brown/Git/dev-env-worktrees/some-worktree-name"),
        ("C:/Users/brown/Git/dev-env-worktrees/some-worktree-name/some/nested/path",
         "C:/Users/brown/Git/dev-env-worktrees/some-worktree-name"),
    ]
    for path, expected in cases:
        got = worktree_root_from_path(path)
        assert got == expected, f"expected {expected!r}, got {got!r} for {path!r}"
    for path in ("C:/Users/brown/Git/dev-env", "C:/Users/brown/Git/dev-env-188"):
        assert worktree_root_from_path(path) is None, f"expected None for {path!r}"
    return "worktree_root_from_path equivalence with the mutate-guard's former anchored root regexes (absolute paths)"


def test_is_worktree_path_boolean() -> str:
    assert is_worktree_path(WT_FWD) is True
    assert is_worktree_path(WT_BACK) is True
    assert is_worktree_path(SIBLING_FWD) is True
    assert is_worktree_path(WT_POSIX) is True
    # a resolved worktree root whose NAME ends in "-fallback" still matches (the
    # exact shape test_is_confirmed_worktree_root_decision_table's backstop uses)
    assert is_worktree_path(WT_FWD + "-fallback") is True
    assert is_worktree_path(CANON_WIN) is False
    assert is_worktree_path("C:/some/unresolvable/path") is False
    assert is_worktree_path("C:/Users/brown/Git/dev-env-188") is False
    assert is_worktree_path("") is False
    assert is_worktree_path(None) is False
    return "is_worktree_path: True for both conventions (incl. resolved roots), False for canonical/bare-suffix/empty/None"


def test_is_worktree_path_agrees_with_former_unanchored_search_for_absolute_paths() -> str:
    # Equivalence pin (dev-env#510): is_worktree_path (an anchored match) agrees
    # with the mutate-guard's FORMER unanchored `_WORKTREE_RE.search` for every
    # ABSOLUTE path that hook passes it (a git-resolved --show-toplevel).
    # Reconstruct that exact former regex here and assert agreement.
    former = re.compile(
        r"[/\\](?:\.claude[/\\]worktrees|[^/\\]+-worktrees)[/\\][^/\\]+",
        re.IGNORECASE,
    )
    absolute_paths = [
        "C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name",
        "C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name-fallback",
        "C:/Users/brown/Git/dev-env-worktrees/some-worktree-name",
        r"C:\Users\brown\Git\dev-env\.claude\worktrees\foo",
        "/home/user/dev-env/.claude/worktrees/foo-123",
        "C:/Users/brown/Git/dev-env",       # canonical, no marker
        "C:/Users/brown/Git/dev-env-188",   # bare-suffix sibling, no marker
        "C:/some/unresolvable/path",
    ]
    for path in absolute_paths:
        anchored = is_worktree_path(path)
        unanchored = former.search(path) is not None
        assert anchored == unanchored, (
            f"is_worktree_path={anchored} but former .search={unanchored} for {path!r}"
        )
    # The sole divergence is a marker at the very START of a RELATIVE path, which
    # never occurs as a resolved toplevel: the anchored sibling pattern matches it,
    # the unanchored search (requiring a leading separator) does not. Documented so
    # this boundary is a pinned, understood limit rather than a silent trap.
    rel = "dev-env-worktrees/foo"
    assert is_worktree_path(rel) is True
    assert (former.search(rel) is not None) is False
    return "is_worktree_path == former unanchored .search for all absolute roots; sole divergence (relative marker-at-start) documented and unreachable in practice"


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
        ("bare unprefixed '-worktrees' dir not matched (dev-env#760 review finding)", test_sibling_convention_bare_worktrees_dir_not_matched),
        ("nested worktree inside sibling worktree resolves to inner root (dev-env#760 review finding)", test_nested_worktree_inside_sibling_worktree_resolves_to_inner),
        ("no-match -> None (canonical_root_from_worktree)", test_no_match_returns_none_for_from_worktree),
        ("no-match -> passthrough (canonical_repo_root)", test_no_match_passes_through_for_repo_root),
        ("bare-suffix sibling worktree still not matched by regex", test_sibling_worktree_not_matched_by_regex),
        ("empty/None input contracts", test_empty_and_none_contracts),
        ("match_worktree exposes both capture groups (dev-env#510)", test_match_worktree_exposes_both_groups),
        ("worktree_root_from_path returns the full root (dev-env#510)", test_worktree_root_from_path_returns_full_root),
        ("worktree_root_from_path equivalence w/ mutate-guard fixtures (dev-env#510)", test_worktree_root_from_path_matches_mutate_guard_fixtures),
        ("is_worktree_path boolean shape check (dev-env#510)", test_is_worktree_path_boolean),
        ("is_worktree_path == former unanchored .search for absolute paths (dev-env#510)", test_is_worktree_path_agrees_with_former_unanchored_search_for_absolute_paths),
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
