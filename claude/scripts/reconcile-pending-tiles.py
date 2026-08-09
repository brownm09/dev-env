#!/usr/bin/env python3
"""UserPromptSubmit hook: re-surface pending tile shards whose chips did not survive.

A `spawn_task` tile is the one-click control for starting a follow-up session, and it is
ephemeral: chip IDs are not persisted across an app restart, and no non-destructive API
reports whether a chip was clicked. ADR-118 makes the *payload* durable by persisting each
tile as a shard `sessions/<project>/tiles/<issue-number>.json`, keyed by the paired GitHub
issue that ADR-094 already requires. PR1 (dev-env#868) landed the shard format, the
`_journal_shards` generalisation, and the `claude/CLAUDE.md` write rule — so shards have
been written but never pruned or surfaced. This hook is the reader that ends that dormancy.

Once per session (per-session sentinel in scratch/, matching `reconcile-open-prs.py`) it
walks every project's `tiles/` directory, reconciles each shard against live GitHub issue
state, unlinks the shards whose issue is CLOSED, and emits a compact **index** of what is
still pending. It surfaces the index, not the payloads: Claude reads a shard for the full
`spawn_task` prompt only when it actually re-spawns one, which keeps turn-1 context small
even when many tiles are pending.

Three things this hook does deliberately differently from its `reconcile-open-prs.py`
model, each load-bearing:

1. **Output goes to stdout at exit 0, as model-visible `additionalContext`.** On
   UserPromptSubmit that is the channel Claude actually sees; exit-0 stderr reaches no one
   there. Routing an advisory to the wrong stream is silent by construction — the ADR-098
   bug class that hid `dev-env-sync.py`'s warnings for months — so the emission goes
   through `_hookout`, which encodes the per-event contract once. Note this is a different
   audience from `reconcile-open-prs.py`'s raw `{"systemMessage"}`, which is the *user*
   toast channel: the tile index exists for Claude to act on, so it must reach the model.

2. **`url` is validated before it can reach `argv`.** The shard's `url` is git-committed,
   cross-machine, and (until dev-env#870 adds write-time validation) unchecked, yet it is
   what a naive reader would parse to derive `--repo`. The open-PR precedent splits the URL
   path with no host or character check, which is tolerable there but not here: this
   reconciler's remove branch **unlinks the shard**, so a mis-resolved lookup that comes
   back CLOSED destroys the payload — and `gh --repo` also accepts a `HOST/OWNER/REPO`
   form, so a crafted path can aim the lookup at another host carrying the user's
   credentials. Hence: host must be github.com, owner/repo must match a conservative
   character class, the issue number comes from the **filename** (never the URL), and any
   failed check **skips and keeps** the shard rather than unlinking it. See
   `repo_from_issue_url` and ADR-118's "The reader must validate `url`" paragraph.

3. **One lookup per REPO, not per shard — over REST, not GraphQL.** Two separate costs are
   avoided here. *Shape:* shards accumulate un-pruned between reconciliations, so a per-shard
   `gh issue view` would pay N sequential subprocess spawns; `lookup_states` instead issues one
   paged lookup per distinct repo, so cost scales with repo count rather than shard count. This
   supersedes ADR-118's Consequences note, which assumed one `gh issue view` per pending tile.
   *Transport:* `gh issue list` is a GraphQL call, and this repo has repeated, measured GraphQL
   exhaustion (dev-env#769/#773, again during PR #872, and again during this hook's own
   implementation session — `graphql 0/5000` while REST `core` sat at `4999/5000`). An exhausted
   bucket failed every lookup, so nothing was ever pruned. `fetch_repo_issue_states` therefore
   reads `GET /repos/{owner}/{repo}/issues` over REST, whose `core` bucket is 5000/hr and
   near-untouched — and which is *not* what Projects v2 operations contend for, since those have
   no REST alternative at all. REST-only, with no GraphQL fallback: one code path, and a `core`
   failure almost always means auth/network is down, which GraphQL would not survive either.
   See dev-env#882 and ADR-118 Amendment 3.

The REST transport described above — `repo_from_issue_url`, `issue_states_from_rows`,
`should_stop_paging`, `fetch_repo_issue_states`, `check_issue_state`, and the constants
that size them — now lives in `_gh_issue_state.py` (dev-env#967, ADR-131), extracted so a
second consumer (`retro-chain-status.py`, the read-only classifier behind the chained-tile
retro-action backlog refill mechanism) can reuse the same hardened logic rather than
duplicating a second, drift-prone copy of the same two REST hazards. This file re-imports
every name unchanged, so every call site and every existing test below is unaffected —
`should_remove_tile` is the one exception kept local: a thin, tile-vocabulary wrapper
around the shared `is_closed`, since "a tile shard is removed" is this hook's own framing,
not the shared module's.

Conservative on every uncertainty, mirroring `should_remove`: only a confirmed CLOSED
unlinks. OPEN, an unknown state, a `gh` failure, an issue outside the lookup window, a
failed URL validation, and a filename/`issue`-field disagreement all **keep** the shard.
Losing a payload is unrecoverable; keeping a stale one costs a line of index text.

Since dev-env#958/#950 (ADR-118 Amendment 5), this hook also restores the "picked up by
the next commit" guarantee ADR-018 promised for open-PR shards, in the shape ADR-119
actually built: a scoped `git status --porcelain` over `sessions/*/tiles/*` surfaces any
tile-shard path already deleted from the working tree but not yet committed (this
session's own fresh unlinks above, or a prior session's never-committed ones — a session
that spawns no tile writes no stub, so these do not self-clear).

Those paths are reported in four classes, never as one list, mirroring
`reconcile-open-prs.py`'s ADR-119 model exactly (see that module's docstring for the full
rationale — porcelain codes, mid-merge suppression, the hook-never-commits contract — all
reused unchanged here):

  - **Deletions whose issue is confirmed CLOSED** — safe for whichever session finds it to
    commit, because ADR-118 made each shard a disjoint per-issue file. Reported with a
    ready-to-run explicit-pathspec `add`/`commit` pair.
  - **Deletions for a still-OPEN issue** — an anomaly; flagged, never recommended.
  - **Deletions whose identity or state could not be confirmed** — `git show`/`gh` failed,
    or the shard's embedded `issue` disagrees with its filename stem.
  - **Everything else** (added/modified/untracked/renamed/unmerged) — a concurrent
    session's in-flight shard; reported as hands-off.

One deliberate departure from the open-PR model: issue-state re-confirmation is **one `gh
api repos/<repo>/issues/<n>` call per deletion candidate, never batched** through
`fetch_repo_issue_states` below. That batch is a *recency-windowed* view (2 pages / 100
issues, newest-created-first) — sound for the primary loop because a pending tile's issue
is recent by construction, but not for an orphaned deletion: while a deletion sits
uncommitted, the repo keeps creating issues, and every one created pushes an
already-resolved issue one position deeper in that window. A deletion candidate
resolvable via the batch at the moment of its original unlink can silently fall outside
it by the time this pass re-confirms it — resolving to "unverified" forever for exactly
the oldest, most-needing-cleanup orphans. See `check_issue_state`.

**This session's own fresh unlinks are pre-seeded as closed, never re-probed**
(`partition_known_closed`). `reconcile_tiles` above already confirmed CLOSED for every
tile it just unlinked, so re-deriving that over `git show` + `gh api` would waste probe
budget the genuine cross-session orphans need — and since a session's own unlinks are the
common case (an issue closes, the primary loop unlinks it, and the very same run then
finds it in `git status`), skipping the redundant work matters in practice, not just in
theory.

**The whole deletion-advisory pass is skipped once too little of `HOOK_TIMEOUT_SECONDS`
plausibly remains** (`deletion_advisory_time_remains`), rather than assuming any fixed
amount of slack. The primary loop's own real worst case is *not* `LOOKUP_BUDGET_SECONDS +
GH_CALL_TIMEOUT`: `fetch_repo_issue_states` carries no deadline of its own once a repo's
fetch has started (`lookup_states` gates only between repos), so one repo already past the
gate can run for the full `MAX_ISSUE_PAGES * GH_CALL_TIMEOUT` — which already exceeds
`HOOK_TIMEOUT_SECONDS` on its own (an earlier draft of this paragraph, and of the test
that pinned it, used the wrong term here and asserted a "zero slack" equality that
disagreed with `test_page_budget_cannot_outrun_the_lookup_budget` in the same test file;
/review caught the contradiction). There is no fixed budget left to "borrow" from safely,
so this pass reacts to actual elapsed time instead of assuming a number.

Always exits 0 — never blocks.

Stdout: one JSON line whose `additionalContext` carries the pending-tile index, any
prunes, an explicit count of anything unresolved or skipped, and the classified dirty
tile-shard deletions sitting uncommitted in the canonical checkout — so a truncated or
partial reconciliation is never reported as a clean one, and Claude has an actionable,
correctly-scoped pathspec for the deletions it may safely commit.
"""
from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import _hookout
import _hookutil
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# iter_tile_shards / shard_number are the shared numeric-shard readers (ADR-057, generalised
# by ADR-118). iter_tile_shards owns the numeric-filename filtering, tolerant parse, and
# numeric sort, and materialises its list before returning — so unlinking shards while
# iterating its result is safe. project_dirs is the sessions/<project>/ walk that precedes
# them; this file held the third copy of it until dev-env#881 hoisted it into the module.
from _journal_shards import iter_tile_shards, project_dirs, shard_number

