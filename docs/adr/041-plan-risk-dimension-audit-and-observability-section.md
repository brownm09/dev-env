# ADR-041 — Plan Risk-Dimension Audit Checklist + Per-Project Observability Section

**Date:** 2026-06-06
**Status:** Accepted
**Closes:** [dev-env#325](https://github.com/brownm09/dev-env/issues/325)

## Context

Implementation plans in this environment reliably consider **testing**, because testing is
gated three independent ways: the mandatory per-project `## Testing` section
([ADR-040](040-global-claudemd-layering-and-slimming.md)), the test-coverage gate
([ADR-022](022-test-coverage-gate-before-pr.md)), and the suppression / test-integrity /
baseline-failure policies ([ADR-026](026-suppression-policy.md),
[ADR-029](029-test-integrity-policy.md), [ADR-030](030-baseline-test-failure-policy.md)).

No equivalent forcing function exists for the other dimensions where changes commonly go
wrong. Nothing in the plan-then-optimize protocol (which had only Pass 1 — token efficiency,
and Pass 2 — outcome correctness) requires a plan to state how a change handles
**observability, security, resilience/failure-modes, performance, or
data-integrity/migrations**. The omission is silent: a plan that never mentions error
handling or logging reads as complete.

The gap is concrete, and an audit of two live projects supplied the motivating evidence:

- **lifting-logbook** (Next.js + NestJS/Fastify + Prisma/Postgres + Clerk + LLM adapters):
  the LLM cycle-generation path has **no timeout or circuit-breaker** for 10s+ calls;
  `$queryRaw` / `$executeRaw` are **not auto-traced** by the OTel Prisma instrumentation;
  the LLM adapters apply **no PII scrubbing** to prompts; Prisma migrations carry **no
  documented rollback strategy**; and there is N+1 risk (no DataLoader). None of these are
  caught by the testing gate — they are observability, resilience, security, and
  data-integrity concerns respectively.
- **career-playbook** (Markdown content + Claude skills): observability, security, and
  performance are largely **N/A**. A flat "audit security and performance every time"
  checklist would force meaningless answers here. What actually matters is reference
  integrity (`validate.sh`), **briefing regeneration before merge**, and artifact-schema
  parity — project-specific gates that no global list could enumerate.

These near-inverse risk profiles are the key constraint: a single flat checklist is wrong
for *both* projects — too thin for the web app, irrelevant for the content repo. The
mechanism has to be **tiered, with an explicit N/A escape**, and it has to **defer the
specifics to each project**.

## Decision

1. **Add Pass 3 — Risk-dimension audit to the plan-then-optimize protocol** in global
   `claude/CLAUDE.md`. Every plan must address six dimensions explicitly — **Testing,
   Observability, Security, Resilience/failure-modes, Performance, Data integrity &
   migrations** — and for any that don't apply must state `"N/A — <reason>"` rather than
   omit it. The bar is *stating the decision*, not adding work everywhere. The protocol's
   intro is updated from "two explicit revision passes" to "three."

2. **Accessibility is conditional, not mandatory** — audited only when a change touches UI.
   Making it a required line item would produce an "N/A" answer on the large majority of
   changes, training reviewers to rubber-stamp the whole list (checklist fatigue). The same
   reasoning was considered for Performance and Data-integrity but rejected: the user elected
   to keep those two mandatory for thoroughness, accepting the more frequent N/A answers.

3. **Project-specific gates are deferred to each project's CLAUDE.md.** Pass 3 names
   lifting-logbook and career-playbook examples inline as illustration, but the rule is that
   the audit defers to whatever gates a project declares.

4. **Add a mandatory per-project `## Observability` section requirement**, mirroring the
   existing `## Testing` requirement. It describes the project's logger/levels, structured vs.
   plain output, where errors and traces go, and what the Observability dimension should
   verify there. Content/docs repos with no runtime must say so and name the equivalent
   verification. Pass 3's Observability dimension defers to this section, exactly as the
   "Test before PR" rule defers to `## Testing`.

5. **Enforcement is advisory.** The gate is the CLAUDE.md rule plus the existing advisory
   pre-PR reminder (`pre-pr-create-check.py`, which always exits 0 — see
   [ADR-007](007-hook-command-invocation.md) for the hook model). No new hook is added, and
   `pre-pr-create-check.py` is unchanged. A missing `## Observability` section is noted in the
   plan and raised with the user; it does not block the PR.

## Consequences

- **The silent-omission failure mode is closed:** a plan can no longer reach execution
  without an explicit (possibly "N/A") decision on each of the six dimensions. The
  lifting-logbook LLM-timeout class of gap is exactly what the Resilience dimension surfaces.
- **The mechanism is correct for inverse risk profiles:** career-playbook answers "N/A —
  content repo, no runtime" and defers to `validate.sh`; lifting-logbook answers in full and
  defers to its OTel/security gates. Neither is forced into the other's checklist.
- **Two follow-up items:** lifting-logbook and career-playbook each need a `## Observability`
  section added to satisfy the new per-project requirement. These are tracked as separate
  issues in their own repos, not bundled into this dev-env change.
- **No README/REFERENCE update is required.** The dev-env Documentation Maintenance table
  governs skill/hook/routine/config-schema changes; a new global CLAUDE.md *rule* is
  documented by the rule text plus this ADR.
- **Small standing cost:** every qualifying plan now carries up to six short audit lines.
  This is the intended trade — the cost is bounded and the N/A escape keeps it proportional.

## Alternatives considered

- **A flat, always-mandatory checklist (no N/A escape, no tiering).** Rejected: wrong for
  both motivating projects — irrelevant to career-playbook, and it can't enumerate
  lifting-logbook's real project-specific gates. Tiering with deferral is what makes one rule
  fit both.
- **A new blocking hook that fails `gh pr create` when `## Observability` is absent.**
  Rejected: diverges from the advisory model that `## Testing` and `pre-pr-create-check.py`
  already use, and risks false blocks (e.g. a docs-only repo, or a section under a synonym
  heading). Kept as a possible future escalation if advisory proves insufficient, not adopted
  now.
- **Make Accessibility mandatory too (the broad option).** Rejected by the scope decision:
  a11y is N/A on most non-UI changes, and mandatory N/A line items erode checklist
  discipline. Conditional triggering keeps the audit honest.
- **Fold the new dimensions into the existing Pass 2 (outcome correctness).** Rejected: Pass 2
  checks that the *optimized plan still does what the original intended*; risk-dimension
  auditing is a distinct concern (what the change does to the system's observability/security
  posture), and burying it in Pass 2 would reproduce the silent-omission problem.
