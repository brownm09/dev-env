#!/usr/bin/env python3
"""Unit tests for new-day-journal-check.py's day-rollover detection (ADR-119, dev-env#866).

The day-rollover check exists because every journal discovery path keys on BOTH halves of
`draft/<DATE>` + `sessions/*/<DATE>_*.stub.md`: `/journal-compose` resolves its source
branch from the date, and `daily-journal-compose` gates on
`show-ref --verify refs/remotes/origin/draft/${DATE} || exit 0`. A stub written onto a
branch named for a different day is therefore composed by nothing and reported by nothing,
*silently* — the failure that left 26 stubs across 5 dates uncomposed on `origin/main`.

These tests pin the two pure helpers behind that check:

  - `branch_date` — the `draft/<date>[-suffix]` name parse, including the documented
    `-recovery` suffix form, and rejection of anything that isn't a real date.
  - `mismatched_stub_paths` — deliberately ONE-SIDED: only a stub dated *after* the branch
    is the rollover failure. Older-dated stubs are branch-lineage artifacts that
    `stale_draft_artifacts`/`unmerged_draft_branches` already cover, and including them
    buried the 6 real hits under 27 unrelated ones on the first live run.

The git-reading helpers (`canonical_current_branch`, `branch_stub_paths`,
`branch_exists`, `canonical_is_dirty`) remain untested subprocess boundaries, matching
`check_pr_state` in `test_reconcile_open_prs.py`. Everything above them is covered:
`format_day_rollover` is pure, and `main()`'s worktree gating is driven end-to-end by
stubbing the module's own check functions and redirecting SCRATCH — no git, no network.

Usage:
    py -3 claude/scripts/tests/test_new_day_journal_check.py

Exit 0 = all pass.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "new-day-journal-check.py"

# The script imports _winsubp / _hookutil (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("new_day_journal_check", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # safe: main() is guarded by __main__

branch_date = mod.branch_date
mismatched_stub_paths = mod.mismatched_stub_paths
format_day_rollover = mod.format_day_rollover
summarize_by_project = mod.summarize_by_project


def test_branch_date_parses_plain_and_suffixed() -> str:
    assert branch_date("draft/2026-07-21") == "2026-07-21"
    assert branch_date("draft/2026-07-21-recovery") == "2026-07-21", \
        "the documented -recovery form is still a dated draft branch"
    assert branch_date("draft/2026-05-09-lifting-logbook-s6") == "2026-05-09", \
        "arbitrary suffixes (these exist on the remote today) still parse"
    return "draft/<date> parses, with or without the documented suffix forms"


def test_branch_date_rejects_non_draft_and_non_dates() -> str:
    assert branch_date("main") is None
    assert branch_date("fix/866-day-rollover") is None
    assert branch_date("draft/not-a-date") is None
    assert branch_date("draft/2026-7-21") is None, "non-zero-padded is not the convention"
    assert branch_date("draft/") is None
    assert branch_date("draft/20260721") is None, "separators are required"
    return "main, feature branches, and malformed draft names yield None (no false rollover)"


def test_mismatched_stub_paths_flags_only_newer_dates() -> str:
    tree = [
        "sessions/dev-env/2026-07-21_040731.stub.md",        # same day — fine
        "sessions/dev-env/2026-07-22_123151.stub.md",        # NEWER — the rollover failure
        "sessions/career-playbook/2026-07-22_021500.stub.md",  # NEWER
        "sessions/lifting-logbook/2026-06-29_002505.stub.md",  # OLDER — lineage artifact
        "sessions/dev-env/2026-07-21_040731.manifest.jsonl",   # not a stub
        "sessions/dev-env/open-prs/770.json",                  # not a stub
        "sessions/meta/2026-07-20-some-composed-entry.md",     # composed doc, not a stub
    ]
    got = mismatched_stub_paths(tree, "2026-07-21")
    assert got == [
        "sessions/career-playbook/2026-07-22_021500.stub.md",
        "sessions/dev-env/2026-07-22_123151.stub.md",
    ], f"only newer-dated stubs, sorted; got {got}"
    return "only stubs dated after the branch are flagged — older ones are lineage, not rollover"


def test_mismatched_stub_paths_ignores_non_convention_names() -> str:
    tree = [
        "sessions/dev-env/2026-07-22-no-underscore.stub.md",  # no HHMMSS separator
        "sessions/dev-env/20260722_010101.stub.md",           # no date separators
        "sessions/dev-env/short.stub.md",                     # too short to carry a date
        "sessions/dev-env/2026-07-22_010101.stub.md",         # the one real match
    ]
    got = mismatched_stub_paths(tree, "2026-07-21")
    assert got == ["sessions/dev-env/2026-07-22_010101.stub.md"], \
        f"names compose's own glob would not match are not mismatches this check can speak to; got {got}"
    return "non-convention filenames are ignored — they are invisible to compose's glob anyway"


def test_mismatched_stub_paths_empty_when_branch_is_current() -> str:
    tree = [
        "sessions/dev-env/2026-07-22_123151.stub.md",
        "sessions/dev-env/2026-07-21_040731.stub.md",
    ]
    assert mismatched_stub_paths(tree, "2026-07-22") == [], \
        "on its own day's branch, an older stub is not a rollover finding"
    assert mismatched_stub_paths([], "2026-07-22") == [], "empty tree -> no findings"
    return "a branch on its own date reports nothing, and an empty tree does not crash"


# --- format_day_rollover: now pure, so the message itself is testable ---------
# (dev-env#873 review — the previous version hard-wired its git calls and could not be
# tested at all; none of the branches below had any coverage.)

TREE = [
    "sessions/dev-env/2026-07-21_040731.stub.md",
    "sessions/dev-env/2026-07-22_123151.stub.md",
    "sessions/career-playbook/2026-07-22_021500.stub.md",
    "sessions/lifting-logbook/2026-07-22_121303.stub.md",
]


def _msg(**kw):
    args = dict(
        branch="draft/2026-07-21",
        tree_paths=TREE,
        today="2026-07-22",
        today_branch_exists=False,
        canonical_dirty=False,
    )
    args.update(kw)
    return format_day_rollover(**args)


def test_format_day_rollover_silent_on_same_date_and_non_draft() -> str:
    assert _msg(branch="draft/2026-07-22") is None, "branch matching today must not fire"
    assert _msg(branch="main") is None, "a non-draft branch must not fire"
    assert _msg(branch="draft/nonsense") is None, "an unparseable draft name must not fire"
    return "no rollover reported when the branch is today's, not a draft, or unparseable"


def test_format_day_rollover_command_is_idempotent_when_branch_exists() -> str:
    """`checkout -b` fails when the branch already exists — and fails AFTER `checkout main`
    has moved the SHARED canonical, parking it on main for every concurrent session. When
    today's branch exists the advisory must switch to it instead of trying to create it."""
    fresh = _msg(today_branch_exists=False)
    assert "checkout -b draft/2026-07-22" in fresh, fresh
    assert "checkout main" in fresh

    exists = _msg(today_branch_exists=True)
    assert "checkout -b" not in exists, "must not try to re-create an existing branch"
    assert "checkout main" not in exists, "must not move the shared canonical to main"
    assert "checkout draft/2026-07-22" in exists, exists
    return "the recommended command matches reality: create when absent, switch when present"