# The REST issue/PR state transport (dev-env#967, ADR-131) — see the module docstring's
# note after point 3 above for what moved and why. Re-imported here, unchanged, so every
# call site below (and every existing test in tests/test_reconcile_pending_tiles.py) keeps
# working against these exact names.
from _gh_issue_state import (
    ISSUE_PAGE_SIZE,
    MAX_ISSUE_PAGES,
    GH_CALL_TIMEOUT,
    GH_ITEM_CALL_TIMEOUT,
    _ISSUE_PROJECTION,
    check_issue_state,
    fetch_repo_issue_states,
    is_closed,
    issue_states_from_rows,
    repo_from_issue_url,
    should_stop_paging,
)

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL_PREFIX = "pending-tiles-reconciled-"

# Index lines shown before truncating. The full total and the dropped count are always
# stated (see format_message) — a cap that hid its own truncation would read as "these are
# all the pending tiles", which is exactly the wrong thing to tell Claude.
MAX_SHOWN = 10

# `ISSUE_PAGE_SIZE`, `MAX_ISSUE_PAGES`, and `GH_CALL_TIMEOUT` (imported above from
# `_gh_issue_state.py`, dev-env#967/ADR-131) size `fetch_repo_issue_states`'s pagination.
# `GET /issues` returns newest-first, and that function stops as soon as every requested
# number is resolved or it has paged past them — so the cap only binds when a tile's issue
# is far older than the newest ~200 issue/PR numbers, which a pending tile is not, by
# construction. A shard whose issue falls outside the paged window resolves to None -> kept
# and counted as unresolved, never silently dropped. Note the window is narrower in *issue*
# terms than the row count suggests, because REST models PRs as issues and those rows
# consume the page too (see `issue_states_from_rows`).
#
# `LOOKUP_BUDGET_SECONDS` below is this hook's own total wall-clock budget for all lookups,
# sized against the fact that this runs on UserPromptSubmit, so every second spent here is
# a second the user's first prompt of the session is stalled. Batching plus early exit makes
# the realistic cost one page (~0.5-1s) per repo with tiles, so the budget only binds when
# `gh` is hanging — and stopping there degrades to "some tiles unresolved, all kept, and
# said so" rather than letting the hook be killed mid-flight and lose the whole index,
# prunes included.
#
# The two are deliberately balanced so the worst case needs no extra bookkeeping inside the page
# loop: MAX_ISSUE_PAGES * GH_CALL_TIMEOUT == LOOKUP_BUDGET_SECONDS, so one hanging repo exhausts
# the budget exactly and `lookup_states` skips every remaining repo. Strictly better than the
# pre-REST 15s single-call pairing, where a 15s hang left elapsed 15 < 20 and a second repo's 15s
# call still started (~30s worst case). Still at/below `reconcile-open-prs.py`'s 30s settings
# timeout, which does strictly more sequential `gh` work.
LOOKUP_BUDGET_SECONDS = 20.0

