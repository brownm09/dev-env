# ADR-127: Global Guard Against Oversized SKILL.md Writes/Edits

**Date:** 2026-08-04
**Status:** Accepted
**Tags:** hooks, pre-tool-use, post-tool-use, skills, file-size, write, edit, global-rule, hookout

---

## Context

Claude Code's official Agent Skills docs publish no byte-size limit for `SKILL.md` — only a soft "keep the body under 500 lines" guideline ([Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)) and an unrelated 30MB total-bundle limit for the Skills *API* upload path ([Using Agent Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide)). Bundled reference/script files alongside a `SKILL.md` are explicitly documented as having "no practical limit," since Claude only reads them on demand — `SKILL.md` itself is different: it's Level 2 content, loaded into context **in full** the moment a skill triggers ([Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)).

The user has directly observed a real runtime error citing 256KB (262,144 bytes) as a limit for an oversized `SKILL.md` — a genuine constraint encountered in practice, distinct from anything in the published guides. Nothing in this repo breaches it today (`claude/skills/journal-compose/SKILL.md` is the largest at 86,627 bytes, ~33% of 256KB), but there was no guard preventing it, here or in any other project — `~/.claude/skills/` and every project's `.claude/skills/` are all filesystem-based and equally exposed. See dev-env#939.

---

## Decision

Two new hooks, both matching on `SKILL.md`'s basename only (case-insensitive) — not directory convention — so the same two hooks cover dev-env's own `claude/skills/*/SKILL.md` and `claude/routines/*/SKILL.md`, and every other project's skills, without any repo-specific logic:

### `pre-tool-use-skill-file-size-guard.py` — hard block

`PreToolUse`, matchers `Write` and `Edit` (not `NotebookEdit` — a `SKILL.md` is never a notebook). Computes the file's *resulting* size before the write/edit lands: for `Write`, the UTF-8 byte length of `tool_input.content`; for `Edit` (whose `tool_input` carries only `old_string`/`new_string`/`replace_all`, not the resulting content), reads the current on-disk file and applies the same substitution. If the resulting size would exceed a configurable limit (`skill_file_size_limit_bytes` in `.claude/hook-config.json`, default 262144), blocks via `_hookout.emit_block()` — exit 2, stderr — naming the size, the limit, and the remediation (split into a reference file the `SKILL.md` links to, citing Anthropic's own progressive-disclosure pattern).

### `skill-file-size-advisory.py` — non-blocking nudge

