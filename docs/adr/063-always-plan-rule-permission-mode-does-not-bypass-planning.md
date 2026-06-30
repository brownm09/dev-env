# ADR-063 — Always-Plan Rule: Permission Mode Does Not Bypass Planning

**Date:** 2026-06-30
**Status:** Accepted
**Tags:** workflow, planning, permission-mode, bypass, auto, claude-behavior, global-rule

---

## Context

Claude Code has three permission modes (plan, bypass, auto), cycled with Shift+Tab. Plan mode
requires explicit approval for each tool call; bypass and auto modes reduce or eliminate those
prompts to enable faster, less-interrupted operation.

The existing `Plan-then-optimize before acting` rule in `claude/CLAUDE.md` triggers on specific
conditions: Agent spawns, skill invocations, multi-file reads, or a switch of primary objective.
In bypass and auto modes, where Claude acts without per-call confirmation, users observed Claude
skipping planning and acting immediately on substantive tasks — behavior inconsistent with what
plan mode enforces but not explicitly prohibited by the existing rule.

The distinction that was missing: permission modes govern **tool execution approval**, not the
**plan-first discipline**. The two are orthogonal. A bypass-mode session still benefits from a
stated plan before action — arguably more so, since there are no individual approval gates to
catch scope drift mid-execution.

---

## Decision

Add an explicit rule to the `## Context & Token Efficiency` section of the global
`claude/CLAUDE.md`, placed before the `Plan-then-optimize before acting` paragraph:

> **Always plan first — permission modes don't bypass it:** Plan mode (Shift+Tab cycles modes)
> is the session default for every substantive task. Bypass and auto modes reduce permission
> prompts only; they do not relax the plan-first discipline.

This establishes plan mode as the conceptual default and makes clear that permission mode is a
separate dimension from the plan-first requirement.

---

## Consequences

**Positive:**
- Eliminates the ambiguity that bypass/auto modes imply permission to skip planning.
- Complements rather than duplicates the existing `Plan-then-optimize` protocol — the new rule
  states *when* planning is required (always); the existing rule states *how* to plan.
- Short and durable — two sentences, unlikely to need revision as modes evolve.

**Negative / Trade-offs:**
- Behavioral rule, not hook-enforced. Relies on Claude reading and following the instruction,
  the same as all other `claude/CLAUDE.md` rules.

---

## Alternatives Considered

**Extend the Plan-then-optimize trigger list to include "bypass/auto mode."** Rejected — the
trigger list describes task characteristics, not session configuration; mixing the two would
confuse the rule's logic and make the trigger conditions harder to reason about.

**Add a hook that detects permission mode and forces plan mode.** Rejected — no reliable way to
detect the current permission mode from a hook; implementing it would introduce significant
complexity for what is fundamentally a behavioral clarification.

---

## References

- [`claude/CLAUDE.md` → Context & Token Efficiency](../claude/CLAUDE.md#context--token-efficiency)
- [ADR-025](025-default-plan-mode.md) — established plan mode as the settings default
- [ADR-008](008-plan-then-optimize-forcing-function.md) — Plan-Then-Optimize protocol this rule contextualizes
- [dev-env issue #409](https://github.com/brownm09/dev-env/issues/409) — motivating issue