# Titles are truncated in the index only; the shard keeps the full value.
MAX_TITLE_CHARS = 60

# --- deletion advisory constants (dev-env#958, dev-env#950, ADR-118 Amendment 5) -----

# A staged (`D `) or unstaged (` D`) delete, and nothing else. Exact-match set, never a
# `"D" in status` substring test: the two-char porcelain field puts a `D` in codes that are
# NOT post-closure bookkeeping — `AD`/`RD`/`MD`/`CD`/`TD` (a concurrent session's own staged
# or modified shard) and the unmerged `DD`/`DU`/`UD`, where a recommended `git add` would
# silently resolve a conflict. Mirrors `reconcile-open-prs.py`'s `DELETED_STATUS_CODES`
# (dev-env#873).
DELETED_STATUS_CODES = frozenset({" D", "D "})

# A shard path safe to interpolate into a ready-to-run shell command — anchored at both ends
# (`\Z`, not `$`, so a trailing newline cannot sneak a path past the check) and ASCII-digit-
# only (`[0-9]+`, not `\d`, which also accepts non-ASCII digit characters that `int()` — and
# so `shard_number` — would parse too). Deliberately narrower than the shape check used for
# reporting. Mirrors `reconcile-open-prs.py`'s `SAFE_SHARD_PATH_RE`.
SAFE_TILE_PATH_RE = re.compile(r"^sessions/[A-Za-z0-9._-]+/tiles/[0-9]+\.json\Z")

# Probing is bounded by BOTH a wall clock and a count, because the count alone cannot bound
# latency. The deadline is the real guard; the count is a secondary runaway backstop.
# Anything past either limit is reported as `skipped`, never silently dropped.
MAX_TILE_DELETION_PROBES = 10
TILE_PROBE_DEADLINE_SECONDS = 5.0

# Local git plumbing only (show / status / branch / rev-parse) — measured at ~0.07s on
# `reconcile-open-prs.py`'s identical calls, hence the small timeout.
GIT_CALL_TIMEOUT = 5

# Single-issue REST GET (`repos/<repo>/issues/<n>`), for the deletion-probe path only —
# kept distinct from, and tighter than, the batched lookup's per-PAGE `GH_CALL_TIMEOUT`
# below. A single-object GET is a much lighter call than a paged list, so there is no reason
# to grant it the same allowance, and a smaller timeout bounds each probe's own worst case
# tighter.
GH_ITEM_CALL_TIMEOUT = 5

# `HOOK_TIMEOUT_SECONDS` names what was previously only a `claude/settings.json` comment.
# The primary lookup loop's OWN worst case is NOT `LOOKUP_BUDGET_SECONDS + GH_CALL_TIMEOUT`:
# `lookup_states` only gates the *start* of each repo's fetch against the budget
# (gate-the-start, not preempt-in-flight), so a repo whose fetch starts a moment before the
# budget expires can still run its own full `MAX_ISSUE_PAGES * GH_CALL_TIMEOUT` (20s) to
# completion — pushing the realistic worst case to `LOOKUP_BUDGET_SECONDS + MAX_ISSUE_PAGES *
# GH_CALL_TIMEOUT` (40s), which already exceeds `HOOK_TIMEOUT_SECONDS` (30s) on its own, with
# the process's own external timeout the only thing that ultimately stops it (see
# `test_page_budget_cannot_outrun_the_lookup_budget`). There is therefore no fixed,
# pre-existing slack the deletion-advisory pass below can safely assume and "borrow" from —
# it instead reacts to how much of HOOK_TIMEOUT_SECONDS actually remains once the primary
# loop returns (`deletion_advisory_time_remains`), rather than adding a fixed deadline on top
# of an assumed budget. (An earlier draft of this comment, and of the test that pinned it,
# asserted the wrong equality here; /review caught the contradiction against
# `test_page_budget_cannot_outrun_the_lookup_budget` in the same test file.)
HOOK_TIMEOUT_SECONDS = 30  # mirrors this hook's `timeout` in claude/settings.json

# Minimum slack against HOOK_TIMEOUT_SECONDS required before the deletion-advisory pass is
# even attempted, once the primary lookup above has already run. Covers the pass's fixed
# git-plumbing overhead — one `git status` scan, always, plus one `git branch
# --show-current`, only when there is a closed bucket to recommend committing — at
# GIT_CALL_TIMEOUT each. Deliberately excludes per-item probe cost (git show + gh api),
# which is bounded separately and locally once probing is under way (see `main()`'s
# `deadline_fn`). Below this floor, skip the whole pass rather than start git plumbing that
# has nowhere left to finish.
DELETION_ADVISORY_MIN_REMAINING_SECONDS = 2 * GIT_CALL_TIMEOUT


def already_ran(session_id: str) -> bool:
    return _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).exists()


def mark_done(session_id: str) -> None:
    try:
        _hookutil.SCRATCH.mkdir(exist_ok=True)
        _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).write_text("")
    except Exception:
        pass


# --- pure helpers (unit-tested in tests/test_reconcile_pending_tiles.py) ------


def should_remove_tile(state) -> bool:
    """A tile shard is removed only when GitHub confirms its paired issue is CLOSED.

    Closing the issue is the tile's completion signal (ADR-118). OPEN, an unknown state, or
    None (a `gh` failure, or an issue outside the lookup window) is conservative — keep.
    Mirrors `reconcile-open-prs.py`'s `should_remove`, minus MERGED (issues never merge).

    Thin delegation to `_gh_issue_state.is_closed` (dev-env#967, ADR-131) — the actual
    case-sensitive "CLOSED"-only comparison, and the reasoning for why it must stay
    case-sensitive (the REST migration's silent-inertness hazard), now live there so a
    second consumer (`retro-chain-status.py`) shares the same check rather than
    reintroducing a second, drift-prone copy of it.
    """
    return is_closed(state)


