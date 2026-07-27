#!/usr/bin/env python3
"""
UserPromptSubmit hook: detect stale journal work in engineering-journal.

Five checks:
1. *_draft.md files from a previous calendar day still on disk (composed but
   branch not yet deleted, or composition never ran).
2. Remote draft/* branches with no composed journal file on main — never
   composed, or composed but the PR was never opened/merged.
3. Remote draft/* branches already merged but since resurrected by a later push.
4. Day rollover (ADR-119, dev-env#866): the canonical checkout sitting on a
   draft/<D> branch whose date is not today, plus any already-date-mismatched
   stubs on it (filename date != branch date). Every discovery path keys on
   BOTH halves — /journal-compose resolves SOURCE_BRANCH=draft/<DATE> and globs
   sessions/*/<DATE>_*.stub.md on that branch, and daily-journal-compose gates
   on `show-ref --verify refs/remotes/origin/draft/${DATE} || exit 0` — so a
   stub written onto a branch named for another day is composed by nothing and
   reported by nothing, silently. ADR-119 records the census of stubs this had
   already stranded on origin/main at the time it was written.
5. Stale-canonical self-healing (ADR-119 Amendment 1, dev-env#911): two
   concurrent sessions can collide on the canonical's shared HEAD with no
   coordination mechanism (a locking primitive was explicitly ruled out of this
   fix's scope) — one doing ordinary day-rollover, another hopping to an old
   draft/<D> branch to commit an orphaned shard deletion (ADR-119 decision 3),
   for example. The loser can strand the canonical on that old branch for
   hours. When the current branch is a non-today draft/<D>, the FULL working
   tree is clean, and HEAD hasn't moved (checkout OR commit) in at least
   STALE_CANONICAL_IDLE_MINUTES, this check auto-restores the canonical to
   main. UNLIKE every other check in this hook, this one MUTATES the canonical
   (a real `git checkout main`) instead of only printing advice — the
   dirty-tree gate is what makes that safe; see
   `stale_canonical_recovery_decision`.

Checks 4 and 5 are the only ones that also run in Claude-managed worktree
sessions: those write stubs into the canonical via `git -C`, so they are
exactly who needs the warning (and who benefits from the auto-recovery),
whereas checks 1-3 concern the canonical's own housekeeping and would be
noise there.

Exit 0 always — never block the user's prompt (advisory hook, fails open).
Stdout is injected as context Claude sees before processing the user's message.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import functools
import glob
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import _hookutil

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SCRATCH = Path.home() / ".claude" / "scratch"
TODAY = date.today().strftime("%Y-%m-%d")
FLAG_MAX_AGE_HOURS = 24
# How long a check's sentinel suppresses a re-run within the same session. Not "once per
# session": a day rollover can begin mid-session, so the checks must re-arm — but re-running
# them on every prompt costs a git spawn (and, for checks 1-3, a network `git ls-remote`) per
# prompt for a condition that is almost always false. (dev-env#873 review)
RECHECK_MINUTES = 30
# Check 5 (stale-canonical self-healing, dev-env#911): a branch is eligible for auto-restore
# only once HEAD hasn't moved in the canonical -- a checkout OR a commit -- for at least this
# long (see canonical_head_idle_minutes; deliberately NOT the branch's own tip-commit age,
# which is already old for any branch this check considers stale and would give a fresh
# legitimate checkout zero headroom). The observed legitimate hops (checkout a stale branch
# -> commit -> checkout away) took 9 seconds to 6 minutes; the incident this check exists to
# bound left the canonical stranded ~32 hours. 15 minutes gives real in-flight work
# comfortable headroom while still bounding worst-case staleness.
STALE_CANONICAL_IDLE_MINUTES = 15


def cleanup_stale_flags() -> None:
    # Deliberately NOT delegated to _hookutil.cleanup_stale_sentinels: this
    # hook's flags need a 24-HOUR retention (this hook fires once per
    # calendar day, so anything older than a day is stale), not the shared
    # helper's fixed 30-DAY MAX_AGE_DAYS -- migrating would silently
    # 30x-extend this flag's retention. Left as its own bespoke loop rather
    # than generalizing the shared helper's retention window too, which
    # would widen this PR's scope beyond its non-cleaning-writer fix
    # (dev-env#768 review; see turn-count-hook.py's cleanup_stale_counters
    # for the sibling case that legitimately COULD migrate but is also
    # deferred to Phase E).
    cutoff = time.time() - FLAG_MAX_AGE_HOURS * 3600
    try:
        for f in SCRATCH.glob("journal_hook_*.flag"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


# Memoized: this reader is called from more than one check with identical inputs;
# without the cache the same git (and for remote_draft_dates, NETWORK) call runs
# twice per hook fire. Process-lifetime cache, so no staleness within a run.
@functools.lru_cache(maxsize=1)
def composed_project_dates_on_main() -> set[tuple[str, str]]:
    """Return (project, YYYY-MM-DD) pairs that have a composed file on origin/main.

    Finer-grained than composed_dates_on_main(): used to suppress false positives
    when stubs from a composed date are still present on disk because a new-day
    branch was cut from the previous day's draft branch instead of from main.

    Note: reads the local remote-tracking ref (origin/main) without fetching —
    results reflect the last fetch. A stale ref could miss composed dates, causing
    branch-lineage artifacts to appear stale. The next fetch will self-correct.
    """
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "origin/main", "sessions/"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        pairs: set[tuple[str, str]] = set()
        for line in result.stdout.splitlines():
            parts = line.split("/")
            if len(parts) < 3:
                continue
            project = parts[1]
            fname = parts[-1]
            if fname.endswith(".stub.md") or not fname.endswith(".md"):
                continue
            if len(fname) >= 10 and fname[4] == "-" and fname[7] == "-":
                pairs.add((project, fname[:10]))
        return pairs
    except Exception:
        return set()


def stale_draft_artifacts() -> list[str]:
    """Return stub/draft paths whose (project, date) lacks a composed file on main.

    Stubs whose project+date already have a composed file on main are branch-lineage
    artifacts (carried forward because the new-day branch was cut from the previous
    day's draft instead of from main) — not genuine unComposed drafts.
    """
    composed = composed_project_dates_on_main()
    artifacts = []
    for pattern in (
        str(JOURNAL_REPO / "sessions" / "**" / "*_draft.md"),
        # Match only convention-named stubs: YYYY-MM-DD_HHMMSS.stub.md
        str(JOURNAL_REPO / "sessions" / "**" / "????-??-??_*.stub.md"),
    ):
        artifacts.extend(glob.glob(pattern, recursive=True))
    stale = []
    for f in artifacts:
        if os.path.basename(f).startswith(TODAY):
            continue
        date_prefix = os.path.basename(f)[:10]
        try:
            parts = Path(f).parts
            sessions_idx = next(i for i, p in enumerate(parts) if p == "sessions")
            project = parts[sessions_idx + 1]
        except (StopIteration, IndexError):
            project = None
        if project and (project, date_prefix) in composed:
            continue  # Already composed on main — branch-lineage artifact, not stale
        stale.append(f)
    stale.sort(key=lambda f: os.path.basename(f), reverse=True)
    return stale


# Memoized: this reader is called from more than one check with identical inputs;
# without the cache the same git (and for remote_draft_dates, NETWORK) call runs
# twice per hook fire. Process-lifetime cache, so no staleness within a run.
@functools.lru_cache(maxsize=1)
def composed_dates_on_main() -> set[str]:
    """Return YYYY-MM-DD dates that have a composed journal file on origin/main.

    A composed file is any sessions/**/*.md that is NOT a stub (*.stub.md).
    This is the squash-merge signal: squash merges don't leave branch commits in
    main's ancestry, so git branch --merged is unreliable. Checking for the
    composed file on main is the authoritative indicator.

    Note: reads the local remote-tracking ref (origin/main) without fetching —
    results reflect the last fetch. A stale ref could produce a false positive
    (flagging a merged date as unmerged), but the next fetch will self-correct.
    """
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "origin/main", "sessions/"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        dates = set()
        for line in result.stdout.splitlines():
            fname = line.split("/")[-1]
            if fname.endswith(".stub.md") or not fname.endswith(".md"):
                continue
            # Composed files are named YYYY-MM-DD-<slug>.md
            if len(fname) >= 10 and fname[4] == "-" and fname[7] == "-":
                dates.add(fname[:10])
        return dates
    except Exception:
        return set()


# Memoized: this reader is called from more than one check with identical inputs;
# without the cache the same git (and for remote_draft_dates, NETWORK) call runs
# twice per hook fire. Process-lifetime cache, so no staleness within a run.
@functools.lru_cache(maxsize=1)
def remote_draft_dates() -> set[str]:
    """Return YYYY-MM-DD date strings for all remote draft/* branches."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "refs/heads/draft/*"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        dates: set[str] = set()
        for line in result.stdout.splitlines():
            if "\t" in line:
                ref = line.split("\t", 1)[1].strip()
                # Parse through branch_date so a suffixed branch (draft/2026-05-09-late,
                # draft/2026-07-03-lifting-logbook-late — 6 of 33 live branches) yields its
                # bare date. Previously the whole suffixed string was treated as the date, so
                # it could never match composed_dates_on_main()'s bare dates and every such
                # branch was reported as unmerged forever. (dev-env#873 review)
                parsed = branch_date(ref.replace("refs/heads/", ""))
                if parsed:
                    dates.add(parsed)
        return dates
    except Exception:
        return set()


def unmerged_draft_branches() -> list[str]:
    """Return remote draft/* branch names not yet merged into origin/main.

    Uses composed_dates_on_main() as the merge signal — squash merges don't
    leave branch commits in main's ancestry, making git branch --merged
    unreliable. A composed journal file on main is the authoritative indicator.
    """
    remote_dates = remote_draft_dates()
    if not remote_dates:
        return []
    merged = composed_dates_on_main()
    # TODAY is always excluded from automatic detection — today's active
    # branch is never stale. Use /journal-compose YYYY-MM-DD explicitly
    # to compose and merge the current day's journal.
    return sorted(
        [d for d in remote_dates if d != TODAY and d not in merged],
        reverse=True,
    )


def resurrected_draft_branches() -> list[str]:
    """Return remote draft/* branch names that were already merged but still exist.

    A branch is resurrected when its date has a composed journal file on
    origin/main (merged PR) but the remote branch was recreated by a later push.
    These need reconciliation via reconcile-late-stubs.py, not recomposition.

    Note: uses the local origin/main cache — results are only accurate after a
    recent git fetch. A stale cache may produce false negatives (silent), which
    is preferable to false-positive noise.
    """
    remote_dates = remote_draft_dates()
    if not remote_dates:
        return []
    merged = composed_dates_on_main()
    return sorted(
        [d for d in remote_dates if d != TODAY and d in merged],
        reverse=True,
    )


def branch_date(branch: str) -> str | None:
    """`YYYY-MM-DD` from a `draft/<date>[-suffix]` branch name, else None.

    Accepts the `-recovery` / other suffixed forms the compose skill documents, since a
    stub written onto `draft/2026-07-21-recovery` is just as date-mismatched as one on the
    plain branch. Pure string parse — unit-tested."""
    if not branch.startswith("draft/"):
        return None
    rest = branch[len("draft/"):]
    candidate = rest[:10]
    if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
        head, mid, tail = candidate[:4], candidate[5:7], candidate[8:10]
        if head.isdigit() and mid.isdigit() and tail.isdigit():
            return candidate
    return None


def _is_iso_date(text: str) -> bool:
    """True for exactly `YYYY-MM-DD`. Single definition shared by `branch_date` and
    `mismatched_stub_paths` — they previously disagreed, the latter checking only for `-`
    at positions 4 and 7, so `abcd-fg-ij_010101.stub.md` passed and then compared
    lexicographically greater than any real date. (dev-env#873 review)"""
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return False
    return text[:4].isdigit() and text[5:7].isdigit() and text[8:10].isdigit()


def mismatched_stub_paths(tree_paths: list[str], expected_date: str) -> list[str]:
    """Stub paths on a `draft/<expected_date>` branch dated *after* that date.

    `tree_paths` is `git ls-tree -r --name-only <branch> sessions/` output. Only
    convention-named `YYYY-MM-DD_HHMMSS.stub.md` files are considered — anything else is
    invisible to compose's own glob and so is not a mismatch this check can speak to.

    Deliberately one-sided: only a *newer*-dated stub is the day-rollover failure this
    check exists for (a later day's work written onto an older branch, which compose will
    never discover). Stubs dated *before* the branch are branch-lineage artifacts carried
    forward from `main` — a different and far older population that `stale_draft_artifacts`
    and `unmerged_draft_branches` already speak to, and including them here buried the six
    real hits under 27 unrelated ones on the first live run. ISO dates compare correctly
    as strings. Pure string filter — unit-tested."""
    out = []
    for path in tree_paths:
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".stub.md") or len(name) < 11 or name[10] != "_":
            continue
        date_prefix = name[:10]
        if not _is_iso_date(date_prefix):
            continue
        if date_prefix > expected_date:
            out.append(path)
    return sorted(out)


def summarize_by_project(paths: list[str]) -> str:
    """`N stub(s) across M project(s): career-playbook 2, dev-env 5, lifting-logbook 2`.

    A raw `paths[:5]` slice of a path-sorted list hid whole projects behind `(+N more)` —
    measured on the real draft/2026-07-21, both lifting-logbook entries were invisible — and
    the project spread is exactly what determines the remediation's scope. (dev-env#873 review)"""
    counts: dict[str, int] = {}
    for path in paths:
        parts = path.split("/")
        project = parts[1] if len(parts) > 2 and parts[0] == "sessions" else "?"
        counts[project] = counts.get(project, 0) + 1
    breakdown = ", ".join(f"{proj} {n}" for proj, n in sorted(counts.items()))
    return f"{len(paths)} stub(s) across {len(counts)} project(s): {breakdown}"


def canonical_current_branch() -> str | None:
    """The branch the shared canonical checkout currently holds; None on any failure
    (missing repo, detached HEAD, git not on PATH). Subprocess boundary — not unit-tested,
    matching this file's convention for the other git readers.

    Deliberately NOT memoized despite being called from two checks (day_rollover_message and
    stale_canonical_recovery_message): unlike the @lru_cache'd readers above (which are pure
    reads of state nothing in this process touches), check 5 can itself MUTATE this exact
    value via `git checkout main` between the two calls. Caching would make check 4's
    subsequent read return the pre-restore branch, firing a stale "you're on a stale branch"
    message about a problem check 5 just fixed one line earlier. The resulting duplicate
    `git branch --show-current` spawn happens on every ~RECHECK_MINUTES-gated run of this
    block (both checks call it unconditionally, not only when a stale branch is actually
    found) — negligible in absolute cost (once per ~30min per session), but correcting this
    from an earlier draft's inaccurate "only on the rare stale-branch path" claim (PR #912
    review finding).
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except Exception:
        return None


def branch_exists(branch: str) -> bool:
    """True when `branch` exists locally in the canonical. Subprocess boundary."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def canonical_is_dirty() -> bool:
    """True when the canonical has uncommitted `sessions/` changes. Subprocess boundary."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "sessions"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        return bool(result.stdout.strip())
    except Exception:
        return False


def branch_stub_paths(branch: str) -> list[str]:
    """`sessions/` paths committed on `branch` in the canonical. Reads the LOCAL branch ref
    deliberately: this asks what the canonical actually holds right now, including commits
    a session has made but not yet pushed — the state the rollover warning is about.
    Subprocess boundary — not unit-tested."""
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", branch, "sessions/"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()
    except Exception:
        return []


def format_day_rollover(
    branch: str,
    tree_paths: list[str],
    today: str,
    today_branch_exists: bool,
    canonical_dirty: bool,
) -> str | None:
    """Build the day-rollover advisory, or None when there is nothing to say.

    Pure: every input is passed in, so the fire/don't-fire decision, the recommended command,
    and the mismatch summary are all unit-testable. The subprocess reads live in
    `day_rollover_message()` below. (dev-env#873 review — the previous version hard-wired its
    git calls and so could not be tested at all, inconsistent with `classify_deletions` in the
    sibling hook and with `test_journal_canonical_guard.py`'s fixture-repo precedent.)"""
    bdate = branch_date(branch)
    if not bdate or bdate == today:
        return None

    ej = JOURNAL_REPO.as_posix()
    # Idempotent by construction: `checkout -b` fails when the branch already exists, and it
    # fails AFTER `checkout main` has already moved the shared canonical — parking it on main
    # for every other session. Pick the command that matches reality instead.
    if today_branch_exists:
        cut = f"git -C {ej} checkout draft/{today} && git -C {ej} pull --ff-only"
    else:
        cut = (
            f"git -C {ej} checkout main && git -C {ej} pull && "
            f"git -C {ej} checkout -b draft/{today}"
        )

    msg = (
        f"[journal-hook] Day rollover — the engineering-journal canonical is on {branch}, "
        f"but today is {today}. Do NOT write today's stub onto {branch}: /journal-compose "
        f"resolves its source branch from the date (draft/<DATE>) and the nightly routine "
        f"exits silently when that branch is missing, so a stub whose filename date does "
        f"not match its branch date is composed by nothing and reported by nothing. Move to "
        f"today's branch first: {cut}. Several unmerged draft branches coexisting is normal "
        f"- they are independent per-day units, not a reason to reuse one. Do this BEFORE "
        f"committing any open-PR shard deletion: a deletion is durable only once its "
        f"carrying branch merges to main, so one committed on {branch} is invisible to "
        f"draft/{today}."
    )

    if canonical_dirty:
        # journal-canonical-guard.py refuses to switch a dirty canonical for exactly this
        # reason; two hooks in the same domain must not give opposite advice.
        msg += (
            f" CAUTION: the canonical has uncommitted changes right now - they belong to "
            f"concurrent sessions. Check `git -C {ej} status --porcelain` and let them settle "
            f"(or commit only your own files with an explicit pathspec) before switching."
        )

    mismatched = mismatched_stub_paths(tree_paths, bdate)
    if mismatched:
        msg += (
            f" ALREADY DATE-MISMATCHED on {branch}: {summarize_by_project(mismatched)}, all "
            f"dated after {bdate}. Newest: {', '.join(sorted(mismatched)[-3:])}. Repair "
            f"additively (never rewrite that shared branch's history) and tile the "
            f"remediation, per claude/CLAUDE.md -> Stub file workflow -> Date-mismatched stub."
        )
    return msg


def day_rollover_message() -> str | None:
    """Subprocess wrapper around `format_day_rollover`. Reads the canonical's branch first and
    returns early when there is no rollover, so the more expensive tree read and the two extra
    probes only happen in the rare firing case."""
    branch = canonical_current_branch()
    if not branch:
        return None
    bdate = branch_date(branch)
    if not bdate or bdate == TODAY:
        return None
    return format_day_rollover(
        branch,
        branch_stub_paths(branch),
        TODAY,
        branch_exists(f"draft/{TODAY}"),
        canonical_is_dirty(),
    )


# --- Check 5: stale-canonical self-healing (ADR-119 Amendment 1, dev-env#911) -------------


def canonical_full_tree_dirty() -> bool:
    """True when the canonical has ANY uncommitted change, anywhere in the working tree —
    not just `sessions/`. Subprocess boundary.

    Deliberately broader than `canonical_is_dirty()` (sessions/-scoped, sufficient for that
    function's advisory-only CAUTION text). The stale-canonical auto-restore below performs a
    REAL `git checkout`, which can silently carry uncommitted changes on any tracked file
    across branches when they don't conflict with the target branch — a sessions/-only gate
    would miss exactly the concurrent-session collision this check exists to bound
    (dev-env#911).

    Fails toward "dirty" — the OPPOSITE direction from `canonical_is_dirty()` (which fails
    toward "not dirty", fine for a CAUTION string nobody acts on). An unreadable status must
    never be treated as safe to auto-checkout.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return True
        return bool(result.stdout.strip())
    except Exception:
        return True


def canonical_head_idle_minutes() -> float | None:
    """Minutes since HEAD last moved in the canonical -- a checkout OR a commit, whichever is
    more recent. None on any failure; the caller treats None the same as "not idle enough" —
    never as "idle" — so an unreadable timestamp fails toward inaction. Subprocess boundary.

    THIS, not a branch's own tip-commit time, is the correct idle signal for check 5 (PR
    #912 review finding). The naive `git log -1 --format=%ct <branch>` reads the branch's
    TIP COMMIT time -- which is, by construction, already old for any branch this check
    considers "stale". That means a session that just checked the stale branch out to do
    legitimate work (the ADR-119 decision-3 shard-deletion hop this check exists downstream
    of) would find idle_minutes already past STALE_CANONICAL_IDLE_MINUTES at the INSTANT of
    checkout -- giving it ZERO of the "comfortable headroom" the threshold is meant to
    provide, and exposing it to having the branch yanked back to `main` mid-work by any
    concurrent session's hook firing moments later.

    HEAD's reflog records every ref update to HEAD -- both a checkout and a commit on the
    currently-checked-out branch -- so its most recent entry is exactly "the last time
    anyone did anything with this checkout," correctly resetting the idle clock on EITHER
    event. Verified empirically: `git log -g -1 --date=unix --format=%gd HEAD` reports the
    real wall-clock time of the checkout itself, whereas `%ct` on the same walk still reports
    the (possibly long-past) commit's own author/committer date -- the two are NOT
    interchangeable despite both being drawn from the same reflog walk.
    """
    try:
        result = subprocess.run(
            ["git", "log", "-g", "-1", "--date=unix", "--format=%gd", "HEAD"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = result.stdout.strip()
        if result.returncode != 0 or "{" not in out or "}" not in out:
            return None
        epoch_str = out.split("{", 1)[1].rsplit("}", 1)[0]
        return (time.time() - float(epoch_str)) / 60
    except Exception:
        return None


def stale_canonical_recovery_decision(
    branch: str,
    today: str,
    dirty: bool,
    idle_minutes: float | None,
) -> str | None:
    """Decide whether to auto-restore the canonical, and build the advisory if so; None means
    "do nothing". Named `_decision`, not `format_*` like its sibling `format_day_rollover`,
    specifically because it is NOT purely advisory the way that sibling is: a non-None return
    here means BOTH "print this" AND "go ahead and restore" — the caller
    (`stale_canonical_recovery_message`) performs the actual checkout only when this returns
    non-None. `format_day_rollover`'s non-None return only ever prints; conflating the two
    naming conventions risks a future maintainer reusing this function's shape assuming the
    advisory-only semantics of its `format_*`-named sibling (PR #912 review finding). This
    function itself never touches git or the filesystem — every input is passed in, so the
    fire-or-stay-silent decision is unit-testable without a subprocess.

    dev-env#911: two concurrent sessions can collide on the shared canonical's HEAD with no
    coordination mechanism (a new locking primitive was explicitly ruled out of this fix's
    scope). This bounds how long the resulting stranded state can persist undetected, rather
    than preventing the collision itself.

    The dirty check is unconditional and checked before idle time, deliberately: a dirty tree
    is never auto-touched regardless of how idle it looks, full stop. This is the single most
    important safety property of this check — getting it wrong risks discarding a concurrent
    session's uncommitted work, exactly the class of harm dev-env#911 was raised to prevent.

    idle_minutes is measured from the last time HEAD moved at all — a checkout AND a commit
    both reset it (see `canonical_head_idle_minutes`) — so it correctly reads as low
    immediately after a session checks the branch out, giving real in-flight work the
    STALE_CANONICAL_IDLE_MINUTES of headroom the threshold is meant to provide.
    """
    bdate = branch_date(branch)
    if not bdate or bdate == today:
        return None
    if dirty:
        return None
    if idle_minutes is None or idle_minutes < STALE_CANONICAL_IDLE_MINUTES:
        return None
    return (
        f"[journal-hook] Canonical recovery — the engineering-journal canonical was "
        f"stranded on {branch} ({idle_minutes:.0f}min since HEAD last moved, clean tree) "
        f"with no coordination mechanism to prevent this (dev-env#911) — restored to main. "
        f"Any session's normal first-session-of-the-day procedure will move it to "
        f"draft/{today} from here."
    )


def stale_canonical_recovery_message() -> str | None:
    """Subprocess wrapper around `stale_canonical_recovery_decision`: reads the canonical's
    branch/dirty/idle state and, only when the decision says to, performs the actual
    `git checkout main`.

    This is the one check in this hook whose action is NOT read-only — every other check here
    only ever prints advice. A failed (or aborted) checkout must never crash or block the
    hook, matching this file's fail-open contract: caught and reported inline, never raised.
    """
    branch = canonical_current_branch()
    if not branch:
        return None
    bdate = branch_date(branch)
    if not bdate or bdate == TODAY:
        return None
    dirty = canonical_full_tree_dirty()
    idle_minutes = canonical_head_idle_minutes()
    message = stale_canonical_recovery_decision(branch, TODAY, dirty, idle_minutes)
    if not message:
        return None

    # Final, cheap re-check immediately before the mutation itself, narrowing the residual
    # TOCTOU window to a single subprocess pair — mirrors journal-canonical-guard.py's
    # identical precaution for its own auto-checkout of this same shared canonical. Acting on
    # the first read alone risks yanking a branch a concurrent session started legitimate
    # work on in the interim (e.g. the ADR-119 decision-3 "commit an orphaned shard deletion
    # immediately" hop this whole check exists downstream of).
    if canonical_current_branch() != branch or canonical_full_tree_dirty():
        return None  # situation already changed since the first read — leave it alone

    try:
        result = subprocess.run(
            ["git", "checkout", "main"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return message
    except Exception:
        result = None

    # The checkout failed (or raised). Re-check before asserting anything is still wrong:
    # journal-canonical-guard.py (or a concurrent session) can restore this exact canonical
    # to `main` independently, and a transient `index.lock` collision between the two hooks
    # racing the same checkout is exactly the kind of contention this feature lives inside of
    # -- reporting "checkout failed, fix it manually" for a canonical that is already fine
    # (or was fixed a moment later) would be actively misleading (PR #912 review finding).
    now = canonical_current_branch()
    if now != branch:
        return None  # someone else already resolved it -- nothing left to say
    stderr = result.stderr.strip() if result is not None and result.stderr else "checkout could not be run"
    return (
        f"[journal-hook] Canonical recovery: {branch} looks stranded "
        f"({idle_minutes:.0f}min idle, clean tree) but `git checkout main` failed: "
        f"{stderr}. Switch it back manually: git -C {JOURNAL_REPO.as_posix()} checkout main"
    )


def emit(messages: list[str]) -> None:
    """Print the accumulated systemMessage, if any. Flag bookkeeping is the caller's job —
    the two checks are gated by separate sentinels, so `emit` must not own either."""
    if not messages:
        return
    print(json.dumps({"systemMessage": " ".join(messages)}))


def flag_fresh(path: Path) -> bool:
    """True when `path` exists and is younger than RECHECK_MINUTES.

    Age-based rather than mere-existence, so a check re-arms during a long session (a day
    rollover can begin mid-session) without paying its git spawns on *every* prompt. The
    previous "write the flag only when something was emitted" rule meant the common quiet
    run wrote no flag and re-ran everything each prompt — measured at 1.74s per prompt for
    the full check set, and a new 130ms tax on worktree sessions that used to exit before any
    subprocess at all. (dev-env#873 review)"""
    try:
        return (time.time() - path.stat().st_mtime) < RECHECK_MINUTES * 60
    except Exception:
        return False


def touch_flag(path: Path) -> None:
    """Re-arm a check's sentinel. Written whether or not the check found anything — that is
    the point: a quiet run must also suppress the next prompt's spawn."""
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        path.touch()
    except Exception:
        pass


def main() -> None:
    _hookutil.record_heartbeat("new-day-journal-check")
    raw = ""
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        pass
    hook_data = json.loads(raw) if raw else {}
    session_id = hook_data.get("session_id", "")

    # Checks 1-3 stay suppressed in Claude-managed worktree sessions — they concern the
    # canonical's own housekeeping and are only actionable from a main-checkout session.
    # Check 4 (day rollover) is NOT suppressed: worktree sessions write journal stubs into
    # the canonical via `git -C` just like any other session, so they are exactly who needs
    # to be told which draft branch to cut (ADR-119, dev-env#866 — the session that hit
    # this was itself a worktree session and saw nothing). Because worktree sessions can
    # now emit, they can now write a flag, so cleanup_stale_flags() must run for them too.
    in_worktree = False
    cwd = hook_data.get("cwd", "")
    if cwd:
        _parts = Path(cwd).parts
        in_worktree = ".claude" in _parts and "worktrees" in _parts

    cleanup_stale_flags()

    # Two sentinels, deliberately separate. Check 4 (and check 5, its self-healing sibling —
    # see the module docstring) runs in worktree sessions where checks 1-3 do not, so a
    # shared flag would let a worktree session's rollover emission suppress checks 1-3 for
    # the rest of that session — including after the cwd later leaves the worktree, which is
    # exactly when they become actionable. (dev-env#873 review)
    #
    # Check 5 deliberately shares check 4's sentinel rather than getting a third of its own:
    # both key off the same canonical-branch state and are always evaluated together. Check 5
    # runs FIRST specifically so that when it fires (mutating the branch), check 4's own
    # fresh, unmemoized read of canonical_current_branch() naturally observes the post-restore
    # state and stays silent — no special-casing needed, and no risk of a "you're on a stale
    # branch" message immediately after that same branch was just auto-restored. (dev-env#911)
    rollover_flag = SCRATCH / f"journal_hook_rollover_{session_id}.flag" if session_id else None
    canonical_flag = SCRATCH / f"journal_hook_{session_id}.flag" if session_id else None

    messages = []

    if rollover_flag is None or not flag_fresh(rollover_flag):
        recovery = stale_canonical_recovery_message()
        if recovery:
            messages.append(recovery)
        rollover = day_rollover_message()
        if rollover:
            messages.append(rollover)
        if rollover_flag is not None:
            touch_flag(rollover_flag)

    if in_worktree:
        emit(messages)
        sys.exit(0)

    if canonical_flag is not None and flag_fresh(canonical_flag):
        emit(messages)
        sys.exit(0)
    if canonical_flag is not None:
        touch_flag(canonical_flag)

    stale = stale_draft_artifacts()
    if stale:
        artifact_path = Path(stale[0]).as_posix()
        artifact_date = os.path.basename(stale[0])[:10]  # YYYY-MM-DD prefix
        messages.append(
            f"[journal-hook] Stale draft artifact detected ({artifact_date}): {artifact_path} — "
            f"the engineering journal draft from {artifact_date} was never composed. "
            f"Mention this to the user at a natural pause and suggest running /journal-compose "
            f"in a dedicated session. Do not defer or interrupt the user's current request."
        )

    unmerged = unmerged_draft_branches()
    if unmerged:
        dates_str = ", ".join(unmerged)
        # Wording tracks the predicate exactly: unmerged_draft_branches() returns dates with
        # NO composed file on main, which covers two states the old text conflated (it
        # asserted "these branches have composed journal files", the opposite of the filter).
        # /journal-compose handles both — it skips a project already composed for that date.
        messages.append(
            f"[journal-hook] Draft branch(es) with no composed journal on main: {dates_str}\n"
            f"Each was either never composed, or composed but never merged via a PR.\n"
            f"Suggest running /journal-compose {unmerged[0]} (and any others listed) in a "
            f"dedicated session; it skips any project already composed for that date."
        )

    resurrected = resurrected_draft_branches()
    if resurrected:
        dates_str = ", ".join(resurrected)
        messages.append(
            f"[journal-hook] Resurrected draft branch(es) detected: {dates_str}\n"
            f"draft/{resurrected[0]} was already merged but new commits were pushed to it after the merge.\n"
            f"Run: py -3 ~/.claude/scripts/reconcile-late-stubs.py draft/{resurrected[0]}"
        )

    emit(messages)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
