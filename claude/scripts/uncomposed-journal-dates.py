#!/usr/bin/env python3
"""Print the past journal dates the nightly compose should compose, oldest first.

Purpose: date selection for the nightly journal compose, so a missed run is caught up.

The nightly compose used to target only yesterday (ADR-084), so a run missed while the
routine host was off was never retried (dev-env#1413, #875). This lists every date D with
today - LOOKBACK_DAYS <= D < today whose remote draft/<D> still holds a date-matching stub,
sessions/<project>/<D>_HHMMSS.stub.md for a project with no composed journal for D on
origin/main. /journal-compose deletes the stubs it composes, so a composed day drops out; the
origin/main check also drops a draft branch that outlived its compose.

Today is never listed: /journal-compose's today-guard (ADR-017) would refuse it.

Reads the local origin/* refs; the caller fetches first.

Usage:
    git -C C:/Users/brown/Git/engineering-journal fetch origin --prune --quiet
    py -3 claude/scripts/uncomposed-journal-dates.py

Exit 0 always on a successful read, printing nothing when there is nothing to compose.
Exit 2 if git fails.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
# Two weeks covers a long host outage (a holiday away from the desktop) while keeping a
# first catch-up run bounded. The months-old backlog found on 2026-11-16 was composed by hand.
LOOKBACK_DAYS = 14
DRAFT_RE = re.compile(r"^origin/draft/(\d{4}-\d{2}-\d{2})$")


def stub_re(d: str) -> re.Pattern:
    return re.compile(rf"^sessions/[^/]+/{re.escape(d)}_\d{{6}}\.stub\.md$")


def composed_pairs(main_paths: list[str]) -> set[tuple[str, str]]:
    """(project, YYYY-MM-DD) pairs with a composed journal on main: a non-stub .md directly in
    sessions/<project>/, the same rule as composed_project_dates_on_main() in
    new-day-journal-check.py (a report or retro in a subdirectory doesn't count, dev-env#1209)."""
    pairs = set()
    for p in main_paths:
        parts = p.split("/")
        if len(parts) != 3 or parts[0] != "sessions":
            continue
        fname = parts[2]
        if fname.endswith(".stub.md") or not fname.endswith(".md"):
            continue
        if len(fname) >= 10 and fname[4] == "-" and fname[7] == "-":
            pairs.add((parts[1], fname[:10]))
    return pairs


def select_dates(drafts: dict[str, list[str]], composed: set[tuple[str, str]], today: date,
                 lookback: int = LOOKBACK_DAYS) -> list[str]:
    """Return the draft dates to compose, oldest first.

    drafts maps each draft date (YYYY-MM-DD) to the paths on its branch. A date counts only
    if some stub's project has no composed journal for it yet: a draft branch can outlive its
    compose (the compose/<D> conflict route), and relisting it would re-run the compose every
    night for nothing.
    """
    earliest = today - timedelta(days=lookback)
    out = []
    for d, paths in drafts.items():
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        if not (earliest <= day < today):
            continue
        pattern = stub_re(d)
        if any(pattern.match(p) and (p.split("/")[1], d) not in composed for p in paths):
            out.append(d)
    return sorted(out)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(JOURNAL_REPO), *args],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    ).stdout


def main() -> int:
    today = date.today()
    earliest = today - timedelta(days=LOOKBACK_DAYS)
    try:
        refs = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/draft/")
        drafts: dict[str, list[str]] = {}
        for ref in refs.splitlines():
            m = DRAFT_RE.match(ref.strip())
            if not m:
                continue
            try:
                day = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if not (earliest <= day < today):
                continue  # skip the ls-tree for drafts select_dates would drop anyway
            drafts[m.group(1)] = git("ls-tree", "-r", "--name-only", ref, "--", "sessions/").splitlines()
        composed = composed_pairs(
            git("ls-tree", "-r", "--name-only", "origin/main", "--", "sessions/").splitlines())
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"uncomposed-journal-dates: git failed: {exc}", file=sys.stderr)
        return 2
    for d in select_dates(drafts, composed, today):
        print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
