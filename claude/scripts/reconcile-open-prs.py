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

**State is resolved over REST `core` only — never GraphQL** (dev-env#888, ADR-018 Amendment
1). `gh pr view --json state` is a GraphQL call, and this repo has repeated, *measured*
GraphQL exhaustion (dev-env#769/#773, PR #872, and `reconcile-pending-tiles.py`'s own
implementation session at `graphql 0/5000` while REST `core` sat at `4999/5000`). An
exhausted bucket failed every lookup, and since an unresolved state is conservatively
*kept*, nothing was ever pruned — safe, but a total and unreported loss of this hook's only
job, on the same bucket Projects v2 operations contend for with no REST alternative at all.

ADR-119 first addressed that with a GraphQL-then-REST *fallback*. Amendment 1 replaces it
with REST-only, which is strictly better on every axis that motivated the fallback: the
contended bucket is never touched rather than merely retried past, a hanging lookup costs
one timeout instead of two (the fallback doubled worst-case latency at exactly the moment
things were already degraded), and there is one code path rather than a rarely-exercised
second one. A `core` failure almost always means auth or network is down, which GraphQL
would not survive either. Mirrors `reconcile-pending-tiles.py` (dev-env#882, ADR-118
Amendment 3), which made the same move for tile shards and reached the same conclusion.

Unlike that reconciler, this one does **not** batch: see `check_pr_state` for why a
per-repo paged walk is actively wrong here, and `pr_state_from_row` for the two REST
hazards — a lowercase `state`, and MERGED not being a REST `state` at all.

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
    explicit-pathspec `add`/`commit` pair (paths shell-quoted and shape-validated first).
    The old advice ("include these in your next stub commit") was unreachable for the many
    sessions that open no PR and so write no stub.
  - **Deletions for a still-OPEN PR** — an anomaly (someone removed a live record); flagged,
    never recommended for commit.
  - **Deletions whose PR identity or state could not be confirmed** — `gh` failed on both the
    GraphQL and REST paths, or the shard's embedded `pr` disagrees with its filename stem.
    Conservative: reported, not recommended.
  - **Everything else (added / modified / untracked / renamed / unmerged)** — a *concurrent*
    session's in-flight shard, or a conflict. Recommending these for this session's pathspec
    is precisely the clobber ADR-056's explicit-pathspec rule exists to prevent, so they are
    reported as hands-off.

