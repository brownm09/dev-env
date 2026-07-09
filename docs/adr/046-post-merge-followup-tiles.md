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
- The checkpoint can be waived only by an explicit user instruction anywhere in the current session
  (e.g. "skip tiles"). Planning artifacts — plan files, session notes, carry-over context — cannot
  override it, even when they contain explicit deferral notes for a later merge.

## Addendum — 2026-06-27: plan approval is not an override

**Incident.** career-playbook PR #537 (Upstart SEM Cash Line) merged. Three follow-up PRs (#541, #542, #543) were identified during the session. The agent continued in-session on those PRs without spawning tiles, reasoning that an approved plan already scoped the follow-up work. Mike flagged this as the wrong behavior.

**Clarification.** The existing rule addresses deferred planning artifacts ("plan files, session notes, carry-over context"). An approved in-session plan is different in character — it is an active user action — but it is still not an explicit instruction *about the tile checkpoint itself*. The principle is:

> The tile checkpoint fires unconditionally after every `gh pr merge`. If follow-up work is already in-session or in an approved plan, spawn the tile anyway. The spawned sessions can be dismissed if the work is already underway. The tile is cheap; missing the capture is not.

The only valid override remains a direct verbal instruction that names the tile step: "skip tiles", "don't spawn tiles", or equivalent. Plan approval — even when the approved plan explicitly scopes the follow-up work — does not qualify.

