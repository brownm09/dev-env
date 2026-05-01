#!/usr/bin/env python3
"""
Stop hook: remind Claude to archive after a stub push, and remind the user
of stale journal work at session end.

Check 1 — stub-push sentinel:
  If stub-push-archive-reminder.py wrote a sentinel flag (meaning a stub was
  pushed to engineering-journal this session), consume the flag and emit a
  closing message reminding the user to call ccd_session_mgmt__archive_session.

Checks 2–3:
1. Stale *_draft.md / *.stub.md files from before today
2. Unmerged remote draft/* branches

Also cleans up orphaned draft files: physical files left on disk as untracked
after git rm. This prevents new-day-journal-check.py false positives on the
next session (see dev-env#31).
"""

import glob
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL = Path.home() / ".claude" / "scratch" / "stub-pushed.flag"
TODAY = date.today().strftime("%Y-%m-%d")


def consume_stub_pushed_sentinel() -> str | None:
    """Return a reminder message if the stub-push sentinel exists, else None.

    Deletes the sentinel before returning so the reminder fires only once.
    Any I/O failure is swallowed — the sentinel check is best-effort.
    """
    try:
        if SENTINEL.exists():
            SENTINEL.unlink()
            return (
                "Stub committed and pushed to engineering-journal. "
                "Archive this session now: call ccd_session_mgmt__archive_session "
                "(use list_sessions to look up the current session_id if needed). "
                "Then stop."
            )
    except Exception:
        pass
    return None


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


def unmerged_draft_branches() -> list[str]:
    """Return remote draft/* branch names not yet merged into origin/main."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "refs/heads/draft/*"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        remote_dates = set()
        for line in result.stdout.splitlines():
            if "\t" in line:
                ref = line.split("\t", 1)[1].strip()
                remote_dates.add(ref.replace("refs/heads/draft/", ""))

        if not remote_dates:
            return []

        merged_pairs = composed_project_dates_on_main()
        merged = {d for _, d in merged_pairs}
        unmerged = sorted(
            [d for d in remote_dates if d != TODAY and d not in merged],
            reverse=True,
        )
        return unmerged
    except Exception:
        return []


def remove_orphaned_drafts(stale: list[str]) -> list[str]:
    """Delete stale draft files that are untracked (orphaned after git rm)."""
    removed = []
    for path_str in stale:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", path_str],
                cwd=JOURNAL_REPO,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout.startswith("??"):
                os.remove(path_str)
                removed.append(path_str)
        except Exception:
            pass
    return removed


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        raw = ""
    # session_id / transcript_path available if needed in future

    # Sentinel check: stub was pushed this session — remind user to archive.
    reminder = consume_stub_pushed_sentinel()

    messages = []
    if reminder:
        messages.append(f"[journal-stop-hook] {reminder}")

    stale = stale_draft_artifacts()

    removed = remove_orphaned_drafts(stale)
    for path_str in removed:
        messages.append(
            f"[journal-stop-hook] Removed orphaned draft: {Path(path_str).as_posix()}"
        )

    # After removing orphans, re-evaluate what's still stale
    still_stale = [f for f in stale if f not in removed]

    if still_stale:
        artifact_path = Path(still_stale[0]).as_posix()
        artifact_date = os.path.basename(still_stale[0])[:10]
        messages.append(
            f"[journal-stop-hook] Stale draft artifact: {artifact_path}\n"
            f"The engineering journal draft from {artifact_date} was never composed.\n"
            f"Run /journal-compose in a new session to close it out."
        )

    unmerged = unmerged_draft_branches()
    if unmerged:
        dates_str = ", ".join(unmerged)
        messages.append(
            f"[journal-stop-hook] Unmerged draft branch(es): {dates_str}\n"
            f"These branches still need a PR merged to main."
        )

    if messages:
        print("\n".join(messages))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
