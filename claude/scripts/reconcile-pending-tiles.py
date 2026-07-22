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

Conservative on every uncertainty, mirroring `should_remove`: only a confirmed CLOSED
unlinks. OPEN, an unknown state, a `gh` failure, an issue outside the lookup window, a
failed URL validation, and a filename/`issue`-field disagreement all **keep** the shard.
Losing a payload is unrecoverable; keeping a stale one costs a line of index text.

Always exits 0 — never blocks.

Stdout: one JSON line whose `additionalContext` carries the pending-tile index, any
prunes, and an explicit count of anything unresolved or skipped — so a truncated or
partial reconciliation is never reported as a clean one.
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
from urllib.parse import urlparse

# iter_tile_shards / shard_number are the shared numeric-shard readers (ADR-057, generalised
# by ADR-118). iter_tile_shards owns the numeric-filename filtering, tolerant parse, and
# numeric sort, and materialises its list before returning — so unlinking shards while
# iterating its result is safe.
from _journal_shards import iter_tile_shards, shard_number

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL_PREFIX = "pending-tiles-reconciled-"

# Index lines shown before truncating. The full total and the dropped count are always
# stated (see format_message) — a cap that hid its own truncation would read as "these are
# all the pending tiles", which is exactly the wrong thing to tell Claude.
MAX_SHOWN = 10

# REST page size (100 is the GitHub maximum) and the per-repo page cap. `GET /issues` returns
# newest-first, and `fetch_repo_issue_states` stops as soon as every requested number is resolved
# or it has paged past them — so the cap only binds when a tile's issue is far older than the
# newest ~200 issue/PR numbers, which a pending tile is not, by construction. A shard whose issue
# falls outside the paged window resolves to None -> kept and counted as unresolved, never
# silently dropped. Note the window is narrower in *issue* terms than the row count suggests,
# because REST models PRs as issues and those rows consume the page too (see
# `issue_states_from_rows`).
ISSUE_PAGE_SIZE = 100
MAX_ISSUE_PAGES = 2

# Per-HTTP-page timeout, and the total wall-clock budget for all lookups. Both are sized against
# the fact that this runs on UserPromptSubmit, so every second spent here is a second the user's
# first prompt of the session is stalled. Batching plus early exit makes the realistic cost one
# page (~0.5-1s) per repo with tiles, so the budget only binds when `gh` is hanging — and stopping
# there degrades to "some tiles unresolved, all kept, and said so" rather than letting the hook be
# killed mid-flight and lose the whole index, prunes included.
#
# The two are deliberately balanced so the worst case needs no extra bookkeeping inside the page
# loop: MAX_ISSUE_PAGES * GH_CALL_TIMEOUT == LOOKUP_BUDGET_SECONDS, so one hanging repo exhausts
# the budget exactly and `lookup_states` skips every remaining repo. Strictly better than the
# pre-REST 15s single-call pairing, where a 15s hang left elapsed 15 < 20 and a second repo's 15s
# call still started (~30s worst case). Still at/below `reconcile-open-prs.py`'s 30s settings
# timeout, which does strictly more sequential `gh` work.
GH_CALL_TIMEOUT = 10
LOOKUP_BUDGET_SECONDS = 20.0

# Titles are truncated in the index only; the shard keeps the full value.
MAX_TITLE_CHARS = 60

# Conservative owner/repo character class, per ADR-118. Deliberately narrower than what
# GitHub technically permits so anything surprising fails closed (skip-and-keep) rather
# than being handed to `gh --repo`.
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

_GITHUB_HOST = "github.com"


def already_ran(session_id: str) -> bool:
    return _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).exists()


def mark_done(session_id: str) -> None:
    try:
        _hookutil.SCRATCH.mkdir(exist_ok=True)
        _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).write_text("")
    except Exception:
        pass


# --- pure helpers (unit-tested in tests/test_reconcile_pending_tiles.py) ------


