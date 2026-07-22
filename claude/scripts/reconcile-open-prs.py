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
scoped `git status --porcelain` and surfaces the exact paths in its systemMessage.

Those paths are reported in four classes, never as one list (ADR-119, dev-env#866):

  - **Deletions whose PR is confirmed MERGED/CLOSED** — post-merge bookkeeping that is safe
    for whichever session finds it to commit, because ADR-056 made each shard a disjoint
    per-PR file: removing one cannot touch another PR's record. Reported with a ready-to-run
    explicit-pathspec `add`/`commit` pair. The old advice ("include these in your next stub
    commit") was unreachable for the many sessions that open no PR and so write no stub,
    which is how four merged-PR deletions sat uncommitted in the canonical for two days.
  - **Deletions for a still-OPEN PR** — an anomaly (someone removed a live record); flagged,
    never recommended for commit.
  - **Deletions whose PR state could not be confirmed** — `gh` failed (offline, or an
    exhausted GraphQL budget, which `gh pr view --json` draws on). Conservative: reported,
    not recommended.
  - **Everything else (added / modified / untracked / renamed)** — a *concurrent* session's
    in-flight shard. Recommending these for this session's pathspec is precisely the clobber
    ADR-056's explicit-pathspec rule exists to prevent, so they are reported as hands-off.

This hook deliberately never commits: it is an advisory UserPromptSubmit hook that must fail
open, it runs in a checkout whose git index every concurrent session shares, and it would be
committing onto whatever branch the canonical happens to hold.

Always exits 0 — never blocks.

Stdout: one JSON line with a systemMessage listing surviving open PRs, any removals, and the
classified dirty open-PR paths sitting uncommitted in the canonical checkout — so Claude has
correct context from turn 1 without reading the files, and an actionable, correctly-scoped
pathspec for the deletions it may safely commit.
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

# Each orphaned deletion costs two subprocesses (a `git show` + a `gh pr view`) inside a
# 30s hook budget. Orphaned deletions are rare (4 was the motivating dev-env#866 case), so
# this is a runaway backstop, not an expected limit — anything past it is reported as
# `skipped` rather than silently dropped.
MAX_DELETION_PROBES = 10


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


def parse_open_pr_status_line(line: str) -> tuple[str, str] | None:
    """`(status, path)` for a porcelain line naming a `sessions/*/open-prs*` path, else None.

    Porcelain format is `XY <path>` (2 status chars + space + path), or `XY <old> -> <new>`
    for a rename — nothing in this hook renames a shard, but a rename from elsewhere is
    handled by keeping just the `<new>` half, the only one that's a real, addable path
    today. Shape match is a shard file (`open-prs/<N>.json`) or the legacy
    `open-prs.jsonl`. Sole line-parsing primitive for both readers below, so the two can
    never drift on what counts as an open-PR path."""
    if len(line) < 4:
        return None
    status = line[:2]
    path = line[3:].strip().replace("\\", "/")
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if "/open-prs/" in path or path.endswith("/open-prs.jsonl"):
        return status, path
    return None


def find_dirty_open_pr_paths(status_lines: list[str]) -> list[str]:
    """Every dirty `sessions/*/open-prs*` path, in `git status` order, whatever its status.
    Surfaces disk state nothing currently commits (see module docstring) — this session's
    own fresh unlinks, or a prior session's never-committed ones. Pure string filter.

    Kept as the unclassified view; `classify_dirty_open_pr_paths` is what the message
    builder uses, because a single undifferentiated list is what made the old advisory
    unsafe (ADR-119)."""
    paths: list[str] = []
    for line in status_lines:
        parsed = parse_open_pr_status_line(line)
        if parsed is not None:
            paths.append(parsed[1])
    return paths


def classify_dirty_open_pr_paths(status_lines: list[str]) -> dict[str, list[str]]:
    """Split dirty open-PR paths into `deleted` vs `other`, preserving `git status` order.

    A *deletion* — `D` in either porcelain column, covering both a staged `D ` and an
    unstaged ` D` — is post-merge bookkeeping over a disjoint per-PR file (ADR-056), so it
    is safe for whichever session finds it to commit once the PR is confirmed merged.
    Anything else (added / modified / untracked / renamed) is a *concurrent* session's
    in-flight shard, and must never be folded into this session's pathspec. Pure string
    filter — the merge confirmation is a separate step (`classify_deletions`)."""
    out: dict[str, list[str]] = {"deleted": [], "other": []}
    for line in status_lines:
        parsed = parse_open_pr_status_line(line)
        if parsed is None:
            continue
        status, path = parsed
        out["deleted" if "D" in status else "other"].append(path)
    return out


def shard_pr_number_from_path(path: str) -> int | None:
    """PR number from a `.../open-prs/<N>.json` path; None for the legacy `open-prs.jsonl`
    or a non-numeric stem — matching `iter_pr_shards`' numeric-stem rule, so a filename no
    reader would enumerate is not silently treated as a tracked PR here either."""
    name = path.rsplit("/", 1)[-1]
    if not name.endswith(".json"):
        return None
    stem = name[: -len(".json")]
    return int(stem) if stem.isdigit() else None


def classify_deletions(
    deleted_paths: list[str],
    url_fn,
    state_fn,
    max_probes: int = MAX_DELETION_PROBES,
) -> dict[str, list[str]]:
    """Confirm each deleted shard's PR state and bucket the paths accordingly.

    The working-tree copy is gone (that *is* the state being classified), so the PR's URL
    comes from the shard as committed at HEAD via `url_fn(path) -> url | None`, and the
    state from `state_fn(pr, repo) -> 'OPEN'|'MERGED'|'CLOSED'|None`. Both are injected so
    this stays offline-testable, matching the reconcilers above.

    Buckets: `merged` (safe to commit), `open` (a live record was deleted — anomaly),
    `unverified` (URL or state unresolvable, e.g. `gh` rate-limited — never recommended),
    and `skipped` (beyond `max_probes`, reported rather than silently dropped, so a capped
    run never reads as full coverage)."""
    out: dict[str, list[str]] = {"merged": [], "open": [], "unverified": [], "skipped": []}
    probes = 0
    for path in deleted_paths:
        pr_number = shard_pr_number_from_path(path)
        if pr_number is None:
            # Legacy open-prs.jsonl (many PRs per file) or an unenumerable name — no single
            # PR to confirm, so it can't be auto-cleared for commit. Costs no probe.
            out["unverified"].append(path)
            continue
        if probes >= max_probes:
            out["skipped"].append(path)
            continue
        probes += 1
        url = url_fn(path)
        repo = repo_from_url(url) if url else None
        state = state_fn(pr_number, repo) if repo else None
        if should_remove(state):
            out["merged"].append(path)
        elif state == "OPEN":
            out["open"].append(path)
        else:
            out["unverified"].append(path)
    return out


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


def committed_shard_url(journal_repo: Path, path: str) -> str | None:
    """The `url` field of a shard as committed at HEAD; None on any failure.

    Read from git rather than disk because the file being classified is precisely one that
    is *deleted* in the working tree — there is nothing left to read there. Not unit-tested:
    subprocess boundary, matching `check_pr_state`'s convention."""
    try:
        result = subprocess.run(
            ["git", "-C", str(journal_repo), "show", f"HEAD:{path}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("url") if isinstance(data, dict) else None
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
    _hookutil.record_heartbeat("reconcile-open-prs")
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
        dirty = classify_dirty_open_pr_paths(dirty_open_pr_status_lines(JOURNAL_REPO))
        deletions = classify_deletions(
            dirty["deleted"],
            url_fn=lambda p: committed_shard_url(JOURNAL_REPO, p),
            state_fn=check_pr_state,
        )
    except Exception:
        dirty = {"deleted": [], "other": []}
        deletions = {"merged": [], "open": [], "unverified": [], "skipped": []}

    parts: list[str] = []
    if all_removed:
        parts.append(
            "Reconciled open-PR tracking — removed stale entries: " + ", ".join(all_removed) + "."
        )
    if all_surviving:
        parts.append("Open PRs: " + ", ".join(all_surviving))
    if deletions["merged"]:
        paths = " ".join(deletions["merged"])
        parts.append(
            "Uncommitted open-PR shard DELETIONS in the canonical checkout whose PRs are "
            "confirmed merged/closed (this session's own unlinks, or an earlier session's "
            "never-committed ones — a session that opens no PR writes no stub, so these do "
            "not self-clear). Commit them now with this exact pathspec, whether or not you "
            f"write a stub (safe: each shard is a disjoint per-PR file, ADR-056): "
            f"git -C {JOURNAL_REPO.as_posix()} add -- {paths} && "
            f'git -C {JOURNAL_REPO.as_posix()} commit -m "journal: close merged open-pr '
            f'shards" -- {paths}'
        )
    if deletions["open"]:
        parts.append(
            "WARNING — deleted open-PR shard(s) for a PR that is still OPEN: "
            + ", ".join(deletions["open"])
            + ". Do NOT commit these; restore them (git checkout -- <path>) or investigate."
        )
    if deletions["unverified"]:
        parts.append(
            "Deleted open-PR shard(s) whose PR state could not be confirmed (gh offline or "
            "rate-limited, or a legacy open-prs.jsonl covering many PRs): "
            + ", ".join(deletions["unverified"])
            + ". Do not commit blind — re-check state first."
        )
    if deletions["skipped"]:
        parts.append(
            f"{len(deletions['skipped'])} further deleted open-PR shard(s) not probed "
            f"(cap {MAX_DELETION_PROBES}): " + ", ".join(deletions["skipped"]) + "."
        )
    if dirty["other"]:
        parts.append(
            "In-flight open-PR shard changes from a concurrent session (added/modified, not "
            "deleted): "
            + ", ".join(dirty["other"])
            + ". Leave these alone — never add another session's shard to your pathspec (ADR-056)."
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
