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

- **`defaultMode: plan`** — every new **local CLI** session starts in plan mode. Claude can explore (read files, run shell commands) but cannot make edits until the user approves a plan via `ExitPlanMode`. The user can still bypass plan mode on any individual session by pressing Shift+Tab. (Platform-launched sessions are an exception — see the Addendum below.)
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

---

## Addendum (2026-06-09) — `defaultMode` governs only local CLI session startup

**Symptom that prompted this:** `defaultMode: plan` had been live since this ADR (2026-05-23), yet interactive sessions kept starting in `bypassPermissions`. An audit with `session-mode-report.py` showed 36 of 48 interactive sessions started off-plan, including SDK-launched worktree sessions. This looked like a broken hook or an unpersisted setting and was investigated as such ([#341](https://github.com/brownm09/dev-env/issues/341)).

**Root cause — by design, not a bug.** `defaultMode` is applied by Claude Code only when *it* starts a session, i.e. a fresh **local CLI** invocation. **Desktop/web-app sessions and spawn-task / SDK-launched sessions are started by the platform in `bypassPermissions`, which overrides `defaultMode`.** (The mechanism — a `bypassPermissions` startup flag passed at launch — is *observed behavior*: Claude Code does not publicly document the launch-mode contract, so this is the empirically-confirmed cause, not a cited guarantee.) That override happens at launch, so:

- `settings.json` cannot countermand it — there is no setting that forces platform-launched sessions into plan.
- Restarting does not help — the config is already correct and already applied; the override is re-imposed at each platform launch.
- The pattern is intermittent precisely because launch *surface* varies: local CLI honors `plan`; app/SDK launches do not.

Verified via the `session-mode-prompt` hook, which records Claude Code's authoritative `permission_mode` from the `UserPromptSubmit` payload at each session's first prompt (`claude/scripts/session-mode-prompt.py`); `session-mode-report.py` aggregates it.

**Operational consequence.** For a platform-launched session that should be in plan, press **Shift+Tab at the first prompt** — that is the only lever. There is no `settings.json` fix to pursue; do not re-chase this as a misconfiguration.
