# ADR-023 — Generic `required_fields` Config for Issue/PR Project-Board Hook

**Date:** 2026-05-16
**Status:** Accepted
**Tags:** hooks, github-project, post-tool-use, hook-config, automation, workflow

---

## Context

`post-tool-use.py` fires after every `gh issue create` or `gh pr create` and automatically adds the item to the configured GitHub Project. After the add, it calls `format_reminder` to emit a structured `systemMessage` listing the next manual steps (set project fields, milestones, etc.).

The original `format_reminder` implementation hardcoded two field references:

- `config['epic_field_id']` — used in a `gh project item-edit` command for lifting-logbook's Epic field.
- `config['milestones']` — used in a `gh issue edit --milestone` reminder.

Dev-env's `hook-config.json` has neither key; it uses **Impact** (single-select) and **Why** (text) as its required post-create fields. Because `config['epic_field_id']` is a dict key access (not `.get()`), the absence of that key raises a `KeyError`, crashing `format_reminder` before any reminder text is printed. The hook exits with Python's unhandled-exception code rather than the expected exit 2. As a result:

- The project-add step executes (no error there), but
- Impact and Why instructions are never shown to the session,
- The session proceeds silently to file edits without setting those fields.

This was the root cause of the dev-env #231 incident: an issue created without Impact/Why, discovered only at PR merge when the fields could no longer be set reliably (the issue had auto-closed and the GraphQL `item-edit` calls raced against project-board removal).

The same `format_reminder` function also lacked the top-level `try/except` required by the hook safe-exit invariant (see [ADR-007](007-hook-command-invocation.md)), meaning any future unhandled exception would also produce non-zero exit rather than a silent 0.

---

## Decision

Refactor `format_reminder` in `post-tool-use.py` to iterate over a generic `required_fields` array in `hook-config.json` instead of hardcoding `epic_field_id` and `milestones` keys.

**New schema (`required_fields`):**
```json
"required_fields": [
  {
    "name": "Impact",
    "field_id": "<PVTSSF_...>",
    "type": "single_select",
    "options": { "High": "<id>", "Medium": "<id>", "Low": "<id>" },
    "hint": "optional parenthetical shown next to field name"
  },
  {
    "name": "Why",
    "field_id": "<PVTF_...>",
    "type": "text",
    "hint": "one sentence — the cost of not fixing it"
  }
]
```

Supported `type` values: `single_select` (emits `--single-select-option-id`), `text` (emits `--text`), `milestone` (emits `gh issue edit --milestone`).

**Backward compatibility:** when `required_fields` is absent, the function falls back to the old `epic_field_id` / `milestones` keys, producing the same output as before. No changes required to lifting-logbook's `hook-config.json`.

**Safe-exit guard:** add a top-level `try/except Exception: sys.exit(0)` around `main()` in the `__main__` block, satisfying the invariant from ADR-007.

**`hook-config.json` update (machine-local for dev-env's own copy — dev-env gitignores all of `.claude/`; not every project does, e.g. lifting-logbook tracks it in git, dev-env#527):** add `required_fields` with Impact and Why entries for dev-env, using the field IDs from the GitHub Project section of `claude/CLAUDE.md`.

---

## Consequences

- Dev-env sessions now see the exact Impact and Why `gh project item-edit` commands immediately after every `gh issue create`, with no manual lookup required.
- Any future unhandled exception in the hook exits 0 (silent) rather than crashing visibly or blocking tool use.
- Lifting-logbook's hook behavior is unchanged (backward-compat fallback path).
- Lifting-logbook can optionally migrate to `required_fields` by adding the array to its `hook-config.json` and removing `epic_field_id` / `milestones` — no script changes needed.
- Adding a new required field to any project's workflow now requires only a `hook-config.json` edit; no script changes are needed.
- `docs/REFERENCE.md` Configuration table updated to document `required_fields`, `repo`, `project_number`, `project_owner`, `project_node_id`, and the deprecated `epic_field_id` / `milestones` fallback keys.
- [ADR-076](076-live-fetch-project-hook-single-select-options.md) later extends `single_select`-type fields' `options` to be live-fetched from GitHub at reminder time (cached `options` above becomes a fallback only), fixing the silent-drift failure mode this schema was otherwise exposed to (dev-env#527).
