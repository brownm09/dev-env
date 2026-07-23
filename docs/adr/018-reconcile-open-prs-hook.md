# ADR 018 — Auto-Reconcile open-prs.jsonl Against GitHub State

**Date:** 2026-05-10
**Status:** Accepted
**Tags:** hooks, open-prs, UserPromptSubmit, github, token-efficiency

---

## Context

`open-prs.jsonl` in `brownm09/engineering-journal` tracks PRs whose full lifecycle (open →
review → merge) spans multiple sessions. It is updated manually by Claude: append on
`gh pr create`, remove via a Node.js one-liner on `gh pr merge`.

In practice the file drifts. Two failure modes recur:

1. **Session-end aborts.** A session that merges a PR and then loses context (e.g., via
   `/compact` before stub writing, or an unexpected stop) may skip the removal step.
   The merged PR stays in the file indefinitely.

2. **Missing additions.** A session that opens a PR in a complex multi-file context
   occasionally omits the `open-prs.jsonl` append step.

The consequences compound: `post-compact.py` reads the file to emit review reminders, and
Claude reads it at session start to establish context. Stale or missing entries cause Claude
to surface wrong reminders, miss real open PRs, or spend turns reconciling discrepancies —
each wasted turn costs tokens.

Confirmed instance at ADR authoring time: dev-env #170 (ADR 012) was open but had no entry;
a session ended without appending it.

---

## Decision

Add a `UserPromptSubmit` hook (`reconcile-open-prs.py`) that self-heals the file at session
start by querying GitHub directly.

**Behaviour:**

1. Runs once per session via a per-session sentinel file in `scratch/` (same pattern as
   `turn-count-hook.py`).
2. Discovers all `sessions/*/open-prs.jsonl` files in the engineering-journal repo.
3. For each entry, calls `gh pr view <N> --repo <owner>/<repo> --json state`.
4. Removes entries whose state is `MERGED` or `CLOSED` (rewrites the file in-place; deletes
   the file when empty).
5. On `gh` failure (network, auth, timeout), leaves the entry untouched — conservative.
6. Emits a `systemMessage` to stdout listing surviving open PRs and any removals, so Claude
   has correct context from turn 1 without a manual file read.
7. Does **not** commit. Modified files are left dirty and picked up by the next stub commit
   via the existing `git add ... open-prs.jsonl` step in CLAUDE.md.
8. Always exits 0 — never blocks.

**Why a hook instead of a manual utility:**
Staleness is invisible until it causes a problem. A hook that silently self-heals on every
session start eliminates the failure mode without requiring user or Claude action. A manual
script would only help after the user notices a problem, which is too late to prevent the
token waste.

**Why fix silently rather than emit a warning:**
A warning prompts Claude to act, which costs at least one additional turn. The hook can fix
the file cheaper than the warning can be processed. The `systemMessage` still surfaces
removals so there is no silent data loss.

**Why once-per-session rather than every prompt:**
`gh pr view` makes one API call per tracked PR. Running on every prompt would add latency and
API load for no additional value — PR state changes slowly, and a single reconciliation at
session start is sufficient coverage.

---

## Alternatives Considered

**Warning-only (no file modification from hook):**
Emitting a message and letting Claude fix the file would preserve the "hooks never modify
state" property, but costs one Claude turn per stale entry. Token waste is the problem being
solved; adding turns defeats the purpose.

**Detect and add missing PRs (not just remove stale ones):**
Could scan recent stubs/manifests to find PRs that should be in the file but aren't. Deferred:
requires parsing stub markdown, is fragile (stubs may not follow the exact format), and the
missing-addition failure mode is less frequent than the missed-removal mode.

**Run as a daily routine rather than a session hook:**
Would batch the `gh` calls but introduce a lag window where stale data affects sessions.
Session-start execution gives the strongest freshness guarantee.

---

## Consequences

- `open-prs.jsonl` is correct from the first turn of every session, eliminating the stale-data
  class of wasted turns.
