#!/usr/bin/env python3
"""UserPromptSubmit hook: reconcile open-PR tracking against live GitHub PR state.

Runs once per session (per-session sentinel in scratch/). For every project under
the engineering-journal repo it reconciles two formats (see ADR-056):

  - Per-PR shards `sessions/<project>/open-prs/<N>.json` (current format) — for each
    shard whose PR is MERGED or CLOSED, the shard file is unlinked individually. No
    surviving shard is ever rewritten, so a concurrent session's shard can never be
    clobbered. The `open-prs/` dir is removed when its last shard is gone.
  - The legacy single file `sessions/<project>/open-prs.jsonl` (pre-ADR-056) — entries
    whose PRs are MERGED or CLOSED are removed via the existing read-filter-write (which
    reads the current on-disk file, so it is safe); the file is deleted when empty.

Both formats are read so the transition needs no forced migration: the legacy file drains
to empty as its PRs merge, and new PRs are tracked only as shards. The shard enumeration
and legacy-line parsing are delegated to the shared `_journal_shards` reader (ADR-057),
which `post-compact.py` imports too, so the two hooks cannot drift on the shard semantics.

Unlinking/rewriting happens directly in the canonical checkout's working tree and this
hook never commits. That is NOT a "the next stub commit will add it" convenience — ADR-018
claimed that, but it stopped being true once ADR-056 moved stub commits to an explicit
per-file pathspec (naming only the shard(s) *this* session touched) and ADR-082
(dev-env#578) removed `/journal-compose`'s old bulk `git add -u sessions/<project>/`, the
last thing still opportunistically catching a *different* session's dirty unlink. Nothing
commits a stray unlink today; it self-heals to a clean `git status` only once the canonical
next pulls a `main` that already contains an equivalent deletion (e.g. from compose's own
Step 9.5).

The unlink still matters independent of that: `post-compact.py` reads these same shards
straight off disk (no git, no network) to decide whether to remind Claude to `/review` an
open PR — the dependency ADR-018 named as this hook's original rationale. Skipping the
unlink (report-only) would silently regress that reminder's accuracy, so it stays.

To restore ADR-018's "picked up by the next commit" guarantee in a form that fits ADR-056's
sharded shape, this hook also detects any currently-uncommitted `sessions/*/open-prs*`
change (this session's own fresh unlinks, or a prior session's never-committed ones) via a
scoped `git status --porcelain` and surfaces the exact paths in its systemMessage, giving
Claude a ready-to-use pathspec for its next stub commit.

Always exits 0 — never blocks.

Stdout: one JSON line with a systemMessage listing surviving open PRs, any removals, and
any already-dirty open-PR paths sitting uncommitted in the canonical checkout — so Claude
has correct context from turn 1 without reading the files, and an actionable path list for
its next commit.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import _hookutil
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# iter_pr_shards / read_legacy_entries are the shared open-PR readers (ADR-057), also used
# by post-compact.py. iter_pr_shards owns the numeric-filename filtering, so the reconcile
# loop no longer needs shard_pr_number directly.
from _journal_shards import iter_pr_shards, read_legacy_entries

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL_PREFIX = "open-prs-reconciled-"


def already_ran(session_id: str) -> bool:
    return _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).exists()


def mark_done(session_id: str) -> None:
    try:
        _hookutil.SCRATCH.mkdir(exist_ok=True)
        _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).write_text("")
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


def find_dirty_open_pr_paths(status_lines: list[str]) -> list[str]:
    """Filter `git status --porcelain` lines to the `sessions/*/open-prs*` shape: shard
    files (`open-prs/<N>.json`) or the legacy `open-prs.jsonl`, whether added, modified,
    or deleted. Surfaces disk state nothing currently commits (see module docstring) —
    this session's own fresh unlinks, or a prior session's never-committed ones. Pure
    string filter; porcelain format is `XY <path>` (2 status chars + space + path)."""
    paths: list[str] = []
    for line in status_lines:
        if len(line) < 4:
            continue
        path = line[3:].strip().replace("\\", "/")
        if "/open-prs/" in path or path.endswith("/open-prs.jsonl"):
            paths.append(path)
    return paths


# --- legacy single-file path -------------------------------------------------


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
    Lines are parsed by the shared `read_legacy_entries` (ADR-057).
    `state_fn(pr, repo) -> state` is injectable for offline tests; defaults to gh."""
    if state_fn is None:
        state_fn = check_pr_state
    entries = read_legacy_entries(path)
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


# --- per-PR shard path (ADR-056) ---------------------------------------------


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

    # Enumeration/parse is delegated to the shared _journal_shards.iter_pr_shards (the
    # single source of truth shared with post-compact.py): numeric-named *.json, numerically
    # sorted, unparseable/non-object shards skipped. It materialises the list before
    # returning, so unlinking shards while we iterate the result is safe.
    for shard, entry in iter_pr_shards(shard_dir):
        repo, pr_number = entry_repo_and_pr(entry)
        if not repo or not pr_number:
            continue  # leave malformed shards (no resolvable repo/PR) in place

        state = state_fn(pr_number, repo)
        if should_remove(state):
            try:
                shard.unlink()
            except OSError:
                pass
            removed.append((entry, state))
        else:
            surviving.append(entry)

    # Best-effort cleanup of an emptied dir, race-tolerant: if a concurrent session
    # writes a new shard between the iterdir() check and rmdir(), rmdir() raises
    # OSError (dir not empty) and we leave the dir — the new shard is never lost.
    try:
        if shard_dir.is_dir() and not any(shard_dir.iterdir()):
            shard_dir.rmdir()
    except OSError:
        pass

    return surviving, removed


# --- network / git boundary (not unit-tested; repo avoids subprocess/urllib mocks) --


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


def dirty_open_pr_status_lines(journal_repo: Path) -> list[str]:
    """`git status --porcelain -- sessions` in the canonical checkout; [] on any failure
    (missing repo, git not on PATH, timeout). Not unit-tested — subprocess boundary,
    matching `check_pr_state`'s convention."""
    try:
        result = subprocess.run(
            ["git", "-C", str(journal_repo), "status", "--porcelain", "--", "sessions"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()
    except Exception:
        return []


def main() -> None:
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

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

    try:
        dirty_paths = find_dirty_open_pr_paths(dirty_open_pr_status_lines(JOURNAL_REPO))
    except Exception:
        dirty_paths = []

    parts: list[str] = []
    if all_removed:
        parts.append(
            "Reconciled open-PR tracking — removed stale entries: " + ", ".join(all_removed) + "."
        )
    if all_surviving:
        parts.append("Open PRs: " + ", ".join(all_surviving))
    if dirty_paths:
        parts.append(
            "Uncommitted open-PR shard changes on disk in the canonical checkout (this "
            "session's or an earlier session's never-committed reconciliation): "
            + ", ".join(dirty_paths)
            + ". Include these paths in your next stub commit's git add/commit pathspec."
        )
    if not parts:
        # nothing to report — no files found, all empty, and nothing dirty
        return

    print(json.dumps({"systemMessage": " ".join(parts)}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
