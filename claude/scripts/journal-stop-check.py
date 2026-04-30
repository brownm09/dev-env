#!/usr/bin/env python3
"""
Stop hook: remind user of stale journal work at session end.

Two checks (same logic as new-day-journal-check.py):
1. Stale *_draft.md / *.stub.md files from before today
2. Unmerged remote draft/* branches

Also cleans up orphaned draft files: physical files left on disk as untracked
after git rm. This prevents new-day-journal-check.py false positives on the
next session (see dev-env#31).

Exit 0 always — never block session close.
Stdout is shown to user as a closing message.
"""

import glob
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
TODAY = date.today().strftime("%Y-%m-%d")


def stale_draft_artifacts() -> list[str]:
    """Return paths of *_draft.md or *.stub.md files from before today."""
    artifacts = []
    for pattern in (
        str(JOURNAL_REPO / "sessions" / "**" / "*_draft.md"),
        str(JOURNAL_REPO / "sessions" / "**" / "????-??-??_*.stub.md"),
    ):
        artifacts.extend(glob.glob(pattern, recursive=True))
    stale = [f for f in artifacts if not os.path.basename(f).startswith(TODAY)]
    stale.sort(key=lambda f: os.path.basename(f), reverse=True)
    return stale


def composed_dates_on_main() -> set[str]:
    """Return YYYY-MM-DD dates that have a composed journal file on origin/main."""
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
            if len(fname) >= 10 and fname[4] == "-" and fname[7] == "-":
                dates.add(fname[:10])
        return dates
    except Exception:
        return set()


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

        merged = composed_dates_on_main()
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

    messages = []

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