`PostToolUse`, matchers `Write` and `Edit`. The write has already landed by this point, so this simply `os.path.getsize()`s the real file and, if it's at or above a lower watermark (`skill_file_size_warn_bytes`, default 204800 / 200KB) — independently configurable from the hard limit — emits the same `_hookout.emit_block()` mechanism. On `PostToolUse` this does **not** block anything (there's nothing left to block); exit 2 here only *surfaces* the message to the model, since PostToolUse has no non-blocking model-visible channel (`_hookout.py`'s documented channel table). This is the same established mechanism `memory-write-advisory.py` already uses for a non-blocking PostToolUse nudge.

Both hooks fail **open** on their own crash (`except Exception: sys.exit(0)`) and call `_hookutil.record_heartbeat()` as the first statement of `main()` (ADR-106).

Wired in `claude/settings.json` as additional entries appended to the existing `hooks` arrays under `PreToolUse.Write`/`PreToolUse.Edit` (the guard) and `PostToolUse.Write`/`PostToolUse.Edit` (the advisory) — not new `"matcher"` blocks, following the repo's existing pattern of stacking multiple hook commands per matcher (e.g. `PostToolUse.Write` already runs both `memory-write-advisory.py` and `journal-shard-write-advisory.py`).

---

## Judgment calls

### `_hookout.emit_block()`, not a hand-rolled `{"reason": ...}` writer

The closest precedent *by tool-matcher shape*, `pre-tool-use-worktree-path-check.py`, hand-rolls its block message as `sys.stderr.write(json.dumps({"reason": ...})) + sys.exit(2)`. But the *most recently merged* `PreToolUse` blocking hook, `pre-tool-use-nested-agent-background-guard.py` (ADR-126, merged the day before this one), already uses the shared `_hookout.emit_block()` helper — and there's an open, tracked migration (referenced in `_worktree_recovery.py`'s docstring, dev-env#865) to move the older hand-rolled hooks *onto* `_hookout`. `docs/REFERENCE.md`'s hook authoring rule 6 is explicit that new hooks should use `_hookout.emit_advisory`/`emit_block` rather than a hand-rolled `print`/`sys.stderr.write`. Building a new hand-rolled hook the same week that migration is tracked would compound debt rather than follow the currently-live convention. `emit_block()` also makes the two new hooks' output identical in shape for free (plain ASCII stderr text, no JSON envelope), which matters for the advisory hook specifically — see the next point.

### The advisory hook reuses `emit_block()`, not a distinct `emit_advisory(blocking=False)` call

A `PostToolUse` hook has no non-blocking, model-visible channel at all (`_hookout.py`'s own documented contract: plain exit-0 stdout is transcript-only there, and `additionalContext` JSON is not honored either). The only way to put text in front of the model on `PostToolUse` is the same exit-2-stderr channel a block uses — `memory-write-advisory.py` already established this exact pattern (call it a block, deliver it via exit 2, but the tool already ran so nothing is actually undone). `emit_block()`'s name is block-centric, so the advisory hook's module docstring says explicitly that exit 2 here surfaces a message rather than blocking anything, to avoid a future reader assuming otherwise.

### Two independently-configurable thresholds, not one derived from the other

`skill_file_size_warn_bytes` and `skill_file_size_limit_bytes` are separate `.claude/hook-config.json` fields with independent defaults (200KB / 256KB) and independent fallback behavior, rather than the advisory computing its watermark as a fixed percentage of the hard limit. This lets a project raise or lower either threshold without the other silently moving, matching every other numeric field already in this config file (`turn_threshold`, `idle_refresher_minutes`, etc. — see `docs/REFERENCE.md`'s Configuration table).

### `SKILL.md` only, not bundled reference/script files

Confirmed with the user before implementation: reference/resource files bundled alongside a `SKILL.md` are explicitly "no practical limit" per Anthropic's own docs (they cost nothing until read), and the operational risk this guard exists for — a file always loaded in full at trigger time — is specific to `SKILL.md` itself. Basename-only matching also keeps both hooks' logic trivial (one string comparison, no directory-structure assumptions).

### Strictly-greater-than for the hard block, inclusive-or-equal for the advisory

The guard blocks only when `size > limit` (exactly-at-limit passes) — a limit is a ceiling, not a value the file can't legitimately reach. The advisory fires at `size >= warn_bytes` (exactly-at-watermark advises) — a watermark is a "you're here or past it" signal, and firing one byte later than the boundary would silently skip the boundary case itself.

### 256KB is not attributed to Anthropic's public docs

Verified via primary source (the two docs pages cited above) that no such byte limit is published. The 256KB default is documented here as an engineering ceiling based on the user's own observed error, not a claimed public specification — see the CLAUDE.md Documentation and Citations rule ("if no authoritative primary source exists, explicitly label the recommendation as based on observed behavior").

### Scope: `Write`/`Edit` only, not `Bash`

Same deferral `pre-tool-use-worktree-path-check.py` (ADR-024) already documents for itself: a shell redirect, heredoc, or `cp`/`tee` writing a `SKILL.md` is not intercepted. `Write`/`Edit` cover the overwhelming majority of real `SKILL.md` authoring (manual editing, `skill-creator`-assisted generation) and have a well-defined path field in structured tool input, unlike a Bash command string. Extend only if a recurrence is observed through that surface.

### No retroactive enforcement

Both hooks fire only on new `Write`/`Edit` calls. A `SKILL.md` that predates the guard and already exceeds either threshold is untouched until it's next written or edited — at which point the guard hook still lets it *shrink* (an edit that reduces size below the limit is allowed even starting from an already-oversized file; see `test_edit_that_shrinks_below_limit_passes`).

---

## Consequences

- Any `Write`/`Edit` targeting a file named `SKILL.md` (case-insensitive), in any project, that would leave it over 256KB (default, configurable) is now blocked with a clear remediation message instead of silently landing.
- A `SKILL.md` crossing 200KB (default, configurable) gets a non-blocking heads-up before ever reaching the hard ceiling.
- No-op cost for every other file: one cheap basename comparison, no I/O, before either hook does any real work.
- Coverage gap: `Bash`-based writes to a `SKILL.md` are not covered (same deferral as ADR-024).
- No change to any existing hook, script, or shared module — both new hooks are pure additions that only import already-shared `_hookout`/`_hookutil`.
- Covered by `claude/scripts/tests/test_skill_file_size_guard.py` and `claude/scripts/tests/test_skill_file_size_advisory.py` (Testing items 85/86).

---

## References

- `claude/scripts/pre-tool-use-skill-file-size-guard.py` — hard-block implementation
- `claude/scripts/skill-file-size-advisory.py` — advisory implementation
- `claude/scripts/tests/test_skill_file_size_guard.py`, `claude/scripts/tests/test_skill_file_size_advisory.py` — self-tests
- `claude/settings.json` — hook wiring
- [ADR-024](024-worktree-path-guard-hook.md) — the closest architectural precedent (`PreToolUse` guard on `Write`/`Edit`/`NotebookEdit`), including its Bash-scope deferral this ADR mirrors
- [ADR-103](103-shared-hookout-emitter.md) — the `_hookout` emitter and its per-event channel contract this ADR relies on for both the block and the advisory
- [ADR-106](106-hook-heartbeat-liveness-ledger.md) — the `record_heartbeat()` requirement both hooks satisfy
- [ADR-126](126-nested-agent-spawn-background-guard.md) — the most recently merged `PreToolUse` blocking hook, and the direct precedent for using `_hookout.emit_block()` over a hand-rolled writer
- `brownm09/dev-env#939` — tracking issue
- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — Level 1/2/3 loading model; the basis for why `SKILL.md` specifically (not bundled files) is the risk this guard targets
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — the "under 500 lines" soft guidance and progressive-disclosure pattern cited in both hooks' block/advisory messages
- [Using Agent Skills with the API](https://platform.claude.com/docs/en/build-with-claude/skills-guide) — the 30MB total-bundle upload limit (confirmed unrelated to this guard's scope)
