# ADR-125 — Check for an Existing Open PR Before Implementing an Issue

**Date:** 2026-07-27
**Status:** Accepted
**Closes:** [dev-env#922](https://github.com/brownm09/dev-env/issues/922)
**Tags:** workflow, git-workflow, duplicate-work, open-pr, issue-implementation, ask-user-question, claude-behavior, global-rule, correction, adr-038, adr-046, adr-048, adr-117
**Related:** [ADR-117](117-absence-claims-need-absolute-paths.md), [ADR-048](048-memory-immortalization-issue-pairing.md), [ADR-038](038-durable-preferences-documented-in-repo.md), [ADR-046](046-post-merge-followup-tiles.md)

---

## Context

A session was asked to fix dev-env#918 (`run-hook-tests.py` missing `import _winsubp`, causing console flashes and a subprocess-scan compliance failure). It verified #918 was a genuine pre-existing failure on `origin/main` (per the CLI Scripting Checklist), planned the fix, implemented it, and opened PR #921 — normal, correct process throughout.

Only while writing the engineering-journal stub — reading `sessions/dev-env/open-prs/770.json` as part of the routine open-PR-record check — did the session discover that **PR #770**, open since 2026-07-14, already made the identical fix. PR #770 closes a different, older issue (#734) that reported the same underlying bug before #918 was ever filed. Two issues, one bug, two independent fixes, and the collision was discovered by accident at the very end of the session rather than at the start.

Nothing in the existing workflow would have surfaced this earlier:

- **Git Workflow → Create an issue before changing files** governs filing a *new* issue before editing — it says nothing about checking whether an *existing* issue already has a fix in flight.
- **Durable Preferences & Memory → Search before filing** (the [ADR-048](048-memory-immortalization-issue-pairing.md) amendment) checks for a duplicate *issue* before filing a new immortalization issue — a different artifact (issue vs. PR) and a different moment (before filing vs. before implementing).
- **CLI Scripting Checklist → Ref scoping** ([ADR-117](117-absence-claims-need-absolute-paths.md)) checks the open-PR set before claiming an *identifier* (an ADR number, a `## Testing` item, a fixture filename) is free — the same underlying blind spot (an open PR is invisible to a plain checkout/ref read), but a different trigger: claiming a number is unclaimed, not starting work on a specific issue.

None of the three covers what's actually missing: a check, run at the *start* of implementation work on any issue — new or picked up from the backlog — for whether an open PR already addresses that issue's underlying problem.

The session resolved the immediate collision by surfacing it to the user via `AskUserQuestion` — closing or superseding an open PR (someone else's, or a prior session's own) is a visible, public action per the system prompt's risk-confirmation discipline, not a decision to make unilaterally. The user chose to consolidate on the fresher PR #921 (closing both #918 and #734) and close #770 as superseded. That resolution was correct, but it was reached by luck: the stub-writing step happens to read the open-PR record, and #770 happened to still have a shard on disk. A session working an issue whose colliding PR has no open-PR shard at all (opened by a human directly, or already reconciled away) would have had no trip-wire whatsoever.

dev-env#842 (open) is a related but non-overlapping backlog: it tracks *already-known* stale PRs (#839/#770/#410/#322/#246) for reactive cleanup. It does not cover the proactive case this ADR addresses — a *new* session about to start work missing an *existing* PR it hasn't looked for yet.

## Decision

Add a new bullet to the global `claude/CLAUDE.md`, in **`## Git Workflow`**, immediately after **"Create an issue before changing files"** — its natural sequence position, the next check after filing/identifying the issue and before the first edit:

> **Check for an existing open PR before implementing.** Before starting implementation work on any issue — freshly filed or picked up from the backlog — confirm no open PR already addresses it: `gh pr list --search "<issue-number-or-keywords>" --state open`, or `gh issue view <N> --json` and check its linked-PRs/timeline. A match means the work is already in flight — **surface it to the user via `AskUserQuestion`** rather than silently duplicating the effort.

**Placement: Git Workflow, not the CLI Scripting Checklist.** The Checklist's "Ref scoping" bullet is about verifying an *identifier* is free before claiming it — a scripting-hygiene concern. This rule is a workflow-sequencing concern (the order of operations at the start of any issue-driven session), so it belongs beside the sibling workflow rule it extends, not folded into the scripting checklist it merely resembles in mechanism.

## Rationale

- **The blind spot is structural, not a one-off mistake.** An open PR is invisible to `origin/main`, to the issue list, and to a fresh session's mental model of "what work exists" unless something explicitly looks for it. The session in the incident had no reason to suspect a duplicate — #918 read as a clean, isolated bug report.
- **Distinct from both existing near-misses, not a duplicate of either.** Search-before-filing (issue vs. issue, at file-time) and Ref scoping (identifier vs. PR set, at claim-time) are genuinely different checks with different triggers; this rule closes the third combination (issue vs. PR set, at implement-time) rather than restating either.
- **`AskUserQuestion` is the correct resolution mechanism, not a new one.** The system prompt already requires explicit user confirmation before any action "visible to others or that affect[s] shared state" — closing or superseding an open PR squarely qualifies. This rule only adds the *detection* step; the escalation path already existed and needed no new tooling.
- **Cheap relative to the cost of the miss.** One `gh pr list --search` call versus a full duplicate implementation, a second PR, and a user-mediated cleanup — the incident's actual cost.
- **Durable, so it belongs in the repo.** Per [ADR-038](038-durable-preferences-documented-in-repo.md), a cross-session workflow correction like this belongs in the version-controlled instructions, not agent memory, where it would be invisible to every other session and to the user.

## Alternatives considered

- **A mechanical `PreToolUse` hook that blocks the first `Edit`/`Write` in a session until a `gh pr list --search` has run.** Rejected — there is no reliable signal tying a session's first edit to "issue N is being implemented"; the harness has no structured input naming the target issue up front (contrast the ADR-number or Testing-item-number collisions, which are literal, greppable strings in a diff at merge time). This is a judgment-based workflow rule, not a pattern match — matching the precedent set by [ADR-113](113-cross-session-handoff-tiles.md) and [ADR-123](123-forward-link-phase-dependent-followons.md) for similarly semantic behaviors: start as documentation, add a hook only if the anti-pattern mechanically recurs.
- **Extend `reconcile-open-prs.py` to cross-check new issues against tracked open-PR shards.** Rejected — that hook reconciles *this project's own* previously-opened PR shards against live GitHub state; it has no visibility into an issue a session is only now about to start, and no shard exists for a PR opened directly by a human or by an untracked session.
- **Fold into the existing CLI Scripting Checklist "Ref scoping" bullet instead of Git Workflow.** Rejected — see Decision. Ref scoping's trigger ("I am about to claim a number/name is free") fires at a different moment than this rule's trigger ("I am about to start implementing an issue"); collapsing them would bury a workflow-sequencing rule inside a scripting-hygiene checklist where it would not be read at the right time.
- **Rely on the "Capture follow-ups as tiles" / tile-tracking discipline ([ADR-094](094-tile-tables-and-issue-per-tile.md)) to eventually surface the duplicate.** Rejected — tiles capture *new* follow-ups discovered during or after a session; they do nothing to prevent a session from duplicating *existing*, already-in-flight work in the first place.

## Consequences

**Positive:** a session starting work on any issue now has an explicit, cheap check for in-flight duplicate effort, with a defined escalation path (`AskUserQuestion`) when a match is found — closing the exact gap that let dev-env#918/#734/#770 collide silently until the journal stub accidentally caught it.

**Negative / residual:**

- Purely behavioral — like [ADR-113](113-cross-session-handoff-tiles.md) and [ADR-123](123-forward-link-phase-dependent-followons.md), this is not mechanically enforced. A session that skips the check pays no immediate penalty beyond the risk the rule exists to prevent.
- The search is best-effort: a keyword or number search can miss a PR whose title/body doesn't mention the issue by number or matching keywords. This mirrors the same accepted caveat on "Search before filing" — a bounded, not perfect, mitigation.
- Adds one more bullet to an always-loaded global file; kept to a single bullet rather than a new subsection to bound the context-weight cost ([ADR-114](114-slim-testing-section-index.md)'s standing concern).

## References

- [dev-env#922](https://github.com/brownm09/dev-env/issues/922) — the immortalization issue this ADR closes.
- [dev-env#921](https://github.com/brownm09/dev-env/pull/921) / [dev-env#770](https://github.com/brownm09/dev-env/pull/770) / [dev-env#918](https://github.com/brownm09/dev-env/issues/918) / [dev-env#734](https://github.com/brownm09/dev-env/issues/734) — the motivating collision.
- [dev-env#842](https://github.com/brownm09/dev-env/issues/842) — the related but non-overlapping reactive stale-PR triage backlog.
- [ADR-117](117-absence-claims-need-absolute-paths.md) — the closest existing precedent (Ref scoping): same open-PR blind spot, different trigger.
- [ADR-048](048-memory-immortalization-issue-pairing.md) — Search-before-filing, the issue-vs-issue sibling of this PR-vs-issue check.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences belong in the repo, not memory.
- [ADR-046](046-post-merge-followup-tiles.md) / [ADR-094](094-tile-tables-and-issue-per-tile.md) — tile capture for *new* follow-ups, the mechanism this ADR distinguishes itself from (this ADR is prevention, not capture).
- [ADR-113](113-cross-session-handoff-tiles.md) / [ADR-123](123-forward-link-phase-dependent-followons.md) — precedent for documenting a semantic workflow rule before considering mechanical enforcement.
