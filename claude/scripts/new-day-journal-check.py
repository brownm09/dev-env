#!/usr/bin/env python3
"""
UserPromptSubmit hook: detect stale journal work in engineering-journal.

Two checks:
1. *_draft.md files from a previous calendar day still on disk (composed but
   branch not yet deleted, or composition never ran).
2. Remote draft/* branches that have not been merged into main (composition ran
   and draft file was deleted, but the PR was never opened/merged).

Exit 0 always — never block the user's prompt.
Stdout is injected as context Claude sees before processing the user's message.
"""

import glob
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
        # Match only convention-named stubs: YYYY-MM-DD_HHMMSS.stub.md
        str(JOURNAL_REPO / "sessions" / "**" / "????-??-??_*.stub.md"),
    ):
        artifacts.extend(glob.glob(pattern, recursive=True))
    stale = [f for f in artifacts if not os.path.basename(f).startswith(TODAY)]
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


def unmerged_draft_branches() -> list[str]:
    """Return remote draft/* branch names not yet merged into origin/main.

    Uses composed_dates_on_main() as the merge signal — squash merges don't
    leave branch commits in main's ancestry, making git branch --merged
    unreliable. A composed journal file on main is the authoritative indicator.
    """
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
        # TODAY is always excluded from automatic detection — today's active
        # branch is never stale. Use /journal-compose YYYY-MM-DD explicitly
        # to compose and merge the current day's journal.
        unmerged = sorted(
            [d for d in remote_dates if d != TODAY and d not in merged],
            reverse=True,
        )
        return unmerged
    except Exception:
        return []


def main() -> None:
    # Consume stdin (UserPromptSubmit sends JSON; we don't need the contents).
    try:
        sys.stdin.read()
    except Exception:
        pass

    messages = []

    stale = stale_draft_artifacts()
    if stale:
        artifact_path = Path(stale[0]).as_posix()
        artifact_date = os.path.basename(stale[0])[:10]  # YYYY-MM-DD prefix
        messages.append(
            f"[journal-hook] Stale draft artifact detected: {artifact_path}\n"
            f"The engineering journal draft from {artifact_date} was never composed.\n"
            f"Before responding to the user's message, invoke the journal-compose skill "
            f"to close out the {artifact_date} session. Then proceed with the user's request."
        )

    unmerged = unmerged_draft_branches()
    if unmerged:
        dates_str = ", ".join(unmerged)
        messages.append(
            f"[journal-hook] Unmerged draft branch(es) detected: {dates_str}\n"
            f"These branches have composed journal files but no PR was opened or merged into main.\n"
            f"Remind the user that draft/{unmerged[0]} (and any others listed) still need a PR to main."
        )

    if messages:
        print("\n".join(messages))

    sys.exit(0)


if __name__ == "__main__":
    main()
