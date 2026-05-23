# ADR 025 — Default Plan Mode

**Date:** 2026-05-23
**Status:** Accepted
**Tags:** config, settings, plan-mode, workflow, defaults

---

## Context

The global `settings.json` had no `permissions.defaultMode` set, so sessions started in "auto" mode: Claude could immediately make file edits without the user first reviewing a plan. For a dev-env repo where most changes affect global tooling, unreviewed edits carry higher-than-usual risk.

A second improvement was also planned: routing plan-phase calls to Opus and execution-phase calls to Sonnet via an `opusplan` alias. Investigation after the initial implementation found that no such alias exists in Claude Code's settings schema — the `model` field accepts only explicit Anthropic model IDs. The `model` value was reverted to `claude-sonnet-4-6`. Per-phase model routing is deferred pending a native feature.

---

## Decision

Set one value in `claude/settings.json`:

```json
"permissions": {
  "defaultMode": "plan",
  ...
}
```

- **`defaultMode: plan`** — every new session starts in plan mode. Claude can explore (read files, run shell commands) but cannot make edits until the user approves a plan via `ExitPlanMode`. The user can still bypass plan mode on any individual session by pressing Shift+Tab.
- **`model`** — remains `claude-sonnet-4-6` (unchanged from prior default).

---

## Consequences

**Positive:**
- Every session now requires explicit user approval before any file is written or edited — consistent with the "measure twice, cut once" workflow.
- The safety default aligns with the pre-existing Plan-then-optimize rule in CLAUDE.md without requiring the user to remember to invoke plan mode manually.

**Negative / Watch-outs:**
- Sessions that consist purely of read-only work (research, `/research`, `/review`) still enter plan mode and require a Shift+Tab to skip — minor friction.

---

## Alternatives Considered

- **`model: opusplan`** — investigated as a built-in alias for Opus-during-planning / Sonnet-during-execution. Not a valid Claude Code model value; the schema accepts only explicit Anthropic model IDs. Reverted.
- **`model: claude-opus-4-7` globally** — improves planning quality but significantly increases execution costs; Sonnet handles execution adequately.
- **`effortLevel: high` instead** — effort level affects the thinking budget within a model tier, not the model itself; does not address per-phase model routing.
