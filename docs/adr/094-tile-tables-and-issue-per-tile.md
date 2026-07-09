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

## Addendum (2026-07-09): the enforcement hook lands ([#656](https://github.com/brownm09/dev-env/issues/656))

The Decision section forward-referenced #656 as filed-but-not-yet-built ("landing in #656, dormant until it merges" — `claude/CLAUDE.md`). This addendum records what actually landed: a **third**, fully independent trigger in `claude/scripts/stop-tile-enumeration-gate.py`, alongside the existing merged-PR ([ADR-088](088-state-keyed-tile-enumeration-gate.md)) and dangling-created-issue ([ADR-092](092-dangling-issue-tile-enumeration-gate.md)) triggers.

**Detection.** Two new pure helpers mirror the gate's existing shape:

- `session_spawned_tiles(records)` — True iff a real `spawn_task` tool call happened this session (checked via the same namespace-agnostic `_SPAWN_TASK_RE` regex against a `tool_use` item's `name`). The single source of truth: `enumeration_recorded` now delegates to it for its own tool_use check rather than re-implementing the same loop (a duplication an independent review pass on PR #674 flagged).
- `table_marker_present(records)` — True iff an **assistant** `text` item carries the stable heading, matched via `^#{1,6}\s*tiles\s+spawned\s+this\s+session` (case-insensitive, lenient on heading level 1–6, but **line-anchored** via `re.MULTILINE` so a mid-sentence mention of the phrase — this hook's own reminder text quoted back, or CLAUDE.md's rule text — is never mistaken for an actual emitted heading; a real markdown heading always starts its own line). Only `assistant` records are scanned, so a user message or a tool_result echoing the heading text can never satisfy it.

`evaluate_tile_table(records)` composes these exactly like `evaluate()`/`evaluate_issues()`: no spawn this session → `(False, False)` (nothing to resolve, so a tile spawned later is still caught); a spawn with the marker or a skip override present → `(False, True)` (resolved, sentinel set); a spawn with neither → `(True, False)` (fire).

**The key asymmetry with triggers (1)/(2).** A spawned tile satisfies `enumeration_recorded` — the same signal `evaluate()`/`evaluate_issues()` check — so it silently *resolves* the merged-PR and dangling-issue triggers. But it does **not**, by itself, satisfy trigger (3): the table marker is a stricter, independent bar. A session that merges a PR, spawns a tile, and never emits the table therefore sees trigger (1) resolve quietly while trigger (3) still fires and blocks the stop — this is intentional, not a bug: the whole point of ADR-094 is that a tile now needs the table, not just an enumeration. The reverse also holds: a merged PR with no tile spawned at all leaves trigger (3) a no-op (there is nothing to table).

**Pre-filter (and a fix from review).** `main()`'s cheap pre-filter (a `"merged"` substring check, extended by ADR-092 with a `gh issue create` regex search) gains a third OR-branch: a bare substring check for `spawn_task`. The PR as first opened instead checked the exact fully-qualified MCP tool name `mcp__ccd_session__spawn_task`, reasoning that it was empirically false-positive-free (a real transcript with zero tiles spawned contained the bare word 8× from prose/tool-result noise but the fully-qualified name 0×). Two independent review passes on PR #674 flagged the same soundness gap from different angles: the fully-qualified check is a *strict subset* of what `_SPAWN_TASK_RE` (the real detector, deliberately namespace-agnostic — "any namespacing hits") can match, so a spawn recorded under any other MCP namespace would satisfy the detector but be silently skipped by the narrower pre-filter, defeating the detector's own documented robustness. Not currently triggerable (every recorded spawn uses the standard prefix today) but a real, latent, cheaply-fixed gap — corrected to the bare substring, accepting the occasional extra reparse as the same bounded cost the pre-existing `"merged"` branch already carries. A dedicated regression test spawns a tile under a fabricated non-standard namespace and confirms both the pure detector and the full end-to-end hook still catch it.

**Combined-message behavior.** All three triggers share one `format_*_reminder` composition in `main()`: if multiple fire in the same session, their reminders are concatenated into a single exit-2 stderr write (unchanged from the two-trigger shape ADR-092 established) — a session that merges a PR, leaves an issue dangling, and spawns an un-tabled tile blocks once, naming all three.

**Test impact on two pre-existing e2e tests.** `test_e2e_merged_with_enum_allows` and `test_e2e_dangling_issue_with_enum_allows` previously used a bare `spawn_task` tile as their stand-in for "enumeration happened, so allow" and asserted exit 0. Once trigger (3) exists, that assumption no longer holds — a bare spawn alone now leaves trigger (3) unsatisfied. Both were extended to also emit the table heading, so the session they exercise is genuinely fully compliant; their original intent (proving triggers (1)/(2) resolve on enumeration) is preserved without weakening the new trigger's assertion.

**Limitations (documented, accepted — review of PR #674).** All three triggers share ONE once-per-session sentinel (per the original task's explicit instruction to reuse the gate's existing machinery), which extends ADR-088's already-documented "session-global enumeration" limitation across triggers rather than introducing a new class of gap: once *any* trigger fires or resolves, the sentinel suppresses re-evaluation of *all three* for the rest of the session — including a trigger whose own triggering condition (a later, separate tile spawn) hadn't even happened yet when the sentinel was set. Concretely: if a PR merges with no tile spawned yet, trigger (1) fires and sets the sentinel; if a tile is then spawned in a genuinely later, separate turn without ever emitting the table, trigger (3) never gets the chance to catch it. This does not affect the common compliant flow, where the reminder's own stderr text prompts the tile-and-table response inline within the same blocked-Stop continuation (`stop_hook_active` governs that case, checked before the sentinel). Splitting per-trigger sentinels would be a materially larger change to `main()`'s core once-per-session gating — shared by, and with correctness implications for, the two existing triggers — and was judged out of scope for a PR whose brief was explicitly "reuse the existing machinery." Tracked as a follow-up: [dev-env#677](https://github.com/brownm09/dev-env/issues/677).

Full detection/decision detail and the isolated interaction tests: `claude/scripts/tests/test_stop_tile_enumeration_gate.py` (dev-env `CLAUDE.md` → `## Testing` item 48) and `docs/REFERENCE.md`'s hook entry.

## References

- [dev-env#652](https://github.com/brownm09/dev-env/issues/652) — top-level issue; [#653](https://github.com/brownm09/dev-env/issues/653) — this PR; [#656](https://github.com/brownm09/dev-env/issues/656) — the enforcement hook (this addendum).
- [ADR-046](046-post-merge-followup-tiles.md) — the tiles-are-capture default this overrides.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md) — the tile-enumeration gate this table complements and whose `spawn_task` detection and `evaluate()`/`evaluate_issues()` shape the [#656](https://github.com/brownm09/dev-env/issues/656) trigger mirrors.
- [ADR-095](095-session-boundary-summaries-and-idle-refresher.md) — the sibling session-boundary decision.
- `spawn_task` / `dismiss_task` / `list_sessions` — the harness tile and session-management tools.
