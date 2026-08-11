# ADR-132 — Sequential Tile Spawning for Hard (Finish-to-Start) Dependency Chains

**Date:** 2026-08-10
**Status:** Accepted
**Closes:** [dev-env#977](https://github.com/brownm09/dev-env/issues/977)
**Tags:** tiles, spawn-task, dependency-chain, finish-to-start, multi-pr-decomposition, sequencing, workflow, global-rule, claude-facing, adr-046, adr-059, adr-094, adr-113, adr-118, adr-123
**Related:** [ADR-046](046-post-merge-followup-tiles.md), [ADR-059](059-multi-pr-issue-hierarchy.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-113](113-cross-session-handoff-tiles.md), [ADR-118](118-tile-persistence-shards.md), [ADR-123](123-forward-link-phase-dependent-followons.md)

---

## Context

The tile-capture discipline ([ADR-046](046-post-merge-followup-tiles.md), extended by
[ADR-059](059-multi-pr-issue-hierarchy.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md),
[ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-109](109-tile-gate-deferral-question-trigger.md),
[ADR-113](113-cross-session-handoff-tiles.md), [ADR-118](118-tile-persistence-shards.md),
[ADR-123](123-forward-link-phase-dependent-followons.md)) directs Claude to spawn a `spawn_task`
tile for every genuine follow-up the moment it's identified, never asking the user whether or
when. Each tile runs as an independent background session with its own worktree/branch. Nothing
in that discipline orders tiles relative to each other — two tiles spawned in the same
enumeration pass are, from the harness's perspective, unrelated concurrent sessions.

[ADR-059](059-multi-pr-issue-hierarchy.md) governs the most common source of *related* tiles: a
multi-PR decomposition, where a top-level issue is broken into sub-issues and each gets its own
tile embedding both issue references. ADR-059 deliberately does not address ordering — for good
reason, since most sub-issues of a decomposition are independently workable and spawning them
together is the point (parallel throughput). But some decompositions produce sub-issues with a
genuine **finish-to-start dependency**: sub-issue B's implementation cannot begin until sub-issue
A's PR has merged, because B needs code, a schema, or an artifact only A produces.

When two such tiles are spawned together, the downstream session starts immediately, before its
prerequisite exists. It then either blocks/idles (wasting the session), improvises against a
guessed version of the not-yet-existing prerequisite (producing work that conflicts with what the
upstream tile actually ships), or discovers the blocker mid-session and hands back an incomplete
result. Any of these costs a session's worth of tokens and needs manual reconciliation once
noticed — the same class of waste the tile-capture discipline exists to prevent, just introduced
by the tiling mechanism itself.

### Motivating example (caught proactively, before any tile was spawned)

cover-letter-runtime [#51](https://github.com/brownm09/cover-letter-runtime/issues/51) and
[#52](https://github.com/brownm09/cover-letter-runtime/issues/52) are both sub-issues of the
Phase 2 decomposition (top-level issue #47), filed three seconds apart on 2026-08-10. #51 is
"Phase 2 — Draft (1/3): fixtures, schemas, and deterministic checks"; #52 is "Phase 2 — Draft
(2/3): nodes and graph," and its body states outright: *"Depends on 1/3 (fixtures, schemas,
deterministic checks)"* — #52's nodes are built against the schemas #51 defines, and its own
open design questions explicitly build on decisions #51 has to make first. As of this ADR, no
tile has been spawned for either issue (confirmed against the `sessions/cover-letter-runtime/tiles/`
shards in engineering-journal). The dependency was flagged before either tile was created, not
after a downstream tile had already started work blind.

## Decision

Add a rule to the **"Capture follow-ups as tiles"** bullet in `## Git Workflow` (the same bullet
[ADR-113](113-cross-session-handoff-tiles.md) and [ADR-123](123-forward-link-phase-dependent-followons.md)
extended): when enumerating follow-ups to tile in the same pass, and two or more have a genuine
finish-to-start dependency, spawn only the first tile in the chain. That tile's `spawn_task`
prompt must explicitly instruct it to spawn the next tile(s) in the chain — giving each its own
paired tracking issue and shard, per the existing discipline — once its own PR has merged, rather
than leaving the hand-off implicit or deferring it to a future session that has to rediscover the
dependency from scratch.

**Scope test.** This does not apply to tiles that are merely related, thematically grouped, or
part of the same initiative — most sub-issues of an ADR-059 decomposition are independently
workable and should still be spawned together, since parallel throughput is the point of
decomposing. The test: could the downstream session do real, non-speculative work right now, with
nothing from the upstream tile's output? If yes, spawn both (or all) together. If no — the
downstream session would have to block, guess, or duplicate — spawn only the head of the chain.

**One edit site.** Following ADR-113 and ADR-123's precedent, this is appended to the existing
"Capture follow-ups as tiles" bullet rather than introduced as a new top-level bullet or as an
amendment to ADR-059 — it is a narrow refinement to *when* to spawn, not a restructuring of the
decomposition hierarchy ADR-059 defines.

## Rationale

- **The dependency is exactly why a round trip is required.** Spawning the downstream tile "to
  save a round trip" defeats its own purpose: the round trip (upstream merges -> its session
  spawns the downstream tile) is what guarantees the downstream session starts with its
  prerequisite actually present, rather than guessed at.
- **This is a spawn-time sequencing decision, not a discoverability problem — the complementary
  half of ADR-123, not a duplicate of it.** ADR-123 addresses a follow-on whose blocking issue
  *already exists and is being worked independently*, discovered as dependent sometime *after*
  both are already in flight; its remedy is a forward-reference comment so whoever reaches the
  blocking issue's trigger phase can find the follow-on. This ADR addresses the case where *both*
  the blocking and the dependent work are being tiled in the *same* enumeration pass, before
  either has started; its remedy is to not spawn the dependent one yet at all, and to make the
  upstream tile responsible for spawning it. Different moment, different remedy, same underlying
  respect for a hard dependency.
- **Consistent with the existing tile-shard and issue-per-tile discipline.** The chained
  (downstream) tile still gets its own tracking issue and shard when the upstream tile spawns it
  ([ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-118](118-tile-persistence-shards.md)) —
  this rule only changes *when* that spawn happens, not whether the downstream follow-up still
  gets the full durable-anchor treatment.
- **Durable, so it belongs in the repo.** Per [ADR-038](038-durable-preferences-documented-in-repo.md),
  a cross-session workflow rule like this must live in the version-controlled instructions, not
  only in agent memory.

## Alternatives considered

- **Spawn both/all tiles together, relying on the downstream session to detect the missing
  prerequisite and wait or abort.** Rejected — this still burns a session's worth of setup/tokens
  before the block is discovered, and background tile sessions have no reliable cross-session
  signal to know when the upstream PR merges.
- **Spawn both, but tell the downstream tile's prompt to check for the dependency and idle/retry.**
  Rejected for the same reason polling patterns are rejected elsewhere in this tiling discipline —
  no reliable wake signal, wasted tokens; better to not start the session until the trigger has
  actually occurred.
- **Fold this into ADR-059 as an amendment instead of a new ADR.** Rejected, mirroring ADR-123's
  identical reasoning: this is a narrow, separable refinement to *when* tiles in a decomposition
  get spawned, not a change to the top-level-issue/sub-issue *structure* ADR-059 defines. Keeping
  it a separate ADR (as ADR-113 and ADR-123 both did for their own single-clause extensions) keeps
  ADR-059 stable and each refinement independently referenceable.
- **A mechanical hook that blocks co-spawning two `spawn_task` calls in the same turn.** Rejected
  as premature, for the same reason ADR-113 and ADR-123 both rejected hooks for adjacent
  tile-judgment rules: "these two follow-ups have a hard finish-to-start dependency" requires
  reading and understanding both issues' content — a semantic judgment a hook can't reliably make
  without a high false-positive rate (most co-spawned tiles are *not* dependent; a naive heuristic
  like "both reference the same top-level issue" would false-positive on every ADR-059
  decomposition, including the common parallelizable case). A hook can follow later if the
  anti-pattern recurs despite the documented rule.

## Consequences

**Positive:** a downstream tile in a hard-dependency chain never starts work before its
prerequisite exists; the chain still gets full tracking (issue + shard) at each link, just
deferred to the moment each link is actually actionable — mirroring how a human engineer
sequences dependent PRs rather than opening all of them at once.

**Negative / residual:**

- Like its sibling tile rules, this is a behavioral convention, not mechanically enforced —
  whether two follow-ups have a "genuine" finish-to-start dependency is a judgment call, same as
  ADR-123's "phase-dependent" judgment.
- The upstream tile's spawned session now carries an extra responsibility (spawn the next tile in
  the chain on completion) that it must be told about explicitly in its own `spawn_task` prompt at
  the moment the chain's head is spawned — if that instruction is dropped, the chain silently
  stalls after the first link merges. The existing pending-tile / dangling-issue backstops
  ([ADR-092](092-dangling-issue-tile-enumeration-gate.md), [ADR-118](118-tile-persistence-shards.md))
  still catch this eventually, since the un-spawned downstream sub-issue stays open with no tile,
  but that's a slower backstop than a chained spawn.
- Adds another clause to an already-dense bullet, accepted for the same reason ADR-113/ADR-123
  accepted it: a single edit site is cheaper than a fragmented one.

## References

- [dev-env#977](https://github.com/brownm09/dev-env/issues/977) — the immortalization issue this ADR closes.
- [ADR-046](046-post-merge-followup-tiles.md) — the base post-merge tile capture discipline.
- [ADR-059](059-multi-pr-issue-hierarchy.md) — multi-PR decomposition (top-level + sub-issues); this ADR refines *when* its tiles get spawned, without changing its structure.
- [ADR-094](094-tile-tables-and-issue-per-tile.md) — issue-per-tile and the tile table; the downstream tile still gets both when it's eventually spawned.
- [ADR-113](113-cross-session-handoff-tiles.md) — the prior extension of this same "Capture follow-ups as tiles" bullet; template for placement and hook-deferral reasoning.
- [ADR-118](118-tile-persistence-shards.md) — tile-persistence shards; the downstream tile's shard is still written when it's spawned.
- [ADR-123](123-forward-link-phase-dependent-followons.md) — forward-linking a follow-on blocked on another already-existing issue's future phase; the complementary sibling rule for the "both already in flight, discovered dependent later" case, versus this ADR's "both about to be tiled in the same pass" case.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the repo, not memory.
- Motivating example: cover-letter-runtime #51 / #52 (Phase 2 decomposition sub-issues, 2026-08-10) — #52 states "Depends on 1/3" on #51; caught before either tile was spawned.
