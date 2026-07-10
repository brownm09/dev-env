# ADR-097: Per-Trigger Sentinels for the Tile-Enumeration Gate

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** hooks, stop, tiles, spawn-task, sentinel, correction, adr-088, adr-092, adr-094

---

## Context

[ADR-088](088-state-keyed-tile-enumeration-gate.md) introduced `stop-tile-enumeration-gate.py` with
one Stop-hook trigger (merged PR) guarded by one scratch-file sentinel, firing at most once per
session. [ADR-092](092-dangling-issue-tile-enumeration-gate.md) added a second trigger (dangling
created issue) onto the **same** sentinel, explicitly documenting the result as an extension of
ADR-088's already-accepted "session-global enumeration" limitation. [ADR-094](094-tile-tables-and-issue-per-tile.md)'s
addendum added a **third** trigger (tiles spawned without a table, dev-env#656 / PR #674) onto the
same single sentinel again, per that task's explicit instruction to "reuse the gate's existing
machinery." That PR's own review flagged the compounding cost of doing this a third time and opened
[dev-env#677](https://github.com/brownm09/dev-env/issues/677) to track a proper fix, deferring it
rather than rushing a redesign of shared gating logic mid-PR.

**The bug.** All three triggers shared ONE `tile-enumeration-gate-<session_id>.flag` file. Whichever
trigger fired or resolved FIRST caused `_mark_fired(session_id)` to write that one file, and
`main()`'s top-of-function check (`if session_id and sentinel_path(...).exists(): sys.exit(0)`) then
skipped evaluating **all three** triggers on every subsequent Stop in the session — including a
trigger whose own condition had not even occurred yet. Concretely: a PR merges with no tile spawned
yet (trigger 1 fires and sets the sentinel); a tile is then spawned in a genuinely later, separate
turn and the table heading is never emitted; trigger 3 never gets the chance to catch it, because the
sentinel it would need to check was never trigger 3's own — it belonged to trigger 1.

This was tolerable while only triggers 1/2 shared ADR-092's "session-global enumeration" limitation
(a second merge in the same session not being independently re-flagged is a narrower, already-accepted
gap), but became materially worse once trigger 3 landed: trigger 3's bar (an emitted table) is
*stricter* than "an enumeration happened" (ADR-094's own key finding — a spawn resolves triggers 1/2
but not 3), so the class of "a sibling trigger's earlier resolution silently disables a completely
different, still-unsatisfied trigger" grew from a narrow edge case into a realistic single-session
sequence: merge → (triggers 1/2 resolve) → spawn a tile later with no table → trigger 3 silently
never fires.

## Decision

Split the single shared sentinel into three independent per-trigger sentinel files, reusing the
existing `_hookutil.sentinel_path(prefix, session_id)` mechanism unchanged — just three distinct
prefixes instead of one:

```python
SENTINEL_PREFIX = "tile-enumeration-gate-"
_TRIGGER_PR = "pr-"
_TRIGGER_ISSUE = "issue-"
_TRIGGER_TABLE = "table-"
```

producing `tile-enumeration-gate-pr-<session_id>.flag`, `-issue-<session_id>.flag`, and
`-table-<session_id>.flag`. `_hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)` needs no change —
its glob (`f"{prefix}*.flag"`) already matches all three suffixed filenames, since each still starts
with `SENTINEL_PREFIX`.

`main()` restructures around this:

1. **Up front**, check all three sentinels. Only when **all three** are already set does the hook
   skip reading the transcript at all — the genuinely-nothing-left-to-check fast path the original
   single sentinel also had, preserved unchanged for a fully-resolved session.
2. Otherwise, read and pre-filter the transcript exactly as before (the combined `"merged"` /
   `gh issue create` / `spawn_task` OR-check is untouched — it remains a correct, if not maximally
   tight, superset gate regardless of which individual triggers are still open).
3. **Evaluate only the still-open triggers** — a trigger whose sentinel is already set is skipped
   entirely (not re-evaluated, not re-fired), preserving each trigger's own "session-global
   enumeration" semantics exactly as before (e.g. a session merging two PRs and enumerating only once
   is still not re-flagged for the second — ADR-088's accepted limitation, now scoped per trigger
   rather than lost across all three).
4. **Mark each fired-or-resolved trigger's own sentinel** before emitting any message (preserving the
   original "sentinel set before emit" re-entrancy guard, just applied per trigger instead of once
   globally).

No pure evaluator (`evaluate`, `evaluate_issues`, `evaluate_tile_table`, `session_merged_prs`,
`enumeration_recorded`, `skip_override`, etc.) changes at all — only `main()`'s sentinel bookkeeping
and the two small helpers it uses (`_trigger_sentinel_path`, `_mark_trigger_fired`, replacing the
single `_mark_fired`). All 112 pre-existing tests pass **unmodified** against the restructured file.

