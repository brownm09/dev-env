# ADR-137 — Proactive Tile Forward-Chaining for the Next High-Priority Thread (No Hard Dependency)

**Date:** 2026-08-19
**Status:** Accepted
**Closes:** [dev-env#1026](https://github.com/brownm09/dev-env/issues/1026)
**Tags:** tiles, spawn-task, forward-chaining, look-ahead, priority-sequencing, session-boundedness, workflow, global-rule, claude-facing, adr-046, adr-059, adr-071, adr-094, adr-113, adr-118, adr-123, adr-132
**Related:** [ADR-046](046-post-merge-followup-tiles.md), [ADR-059](059-multi-pr-issue-hierarchy.md), [ADR-071](071-canonical-checkout-mutate-guard-hook.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-113](113-cross-session-handoff-tiles.md), [ADR-118](118-tile-persistence-shards.md), [ADR-123](123-forward-link-phase-dependent-followons.md), [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md), [ADR-038](038-durable-preferences-documented-in-repo.md)

---

## Context

The tile-capture discipline ([ADR-046](046-post-merge-followup-tiles.md), extended by
[ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-113](113-cross-session-handoff-tiles.md),
[ADR-118](118-tile-persistence-shards.md), [ADR-123](123-forward-link-phase-dependent-followons.md),
[ADR-132](132-sequential-tile-spawning-for-dependency-chains.md)) directs Claude to spawn a
`spawn_task` tile for every genuine follow-up the moment it's identified. Two existing rules
touch what happens *after* a tile is spawned, and neither covers the gap this ADR closes:

- [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md) sequences tiles only when
  **two or more** follow-ups are being tiled in the **same enumeration pass** and have a genuine
  finish-to-start dependency — spawn only the head, and its prompt spawns the rest once its own
  PR merges.
- [ADR-123](123-forward-link-phase-dependent-followons.md) forward-links a follow-on that is
  blocked on **another already-existing issue's** future phase — the remedy is a
  forward-reference comment on the blocking issue, not a tile-prompt instruction.

Neither covers a **single** tile, spawned alone, with **no hard dependency** on what comes next,
whose own completion will nonetheless predictably surface or unblock a next high-priority
thread. Today nothing prompts checking for this at spawn time, so a tile's scope quietly ends at
whatever was identified when it was created — the "what's next after this lands" question either
goes unasked (the chain silently stops) or gets re-asked later by whoever picks up the thread,
who then has to reconstruct priority reasoning a prior session already had fresh in context.

### Motivating incident (cover-letter-runtime, 2026-08-18)

A session spawned a tile for [#93](https://github.com/brownm09/cover-letter-runtime/issues/93)+[#92](https://github.com/brownm09/cover-letter-runtime/issues/92)
(batch driver hardening: a surgical spend-recording fix, then a larger circuit-breaker design
pass, bundled and sequenced within one tile). Before spawning, that same session had already
ranked four candidate "what's next" threads via `AskUserQuestion` — #92/#93 (recommended), then
[#76](https://github.com/brownm09/cover-letter-runtime/issues/76)/[#77](https://github.com/brownm09/cover-letter-runtime/issues/77)
(verify-script cleanup), then #17 (Phase 1 live verification), then nothing. So the #2-ranked
thread (#76/#77) was already known and already judged lower-priority than #92/#93 but higher
than the rest, at the exact moment the #92/#93 tile was spawned — yet nothing in the tiling
discipline prompted carrying that ranking into the tile itself. Only a direct user follow-up
("are there any high-priority threads that could be picked up at the end of this work chain?
integrate follow-up into the tile") caught it; the session would not have surfaced it
unprompted.

The ad hoc fix applied in that session: dismiss the already-spawned tile, respawn it with an
appended instruction to re-verify #76/#77 live once #92/#93's own work merges and then spawn a
fresh follow-up tile for it — rather than continuing in the same session. That "rather than
continuing in the same session" instruction was itself motivated by a second, independent
observation: that same session had already grown past 430K context tokens earlier the same day,
from chaining two large PR cycles back-to-back in one continuous conversation. Keeping the next
thread's work in a freshly-spawned tile, rather than folding it into an already-large session,
avoids repeating that growth.

## Decision

Add a new paragraph to the **"Capture follow-ups as tiles"** bullet in `## Git Workflow` (the
same bullet [ADR-113](113-cross-session-handoff-tiles.md), [ADR-123](123-forward-link-phase-dependent-followons.md),
and [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md) each extended), appended
after ADR-132's paragraph as the bullet's newest, most-recent extension:

> **Every tile spawn is also a look-ahead moment for what comes after it, not only a capture
> moment for what's already known.** Before finalizing any tile's `spawn_task` prompt, ask
> whether that tile's own completion will predictably surface or unblock a next high-priority
> thread — even with no hard finish-to-start dependency on it (the next thread doesn't need this
> tile's code, schema, or artifact to start; it would just be the natural next thing worth doing
> once this one lands). When the answer is yes and the next thread is in the same subsystem,
> small, and genuinely bounded in scope, fold it into the same tile as explicitly-sequenced
> bundled work, the same way a tile can sequence a smaller fix ahead of a larger design pass
> within itself. Otherwise — a different subsystem, or work that would meaningfully grow the
> spawned session's own context — do not bundle it in; instead add an explicit instruction to
> the tile's own prompt: once its work merges, re-verify the next thread is still open and
> unaddressed (state may have drifted) and, if so, spawn a new, separate follow-up tile for it —
> with its own paired tracking issue and shard, per the discipline above — rather than
> continuing in the same session. Name the specific next issue(s) in the prompt now, while the
> priority reasoning is fresh, rather than leaving "what's next" for the chained session to
> rediscover from scratch. This is a proactive step for every tile spawn, independent of whether
> multiple follow-ups happen to be enumerated together — that narrower same-pass,
> hard-dependency case is [ADR-132](../docs/adr/132-sequential-tile-spawning-for-dependency-chains.md)'s.
> ([ADR-137](../docs/adr/137-proactive-tile-forward-chaining.md), extending ADR-132's "spawn
> only the head of the chain" mechanism to the no-hard-dependency case; motivating incident:
> cover-letter-runtime, 2026-08-18 — a tile spawned for [#93](https://github.com/brownm09/cover-letter-runtime/issues/93)+[#92](https://github.com/brownm09/cover-letter-runtime/issues/92)
> left out an already-ranked, already-known second-priority thread ([#76](https://github.com/brownm09/cover-letter-runtime/issues/76)/[#77](https://github.com/brownm09/cover-letter-runtime/issues/77))
> the same session had surfaced minutes earlier via `AskUserQuestion`; only a direct user
> follow-up asking whether a high-priority thread should be integrated into the tile caught the
> gap.)

**One edit site.** Following ADR-123 and ADR-132's precedent, this is appended to the existing
"Capture follow-ups as tiles" bullet rather than introduced as a new bullet — it does not narrow
any existing carve-out (unlike ADR-113, which needed a second site to bound the ADR-095 "merely
list, don't tile" exception), so a single addition suffices.

## Rationale

- **Priority reasoning computed fresh at spawn time is cheap to carry forward, expensive to
  reconstruct later.** The motivating incident shows this directly: the #2-ranked thread was
  already known and already ranked at the moment the tile was spawned. Naming it in the tile's
  own prompt costs a sentence; reconstructing it later costs a repeat of the same ranking
  exercise, by someone (or some session) without the context that produced it the first time.
- **Session-boundedness.** Chaining the next thread via a newly-spawned tile — rather than
  continuing the current session — keeps each session's context from growing unbounded,
  mirroring [ADR-113](113-cross-session-handoff-tiles.md)'s cross-session hand-off principle ("a
  required cross-session hand-off is itself a tile, not a chat brief") and directly answering
  the 430K-token growth the motivating incident's session had already exhibited earlier the same
  day.
- **Composes with, rather than overlaps, [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md).**
  The two rules answer different questions and can both apply to the same tile: ADR-132 decides
  *whether to spawn a downstream tile now at all* (deferred if a hard dependency exists); this
  ADR decides *whether to tell an already-being-spawned tile to look ahead and chain forward*
  (regardless of whether that same tile is itself the head of an ADR-132 chain). A chain-head
  tile spawned under ADR-132's rule can carry an ADR-137 look-ahead instruction in the same
  prompt without conflict.
- **Complementary to, not overlapping with, [ADR-123](123-forward-link-phase-dependent-followons.md).**
  ADR-123's remedy (a forward-reference comment) applies when the next thread's blocking issue
  *already exists and is being worked independently* — discoverability from the issue graph is
  the gap it closes. This ADR's remedy (a tile-prompt instruction) applies when the next thread
  is not blocked at all, merely *deprioritized relative to the tile being spawned right now* —
  continuity of priority reasoning across a session boundary is the gap it closes.
- **Durable, so it belongs in the repo.** Per [ADR-038](038-durable-preferences-documented-in-repo.md),
  a cross-session workflow rule like this must live in the version-controlled instructions, not
  only in agent memory.

## Alternatives considered

- **Rely on the existing tile-table / "merely list (don't tile) the immediate next steps of
  the task in progress" carve-out** (the `## Session Summaries & Tile Tracking` section in
  `claude/CLAUDE.md`).
  Rejected — that carve-out covers immediate next steps of work already in progress *in the
  current session*; the next thread here only becomes actionable after the tile being spawned
  right now runs its own future session and merges its own PR. That is a cross-session hand-off,
  which [ADR-113](113-cross-session-handoff-tiles.md) already treats as tile-worthy, not
  chat-list-worthy.
- **Fold this into [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md) as an
  amendment instead of a new ADR.** Considered and rejected. [ADR-071](071-canonical-checkout-mutate-guard-hook.md)'s
  six amendments each justify themselves the same way, in their own words — e.g. Amendment 1's
  "a previously-unrecognized command surface reaching that already-decided harm model,"
  Amendment 4's "a previously-unrecognized *tool* surface... reaching that already-decided harm
  model," Amendment 5's "a previously-unrecognized *shape* for a worktree cwd." Distilled, the
  pattern is: same
  file, same mechanism, same harm model, only a previously-unrecognized surface, shape, or signal
  newly recognized — never a change to what is being decided or why. This rule fails that bar on
  two axes: the **trigger condition**
  differs (ADR-132 fires only when two-or-more follow-ups are enumerated together in the same
  pass with a genuine finish-to-start dependency; this rule fires on every single tile spawn,
  unconditionally, specifically for the *no*-hard-dependency case), and the **harm model**
  differs (ADR-132 prevents a downstream session from starting before its hard prerequisite
  exists; this rule prevents priority context from being silently dropped across a session
  boundary when no such prerequisite exists at all). ADR-132's own "Alternatives considered"
  rejected folding into [ADR-059](059-multi-pr-issue-hierarchy.md) for the identical reason —
  "a narrow, separable refinement... not a change to the... structure [the earlier ADR]
  defines" — and that reasoning applies here in reverse: this is a narrow, separable refinement
  to the tile-capture bullet in its own right, not a gap in ADR-132's specific mechanism.
  [ADR-123](123-forward-link-phase-dependent-followons.md) independently arrived at the same
  new-ADR-over-amendment outcome for its own extension of this bullet, though its own
  Alternatives-considered section does not run this exact analysis explicitly — the "ADR-113 and
  ADR-123 both did this" framing lives in ADR-132's text, not in ADR-123's own.
- **Always bundle the next thread into the same tile, regardless of subsystem or size.**
  Rejected — this would grow spawned sessions unboundedly and risk reproducing the same
  context-bloat problem (430K tokens from chaining two large PR cycles in one session) the
  motivating incident already exhibited.
- **A mechanical hook enforcing the look-ahead check.** Rejected as premature, mirroring
  [ADR-123](123-forward-link-phase-dependent-followons.md) and
  [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md)'s identical hook-deferral
  reasoning: "will this tile's completion predictably surface or unblock a next high-priority
  thread" is a semantic priority judgment, not a pattern a hook can reliably detect. A hook can
  follow later if the anti-pattern recurs despite the documented rule.

## Consequences

**Positive:** a chain of naturally-related work no longer silently ends at the first tile's
merge; priority reasoning captured live at spawn time is preserved in the next tile's prompt
instead of being reconstructed later by whoever picks up the thread; sessions stay bounded,
since a chained continuation is a new tile/session rather than an extension of the one just
spawned.

**Negative / residual:**

- Like its sibling tile rules, this is a behavioral convention, not mechanically enforced —
  whether a next thread is "predictably" surfaced or unblocked, and whether it's "high-priority"
  enough to name, is a judgment call, same as ADR-123's "phase-dependent" judgment and ADR-132's
  "genuine finish-to-start dependency" judgment.
- Adds another clause to an already-dense bullet, accepted for the same reason
  ADR-113/ADR-123/ADR-132 accepted it: a single edit site is cheaper than a fragmented one.
- A named "next issue" can go stale if its state drifts between when the spawning tile is
  created and when its own work merges — mitigated by the rule's explicit "re-verify the next
  thread is still open and unaddressed" instruction, which the chained tile's prompt carries out
  live rather than trusting the original snapshot.

## References

- [dev-env#1026](https://github.com/brownm09/dev-env/issues/1026) — the immortalization issue
  this ADR closes.
- [ADR-046](046-post-merge-followup-tiles.md) — the base post-merge tile capture discipline.
- [ADR-094](094-tile-tables-and-issue-per-tile.md) — issue-per-tile and the tile table; the
  chained follow-up tile still gets both when it's eventually spawned.
- [ADR-113](113-cross-session-handoff-tiles.md) — the prior extension of this same "Capture
  follow-ups as tiles" bullet; template for placement and the cross-session hand-off principle
  this ADR's session-boundedness rationale mirrors.
- [ADR-118](118-tile-persistence-shards.md) — tile-persistence shards; the chained follow-up
  tile's shard is still written when it's spawned.
- [ADR-123](123-forward-link-phase-dependent-followons.md) — forward-linking a follow-on blocked
  on another already-existing issue's future phase; the complementary sibling rule for the
  "already blocked, discoverability" case, versus this ADR's "not blocked, priority continuity"
  case.
- [ADR-132](132-sequential-tile-spawning-for-dependency-chains.md) — sequential tile spawning
  for same-pass, hard finish-to-start dependencies; this ADR extends its "spawn only the head
  of the chain" mechanism to the no-hard-dependency, single-tile case, and composes with it
  rather than overlapping.
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the amendment-vs-new-ADR litmus test
  this ADR's Alternatives-considered section applies.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the
  repo, not memory.
- Motivating incident: cover-letter-runtime, 2026-08-18 — tile spawned for
  [#93](https://github.com/brownm09/cover-letter-runtime/issues/93)/[#92](https://github.com/brownm09/cover-letter-runtime/issues/92)
  omitted an already-ranked #2-priority thread
  ([#76](https://github.com/brownm09/cover-letter-runtime/issues/76)/[#77](https://github.com/brownm09/cover-letter-runtime/issues/77)),
  caught only by direct user follow-up.