- Sessions with no open PRs see no output (hook exits silently).
- Sessions with open PRs receive a `systemMessage` that replaces the manual file read Claude
  would otherwise perform.
- One `gh pr view` call per tracked PR per session — negligible cost given the typical file
  has 0–4 entries. **→ The transport is superseded by Amendment 1** (the per-PR *shape* stands
  unchanged — only the bucket moved).
- Stale sentinel files cleaned up after 30 days (same maintenance window as `turn-count-hook.py`).

---

## Amendment 1 (2026-07-23, dev-env#888) — REST-only transport; the two hazards it introduces

Decision step 3 above specifies `gh pr view <N> --repo <owner>/<repo> --json state`. That is
a **GraphQL** call, and this repo has repeated, *measured* GraphQL exhaustion: dev-env#769/#773
(project-board operations), again during PR #872, and again during `reconcile-pending-tiles.py`'s
implementation session at `graphql 0/5000` while REST `core` sat at `4999/5000`. An exhausted
bucket failed every lookup, and because step 5's conservative contract treats an unresolved
state as *keep*, **nothing was ever pruned**. The hook handled that safely — no entry was ever
dropped — so it was a degradation rather than a defect, but a total one, and it landed on the
same bucket that Projects v2 operations contend for with no REST surface at all (dev-env#769).

It was also worse here than in the tile reconciler that hit this first: one call per *PR*
rather than per *repo*, and both reconcilers run at session start, so an outage stopped them
together.

**`check_pr_state` now reads `GET /repos/{owner}/{repo}/pulls/{n}` over REST `core`, only.**

### Why REST-only, and not ADR-119's fallback

[ADR-119](119-day-rollover-draft-branch-and-orphaned-shard-deletions.md) reached the same
diagnosis from the orphaned-deletion side and addressed it with a GraphQL-then-REST *fallback*:
try `gh pr view`, and on `None` retry over REST. That removed the hard failure, but left three
things this amendment fixes, each of which cuts against the fallback's own stated goal:

- **The contended bucket is still hit first, on every healthy session.** The point of moving is
  to stop competing with the GraphQL-only Projects v2 operations, not to recover after losing
  the race. A fallback still spends the quota it was introduced to protect.
- **A hanging lookup costs two timeouts, not one** — 15 s + 15 s per PR — doubling worst-case
  latency at precisely the moment things are already degraded, inside a hook whose declared
  `settings.json` timeout is 30 s. One slow shard could get the hook killed.
- **The REST path was the rarely-exercised one.** It only ran once GraphQL had already failed,
  so its parsing was in practice never executed — and it carried both hazards below inline,
  untested.

REST-only keeps every benefit the fallback was reaching for (REST is now used *always*, so a
GraphQL outage can no longer route a deletion to `unverified`) with one code path instead of
two. A `core` failure almost always means auth or network is down, which GraphQL would not
survive either. This mirrors [ADR-118 Amendment 3](118-tile-persistence-shards.md)
(dev-env#882), which made the same move for tile shards and rejected a fallback for the same
reasons.

### Why `/pulls/{n}` and not `/issues/{n}`

REST models issues and pull requests in **one number space** per repo, distinguishing them only
by a `pull_request` key on the issues representation. `/issues/{n}` would therefore answer a
plausible `state` for a number naming a plain issue, and we would believe it. `/pulls/{n}`
cannot: it returns only pull requests, so an issue number 404s → non-zero exit → `None` → keep
(verified live). The hazard is *structurally absent* rather than filtered — which is why this
hook needs no `pull_request` check, while its `/issues`-reading sibling does.

### Why the per-PR shape is kept — batching is actively wrong here

The tile reconciler batches one paged `GET /issues` per *repo* and stops early once every
requested number resolves. That is sound there because a pending tile's issue is recent by
construction. **Tracked open-PR shards are the opposite: lingering *is* the failure this ADR
exists to fix**, so the oldest shard is simultaneously the most stale and the least reachable
by a bounded recent-window walk.

Measured at authoring time: a live `sessions/dev-env/open-prs/178.json` tracked a PR merged
**2026-05-05**, while page 2 of `GET /pulls?state=all&per_page=100` bottomed out at #406. At the
tile reconciler's 2-page cap that shard — the one most needing a prune — would resolve to `None`
forever. The batch would have saved 4 subprocess spawns (7 tracked shards across 3 repos) and
cost the hook its purpose. Total time is bounded by a wall clock instead (below), which does not
trade correctness for it.

The list endpoint is also the wrong shape for a second reason: it omits `merged` entirely (see
below), so batching would add a silent hazard for that non-gain.

### Two REST-specific hazards, both silent, both relocated into pure code

The transport function is an untested subprocess boundary by this repo's fixture-only
convention, so leaving either rule inside it — which is exactly what ADR-119's fallback did —
makes both untestable. Both now live in a pure `pr_state_from_row`:

- **MERGED is not a REST `state`.** `gh pr view --json state` returned `MERGED` as a distinct
  value; REST returns `state: "closed"` plus a *separate* merge signal. Collapsing merged into
  closed would **not** change what gets pruned — `should_remove` accepts both — which is
  precisely why it would go unnoticed, while every "removed stale entries" line silently
  mislabelled merges as closures and `classify_deletions` lost the distinction it buckets on.
  Note also that the merge signal **differs by endpoint**: `GET /pulls/{n}` carries a `merged`
  boolean, while the `GET /pulls` list shape (`pull-request-simple`) omits it and carries only
  `merged_at`. Both are honoured, so a future move to a list-based batch cannot quietly
  regress. This hazard has no analogue in ADR-118 Amendment 3 — issues never merge — and is
  the main reason that port did not transfer mechanically.
- **REST returns `state` lowercase** (`open`/`closed`) where GraphQL returned uppercase, and
  `should_remove` compares `"MERGED"`/`"CLOSED"` without case-folding. The predicate stays
  strict and the boundary normalises, rather than the reverse: a case-folding predicate would
  delete the one cheap regression pin that catches normalisation being dropped. Getting this
  wrong leaves the hook **inert** rather than fixed — fail-safe in direction, but a complete
  loss of pruning that nothing else reports. The test therefore runs raw REST rows all the way
  to the `unlink`, since a per-function assertion still passes when the normalisation is gone.

The `--jq` projection (`{number, state, merged, merged_at}`) is payload-shrink only — ~26 KB to
~81 B — and classifies nothing; a structural test pins that it carries both merge signals and
contains no `select(`, because it cannot be executed offline.

### A hook-wide lookup budget

ADR-119 bounded its *probe* block with a count and a deadline, noting that the 30 s
`settings.json` budget "already funds one `gh pr view` per tracked shard in the reconcile loop
before any probing starts". That was correct, and the reason the loop needed a bound of its
own: nothing capped its total, so N sequential lookups could exhaust the hook's timeout and get
it killed mid-flight — losing the entire `systemMessage`, including the `Open PRs:` line that is
this ADR's original job, after `mark_done()` had already fired so the session would never retry.

A `WorkBudget` now gates the *start* of every lookup, shared by the reconcile loop and the probe
block (whose own ADR-119 deadline is retained — whichever fires first wins, so neither guarantee
is lost). Gating starts rather than tracking segments keeps the arithmetic true however many
segments are added later: no subprocess ever starts after `WORK_BUDGET_SECONDS`, and any one in
flight is capped by its own timeout, so the hook cannot exceed
`WORK_BUDGET_SECONDS + max(per-call timeout)`. That relation is asserted by a test rather than
left in a comment — the `/review` finding on dev-env#886, applied to its sibling.

Exceeding the budget degrades to "unresolved, kept, and said so", which the step-5 contract
already treats as keep. **Step 5 is unchanged and remains the invariant every new path converges
on:** a 404, a non-zero exit, a timeout, malformed JSON, a junk row, and a spent budget all
yield `None` → keep. No path added here can drop an entry.
