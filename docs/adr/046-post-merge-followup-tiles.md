# ADR-046 — Post-Merge Follow-Up Tiles

**Date:** 2026-06-20
**Status:** Accepted
**Closes:** [dev-env#369](https://github.com/brownm09/dev-env/issues/369)
**Tags:** git-workflow, post-merge, follow-ups, spawn-task, tiles
**Related:** [ADR-012](012-post-merge-checklist-board-done-roadmap-update.md), [ADR-028](028-all-findings-merge-gate.md), [ADR-038](038-durable-preferences-documented-in-repo.md)

---

## Context

A merge is a session boundary. The work that led to it routinely surfaces follow-ups that are
deliberately out of scope for the PR just landed — a fix spotted in adjacent code, deferred work,
tech debt, or an idea worth pursuing. Once the session ends these evaporate: they live only in the
conversation, which is not durable, and re-deriving them later costs more than capturing them in the
moment.

The Claude Code harness already provides a mechanism built for exactly this: the `spawn_task` tool
surfaces a clickable "tile" (chip) that the user can spin into its own session + worktree with one
click, or dismiss. Its own guidance names good moments to use it — "right after verification passes,
or right before you summarize a completed task." The merge boundary is the same kind of moment, but
no workflow rule tied `spawn_task` to it, so the capture depended on ad-hoc judgment and was
inconsistent.

[ADR-012](012-post-merge-checklist-board-done-roadmap-update.md) established the post-merge checklist
(move the board item to Done; update the roadmap on work-stream completion). Capturing follow-ups is
the missing third post-merge action. Per
[ADR-038](038-durable-preferences-documented-in-repo.md), a standing rule like this must live in the
version-controlled repo, not only in agent memory.

## Decision

Add a global post-merge rule to `claude/CLAUDE.md` (Git Workflow), alongside the existing
board/roadmap post-merge actions:

> After `gh pr merge`, if the work surfaced any follow-ups, create a background-task tile for each
> via the `spawn_task` tool.

Three qualifications are part of the rule:

1. **Tiles are capture, not tracking.** A tile is ephemeral (chip IDs are not persisted across app
   restarts) and requires a user click to become real work. For a follow-up that must be durably
   tracked, still file a GitHub issue — the tile's spawned session is a natural place to do that. The
   tile and the issue are complementary, not redundant.
2. **Fallback where unavailable.** `spawn_task` is a harness tool not present in every session (e.g.
   some terminal CLI sessions). Where it is unavailable, file a follow-up issue instead, so the
   capture still happens.
3. **Genuine follow-ups only.** The bar is the same as the existing file-and-link guidance
   ([ADR-028](028-all-findings-merge-gate.md)): real, actionable, out-of-scope items — not
   speculative musings — to keep the tile surface signal-rich.

## Rationale

**Why the merge boundary.** It is the point where the just-completed work is freshest and the
out-of-scope items are most clearly identified, and it is a session boundary where context is about
to be lost. Capturing then maximizes recall and minimizes re-derivation cost.

**Why tiles rather than always filing issues.** Tiles are the lowest-friction capture the harness
offers and put the user in control (spin off or dismiss). Forcing a full `gh issue create` for every
passing thought would be heavy and would pollute the issue tracker with speculative entries; the tile
lets the user promote only the ones worth it. Durable items still get issues — the rule keeps both.

**Why a rule and not memory.** The capture must happen reliably across every session and every
project, including a fresh agent that never saw the conversation where the preference was stated.
ADR-038 is explicit that durable, cross-session preferences belong in the repo.

## Alternatives considered

- **Always file a GitHub issue for every follow-up.** Durable, but heavy and noisy — speculative
  ideas would clutter the tracker, and the friction discourages capture. Rejected in favor of tiles
  as the default, with issues reserved for items that need tracking.
- **Leave it to the existing `spawn_task` guidance / ad-hoc judgment.** That is the status quo, which
  produced inconsistent capture; the user asked to formalize the post-merge checkpoint. Rejected.
- **Record follow-ups only in agent memory.** Invisible to the user and other collaborators and not
  reliably consulted — directly contrary to ADR-038. Rejected.
- **An addendum to ADR-012 instead of a new ADR.** ADR-012 is specifically the board/roadmap
  checklist; tiles introduce a distinct mechanism and terminology with their own tradeoffs, which
  warrants its own record under this repo's one-decision-per-ADR convention. Captured here,
  cross-linked to ADR-012.

## Consequences

**Positive:**

- Follow-ups noticed at merge are captured consistently instead of silently dropped.
- The user stays in control — each tile is reviewed and either spun off or dismissed.
- Complements, rather than duplicates, the file-and-link issue flow and the ADR-012 board/roadmap
  actions.

**Negative / residual:**

- Tiles are ephemeral, so a follow-up captured only as a tile and then dismissed/lost is gone — the
  rule mitigates this by requiring an issue for anything that must be tracked, but the judgment of
  "must be tracked" rests with the agent/user.
- A small risk of tile clutter if the "genuine follow-ups only" bar is applied loosely.
- The rule is partly a behavioral convention (it asks the agent to notice and act at merge); it is
  documented but not mechanically enforced by a hook.

## References

- [dev-env#369](https://github.com/brownm09/dev-env/issues/369) — issue this ADR closes.
- [ADR-012](012-post-merge-checklist-board-done-roadmap-update.md) — the post-merge checklist this extends.
- [ADR-028](028-all-findings-merge-gate.md) — the file-and-link guidance the "genuine follow-ups" bar mirrors.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences must be documented in the repo, not only in memory.
- `spawn_task` (Claude Code background-task tool) — the harness mechanism that renders the tile/chip.