### Why not migrate the old single-sentinel files

A session already mid-flight when this change deploys may have an old-format
`tile-enumeration-gate-<session_id>.flag` file on disk with no corresponding new-format file. No
migration logic was added: the old file simply becomes an inert orphan (none of the three new checks
ever look for it), self-expiring via the existing 30-day `cleanup_stale_sentinels` sweep (its glob
still matches the old filename shape too, since it also starts with `SENTINEL_PREFIX`). Worst case is
one extra evaluation immediately after a live deploy mid-session — self-correcting the instant the new
sentinel is written — and not a new class of risk: this repo has no precedent of migrating
sentinel-file formats when ADR-092 or ADR-094 changed what the shared sentinel meant, and a hook
redeploy racing a live session is an accepted, unaddressed edge case throughout this file's history.

### The pre-filter is also gated per trigger (review finding)

An initial version of this ADR left the cheap pre-filter substring/regex check (`"merged"` / `gh
issue create` / `spawn_task`) as one combined OR-check regardless of which individual triggers were
already resolved, reasoning that the extra reparses this caused were confined to "a narrow window."
Independent review of the PR implementing this ADR found that framing understated the cost: a
transcript signal never disappears once written — `"merged"` remains in the text for the rest of the
session after the very first merge, which is also the single most common trigger — so an *unqualified*
combined check would in practice force a full reparse on **every remaining Stop of the session**
whenever trigger 1 had ever fired, not merely in some narrow window. That is the common case (a
session that merges, then keeps working), not an edge case.

The pre-filter is therefore gated per trigger: each clause becomes
`already_done[trigger] or <original per-trigger check>`, so a trigger's clause is satisfied
unconditionally the moment that trigger is resolved, regardless of what stale signal text remains in
the transcript, while an unresolved trigger's clause is unaffected (reduces to the original
unconditional check). This restores the parse-skipping fast path the instant every trigger with a
live signal in the transcript is either resolved or genuinely absent — a materially stronger guarantee
than "only when all three are resolved," and the one dev-env#677 itself asked for ("avoid a full
transcript re-parse ... for a session that has already fully resolved everything"). The added
complexity is three `already_done[...] or` guards, not a new mechanism.

## Consequences

- The dev-env#677 bug is fixed: a trigger whose condition arises after a sibling trigger has already
  fired or resolved is still independently evaluated and can still fire.
- A session where every trigger has already fired or resolved still short-circuits before reading the
  transcript at all — unchanged from the pre-fix behavior.
- A session where only some triggers are resolved skips re-parsing the transcript on a subsequent Stop
  as soon as every trigger with a live signal in the transcript is either resolved or genuinely absent
  — not only once all three are fully resolved (see "The pre-filter is also gated per trigger" above).
- Each trigger's own "fires/resolves at most once per session" semantics (ADR-088's accepted
  session-global-enumeration limitation) is now scoped precisely to that trigger, rather than
  incidentally suppressing its siblings.

### Testing

`test_stop_tile_enumeration_gate.py` grows from 112 to 116 tests, 0 failures. All 112 pre-existing
tests pass **unmodified**, including the three existing "sentinel suppresses a second fire" e2e tests
— traced by hand and confirmed: each of those fixtures only ever satisfies ONE trigger's precondition,
so the other two triggers evaluate to their own "nothing to resolve yet" no-op on every call
regardless of sentinel state, and the assertions hold unchanged under the new per-trigger design. Four
new tests cover the fix directly:

- **The dev-env#677 regression itself**: a two-turn simulation (same session_id, same isolated HOME,
  two separate hook invocations) — turn 1 merges a PR with no enumeration (fires and resolves trigger
  1 only); turn 2's transcript additionally contains a tile spawned with no table (simulating that the
  spawn happened in a later, separate Stop). Turn 2 must still fire on trigger 3 — reproducing exactly
  the bug this ADR fixes, and failing under the pre-fix single-sentinel code.
- **Precise per-trigger sentinel-file footprint after a partial session**: a merge-only session sets
  ONLY the `pr-` sentinel file on disk; the `issue-`/`table-` files must NOT exist, since neither
  trigger had a condition to resolve yet and must stay open for one that could still arise later.
- **All three sentinels set after a fully-compliant session**: a session resolving all three triggers
  in one turn sets all three sentinel files, and a second Stop with the same transcript stays exit 0
  (stable, no spurious re-fire) — the practical, externally-observable form of the fully-resolved fast
  path (the internal "was the transcript read at all" distinction is not independently observable
  through the hook's black-box exit-code/stdout/stderr contract, since a fully-resolved session
  produces exit 0 whether or not the short-circuit fires first; this test instead pins the *outcome*
  the short-circuit exists to guarantee stays correct).
