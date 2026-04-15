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


def stale_draft_files() -> list[str]:
    """Return paths of *_draft.md files from before today."""
    pattern = str(JOURNAL_REPO / "sessions" / "**" / "*_draft.md")
    drafts = glob.glob(pattern, recursive=True)
    stale = [f for f in drafts if not os.path.basename(f).startswith(TODAY)]
    stale.sort(key=lambda f: os.path.basename(f), reverse=True)
    return stale


def unmerged_draft_branches() -> list[str]:
    """Return remote draft/* branch names not yet merged into origin/main."""
    try:
        # List remote draft branches
        result = subprocess.run(
            ["git", "branch", "-r", "--list", "origin/draft/*"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        branches = [b.strip() for b in result.stdout.splitlines() if b.strip()]

        # Find which are not merged into origin/main
        unmerged = []
        for branch in branches:
            date_part = branch.replace("origin/draft/", "")
            if date_part == TODAY:
                continue  # current day is always open
            merged_result = subprocess.run(
                ["git", "branch", "-r", "--merged", "origin/main", "--list", branch],
                cwd=JOURNAL_REPO,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if branch not in merged_result.stdout:
                unmerged.append(date_part)

        unmerged.sort(reverse=True)
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

    stale = stale_draft_files()
    if stale:
        draft_path = Path(stale[0]).as_posix()
        draft_date = os.path.basename(stale[0]).replace("_draft.md", "")
        messages.append(
            f"[journal-hook] Stale draft file detected: {draft_path}\n"
            f"The engineering journal draft from {draft_date} was never composed.\n"
            f"Before responding to the user's message, invoke the journal-compose skill "
            f"to close out the {draft_date} session. Then proceed with the user's request."
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
