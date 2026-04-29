# ADR 008 — Plan-Then-Optimize as an Embedded Skill Step

**Date:** 2026-04-27  
**Status:** Accepted

---

## Context

The plan-then-optimize protocol — state a numbered plan, apply a token-efficiency revision pass, apply an outcome-correctness revision pass, then act — was documented in `claude/CLAUDE.md` as a required pre-execution step for any multi-step task.

**Incident (dev-env#51):** A `/journal-compose` run spawned parallel agents that duplicated significant work. The plan-then-optimize protocol would have caught the redundancy in the efficiency pass, but it was skipped. Post-mortem finding: rules in documentation files rely on recall at execution time. Under time pressure or context load, recall fails.

---

## Decision

**Step 0.5** (the plan-then-optimize protocol) was added directly to the `/journal-compose` skill execution path as an explicit, mandatory step that fires before any tool calls. The skill cannot proceed to Step 1 without completing Step 0.5.

**Generalised pattern:** When a practice documented in CLAUDE.md is repeatedly missed in execution, the correct fix is to embed enforcement as an explicit step in the relevant skill or tool — not to restate or emphasise the rule in documentation. Documentation is for humans reading the file; forcing functions are for execution paths.

---

## Consequences

- Any multi-step skill should include a mandatory planning step (named Step 0.5 by convention) near the top of its execution path.
- Two copies of Step 0.5 exist in `journal-compose/SKILL.md` (main flow + subagent template). Differences between them are intentional and annotated with a sync comment — do not mechanically de-duplicate them.
- The plan-then-optimize rule in `claude/CLAUDE.md` remains as human-readable documentation of the protocol's intent; the skill step is the enforcement mechanism.
- New skills that involve Agent spawning or multi-file reads should include Step 0.5 at authoring time, not after a missed-protocol incident.

---

## References

- Engineering journal: `sessions/meta/2026-04-27-validator-and-workflow-hardening.md`
- `dev-env` commit `88f2650` — `feat: add Step 0.5 plan-then-optimize to journal-compose skill`
- `dev-env` commit `d687709` — `feat: require plan-then-optimize pass before multi-step tasks` (original CLAUDE.md rule)
- `claude/skills/journal-compose/SKILL.md` — Step 0.5 implementation
- `claude/CLAUDE.md` § Context & Token Efficiency — plan-then-optimize protocol
