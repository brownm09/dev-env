# ADR-091: Deliver the journal-stop-check Archive Reminder to Claude (exit 2 + stderr, not exit-0 stdout)

**Date:** 2026-07-08
**Status:** Accepted
**Tags:** hooks, stop, journal, archive, session-archiving, exit-code, stderr, claude-facing, adr-021, adr-088

---

## Context

After a journal stub is pushed to `engineering-journal`, `stub-push-archive-reminder.py`
(a PostToolUse/Bash hook) writes a `~/.claude/scratch/stub-pushed.flag` sentinel. The
`journal-stop-check.py` **Stop** hook then consumes that sentinel and emits an archive
reminder:

> "Stub committed and pushed to engineering-journal. Archive this session now: call
> `ccd_session_mgmt__archive_session` (use `list_sessions` to look up the current
> session_id if needed). Then stop."

That reminder is **Claude-facing**: it instructs the invocation of an MCP tool only Claude
can call (`mcp__ccd_session_mgmt__archive_session`) and then to stop. Both sibling
docstrings confirm the intent ("remind **Claude** to archive"). The intended behavior is
"Claude auto-calls `archive_session` at session end after a journal-stub push."

But the hook emitted the reminder via `print(...)` + `sys.exit(0)`. Per the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks), for a **Stop** hook,
exit-0 **stdout** is *"written to the debug log but not shown in the transcript"* and is
**not** added to Claude's context — only `UserPromptSubmit`, `UserPromptExpansion`, and
`SessionStart` get exit-0 stdout added to context. Only **exit 2 + stderr** feeds text back
to Claude on a Stop hook (blocking the stop so Claude continues with the stderr reason).

So the archive reminder never reached Claude. The intended session-archiving silently never
happened. This is the exact failure class [ADR-088](088-state-keyed-tile-enumeration-gate.md)
identified and fixed for the tile gate — *"an exit-0 advisory would add another ignorable
line … Advisory (exit 0) instead of blocking. Would be ignored — the exact mechanism-2
failure. Rejected."* ADR-088 even records the pre-existing state: *"`journal-stop-check.py`
… always exits 0 and emits its reminders on stdout."*

