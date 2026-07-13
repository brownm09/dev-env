#!/usr/bin/env python3
"""Unit tests for _worktree_topology.py — the worktree-on-`main` topology helpers.

Covers the pure logic that detects a worktree squatting `main` (and the canonical being
knocked off `main`) and decides the non-destructive correction (dev-env#396, ADR-058):

  1. parse_worktree_porcelain()  — path/branch/detached/refs-heads-stripping (pure).
  2. canonical_worktree()        — first entry is the primary; empty -> None.
  3. park_branch_for()           — basename -> claude/<slug> (Windows + POSIX spellings).
  4. main_squatter()             — the non-canonical worktree on main, or None (incl. a
                                   squatter at a non-adjacent index, and None for a bare /
                                   detached canonical that can't hold main itself).
  5. canonical_on_main()         — primary-on-main predicate.
  6. diagnose_main_topology()    — healthy vs. squat vs. canonical-off-main-no-squatter.
  7. canonical_sync_action()     — warn-squatter / return-canonical / warn-dirty / on-main.
  8. merge_park_target()         — park a repo's own worktree left on main; else None
                                   (empty / cwd==canonical / not-on-main / cross-repo / spelling).
  9. resolve_current_branch()    — `git symbolic-ref` returncode!=0 -> "<detached>" sentinel,
                                   else stripped stdout (dev-env#619).
 10. canonical_sync_action() again, fed "<detached>" through the FULL pipeline (not just the
                                   isolated helper) — the exact dev-env#619 regression scenario.
 11. is_hijacked_branch()        — the dev-env#630 hijack signature: "<detached>" or a
                                   "claude/*" branch on the canonical; not main / draft/* /
                                   any other named branch; None/"" safe (no raise).
 12. DRAFT_BRANCH_RE              — draft/YYYY-MM-DD and -recovery suffix match; malformed
                                   dates / other suffixes / other branches don't (dev-env#747).
 13. non_canonical_worktrees_matching() — finds every non-canonical squatter of a pattern
                                   (not just the first); the canonical itself is never
                                   flagged even when it legitimately holds the pattern; no
                                   match anywhere -> [].
 14. pattern_squat_action()       — warn-live (never touch a live session) / park-and-remove
                                   (idle, clean, fully pushed) / park-only (dirty, or not
                                   provably fully pushed — the conservative default).

Fully offline — no git, no network, no filesystem writes (paths need not exist; the
module resolves them for comparison only). The git-driven prune/post-merge/dev-env-sync/
journal-canonical-guard loops are exercised by --dry-run / live behavior in the PR, not here.

Usage:
    py -3 claude/scripts/tests/test_worktree_topology.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _worktree_topology as wt  # noqa: E402
from _worktree_topology import MainTopology  # noqa: E402

CANON = "C:/Users/brown/Git/dev-env"
WT_FOO = "C:/Users/brown/Git/dev-env/.claude/worktrees/foo-bar-abc123"
WT_SQUAT = "C:/Users/brown/Git/dev-env/.claude/worktrees/squat-stonebraker-2156f6"


def _porcelain(entries: "list[tuple[str, str | None]]") -> str:
    """Build `git worktree list --porcelain` text. branch=None -> a detached HEAD."""
    blocks = []
    for path, branch in entries:
        lines = [f"worktree {path}", "HEAD " + "0" * 40]
        lines.append("detached" if branch is None else f"branch refs/heads/{branch}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def test_parse_worktree_porcelain() -> str:
    text = _porcelain([(CANON, "main"), (WT_FOO, "claude/foo-bar-abc123"), (WT_SQUAT, None)])
    got = wt.parse_worktree_porcelain(text)
    expected = [
        {"path": CANON, "branch": "main"},
        {"path": WT_FOO, "branch": "claude/foo-bar-abc123"},
        {"path": WT_SQUAT, "branch": "<detached>"},
    ]
    if got != expected:
        raise AssertionError(f"parsed {got}, expected {expected}")
    if wt.parse_worktree_porcelain("") != []:
        raise AssertionError("empty input must parse to []")
    return "3 worktrees parsed; refs/heads/ stripped; detached -> '<detached>'; '' -> []"


def test_canonical_worktree() -> str:
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "main"), (WT_FOO, "claude/x")]))
    if wt.canonical_worktree(worktrees)["path"] != CANON:
        raise AssertionError("canonical must be the first worktree entry")
    if wt.canonical_worktree([]) is not None:
        raise AssertionError("no worktrees -> None")
    return "first entry is canonical; empty list -> None"


def test_park_branch_for() -> str:
    cases = [
        (WT_SQUAT, "claude/squat-stonebraker-2156f6"),
        (r"C:\Users\brown\Git\dev-env\.claude\worktrees\agitated-stonebraker-2156f6",
         "claude/agitated-stonebraker-2156f6"),
        ("C:/Users/brown/Git/lifting-logbook/.claude/worktrees/zesty-mayer-9f9f9f",
         "claude/zesty-mayer-9f9f9f"),
    ]
    for path, expected in cases:
        got = wt.park_branch_for(path)
        if got != expected:
            raise AssertionError(f"park_branch_for({path!r}) = {got!r}, expected {expected!r}")
    return "claude/<basename> derived; Windows and POSIX spellings agree"


def test_main_squatter_found() -> str:
    # Canonical off main (pr-385) + a non-canonical worktree squatting main.
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385"), (WT_SQUAT, "main")]))
    sq = wt.main_squatter(worktrees)
    if sq is None or sq["path"] != WT_SQUAT:
        raise AssertionError(f"expected squatter {WT_SQUAT}, got {sq}")
    return "non-canonical worktree on main is identified as the squatter"


def test_main_squatter_none_when_canonical_on_main() -> str:
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "main"), (WT_FOO, "claude/x")]))
    if wt.main_squatter(worktrees) is not None:
        raise AssertionError("canonical on main -> no squatter")
    return "canonical holds main -> no squatter"


def test_main_squatter_none_when_main_free() -> str:
    # Canonical off main but nobody is on main (the ref is free).
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385"), (WT_FOO, "claude/x")]))
    if wt.main_squatter(worktrees) is not None:
        raise AssertionError("main free (nobody on it) -> no squatter")
    return "canonical off main, ref free -> no squatter"


def test_main_squatter_multiple_worktrees() -> str:
    # Several non-canonical worktrees; a LATER one squats main (exercises the worktrees[1:]
    # loop past index 1, not just an adjacent pair).
    worktrees = wt.parse_worktree_porcelain(_porcelain([
        (CANON, "pr-385"),
        (WT_FOO, "claude/foo-bar-abc123"),
        (WT_SQUAT, "main"),
    ]))
    sq = wt.main_squatter(worktrees)
    if sq is None or sq["path"] != WT_SQUAT:
        raise AssertionError(f"a squatter at a non-adjacent index must be found, got {sq}")
    return "squatter found at index>1 among several non-canonical worktrees"


def test_main_squatter_bare_or_detached_canonical() -> str:
    # A bare/empty-branch primary cannot hold a working-tree checkout of main, so a secondary
    # worktree on main there is LEGITIMATE (main must live somewhere) — never a squatter. Else
    # prune/dev-env-sync would mis-park a bare/detached-primary repo's real main worktree.
    bare = wt.parse_worktree_porcelain(_porcelain([(CANON, ""), (WT_FOO, "main")]))
    if wt.main_squatter(bare) is not None:
        raise AssertionError("bare/empty-branch canonical -> no squatter (secondary-on-main is legit)")
    # A detached primary likewise can't assert the on-main invariant.
    det = wt.parse_worktree_porcelain(_porcelain([(CANON, None), (WT_FOO, "main")]))
    if wt.main_squatter(det) is not None:
        raise AssertionError("detached canonical -> no squatter")
    return "bare or detached canonical never flags a main worktree as a squatter"


def test_canonical_on_main() -> str:
    on = wt.parse_worktree_porcelain(_porcelain([(CANON, "main")]))
    off = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385")]))
    if not wt.canonical_on_main(on):
        raise AssertionError("canonical on main must read True")
    if wt.canonical_on_main(off):
        raise AssertionError("canonical off main must read False")
    if wt.canonical_on_main([]):
        raise AssertionError("empty list must read False")
    return "True on main, False off main, False for empty"


def test_diagnose_healthy() -> str:
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "main"), (WT_FOO, "claude/x")]))
    topo = wt.diagnose_main_topology(worktrees)
    if not topo.healthy or topo.squatter_path is not None or topo.canonical_branch != "main":
        raise AssertionError(f"expected healthy on-main topology, got {topo}")
    return "canonical on main + no squatter -> healthy"


def test_diagnose_squat() -> str:
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385"), (WT_SQUAT, "main")]))
    topo = wt.diagnose_main_topology(worktrees)
    if topo.healthy:
        raise AssertionError("a squat is not healthy")
    if topo.canonical_branch != "pr-385" or topo.squatter_path != WT_SQUAT or topo.squatter_branch != "main":
        raise AssertionError(f"squat fields wrong: {topo}")
    return "squat diagnosed: canonical_branch + squatter_path/branch populated, healthy=False"


def test_diagnose_canonical_off_main_no_squatter() -> str:
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385"), (WT_FOO, "claude/x")]))
    topo = wt.diagnose_main_topology(worktrees)
    if topo.healthy or topo.squatter_path is not None or topo.canonical_branch != "pr-385":
        raise AssertionError(f"expected off-main-no-squatter, got {topo}")
    if wt.diagnose_main_topology([]).healthy is not True:
        raise AssertionError("empty worktree list -> healthy (nothing to correct)")
    return "canonical off main, ref free -> not healthy, squatter None; empty -> healthy"


def test_canonical_sync_action() -> str:
    squat = MainTopology(CANON, "pr-385", WT_SQUAT, "main", False)
    a = wt.canonical_sync_action(squat, canonical_clean=True)
    if a.kind != "warn-squatter" or a.squatter_path != WT_SQUAT or a.park_branch != "claude/squat-stonebraker-2156f6":
        raise AssertionError(f"squatter case wrong: {a}")

    free = MainTopology(CANON, "pr-385", None, None, False)
    if wt.canonical_sync_action(free, canonical_clean=True).kind != "return-canonical":
        raise AssertionError("main free + clean canonical -> return-canonical")
    if wt.canonical_sync_action(free, canonical_clean=False).kind != "warn-dirty":
        raise AssertionError("main free + dirty canonical -> warn-dirty (preserve drift)")

    on_main = MainTopology(CANON, "main", None, None, True)
    if wt.canonical_sync_action(on_main, canonical_clean=True).kind != "on-main":
        raise AssertionError("canonical already on main -> on-main")
    return "warn-squatter / return-canonical / warn-dirty / on-main decided correctly"


def test_merge_park_target() -> str:
    # `merge_park_target(cwd, worktrees)` is fed the MERGED REPO's worktree list.
    squat = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385"), (WT_FOO, "main")]))
    # A worktree OF THE MERGED REPO, left on main after a merge -> park it (branch read from list).
    if wt.merge_park_target(WT_FOO, squat) != "claude/foo-bar-abc123":
        raise AssertionError("a repo's own worktree on main after merge must be parked")
    # Merge run from the canonical itself -> nothing to park.
    canon_main = wt.parse_worktree_porcelain(_porcelain([(CANON, "main"), (WT_FOO, "claude/x")]))
    if wt.merge_park_target(CANON, canon_main) is not None:
        raise AssertionError("cwd == canonical -> no park")
    # Normal case: gh's local checkout failed, the worktree kept its own branch -> no park.
    normal = wt.parse_worktree_porcelain(_porcelain([(CANON, "pr-385"), (WT_FOO, "claude/foo-bar-abc123")]))
    if wt.merge_park_target(WT_FOO, normal) is not None:
        raise AssertionError("worktree not on main -> no park")
    # CROSS-REPO GUARD: cwd is NOT a worktree of the merged repo (e.g. `gh pr merge --repo X`
    # run from an unrelated repo's checkout sitting on main) -> must NOT park that other repo.
    unrelated = "C:/Users/brown/Git/lifting-logbook"
    if wt.merge_park_target(unrelated, squat) is not None:
        raise AssertionError("cwd not a worktree of the merged repo -> no park (cross-repo guard)")
    # Empty cwd / empty worktree list -> no park.
    if wt.merge_park_target("", squat) is not None:
        raise AssertionError("empty cwd -> no park")
    if wt.merge_park_target(WT_FOO, []) is not None:
        raise AssertionError("empty worktree list -> no park")
    # Windows vs POSIX spelling of the canonical must compare equal (no spurious park).
    win = wt.parse_worktree_porcelain(_porcelain([(r"C:\Users\brown\Git\dev-env", "main"), (WT_FOO, "claude/x")]))
    if wt.merge_park_target(CANON, win) is not None:
        raise AssertionError("canonical in Windows spelling must equal canonical -> no park")
    return "parks a repo's own worktree on main; None for canonical / not-main / cross-repo / empty / spelling"


def test_resolve_current_branch() -> str:
    if wt.resolve_current_branch(1, "") != "<detached>":
        raise AssertionError("non-zero returncode -> '<detached>' sentinel")
    if wt.resolve_current_branch(128, "fatal: not a valid ref\n") != "<detached>":
        raise AssertionError("non-zero returncode with stderr-like stdout -> '<detached>' sentinel")
    if wt.resolve_current_branch(0, "main\n") != "main":
        raise AssertionError("returncode 0 -> stripped stdout")
    if wt.resolve_current_branch(0, "  draft/2026-07-09  \n") != "draft/2026-07-09":
        raise AssertionError("returncode 0 -> stdout stripped of surrounding whitespace")
    return "returncode!=0 -> '<detached>' sentinel; returncode==0 -> stripped stdout (dev-env#619)"


def test_canonical_sync_action_detached_head() -> str:
    # dev-env#619 regression: a detached canonical must flow through the FULL pipeline (not
    # just resolve_current_branch or main_squatter in isolation) to the same
    # return-canonical/warn-dirty decision a wrong-branch canonical already gets.
    detached = MainTopology(CANON, "<detached>", None, None, False)
    if wt.canonical_sync_action(detached, canonical_clean=True).kind != "return-canonical":
        raise AssertionError("detached + clean -> return-canonical (safe auto-return)")
    if wt.canonical_sync_action(detached, canonical_clean=False).kind != "warn-dirty":
        raise AssertionError("detached + dirty -> warn-dirty (preserve drift, don't auto-switch)")
    # Same scenario via a REAL detached-canonical worktree list (not a hand-built MainTopology),
    # proving diagnose_main_topology and canonical_sync_action compose correctly together.
    worktrees = wt.parse_worktree_porcelain(_porcelain([(CANON, None), (WT_FOO, "claude/x")]))
    topo = wt.diagnose_main_topology(worktrees)
    if topo.canonical_branch != "<detached>":
        raise AssertionError(f"expected diagnose_main_topology to report '<detached>', got {topo}")
    if wt.canonical_sync_action(topo, canonical_clean=True).kind != "return-canonical":
        raise AssertionError("real detached-canonical topology + clean -> return-canonical")
    return "detached canonical (hand-built + real topology) -> return-canonical/warn-dirty, never on-main/warn-squatter"


def test_is_hijacked_branch() -> str:
    if not wt.is_hijacked_branch("<detached>"):
        raise AssertionError("detached sentinel -> hijacked")
    if not wt.is_hijacked_branch("claude/priceless-kalam-4255c1"):
        raise AssertionError("claude/* branch -> hijacked (dev-env#630 signature)")
    if wt.is_hijacked_branch("main"):
        raise AssertionError("main -> never hijacked")
    if wt.is_hijacked_branch("draft/2026-07-09"):
        raise AssertionError("draft/YYYY-MM-DD -> legitimate for engineering-journal, not hijacked")
    if wt.is_hijacked_branch("pr-385"):
        raise AssertionError("an arbitrary named branch -> not the hijack signature")
    if wt.is_hijacked_branch(""):
        raise AssertionError("empty string -> not hijacked (falsy guard)")
    if wt.is_hijacked_branch(None):
        raise AssertionError("None -> not hijacked, must not raise (falsy guard)")
    return "claude/* and <detached> -> hijacked; main/draft/named/empty/None -> not hijacked, no raise"


EJ_CANON = "C:/Users/brown/Git/engineering-journal"
EJ_STUB_TODAY = "C:/Users/brown/Git/engineering-journal/.claude/worktrees/stub-829-165612"
EJ_STUB_YESTERDAY = "C:/Users/brown/Git/engineering-journal/.claude/worktrees/stub-823-120134"


def test_draft_branch_re() -> str:
    for b in ("draft/2026-07-12", "draft/2026-01-01-recovery"):
        if not wt.DRAFT_BRANCH_RE.match(b):
            raise AssertionError(f"{b!r} should match DRAFT_BRANCH_RE")
    for b in ("draft/2026-7-12", "draft/2026-07-12-late", "main", ""):
        if wt.DRAFT_BRANCH_RE.match(b):
            raise AssertionError(f"{b!r} should NOT match DRAFT_BRANCH_RE")
    return "draft/YYYY-MM-DD and -recovery suffix match; malformed/other-suffixed/other branches don't"


def test_non_canonical_worktrees_matching_finds_all() -> str:
    # dev-env#747: two DIFFERENT dates squatted by two DIFFERENT worktrees, simultaneously --
    # both must be returned, not just the first.
    worktrees = wt.parse_worktree_porcelain(_porcelain([
        (EJ_CANON, "draft/2026-07-12"),
        (EJ_STUB_YESTERDAY, "draft/2026-07-11"),
        (EJ_STUB_TODAY, "draft/2026-07-12"),
    ]))
    got = wt.non_canonical_worktrees_matching(worktrees, wt.DRAFT_BRANCH_RE)
    got_paths = {w["path"] for w in got}
    if got_paths != {EJ_STUB_YESTERDAY, EJ_STUB_TODAY}:
        raise AssertionError(f"expected both squatters found, got {got_paths}")
    return "two simultaneous squatters on two different dates both found"


def test_non_canonical_worktrees_matching_excludes_canonical() -> str:
    # The canonical itself legitimately holds draft/YYYY-MM-DD most of the day -- must never
    # be flagged as its own squatter.
    worktrees = wt.parse_worktree_porcelain(_porcelain([(EJ_CANON, "draft/2026-07-12"), (WT_FOO, "claude/x")]))
    got = wt.non_canonical_worktrees_matching(worktrees, wt.DRAFT_BRANCH_RE)
    if got:
        raise AssertionError(f"canonical legitimately holding the pattern must not match itself, got {got}")
    return "canonical holding draft/YYYY-MM-DD is never flagged as a squatter of itself"


def test_non_canonical_worktrees_matching_none_when_no_match() -> str:
    worktrees = wt.parse_worktree_porcelain(_porcelain([(EJ_CANON, "main"), (WT_FOO, "claude/x")]))
    if wt.non_canonical_worktrees_matching(worktrees, wt.DRAFT_BRANCH_RE) != []:
        raise AssertionError("no draft/* branch anywhere -> no matches")
    return "no matching branch anywhere -> []"


def test_pattern_squat_action_live() -> str:
    a = wt.pattern_squat_action(EJ_STUB_TODAY, "draft/2026-07-12", live=True, dirty=False, fully_pushed=True)
    if a.kind != "warn-live" or a.park_branch is not None:
        raise AssertionError(f"a live squatter must never be touched: {a}")
    return "live=True -> warn-live, no park_branch offered (never touch a live session)"


def test_pattern_squat_action_park_and_remove() -> str:
    a = wt.pattern_squat_action(EJ_STUB_TODAY, "draft/2026-07-12", live=False, dirty=False, fully_pushed=True)
    if a.kind != "park-and-remove" or a.park_branch != "claude/stub-829-165612":
        raise AssertionError(f"idle+clean+fully-pushed -> park-and-remove: {a}")
    return "idle, clean, fully pushed -> park-and-remove (zero data at risk)"


def test_pattern_squat_action_park_only_dirty() -> str:
    a = wt.pattern_squat_action(EJ_STUB_YESTERDAY, "draft/2026-07-11", live=False, dirty=True, fully_pushed=True)
    if a.kind != "park-only" or a.park_branch != "claude/stub-823-120134":
        raise AssertionError(f"dirty -> park-only (preserve untouched, never discard): {a}")
    return "idle but dirty -> park-only, worktree contents left untouched (dev-env#747 stub-823-120134 case)"


def test_pattern_squat_action_park_only_not_fully_pushed() -> str:
    a = wt.pattern_squat_action(EJ_STUB_TODAY, "draft/2026-07-12", live=False, dirty=False, fully_pushed=False)
    if a.kind != "park-only":
        raise AssertionError(f"not provably fully pushed -> park-only (conservative default): {a}")
    return "clean but not provably fully pushed -> park-only, not park-and-remove (conservative default)"


def main() -> int:
    tests = [
        ("parse_worktree_porcelain", test_parse_worktree_porcelain),
        ("canonical_worktree (first / empty)", test_canonical_worktree),
        ("park_branch_for (Windows + POSIX)", test_park_branch_for),
        ("main_squatter found", test_main_squatter_found),
        ("main_squatter none when canonical on main", test_main_squatter_none_when_canonical_on_main),
        ("main_squatter none when main free", test_main_squatter_none_when_main_free),
        ("main_squatter across multiple worktrees", test_main_squatter_multiple_worktrees),
        ("main_squatter none for bare/detached canonical", test_main_squatter_bare_or_detached_canonical),
        ("canonical_on_main predicate", test_canonical_on_main),
        ("diagnose healthy", test_diagnose_healthy),
        ("diagnose squat", test_diagnose_squat),
        ("diagnose canonical off main, no squatter", test_diagnose_canonical_off_main_no_squatter),
        ("canonical_sync_action decision table", test_canonical_sync_action),
        ("merge_park_target decision table", test_merge_park_target),
        ("resolve_current_branch (dev-env#619)", test_resolve_current_branch),
        ("canonical_sync_action with detached HEAD (dev-env#619)", test_canonical_sync_action_detached_head),
        ("is_hijacked_branch (dev-env#630)", test_is_hijacked_branch),
        ("DRAFT_BRANCH_RE matrix (dev-env#747)", test_draft_branch_re),
        ("non_canonical_worktrees_matching finds all squatters", test_non_canonical_worktrees_matching_finds_all),
        ("non_canonical_worktrees_matching excludes canonical", test_non_canonical_worktrees_matching_excludes_canonical),
        ("non_canonical_worktrees_matching none when no match", test_non_canonical_worktrees_matching_none_when_no_match),
        ("pattern_squat_action: live", test_pattern_squat_action_live),
        ("pattern_squat_action: park-and-remove", test_pattern_squat_action_park_and_remove),
        ("pattern_squat_action: park-only (dirty)", test_pattern_squat_action_park_only_dirty),
        ("pattern_squat_action: park-only (not fully pushed)", test_pattern_squat_action_park_only_not_fully_pushed),
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
