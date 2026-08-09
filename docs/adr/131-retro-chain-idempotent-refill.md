# ADR-131: Idempotent Retro-Chain Refill

**Date:** 2026-08-09
**Status:** Accepted
**Tags:** routines, tiles, spawn-task, github-issues, retro-action, self-healing, idempotency, scheduled-tasks, rest-api, adr-094, adr-118, adr-129

---

## Context

On 2026-08-08 a `biweekly-retro` session hand-seeded one "chained tile" per repo (see
[dev-env#967](https://github.com/brownm09/dev-env/issues/967)): a `spawn_task` tile anchored to a
dedicated GitHub issue (issue-per-tile, [ADR-094](094-tile-tables-and-issue-per-tile.md)), carrying a
prompt-embedded "CHAIN block" that instructs whichever session picks up that tile to tick the
completed item in the repo's `retro-action` queue issue, pick the next unchecked item, and
`spawn_task` the next link itself — carrying the block forward verbatim.

The mechanism is **entirely prompt-carried**. Nothing in this repo enforces it mechanically: a
dismissed chip, a compacted session, an early exit, an API failure, or a human finishing the item by
hand outside the tile all break the chain silently and permanently, with no code anywhere aware the
chain exists (a repo-wide grep for `spawn_task`/`retro-action` inside `claude/scripts/` and
`claude/routines/` returned zero matches at the time this ADR was written). `biweekly-retro`'s own
Step 6 files (or updates) each repo's `retro-action` queue issue and stops — it has no seeding step
at all, so a subsequent biweekly run does nothing for a repo whose chain already died between runs.
dev-env's own chain very nearly demonstrated this directly: issue #966 closed with no successor tile
for a window before being manually re-seeded — the exact failure this ADR's mechanism exists to catch
automatically, observed live during the research for this change rather than hypothesized.

Three further messy, load-bearing facts, all confirmed by direct inspection of live repo state rather
than assumed:

- The `retro-action` label lives on the *queue* issue in every repo, but only lives on the *anchor*
  (work-item) issue when that anchor happens to be a freshly-filed issue — four of the six
  currently-chained repos instead reuse a pre-existing, unlabeled issue as their anchor. "Newest
  labeled issue" is therefore not a safe way to find either the queue or the anchor: a queue issue
  must be identified **structurally**, by the shape of its body, and a single repo can carry several
  open `retro-action`-labeled issues at once (career-playbook currently has five).
- `merickvaughn/lifting-logbook`'s currently-cited anchor, `#814`, is an already-merged **pull
  request**, not an issue. A candidate inline `#NNN` reference is therefore not safe to trust without
  verification — the exact failure mode the Decision section below closes.
- A candidate anchor issue number can already have an unrelated, older tile shard sitting at that
  path (an open issue, stale work, never garbage-collected) — this exact collision already happened
  once by hand (`win11-init-tools/tiles/55.json`, 2026-07-22).
- Repo state moves during the time a session spends investigating it — a live research pass for this
  change observed edits to `gas-lifting-logbook`'s queue issue within the preceding ten minutes. Any
  classification has to be made at the moment of use, never against an earlier snapshot.

## Decision

Split the mechanism into a deterministic, tested **classifier** and a judgment-requiring,
prose-driven **mutator**, rather than one script that does both:

```
claude/scripts/_gh_issue_state.py       (shared, pure GitHub issue/PR state helpers)
              |
claude/scripts/retro-chain-status.py    (read-only CLI -- per-repo classification)
              |
claude/skills/retro-chain-refill/SKILL.md  (mutating skill -- triages, refills, commits)
              |
    +---------+---------+
    |                   |
claude/routines/     claude/routines/biweekly-retro/SKILL.md
retro-chain-backstop/    (Step 6.5)
SKILL.md
```

This split is forced by a harness constraint, not a style preference: `spawn_task`, `list_sessions`,
and a validated Write-tool shard write are all **session-only** capabilities — nothing under
`claude/scripts/` can call any of them, the same constraint
[ADR-118](118-tile-persistence-shards.md) already documents as the reason no headless process can
re-spawn a tile. So the *mutating* half of this mechanism can only ever be prose a Claude session
follows, never a script; what a script **can** do safely is the *read-only* half — answering "is this
repo's chain alive, and if not, what is next" — which is exactly the part worth making deterministic
and unit-tested rather than re-derived in judgment-driven prose on every invocation.
`retro-chain-status.py`'s `classify_repo_status` is that answer, expressed as a five-way decision
table:

| Condition | Status |
|---|---|
| Newest chain shard exists, its issue is live `OPEN` | **ALIVE** — no refill |
| No live chain shard, no queue issue found | **NO_QUEUE_FOUND** |
| No live chain shard, queue issue found, no unchecked items | **QUEUE_EXHAUSTED** |
| No live chain shard, queue found, unchecked items exist, but an untagged shard was spawned on/after the queue's `createdAt` | **AMBIGUOUS** — don't guess, report for human review |
| No live chain shard, queue found, unchecked items exist, no same-window untagged shard | **NEEDS_REFILL** |

Both callers — the new `retro-chain-backstop` daily routine and `biweekly-retro`'s new Step 6.5 — act
on this identical classification through the shared `retro-chain-refill` skill, rather than each
re-deriving its own liveness check. That sharing is what makes item 2 of dev-env#967 ("make
`biweekly-retro`'s seeding conditional so it never double-seeds") a direct consequence of building
item 0 (the missing seeding step) correctly, rather than separate work: a repo the script classifies
`ALIVE` is recorded and left alone by both callers identically, because both callers are the same
skill.

### Structural queue/anchor identification, not label recency

`find_queue_issue` selects, among open `retro-action`-labeled issues sorted newest-`createdAt`-first,
the first one whose body is structurally a checklist (`is_queue_body`, backed by `parse_checklist`
finding at least one real `- [ ]`/`- [x]` line, skipping fenced code blocks). A single repo can carry
several `retro-action`-labeled issues simultaneously — most of them individual anchors, not the queue
— so "newest labeled issue" alone is not a safe selector; only the structural checklist shape reliably
distinguishes the queue from an anchor.

### `- [ ]` checklist lines only — escalation bullets are deliberately excluded

`parse_checklist` matches real Markdown checkbox syntax only. A `retro-action` queue issue's body can
also carry free-form `- **Escalation:**` bullets that reference already-tracked, older issues rather
than proposing new spawnable work — dev-env's own issue #966 was in fact seeded from exactly such an
escalation bullet, a one-off human judgment call this mechanism deliberately does not try to
reproduce. Checkbox syntax alone is sufficient to exclude these (confirmed against the literal
dev-env#963 body, pinned as a fixture in `test_retro_chain_status.py`) — no heading-based scoping is
needed. This is a considered scope decision, not an oversight: a mechanism general enough to pick the
next *checklist* item can run unattended; one that also has to judge which bold-prose bullets are
"really" spawnable work cannot, without re-introducing exactly the human judgment this mechanism
exists to take off the critical path.

### A candidate inline `#NNN` reference must be verified before it is trusted

An action item's text sometimes already names a pre-existing issue inline (e.g. "fix the thing, see
#814"). Before `retro-chain-refill` reuses such a reference as a tile's anchor, it verifies live via
`gh issue view` that the number resolves to an issue in this repo that is **open** and **not a pull
request** — never assumed valid from the text alone. This directly closes the
`merickvaughn/lifting-logbook` failure mode from the Context above: `#814` there is an already-merged
pull request, and issues and pull requests share one number sequence per repo, so a naive "does #NNN
exist" check would have passed it. Absent a valid candidate, `retro-chain-refill` files a fresh issue
instead, labeled `retro-action`, exactly as `biweekly-retro` already does for the queue issue itself.

### `AMBIGUOUS` is scoped to same-window shards, not "any open shard ever"

An untagged tile shard for the same repo, spawned in or after the queue issue's own `createdAt`
window, produces `AMBIGUOUS` rather than `NEEDS_REFILL` — reported for human review, never guessed.
Scoping the comparison to that window (rather than any open shard the repo has ever accumulated) is
deliberate: a repo with dozens of ordinary, unrelated tiles would otherwise report `AMBIGUOUS`
permanently. The accepted, honestly-documented limitation is the mirror case — a same-day unrelated
tile can still trigger a false `AMBIGUOUS` — accepted because over-flagging costs a few seconds of
human or session judgment, while under-flagging risks a duplicate spawn on top of a chain link that
was actually still alive. Between those two costs, this repo's existing conventions consistently
prefer the cheaper mistake (see, e.g., every "skip-and-keep" failure direction in
[ADR-118](118-tile-persistence-shards.md)), and this decision follows the same preference.

### The mutating skill, not the routines directly, owns the refill logic and the repo table

`retro-chain-refill` (`claude/skills/retro-chain-refill/SKILL.md`) is a shared building block,
modeled on `sync-routine-worktree`: invoked by other routines' prose, not primarily meant for direct
end-user invocation. It owns the single canonical six-repo participant table (repo, journal project
directory, local clone path) — the one place that list lives — and performs, in order: triage of the
script's classification (including a `list_sessions` cross-check before treating `NEEDS_REFILL` as
actionable, since a session already working the item — chip dismissed or not — should read as alive,
not as a gap); anchor resolution (verify-or-file, above); a fresh shard-path collision re-check
immediately before writing (closing the `win11-init-tools/tiles/55.json` collision from the Context
above — the script's own snapshot can be stale by the time a write actually happens, given how fast
this state moves); `spawn_task`; a Write-tool shard write (never a shell redirect or heredoc,
[ADR-129](129-journal-shell-write-guard.md)); and a direct commit of that shard to today's
`draft/YYYY-MM-DD` branch in the engineering-journal canonical checkout via `git -C`, never a
dedicated worktree, mirroring the existing Stub file workflow convention for the identical
disjoint-per-issue-file reason a stub, manifest, or open-PR shard commits that way.

### The updated CHAIN block: Write-tool wording, and the same PR/closed-issue validation

The CHAIN block text every future tile carries forward is updated in two places, both load-bearing:
the shell-serializer wording ("build the JSON with a serializer, never echo") is replaced with the
current Write-tool mandate ([ADR-129](129-journal-shell-write-guard.md) retired the serializer recipe
the same week the original block was written, so the block was already stale the day it shipped); and
a new step requires the same live PR/closed-issue validation described above before a tile reuses an
inline `#NNN` reference as its anchor — closing the identical `lifting-logbook#814` failure mode for
every *future* link in the chain, not only for `retro-chain-refill`'s own refills. The block also now
states plainly that a skipped or malformed CHAIN block is a same-day-recoverable miss rather than a
silent, permanent break, since `retro-chain-backstop` exists. The six tile shards already spawned on
2026-08-08 under the original block text are **not** retroactively edited — their `prompt` field is a
historical record of what was actually sent to `spawn_task`, and the updated block applies only to
links spawned going forward.

### A new optional `chain` field on the tile shard schema

A tile spawned by this mechanism carries one additional field beyond the seven `TILE_REQUIRED_FIELDS`
[ADR-118](118-tile-persistence-shards.md) already defines: `chain: {"queue_issue": "<url>",
"seeded_by": "<string>"}`. `queue_issue` lets a human (or a future tooling pass) trace a chain link
back to the queue it came from without re-deriving it from prompt text; `seeded_by` records which
caller and run produced it (e.g. `biweekly-retro 2026-08-08` vs. `retro-chain-backstop 2026-08-09`),
which is exactly the provenance this ADR's own idempotency argument depends on being auditable after
the fact. See [ADR-118](118-tile-persistence-shards.md) Amendment 6 for the field's exact shape and
why it is deliberately **not** added to `TILE_REQUIRED_FIELDS`.

## Alternatives considered

- **A stored chain-state file** (e.g. one JSON record per repo tracking "last known chain tip").
  Rejected: dev-env#967 explicitly requires the mechanism stay stateless beyond what is already
  durable (the queue issue's checklist, the tile shards themselves, and the paired GitHub issues) —
  introducing a sixth kind of journal-adjacent state file would duplicate information the shards and
  the queue issue already carry, and would itself need the same staleness handling
  `retro-chain-status.py` already has to do live against GitHub. The classifier reads live state on
  every invocation instead, per the Context section's observation that repo state moves during the
  time a session spends investigating it.
- **Always trusting an inline `#NNN` reference without live verification.** Rejected outright — this
  is not a hypothetical risk being defended against in the abstract, it is the literal
  `merickvaughn/lifting-logbook#814` bug this design exists to fix. An unverified reference would have
  reused an already-merged pull request as a tile's anchor.
- **A single script that both classifies and mutates.** Rejected because it is not available as a
  design choice, not merely undesirable: `spawn_task`, `list_sessions`, and a Write-tool shard write
  are session-only capabilities per the harness constraint [ADR-118](118-tile-persistence-shards.md)
  already established for the identical reason no headless process can re-spawn a tile. Recording
  this here so the idea is not re-proposed, the way ADR-118's own "headless re-spawner" alternative
  warns against re-proposing.
- **Scoping `AMBIGUOUS` to any open shard the repo has ever accumulated**, rather than only
  same-window shards. Rejected: a repo with many ordinary, unrelated open tiles would report
  `AMBIGUOUS` permanently, which is a worse failure mode (a mechanism that never actually refills a
  chain-heavy repo) than the accepted, narrower false-positive window discussed in the Decision
  section above.

## Consequences

- A repo's chain can no longer die permanently on a dismissed chip, a compacted session, an early
  exit, or an API failure: `retro-chain-backstop` re-checks every tracked repo once daily, and
  `biweekly-retro`'s own Step 6.5 re-checks the same six repos on every biweekly run, both through the
  identical, idempotent classification.
- `biweekly-retro` gains a new external dependency at run time: it must be able to reach
  `~/.claude/scripts/retro-chain-status.py` (the live junction to this repo's `claude/scripts/`,
  current as of whatever commit the canonical dev-env worktree currently holds on `main`) and the
  GitHub REST API, in addition to what it already depended on.
- A repo the classifier reports `AMBIGUOUS` gets **no** automatic action from either caller — by
  design, this is a human-review signal, not a silent gap. A backlog of unresolved `AMBIGUOUS` reports
  across repos and runs would be worth revisiting as its own follow-up if it turns out to recur often
  in practice.
- **A residual gap, honestly not closed by this design:** a `spawn_task` that succeeds followed by an
  interrupted shard write (a session crash between the two) leaves an anchor issue with no chain
  shard, so a later run's classification still reads `NEEDS_REFILL` for that repo and may file a
  second anchor issue for the same queue item rather than recognizing the first spawn as already
  underway. This is the same class of gap [ADR-118](118-tile-persistence-shards.md) already accepts
  for any non-chain tile interrupted between `spawn_task` and its shard write (the paired issue
  survives; only the shard's provenance detail is lost) — no different in kind here, and not
  otherwise mitigated by this ADR.
- New tile shards carry one additional optional field (`chain`); every existing reader of the tile
  shard schema (`reconcile-pending-tiles.py`, `journal-shard-write-advisory.py`) is unaffected, since
  neither validates or depends on any field beyond `TILE_REQUIRED_FIELDS`.
- Items 3 (an expiry/triage pass for stale, unresolvable tile shards) and 4 (splitting a retro's §3
  action items into product vs. process) from dev-env#967 remain explicitly out of scope for this
  ADR — filed as separate follow-up issues referencing dev-env#967, per this repo's tiling
  discipline, rather than folded in here.

## References

- `claude/scripts/_gh_issue_state.py` — shared GitHub issue/PR state helpers, extracted from
  `claude/scripts/reconcile-pending-tiles.py`
- `claude/scripts/retro-chain-status.py`, `claude/scripts/tests/test_retro_chain_status.py` — the
  read-only classifier and its coverage
- `claude/skills/retro-chain-refill/SKILL.md` — the shared mutating skill
- `claude/routines/retro-chain-backstop/SKILL.md` — the new daily routine
- `claude/routines/biweekly-retro/SKILL.md` → Step 6.5 — the conditional seeding insertion
- [GitHub REST API — Issues: List repository issues](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
  — primary source for the `GET /repos/{owner}/{repo}/issues` endpoint this mechanism's live-state
  checks are built on
- [ADR-094](094-tile-tables-and-issue-per-tile.md) — issue-per-tile, the anchor-issue requirement
  this mechanism's anchor resolution satisfies
- [ADR-118](118-tile-persistence-shards.md), especially Amendment 3 (the REST transport and its two
  hazards) and Amendment 6 (the new `chain` field) — the tile shard persistence model this mechanism
  writes into
- [ADR-129](129-journal-shell-write-guard.md) — the Write-tool mandate the updated CHAIN block and
  `retro-chain-refill`'s own writes both follow
- `brownm09/dev-env#967` — tracking issue; items 0/1/2 are this ADR's scope, items 3/4 are filed as
  separate follow-ups
- `merickvaughn/lifting-logbook#814` — the already-merged-pull-request-as-anchor incident this
  mechanism's candidate-validation step directly fixes
- `win11-init-tools/tiles/55.json` (2026-07-22) — the shard-path collision precedent motivating the
  pre-write re-check in `retro-chain-refill`