def test_format_day_rollover_warns_on_dirty_canonical() -> str:
    """journal-canonical-guard.py refuses to switch a dirty canonical; this hook must not
    hand out contradictory advice about the same shared checkout."""
    assert "CAUTION" not in _msg(canonical_dirty=False)
    dirty = _msg(canonical_dirty=True)
    assert "CAUTION" in dirty and "uncommitted changes" in dirty, dirty
    return "a dirty canonical adds an explicit caution rather than advising a blind switch"


def test_format_day_rollover_states_the_ordering_constraint() -> str:
    """The two journal hooks both fire on turn 1, and acting on the shard-deletion commit
    first strands it on the stale branch. The ordering has to be in the message, not only
    in the ADR."""
    msg = _msg()
    assert "BEFORE" in msg and "shard deletion" in msg, msg
    assert "durable only once its carrying branch merges" in msg, msg
    return "the message states that the branch move precedes any shard-deletion commit"


def test_format_day_rollover_summarizes_projects() -> str:
    msg = _msg()
    assert "3 stub(s) across 3 project(s)" in msg, msg
    for proj in ("career-playbook 1", "dev-env 1", "lifting-logbook 1"):
        assert proj in msg, f"{proj} missing from {msg}"
    return "the mismatch summary names every affected project, not just the first few paths"


