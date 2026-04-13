#!/usr/bin/env python3
"""
UserPromptSubmit hook: detect stale draft files in engineering-journal.

If a *_draft.md file from a previous calendar day exists in the journal's
sessions/ tree, inject a reminder so Claude runs /journal-compose before
handling the user's request.

Exit 0 always — never block the user's prompt.
Stdout is injected as context Claude sees before processing the user's message.
"""

import glob
import os
import sys
from datetime import date
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
TODAY = date.today().strftime("%Y-%m-%d")


def main() -> None:
    # Consume stdin (UserPromptSubmit sends JSON; we don't need the contents).
    try:
        sys.stdin.read()
    except Exception:
        pass

    pattern = str(JOURNAL_REPO / "sessions" / "**" / "*_draft.md")
    drafts = glob.glob(pattern, recursive=True)
    stale = [
        f for f in drafts
        if not os.path.basename(f).startswith(TODAY)
    ]

    if not stale:
        sys.exit(0)

    # Most recent stale draft first (basename sort is date-based).
    stale.sort(key=lambda f: os.path.basename(f), reverse=True)
    draft_path = Path(stale[0]).as_posix()
    draft_date = os.path.basename(stale[0]).replace("_draft.md", "")

    print(
        f"[journal-hook] Stale draft detected: {draft_path}\n"
        f"The engineering journal draft from {draft_date} was never composed.\n"
        f"Before responding to the user's message, invoke the journal-compose skill "
        f"to close out the {draft_date} session. Then proceed with the user's request."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
