"""What counts as a late commit on a journal draft branch (dev-env#1262).

Shared by new-day-journal-check.py, which warns about late commits, and
reconcile-late-stubs.py, which moves them, so the two can't disagree.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import re
import subprocess
from datetime import datetime
from pathlib import Path

# reconcile-late-stubs.py points this tag at a branch's tip once its late stubs are moved.
RECONCILED_TAG_PREFIX = "refs/tags/reconciled/"
DRAFT_BRANCH = re.compile(r"draft/(\d{4}-\d{2}-\d{2})(-[A-Za-z0-9._-]+)?")
# The files that hold session content. A late commit touching only open-PR shards, or a
# merge from main, leaves nothing to reconcile.
CONTENT_PATHS = ("*.stub.md", "*.manifest.jsonl", "*/reports/*.md", "*/tiles/*.json")
BEGINNING = "1970-01-01T00:00:00Z"


def late_commits(repo: Path, tip: str, merged_at: str, reconciled: str | None = None,
                 paths: tuple[str, ...] = (), timeout: float = 30) -> list[str]:
    """Commits on `tip` from `merged_at` on, not on main and not behind `reconciled`.

    Filters dates here, not with `git log --after`, which stops at the first older commit
    and hides any late parent behind it. Raises if git fails, so a missing object never
    reads as "no late commits".
    """
    exclude = ["origin/main"] + ([reconciled] if reconciled else [])
    cmd = ["git", "log", "--format=%H %ct", tip, "--not", *exclude]
    if paths:
        cmd += ["--", *paths]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout, check=True)
    cutoff = datetime.fromisoformat(merged_at.replace("Z", "+00:00")).timestamp()
    commits = (line.split() for line in result.stdout.splitlines())
    return [sha for sha, committed in commits if int(committed) >= cutoff]
