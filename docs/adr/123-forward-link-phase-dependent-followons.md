# ADR-123 — Forward-Link Phase-Dependent Follow-Ons FROM the Blocking Open Issue

**Date:** 2026-07-27
**Status:** Accepted
**Closes:** [dev-env#913](https://github.com/brownm09/dev-env/issues/913)
**Tags:** workflow, tiles, spawn-task, follow-ons, forward-reference, issue-graph, phase-dependent, discoverability, global-rule, claude-facing, adr-046, adr-059, adr-094, adr-118
**Related:** [ADR-046](046-post-merge-followup-tiles.md), [ADR-059](059-multi-pr-issue-hierarchy.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-118](118-tile-persistence-shards.md), [ADR-113](113-cross-session-handoff-tiles.md), [ADR-038](038-durable-preferences-documented-in-repo.md)

---

## Context

The tile-capture discipline ([ADR-046](046-post-merge-followup-tiles.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-118](118-tile-persistence-shards.md)) requires that a genuine follow-up be captured three ways: a `spawn_task` tile (the one-click chip), a paired GitHub *tracking issue* (the durable anchor), and a per-tile *shard* on disk (the durable payload that lets a lost chip be re-spawned exactly). Every one of those channels points **from the follow-on back to its context** — the tile prompt and the issue body name the parent work, and the shard is keyed by the follow-on's own issue.

A distinct case is not covered by any of them: a follow-on whose trigger is **another open issue reaching a future phase or completing**. Here the follow-on is not merely "related to" a parent — it is *blocked on* it, and it becomes actionable only when that other issue advances. The person (or session) who will naturally act is whoever is working the *blocking* issue and arrives at the triggering phase. But nothing points **forward** from the blocking issue to the follow-on, so at exactly the moment the follow-on becomes actionable, its existence is invisible from the place someone is looking.

The existing durable artifacts do not close this gap:

- **The tile chip is ephemeral.** It does not survive an app restart, and it is never visible to someone reading an issue.
- **The shard is *extra-issue* evidence.** [ADR-118](118-tile-persistence-shards.md) persists the tile payload to `sessions/<project>/tiles/<N>.json`, which survives a restart — but it is a file in the engineering-journal repo that can be lost, and that nobody working the *parent* issue naturally encounters. It solves "the chip evaporated on restart," not "find this from the blocking issue."
- **GitHub's auto-generated back-reference is weak for this purpose.** When the follow-on's issue mentions the blocking issue, GitHub adds a "mentioned this issue" timeline entry on the blocking issue. That is a genuine forward link, but it is easy to miss in a long timeline and — critically — it states no **trigger condition**. It says *that* another issue mentioned this one, never *"action this when phase X arrives."*

The **issue graph is the durable, canonical, human-navigable discovery surface** — the one artifact that outlives sessions, chips, and shard files, and that a person actually reads while working an issue. A phase-dependent follow-on should therefore be discoverable *from the blocking issue itself*, with its trigger stated, independent of whether the chip or shard still exists.

### Motivating first instance (2026-07-27, career-playbook)

career-playbook #904 (an active G3 throughline-admission reason) depends on career-playbook #810 reaching Phase 3. When this was noticed, #810 was given a comment headed **"Follow-ons discoverable from this issue (forward-links)"** that reads, in part: *"Recorded here so they surface from #810 itself when this loop reaches the relevant phase — independent of any tile chip (ephemeral) or shard file (extra-issue evidence)."* It lists the dependent follow-ons (#903 / PR #905, #904) with their trigger conditions. That comment is the concrete realized form of the rule this ADR immortalizes, and writing it is what prompted [dev-env#913](https://github.com/brownm09/dev-env/issues/913).

## Decision

Add a global workflow rule to `claude/CLAUDE.md`, appended to the **"Capture follow-ups as tiles"** bullet in `## Git Workflow` (its natural home — the same bullet [ADR-113](113-cross-session-handoff-tiles.md) extended):

> When a follow-on — a `spawn_task` tile, or any deferred work item — is **blocked on another open issue's future phase or completion**, the *blocking issue itself* must, in the same session, carry a **forward-reference** to the follow-on: a "Follow-ons" note, a checklist item, or a linked comment **stating the trigger condition** (e.g. *"actionable once #905 merges and #810 reaches Phase 3"*), not only a back-reference from the follow-on to its parent. This is **in addition to** the existing back-reference discipline (issue-per-tile [ADR-094], the tile shard [ADR-118], and the parent reference in the follow-on's own body/prompt). It **complements — does not duplicate — the shard mechanism**: the shard survives an app restart via a file on disk (extra-issue evidence that can be lost and that nobody working the parent naturally encounters), whereas the forward-reference survives via the issue graph — the durable, human-navigable discovery surface — so whoever works the blocking issue and reaches the triggering phase finds the follow-on *from that issue alone*, regardless of whether the chip or shard still exists.

