# ADR 025 — Default Plan Mode and opusplan Model

**Date:** 2026-05-23
**Status:** Accepted
**Tags:** config, settings, plan-mode, model, workflow, defaults

---

## Context

Two global `settings.json` defaults were producing avoidable friction:

1. **No `permissions.defaultMode` set** — sessions started in "auto" mode, meaning Claude could immediately make file edits without the user first reviewing a plan. For a dev-env repo where most changes affect global tooling, unreviewed edits carry higher-than-usual risk.

2. **`model: claude-sonnet-4-6`** — Sonnet was used for both planning and execution. Planning is the most intelligence-sensitive phase of any task (a weak plan compounds into larger execution errors), yet the cheaper execution phase was receiving the same model weight as planning.

The `opusplan` alias (introduced in Claude Code v1.0.75) provides a built-in Opus-for-planning / Sonnet-for-execution split without requiring per-task model overrides.

---

## Decision

Set two values in `claude/settings.json`:

```json
"permissions": {
  "defaultMode": "plan",
  ...
},
"model": "opusplan"
```

- **`defaultMode: plan`** — every new session starts in plan mode. Claude can explore (read files, run shell commands) but cannot make edits until the user approves a plan via `ExitPlanMode`. The user can still bypass plan mode on any individual session by pressing `Shift+Tab`.
- **`model: opusplan`** — Opus is used during the plan phase; Sonnet is used for execution. No change to `effortLevel` (remains `"medium"`).

---

## Consequences

**Positive:**
- Every session now requires explicit user approval before any file is written or edited — consistent with "measure twice, cut once" workflow.
- Plan quality improves (Opus reasoning) without increasing execution token costs (Sonnet executes).
- The safety default aligns with the pre-existing Plan-then-optimize rule in CLAUDE.md without requiring the user to remember to invoke plan mode manually.

**Negative / Watch-outs:**
- Sessions that consist purely of read-only work (research, `/research`, `/review`) still enter plan mode and require a `Shift+Tab` to skip — minor friction.
- `opusplan` is a Claude Code alias, not a model ID. If the alias is renamed or removed in a future Claude Code version, `settings.json` will need updating. Monitor Claude Code changelogs.
- Token cost per session may increase slightly during plan phases (Opus input pricing); offset by reduced rework from lower-quality plans.

---

## Alternatives Considered

- **Keep Sonnet as default, add `defaultMode: plan` only** — still improves safety, but misses the planning-quality improvement.
- **Set `model: claude-opus-4-7` globally** — improves quality everywhere but significantly increases execution costs; not worth it when Sonnet handles execution well.
- **Use `effortLevel: high` instead** — effort level affects thinking budget within a model tier, not the model itself; doesn't solve the plan-phase model routing problem.
