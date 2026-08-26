# ADR-140 — Unchained-Merge Look-Ahead Gate, with a Ranked Open-Issue Workstream Fallback

**Date:** 2026-08-25
**Status:** Accepted
**Closes:** [dev-env#1044](https://github.com/brownm09/dev-env/issues/1044) (sub-issue 2 of [dev-env#1043](https://github.com/brownm09/dev-env/issues/1043))
**Tags:** hooks, stop-hook, tiles, spawn-task, forward-chaining, look-ahead, ask-user-question, workstream, priority-ranking, blocking-gate, trigger-table, global-rule, claude-facing, adr-046, adr-088, adr-094, adr-097, adr-109, adr-118, adr-137
**Related:** [ADR-137](137-proactive-tile-forward-chaining.md), [ADR-088](088-state-keyed-tile-enumeration-gate.md), [ADR-097](097-per-trigger-tile-gate-sentinels.md), [ADR-109](109-tile-gate-deferral-question-trigger.md), [ADR-046](046-post-merge-followup-tiles.md), [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-118](118-tile-persistence-shards.md), [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md), [ADR-090](090-shared-transcript-readers-hookutil.md)

---

## Context

[ADR-137](137-proactive-tile-forward-chaining.md) established that every tile spawn is a
look-ahead moment: before finalizing a `spawn_task` prompt, ask whether that tile's completion
will predictably surface or unblock a next high-priority thread, and either bundle it or
instruct the tile to chain it. That rule is **prose only**. Its own *Alternatives considered*
deferred a hook:

> **A mechanical hook enforcing the look-ahead check.** Rejected as premature… "will this
> tile's completion predictably surface or unblock a next high-priority thread" is a semantic
> priority judgment, not a pattern a hook can reliably detect. A hook can follow later if the
> anti-pattern recurs despite the documented rule.

That deferral was right about the *judgment*, and it left two gaps around it
([dev-env#1043](https://github.com/brownm09/dev-env/issues/1043)):

1. **No forcing function.** A session can merge a PR and end with nothing queued at all. The
   nearest existing enforcement, `post-merge-tile-checkpoint.py` (ADR-060), fires on every
   merge but only says "spawn tiles for work you already know about" — it has no transcript,
   so it cannot tell whether the session ever *had* a next thread, and it is inert entirely in
   background / `spawn_task` sessions ([ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)).
2. **No defined fallback when nothing is known.** ADR-137 assumes a next thread is identifiable
   from the session's own work. When it is not, no rule says what to do, and no code anywhere
   under `claude/` surveys open issues to answer "what next." ADR-137's own motivating incident
   involved a session that *had* ranked four threads and still dropped the second-ranked one.

The `stop-tile-enumeration-gate.py` (ADR-088) trigger family is the natural home for gap 1: it
is state-keyed rather than command-keyed, so it sees every merge path including auto-merge, and
the Stop event still dispatches in background sessions where every PostToolUse hook is silent.
[dev-env#696](https://github.com/brownm09/dev-env/issues/696) / [PR #1048](https://github.com/brownm09/dev-env/pull/1048)
(sub-issue 1, landed first as a hard finish-to-start dependency under
[ADR-132](132-sequential-tile-spawning-for-dependency-chains.md)) collapsed that hook's five
hand-synchronized per-trigger structures into a `_TriggerSpec` table, so adding a trigger is now
one row.

## Decision

**Add a sixth trigger — "unchained merge" — to `stop-tile-enumeration-gate.py`, and a paired
CLAUDE.md rule that carries the ranking order the hook cannot.**

### The trigger (mechanical half)

`evaluate_workstream(records)` fires when **all four** hold:

- a PR reached merged state this session (`session_merged_prs`, **reused** — its direct-marker /
  REST-fallback / observed-auto-merge detection is the single source of truth for "a PR merged",
  and a second copy would be exactly the drift [ADR-090](090-shared-transcript-readers-hookutil.md)
  hoisted the shared readers to prevent);
- the session's **opening prompt is not chain-bearing**;
- no `spawn_task` ran (`session_spawned_tiles`, reused); and
- no `AskUserQuestion` ran (`session_asked_user`, a new detector structurally mirroring
  `session_spawned_tiles`' bare-verb `tool_use.name` match).

**Chain-bearing** means the session's **first genuine (non-synthetic) user prompt** contains a
same-repo issue/PR reference (`#\d+`), a `github.com/…/issues|pull/\d+` URL, or the `=== CHAIN`
marker. Reading it needs a new shared helper, `_hookutil.first_user_prompt_text(records)`,
composing the existing `_user_message_texts` and `_is_synthetic_user`; no hook read the opening
prompt before this one.

`blocking=True`. Every input is an objectively verifiable fact — a merge marker, a `tool_use`
name, a regex over one fixed string — not a natural-language judgment, so it does **not** ride
[ADR-109](109-tile-gate-deferral-question-trigger.md)'s advisory channel. The pre-filter clause
reuses trigger 1's `"merged" in c.lower` verbatim, so the addition costs no new scan.

Resolution is `skip_override`, a real `spawn_task`, or a real `AskUserQuestion` — the two paths
the rule below actually prescribes. Two further cases resolve as **out of scope**: a
chain-bearing opening prompt, and no readable opening prompt at all.

### The rule (judgment half)

One paragraph appended to the **"Capture follow-ups as tiles"** bullet in `## Git Workflow`,
immediately after ADR-137's paragraph — the established edit site this bullet's whole ADR family
([ADR-113](113-cross-session-handoff-tiles.md), [ADR-123](123-forward-link-phase-dependent-followons.md),
[ADR-132](132-sequential-tile-spawning-for-dependency-chains.md), ADR-137) uses. It carries what
a hook cannot: **chain the known thread first**; only when none is determinable, rank the repo's
open issues — (1) `start-here`-labeled, (2) the first unchecked item of the newest open
`retro-action` checklist issue, (3) open issues in the merged PR's own subsystem, (4)
most-recently-updated — dedupe against `sessions/<project>/tiles/` shards and `list_sessions`,
offer the top 3 via `AskUserQuestion`, and tile the choice.

The rule states explicitly that this ask is the **one bounded carve-out** to the
"never ask the user whether or when to tile" rule: legitimate only when no follow-up is
determinable, never as a substitute for chaining a known one.

`post-merge-tile-checkpoint.py`'s exit-2 message gains a one-line pointer to the same
look-ahead + survey path, so the reinforcement also lands at merge time when context is
freshest. The Stop trigger stays the real gate (ADR-053).

## Rationale

- **The hook enforces the floor; the prose keeps the judgment.** ADR-137 was right that "will
  this predictably surface a next thread" is not hook-detectable. But "a PR merged, the session
  was handed nothing, and nothing was queued" *is* — it is three tool-name/marker facts and one
  regex. Splitting the rule at that seam is what makes a hook possible without the
  false-positive surface ADR-137 feared.
- **The opening prompt is the right scope test, and the only honest one.** A session whose tile
  prompt names an issue was *given* its next thread; whatever it chains after that is ADR-137's
  prose rule to judge. A mid-session mention of an issue number is deliberately **not**
  consulted: it says nothing about what the session was *asked* to do, and consulting it would
  let any passing `#N` in a diff, a `gh` output, or a quoted CLAUDE.md line silently disarm the
  trigger.
- **`#\d+` is intentionally broad, and safe *because of where it is applied*.** Over-matching is
  the safe direction here (an extra match means the trigger does not fire), exactly inverting
  the anchored command-shape regexes elsewhere in the file, where under-matching is safe. It
  runs against one fixed string — the first genuine user prompt — so the "a decoy inside a
  heredoc body" hazard (the dev-env#499 class) has no surface to exploit.
- **An unreadable opening prompt must not block.** When no genuine user record exists, the scope
  test cannot be established at all. For a *blocking* trigger whose false positive costs a
  blocked stop and a pointless self-correction, "cannot establish" must mean "do not fire."
- **Out-of-scope resolves rather than defers, because the input is immutable.** The first
  genuine user prompt never changes for the life of a session, so a chain-bearing (or
  unreadable) session can never become in-scope later. Marking the sentinel restores ADR-097's
  skip-the-parse fast path instead of re-scanning an ever-growing transcript on every remaining
  Stop.
- **Enumeration text deliberately does not resolve it** — the same call
  [ADR-109](109-tile-gate-deferral-question-trigger.md) made for its own trigger, for the same
  reason. "Follow-ups considered: none → not tiled, because nothing surfaced" is precisely the
  decision this trigger exists to question; accepting it as resolution would let the trigger be
  waved off by the very sentence it targets.
- **A spawn resolves this trigger while still arming triggers 3 and 3b.** That asymmetry is
  established, not new — those two already demand a table and a shard from any spawn that
  resolves trigger 1 — and it is what keeps "chained the next thread" from silently excusing
  the tile's own table/shard obligations ([ADR-094](094-tile-tables-and-issue-per-tile.md),
  [ADR-118](118-tile-persistence-shards.md)).
- **The ranking order belongs in prose, not in the hook.** Reading labels, `retro-action`
  checklists, and issue recency requires live `gh` calls; this hook is a pure transcript scan
  with no network, no subprocess, and no filesystem reads by design (ADR-088). Encoding the
  ranking here would break that property for a list that will keep evolving.

## Alternatives considered

- **Extend trigger 1 (merged PR) rather than add a sixth trigger.** Rejected. The two ask
  different questions and accept different resolutions: trigger 1 asks "was an enumeration
  recorded" and accepts enumeration *text*; this asks "was a next thread queued" and
  specifically does not. Folding them would force one of the two to relax, and the pair also
  needs independent sentinels — a session can legitimately resolve one and not the other in the
  same turn.
- **Make it advisory (a systemMessage), like ADR-109's trigger.** Rejected. ADR-109 rides the
  advisory channel because its input is a natural-language pattern match that can be wrong. This
  trigger's four inputs are all objectively verifiable, and a Stop hook has no non-blocking
  *model-visible* channel at all — an advisory here would reach the user but never Claude, which
  is precisely who must act on it.
- **Fire on every merge, ignoring the opening prompt.** Rejected — it would block every chained
  tile session that correctly deferred its own chaining decision to ADR-137's prose rule, i.e.
  most sessions, making the gate noise rather than signal.
- **Have the hook itself run the ranked open-issue survey and name candidates.** Rejected: it
  would require `gh` calls from a Stop hook that is deliberately network- and subprocess-free,
  and would bake a still-evolving ranking into code where the CLAUDE.md rule can carry it.
- **Amend ADR-137 rather than write a new ADR.** Rejected under the
  [ADR-071](071-canonical-checkout-mutate-guard-hook.md) litmus test ADR-137 itself applied:
  an amendment covers "same file, same mechanism, same harm model, only a previously
  unrecognized surface." This changes the *mechanism* (a blocking Stop-hook trigger where
  ADR-137 has only prose) and the *harm model* (a session ending idle after a merge, versus
  priority context dropped across a session boundary), and it reverses ADR-137's own recorded
  decision to defer a hook. ADR-137 gets a dated amendment pointing here instead.

## Consequences

**Positive:** a merge in an unchained session can no longer end with nothing queued; the
"nothing is determinable" case now has a defined, ranked answer instead of being undefined; the
enforcement survives background / `spawn_task` sessions where the command-keyed checkpoint is
inert; ADR-137's deferred hook exists without the false-positive surface it was deferred over.

**Negative / residual:**

- **The `AskUserQuestion` resolution is coarse.** Any `AskUserQuestion` this session resolves the
  trigger, not specifically one offering the ranked survey — a session that asked the user
  something unrelated satisfies it. Accepted deliberately: the alternative is inspecting question
  *content*, which reintroduces exactly the natural-language judgment this trigger avoids. The
  CLAUDE.md rule is what makes the ask the right one; the hook only guarantees the session
  reached one of the two paths.
- **Session-global, not per-merge.** Like every trigger in this file since ADR-088, one
  resolution satisfies the session — a two-merge session that chains one thread is not asked
  again for the second. Same documented, accepted limitation.
- **One more `iter_bash_calls` / `session_merged_prs` pass per Stop in merged sessions**, matching
  what `evaluate` and `evaluate_deferral` already each do. Bounded by the shared pre-filter,
  which still skips the parse entirely for any session with no merge signal.
- **The bullet grows again.** Accepted for the same reason its four predecessors accepted it: one
  dense edit site is cheaper to keep coherent than a fragmented one.

## References

- [dev-env#1043](https://github.com/brownm09/dev-env/issues/1043) — top-level issue (both gaps).
- [dev-env#1044](https://github.com/brownm09/dev-env/issues/1044) — sub-issue this ADR closes.
- [dev-env#696](https://github.com/brownm09/dev-env/issues/696) / [PR #1048](https://github.com/brownm09/dev-env/pull/1048)
  — the `_TriggerSpec` table this trigger plugs into (sub-issue 1, hard prerequisite).
- [ADR-137](137-proactive-tile-forward-chaining.md) — the prose look-ahead rule this enforces;
  amended to record that its deferred hook has landed.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md) — the state-keyed gate and its
  session-global limitation.
- [ADR-097](097-per-trigger-tile-gate-sentinels.md) — per-trigger sentinels and the
  `_TriggerSpec` table amendment; this trigger gets its own `workstream-` sentinel.
- [ADR-109](109-tile-gate-deferral-question-trigger.md) — the deferral-question trigger; the
  "never ask, tile it" rule this ADR's survey path is the one bounded carve-out to, and the
  precedent for refusing enumeration text as resolution.
- [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) — why the Stop trigger, not
  the merge-time checkpoint, is the real gate.
- [ADR-090](090-shared-transcript-readers-hookutil.md) — the shared-reader home
  `first_user_prompt_text` joins, and the drift rationale for reusing `session_merged_prs`.
- [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-118](118-tile-persistence-shards.md) —
  the table and shard a chained tile still owes, which this trigger's resolution does not excuse.

## Amendment (2026-08-26) — review of PR #1053 closed three correctness gaps and one self-contradiction

`/review` on the PR that introduced this trigger found three gaps in the mechanical half and one
place the code contradicted this ADR's own Rationale. All four are fixed in the same PR, before
merge — this amendment updates the record rather than leaving the Decision/Rationale sections
above describing behavior the code no longer has.

- **Resolution is now scoped to AT OR AFTER the merge, not "anywhere in the session."** The
  original `session_spawned_tiles(records) or session_asked_user(records)` scanned the WHOLE
  transcript with no positional relationship to the merge it was supposed to gate — a spawn or ask
  from earlier in the session, for unrelated reasons, satisfied it. `_records_after_merge` (a new
  helper, delegating all merge detection to `session_merged_prs` re-invoked on growing prefixes of
  `iter_bash_calls`, never reimplementing it) now excludes pre-merge evidence. Quadratic in the
  session's Bash-call count, not transcript size, and paid only on the already-narrow subset of
  Stops where a merge was already detected.
- **A same-session `gh issue create` is now a third resolution path**, alongside `spawn_task` and
  `AskUserQuestion` — closing the gap where the trigger's only two resolutions were tool calls,
  with no path matching CLAUDE.md's own documented fallback ("file the follow-up issue anyway")
  for a session where `spawn_task` itself is unavailable. `session_created_issues` (the existing
  trigger-2 detector) is reused, not reimplemented, and is subject to the same after-the-merge
  scoping as the other two paths.
- **`first_user_prompt_text` now also skips a `<command-name>` slash-command wrapper**, not just
  synthetic (`isMeta`/`isCompactSummary`) records — a `/review`-launched or otherwise
  command-wrapped session used to have its scope decided by whatever the wrapper's own machinery
  text happened to contain. The prefix constant is hoisted from
  `stop-journal-stub-checkpoint.py`'s own local copy into `_hookutil.py`, so the two hooks can no
  longer drift on what counts as a wrapper (the exact duplication ADR-090 exists to prevent).
- **A bare `#\d+` no longer over-matches ordinary ordinal/step prose** ("do step #2 of the
  runbook"). This ADR's own Rationale called that over-matching "the SAFE direction" — true only
  against a false *block*; the branch it feeds returns `(None, True)`, which the hook writes as a
  RESOLVED sentinel, so an over-match instead silently and PERMANENTLY disarmed the trigger for
  the rest of the session. `_has_chain_issue_reference` narrows the bare-`#N` case with a small,
  deliberately incomplete denylist of common ordinal/step words, while still matching a bare `#N`
  at a sentence boundary and a repo-prefixed compound (`dev-env#696`) unconditionally.
  `_CHAIN_ISSUE_REF_RE` also gained `re.ASCII`, mirroring `_TILE_SHARD_PATH_RE`.
- **`format_workstream_reminder` no longer restates the survey ranking.** This ADR's own
  Rationale ("The ranking order belongs in prose, not in the hook... a list that will keep
  evolving") argued directly against exactly what the reminder did — spell out the four-step
  ranking verbatim, additionally pinned by a test asserting `"start-here"`/`"retro-action"`
  present. The reminder now points at `claude/CLAUDE.md` only, matching
  `post-merge-tile-checkpoint.py`'s own reminder; the ranking still lives in exactly one place.
- **The `=== CHAIN` marker's ADR-094 citation was wrong**, in the hook's own docstring, in
  `claude/CLAUDE.md`, and in `docs/REFERENCE.md`. ADR-094 mandates only that a tile prompt
  reference its tracking issue (the case the `#N`/URL forms already detect) — it says nothing
  about a `=== CHAIN` marker, which is the `retro-chain-refill` skill's own convention
  ([ADR-132](132-sequential-tile-spawning-for-dependency-chains.md) /
  [ADR-137](137-proactive-tile-forward-chaining.md)). All three sites now cite the marker's real
  source.

The Decision and Rationale sections above are left describing the pre-fix shape (two resolution
paths, unscoped, a broad `#\d+`, the ranking restated) as a matter of record — read them alongside
this amendment, not in place of it, for what the trigger actually does today. The Positive
consequence claiming the trigger "survives background / `spawn_task` sessions" is now more fully
true than it was: it previously had no resolution path at all for a session where `spawn_task`
itself is unavailable (the "some terminal sessions" case CLAUDE.md's own fallback names); the
third resolution path closes that specific gap, though the general residual — a session able to
call NEITHER `spawn_task` NOR `AskUserQuestion` NOR `gh issue create` still gets exactly one
blocked turn before `stop_hook_active` lets it through unresolved — is unchanged, and was already
documented as bounded (Consequences → Session-global, not per-merge applies the same way here).
