# ADR-095 — Session-Boundary Summaries and Idle Refresher

**Date:** 2026-07-09
**Status:** Accepted
**Closes:** part of [dev-env#652](https://github.com/brownm09/dev-env/issues/652) (via [#653](https://github.com/brownm09/dev-env/issues/653))
**Tags:** workflow, session-boundary, summaries, idle-refresher, hooks, UserPromptSubmit, claude-facing, context, adr-027, adr-046, adr-094
**Related:** [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md), [ADR-046](046-post-merge-followup-tiles.md), [ADR-094](094-tile-tables-and-issue-per-tile.md)

---

## Context

A session boundary — finishing a task, pausing for input, or the user returning after time away — is where the user most needs orientation, and where the agent is most prone to drop it. Two gaps:

1. A stop often ends with the work done but no crisp recap of what changed, what (if anything) is being asked of the user, and what is left. The user has to reconstruct it from the preceding tool calls and prose.
2. When the user steps away and returns an hour later, they have lost the thread and want a refresher — but the agent resumes mid-stream as if no time had passed.

The user asked for both: a summary at every (substantive) stop, and a refresher when returning after a long idle gap ("at which point I will need a refresher").

## Decision

Two behavioral rules in global `claude/CLAUDE.md` (new **Session Summaries & Tile Tracking** section), scoped globally:

1. **Substantive-stop summary.** At each stop that follows real work or leaves something for the user, close with three parts: **Completed**, **Context / ask** (what's being asked now, if anything — each with its exact path/URL per *User-Actionable References*), and **Remaining** (outstanding to-dos; the genuine out-of-scope follow-ups among them are tiled per *Capture follow-ups as tiles* — deduplicated against tiles/issues already created this session, so a multi-stop session doesn't re-file the same follow-up — while immediate next steps are merely listed). **Skip on trivial exchanges** — a greeting, a one-line acknowledgment, a single clarifying question.
2. **Idle refresher.** On the user's return after an idle gap exceeding a threshold (default 60 min), lead the reply with a brief refresher — what we were working on, current state, pending to-dos/tiles — before addressing the new prompt. This is **hook-driven**: the agent cannot observe elapsed idle time, so a `UserPromptSubmit` hook (`idle-refresher.py`, filed as [#655](https://github.com/brownm09/dev-env/issues/655)) measures the gap from the transcript and injects the cue via `additionalContext`. Threshold is per-project–overridable (`idle_refresher_minutes` in `.claude/hook-config.json`).

## Rationale

- **Why substantive-only.** A summary on a greeting or one-line ack is noise; the carve-out mirrors the session-mode preamble's "skip trivial prompts" judgment ([ADR-027](027-userpromptsubmit-blocking-hook-conventions.md)).
- **Why a hook for the refresher.** Idle time is not observable to the model — only the harness/transcript knows the wall-clock gap. A `UserPromptSubmit` hook is the same injection mechanism `session-mode-prompt.py` uses (`additionalContext`, exit 0). A *truly proactive* push would need a scheduled wake that can fire into an empty room and desync from the user; the return-triggered refresher is reliable and matches the user's framing.
- **Why global.** The user framed it as "whenever a session stops" — a universal working-style preference. [ADR-038](038-durable-preferences-documented-in-repo.md) requires durable, cross-session preferences to live in the repo instructions, not memory.

## Alternatives considered

- **Summary on literally every stop.** Rejected as noisy for trivial exchanges; the user accepted substantive-only.
- **Refresher via a proactive scheduled push.** Heavier, fires with no user present, can desync from actual return; rejected in favor of the return-triggered `UserPromptSubmit` cue.
- **Record the preference in agent memory only.** Invisible to the user and not reliably consulted — contra [ADR-038](038-durable-preferences-documented-in-repo.md).

## Consequences

**Positive:** consistent orientation at every boundary; less user reconstruction; graceful re-entry after a break.

**Negative / residual:**

- The summary is a behavioral convention — its *content* cannot be hook-verified, only its presence heuristically. The sibling tile-table has a targeted enforcement hook ([ADR-094](094-tile-tables-and-issue-per-tile.md)); the free-form summary does not.
- The idle refresher depends on the `idle-refresher.py` hook ([#655](https://github.com/brownm09/dev-env/issues/655)) to fire at all; until that lands, the rule is dormant (no cue → no refresher), not wrong.
- Threshold tuning is per-project; a too-low value nags, a too-high one misses short breaks.

## References

- [dev-env#652](https://github.com/brownm09/dev-env/issues/652) — top-level issue; [#653](https://github.com/brownm09/dev-env/issues/653) — this PR; [#655](https://github.com/brownm09/dev-env/issues/655) — the idle-refresher hook.
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — UserPromptSubmit conventions and the trivial-prompt carve-out this reuses.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the repo, not memory.
- [ADR-046](046-post-merge-followup-tiles.md) — post-merge tile capture; [ADR-094](094-tile-tables-and-issue-per-tile.md) — the sibling tile-table / issue-per-tile decision.
- `session-mode-prompt.py` — the `additionalContext` injection pattern the idle-refresher reuses.