Only an exact ` D` / `D ` porcelain code counts as a deletion: the two-char status field puts
a `D` in several codes that are not post-merge bookkeeping at all (`AD`/`RD` — a concurrent
session's *staged* shard; the unmerged `DD`/`DU`/`UD`). The whole deletion advisory is
suppressed while the canonical is mid-merge, where a partial commit cannot run and `git add`
would silently resolve a conflict.

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
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# iter_pr_shards / read_legacy_entries are the shared open-PR readers (ADR-057), also used
# by post-compact.py. iter_pr_shards owns the numeric-filename filtering, so the reconcile
# loop no longer needs shard_pr_number directly. project_dirs is the sessions/<project>/
# walk that precedes them, shared for the same anti-drift reason (dev-env#881).
from _journal_shards import iter_pr_shards, project_dirs, read_legacy_entries, shard_pr_number

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL_PREFIX = "open-prs-reconciled-"

# Probing is bounded by BOTH a wall clock and a count, because the count alone cannot bound
# latency. The deadline is the real guard (a count of N still permits N × the per-call
# timeouts); the count is a secondary runaway backstop. Anything past either limit is
# reported as `skipped` rather than silently dropped. See ADR-119.
MAX_DELETION_PROBES = 10
PROBE_DEADLINE_SECONDS = 8.0

# One wall clock shared by EVERY `gh`/`git` subprocess this hook spawns, plus a ceiling on
# any single one. ADR-119's comment here noted that the 30s settings.json budget "already
# funds one `gh pr view` per tracked shard in the reconcile loop *before* any probing
# starts" — correct, and the reason that loop needed a bound of its own: nothing capped its
# total, so N sequential lookups could exhaust the hook's whole timeout and get it killed
# mid-flight, losing the entire systemMessage including the `Open PRs:` line that is this
# hook's original ADR-018 job. (Under ADR-119's fallback the per-PR worst case was two
# timeouts, 30s, so a *single* hanging shard could do it.)
#
# `WorkBudget` gates the start of every lookup, so the arithmetic needs no per-segment
# bookkeeping and stays true however many segments are added later: no subprocess ever
# *starts* after WORK_BUDGET_SECONDS, and any one already in flight is capped by its own
# timeout. `test_work_budget_cannot_outrun_the_hook_timeout` pins the resulting inequality as
# an assertion rather than leaving it in this comment — the /review finding on dev-env#886,
# applied to its sibling.
#
# NONLOOKUP_RESERVE_SECONDS is part of that inequality rather than an unstated remainder: the
# hook also does work no budget gates — `record_heartbeat`, `cleanup_stale_sentinels` (a scan
# of a shared scratch directory that accumulates indefinitely), the stdin read, and message
# assembly. Leaving that as whatever happened to be left over made the assertion claim more
# than it covered, which matters because a kill here is unrecoverable: `mark_done()` has
# already fired, so the session never retries. (/review finding on this PR.)
#
# Exceeding the budget degrades to "unresolved, kept, and said so" — kept because
# `should_remove` treats None as keep, and *said so* via the unresolved count in the emitted
# message, so a partial reconciliation is never reported as a clean one. Never a mis-prune.
HOOK_TIMEOUT_SECONDS = 30      # mirrors this hook's `timeout` in claude/settings.shared.json
GH_CALL_TIMEOUT = 8            # one REST lookup; was 15 per path, twice over, under ADR-119
GIT_CALL_TIMEOUT = 5           # local git plumbing — measured at ~0.07s
NONLOOKUP_RESERVE_SECONDS = 5  # heartbeat + sentinel sweep + stdin read + message assembly
WORK_BUDGET_SECONDS = 15.0


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
    OPEN, an unknown state, or None (a gh failure) is conservative — keep it.

    Deliberately case-sensitive. The state vocabulary is normalised once, at the transport
    boundary (`pr_state_from_row`), so relaxing this to a case-fold would delete the one
    cheap regression pin that catches that normalisation being dropped — which is precisely
    the failure the REST migration could reintroduce, and which is silent: REST answers
    "closed", this returns False for every PR, and the hook goes inert while still looking
    healthy. (dev-env#888)"""
    return state in ("MERGED", "CLOSED")


def pr_state_from_row(row) -> str | None:
    """A REST pull-request object -> 'OPEN' | 'MERGED' | 'CLOSED'; None if unusable.

    Pure, and deliberately so: this is where both REST-specific hazards live, and both are
    the kind that pass a naive test. `check_pr_state` is an untested subprocess boundary
    (this repo's fixture-only convention), so parsing here is what makes them coverable at
    all — the same split `reconcile-pending-tiles.py` uses for `issue_states_from_rows`.

    **MERGED is not a REST `state`.** The GraphQL predecessor (`gh pr view --json state`)
    returned MERGED as a distinct value; REST returns `state: "closed"` plus a *separate*
    merge signal. A naive port collapses merged into closed, silently retiring a value this
    module documents and `classify_deletions` buckets on. It would not change what gets
    pruned (`should_remove` accepts both) — which is exactly why it would go unnoticed,
    while every "removed stale entries" line silently mislabelled merges as closures.

    **The merge signal differs by endpoint.** `GET /pulls/{n}` carries a `merged` boolean;
    the `GET /pulls` *list* endpoint (the `pull-request-simple` schema) omits `merged`
    entirely and carries only `merged_at` — verified live against both. Honouring either
    signal keeps this helper correct for both shapes, so a future move to a list-based batch
    cannot quietly start reporting every merged PR as CLOSED.

    **`state` is upper-cased.** REST answers "open"/"closed" where GraphQL answered
    "OPEN"/"CLOSED", and `should_remove` compares uppercase without case-folding. Skipping
    this leaves the hook *inert* rather than fixed — fail-safe in direction, but a total,
    unreported loss of pruning.

    An unrecognised `state` passes through upper-cased, preserving `should_remove`'s
    "unknown -> keep" contract. Malformed input (non-dict, missing or non-string `state`)
    degrades to None -> keep, never to a spurious CLOSED.
    """
    if not isinstance(row, dict):
        return None
    state = row.get("state")
    if not isinstance(state, str) or not state:
        return None
    state = state.upper()
    if state != "CLOSED":
        return state
    # Only a closed PR can be a merged one; check the merge signal before settling on CLOSED.
    # `merged_at` is tested for truthiness, not merely for being a str: GitHub returns either
    # null or an ISO timestamp, so an empty string is anomalous and should not read as a merge.
    merged_at = row.get("merged_at")
    if row.get("merged") is True or (isinstance(merged_at, str) and merged_at):
        return "MERGED"
    return "CLOSED"


class WorkBudget:
    """One wall clock shared by every `gh`/`git` lookup this hook makes (see the constants).

    `clock` is injectable so the gating logic unit-tests offline without sleeping.
    """

    def __init__(self, budget: float = WORK_BUDGET_SECONDS, clock=time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + budget

    def spent(self) -> bool:
        return self._clock() >= self._deadline


def budgeted_state_fn(state_fn, budget: WorkBudget):
    """Wrap `state_fn(pr, repo)` so it stops issuing lookups once *budget* is spent.

    Returns a callable with the identical `(pr_number, repo) -> state | None` signature, so
    every existing call site — `reconcile_shard_dir`, `reconcile_file`, and
    `classify_deletions` — is unchanged and a future one is covered by construction.

    A short-circuited lookup returns None, which `should_remove` already keeps and which
    `classify_deletions` already routes to `unverified`. So the degradation is the same
    conservative one a `gh` failure produces, and both are reported rather than silent.
    """
    def call(pr_number, repo):
        if budget.spent():
            return None
        return state_fn(pr_number, repo)
    return call


def counting_state_fn(state_fn, tally: dict):
    """Wrap `state_fn(pr, repo)` so every unresolved lookup increments `tally['unresolved']`.

    An unresolved lookup (`None`) always *keeps* its entry, so the count is exactly the number
    of tracked PRs reported as surviving without GitHub having confirmed it. Surfacing it is
    what makes the "and said so" half of the conservative contract real: the `Open PRs:` line
    is injected into Claude's context on the first prompt of every session and drives real
    decisions (which PR to `/review`, whether work is outstanding), so listing an unconfirmed
    PR indistinguishably from a confirmed-open one lets a merged PR read as outstanding work.

    Before the lookup budget existed this was sporadic — an individual `gh` failure. A spent
    budget makes it *systematic*: every remaining PR resolves to None at once. Mirrors
    `reconcile-pending-tiles.py`'s unresolved count, whose stated purpose is that "a truncated
    or partial reconciliation is never reported as a clean one". (/review finding on this PR.)

    Deliberately wrapped around the reconcile loop's lookups only — the deletion probes have
    their own `unverified` bucket, and folding them in here would inflate a count the
    `Open PRs:` line is supposed to qualify.
    """
    def call(pr_number, repo):
        state = state_fn(pr_number, repo)
        if state is None:
            tally["unresolved"] = tally.get("unresolved", 0) + 1
        return state
    return call


def entry_repo_and_pr(entry: dict) -> tuple[str | None, int | None]:
    """Resolve (owner/repo, pr_number) from a tracking entry, or (None, *)/(*, None)."""
    repo = repo_from_url(entry.get("url", ""))
    pr_number = entry.get("pr")
    if not isinstance(pr_number, int):
        pr_number = None
    return repo, pr_number


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


# A staged (`D `) or unstaged (` D`) delete, and nothing else. Deliberately an exact-match
# set rather than a `"D" in status` substring test: the two-char porcelain field puts a `D`
# in many codes that are NOT post-merge bookkeeping — `AD`/`RD`/`MD`/`CD`/`TD` (a concurrent
# session's *staged* shard, the exact class ADR-056's pathspec rule exists to keep out of
# your commit) and the unmerged `DD`/`DU`/`UD`. The unmerged ones are the worst: a
# modify/delete conflict yields `UD`, where a recommended `git add` silently *resolves* the
# conflict and the following partial `git commit` then fails outright, stranding the shared
# canonical mid-merge — reachable straight from ADR-119's own `git merge origin/main`
# remediation step. Everything outside this set is reported as hands-off. (dev-env#873 review)
DELETED_STATUS_CODES = frozenset({" D", "D "})

# A shard path safe to interpolate into a ready-to-run shell command. `git status
# --porcelain` does not quote a path containing `;` (it quotes only paths with spaces or
# control chars), and such a path still satisfies the `/open-prs/` shape check — so without
# this the emitted "run this exact command" text could carry a second command. Anchored at
# both ends (`\Z`, not `$`, so a trailing newline cannot sneak a path past the check) and
# ASCII-digit-only (`[0-9]+`, not `\d`, which also accepts non-ASCII digit characters
# `int()` — and so `shard_pr_number` — would parse too). Deliberately narrower than the
# shape check used for *reporting*. (dev-env#873 review, dev-env#958 tightening)
SAFE_SHARD_PATH_RE = re.compile(r"^sessions/[A-Za-z0-9._-]+/open-prs/[0-9]+\.json\Z")


def classify_dirty_open_pr_paths(status_lines: list[str]) -> dict[str, list[str]]:
    """Split dirty open-PR paths into `deleted` vs `other`, preserving `git status` order.

    A *deletion* (exactly `D ` or ` D` — see `DELETED_STATUS_CODES` for why this is not a
    substring test) is post-merge bookkeeping over a disjoint per-PR file (ADR-056), so it
    is safe for whichever session finds it to commit once the PR is confirmed merged.
    Everything else — added, modified, untracked, renamed, or any unmerged/conflicted code —
    is a *concurrent* session's in-flight shard or a conflict, and must never be folded into
    this session's pathspec. Pure string filter; the merge confirmation is a separate step
    (`classify_deletions`)."""
    out: dict[str, list[str]] = {"deleted": [], "other": []}
    for line in status_lines:
        parsed = parse_open_pr_status_line(line)
        if parsed is None:
            continue
        status, path = parsed
        out["deleted" if status in DELETED_STATUS_CODES else "other"].append(path)
    return out


def shard_pr_number_from_path(path: str) -> int | None:
    """PR number from a `.../open-prs/<N>.json` path; None for the legacy `open-prs.jsonl`
    or a non-numeric stem.

    String-taking adapter over the ADR-057 shared reader — delegating rather than re-deriving
    keeps this hook from drifting from `iter_pr_shards`' enumeration, which is the whole point
    of that module and was asserted here only in prose before. (dev-env#873 review)

    The composite "is this a PR shard" rule is split across two places in `_journal_shards`:
    `shard_pr_number` owns the numeric-stem half, while the `*.json` half lives in
    `iter_pr_shards`' glob. Callers there always go through the glob first; this one receives
    a raw path from `git status`, so it must apply the suffix gate itself or a stray
    `open-prs/12.txt` would be treated as tracking PR 12."""
    if not path.endswith(".json"):
        return None
    return shard_pr_number(Path(path))


def classify_deletions(
    deleted_paths: list[str],
    url_fn,
    state_fn,
    max_probes: int = MAX_DELETION_PROBES,
    deadline_fn=None,
) -> dict[str, list[str]]:
    """Confirm each deleted shard's PR state and bucket the paths accordingly.

    The working-tree copy is gone (that *is* the state being classified), so the PR
    identity comes from the shard as committed at HEAD via `url_fn(path) -> (url, pr) | None`,
    and the state from `state_fn(pr, repo) -> 'OPEN'|'MERGED'|'CLOSED'|None`. Both are
    injected so this stays offline-testable, matching the reconcilers above.

    The shard's *embedded* `pr` is cross-checked against its filename stem and any mismatch
    is routed to `unverified` rather than trusted. The two can genuinely disagree —
    `journal-shard-write-advisory.py` flags exactly that case and is only *advisory*, so
    mismatched shards do land on disk — and trusting the filename alone would let a still-OPEN
    PR be reported as "confirmed merged, commit now". (dev-env#873 review)

    `deadline_fn() -> bool` returns True once the probe budget is spent; probing stops and the
    remainder is reported as `skipped`. Injected (default: never expired) so tests stay pure.

    Buckets: `merged` (safe to commit), `open` (a live record was deleted — anomaly),
    `unverified` (identity or state unresolvable — e.g. `gh` unavailable, or a filename/`pr`
    mismatch — never recommended), and `skipped` (past the count or time budget, reported
    rather than silently dropped, so a capped run never reads as full coverage)."""
    out: dict[str, list[str]] = {"merged": [], "open": [], "unverified": [], "skipped": []}
    probes = 0
    for path in deleted_paths:
        pr_number = shard_pr_number_from_path(path)
        if pr_number is None:
            # Legacy open-prs.jsonl (many PRs per file) or an unenumerable name — no single
            # PR to confirm, so it can't be auto-cleared for commit. Costs no probe.
            out["unverified"].append(path)
            continue
        if probes >= max_probes or (deadline_fn is not None and deadline_fn()):
            out["skipped"].append(path)
            continue
        probes += 1
        resolved = url_fn(path)
        url, embedded_pr = resolved if resolved else (None, None)
        repo = repo_from_url(url) if url else None
        if not repo or embedded_pr != pr_number:
            # No resolvable repo, or the shard's own `pr` disagrees with its filename — in
            # either case we do not know which PR this record belongs to.
            out["unverified"].append(path)
            continue
        state = state_fn(pr_number, repo)
        if should_remove(state):
            out["merged"].append(path)
        elif state == "OPEN":
            out["open"].append(path)
        else:
            out["unverified"].append(path)
    return out


def safe_for_command(paths: list[str]) -> bool:
    """True when every path is safe to interpolate into a ready-to-run shell command."""
    return all(SAFE_SHARD_PATH_RE.match(p) for p in paths)


def cap(paths: list[str], limit: int = 5) -> str:
    """Render a path list for a systemMessage, bounded. The message is injected into Claude's
    context on the first prompt of every session, so an unbounded list is a context cost paid
    by every session; `(+N more)` keeps the count honest. (dev-env#873 review)"""
    shown = ", ".join(paths[:limit])
    return shown + (f" (+{len(paths) - limit} more)" if len(paths) > limit else "")


# A branch name safe to interpolate, unquoted, into the ready-to-run advisory command's
# prose (the `git -C ... commit` recommendation names the current branch for the "only
# commit if this is today's draft branch" caveat). `current_branch` reads this from live
# `git branch --show-current` output, which is refspec-constrained but NOT shell-metachar-
# constrained — git's own ref-naming rules permit `~^:?*[\`, spaces, and other characters a
# shell parses specially (`git-check-ref-format`(1)). Conservative allowlist: fail closed to
# a placeholder rather than interpolate anything surprising into text a session may
# copy-paste and run verbatim. (dev-env#958 review)
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _safe_branch_label(branch: str | None) -> str:
    """`branch` if safe to interpolate into advisory prose, else a placeholder.

    `None` (detached HEAD, or `current_branch` itself failed) renders as `DETACHED`,
    matching this module's pre-existing wording. A non-None branch that fails the safe-
    character check renders as a distinct placeholder rather than being interpolated
    unsafely, or silently mislabeled as `DETACHED` — which would be factually wrong and
    could mask a real branch-name anomaly worth a human's attention. Mirrors
    `reconcile-pending-tiles.py`'s `_safe_branch_label`.
    """
    if branch is None:
        return "DETACHED"
    if _SAFE_BRANCH_RE.match(branch):
        return branch
    return "UNSAFE-BRANCH-NAME (verify manually)"


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


# jq projection applied by `gh api`, for payload size only — NEVER for classification. It
# carries the fields `pr_state_from_row` classifies on and decides nothing itself, so the
# rule stays in pure, testable code (the ADR-118 Amendment 3 discipline). Worth the filter:
# a full PR object is ~26 KB, this is ~81 B — ~327x less over a wire that is stalling the
# user's first prompt of the session. Both merge signals are carried deliberately; dropping
# either is the natural-looking simplification `test_pr_projection_preserves_merge_signals`
# exists to catch.
_PR_PROJECTION = "{number, state, merged, merged_at}"


def check_pr_state(pr_number: int, repo: str) -> str | None:
    """Return 'OPEN', 'MERGED', or 'CLOSED'; None on any failure. REST `core` only.

    See the module docstring for why the transport is REST-only rather than ADR-119's
    GraphQL-then-REST fallback (dev-env#888, ADR-018 Amendment 1).

    **`/pulls/{n}`, not `/issues/{n}`.** REST models issues and pull requests in one number
    space per repo, distinguishing them only by a `pull_request` key on the issues
    representation. Reading `/issues/{n}` would answer a plausible `state` for a number that
    names a plain *issue*, and we would believe it. `/pulls/{n}` cannot: it returns only
    pull requests, so an issue number 404s -> non-zero exit -> None -> keep. The hazard is
    structurally absent rather than filtered, which is why no `pull_request` check appears
    here (its sibling `reconcile-pending-tiles.py` needs one; it reads `/issues`).

    **One lookup per PR — deliberately not batched.** `reconcile-pending-tiles.py` batches a
    paged `GET /issues` per *repo* and stops early once every requested number is resolved,
    which is sound there because a pending tile's issue is recent by construction. Tracked
    open-PR shards are the opposite: lingering *is* the failure ADR-018 exists to fix, so
    the oldest shard is both the most stale and the least reachable. Measured at authoring
    time — a live `sessions/dev-env/open-prs/178.json` tracked a PR merged 2026-05-05, while
    page 2 of `GET /pulls?state=all&per_page=100` bottomed out at #406; at that reconciler's
    2-page cap the shard most needing a prune would resolve to None forever. The batch would
    have saved 4 subprocess spawns (7 tracked shards across 3 repos) and cost the hook its
    purpose. Total time is bounded by `WorkBudget` instead, which does not trade correctness
    for it.

    Interpolating *repo* into a REST **path** is also strictly safer than the `--repo` flag
    it replaces: `gh --repo` accepts a `HOST/OWNER/REPO` form, while `repos/<owner>/<repo>/
    pulls/<n>` cannot name a host at all.

    Not unit-tested — subprocess boundary, matching this module's convention. Everything
    around it is: classification through `pr_state_from_row`, gating through
    `budgeted_state_fn`, and the projection through a structural test.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}", "--jq", _PR_PROJECTION],
            capture_output=True,
            text=True,
            timeout=GH_CALL_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        return pr_state_from_row(json.loads(result.stdout))
    except Exception:
        return None


def committed_shard_identity(journal_repo: Path, path: str) -> tuple[str, int | None] | None:
    """`(url, pr)` from a shard as committed at HEAD; None on any failure.

    Read from git rather than disk because the file being classified is precisely one that
    is *deleted* in the working tree — there is nothing left to read there. Returns the
    embedded `pr` alongside the URL so the caller can cross-check it against the filename
    (see `classify_deletions`). `GIT_CALL_TIMEOUT`: this is a purely local `git show`,
    measured at ~0.07s — the original 15s was copied from the network path and doubled the
    worst-case probe cost for no benefit. Not unit-tested: subprocess boundary, matching
    `check_pr_state`'s convention."""
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
        pr = data.get("pr")
        if not isinstance(url, str):
            return None
        return url, pr if isinstance(pr, int) else None
    except Exception:
        return None


def merge_in_progress(journal_repo: Path) -> bool:
    """True when the canonical is mid-merge (`.git/MERGE_HEAD` present).

    During a merge, `git commit -- <pathspec>` fails outright (`cannot do a partial commit
    during a merge`), so every deletion recommendation this hook emits would be unrunnable —
    and the `git add` half would silently resolve a conflict on the way. Suppress the whole
    deletion advisory instead. Handles a linked worktree, where `.git` is a file, by asking
    git for the real git dir. (dev-env#873 review)"""
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

    Used to gate the commit recommendation: ADR-119 lists "would commit onto whatever branch
    the canonical happens to hold" as a reason the *hook* must not commit, so delegating the
    commit to Claude has to carry that guard along with it rather than drop it.
    (dev-env#873 review)"""
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


def dirty_open_pr_status_lines(journal_repo: Path) -> list[str]:
    """`git status --porcelain -- sessions` in the canonical checkout; [] on any failure
    (missing repo, git not on PATH, timeout). Not unit-tested — subprocess boundary,
    matching `check_pr_state`'s convention."""
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

    # One clock for every lookup below, reconcile loop and probe block alike, so N tracked
    # shards across N projects cannot collectively exhaust the hook's settings.json timeout
    # and get it killed before it prints anything (dev-env#888).
    budget = WorkBudget()
    gated = budgeted_state_fn(check_pr_state, budget)
    # The reconcile loop's lookups are additionally counted, so the `Open PRs:` line can say
    # how many of the PRs it lists were never actually confirmed. The deletion probes below
    # reuse `gated` directly — they report unconfirmed paths through their own `unverified`
    # bucket, and counting them here would inflate a figure that qualifies a different line.
    tally: dict = {"unresolved": 0}
    state_fn = counting_state_fn(gated, tally)

    for project_dir in project_dirs(JOURNAL_REPO):
        project = project_dir.name

        # Current format: per-PR shards.
        try:
            surviving, removed = reconcile_shard_dir(project_dir / "open-prs", state_fn=state_fn)
        except Exception:
            surviving, removed = [], []

        # Legacy format: single open-prs.jsonl (drains as its PRs merge).
        legacy = project_dir / "open-prs.jsonl"
        if legacy.exists():
            try:
                s2, r2 = reconcile_file(legacy, state_fn=state_fn)
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

    # Build the reconcile-loop parts FIRST, before any of the newer, more failure-prone
    # probing below. This hook's original ADR-018 job is the "Open PRs:" line; a crash or a
    # timeout kill inside the probe block must not take that with it, especially since
    # mark_done() has already fired and the session will never retry. (dev-env#873 review)
    parts: list[str] = []
    if all_removed:
        parts.append(
            "Reconciled open-PR tracking — removed stale entries: " + ", ".join(all_removed) + "."
        )
    if all_surviving:
        parts.append("Open PRs: " + ", ".join(all_surviving))
    if tally["unresolved"]:
        parts.append(
            f"{tally['unresolved']} of those could not be resolved against GitHub (gh failure, "
            f"or the {WORK_BUDGET_SECONDS:g}s lookup budget spent) — kept unchanged and listed "
            "above as open, so that list may include already-merged PRs."
        )

    # Two separate try blocks: a failure in the fragile probing must not also discard the
    # cheap, reliable hands-off warning derived from `git status` alone.
    try:
        dirty = classify_dirty_open_pr_paths(dirty_open_pr_status_lines(JOURNAL_REPO))
    except Exception:
        dirty = {"deleted": [], "other": []}

    deletions: dict[str, list[str]] = {"merged": [], "open": [], "unverified": [], "skipped": []}
    try:
        if dirty["deleted"] and not merge_in_progress(JOURNAL_REPO):
            probe_start = time.monotonic()
            deletions = classify_deletions(
                dirty["deleted"],
                url_fn=lambda p: committed_shard_identity(JOURNAL_REPO, p),
                state_fn=state_fn,
                # Whichever fires first: ADR-119's own probe-block deadline (so a slow probe
                # block still cannot eat the rest of the hook) or the hook-wide budget the
                # reconcile loop has already drawn on. Additive — neither guarantee is lost.
                deadline_fn=lambda: (
                    time.monotonic() - probe_start > PROBE_DEADLINE_SECONDS or budget.spent()
                ),
            )
        elif dirty["deleted"]:
            parts.append(
                "Deleted open-PR shard path(s) present, but the canonical is mid-merge "
                "(MERGE_HEAD exists), where a partial `git commit -- <pathspec>` cannot run "
                "and `git add` would silently resolve a conflict. Not classifying or "
                "recommending anything: finish or abort the merge first. Paths: "
                + cap(dirty["deleted"])
            )
    except Exception:
        pass

    if deletions["merged"]:
        branch = current_branch(JOURNAL_REPO)
        if not safe_for_command(deletions["merged"]):
            # A path outside the strict shard shape — never interpolate it into a
            # ready-to-run command; report it and let a human look.
            parts.append(
                "Uncommitted open-PR shard DELETIONS with confirmed merged/closed PRs, but "
                "at least one path is not a plain sessions/<project>/open-prs/<N>.json — not "
                "emitting a ready-to-run command. Inspect manually: "
                + cap(deletions["merged"])
            )
        else:
            paths = " ".join(f"'{p}'" for p in deletions["merged"])
            where = f"git -C '{JOURNAL_REPO.as_posix()}'"
            parts.append(
                "Uncommitted open-PR shard DELETIONS in the canonical checkout whose PRs are "
                "confirmed merged/closed (this session's own unlinks, or an earlier session's "
                "never-committed ones — a session that opens no PR writes no stub, so these do "
                "not self-clear). Commit them with this exact pathspec, whether or not you "
                "write a stub (safe: each shard is a disjoint per-PR file, ADR-056). The "
                f"canonical is currently on '{_safe_branch_label(branch)}' — commit only if "
                "that is today's draft branch; if a day rollover is also being reported, cut "
                "the new branch FIRST, then commit there (a deletion is durable only once its "
                f"carrying branch merges to main). {where} add -- {paths} && "
                f'{where} commit -m "journal: close merged open-pr shards" -- {paths}'
            )
    if deletions["open"]:
        parts.append(
            "WARNING — deleted open-PR shard(s) for a PR that is still OPEN: "
            + cap(deletions["open"])
            + ". Do NOT commit these; restore with `git checkout HEAD -- <path>` (works for "
            "both a staged and an unstaged delete, unlike `git checkout -- <path>`), or "
            "investigate."
        )
    if deletions["unverified"]:
        parts.append(
            "Deleted open-PR shard(s) whose PR identity or state could not be confirmed (gh "
            "unavailable, a filename/embedded-pr mismatch, or a legacy open-prs.jsonl covering "
            "many PRs): "
            + cap(deletions["unverified"])
            + ". Do not commit blind — re-check state first."
        )
    if deletions["skipped"]:
        parts.append(
            f"{len(deletions['skipped'])} further deleted open-PR shard(s) not probed (budget: "
            f"{MAX_DELETION_PROBES} probes / {PROBE_DEADLINE_SECONDS:g}s probing, or the "
            f"{WORK_BUDGET_SECONDS:g}s hook-wide lookup budget already spent): "
            + cap(deletions["skipped"])
            + "."
        )
    if dirty["other"]:
        parts.append(
            "In-flight or conflicted open-PR shard changes from a concurrent session (added, "
            "modified, renamed, or unmerged — not a plain delete): "
            + cap(dirty["other"])
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
