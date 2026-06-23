# ADR-059 — Multi-PR Decomposition: Top-Level Issue + Sub-Issue Hierarchy with Tile Context

**Date:** 2026-06-23
**Status:** Accepted
**Closes:** [dev-env#411](https://github.com/brownm09/dev-env/issues/411)
**Tags:** issues, decomposition, tiles, workflow, global-rule
**Related:** [ADR-046](046-post-merge-followup-tiles.md), [ADR-048](048-memory-immortalization-issue-pairing.md)

---

## Context

When a user prompt maps to a large or multi-faceted task, Claude Code may decompose it into
multiple PRs and spawn tiles (via `spawn_task`) so each PR can be worked in its own session.
Two gaps emerged from practice:

1. **Issue tracking fragmentation.** Individual sub-issues get filed per-PR, but there is no
   parent issue anchoring the full initiative. Once the spawning session ends and its context
   window is gone, the relationship between all the sub-PRs and the originating request is
   lost. A user looking at the issue tracker later cannot tell which issues belong to the same
   initiative, and the top-level intent is never durably recorded.

2. **Tile context loss.** The `spawn_task` prompt for each tile typically describes only the
   immediate sub-task. The spawned session has no memory of which larger initiative it belongs
   to, so it cannot properly reference the parent issue when opening its PR, and the user must
   re-establish the hierarchy context manually in every follow-on session.

## Decision

When a prompt or initiative decomposes into multiple PRs, the following four-part structure
applies:

1. **Create a top-level issue first.** Before cutting any branch or filing sub-issues, open a
   single issue that captures the full scope and rationale of the originating request. This
   issue is never directly closed by a PR; it is the anchor for the whole initiative.

2. **Create a sub-issue for each individual PR.** File one sub-issue per PR, describing what
   that specific PR accomplishes. Link back to the top-level issue in the sub-issue body.

3. **Each PR closes its sub-issue.** The PR body contains `Closes #<sub-issue-N>` (not the
   top-level issue). The top-level issue remains open until all sub-PRs have merged, then is
   closed manually or via a final PR that explicitly targets it.

4. **Tiles embed both issue references.** Each tile spawned by the decomposing session must
   include in its prompt: the top-level issue number/URL, and the specific sub-issue number/URL
   that the spawned session should address. This allows the spawned session to reference both
   correctly when it opens its PR and to orient itself within the larger initiative.

## Rationale

**Why a top-level issue?** GitHub issues are the durable, user-visible tracking artifact.
A plain tile or session note is ephemeral. Recording the full initiative intent in an issue
ensures it survives the spawning session, is visible to the user in the tracker, and can be
linked from every sub-PR and tile prompt as an anchor.

**Why sub-issues?** One issue per PR provides per-PR traceability. The sub-issue is what the
PR closes; it scopes the work for that branch and makes review context clear without conflating
all work in the top-level issue. It also allows individual PRs to be tracked on project boards
independently while the top-level issue provides the roll-up view.

**Why do tiles embed both refs?** A tile's spawned session starts with zero context from the
originating conversation. The only durable channel the decomposing session has to convey
hierarchy to the spawned session is the tile prompt itself. Without both refs in the prompt,
the spawned session opens its PR with only a `Closes #<sub-issue>` reference, the top-level
issue is never mentioned, and the user cannot discover the initiative hierarchy from the PR
alone.

## Alternatives considered

- **One issue for all PRs (no sub-issues).** All PRs close the same top-level issue. Simple,
  but loses per-PR traceability: a single issue cannot capture what each individual PR
  addressed, making history harder to read. Rejected.

- **Sub-issues only, no top-level.** The implicit status quo before this ADR. Each PR gets
  its own issue, but there is no parent; the initiative-level intent is never recorded. Tiles
  carry no hierarchy context. Rejected because the first gap (fragmentation) is never resolved.

- **Embed only the top-level issue in tiles, not the sub-issue.** The spawned session would
  know the initiative but not which specific sub-task it owns, so it could not file the correct
  sub-issue or produce the correct `Closes #N`. Rejected.

- **Leave hierarchy in session memory or the plan file.** Memory is invisible to spawned
  sessions and expires; plan files are not reliably read by a fresh agent. Neither is a
  durable channel to the spawned session. Rejected.

## Consequences

**Positive:**

- The full initiative intent is durably captured in an issue that survives the spawning session.
- Each spawned session receives enough context to open its PR with correct issue references
  without the user re-establishing the hierarchy.
- The user can navigate the full initiative from either direction: the top-level issue links
  sub-issues; each PR links its sub-issue; tile prompts name both.

**Negative / residual:**

- Slightly more issue-creation overhead at decomposition time (top-level + N sub-issues vs.
  N issues). For small decompositions (2–3 PRs) this is minimal; for large ones the structure
  pays for itself in clarity.
- The rule is a behavioral convention, not mechanically enforced by a hook. It relies on the
  agent applying the rule at decomposition time.
- The top-level issue must be closed manually (or via a final PR that targets it explicitly)
  after the last sub-PR merges — it is not auto-closed by any `Closes #N` in a sub-PR.

## References

- [dev-env#411](https://github.com/brownm09/dev-env/issues/411) — issue this ADR closes.
- [ADR-046](046-post-merge-followup-tiles.md) — established the post-merge tile capture
  pattern; this ADR extends tile content requirements for multi-PR decomposition.
- [ADR-048](048-memory-immortalization-issue-pairing.md) — durable rules must live in the
  repo, not only in memory; this rule follows that convention.