**CLAUDE.md update.** The override sentence in `claude/CLAUDE.md` was amended to add: "Plan approval is not that instruction: even when the approved plan explicitly scopes the follow-up work in-session, the tile checkpoint still fires; spawn the tiles and let the user dismiss any whose work is already underway." Closes [dev-env#413](https://github.com/brownm09/dev-env/issues/413).

## Addendum — 2026-07-05: forcing-function refinement (enumeration + merged-state re-key)

**Incident.** lifting-logbook PR #700 (settings hub, closes #679, merged 2026-07-05). In a single message the session flagged that an idle worktree was now removable *and* asserted "the finalization work surfaced no new follow-ups" — spawning no tile. The user had to prompt twice ("Where's the tile, then?", then "why do I keep having to ask?"). **Auto-merge** had landed the PR while the agent was away, so the literal "after `gh pr merge`" trigger never fired and post-merge was pure bookkeeping — the checkpoint's salience was already lowest exactly when it should have been highest.

**Three mechanisms** ([dev-env#595](https://github.com/brownm09/dev-env/issues/595)):

1. **Discretionary trigger, no forcing function.** The rule fired "*if* the work surfaced any follow-ups." That `if` is a self-assessment made when the agent is most motivated to be done, and completion-bias reliably resolves it to "none" as a bare, unexamined assertion — nothing forced an enumeration of candidates before concluding zero.
2. **No required artifact, no hook, so a skip is invisible.** Contrast the review-findings gate: the agent MUST write a "Review findings disposition" section and `pre-merge-findings-gate` blocks the merge without it. A skipped tile checkpoint left no trace — the only detector was the user noticing the absence.
3. **Trigger keyed to the `gh pr merge` command.** When a PR lands via **auto-merge** (GitHub merges it server-side — the #700 case) or a pure `gh api` merge, the literal `gh pr merge` command never runs, so a command-keyed checkpoint is blind to the merge entirely. And even when `gh pr merge` *does* run — a manual merge, or the two-step workaround's `gh pr merge <N> --squash` first step — the post-merge sequence is pure bookkeeping that crowds the checkpoint out. Keying to the merged *state* fires uniformly, whichever path landed the PR.

**Refinement.**

1. **Force an explicit enumeration** at every post-merge checkpoint. Record each considered follow-up as `→ tiled (task_id / #N)` or `→ not tiled, because <reason>`, covering out-of-scope fixes, deferred work, tech debt, and ideas noticed in passing. **"No follow-ups" is valid only as the visible result of that scan — never as a bare assertion.**
2. **Re-key the trigger** from "after `gh pr merge`" to **"when a PR reaches merged state, however it merged"** — a `gh pr merge` you ran, the two-step REST merge, or auto-merge landing it while you were away.
3. **Tile, don't ask.** *Identifying* deferred work at the checkpoint means tiling it yourself, not deflecting the triage into a user-facing question ("open it now, or tee it up for a fresh session?"). Pushing the follow-up's scheduling back onto the user is itself a form of the skip (facet observed 2026-07-06 in the #595 thread).

**Relationship to [ADR-060](060-post-merge-tile-checkpoint-hook.md).** The existing `post-merge-tile-checkpoint.py` hook is **command-keyed** (`"gh pr merge" in command`): it fires on a `gh pr merge` you run — including the two-step's first command — but is **blind to auto-merge**, the exact case that motivated this addendum. The durable enforcement #595 envisions is a *different*, **state-keyed** Stop/PostToolUse hook that observes a merged-state transition and requires a recorded tile-enumeration artifact (analogous to `pre-merge-findings-gate`). That hook is **deferred to separate follow-up work** — this addendum lands the `claude/CLAUDE.md` wording floor only.

**CLAUDE.md update.** The "Capture post-merge follow-ups as tiles" bullet's opening sentence was rewritten per mechanisms 1 + 2 and the tile-don't-ask facet; the `docs/REFERENCE.md` "Post-merge follow-up tiles (chips)" runbook was re-keyed to merged state to match. Closes [dev-env#595](https://github.com/brownm09/dev-env/issues/595). Related: [dev-env#413](https://github.com/brownm09/dev-env/issues/413).

## Addendum — 2026-07-08: the two checkpoints are a floor, not a ceiling

**Incident.** A lifting-logbook-cwd session investigating a dangling engineering-journal
shard-deletion anomaly (root-caused to dev-env#615) identified a genuine, well-scoped follow-up
mid-investigation — recovering the orphaned `draft/2026-07-07` branch via a dedicated
`/journal-compose` session. Neither checkpoint had fired: no PR was merged, and the only GitHub
interaction was a comment on an already-open issue (dev-env#615), not a new issue creation. The
agent treated the tile as optional and asked "Want me to spawn a tile for that now?" instead of
spawning it. The user said yes, then asked for the instructions to be updated so this happens
without asking, full stop.

**Clarification.** The two checkpoints exist to force an enumeration pass at moments a follow-up
could otherwise be silently dropped by omission — they were never meant to be the *only* moments
tiling happens. They are a floor under an otherwise-discretionary judgment call, not a fence around
it. The "tile, don't ask" principle from the 2026-07-05 addendum applies with equal force outside
both checkpoints: any time, in any session, a genuine follow-up is identified — mid-investigation,
while answering a question, in passing during unrelated work — spawn the tile immediately. Offering
to spawn one ("want me to tile this?") is the same anti-pattern the 2026-07-05 addendum named
("deflecting the triage into a user-facing question") wearing a different hat: the tile mechanism
exists specifically to *be* the low-friction ask, so a chat question in front of it defeats the
point.

**CLAUDE.md update.** Added a clause after the two-checkpoint definition stating they are a floor,
not a ceiling, and reworded the "tile, don't ask" sentence to apply unconditionally rather than
"at either checkpoint." Closes [dev-env#642](https://github.com/brownm09/dev-env/issues/642).

---

## References

- [dev-env#369](https://github.com/brownm09/dev-env/issues/369) — issue this ADR closes.
- [dev-env#413](https://github.com/brownm09/dev-env/issues/413) — issue clarifying the plan-approval edge case; closed by the 2026-06-27 addendum.
- [dev-env#595](https://github.com/brownm09/dev-env/issues/595) — issue driving the 2026-07-05 forcing-function refinement (enumeration + merged-state re-key); closed by the addendum above.
- [dev-env#615](https://github.com/brownm09/dev-env/issues/615) — the investigation whose mid-session follow-up motivated the 2026-07-08 addendum.
- [dev-env#642](https://github.com/brownm09/dev-env/issues/642) — issue driving the 2026-07-08 addendum (checkpoints are a floor, not a ceiling); closed by the addendum above.
- [lifting-logbook#700](https://github.com/brownm09/lifting-logbook/pull/700) — motivating incident for the 2026-07-05 addendum: auto-merge landed the PR and "no follow-ups" was asserted with no enumeration.
- [ADR-012](012-post-merge-checklist-board-done-roadmap-update.md) — the post-merge checklist this extends.
- [ADR-028](028-all-findings-merge-gate.md) — the file-and-link guidance the "genuine follow-ups" bar mirrors.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences must be documented in the repo, not only in memory.
- [ADR-060](060-post-merge-tile-checkpoint-hook.md) — the command-keyed tile-checkpoint hook; the deferred state-keyed enforcement (per the 2026-07-05 addendum) complements it.
- `spawn_task` (Claude Code background-task tool) — the harness mechanism that renders the tile/chip.
