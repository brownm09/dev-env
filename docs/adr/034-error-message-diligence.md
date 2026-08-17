# ADR-034 — Error Message Diligence

**Date:** 2026-06-01
**Status:** Accepted
**Closes:** [dev-env#296](https://github.com/brownm09/dev-env/issues/296)
**Tags:** workflow, diagnosis, ci, error-handling, claude-behavior, global-rule
**Related:** [ADR-004](004-pr-review-reads-from-remote.md), [ADR-008](008-plan-then-optimize-forcing-function.md)

---

## Context

CI guards, hooks, bots, and library exceptions emit error messages written by the *author of the guard*. The message describes what the author predicted would go wrong at that condition, not what is actually true at runtime. A single conditional check often stands in for several upstream states:

- A GitHub Actions job skipped because a job in its `needs:` chain failed — downstream outputs default to the empty string, and a `!= 'true'` check fires the "feature not configured" branch even when the feature is configured.
- A try/except that re-raises with a generic message — the original cause is in the traceback, not in the surface string.
- A health check that bundles connectivity, auth, and configuration into a single boolean — the message names only one of the three.

When I have repeated an emitted message back to the user as a diagnosis without verifying the underlying condition, the cost has consistently exceeded the cost of the verification itself: a misdirected PR comment, a wrong issue, or a user push-back that requires a follow-up correction.

The existing global CLAUDE.md has rules about reading remote state ([ADR-004](004-pr-review-reads-from-remote.md)), planning before acting ([ADR-008](008-plan-then-optimize-forcing-function.md)), and primary-source citation, but nothing that names the specific failure mode of treating a guard's *narrative* as the *diagnosis*.

---

## Decision

Add a new `## Error Message Diligence` section to `claude/CLAUDE.md`, placed between **Context & Token Efficiency** and **Documentation and Citations**. The rule directs three diligence steps before acting on any non-trivial automation- or library-emitted error message:

1. Locate the emitting line.
2. Read the condition the code actually evaluates (the literal expression, not the human-readable label).
3. Distinguish the upstream signal from the message's narrative — for CI, follow `needs:` chains; for hooks and scripts, trace to the input that produced the falsy value.

When uncertain after the three steps, the rule requires explicit hedging ("the guard printed X; I have not yet confirmed the underlying condition") rather than restating the message as fact. Naming the anti-pattern — quoting an emitted message back as a diagnosis — makes the violation easier to recognize mid-turn. An exemption carves out local errors that are reliable by construction (syntax errors, file-not-found at a path just written, type errors with a specific symbol named).

The section cites the lifting-logbook [PR #395](https://github.com/merickvaughn/lifting-logbook/pull/395) incident inline as the rationale.

---

## Rationale

**Why a behavioral rule rather than a hook.** The failure mode is judgment, not mechanics. A hook could grep for `staging.yml`-style guard messages in tool output, but it cannot decide whether the model is treating the message as evidence or as diagnosis. A rule in CLAUDE.md, read on every session, is the right altitude.

**Why three steps and not one.** Step 1 alone ("read the emitting line") is what a careful reader would do without the rule; the failure mode is skipping that step. Splitting the diligence into three lets the rule call out the specific transition — *from* "the message says X" *to* "the code evaluates Y when Z is empty" — that the model omits when it propagates wrong root causes.

**Why an explicit exemption.** Without one, the rule reads as "always trace every error message," which would burn tokens on the common case (a `FileNotFoundError` at a path just written by the same turn). Calling out the safe class — messages reliable by construction — keeps the rule's force on the dangerous class.

**Why cite the incident inline.** The incident is the strongest forcing memory the rule has. Future sessions reading the section see a concrete worked example with a click-through to the actual misdiagnosis, not an abstract injunction.

**Why placement between Context & Token Efficiency and Documentation and Citations.** Diagnosing errors is an information-gathering activity adjacent to planning and citation. Placing it near the planning rules clusters the "be careful about evidence" themes; placing it before the citation rule (which is also about evidence) signals their kinship.

---

## Alternatives considered

- **Add to an existing section** (Code Quality, or Plan-Then-Optimize). Rejected: Code Quality is about written code, not about how the model reads runtime output; Plan-Then-Optimize is about efficiency, not evidence. A new top-level section makes the rule easier to find when triaging a future incident.
- **Embed in `/review` skill prompts.** Rejected: the failure mode occurs across the whole workflow (PR comments, bot replies, issue filings), not only during review. A global rule covers all contexts.
- **No written rule; rely on the incident memory.** Rejected: incidents do not propagate to future sessions without a written artifact.

---

## Consequences

**Positive:**
- Future sessions encountering a CI guard, hook, or bot error string have an explicit named anti-pattern to avoid and a three-step procedure to follow.
- The rule generalizes — it applies equally to GitHub Actions, pre-commit hooks, dev-env scripts, and library exceptions.
- The incident citation provides a concrete worked example that survives session boundaries.

**Negative:**
- Adds ~25 lines to global CLAUDE.md, which is read on every session.
- The three-step procedure adds tokens to error-handling turns where the rule applies. Mitigated by the exemption clause for local errors.
- Judgment-based rules have softer enforcement than mechanical hooks; compliance depends on session-by-session attention.

---

## References

- Incident: [lifting-logbook PR #395 comment thread](https://github.com/merickvaughn/lifting-logbook/pull/395#issuecomment-4594434736)
- [GitHub Actions job dependencies](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow#defining-prerequisite-jobs) — primary source for `needs:` skip semantics that produced the misleading empty output in the incident.