def make_tile(project: str, shard: Path, entry: dict) -> dict:
    """Build one reconciliation record from a parsed shard.

    Resolves the repo and issue number up front and records *why* a shard is unusable in
    ``skip`` — a non-None ``skip`` means the record is reported but never unlinked, so
    every validation failure converges on keep-the-payload.

    The issue number comes from the **filename**, per ADR-118. A shard whose ``issue``
    field contradicts its filename is treated as corrupt and skipped rather than resolved
    in favour of either: the disagreement itself is evidence the shard cannot be trusted to
    drive a destructive unlink.
    """
    issue = shard_number(shard)
    repo = repo_from_issue_url(entry.get("url"))
    skip = None
    if issue is None:
        skip = "non-numeric shard filename"
    elif repo is None:
        skip = "url failed validation"
    else:
        claimed = entry.get("issue")
        if isinstance(claimed, int) and claimed != issue:
            skip = f"`issue` field ({claimed}) disagrees with filename ({issue})"
    return {
        "project": project,
        "shard": shard,
        "entry": entry,
        "issue": issue,
        "repo": repo,
        "skip": skip,
    }


def load_tiles(journal_repo: Path) -> list[dict]:
    """Every tile shard under `sessions/*/tiles/`, as reconciliation records.

    Enumeration and tolerant parsing are delegated to the shared `iter_tile_shards`
    (ADR-057/ADR-118): non-numeric stems, unparseable JSON, and non-object shards are
    skipped there and are never seen — or deleted — here.
    """
    tiles: list[dict] = []
    for project_dir in project_dirs(journal_repo):
        for shard, entry in iter_tile_shards(project_dir / "tiles"):
            tiles.append(make_tile(project_dir.name, shard, entry))
    return tiles


def group_numbers_by_repo(tiles: list[dict]) -> dict[str, list[int]]:
    """`{repo: [issue numbers]}` for the tiles worth looking up — the batching key.

    Skipped tiles are excluded: their repo never made it through validation, so including
    them would be the very thing the validation exists to prevent. Numbers are deduped and
    sorted so the lookup plan is deterministic (and testable).
    """
    by_repo: dict[str, list[int]] = {}
    for tile in tiles:
        if tile["skip"] or not tile["repo"] or tile["issue"] is None:
            continue
        by_repo.setdefault(tile["repo"], []).append(tile["issue"])
    return {repo: sorted(set(nums)) for repo, nums in sorted(by_repo.items())}


def lookup_states(by_repo: dict[str, list[int]], fetch=None, budget: float = LOOKUP_BUDGET_SECONDS,
                  clock=time.monotonic) -> dict[tuple[str, int], str | None]:
    """Resolve `{(repo, issue): state|None}` with ONE fetch per repo.

    This is the cold-start guard described in the module docstring: cost is O(repos), not
    O(shards), so the first prompt after a long dormant window does not fan out into one
    subprocess per accumulated tile.

    A repo whose fetch fails, or that is reached after the wall-clock *budget* is spent,
    yields None for all of its numbers — every one of which is conservatively kept by
    `should_remove_tile` and counted as unresolved in the emitted message. Same for an
    issue the fetch simply did not return (older than the page window, transferred, or
    deleted): absence is never read as "closed".

    The repo's requested numbers are handed to `fetch` as well as used to index its result:
    knowing what it is looking for is what lets the REST transport stop after the first page
    instead of walking its whole cap (`should_stop_paging`).

    `fetch` and `clock` are injectable so the batching, budget, and None-fallback logic
    unit-test offline; the default `fetch` is the live `gh` boundary.
    """
    if fetch is None:
        fetch = fetch_repo_issue_states
    states: dict[tuple[str, int], str | None] = {}
    started = clock()
    for repo, numbers in by_repo.items():
        repo_states = None
        if clock() - started < budget:
            repo_states = fetch(repo, numbers)
        if not isinstance(repo_states, dict):
            repo_states = {}
        for number in numbers:
            states[(repo, number)] = repo_states.get(number)
    return states


