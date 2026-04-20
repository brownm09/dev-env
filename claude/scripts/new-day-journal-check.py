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


def unmerged_draft_branches() -> list[str]:
    """Return remote draft/* branch names not yet merged into origin/main.

    Fast path: check local remote-tracking refs (zero network cost).
    Verification pass: for any branch that appears unmerged locally, confirm it
    still exists on the remote via ls-remote. This prunes false positives caused
    by stale tracking refs left behind after squash-merge + branch deletion,
    while avoiding a network call when there is nothing to verify.
    """
    try:
        # Fast path — local tracking refs, no network
        result = subprocess.run(
            ["git", "branch", "-r", "--list", "origin/draft/*"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        candidates = []
        for b in result.stdout.splitlines():
            b = b.strip()
            date_part = b.replace("origin/draft/", "")
            if date_part and date_part != TODAY:
                merged = subprocess.run(
                    ["git", "branch", "-r", "--merged", "origin/main", "--list", b],
                    cwd=JOURNAL_REPO,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if b not in merged.stdout:
                    candidates.append(date_part)

        if not candidates:
            return []

        # Verification pass — one ls-remote call only when candidates exist.
        # Squash-merge + delete means "branch still on remote = not yet merged".
        ls = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "refs/heads/draft/*"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        remote_dates = set()
        for line in ls.stdout.splitlines():
            if "\t" in line:
                ref = line.split("\t", 1)[1].strip()
                remote_dates.add(ref.replace("refs/heads/draft/", ""))

        unmerged = [d for d in candidates if d in remote_dates]
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
