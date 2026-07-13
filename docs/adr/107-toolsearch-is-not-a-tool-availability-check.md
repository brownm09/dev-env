# ADR-107: `ToolSearch` Answers "Is This Tool Deferred?", Not "Is This Tool Available?"

**Date:** 2026-07-13
**Status:** Accepted
**Tags:** claude-behavior, tool-discovery, tools, tool-search, spawn-task, tiles, workflow, global-rule, correction

---

## Context

A session working PR8 of the #717 hook-reliability initiative (dev-env#752) reached the
post-merge tile checkpoint, correctly identified a genuine follow-up (PR9, PowerShell matcher
coverage — already tracked at [dev-env#620](https://github.com/brownm09/dev-env/issues/620)), and
correctly decided it warranted a `spawn_task` tile per the Git Workflow → *Capture follow-ups as
tiles* rule. It then called `ToolSearch` with query `"spawn_task"`, received zero results, and
concluded from that alone that `spawn_task` "isn't available in this session" — invoking the rule's
own documented fallback ("Where `spawn_task` is unavailable ... file the follow-up issue anyway")
even though that fallback did not apply. No tile was spawned; the session's final summary
substituted a prose note instead. The user caught this on the next turn.

The tool was available the entire time. `mcp__ccd_session__spawn_task` is a directly-defined tool
in the system prompt's tool list — never deferred. `ToolSearch`'s own description states plainly
that it searches *deferred* tools: ones announced by name only in a system-reminder, whose full
parameter schema must be fetched before they are callable. A tool that is already fully defined
does not need finding, and correspondingly does not appear in `ToolSearch`'s index — that absence
is by design, not a signal of unavailability.

The root cause is a conflation of two distinct questions:

1. **"Is this tool deferred (does it need a `ToolSearch` schema fetch before I can call it)?"** —
   this is the question `ToolSearch` answers.
2. **"Is this tool available to me at all in this session?"** — answered by the full tool
   inventory already present in the system prompt: both directly-defined tools (fully callable
   immediately) and the deferred-tool *names* listed in system-reminders (callable after a
   `ToolSearch` fetch).

A zero-result `ToolSearch` only ever answers question 1 in the negative for tools that were never
deferred — it says nothing about question 2. Treating "not found by `ToolSearch`" as synonymous
with "unavailable" silently drops exactly the kind of follow-up the tile-checkpoint mechanism
([ADR-046](046-post-merge-followup-tiles.md), [ADR-088](088-state-keyed-tile-enumeration-gate.md))
exists to guarantee doesn't get dropped — and the failure mode generalizes to any other
directly-available tool, not just `spawn_task`.

## Decision

Add a new, standalone **"Tool Discovery"** section to the global `claude/CLAUDE.md` (placed after
*Platform & Environment*, before the environment-specific scripting guidance) stating the rule
plainly: before concluding any tool is unavailable in a session, check the full tool list already
present in the system prompt first, not just `ToolSearch`'s result set. The section names the
motivating incident and issue ([dev-env#754](https://github.com/brownm09/dev-env/issues/754)) so
the rule is anchored to a concrete failure rather than reading as abstract advice.

This is deliberately a general tool-discovery discipline note, not a `spawn_task`-specific patch —
folding it into the *Capture follow-ups as tiles* bullet (where the original mistake surfaced)
would scope the fix too narrowly to the one tool that happened to trigger it this time, when the
underlying confusion (deferred-vs-available) applies equally to any tool in any context.

## Consequences

- A session that reflexively reaches for `ToolSearch` to "check" a tool now has an explicit,
  global instruction to look at its own tool inventory first — reducing the odds of the identical
  false-negative recurring with `spawn_task` or any other directly-available tool.
- The *Capture follow-ups as tiles* rule's existing "where `spawn_task` is unavailable" fallback
  remains correct and necessary for genuine unavailability cases (documented elsewhere as
  occurring in some terminal sessions) — this ADR does not change that fallback, only guards
  against it being invoked incorrectly.
- No code, hook, or test changes — this is a pure `claude/CLAUDE.md` instruction addition; the
  `## Testing` docs-only guard (item 4) was run and found no `date -u` regressions.

## Alternatives considered

- **Fold the note into the existing "Capture follow-ups as tiles" bullet** instead of a new
  section. Rejected — that bullet is already long, and the fix belongs to tool-discovery in
  general, not tile-spawning specifically; a reader hunting for "why did `ToolSearch` return
  nothing" would not think to look inside the tiles workflow bullet.
- **Rely on memory alone** (a `feedback`-type entry noting the mistake) without a `CLAUDE.md`
  edit. Rejected per the *Durable Preferences & Memory* section's own standing rule — a durable,
  cross-session workflow correction must live in the instructions, not only in a private,
  per-session memory cache.
- **No fix — treat as a one-off mistake.** Rejected; the user explicitly asked for a permanent,
  global fix, and the failure mode (conflating "not deferred" with "not available") is structural
  to how this harness exposes tools, not specific to the one session that hit it.

## References

- [dev-env#754](https://github.com/brownm09/dev-env/issues/754) — the incident and issue this ADR
  resolves.
- [dev-env#717](https://github.com/brownm09/dev-env/issues/717) /
  [dev-env#752](https://github.com/brownm09/dev-env/pull/752) — the session in which the mistake
  occurred (PR8 of the hook-reliability initiative).
- [ADR-046](046-post-merge-followup-tiles.md), [ADR-088](088-state-keyed-tile-enumeration-gate.md),
  [ADR-094](094-tile-tables-and-issue-per-tile.md) — the tile-checkpoint mechanism this mistake
  caused a false-negative against.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — the standing rule that durable
  workflow corrections belong in instructions, not memory alone, which this ADR's fix follows.