**One edit site.** Unlike [ADR-113](113-cross-session-handoff-tiles.md) — which added a second bounding pointer at a carve-out it *narrowed* — this rule *adds* a requirement and has no "over-application at point of use" risk, so a single edit in the "Capture follow-ups as tiles" bullet suffices. The [ADR-059](059-multi-pr-issue-hierarchy.md) "Multi-PR decomposition" bullet is deliberately left untouched; the extension relationship is recorded here rather than by inlining a pointer into a second dense bullet.

## Rationale

- **The issue graph is the surface that actually gets read at the triggering moment.** The actor who resolves a phase-dependent follow-on is whoever is working the *blocking* issue when it reaches the trigger phase. A back-reference only helps someone who already knows the follow-on exists and is looking at *it*; the forward-reference reaches the person looking at the *blocking* issue, which is where the trigger is observed. Discovery has to live where the trigger is seen.
- **It states the trigger, which nothing else does.** The whole point is temporal: "do this *when* phase X arrives." A chip carries no schedule, a shard carries no schedule, and GitHub's auto back-reference carries no schedule. Only an author-written forward-reference records *when* the follow-on becomes actionable.
- **It complements, not duplicates, the shard.** [ADR-118](118-tile-persistence-shards.md) already defends against "the chip evaporated on restart" with an on-disk payload. This defends against a different loss — "the follow-on is invisible from the place its trigger is observed" — using a different, independently-durable channel (the issue graph). Losing the shard file does not lose the forward-reference, and vice-versa; the two are belt-and-suspenders over distinct failure modes.
- **It extends [ADR-059](059-multi-pr-issue-hierarchy.md)'s "navigable from both directions."** ADR-059 established bidirectional navigability for *decomposition-time* hierarchy: a top-level issue links its sub-issues and each PR links its sub-issue. This rule carries the same principle to *temporally phase-dependent* follow-ons discovered *later* — the dependency is not known at decomposition time but surfaces mid-work, and the same "reachable from both ends" property must still hold.
- **Durable, so it belongs in the repo.** This is a cross-session working-style rule — exactly what [ADR-038](038-durable-preferences-documented-in-repo.md) says must live in the version-controlled instructions, not agent memory. The rule was immortalized straight into `claude/CLAUDE.md`; the pairing issue is [dev-env#913](https://github.com/brownm09/dev-env/issues/913).

## Alternatives considered

- **Rely on the shard alone ([ADR-118](118-tile-persistence-shards.md)).** Rejected — the shard is extra-issue evidence: a file that can be lost and that nobody working the *parent* issue encounters. It restores a lost chip; it does not make the follow-on discoverable from the blocking issue when the phase arrives.
- **Rely on GitHub's auto-generated "mentioned this issue" back-reference.** Rejected — it is easy to miss in a long timeline and states no trigger condition. It records *that* a mention happened, never *when* to act.
- **Record the dependency in agent memory only.** Rejected per [ADR-038](038-durable-preferences-documented-in-repo.md) — memory is invisible to the user and to any other session, and is not reliably consulted at the moment it matters (here, the arrival of the trigger phase, possibly sessions later).
- **A new enforcement hook.** Rejected as premature, mirroring [ADR-113](113-cross-session-handoff-tiles.md)'s hook rejection. "This follow-on is *phase-dependent* on another open issue" is a semantic judgment that is hard to detect mechanically without high false-positive risk; this is a Claude-facing behavioral rule that starts as documentation. A hook can follow if the anti-pattern recurs.

## Consequences

**Positive:** a phase-dependent follow-on is re-discoverable — and, because the tracking issue + shard still exist, re-spawnable — from the issue graph alone, at the moment its trigger phase arrives, independent of the chip or shard file. The bidirectional-navigability property of [ADR-059](059-multi-pr-issue-hierarchy.md) now also holds for follow-ons discovered after decomposition time.

**Negative / residual:**

- Like the sibling tile rules, this is a behavioral convention — its observance is not hook-verified. Whether a given follow-on is "phase-dependent on another open issue" (and therefore owes a forward-reference) is a judgment call.
- It adds another clause to an already-dense tile-capture bullet. Kept to a single edit site to bound that cost.
- The forward-reference is authored once, at capture time; if the trigger condition later changes, the note can go stale. This is the normal cost of any human-authored cross-reference and is accepted.

## References

- [dev-env#913](https://github.com/brownm09/dev-env/issues/913) — the immortalization issue this ADR closes.
- [ADR-046](046-post-merge-followup-tiles.md) — post-merge tile capture; the base tile discipline.
- [ADR-059](059-multi-pr-issue-hierarchy.md) — multi-PR hierarchy and "navigable from both directions"; this ADR extends that principle to phase-dependent follow-ons.
- [ADR-094](094-tile-tables-and-issue-per-tile.md) — issue-per-tile (the durable anchor) and the end-of-session tile table.
- [ADR-118](118-tile-persistence-shards.md) — tile-persistence shards (the durable payload); this ADR complements it with the issue-graph channel.
- [ADR-113](113-cross-session-handoff-tiles.md) — the prior extension of this same "Capture follow-ups as tiles" bullet; template for placement and hook-deferral reasoning.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the repo, not memory.
- Motivating first instance: 2026-07-27 career-playbook #904 → #810 forward-reference comment ("Follow-ons discoverable from this issue (forward-links)").
