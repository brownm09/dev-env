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

### Why not make the pre-filter per-trigger-aware too

The cheap pre-filter substring/regex check (`"merged"` / `gh issue create` / `spawn_task`) still runs
as one combined OR-check regardless of which individual triggers are already resolved. Scoping it
tighter (e.g., skip the `"merged"` branch once trigger 1 alone is resolved) would save a marginal
number of reparses in a narrow window — a session where exactly one or two triggers are resolved and
the transcript happens to contain a stale, irrelevant `"merged"` mention — at the cost of real
complexity in a hot, already-subtle bit of code. The combined check remains **correct** (it never
under-fires: any signal any open trigger needs still passes the gate); it is simply not maximally
tight. Left as-is, consistent with dev-env#677's own framing of the fix as "avoid a full transcript
re-parse ... for a session that has already fully resolved everything" — a fully-resolved session (all
three sentinels set) already gets the fast, first-line short-circuit; a partially-resolved session
re-parsing on a subsequent Stop is the accepted, bounded cost this ADR takes on.

## Consequences

- The dev-env#677 bug is fixed: a trigger whose condition arises after a sibling trigger has already
  fired or resolved is still independently evaluated and can still fire.
- A session where every trigger has already fired or resolved still short-circuits before reading the
  transcript at all — unchanged from the pre-fix behavior.
- A session where only some triggers are resolved now re-parses the transcript on each subsequent
  Stop (rather than short-circuiting immediately) until every trigger is resolved — a deliberate,
  bounded performance trade-off; see "Why not make the pre-filter per-trigger-aware" above.
- Each trigger's own "fires/resolves at most once per session" semantics (ADR-088's accepted
  session-global-enumeration limitation) is now scoped precisely to that trigger, rather than
  incidentally suppressing its siblings.

### Testing

`test_stop_tile_enumeration_gate.py` grows from 112 to 115 tests, 0 failures. All 112 pre-existing
tests pass **unmodified**, including the three existing "sentinel suppresses a second fire" e2e tests
— traced by hand and confirmed: each of those fixtures only ever satisfies ONE trigger's precondition,
so the other two triggers evaluate to their own "nothing to resolve yet" no-op on every call
regardless of sentinel state, and the assertions hold unchanged under the new per-trigger design. Three
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

## Limitations (documented, accepted)

- **Still session-global per trigger, not per event.** A trigger merging TWO PRs in the same session
  and enumerating only once still isn't independently re-flagged for the second merge — this was
  already ADR-088's accepted limitation and is unchanged by this ADR; only the *cross-trigger*
  suppression is fixed, not the *within-trigger* one.
- **Partially-resolved sessions re-parse on every subsequent Stop** until every trigger resolves (see
  Consequences above) — an accepted, bounded performance trade-off, not a correctness gap.
- **No migration for pre-existing single-sentinel files** — see "Why not migrate" above; a live
  deploy racing an in-flight session may re-evaluate once extra, self-correcting immediately.

## Alternatives considered

- **A single JSON state file per session** (e.g. `{"pr": true, "issue": false, "table": true}`)
  instead of three flag files. Rejected: diverges from the established sentinel-is-file-existence
  convention every other hook in this family uses (ADR-064), for no benefit — three independent files
  are exactly as cheap to check and need no new read/parse/write logic.
- **Leave the shared sentinel and instead loosen `main()`'s early-exit to always re-parse.** Rejected:
  this would silently regress the fully-resolved-session fast path the original design (and
  dev-env#677 itself) explicitly wanted preserved — every Stop in every session would re-pay the full
  parse cost forever, including sessions that resolved everything in turn one.
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
