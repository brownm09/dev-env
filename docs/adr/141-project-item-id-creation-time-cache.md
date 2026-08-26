# ADR-141 — Cache Project-Board Item IDs at Creation Time, Fall Back to a Full Fetch Only on a Miss

**Date:** 2026-08-26
**Status:** Accepted
**Tags:** hooks, post-tool-use, github-project, graphql, caching, reconcile-project-board, performance

---

## Context

Root `CLAUDE.md` → `## GitHub Project` documents a recipe for resolving a GitHub Projects v2 item
ID from an issue/PR number (e.g., to move Status in a later session). Before this ADR, that recipe
fetched **all** ~719 board items (`gh project item-list 3 --owner brownm09 --format json --limit
1000`) and discarded every one but the match — for a single-item lookup. GraphQL bills by query
cost, and this repo has 9+ filed GraphQL-exhaustion issues ([dev-env#999](https://github.com/brownm09/dev-env/issues/999),
[#899](https://github.com/brownm09/dev-env/issues/899), [#769](https://github.com/brownm09/dev-env/issues/769),
[#773](https://github.com/brownm09/dev-env/issues/773), [#882](https://github.com/brownm09/dev-env/issues/882),
[#886](https://github.com/brownm09/dev-env/issues/886), [#888](https://github.com/brownm09/dev-env/issues/888),
[#897](https://github.com/brownm09/dev-env/issues/897), [#1008](https://github.com/brownm09/dev-env/issues/1008)).

[dev-env#1057](https://github.com/brownm09/dev-env/issues/1057) is deliberately **not** filed as
the proven root cause of any specific exhaustion event — an earlier attempt at that attribution
could not be substantiated (the first budget reading taken was already post-call, with no
before-figure, and this machine's canonical checkout is shared by concurrent sessions drawing on
the same bucket). It rests only on the narrower, independently-verifiable claim: a full-board
fetch is the wrong shape for a one-item lookup, regardless of what else is consuming budget.

Investigation found three real consumers of this exact pattern (full fetch + linear scan for one
item), and one legitimate full-sweep consumer that must be left alone:

- The root `CLAUDE.md` recipe itself (the one dev-env#1057 named).
- `claude/scripts/get-project-item.sh` — a reusable script wrapping the identical pattern; it does
  **not** already solve the problem, despite dev-env#1057's hope that it might.
- `claude/scripts/post-pr-merge-project.py`'s `find_project_item()` — not named in the original
  issue at all. Runs the identical full fetch on **every PR merge** (to move the linked board item
  to Done), making it the highest-value fix of the three: fully automatic, no session judgment
  involved in whether to pay the cost.
- `claude/scripts/reconcile-project-board.py` is a **genuine** full-sweep consumer (open issues vs.
  board issues set difference, missing-required-field scan across every item) with its own
  `is_truncated()` guard already in place. Its core fetch is explicitly **not** narrowed by this
  ADR.

Separately, `post-tool-use.py` already computes a newly-created item's ID via `_gh_project.py`'s
`add_to_project()` and has, until now, only printed it to stderr for the session to manually copy
— never persisted it, even though every later lookup for that same item pays the full-fetch cost
again.

**Population-gap constraint (ADR-053):** `post-tool-use.py` never fires in background/`spawn_task`
sessions — an upstream Claude Code harness limitation, not something this repo's hooks can work
around. This repo uses background sessions heavily (240+ pending tiles at time of writing). Any
design that seeds a cache *only* at `post-tool-use.py`'s creation-time hook would have a large,
permanent population gap for exactly the sessions most likely to need a later lookup — a tile
session that files an issue, does work, and later needs to move its own item's Status.

## Decision

`_gh_project.py`'s `add_to_project()` — already the single shared choke point for `gh project
item-add`, used by both `post-tool-use.py`'s hook-triggered adds and `reconcile-project-board.py`'s
orphan-add step (ADR-073) — best-effort-caches every item ID it successfully creates, keyed by the
issue/PR number parsed from the URL it was already given. One change covers both creation paths
with no signature change at either call site.

1. **Cache format.** A single flat JSON dict at `C:/Users/brown/.claude/scratch/project-item-cache.json`,
   `"<owner>/<repo>#<number>": "<item-id>"`. Not per-issue-sharded like engineering-journal's tile
   shards — this is small and mostly-append, not a high-concurrency multi-writer system. A
   read-modify-write race between two concurrent sessions can lose an entry; accepted as
   self-healing (a lost write is just a future cache miss, not data loss), the same trade-off
   `dev-env-sync.py`'s single global scratch-state file already accepts.
2. **Atomic write.** Per-process tmp file + `os.replace` swap, copying `_hookutil.py`'s
   `record_heartbeat()` idiom exactly. Every cache function is best-effort and never raises — a
   cache miss or a failed cache write must never be indistinguishable from, or turn into, an
   `add_to_project` failure. A `PROJECT_ITEM_CACHE_PATH_OVERRIDE` env var, checked at call time
   (not import time), lets tests redirect every cache function to a throwaway path, mirroring
   `HOOK_HEARTBEAT_DIR_OVERRIDE` — including its override-beats-explicit-parameter precedence.
3. **Opportunistic full backfill.** `reconcile-project-board.py`'s `_reconcile_repo()` already
   fetches every board item for its legitimate set-difference sweep. It now also writes every
   fetched item's `(repo, number) -> id` into the same cache — keyed by each item's own
   `content.repository`, not the single repo being reconciled, since a shared board can carry
   items from other repos too (ADR-070, `--scan-dir`). This piggybacks on a fetch the script
   already pays for (zero additional `gh` calls) and closes the ADR-053 population gap: any item
   created by a background session, or via the documented manual fallback, gets backfilled the
   next time this script runs — regardless of how it was created.
4. **Fallback stays the existing full fetch, gated behind a cache miss.** `get-project-item.sh` and
   `post-pr-merge-project.py`'s `find_project_item()` both check the cache first (zero `gh` calls
   on a hit) and, only on a miss, run exactly the same full `--limit 1000` fetch-and-scan they
   already had — writing the result back into the cache so a repeat lookup hits. This is never a
   regression from current behavior: the worst case (a cold cache) costs exactly what every lookup
   already cost before this ADR.
5. **The root `CLAUDE.md` recipe now points at `get-project-item.sh`** instead of duplicating the
   inline fetch-and-scan pattern — the script now implements it correctly, so there is exactly one
   place to keep in sync instead of two. The manual-fallback recipe (used when the automatic hook
   didn't fire) also writes its computed item ID into the cache before finishing.

**Why cache-primary / fetch-fallback inverts ADR-076's live-primary / cache-fallback shape.**
[ADR-076](076-live-fetch-project-hook-single-select-options.md) decided the opposite precedence for
a *different* kind of cached data — a `single_select` field's *options* — because a live fetch
there is cheap (a handful of field options) and the options genuinely can change (an
`updateProjectV2Field` mutation regenerates all option IDs). Here the situation is inverted on both
axes: the *naive live path itself* is the expensive thing this ADR exists to avoid, and a project
item's ID is immutable for the life of that item on the board — it can never go stale the way
options can, so a cache entry is either correct or absent, never wrong. That asymmetry is why the
cache is the primary path here and the live fetch is the fallback, rather than the other way
around.

**Why not eliminate GraphQL entirely (distinguishing from ADR-018 Amendment 1).**
[ADR-018](018-reconcile-open-prs-hook.md) Amendment 1 rejected a GraphQL-fallback design for a
different per-PR lookup, reasoning "a fallback still spends the quota it was introduced to
protect... on every healthy session" — and could do this because a REST equivalent existed for PR
state. That move is unavailable here: GitHub Projects v2 has **no REST API surface at all**
(confirmed in root `CLAUDE.md`'s own "GraphQL-only, no REST fallback" note, citing
[dev-env#769](https://github.com/brownm09/dev-env/issues/769)). The lever available to this design
is avoiding *redundant* GraphQL calls via a cache, not eliminating GraphQL calls the way ADR-018
Amendment 1 did — a fallback that only fires on a genuine miss does not "spend the quota it was
introduced to protect" on a healthy (cache-hit) session, since it never runs at all in that case.

**Why not also implement narrow-`--limit`-first-then-paginate** (the other candidate direction
dev-env#1057 named). Its correctness would depend on an unverified assumption about `gh project
item-list`'s sort order — GitHub Projects v2 items are not documented as sorted by creation time or
content number, so a small-limit-first probe could still force a full paginated walk for an older
item, in the worst case barely better than today. Once the cache handles the common case (looking
up an item this same interactive session, or a recent background session via the reconcile
backfill, just created), the added complexity and correctness risk of also implementing pagination
isn't justified — the fallback keeps today's known-correct full fetch instead.

**Why `reconcile-project-board.py`'s core fetch is not narrowed.** It is a genuine, already-guarded
full-sweep consumer (`is_truncated()`), not a mis-implemented single-item lookup. The three fixed
consumers above are all "look up one item, fetch everything" bugs; this script's whole job is
compute a set difference across every item, which structurally requires fetching every item.

## Consequences

- The common case — look up an item this session, or a recently-active background session, just
  created — costs zero `gh` calls instead of a ~719-item fetch.
- `reconcile-project-board.py`'s already-necessary sweep now also fully backfills the cache
  (including pre-existing items that predate this ADR), closing the ADR-053 population gap without
  any additional `gh` calls.
- `get-project-item.sh` gains a genuinely new, deterministically-testable no-network cache-hit
  behavior — provable (not just assumed) by pointing `gh`'s own `GH_CONFIG_DIR` at an empty
  directory during a test and confirming the script still succeeds (see
  `claude/scripts/tests/test-get-project-item.sh`'s Test 0).
- One new shared-module test file, `claude/scripts/tests/test_gh_project.py` (Testing index item
  99), plus additive test coverage in `test_reconcile_project_board.py` (item 29) and
  `test-get-project-item.sh` (item 6). `test_post_tool_use.py` (item 12) needed no change — no
  existing case asserted anything about `item_id` persistence.
- Root `CLAUDE.md`'s duplicated inline recipe is removed in favor of pointing at
  `get-project-item.sh`, so there is exactly one implementation to keep correct.
- No new required configuration. The cache degrades gracefully to today's exact behavior (a full
  fetch every time) if the cache file is ever lost, corrupted, or unreadable — every cache function
  treats those cases identically to a cold miss.

**Family:** [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) (the population-gap
constraint this design's backfill answers), [ADR-068](068-reconcile-project-board-orphan-issues.md)
/ [ADR-070](070-reconcile-project-board-scan-dir.md) (the sweep the backfill piggybacks on),
[ADR-073](073-shared-worktree-canon-gh-project-modules.md) (the shared `_gh_project.py` module this
extends), [ADR-076](076-live-fetch-project-hook-single-select-options.md) (the inverted
live/cache-precedence precedent), [ADR-018](018-reconcile-open-prs-hook.md) (the distinguished
counter-precedent on eliminating vs. reducing GraphQL calls).

## References

- [dev-env#1057](https://github.com/brownm09/dev-env/issues/1057) — the motivating issue.
- [GitHub CLI manual — `gh project item-list`](https://cli.github.com/manual/gh_project_item-list) /
  [`gh project item-add`](https://cli.github.com/manual/gh_project_item-add) — the commands this
  ADR reduces the call volume of, not the call shape of.
- [GitHub REST API — Projects (classic)](https://docs.github.com/en/rest/projects) vs.
  [GitHub GraphQL API — ProjectV2](https://docs.github.com/en/graphql/reference/objects#projectv2) —
  confirms Projects v2 (used here) has no REST surface, unlike the classic Projects API GitHub
  deprecated; this is why a cache, not a protocol switch, is the available lever.