- **Stale resolved-trigger signal text does not block a later, distinct trigger**: a three-turn
  sequence — turn 1 merges (fires and resolves trigger 1); turn 2 is a pure continuation with no new
  signal (stays exit 0); turn 3, with trigger 1's stale `"merged"` text still present throughout, spawns
  a tile with no table — proves the per-trigger pre-filter gating does not let an already-resolved
  trigger's leftover transcript text suppress detection of a later, genuinely new, different trigger.

## Limitations (documented, accepted)

- **Still session-global per trigger, not per event.** A trigger merging TWO PRs in the same session
  and enumerating only once still isn't independently re-flagged for the second merge — this was
  already ADR-088's accepted limitation and is unchanged by this ADR; only the *cross-trigger*
  suppression is fixed, not the *within-trigger* one.
- **A genuinely still-open trigger's own signal keeps forcing a re-parse.** The per-trigger pre-filter
  gate (see above) eliminates the reparse cost for *resolved* triggers, but a trigger that stays
  unresolved for many turns (e.g. a dangling created issue never closed) has a signal (`gh issue
  create`) that, once written, is also permanent — every subsequent Stop still re-parses until that
  trigger resolves. This is the same cost the pre-fix design always had for the still-unresolved case;
  it is now scoped to only the genuinely open trigger(s) rather than the whole session.
- **No migration for pre-existing single-sentinel files** — see "Why not migrate" above; a live
  deploy racing an in-flight session may re-evaluate once extra, self-correcting immediately.
- **Four parallel per-trigger structures in `main()`.** Each trigger's identity is now spread across
  the `_TRIGGERS` tuple, the evaluate block's hardcoded skip-default tuples, the sentinel-marking
  loop's `fired` test, and the message-building block's per-trigger formatter call — with a silent
  None-vs-bool asymmetry between the PR/issue and table triggers. A fully generic loop was considered
  and deferred (see Alternatives) as a net complexity wash given the triggers' heterogeneous shapes;
  tracked as [dev-env#696](https://github.com/brownm09/dev-env/issues/696) for reconsideration if a
  4th trigger is ever proposed.

## Alternatives considered

- **A single JSON state file per session** (e.g. `{"pr": true, "issue": false, "table": true}`)
  instead of three flag files. Rejected: diverges from the established sentinel-is-file-existence
  convention every other hook in this family uses (ADR-064), for no benefit — three independent files
  are exactly as cheap to check and need no new read/parse/write logic.
- **Leave the shared sentinel and instead loosen `main()`'s early-exit to always re-parse.** Rejected:
  this would silently regress the fully-resolved-session fast path the original design (and
  dev-env#677 itself) explicitly wanted preserved — every Stop in every session would re-pay the full
  parse cost forever, including sessions that resolved everything in turn one.
- **A fully generic loop over trigger descriptors** (`(trigger, evaluator, skip_default, fire_test,
  formatter)` tuples iterated once) to collapse the four parallel structures noted in Limitations.
  Deferred: the three evaluators don't share a return-shape convention (PR/issue are Optional-first,
  table is bool-first) or a formatter signature (two take a numeric argument, one takes none), so a
  fully generic version needs a 5th parallel element and heterogeneous lambda-based dispatch — trading
  four simple, explicit parallel lists for one denser, more abstract shape, which is not clearly a net
  maintainability gain. Tracked as [dev-env#696](https://github.com/brownm09/dev-env/issues/696) for
  reconsideration if a 4th trigger is ever proposed.
- **Fold the fix into ADR-094's addendum instead of a new ADR.** Rejected: ADR-094's addendum already
  documents the pre-fix limitation and explicitly forward-references dev-env#677 as separate follow-up
  work; recording the fix as its own ADR keeps the paper trail matching the actual PR history (a
  distinct PR, closing a distinct issue) rather than retroactively rewriting an already-accepted ADR's
  addendum.

## References

- [dev-env#677](https://github.com/brownm09/dev-env/issues/677) — issue this ADR closes.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md) — the original single sentinel and its
  "session-global enumeration" limitation this ADR narrows to per-trigger scope.
- [ADR-092](092-dangling-issue-tile-enumeration-gate.md) — the second trigger added onto the same
  shared sentinel, extending the limitation unchanged.
- [ADR-094](094-tile-tables-and-issue-per-tile.md) — the third trigger (PR #674) whose review found
  and filed this issue; its addendum's "Limitations" section documents the pre-fix state in detail.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — the shared `_hookutil` sentinel-path
  convention this ADR reuses unchanged (three prefixes, not a new mechanism).
