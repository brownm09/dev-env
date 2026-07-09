# ADR-094 — Tile Tables and Issue-Per-Tile

**Date:** 2026-07-09
**Status:** Accepted
**Closes:** part of [dev-env#652](https://github.com/brownm09/dev-env/issues/652) (via [#653](https://github.com/brownm09/dev-env/issues/653))
**Tags:** tiles, spawn-task, issue-per-tile, session-boundary, tracking, tile-table, adr-046, adr-088, adr-092, adr-095
**Related:** [ADR-046](046-post-merge-followup-tiles.md), [ADR-088](088-state-keyed-tile-enumeration-gate.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md), [ADR-095](095-session-boundary-summaries-and-idle-refresher.md)

---

## Context

[ADR-046](046-post-merge-followup-tiles.md) established tiles (`spawn_task` chips) as low-friction, **ephemeral** follow-up capture — deliberately *not* issues, to avoid tracker noise, with an issue filed only for a follow-up that "must be tracked." Three harness facts constrain what a tile can offer downstream: chip IDs are not persisted across app restarts, there is no URL that links to a spawned session, and no non-destructive API reports whether a chip was clicked into a session (`dismiss_task` reveals it only by consuming it).

The user wants, at session end, a **table of the tiles spawned that session**, with durable clickable links and a real status column telling them "which tiles have already been spawned into sessions." The ephemeral-chip model cannot supply durable links or persistent status on its own.

## Decision

Two coupled rules in global `claude/CLAUDE.md` (**Session Summaries & Tile Tracking** section):

1. **Issue-per-tile.** When spawning a genuine `spawn_task` tile, also file a tracking issue for it in the same repo, referenced in the tile prompt. This **deliberately overrides** ADR-046's "not every tile is an issue" default. The issue supplies the durable link and open/closed status the chip cannot.
2. **End-of-session tile table.** Whenever one or more tiles were spawned this session, close with a table under the stable heading **`### Tiles spawned this session`**: `Tile | Issue | Status | Next`. **Status** = the issue's open/closed state plus a *best-effort* "started" note derived from `list_sessions` (title/branch/PR match). The live chip stays the one-click "start the session" control; the issue is the durable anchor if the chip is dismissed or lost.

A targeted `Stop` hook (filed as [#656](https://github.com/brownm09/dev-env/issues/656), extending `stop-tile-enumeration-gate.py`) reminds when tiles were spawned this session but no table marker was emitted.

## Rationale

- **Why override ADR-046.** The user explicitly chose durable links + status over lightweight ephemeral capture, accepting the issue-tracker-noise tradeoff — it is their tracker. ADR-046's "genuine follow-ups only" bar still bounds the volume, so the override widens *which* tiles get an issue (all genuine ones), not the bar for what counts as a tile.
- **Why the issue supplies status.** It is the only durable, queryable (`gh issue view`) anchor that survives across sessions and app restarts; chip IDs do not. `list_sessions` adds a heuristic "started" signal but cannot stand alone (title-match only, and the user may not have clicked yet when the table is emitted).
- **Why a stable heading marker.** The enforcement hook must detect "table emitted" from a transcript scan; a fixed heading is the cheapest robust signal — the same shape as the enumeration-text markers [ADR-088](088-state-keyed-tile-enumeration-gate.md)'s gate already scans for.
- **No loop with [ADR-092](092-dangling-issue-tile-enumeration-gate.md).** The dangling-created-issue trigger is satisfied session-globally as soon as any `spawn_task` runs (`enumeration_recorded`), so the tile-tracking issues this rule files never re-trigger "tile this issue."

## Alternatives considered

- **Keep tiles ephemeral (task_id record only).** The lightweight option the user rejected; no durable links or status.
- **Best-effort live status via `list_sessions` only, no issues.** Partial and heuristic (title match), with no durable open/closed state that survives the session.
- **Link to the spawned session directly.** No such URL/API is exposed; the chip is the control, so there is nothing stable to link.

## Consequences

**Positive:** every genuine tile is durably linkable and status-trackable; the end-of-session table answers "which follow-ups are still open / already started" at a glance.

**Negative / residual:**

- More `gh issue create` calls, and per-project board overhead where a project auto-boards new issues (e.g. dev-env project #3 requires Impact/Why).
- Softens ADR-046's deliberate tile≠issue separation — accepted as an explicit, user-chosen override, recorded here.
- "Started" status is best-effort (no per-chip API); the table is honest about this rather than implying live tracking.

## References

- [dev-env#652](https://github.com/brownm09/dev-env/issues/652) — top-level issue; [#653](https://github.com/brownm09/dev-env/issues/653) — this PR; [#656](https://github.com/brownm09/dev-env/issues/656) — the enforcement hook.
- [ADR-046](046-post-merge-followup-tiles.md) — the tiles-are-capture default this overrides.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md) — the tile-enumeration gate this table complements and whose `spawn_task` detection the [#656](https://github.com/brownm09/dev-env/issues/656) hook reuses.
- [ADR-095](095-session-boundary-summaries-and-idle-refresher.md) — the sibling session-boundary decision.
- `spawn_task` / `dismiss_task` / `list_sessions` — the harness tile and session-management tools.
