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

Check 5 (stale-canonical self-healing, ADR-119 Amendment 1 / dev-env#911) gets the same
pure-function coverage (`stale_canonical_recovery_decision` — named `_decision`, not
`format_*`, because unlike `format_day_rollover` a non-None return here ALSO authorizes the
actual checkout, not just advisory text), PLUS — unlike every other check in this file — a
set of real-repo end-to-end tests. Check 5 is the one check whose action mutates the
canonical (`git checkout main`) rather than only printing advice, so proving the single most
important safety property ("a dirty tree is never auto-touched") requires actually asserting
on a real repo's branch after the call, not just on a returned string. Those tests drive the
real `stale_canonical_recovery_message()` against a disposable throwaway git repo, borrowing
the init-a-real-repo fixture technique from this repo's closest analog for a mutating journal
hook, `test_journal_canonical_guard.py` — the only other test file in this family that
asserts on real post-call git state. One of the four (`test_stale_recovery_noop_when_just_checked_out`)
pins a real bug PR #912's own review caught: idle time must be measured from the last time
HEAD moved (checkout OR commit), never from the stale branch's own tip-commit time, or a
session that just legitimately checked the branch out finds idle_minutes already past
threshold at the instant of checkout — see `canonical_head_idle_minutes`'s docstring.

Usage:
    py -3 claude/scripts/tests/test_new_day_journal_check.py

Exit 0 = all pass.
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
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
stale_canonical_recovery_decision = mod.stale_canonical_recovery_decision


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


# --- stale_canonical_recovery_decision: pure decision + formatting (dev-env#911) ---------


def test_stale_canonical_recovery_decision_silent_cases() -> str:
    assert stale_canonical_recovery_decision("draft/2026-07-22", "2026-07-22", False, 999) is None, \
        "today's own branch must never fire, no matter how idle"
    assert stale_canonical_recovery_decision("main", "2026-07-22", False, 999) is None, \
        "a non-draft branch must never fire"
    assert stale_canonical_recovery_decision("draft/2026-07-21", "2026-07-22", True, 999) is None, \
        "a dirty tree must never fire, no matter how idle"
    assert stale_canonical_recovery_decision("draft/2026-07-21", "2026-07-22", False, 5) is None, \
        "under the idle threshold (15min) must not fire"
    assert stale_canonical_recovery_decision("draft/2026-07-21", "2026-07-22", False, None) is None, \
        "unknown idle time must not fire (fails toward inaction, never toward acting)"
    return "silent on: today's branch, non-draft branch, dirty tree, under-threshold idle, unknown idle"


def test_stale_canonical_recovery_decision_dirty_short_circuits_before_idle() -> str:
    """Regression pin for the single most important safety property (dev-env#911): dirty
    must be checked BEFORE idle_minutes is even consulted, so a dirty+unknown-idle or a
    dirty+enormously-idle branch is silent either way -- the ordering must not silently drift
    so that some future edit lets an absent idle reading coincidentally short-circuit ahead of
    the dirty check instead of the dirty check owning that job unconditionally."""
    assert stale_canonical_recovery_decision("draft/2026-07-21", "2026-07-22", True, None) is None
    assert stale_canonical_recovery_decision("draft/2026-07-21", "2026-07-22", True, 999999) is None
    return "dirty is silent regardless of idle_minutes being absent or enormous"


def test_stale_canonical_recovery_decision_fires_when_clean_and_idle() -> str:
    msg = stale_canonical_recovery_decision("draft/2026-07-21", "2026-07-22", False, 15)
    assert msg is not None, "exactly at the 15min threshold must fire (not-less-than, not strictly-greater)"
    assert "draft/2026-07-21" in msg, msg
    assert "restored to main" in msg, msg
    assert "15min" in msg, msg
    assert "dev-env#911" in msg, msg
    return "clean + idle >= threshold fires, naming the branch, the restore, and the idle minutes"


def test_stale_canonical_recovery_decision_suffixed_branch_matches_today() -> str:
    """branch_date() parses through documented suffix forms (e.g. -recovery); reusing it here
    means a suffixed branch whose DATE is today is treated as current, exactly like check 4's
    own semantics -- the two checks must agree on what "today's branch" means."""
    assert stale_canonical_recovery_decision("draft/2026-07-22-recovery", "2026-07-22", False, 999) is None
    return "a suffixed branch dated today is treated as today's branch, same as format_day_rollover"


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


# --- Check 5: stale-canonical self-healing — real-repo end-to-end tests (dev-env#911) -----
# Unlike every other check in this file, check 5's action is a REAL git mutation
# (`git checkout main`), not just advisory text. The pure tests above prove the decision
# logic; they cannot prove the wrapper actually leaves a dirty repo untouched. These four
# tests drive the real `stale_canonical_recovery_message()` against a disposable throwaway
# git repo and assert on the repo's ACTUAL branch afterward — borrowing the init-a-real-repo
# fixture technique from `test_journal_canonical_guard.py`'s `_init_throwaway_repo`, this
# repo's only other test file that asserts on real post-call git state for a mutating
# journal hook.

# Synthetic, wall-clock-independent dates (never a real "today") so these tests stay
# deterministic regardless of which real date they happen to run on.
FAKE_TODAY = "2099-01-01"
FAKE_STALE_DATE = "2098-06-15"


def _run_git(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, env=env,
    )


def _init_canonical_fixture(root: Path) -> None:
    """Minimal real git repo at `root`, branch `main`, one empty commit. Mirrors
    test_journal_canonical_guard.py's `_init_throwaway_repo` fixture helper exactly (same
    target repo family, same convention) -- `-c init.templateDir= -c core.hooksPath=`
    neutralizes any global template/hooks directory the developer's machine has configured."""
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=", "init", "-q", str(root)],
        check=True, capture_output=True,
    )
    _run_git(root, "config", "user.email", "test@example.com")
    _run_git(root, "config", "user.name", "Test")
    _run_git(root, "commit", "--allow-empty", "-q", "-m", "init")
    _run_git(root, "branch", "-M", "main")