def test_summarize_by_project_counts_and_sorts() -> str:
    paths = [
        "sessions/dev-env/a_1.stub.md",
        "sessions/dev-env/b_2.stub.md",
        "sessions/career-playbook/c_3.stub.md",
    ]
    got = summarize_by_project(paths)
    assert got == "3 stub(s) across 2 project(s): career-playbook 1, dev-env 2", got
    assert summarize_by_project([]) == "0 stub(s) across 0 project(s): "
    return "per-project counts are alphabetised and totals are honest"


def test_mismatched_stub_paths_requires_digits_not_just_separators() -> str:
    """The date check must be digit-strict: a name like `abcd-fg-ij_010101.stub.md` has
    separators in the right places and compares lexicographically GREATER than any real
    date, so a separator-only check reported it as mismatched."""
    tree = ["sessions/x/abcd-fg-ij_010101.stub.md", "sessions/x/2026-07-22_010101.stub.md"]
    got = mismatched_stub_paths(tree, "2026-07-21")
    assert got == ["sessions/x/2026-07-22_010101.stub.md"], got
    return "non-digit date-shaped filenames are rejected, matching branch_date's strictness"


# --- end-to-end main() gating (dev-env#873 review) ---------------------------
# The worktree branching had ZERO coverage: a future edit hoisting the in_worktree
# early-return above the rollover check, or restoring the old blanket sys.exit(0), would
# silently reinstate the exact dev-env#866 blind spot with a green suite.


def _run_main(cwd, session_id, scratch, rollover="ROLLOVER-MSG"):
    """Drive main() with stubbed checks; return the emitted systemMessage ('' if silent)."""
    orig = {
        name: getattr(mod, name)
        for name in (
            "day_rollover_message", "stale_draft_artifacts", "unmerged_draft_branches",
            "resurrected_draft_branches", "cleanup_stale_flags", "SCRATCH",
        )
    }
    mod.day_rollover_message = lambda: rollover
    mod.stale_draft_artifacts = lambda: ["/x/sessions/dev-env/2026-07-01_010101.stub.md"]
    mod.unmerged_draft_branches = lambda: ["2026-07-01"]
    mod.resurrected_draft_branches = lambda: []
    mod.cleanup_stale_flags = lambda: None
    mod.SCRATCH = scratch
    payload = json.dumps({"session_id": session_id, "cwd": cwd})
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            sys.stdin = io.StringIO(payload)
            try:
                mod.main()
            except SystemExit:
                pass
    finally:
        sys.stdin = sys.__stdin__
        for name, value in orig.items():
            setattr(mod, name, value)
    out = buf.getvalue().strip()
    return json.loads(out)["systemMessage"] if out else ""


WORKTREE_CWD = "C:/Users/brown/Git/lifting-logbook/.claude/worktrees/some-wt"
MAIN_CWD = "C:/Users/brown/Git/dev-env"


def test_main_worktree_session_gets_rollover_only() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        msg = _run_main(WORKTREE_CWD, "sid-wt", Path(tmp))
        assert "ROLLOVER-MSG" in msg, "the rollover check MUST fire in a worktree session"
        assert "Stale draft artifact" not in msg, "check 1 must stay suppressed in a worktree"
        assert "no composed journal on main" not in msg, "check 2 must stay suppressed"
    return "worktree session emits the rollover check and none of the canonical-housekeeping ones"


