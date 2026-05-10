#!/usr/bin/env python3
"""UserPromptSubmit hook: reconcile open-prs.jsonl against live GitHub PR state.

Runs once per session (per-session sentinel in scratch/). For every
sessions/*/open-prs.jsonl in the engineering-journal repo:
  - Calls `gh pr view` for each entry to check current state.
  - Removes entries whose PRs are MERGED or CLOSED (in-place rewrite).
  - Deletes the file when the last entry is removed.

Modified files are left dirty for Claude to pick up in the next stub commit.
Always exits 0 — never blocks.

Stdout: one JSON line with a systemMessage listing surviving open PRs (and any
removals), so Claude has correct context from turn 1 without reading the file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

SCRATCH = Path.home() / ".claude" / "scratch"
JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL_PREFIX = "open-prs-reconciled-"
MAX_AGE_DAYS = 30


def cleanup_stale_sentinels() -> None:
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        for f in SCRATCH.glob(f"{SENTINEL_PREFIX}*.flag"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


def sentinel_path(session_id: str) -> Path:
    return SCRATCH / f"{SENTINEL_PREFIX}{session_id}.flag"


def already_ran(session_id: str) -> bool:
    return sentinel_path(session_id).exists()


def mark_done(session_id: str) -> None:
    try:
        sentinel_path(session_id).write_text("")
    except Exception:
        pass


def repo_from_url(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub PR URL."""
    try:
        parts = urlparse(url).path.strip("/").split("/")
        # expected: ['owner', 'repo', 'pull', 'N']
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return None


def check_pr_state(pr_number: int, repo: str) -> str | None:
    """Return 'OPEN', 'MERGED', or 'CLOSED'; None on any failure."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "state"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("state")
    except Exception:
        return None


def load_entries(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def write_entries(path: Path, entries: list[dict]) -> None:
    if not entries:
        path.unlink(missing_ok=True)
    else:
        path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )


def reconcile_file(path: Path) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Return (surviving_entries, [(removed_entry, state), ...])."""
    entries = load_entries(path)
    if not entries:
        return [], []

    surviving = []
    removed: list[tuple[dict, str]] = []
    for entry in entries:
        url = entry.get("url", "")
        pr_number = entry.get("pr")
        repo = repo_from_url(url)

        if not repo or not pr_number:
            removed.append((entry, "malformed"))
            continue

        state = check_pr_state(pr_number, repo)
        if state in ("MERGED", "CLOSED"):
            removed.append((entry, state))
        else:
            # OPEN, unknown (gh failed), or None — keep the entry
            surviving.append(entry)

    if removed:
        write_entries(path, surviving)

    return surviving, removed


def main() -> None:
    cleanup_stale_sentinels()

    raw = sys.stdin.read().strip()
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            pass

    session_id = data.get("session_id") or f"unknown-{int(time.time())}"

    if already_ran(session_id):
        return

    all_surviving: list[str] = []
    all_removed: list[str] = []

    jsonl_files = sorted(JOURNAL_REPO.glob("sessions/*/open-prs.jsonl"))
    for path in jsonl_files:
        project = path.parent.name
        try:
            surviving, removed = reconcile_file(path)
        except Exception:
            continue

        for entry in surviving:
            all_surviving.append(f"{project}#{entry.get('pr')} ({entry.get('url', '')})")
        for entry, state in removed:
            if state == "malformed":
                all_removed.append(f"{project}: malformed entry (missing pr/url)")
            else:
                all_removed.append(f"{project}#{entry.get('pr')} — {state.lower()}")

    mark_done(session_id)

    parts: list[str] = []
    if all_removed:
        parts.append(
            "Reconciled open-prs.jsonl — removed stale entries: "
            + ", ".join(all_removed)
            + ". Files updated; include open-prs.jsonl in your next stub commit."
        )
    if all_surviving:
        parts.append("Open PRs: " + ", ".join(all_surviving))
    elif not all_removed:
        # nothing to report — no files found or all are empty
        return

    msg = " ".join(parts) if parts else ""
    if msg:
        print(json.dumps({"systemMessage": msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