def reconcile_tiles(tiles: list[dict], states: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Return `(pending, removed, skipped)`, unlinking each CLOSED tile's shard.

    Every removal is a per-file `unlink` — no surviving shard is ever rewritten, so a
    concurrent session's shard cannot be clobbered (the ADR-056 structural guarantee that
    the open-PR shards rely on, inherited here unchanged).
    """
    pending: list[dict] = []
    removed: list[dict] = []
    skipped: list[dict] = []
    for tile in tiles:
        if tile["skip"]:
            skipped.append(tile)
            continue
        if should_remove_tile(states.get((tile["repo"], tile["issue"]))):
            try:
                tile["shard"].unlink()
            except OSError:
                pass
            removed.append(tile)
        else:
            pending.append(tile)
    return pending, removed, skipped


def prune_empty_tile_dirs(removed: list[dict]) -> None:
    """Best-effort `rmdir` of any `tiles/` directory this run emptied.

    Scoped to directories we actually removed a shard from, so a `tiles/` dir another
    session just created but has not populated yet is never touched. Race-tolerant: if a
    concurrent session writes a shard between the `iterdir()` check and the `rmdir()`, the
    `rmdir()` raises OSError and the directory is left — the new shard is never lost.
    (`tiles/` is recreated by the documented `mkdir -p` in the write recipe, so removing it
    is safe rather than a one-way door.)
    """
    for tile_dir in {tile["shard"].parent for tile in removed}:
        try:
            if tile_dir.is_dir() and not any(tile_dir.iterdir()):
                tile_dir.rmdir()
        except OSError:
            pass


def shard_rel_path(tile: dict) -> str:
    """`sessions/<project>/tiles/<N>.json` — the path Claude reads to re-spawn."""
    return f"sessions/{tile['project']}/tiles/{tile['issue']}.json"


def tile_index_line(tile: dict) -> str:
    """One index row: project, issue, title, spawned date, shard path.

    Deliberately not the payload — `prompt`, `tldr`, and `cwd` stay on disk until Claude
    actually re-spawns the tile, so a session with many pending tiles pays a few lines of
    turn-1 context rather than a few thousand tokens of prompts.
    """
    entry = tile["entry"]
    title = str(entry.get("title") or "(no title)")
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 3] + "..."
    spawned = str(entry.get("spawned") or "unknown")
    return f'  {tile["project"]} #{tile["issue"]} "{title}" (spawned {spawned}) -- {shard_rel_path(tile)}'


def format_message(pending: list[dict], removed: list[dict], skipped: list[dict],
                   unresolved: int = 0, max_shown: int = MAX_SHOWN) -> str:
    """Render the advisory, or "" when there is nothing worth saying.

    Every count is stated even when the list is capped: the header carries the true total
    and a capped list says exactly how many rows were withheld. A cap that reported only
    what it showed would read as a complete inventory of pending tiles, which is the one
    misreading that costs a lost follow-up.
    """
    parts: list[str] = []

    if pending:
        parts.append(
            f"Pending tiles ({len(pending)}) -- spawn_task chips do not survive an app restart; "
            "these shards are the durable payload (ADR-118)."
        )
        parts.extend(tile_index_line(tile) for tile in pending[:max_shown])
        if len(pending) > max_shown:
            hidden = len(pending) - max_shown
            parts.append(
                f"  ... and {hidden} more not shown ({max_shown} of {len(pending)} listed) -- "
                "the rest are in the same sessions/<project>/tiles/ directories."
            )
        parts.append(
            "Before re-spawning any of these, check list_sessions for a session whose title or "
            "branch already matches: a tile whose work has started keeps its issue open, so it "
            "still appears here. Read a shard only when you actually re-spawn one -- the full "
            "spawn_task payload (title/tldr/prompt/cwd) is inside it."
        )

    if removed:
        listed = ", ".join(f"{t['project']} #{t['issue']}" for t in removed)
        parts.append(f"Pruned {len(removed)} tile shard(s) whose issue is now closed: {listed}.")

    if unresolved:
        parts.append(
            f"{unresolved} pending tile(s) could not be resolved against GitHub (gh failure, "
            "lookup budget spent, or issue outside the lookup window) -- kept unchanged, so this "
            "list may include already-finished tiles."
        )

    if skipped:
        listed = ", ".join(f"{shard_rel_path(t)} ({t['skip']})" for t in skipped)
        parts.append(
            f"{len(skipped)} tile shard(s) skipped and kept -- not reconciled, never deleted: {listed}."
        )

    return "\n".join(parts)


# --- deletion advisory (dev-env#958, dev-env#950, ADR-118 Amendment 5) --------


def parse_tile_status_line(line: str) -> tuple[str, str] | None:
    """`(status, path)` for a porcelain line naming a `sessions/*/tiles/*` path, else None.

    Porcelain format is `XY <path>` (2 status chars + space + path), or `XY <old> -> <new>`
    for a rename — nothing in this hook renames a shard, but a rename from elsewhere is
    handled by keeping just the `<new>` half, the only one that's a real, addable path
    today. Mirrors `reconcile-open-prs.py`'s `parse_open_pr_status_line`, minus the
    legacy-single-file branch: tile shards have no legacy `.jsonl` equivalent.
    """
    if len(line) < 4:
        return None
    status = line[:2]
    path = line[3:].strip().replace("\\", "/")
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    if "/tiles/" in path:
        return status, path
    return None


def classify_dirty_tile_paths(status_lines: list[str]) -> dict[str, list[str]]:
    """Split dirty tile paths into `deleted` vs `other`, preserving `git status` order.

    A *deletion* (exactly `D ` or ` D` — see `DELETED_STATUS_CODES`) is post-closure
    bookkeeping over a disjoint per-issue file (ADR-118), so it is safe for whichever
    session finds it to commit once the issue is confirmed closed. Everything else —
    added, modified, untracked, renamed, or any unmerged/conflicted code — is a
    *concurrent* session's in-flight shard or a conflict, and must never be folded into
    this session's pathspec. Pure string filter; the closure confirmation is a separate
    step (`classify_tile_deletions`). Mirrors `classify_dirty_open_pr_paths`.
    """
    out: dict[str, list[str]] = {"deleted": [], "other": []}
    for line in status_lines:
        parsed = parse_tile_status_line(line)
        if parsed is None:
            continue
        status, path = parsed
        out["deleted" if status in DELETED_STATUS_CODES else "other"].append(path)
    return out


def shard_issue_number_from_path(path: str) -> int | None:
    """Issue number from a `.../tiles/<N>.json` path; None for a non-numeric stem.

    String-taking adapter over the shared `shard_number` reader, mirroring
    `reconcile-open-prs.py`'s `shard_pr_number_from_path` — a raw path from `git status`
    needs its own `.json`-suffix gate, since the shared reader's other callers apply it via
    `iter_tile_shards`'s glob first.
    """
    if not path.endswith(".json"):
        return None
    return shard_number(Path(path))


def classify_tile_deletions(
    deleted_paths: list[str],
    identity_fn,
    state_fn,
    max_probes: int = MAX_TILE_DELETION_PROBES,
    deadline_fn=None,
) -> dict[str, list[str]]:
    """Confirm each deleted shard's issue state and bucket the paths accordingly.

    The working-tree copy is gone (that *is* the state being classified), so the shard's
    identity comes from HEAD via `identity_fn(path) -> (url, issue) | None`, and the state
    from `state_fn(issue, repo) -> 'OPEN'|'CLOSED'|None`. Both are injected so this stays
    offline-testable, matching `classify_deletions`.

    `repo` is derived from the recovered `url` via the same strict `repo_from_issue_url`
    the primary loop above uses — never a raw split — since a mis-resolved repo here would
    aim a real GitHub lookup, and potentially a recommended commit, at the wrong repository
    (the exact credential-redirect hazard `repo_from_issue_url`'s docstring describes).

    The shard's *embedded* `issue` field is cross-checked against its filename stem, but a
    *missing* field is not itself a mismatch: `make_tile` already extends that same
    tolerance to on-disk shards (trust the filename when nothing contradicts it), and a
    stricter rule here would treat every shard written before that field existed as
    unverified for no reason. Only a *present and disagreeing* `issue` field routes to
    unverified — the disagreement itself is what makes the shard untrustworthy, not the
    field's absence.

    `deadline_fn() -> bool` returns True once the probe budget is spent; probing stops and
    the remainder is reported as `skipped`. Injected (default: never expired) so tests stay
    pure.

    Buckets: `closed` (confirmed closed — safe to commit), `open` (a live record was
    deleted — anomaly), `unverified` (identity or state unresolvable, or a filename/`issue`
    mismatch — never recommended), and `skipped` (past the count or time budget, reported
    rather than silently dropped, so a capped run never reads as full coverage). Mirrors
    `classify_deletions`, renamed for tiles' single terminal state (an issue closes; it
    does not merge).
    """
    out: dict[str, list[str]] = {"closed": [], "open": [], "unverified": [], "skipped": []}
    probes = 0
    for path in deleted_paths:
        issue = shard_issue_number_from_path(path)
        if issue is None:
            # An unenumerable name — no single issue to confirm, so it can't be
            # auto-cleared for commit. Costs no probe.
            out["unverified"].append(path)
            continue
        if probes >= max_probes or (deadline_fn is not None and deadline_fn()):
            out["skipped"].append(path)
            continue
        probes += 1
        resolved = identity_fn(path)
        url, embedded_issue = resolved if resolved else (None, None)
        repo = repo_from_issue_url(url) if url else None
        if not repo or (isinstance(embedded_issue, int) and embedded_issue != issue):
            # No resolvable repo, or the shard's own `issue` disagrees with its filename —
            # in either case we do not know which issue this record belongs to. A missing
            # embedded `issue` is NOT treated as a mismatch (see docstring above).
            out["unverified"].append(path)
            continue
        state = state_fn(issue, repo)
        if should_remove_tile(state):
            out["closed"].append(path)
        elif state == "OPEN":
            out["open"].append(path)
        else:
            out["unverified"].append(path)
    return out


def safe_for_command(paths: list[str]) -> bool:
    """True when every path is safe to interpolate into a ready-to-run shell command."""
    return all(SAFE_TILE_PATH_RE.match(p) for p in paths)


def cap(paths: list[str], limit: int = 5) -> str:
    """Render a path list for the advisory, bounded. `(+N more)` keeps the count honest —
    the message is injected into Claude's context on the first prompt of every session, so
    an unbounded list is a cost paid every session. Mirrors `reconcile-open-prs.py`'s `cap`.
    """
    shown = ", ".join(paths[:limit])
    return shown + (f" (+{len(paths) - limit} more)" if len(paths) > limit else "")


def deletion_advisory_time_remains(
    elapsed_since_lookup: float,
    hook_timeout: float = HOOK_TIMEOUT_SECONDS,
    min_remaining: float = DELETION_ADVISORY_MIN_REMAINING_SECONDS,
) -> bool:
    """True when enough of `hook_timeout` plausibly remains, given `elapsed_since_lookup`
    seconds already spent in the primary lookup loop, to attempt the deletion-advisory pass
    at all.

    Reacts to actual elapsed time rather than assuming a fixed, pre-existing slack — see the
    HOOK_TIMEOUT_SECONDS comment for why the primary loop's own worst case can already exceed
    `hook_timeout` on its own, leaving nothing fixed to safely "borrow" from. A `False` here
    skips the WHOLE deletion-advisory pass in `main()` — both the `git status` scan and any
    probing — because reporting only the already-built primary pending-tile message is
    strictly better than starting git plumbing that has nowhere left to finish before the
    hook's own external timeout kills the process mid-flight, losing that primary message
    too.
    """
    return (hook_timeout - elapsed_since_lookup) >= min_remaining


def partition_known_closed(deleted_paths: list[str], removed: list[dict]) -> tuple[list[str], list[str]]:
    """Split `deleted_paths` into `(needs_probing, pre_confirmed_closed)`.

    `removed` is this session's own primary-loop unlink list (`reconcile_tiles`'s second
    return value) — each already confirmed CLOSED by the batched lookup above, in this same
    run. When one of those fresh unlinks shows up in this same run's `git status` scan (the
    common case: an issue closes, the primary loop unlinks its shard, and the very same run's
    deletion-advisory pass then finds that path dirty), re-deriving what this session already
    knows over `git show HEAD:<path>` plus a fresh `gh api` call would waste probe budget the
    genuine cross-session orphans need, for a fact already established with certainty moments
    earlier in the same process.

    Matching is by shard path, built from each `removed` record the same way `shard_rel_path`
    does — a plain set-membership test, not a second identity resolution.
    """
    pre_confirmed = {shard_rel_path(tile) for tile in removed}
    needs_probe = [p for p in deleted_paths if p not in pre_confirmed]
    already_closed = [p for p in deleted_paths if p in pre_confirmed]
    return needs_probe, already_closed


# A branch name safe to interpolate, unquoted, into the ready-to-run advisory command's
# prose (the `git -C ... commit` recommendation names the current branch for the "only
# commit if this is today's draft branch" caveat). `current_branch` reads this from live
# `git branch --show-current` output, which is refspec-constrained but NOT shell-metachar-
# constrained — git's own ref-naming rules permit `~^:?*[\`, spaces, and other characters a
# shell parses specially (`git-check-ref-format`(1)). Conservative allowlist, mirroring
# `_OWNER_REPO_RE`'s posture: fail closed to a placeholder rather than interpolate anything
# surprising into text a session may copy-paste and run verbatim.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _safe_branch_label(branch: str | None) -> str:
    """`branch` if safe to interpolate into advisory prose, else a placeholder.

    `None` (detached HEAD, or `current_branch` itself failed) renders as `DETACHED`,
    matching this module's pre-existing wording. A non-None branch that fails the safe-
    character check renders as a distinct placeholder rather than being interpolated
    unsafely, or silently mislabeled as `DETACHED` — which would be factually wrong and
    could mask a real branch-name anomaly worth a human's attention.
    """
    if branch is None:
        return "DETACHED"
    if _SAFE_BRANCH_RE.match(branch):
        return branch
    return "UNSAFE-BRANCH-NAME (verify manually)"


def format_deletion_message(
    dirty: dict, deletions: dict, branch: str | None, mid_merge: bool = False
) -> str:
    """Render the deletion-advisory text, or "" when there is nothing worth saying.

    A single pure formatter for every case (all four `deletions` buckets, the hands-off
    `other` class, and the mid-merge suppression) — matches this file's own `format_message`
    convention rather than `reconcile-open-prs.py`'s inline-in-`main()` shape, so every
    branch stays independently unit-testable.

    Mid-merge suppresses only the deletion-*classification* text (closed/open/unverified/
    skipped), since that is the part a partial `git commit` or a conflict-resolving `git
    add` would make unsafe — `dirty["other"]` needs no git mutation to report and is still
    surfaced, mirroring `reconcile-open-prs.py`'s `main()`, which reports `dirty["other"]`
    unconditionally regardless of merge state.
    """
    parts: list[str] = []

    if mid_merge:
        parts.append(
            "Deleted tile shard path(s) present, but the canonical is mid-merge "
            "(MERGE_HEAD exists), where a partial `git commit -- <pathspec>` cannot run "
            "and `git add` would silently resolve a conflict. Not classifying or "
            "recommending anything: finish or abort the merge first. Paths: "
            + cap(dirty["deleted"])
        )
    else:
        if deletions["closed"]:
            if not safe_for_command(deletions["closed"]):
                parts.append(
                    "Uncommitted tile shard DELETIONS with confirmed closed issues, but "
                    "at least one path is not a plain sessions/<project>/tiles/<N>.json -- "
                    "not emitting a ready-to-run command. Inspect manually: "
                    + cap(deletions["closed"])
                )
            else:
                paths = " ".join(f"'{p}'" for p in deletions["closed"])
                where = f"git -C '{JOURNAL_REPO.as_posix()}'"
                parts.append(
                    "Uncommitted tile shard DELETIONS in the canonical checkout whose "
                    "issues are confirmed closed (this session's own unlinks, or an "
                    "earlier session's never-committed ones -- a session that spawns no "
                    "tile writes no stub, so these do not self-clear). Commit them with "
                    "this exact pathspec, whether or not you write a stub (safe: each "
                    "shard is a disjoint per-issue file, ADR-118). The canonical is "
                    f"currently on '{_safe_branch_label(branch)}' -- commit only if that "
                    "is today's draft branch; if a day rollover is also being reported, "
                    "cut the new branch FIRST, then commit there (a deletion is durable "
                    f"only once its carrying branch merges to main). {where} add -- {paths} "
                    f'&& {where} commit -m "journal: close tile shards for closed '
                    f'issues" -- {paths}'
                )
        if deletions["open"]:
            parts.append(
                "WARNING -- deleted tile shard(s) for an issue that is still OPEN: "
                + cap(deletions["open"])
                + ". Do NOT commit these; restore with `git checkout HEAD -- <path>` "
                "(works for both a staged and an unstaged delete, unlike `git checkout "
                "-- <path>`), or investigate."
            )
        if deletions["unverified"]:
            parts.append(
                "Deleted tile shard(s) whose issue identity or state could not be "
                "confirmed (gh/git unavailable, or a filename/embedded-issue mismatch): "
                + cap(deletions["unverified"])
                + ". Do not commit blind -- re-check state first."
            )
        if deletions["skipped"]:
            parts.append(
                f"{len(deletions['skipped'])} further deleted tile shard(s) not probed "
                f"(budget: {MAX_TILE_DELETION_PROBES} probes / "
                f"{TILE_PROBE_DEADLINE_SECONDS:g}s probing, or the "
                f"{LOOKUP_BUDGET_SECONDS:g}s hook-wide lookup budget already spent): "
                + cap(deletions["skipped"])
                + "."
            )

    if dirty["other"]:
        parts.append(
            "In-flight or conflicted tile shard changes from a concurrent session (added, "
            "modified, renamed, or unmerged -- not a plain delete): "
            + cap(dirty["other"])
            + ". Leave these alone -- never add another session's shard to your pathspec "
            "(ADR-118)."
        )

    return "\n".join(parts)


# --- network boundary (not unit-tested; repo avoids subprocess mocks) ---------


# `_ISSUE_PROJECTION`, `fetch_repo_issue_states`, and `check_issue_state` (imported above
# from `_gh_issue_state.py`, dev-env#967/ADR-131) are the `gh api` REST boundary this
# module's primary lookup loop and deletion-probe path call into. Only the git-plumbing
# subprocess boundaries below (committed_shard_identity onward) remain local to this file —
# they read/inspect the engineering-journal checkout itself, not GitHub issue state.


def committed_shard_identity(journal_repo: Path, path: str) -> tuple[str, int | None] | None:
    """`(url, issue)` from a shard as committed at HEAD; None on any failure.

    Read from git rather than disk because the file being classified is precisely one that
    is *deleted* in the working tree — there is nothing left to read there. Returns the
    embedded `issue` alongside the URL so the caller can cross-check it against the
    filename (see `classify_tile_deletions`). Mirrors `reconcile-open-prs.py`'s
    `committed_shard_identity`, reading the tile schema's `issue` field rather than
    open-PR's `pr`. Not unit-tested: subprocess boundary, matching `check_issue_state`'s
    convention.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(journal_repo), "show", f"HEAD:{path}"],
            capture_output=True,
            text=True,
            timeout=GIT_CALL_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            return None
        url = data.get("url")
        issue = data.get("issue")
        if not isinstance(url, str):
            return None
        return url, issue if isinstance(issue, int) else None
    except Exception:
        return None