def test_main_non_worktree_session_gets_everything() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        msg = _run_main(MAIN_CWD, "sid-main", Path(tmp))
        assert "ROLLOVER-MSG" in msg, msg
        assert "Stale draft artifact" in msg, msg
        assert "no composed journal on main" in msg, msg
    return "main-checkout session emits the rollover check plus checks 1-3"


def test_main_worktree_emission_does_not_suppress_canonical_checks() -> str:
    """The regression this PR's review caught: with ONE shared flag, a worktree session that
    emitted the rollover burned the sentinel, so when the same session's cwd later left the
    worktree, checks 1-3 were silently skipped for the rest of it."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch, sid = Path(tmp), "sid-moves"
        first = _run_main(WORKTREE_CWD, sid, scratch)
        assert "ROLLOVER-MSG" in first and "Stale draft artifact" not in first, first
        second = _run_main(MAIN_CWD, sid, scratch)
        assert "Stale draft artifact" in second, \
            f"checks 1-3 must still fire after the cwd leaves the worktree; got {second!r}"
    return "a worktree session's rollover emission no longer suppresses checks 1-3 later on"


def test_main_rollover_resuppresses_within_the_recheck_window() -> str:
    """The rollover sentinel must actually gate, or the git spawns run on every prompt."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch, sid = Path(tmp), "sid-repeat"
        first = _run_main(WORKTREE_CWD, sid, scratch)
        assert "ROLLOVER-MSG" in first
        second = _run_main(WORKTREE_CWD, sid, scratch)
        assert second == "", f"second prompt in the window must be silent; got {second!r}"
    return "the rollover check re-arms on age, so it does not respawn git on every prompt"


def test_main_quiet_run_still_arms_the_sentinel() -> str:
    """A quiet run previously wrote no flag, so the whole check set re-ran every prompt
    (measured 1.74s/prompt). Arming on a quiet run is the fix."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch, sid = Path(tmp), "sid-quiet"
        msg = _run_main(WORKTREE_CWD, sid, scratch, rollover=None)
        assert msg == "", "no findings -> no output"
        assert (scratch / f"journal_hook_rollover_{sid}.flag").exists(), \
            "a quiet run must still arm the sentinel, or every prompt respawns git"
    return "a quiet run arms its sentinel instead of re-running the check on the next prompt"


def main() -> int:
    tests = [
        ("branch_date parses plain and suffixed draft branches", test_branch_date_parses_plain_and_suffixed),
        ("branch_date rejects non-draft and malformed names", test_branch_date_rejects_non_draft_and_non_dates),
        ("mismatched stubs are only the newer-dated ones", test_mismatched_stub_paths_flags_only_newer_dates),
        ("non-convention stub filenames ignored", test_mismatched_stub_paths_ignores_non_convention_names),
        ("no findings on a branch matching its own date", test_mismatched_stub_paths_empty_when_branch_is_current),
        ("date check is digit-strict, not separator-only", test_mismatched_stub_paths_requires_digits_not_just_separators),
        ("rollover silent on same-date / non-draft branches", test_format_day_rollover_silent_on_same_date_and_non_draft),
        ("recommended command is idempotent when today's branch exists", test_format_day_rollover_command_is_idempotent_when_branch_exists),
        ("dirty canonical gets a caution, not a blind switch", test_format_day_rollover_warns_on_dirty_canonical),
        ("message states the branch-move-before-commit ordering", test_format_day_rollover_states_the_ordering_constraint),
        ("mismatch summary names every affected project", test_format_day_rollover_summarizes_projects),
        ("summarize_by_project counts and sorts", test_summarize_by_project_counts_and_sorts),
        ("worktree session: rollover only", test_main_worktree_session_gets_rollover_only),
        ("main checkout: rollover plus checks 1-3", test_main_non_worktree_session_gets_everything),
        ("worktree emission does not suppress checks 1-3", test_main_worktree_emission_does_not_suppress_canonical_checks),
        ("rollover re-suppresses within the recheck window", test_main_rollover_resuppresses_within_the_recheck_window),
        ("a quiet run still arms the sentinel", test_main_quiet_run_still_arms_the_sentinel),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {name}")
            print(f"      {exc}")
    print(f"\nTests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
