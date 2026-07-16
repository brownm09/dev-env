# ADR-112 — Cross-Session Hand-Offs Are Tiles, Not Chat Briefs

**Date:** 2026-07-16
**Status:** Accepted
**Closes:** [dev-env#817](https://github.com/brownm09/dev-env/issues/817)
**Tags:** workflow, tiles, spawn-task, session-handoff, session-restart, blocked-worktree, plan-mode, claude-facing, global-rule, adr-046, adr-092, adr-094, adr-095, adr-107, adr-109
**Related:** [ADR-046](046-post-merge-followup-tiles.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-095](095-session-boundary-summaries-and-idle-refresher.md), [ADR-107](107-toolsearch-is-not-a-tool-availability-check.md), [ADR-109](109-tile-gate-deferral-question-trigger.md), [ADR-038](038-durable-preferences-documented-in-repo.md)

---

## Context

The tile-capture discipline ([ADR-046](046-post-merge-followup-tiles.md), [ADR-094](094-tile-tables-and-issue-per-tile.md)) exists so that genuine follow-up work is captured as a `spawn_task` tile — a one-click chip that spins the work off into its own session with a self-contained prompt — rather than left as an easily-lost note. The **Session Summaries & Tile Tracking** rules ([ADR-095](095-session-boundary-summaries-and-idle-refresher.md)) carve out one exception: *"merely list (don't tile) the immediate next steps of the task in progress."* That carve-out is right when the *same* session will keep going — tiling its own next keystroke would be noise.

But there is a failure mode the carve-out invites when it is over-read: work that genuinely cannot continue in the current session — because the environment is blocked, or because the user has decided to restart — gets treated as "the immediate next step of the task in progress" and handed off as a **chat brief** ("here's what's left; open a fresh session and paste this"), with a request that the user restart manually. That is exactly the case where a tile is *most* valuable and *least* costly: the `spawn_task` chip already carries the self-contained hand-off and starts the new session with one click, so it is strictly lower-friction than asking the user to restart-and-paste by hand.

### Motivating incident (2026-07-16, career-playbook)

A session running a Clarium interview assessment was in an orphaned worktree that blocked all file writes; plan mode also blocked repairing the worktree. When the user chose to "restart in a clean session," the assistant handed the remaining filing work off as a chat brief and did **not** spawn a tile. Its reasoning over-applied the ADR-095 carve-out — it treated the filing as the in-progress task being carried forward manually. But the current session was, by construction, *not* going to continue that work: it was blocked and ending. The remaining filing was therefore a genuine cross-session follow-up, and the chip would have restarted it in one click. The user corrected this and asked that the rule be written into the instructions.

This is adjacent to, but distinct from, [ADR-109](109-tile-gate-deferral-question-trigger.md)'s deferral-*question* anti-pattern (*asking* "should I do this now or in a fresh session" instead of tiling). Here the assistant did not ask a question — it decided to hand off and executed that hand-off as a brief plus a manual-restart request. Same root value (the chip is the low-friction path), different surface — and so the `stop-tile-enumeration-gate.py` deferral-question detector would not have caught it.

## Decision

Add a global workflow rule to `claude/CLAUDE.md`, in the **"Capture follow-ups as tiles"** bullet (its natural home) with a bounding pointer at the **"Close each substantive stop with a summary"** carve-out:

> A required cross-session hand-off is itself a tile, not a chat brief. When in-scope work cannot be finished in the current session — a blocked or corrupted environment (an orphaned or otherwise unusable worktree, a write-blocked cwd), a required session restart, a session boundary, or any "let's finish this in a fresh session" — spawn a `spawn_task` tile whose prompt is the self-contained hand-off, rather than leaving a paste-it-yourself brief in chat and asking the user to restart manually. The tile chip *is* the one-click restart, so it is strictly lower-friction than a manual restart-and-paste. The "merely list (don't tile) the immediate next steps of the task in progress" carve-out does **not** apply once the work must move to a *different* session — at that point it is a genuine cross-session follow-up, not a next step of the task in progress. The user choosing to "restart in a clean session" is a reason to tile the restart, never a reason to skip the tile; this holds even in plan mode when the user has asked for the hand-off, since spawning a capture chip is an inert proposal, not a mutation of the user's system.

Two edit sites, kept mutually consistent (the same two-place structure the file already uses for the "immediate next step" concept):

1. **`## Git Workflow` → "Capture follow-ups as tiles"** — the full rule, appended after the ADR-109 sentence.
2. **`## Session Summaries & Tile Tracking` → "Close each substantive stop with a summary"** — a bounding clause at the carve-out itself, so it can't be over-applied at its point of use.

## Rationale

- **The chip is the one-click restart.** A `spawn_task` tile *is* the mechanism for starting a fresh session with a self-contained prompt. A chat brief that asks the user to open a new session and paste context is a strictly worse version of the same thing — more steps, more friction, and the context can be lost between copy and paste. There is no case where "brief + manual restart" beats "tile" for genuine cross-session work.
- **The carve-out has a boundary.** ADR-095's "merely list the immediate next steps" applies to a session that will *continue*. Once the current session cannot continue the work — blocked, or deliberately ended by the user — the work is by definition not "in progress" here; it is a cross-session follow-up, exactly the shape ADR-046/094 already require to be tiled. This mirrors the existing clarification for a multi-PR initiative's "next unit" (once the current unit merges, the next unit is a genuine follow-up, "not the immediate next step of the task in progress").
- **Plan-mode-safe.** A capture chip is an inert proposal — it neither edits the user's files nor runs a command; the user chooses whether to click it. So even in plan mode, and even when the mode otherwise blocks mutations, spawning the hand-off tile the user has asked for is permitted (it is the low-friction ask, not an action on the system). This removes the one apparent reason a blocked or plan-mode session might skip the tile.
- **Durable, so it belongs in the repo.** This is a cross-session working-style rule — exactly what [ADR-038](038-durable-preferences-documented-in-repo.md) says must live in the version-controlled instructions, not agent memory. The rule was immortalized straight into `claude/CLAUDE.md`; the pairing issue is [dev-env#817](https://github.com/brownm09/dev-env/issues/817).

## Alternatives considered

- **Rely on ADR-109's deferral-question trigger.** Rejected — different failure mode. That trigger fires on *asking* a scheduling/permission question; the incident produced no question, just a brief and a manual-restart request. The deferral-question detector would not have caught it.
- **Record the rule in agent memory only.** Rejected per [ADR-038](038-durable-preferences-documented-in-repo.md) — memory is invisible to the user and not reliably consulted at the moment it matters.
- **A new enforcement hook.** Rejected as premature. "The session cannot continue" and "the user chose to restart" are hard to detect mechanically without high false-positive risk; this is a Claude-facing behavioral rule, like the ADR-095 summary rule, and starts as documentation. A hook can follow if the anti-pattern recurs.
- **A career-playbook-local note.** Assessed and rejected — the rule is universal with no career-playbook-specific deviation, and that repo's convention is to reference global dev-env rules rather than restate them. Global-only.

## Consequences

**Positive:** a blocked, corrupted, or deliberately-ended session hands its remaining in-scope work off via a one-click chip carrying self-contained context, instead of a brief the user must act on by hand; the ADR-095 carve-out gains an explicit boundary so it stops being over-applied across session lines.

**Negative / residual:**

- Like the sibling summary rule, this is a behavioral convention — its observance is not hook-verified. Whether a given hand-off "should have been a tile" is a judgment call.
- The rule adds another clause to an already-dense tile-capture bullet; the bounding pointer at the carve-out mitigates the risk that the full rule is read in only one place.

## References

- [dev-env#817](https://github.com/brownm09/dev-env/issues/817) — the immortalization issue this ADR closes.
- [ADR-046](046-post-merge-followup-tiles.md) — post-merge tile capture; the base tile discipline.
- [ADR-094](094-tile-tables-and-issue-per-tile.md) — issue-per-tile and the end-of-session tile table.
- [ADR-095](095-session-boundary-summaries-and-idle-refresher.md) — the session-summary rule and the "merely list the immediate next steps" carve-out this ADR bounds.
- [ADR-109](109-tile-gate-deferral-question-trigger.md) — the deferral-question trigger; the adjacent-but-distinct failure mode.
- [ADR-107](107-toolsearch-is-not-a-tool-availability-check.md) — a prior missed-`spawn_task`-tile incident.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the repo, not memory.
- Motivating incident: 2026-07-16 career-playbook Clarium interview-assessment session (orphaned worktree, hand-off as chat brief).