def repo_from_issue_url(url) -> str | None:
    """Extract a validated ``owner/repo`` from a GitHub issue URL, or ``None``.

    ``None`` means "do not touch this shard" — every caller treats it as skip-and-keep,
    never as a reason to unlink. This is the security boundary described in the module
    docstring: the return value becomes ``gh --repo``'s argument, and ``gh`` accepts a
    ``HOST/OWNER/REPO`` form, so an unvalidated path segment could redirect the lookup at
    another host with the user's credentials — and a wrong lookup answering ``CLOSED``
    would delete a payload that cannot be reconstructed.

    Four checks, all required:

      - the scheme must be https. A tile shard's ``url`` is always a ``gh``-emitted issue
        URL, so anything else (``ssh://``, ``file://``, a bare ``http://``) is anomalous —
        and a scheme-only check on ``netloc`` would pass ``ssh://github.com/o/r``;
      - the host must be github.com. Compared case-insensitively because host names are
        case-insensitive (RFC 3986 s3.2.2), so lower-casing is normalisation rather than a
        relaxation of ADR-118's ``netloc == "github.com"`` — a ``userinfo@`` prefix or any
        other host still fails, since the whole ``netloc`` must match;
      - the first two path segments must exist and match ``_OWNER_REPO_RE``, which excludes
        ``/``, ``:``, whitespace, and every shell metacharacter;
      - neither segment may be ``.`` or ``..``. Both are spelled entirely from characters
        ``_OWNER_REPO_RE`` allows, so the regex alone accepts ``https://github.com/../..``
        and hands ``../..`` to ``gh --repo``. Caught by this function's own tests during
        authoring; relative-path segments have no meaning in an ``owner/repo`` and are
        exactly the kind of surprise that must fail closed.

    Anything unparseable is a failure, not an exception.

    Note what is deliberately *not* derived here: the issue number. It always comes from
    the shard's filename (``shard_number``), so even a URL that passes these checks cannot
    redirect the lookup to a different issue in the same repo.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https":
        return None
    if parsed.netloc.lower() != _GITHUB_HOST:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if owner in (".", "..") or name in (".", ".."):
        return None
    repo = f"{owner}/{name}"
    if not _OWNER_REPO_RE.match(repo):
        return None
    return repo


def should_remove_tile(state) -> bool:
    """A tile shard is removed only when GitHub confirms its paired issue is CLOSED.

    Closing the issue is the tile's completion signal (ADR-118). OPEN, an unknown state, or
    None (a `gh` failure, or an issue outside the lookup window) is conservative — keep.
    Mirrors `reconcile-open-prs.py`'s `should_remove`, minus MERGED (issues never merge).

    Deliberately case-sensitive. The state vocabulary is normalised once, at the transport
    boundary (`issue_states_from_rows`), so relaxing this to a case-fold would remove the
    regression pin that catches that normalisation being dropped — which is precisely the
    failure the REST migration could reintroduce, and which is silent: REST answers "closed",
    this returns False for every tile, and the hook goes inert while still looking healthy.
    """
    return state == "CLOSED"


def issue_states_from_rows(rows) -> dict[int, str]:
    """Parsed REST `GET /issues` rows -> `{issue_number: STATE}`.

    Pure, and deliberately so: this is where both REST-specific hazards live, and both are the
    kind that pass a naive test. `fetch_repo_issue_states` is an untested subprocess boundary
    (this repo's fixture-only convention), so parsing here is what makes them coverable at all.

    **Pull requests are dropped.** GitHub REST models a PR as an issue, so `GET /issues` returns
    both, distinguished only by a `pull_request` key; the GraphQL predecessor (`gh issue list`)
    filtered them server-side, which is why the original reader needed no such check. Issues and
    PRs share ONE number sequence per repo, so this is not a collision between two live objects:
    the hazard is a shard whose number happens to name a PR, which without this filter would
    resolve to that PR's state and be unlinked on a merged/closed one. A dropped row is simply
    absent, so such a shard resolves to None -> kept.

    *Presence* of the key is the test, never its value — a row carrying `pull_request: null` is
    still treated as a PR. If the projection in `fetch_repo_issue_states` ever changes shape, that
    direction degrades to "everything unresolved, everything kept" rather than to a mis-prune.

    **`state` is upper-cased.** REST returns "open"/"closed" where `gh issue list` returned
    "OPEN"/"CLOSED", and `should_remove_tile` compares against "CLOSED" without case-folding.
    Skipping this would leave the hook *inert* rather than fixed — fail-safe in direction, but a
    total, unreported loss of pruning.

    Malformed input degrades to omission, never to a spurious state: a non-list, a non-dict row, a
    missing or non-string `state`, and a JSON `true` masquerading as issue #1 (`isinstance(True,
    int)` is True) are all skipped.
    """
    states: dict[int, str] = {}
    if not isinstance(rows, list):
        return states
    for item in rows:
        if not isinstance(item, dict):
            continue
        if "pull_request" in item:
            continue
        number, state = item.get("number"), item.get("state")
        if isinstance(number, int) and not isinstance(number, bool) and isinstance(state, str):
            states[number] = state.upper()
    return states


def should_stop_paging(rows, resolved, wanted, page_size: int = ISSUE_PAGE_SIZE) -> bool:
    """True when another page of `GET /issues` cannot usefully change the result.

    Any of four independent reasons ends the walk:

      - the page came back short (or unusable), so it was the last one;
      - nothing was requested, so one page is already more than enough;
      - every requested number is resolved — the common case, satisfied by page 1, since a
        pending tile's issue is recent by construction;
      - the page carries a number below the lowest one requested. REST `GET /issues` defaults to
        `sort=created&direction=desc` and GitHub assigns numbers in creation order, so crossing
        that floor means every remaining page is older than anything wanted.

    The floor check reads *raw* rows, PRs included: they share the number sequence, so a PR below
    the floor proves the same thing an issue would, and they are exactly what makes the paged
    window narrower in issue terms than its row count.

    A transferred issue can break the created/number correspondence and stop the walk early; it
    then resolves to None -> kept, the same fail-safe as an issue outside the window. Both wrong
    directions are bounded and safe: stopping late costs one extra page, stopping early costs a
    kept shard.
    """
    if not isinstance(rows, list) or len(rows) < page_size:
        return True
    if not wanted:
        return True
    if set(wanted) <= set(resolved):
        return True
    floor = min(wanted)
    for item in rows:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if isinstance(number, int) and not isinstance(number, bool) and number < floor:
            return True
    return False


def project_dirs(journal_repo: Path) -> list[Path]:
    """Every `sessions/<project>/` directory, sorted; [] if sessions/ is absent.

    A local copy of `reconcile-open-prs.py`'s helper rather than a shared import: that
    script is a hyphenated module (not importable by name) and is concurrently modified by
    an in-flight PR, so hoisting the helper into `_journal_shards.py` and migrating both
    callers is deferred to its own change (dev-env#881) to avoid a merge conflict.
    """
    sessions = journal_repo / "sessions"
    if not sessions.is_dir():
        return []
    return sorted(p for p in sessions.iterdir() if p.is_dir())


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


# --- network boundary (not unit-tested; repo avoids subprocess mocks) ---------


# jq projection applied by `gh api`, for payload size only — NEVER for classification. It
# preserves the *presence* of `pull_request` rather than deciding what it means, so
# `issue_states_from_rows` applies one identical rule to both this shape and a full unprojected
# REST row (where `pull_request` is an object), and the filter stays testable against real API
# output. Worth the filter: a full row is ~9 KB, this is ~40 B — ~225x less over a wire that is
# stalling the user's first prompt of the session. The `[...]` wrapper keeps each page a single
# `json.loads`-able array.
_ISSUE_PROJECTION = (
    '[.[] | if has("pull_request") then {number, state, pull_request: true} '
    'else {number, state} end]'
)


def fetch_repo_issue_states(repo: str, numbers=(), page_size: int = ISSUE_PAGE_SIZE,
                            max_pages: int = MAX_ISSUE_PAGES,
                            timeout: int = GH_CALL_TIMEOUT) -> dict[int, str] | None:
    """REST `GET /repos/<repo>/issues` for *repo* -> `{issue_number: STATE}`; None if no page read.

    The batched counterpart of `reconcile-open-prs.py`'s per-item `gh pr view`, on the REST `core`
    bucket rather than GraphQL — see the module docstring's point 3 for why the transport moved
    (dev-env#882, ADR-118 Amendment 3).

    Interpolating *repo* into a REST **path** is strictly safer than the `--repo` flag it replaces:
    `gh --repo` accepts a `HOST/OWNER/REPO` form, which is the credential-redirect primitive
    `repo_from_issue_url` exists to block, while `repos/<owner>/<repo>/issues` cannot name a host
    at all. That validation stays regardless — it is still how *repo* is derived, and defence in
    depth costs nothing here.

    Pagination is bounded and stops early (`should_stop_paging`): the normal case is one page,
    because a pending tile's issue is recent by construction. On any page failure the walk stops
    and whatever was already collected is returned — a partial result can only ever *omit* a
    number, never mis-resolve one, and an omitted number is None -> kept. None is returned only
    when not even the first page was read, preserving the previous all-or-nothing failure signal
    (`lookup_states` treats both identically anyway).

    Not unit-tested — subprocess boundary, matching `check_pr_state`'s convention. Everything
    around it is: row parsing through `issue_states_from_rows`, the stop rule through
    `should_stop_paging`, and the batching/budget/None-fallback through `lookup_states`'
    injectable `fetch`.
    """
    wanted = {n for n in numbers if isinstance(n, int) and not isinstance(n, bool)}
    states: dict[int, str] = {}
    read_a_page = False
    for page in range(1, max_pages + 1):
        try:
            result = subprocess.run(
                ["gh", "api",
                 f"repos/{repo}/issues?state=all&per_page={page_size}&page={page}",
                 "--jq", _ISSUE_PROJECTION],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                break
            rows = json.loads(result.stdout)
        except Exception:
            break
        read_a_page = True
        states.update(issue_states_from_rows(rows))
        if should_stop_paging(rows, states, wanted, page_size):
            break
    if not read_a_page:
        return None
    return states


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

    states = lookup_states(group_numbers_by_repo(tiles))
    pending, removed, skipped = reconcile_tiles(tiles, states)
    prune_empty_tile_dirs(removed)

    mark_done(session_id)

    unresolved = sum(
        1 for tile in pending if states.get((tile["repo"], tile["issue"])) is None
    )
    message = format_message(pending, removed, skipped, unresolved)
    if not message:
        return

    # audience="model" -> {"hookSpecificOutput": {"additionalContext": ...}} on stdout at
    # exit 0, the channel UserPromptSubmit delivers to Claude. NOT stderr (invisible at
    # exit 0 here) and NOT systemMessage (the user toast) — the index exists for Claude to
    # act on. The event is a hardcoded literal, per _hookout's migration note.
    _hookout.emit_advisory("UserPromptSubmit", message, audience="model")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