def merge_in_progress(journal_repo: Path) -> bool:
    """True when the canonical is mid-merge (`.git/MERGE_HEAD` present).

    During a merge, `git commit -- <pathspec>` fails outright (cannot do a partial commit
    during a merge), so every deletion recommendation this hook emits would be unrunnable —
    and the `git add` half would silently resolve a conflict on the way. Suppress the whole
    deletion advisory instead. Handles a linked worktree, where `.git` is a file, by asking
    git for the real git dir. Mirrors `reconcile-open-prs.py`'s `merge_in_progress` exactly.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(journal_repo), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=GIT_CALL_TIMEOUT,
        )
        if result.returncode != 0:
            return False
        git_dir = Path(result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = journal_repo / git_dir
        return (git_dir / "MERGE_HEAD").exists()
    except Exception:
        return False


def current_branch(journal_repo: Path) -> str | None:
    """The branch the canonical currently holds; None on failure or detached HEAD.

    Used to gate the commit recommendation: ADR-119 lists "would commit onto whatever
    branch the canonical happens to hold" as a reason the *hook* must not commit, so
    delegating the commit to Claude has to carry that guard along with it rather than drop
    it. Mirrors `reconcile-open-prs.py`'s `current_branch`.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(journal_repo), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=GIT_CALL_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except Exception:
        return None


