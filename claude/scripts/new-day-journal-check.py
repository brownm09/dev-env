#!/usr/bin/env python3
"""
UserPromptSubmit hook: detect stale journal work in engineering-journal.

Four checks:
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
   reported by nothing, silently. 26 such stubs across 5 dates were sitting
   uncomposed on origin/main when this check was written.

Check 4 is the only one that also runs in Claude-managed worktree sessions:
those write stubs into the canonical via `git -C`, so they are exactly who
needs the warning, whereas checks 1-3 concern the canonical's own housekeeping
and would be noise there.

Exit 0 always — never block the user's prompt (advisory hook, fails open).
Stdout is injected as context Claude sees before processing the user's message.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
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
                dates.add(ref.replace("refs/heads/draft/", ""))
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
        if date_prefix[4] != "-" or date_prefix[7] != "-":
            continue
        if date_prefix > expected_date:
            out.append(path)
    return sorted(out)


def canonical_current_branch() -> str | None:
    """The branch the shared canonical checkout currently holds; None on any failure
    (missing repo, detached HEAD, git not on PATH). Subprocess boundary — not unit-tested,
    matching this file's convention for the other git readers."""
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


def day_rollover_message() -> str | None:
    """Advisory when the canonical rests on a draft branch dated other than today."""
    branch = canonical_current_branch()
    if not branch:
        return None
    bdate = branch_date(branch)
    if not bdate or bdate == TODAY:
        return None

    msg = (
        f"[journal-hook] Day rollover — the engineering-journal canonical is on {branch}, "
        f"but today is {TODAY}. Do NOT write today's stub onto {branch}: /journal-compose "
        f"resolves its source branch from the date (draft/<DATE>) and the nightly routine "
        f"exits silently when that branch is missing, so a stub whose filename date does "
        f"not match its branch date is composed by nothing and reported by nothing. Cut "
        f"today's branch from main first: "
        f"git -C {JOURNAL_REPO.as_posix()} checkout main && "
        f"git -C {JOURNAL_REPO.as_posix()} pull && "
        f"git -C {JOURNAL_REPO.as_posix()} checkout -b draft/{TODAY}. "
        f"Several unmerged draft branches coexisting is normal — they are independent "
        f"per-day units, not a reason to reuse one."
    )

    mismatched = mismatched_stub_paths(branch_stub_paths(branch), bdate)
    if mismatched:
        shown = ", ".join(mismatched[:5])
        more = f" (+{len(mismatched) - 5} more)" if len(mismatched) > 5 else ""
        msg += (
            f" ALREADY DATE-MISMATCHED on {branch}: {len(mismatched)} stub(s) dated after "
            f"{bdate} — {shown}{more}. Repair additively (never rewrite that shared "
            f"branch's history) and tile the remediation, per claude/CLAUDE.md -> Stub "
            f"file workflow -> Date-mismatched stub."
        )
    return msg


def emit(messages: list[str], session_id: str) -> None:
    """Print the once-per-day systemMessage and set the session flag. No-op when there is
    nothing to say, so a quiet run never burns the flag and suppresses a later real
    finding in the same session."""
    if not messages:
        return
    print(json.dumps({"systemMessage": " ".join(messages)}))
    if session_id:
        try:
            (SCRATCH / f"journal_hook_{session_id}.flag").touch()
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

    if session_id:
        flag_path = SCRATCH / f"journal_hook_{session_id}.flag"
        if flag_path.exists():
            sys.exit(0)

    messages = []

    rollover = day_rollover_message()
    if rollover:
        messages.append(rollover)

    if in_worktree:
        emit(messages, session_id)
        sys.exit(0)

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

    emit(messages, session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
