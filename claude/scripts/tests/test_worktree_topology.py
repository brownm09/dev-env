#!/usr/bin/env python3
"""Unit tests for _worktree_topology.py — the worktree-on-`main` topology helpers.

Covers the pure logic that detects a worktree squatting `main` (and the canonical being
knocked off `main`) and decides the non-destructive correction (dev-env#396, ADR-058):

  1. parse_worktree_porcelain()  — path/branch/detached/refs-heads-stripping (pure).
  2. canonical_worktree()        — first entry is the primary; empty -> None.
  3. park_branch_for()           — basename -> claude/<slug> (Windows + POSIX spellings).
  4. main_squatter()             — the non-canonical worktree on main, or None.
  5. canonical_on_main()         — primary-on-main predicate.
  6. diagnose_main_topology()    — healthy vs. squat vs. canonical-off-main-no-squatter.
  7. canonical_sync_action()     — warn-squatter / return-canonical / warn-dirty / on-main.
  8. merge_park_target()         — park a non-canonical worktree left on main; else None
                                   (empty cwd / cwd==canonical / not-on-main / path spelling).

Fully offline — no git, no network, no filesystem writes (paths need not exist; the
module resolves them for comparison only). The git-driven prune/post-merge/dev-env-sync
loops are exercised by --dry-run / live behavior in the PR, not here.

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
    # A non-canonical worktree left on main after a merge -> park it.
    if wt.merge_park_target(WT_FOO, CANON, "main") != "claude/foo-bar-abc123":
        raise AssertionError("worktree on main after merge must be parked")
    # Merge run from the canonical itself (on main) -> nothing to park.
    if wt.merge_park_target(CANON, CANON, "main") is not None:
        raise AssertionError("cwd == canonical -> no park")
    # Normal case: gh's local checkout failed, worktree kept its own branch -> no park.
    if wt.merge_park_target(WT_FOO, CANON, "claude/foo-bar-abc123") is not None:
        raise AssertionError("worktree not on main -> no park")
    # Empty cwd must never resolve to os.getcwd() and park something.
    if wt.merge_park_target("", CANON, "main") is not None:
        raise AssertionError("empty cwd -> no park")
    # Windows vs POSIX spelling of the canonical must compare equal (no spurious park).
    if wt.merge_park_target(r"C:\Users\brown\Git\dev-env", CANON, "main") is not None:
        raise AssertionError("canonical in Windows spelling must equal canonical -> no park")
    return "parks a non-canonical worktree on main; None for canonical/not-main/empty/spelling"


def main() -> int:
    tests = [
        ("parse_worktree_porcelain", test_parse_worktree_porcelain),
        ("canonical_worktree (first / empty)", test_canonical_worktree),
        ("park_branch_for (Windows + POSIX)", test_park_branch_for),
        ("main_squatter found", test_main_squatter_found),
        ("main_squatter none when canonical on main", test_main_squatter_none_when_canonical_on_main),
        ("main_squatter none when main free", test_main_squatter_none_when_main_free),
        ("canonical_on_main predicate", test_canonical_on_main),
        ("diagnose healthy", test_diagnose_healthy),
        ("diagnose squat", test_diagnose_squat),
        ("diagnose canonical off main, no squatter", test_diagnose_canonical_off_main_no_squatter),
        ("canonical_sync_action decision table", test_canonical_sync_action),
        ("merge_park_target decision table", test_merge_park_target),
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