def dirty_tile_status_lines(journal_repo: Path) -> list[str]:
    """`git status --porcelain -- sessions` in the canonical checkout; [] on any failure
    (missing repo, git not on PATH, timeout). Not unit-tested — subprocess boundary,
    matching `check_issue_state`'s convention. Mirrors `reconcile-open-prs.py`'s
    `dirty_open_pr_status_lines`.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(journal_repo), "status", "--porcelain", "--", "sessions"],
            capture_output=True,
            text=True,
            timeout=GIT_CALL_TIMEOUT,
        )
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()
    except Exception:
        return []


def main() -> None:
    _hookutil.record_heartbeat("reconcile-pending-tiles")
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

    raw = sys.stdin.read().strip()
    data: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            data = parsed

    session_id = data.get("session_id") or f"unknown-{int(time.time())}"

    if already_ran(session_id):
        return

    try:
        tiles = load_tiles(JOURNAL_REPO)
    except Exception:
        tiles = []

    lookup_started = time.monotonic()
    states = lookup_states(group_numbers_by_repo(tiles))
    pending, removed, skipped = reconcile_tiles(tiles, states)
    prune_empty_tile_dirs(removed)

    mark_done(session_id)

    # Build the primary pending-tile message FIRST, before any of the newer, more
    # failure-prone deletion-advisory logic below. This hook's original job is the
    # "Pending tiles:" index; a crash or a timeout kill inside the new logic must not take
    # that with it, especially since mark_done() has already fired and the session will
    # never retry (dev-env#958, mirroring reconcile-open-prs.py's identical ordering note).
    unresolved = sum(
        1 for tile in pending if states.get((tile["repo"], tile["issue"])) is None
    )
    message = format_message(pending, removed, skipped, unresolved)

    # The whole deletion-advisory pass below is gated on actual elapsed time, not attempted
    # unconditionally: see deletion_advisory_time_remains / the HOOK_TIMEOUT_SECONDS comment
    # for why no fixed slack can be safely assumed to exist at this point.
    deletion_message = ""
    if deletion_advisory_time_remains(time.monotonic() - lookup_started):
        # Two separate try blocks: a failure in the fragile probing must not also discard
        # the cheap, reliable hands-off warning derived from `git status` alone. Mirrors
        # `reconcile-open-prs.py`'s identical split.
        try:
            dirty = classify_dirty_tile_paths(dirty_tile_status_lines(JOURNAL_REPO))
        except Exception:
            dirty = {"deleted": [], "other": []}

        deletions: dict[str, list[str]] = {"closed": [], "open": [], "unverified": [], "skipped": []}
        mid_merge = False
        try:
            if dirty["deleted"]:
                mid_merge = merge_in_progress(JOURNAL_REPO)
                if not mid_merge:
                    # Skip re-probing this session's own fresh unlinks -- reconcile_tiles
                    # above already confirmed CLOSED for each of them moments ago.
                    to_probe, pre_confirmed_closed = partition_known_closed(dirty["deleted"], removed)
                    deletions["closed"] = list(pre_confirmed_closed)
                    probe_start = time.monotonic()
                    try:
                        probed = classify_tile_deletions(
                            to_probe,
                            identity_fn=lambda p: committed_shard_identity(JOURNAL_REPO, p),
                            state_fn=check_issue_state,
                            # Local probe-phase deadline: either its own small budget, or
                            # close enough to HOOK_TIMEOUT_SECONDS that the trailing
                            # current_branch() call below still has room to run. NOT tied
                            # to LOOKUP_BUDGET_SECONDS -- that constant belongs to the
                            # primary lookup above and bears no fixed relationship to how
                            # much of HOOK_TIMEOUT_SECONDS is actually left once probing
                            # starts (see the HOOK_TIMEOUT_SECONDS comment).
                            deadline_fn=lambda: (
                                time.monotonic() - probe_start > TILE_PROBE_DEADLINE_SECONDS
                                or (time.monotonic() - lookup_started)
                                > HOOK_TIMEOUT_SECONDS - GIT_CALL_TIMEOUT
                            ),
                        )
                        for key in ("closed", "open", "unverified", "skipped"):
                            deletions[key].extend(probed.get(key, []))
                    except Exception:
                        # Classification itself failed part-way through -- route what was
                        # about to be probed to unverified rather than silently dropping it
                        # (the pre-confirmed-closed paths above are unaffected: they never
                        # depended on this call succeeding).
                        deletions["unverified"].extend(to_probe)
        except Exception:
            pass

        try:
            branch = current_branch(JOURNAL_REPO) if deletions["closed"] else None
            deletion_message = format_deletion_message(dirty, deletions, branch, mid_merge=mid_merge)
        except Exception:
            deletion_message = ""

    full_message = "\n".join(p for p in (message, deletion_message) if p)
    if not full_message:
        return

    # audience="model" -> {"hookSpecificOutput": {"additionalContext": ...}} on stdout at
    # exit 0, the channel UserPromptSubmit delivers to Claude. NOT stderr (invisible at
    # exit 0 here) and NOT systemMessage (the user toast) — the index exists for Claude to
    # act on. The event is a hardcoded literal, per _hookout's migration note.
    _hookout.emit_advisory("UserPromptSubmit", full_message, audience="model")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