Surfaced during the [dev-env#612](https://github.com/brownm09/dev-env/issues/612) /
[PR #613](https://github.com/brownm09/dev-env/pull/613) Stop-hook parallelism investigation;
tracked as [dev-env#622](https://github.com/brownm09/dev-env/issues/622).

## Decision

Convert **only the archive-reminder branch** of `journal-stop-check.py` to **exit 2 +
stderr**, mirroring `stop-tile-enumeration-gate.py` (ADR-088):

- When the stub-push sentinel is present, write the archive instruction to `stderr` and
  `sys.exit(2)` — the channel that reaches Claude for a Stop hook. Nothing is written to
  stdout on this path (Claude Code ignores a Stop hook's stdout on exit 2).
- The reminder text is extracted into a pure `archive_reminder_message()` and kept
  **ASCII-only** — Claude Code pipes hook output as cp1252 on Windows, so a non-cp1252
  character (an arrow, an em-dash) would raise `UnicodeEncodeError` and the whole reminder
  would vanish (same constraint `stop-tile-enumeration-gate.py` /
  `posttooluse-inert-advisory.py` observe).

### The other three message types stay NON-blocking (exit 0, stdout)

The same hook also emits an orphaned-draft-removed FYI, a **stale-draft advisory**, and an
**unmerged-draft-branch advisory**. These are **user-facing** and point at work for a
*later, dedicated* session:

- Journal composition is a dedicated-session operation that **must never be triggered
  proactively** (`claude/CLAUDE.md` → "Never compose proactively"). Blocking the stop to
  make Claude compose now would violate that rule.
- Merging a stale draft PR is separate work, not this session's job.

So those three remain exit-0 stdout advisories, unchanged. Only the archive reminder — the
one message that asks *Claude* to take an in-session action — blocks.

### Loop safety

The archive block fires **at most once** with no Stop-loop risk, via two guards:

1. **Consume-on-read (primary).** `consume_stub_pushed_sentinel()` deletes the sentinel
   *before* returning the reminder. Once the block fires, the flag is gone, so the next Stop
   finds no reminder and exits 0. (If the `unlink` fails, the function returns `None` and
   never blocks — the flag can't both persist *and* trigger a block.)
2. **`stop_hook_active` (backstop).** The consume is gated on `not stop_hook_active`, the
   documented Claude Code anti-loop flag: while Claude is continuing from a prior block the
   hook does not consume-or-block, so the reminder is delivered on a genuine (non-continuation)
   Stop rather than being consumed without delivery.

### No settings change

`journal-stop-check.py` is already registered in the `Stop` list in `claude/settings.json`,
invoked via `pyw -3`. The tile gate — also a blocking (exit 2 + stderr) Stop hook invoked
via `pyw -3` — proves that path delivers stderr correctly (ADR-007's 2026-06-01 pyw-stdio
decision). No registration or invocation change is needed. Stop hooks run in **parallel**
(ADR-088 → Stop-hook parallelism), so this second blocking hook does not short-circuit
`awake-blocker.py`'s sleep-lock release, and two blocking hooks can each exit 2 on the same
Stop with both stderr reasons merged and fed to Claude.

## Consequences

- After a journal-stub push, the Stop is blocked once and Claude actually receives the
  archive instruction — the intended session-archiving now happens.
- If the `ccd_session_mgmt__archive_session` MCP tool is unavailable in a given session
  (e.g. a headless/cron run without that server), the single block is harmless: Claude
  notes it cannot archive and stops; the flag is already consumed, so no re-block.
- `journal-stop-check.py` becomes the **second** blocking Stop hook (with
  `stop-tile-enumeration-gate.py`). ADR-088's "first and only blocking Stop hook" note is
  corrected accordingly.
- Rollback is a one-file revert of the branch logic; the sentinel-file contract
  (`stub-pushed.flag`, consumed once) is unchanged.

## Alternatives considered

- **Keep it exit-0 stdout (status quo).** Rejected — the reminder never reaches Claude; the
  feature is inert. This is the ADR-088 mechanism-2 failure.
- **JSON `{"decision":"block","reason":...}` / `additionalContext` instead of exit 2 +
  stderr.** A valid Stop-hook mechanism, but exit 2 + stderr is the pattern already proven
  in this codebase under `pyw -3` (the tile gate) and keeps the two blocking Stop hooks
  consistent. Rejected in favor of consistency and a proven path.
- **Block on the stale-draft / unmerged-branch advisories too.** Rejected — those are
  user-facing and point at dedicated-session follow-up work; blocking the stop for them
  would violate the "never compose proactively" rule and add no value.
- **Amend ADR-088 instead of a new ADR.** Rejected — the archive reminder is a distinct
  feature (session archiving, ADR-021 lineage) from the tile gate (post-merge tiles,
  ADR-046 lineage). It shares only the *failure class*. A separate ADR is more discoverable;
  it cites ADR-088 for the shared rationale rather than duplicating it, and ADR-088's stale
  parenthetical is corrected with a cross-reference.

## References

- [ADR-088](088-state-keyed-tile-enumeration-gate.md) — the Stop-hook exit-2 blocking
  pattern this mirrors, and the "exit-0 Stop advisory is invisible/ignored" rationale.
- [ADR-021](021-auto-stub-on-pr-push.md) — the journal-stub / archive-reminder flow this
  fixes the delivery of.
- [ADR-007](007-hook-command-invocation.md) — the `pyw -3` stdio decision that makes exit 2
  + stderr deliverable from a windowless Stop hook.
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the exit-code /
  stdout-vs-stderr / per-event Stop semantics quoted above.
- [dev-env#612](https://github.com/brownm09/dev-env/issues/612) /
  [PR #613](https://github.com/brownm09/dev-env/pull/613) — the investigation that surfaced
  this.
- [dev-env#622](https://github.com/brownm09/dev-env/issues/622) — the issue this ADR closes.
