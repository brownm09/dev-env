#!/usr/bin/env python3
"""UserPromptSubmit hook: reconcile open-PR tracking against live GitHub PR state.

Runs once per session (per-session sentinel in scratch/). For every project under
the engineering-journal repo it reconciles two formats (see ADR-055):

  - Per-PR shards `sessions/<project>/open-prs/<N>.json` (current format) — for each
    shard whose PR is MERGED or CLOSED, the shard file is unlinked individually. No
    surviving shard is ever rewritten, so a concurrent session's shard can never be
    clobbered. The `open-prs/` dir is removed when its last shard is gone.
  - The legacy single file `sessions/<project>/open-prs.jsonl` (pre-ADR-055) — entries
    whose PRs are MERGED or CLOSED are removed via the existing read-filter-write (which
    reads the current on-disk file, so it is safe); the file is deleted when empty.

Both formats are read so the transition needs no forced migration: the legacy file drains
to empty as its PRs merge, and new PRs are tracked only as shards.

Modified files are left dirty for Claude to pick up in the next stub commit.
Always exits 0 — never blocks.

Stdout: one JSON line with a systemMessage listing surviving open PRs (and any
removals), so Claude has correct context from turn 1 without reading the files.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
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


# --- pure helpers (unit-tested in tests/test_reconcile_open_prs.py) ----------


def repo_from_url(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub PR URL."""
    try:
        parts = urlparse(url).path.strip("/").split("/")
        # expected: ['owner', 'repo', 'pull', 'N']
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return None


def should_remove(state: str | None) -> bool:
    """A tracked PR is removed only when GitHub confirms it MERGED or CLOSED.
    OPEN, an unknown state, or None (a gh failure) is conservative — keep it."""
    return state in ("MERGED", "CLOSED")


def shard_pr_number(path: Path) -> int | None:
    """Parse the PR number from an `open-prs/<N>.json` shard filename.
    Returns None for any non-numeric stem so stray files are ignored."""
    try:
        return int(path.stem)
    except (ValueError, TypeError):
        return None


def entry_repo_and_pr(entry: dict) -> tuple[str | None, int | None]:
    """Resolve (owner/repo, pr_number) from a tracking entry, or (None, *)/(*, None)."""
    repo = repo_from_url(entry.get("url", ""))
    pr_number = entry.get("pr")
    if not isinstance(pr_number, int):
        pr_number = None
    return repo, pr_number


def project_dirs(journal_repo: Path) -> list[Path]:
    """Every `sessions/<project>/` directory, sorted; [] if sessions/ is absent."""
    sessions = journal_repo / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(p for p in sessions.iterdir() if p.is_dir())


# --- legacy single-file path -------------------------------------------------


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


def reconcile_file(path: Path, state_fn=None) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Legacy `open-prs.jsonl`: return (surviving_entries, [(removed_entry, state), ...]).
    Rewrites the file in place (safe — derived from the current on-disk contents).
    `state_fn(pr, repo) -> state` is injectable for offline tests; defaults to gh."""
    if state_fn is None:
        state_fn = check_pr_state
    entries = load_entries(path)
    if not entries:
        return [], []

    surviving = []
    removed: list[tuple[dict, str]] = []
    for entry in entries:
        repo, pr_number = entry_repo_and_pr(entry)
        if not repo or not pr_number:
            removed.append((entry, "malformed"))
            continue

        state = state_fn(pr_number, repo)
        if should_remove(state):
            removed.append((entry, state))
        else:
            # OPEN, unknown (gh failed), or None — keep the entry
            surviving.append(entry)

    if removed:
        write_entries(path, surviving)

    return surviving, removed


# --- per-PR shard path (ADR-055) ---------------------------------------------


def reconcile_shard_dir(shard_dir: Path, state_fn=None) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Per-PR shards `open-prs/<N>.json`: return (surviving, [(removed, state), ...]).

    Each merged/closed shard is unlinked on its own — surviving shards are never
    rewritten, so concurrent sessions' shards cannot be clobbered. Unparseable or
    malformed shards are left untouched (conservative). Removes the dir when empty.
    `state_fn(pr, repo) -> state` is injectable for offline tests; defaults to gh.
    """
    if state_fn is None:
        state_fn = check_pr_state
    surviving: list[dict] = []
    removed: list[tuple[dict, str]] = []
    if not shard_dir.is_dir():
        return surviving, removed

    for shard in sorted(shard_dir.glob("*.json")):
        if shard_pr_number(shard) is None:
            continue  # not a PR shard — ignore
        try:
            entry = json.loads(shard.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # leave unparseable shards for a human

        repo, pr_number = entry_repo_and_pr(entry)
        if not repo or not pr_number:
            continue  # leave malformed shards in place

        state = state_fn(pr_number, repo)
        if should_remove(state):
            try:
                shard.unlink()
            except OSError:
                pass
            removed.append((entry, state))
        else:
            surviving.append(entry)

    try:
        if shard_dir.is_dir() and not any(shard_dir.iterdir()):
            shard_dir.rmdir()
    except OSError:
        pass

    return surviving, removed


# --- network boundary (not unit-tested; repo avoids subprocess/urllib mocks) --


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

    for project_dir in project_dirs(JOURNAL_REPO):
        project = project_dir.name

        # Current format: per-PR shards.
        try:
            surviving, removed = reconcile_shard_dir(project_dir / "open-prs")
        except Exception:
            surviving, removed = [], []

        # Legacy format: single open-prs.jsonl (drains as its PRs merge).
        legacy = project_dir / "open-prs.jsonl"
        if legacy.exists():
            try:
                s2, r2 = reconcile_file(legacy)
            except Exception:
                s2, r2 = [], []
            surviving = surviving + s2
            removed = removed + r2

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
            "Reconciled open-PR tracking — removed stale entries: "
            + ", ".join(all_removed)
            + ". Files updated; include the open-PR changes in your next stub commit."
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