def _checkout_draft_branch(root: Path, date_str: str, minutes_ago: float) -> None:
    """Create and check out `draft/<date_str>` with one commit, backdated `minutes_ago`
    minutes via GIT_AUTHOR_DATE/GIT_COMMITTER_DATE so canonical_branch_idle_minutes() can be
    exercised against a real repo without sleeping in the test."""
    _run_git(root, "checkout", "-q", "-b", f"draft/{date_str}")
    env = dict(os.environ)
    commit_epoch = int(time.time() - minutes_ago * 60)
    env["GIT_AUTHOR_DATE"] = f"{commit_epoch} +0000"
    env["GIT_COMMITTER_DATE"] = f"{commit_epoch} +0000"
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-q", "-m", f"draft: {date_str} session 1"],
        check=True, capture_output=True, text=True, env=env,
    )


def _make_dirty(root: Path) -> None:
    """Stage an uncommitted change. Staged (not left untracked) so `git status --porcelain`
    shows it regardless of the ambient `status.showUntrackedFiles` config -- same caution
    test_journal_canonical_guard.py's dirty-canonical fixture documents."""
    (root / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    _run_git(root, "add", "dirty.txt")


def _current_branch(root: Path) -> str:
    return _run_git(root, "branch", "--show-current").stdout.strip()


def _stale_recovery_against(root: Path):
    """Call the real stale_canonical_recovery_message() with mod.JOURNAL_REPO/mod.TODAY
    patched to this fixture, restoring both afterward regardless of outcome -- same
    direct-module-attribute-patching style _run_main() above already uses for mod.SCRATCH."""
    orig_repo, orig_today = mod.JOURNAL_REPO, mod.TODAY
    mod.JOURNAL_REPO, mod.TODAY = root, FAKE_TODAY
    try:
        return mod.stale_canonical_recovery_message()
    finally:
        mod.JOURNAL_REPO, mod.TODAY = orig_repo, orig_today


def test_stale_recovery_noop_on_todays_branch() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "engineering-journal"
        root.mkdir()
        _init_canonical_fixture(root)
        _checkout_draft_branch(root, FAKE_TODAY, minutes_ago=20)  # idle, but it IS today
        result = _stale_recovery_against(root)
        assert result is None, f"today's branch must never be touched; got {result!r}"
        assert _current_branch(root) == f"draft/{FAKE_TODAY}", \
            f"branch must be unchanged; got {_current_branch(root)!r}"
    return "on today's own draft branch, no message and no checkout, regardless of idle time"


def test_stale_recovery_noop_when_dirty() -> str:
    """THE core safety-property proof (dev-env#911): a stale, sufficiently idle branch with
    an uncommitted change must NEVER be auto-checked-out, no matter how safe the idle time
    alone would otherwise make it look."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "engineering-journal"
        root.mkdir()
        _init_canonical_fixture(root)
        _checkout_draft_branch(root, FAKE_STALE_DATE, minutes_ago=60)  # plenty idle
        _make_dirty(root)
        result = _stale_recovery_against(root)
        assert result is None, f"a dirty tree must never be auto-touched; got {result!r}"
        assert _current_branch(root) == f"draft/{FAKE_STALE_DATE}", \
            f"checkout must NOT happen on a dirty tree; got {_current_branch(root)!r}"
        status = _run_git(root, "status", "--porcelain").stdout
        assert "dirty.txt" in status, "the uncommitted change itself must also be untouched"
    return "stale + idle + DIRTY -> no message, no checkout: the single most important safety property"


def test_stale_recovery_noop_when_recently_committed() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "engineering-journal"
        root.mkdir()
        _init_canonical_fixture(root)
        _checkout_draft_branch(root, FAKE_STALE_DATE, minutes_ago=2)  # well under the 15min bar
        result = _stale_recovery_against(root)
        assert result is None, f"a recent (in-flight) hop must not be touched; got {result!r}"
        assert _current_branch(root) == f"draft/{FAKE_STALE_DATE}", \
            f"branch must be unchanged; got {_current_branch(root)!r}"
    return "stale + clean but recently committed (<15min) -> no message, no checkout"


def test_stale_recovery_noop_when_just_checked_out() -> str:
    """THE regression pin for the bug PR #912's own review caught: idle time must be measured
    from the last time HEAD moved (checkout OR commit), never from the stale branch's own
    tip-commit time. A naive tip-commit-time signal would already read as "idle" the INSTANT
    a session checks the branch out, since a genuinely stale branch's last real commit is, by
    construction, already old -- giving a legitimate in-flight checkout ZERO of the headroom
    STALE_CANONICAL_IDLE_MINUTES is meant to provide, and exposing it to exactly the
    collision dev-env#911 is about."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "engineering-journal"
        root.mkdir()
        _init_canonical_fixture(root)
        # The branch's own tip commit is VERY old -- this alone would satisfy a (buggy)
        # tip-commit-time idle check immediately, with no checkout-time protection at all.
        _checkout_draft_branch(root, FAKE_STALE_DATE, minutes_ago=1000)
        _run_git(root, "checkout", "-q", "main")
        # Simulate a session legitimately (and freshly) checking the stale branch back out
        # right now -- e.g. to commit an orphaned shard deletion per ADR-119 decision 3 --
        # with no new commit yet.
        _run_git(root, "checkout", "-q", f"draft/{FAKE_STALE_DATE}")
        result = _stale_recovery_against(root)
        assert result is None, \
            f"a just-checked-out branch must not be touched, even if its OWN tip commit is ancient; got {result!r}"
        assert _current_branch(root) == f"draft/{FAKE_STALE_DATE}", \
            f"checkout must NOT happen right after a fresh legitimate checkout; got {_current_branch(root)!r}"
    return "stale branch with an ancient tip commit, but JUST checked out -> no message, no checkout"


def test_stale_recovery_restores_when_clean_and_idle() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "engineering-journal"
        root.mkdir()
        _init_canonical_fixture(root)
        _checkout_draft_branch(root, FAKE_STALE_DATE, minutes_ago=20)
        result = _stale_recovery_against(root)
        assert result and f"draft/{FAKE_STALE_DATE}" in result, f"expected a recovery message; got {result!r}"
        assert "restored to main" in result, result
        assert _current_branch(root) == "main", \
            f"the canonical must actually be back on main; got {_current_branch(root)!r}"
    return "stale + clean + idle >=15min -> auto-restored to main, confirmed against the real repo"


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
        ("stale-recovery: silent cases (today/non-draft/dirty/under-threshold/unknown-idle)", test_stale_canonical_recovery_decision_silent_cases),
        ("stale-recovery: dirty short-circuits before idle is consulted", test_stale_canonical_recovery_decision_dirty_short_circuits_before_idle),
        ("stale-recovery: fires when clean and idle >= threshold", test_stale_canonical_recovery_decision_fires_when_clean_and_idle),
        ("stale-recovery: suffixed branch dated today is not stale", test_stale_canonical_recovery_decision_suffixed_branch_matches_today),
        ("stale-recovery (real repo): today's branch never touched", test_stale_recovery_noop_on_todays_branch),
        ("stale-recovery (real repo): dirty tree -> checkout does NOT happen", test_stale_recovery_noop_when_dirty),
        ("stale-recovery (real repo): recent commit (<15min) -> no-op", test_stale_recovery_noop_when_recently_committed),
        ("stale-recovery (real repo): just checked out (ancient tip commit) -> no-op", test_stale_recovery_noop_when_just_checked_out),
        ("stale-recovery (real repo): clean + idle -> auto-restores to main", test_stale_recovery_restores_when_clean_and_idle),
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
        except Exception as exc:  # noqa: BLE001 -- the real-repo tests spawn git subprocesses
            # (check=True) that can raise CalledProcessError on unexpected fixture failure;
            # without this, an uncaught non-AssertionError exception would abort main()'s
            # loop entirely (killing every subsequent test) instead of reporting one clean
            # FAIL line, matching test_journal_canonical_guard.py's more defensive pattern
            # for the same real-subprocess-fixture test style.
            failed += 1
            print(f"ERROR: {name}: {type(exc).__name__}: {exc}")
    print(f"\nTests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
