# ADR-109: Deferral-Question Trigger for the Tile-Enumeration Gate, and a Sharper Post-Merge Reminder

**Date:** 2026-07-14
**Status:** Accepted
**Tags:** hooks, stop, post-tool-use, tiles, spawn-task, post-merge, enforcement, natural-language-detection, advisory, adr-046, adr-059, adr-060, adr-088, adr-092, adr-094, adr-097, adr-103

---

## Context

While closing out dev-env PR #762 (PR9 of the #717 hook-reliability initiative), the closing session
identified PR10 (a known, already-scoped-out next unit of the same multi-PR initiative, per
[ADR-059](059-multi-pr-issue-hierarchy.md)) as a genuine follow-up — the user's own original task
prompt had explicitly said "tile PR10 next if you finish with capacity" — but instead of tiling it, the
session wrote a "Remaining" note asking the user whether to start it now or defer to a fresh session.
The user called this out directly and confirmed it is a **repeated** pattern, not a one-off, and asked
for "a more involved fix."

**Root cause.** The "tile it yourself, never ask whether or when" rule already exists in plain language
in `claude/CLAUDE.md` (Git Workflow → Capture follow-ups as tiles): *"Create a `spawn_task` tile for
each genuine follow-up the moment you identify it ... tiling it yourself, never asking the user whether
or when to do it."* The failure was not unawareness of the rule — it was misclassifying a completed
multi-PR initiative's next unit as "the immediate next step of the task in progress" (which the Session
Summaries rule says to just list) rather than "a genuine follow-up" (tile it). Since the task in
progress (the just-merged PR) is by definition done, that carve-out does not actually apply — but the
boundary is subtle enough to be misjudged under real working conditions, repeatedly. Reinforcing the
prose again is unlikely to fix a judgment/classification failure a plainly-worded rule has already
failed to prevent multiple times; this repo's own established response to exactly that shape of problem
— see [ADR-046](046-post-merge-followup-tiles.md), [ADR-088](088-state-keyed-tile-enumeration-gate.md),
[ADR-092](092-dangling-issue-tile-enumeration-gate.md) — is to stop relying on recall and enforce it
mechanically instead.

## Decision

Two independent fixes, one PR.

### Layer 1 — sharpen the already-firing command-keyed reminder

`claude/scripts/post-merge-tile-checkpoint.py` ([ADR-060](060-post-merge-tile-checkpoint-hook.md))
already fires a blocking reminder on every successful `gh pr merge`. Its text now explicitly names this
anti-pattern:

> "...This includes the next not-yet-started unit of a multi-PR initiative (ADR-059) if one exists — do
> not convert it into a scheduling/permission question back to the user ('let me know if you want me to
> start it now'); tile it the same as any other follow-up. ..."

Zero new detection logic, no new risk — the sharpened text takes effect on the very next merge. (The
existing test file for this hook only exercises the `is_successful_merge()` predicate, not message
content, so no test needed updating for this layer.)

### Layer 2 — a new, advisory-only trigger on the state-keyed gate

`claude/scripts/stop-tile-enumeration-gate.py` already restructures around three independent,
per-trigger-sentineled triggers (merged PR / dangling issue / tiles-without-a-table — ADR-088/092/094,
per-trigger sentinels via [ADR-097](097-per-trigger-tile-gate-sentinels.md)). This ADR adds a **fourth**:
**deferral-question**.

**Detection.** A bounded, deliberately narrow set of regexes (`_DEFERRAL_QUESTION_RES`) matching the
motivating incident's shape and close variants:

```python
_DEFERRAL_QUESTION_RES = (
    re.compile(r"let me know if you (?:want|\'d like) me to", re.IGNORECASE),
    re.compile(r"\bshould i (?:start|begin|implement|proceed|go ahead|do this|tackle)\b",
               re.IGNORECASE),
    re.compile(r"\bwant me to (?:start|begin|implement|proceed|tackle|do (?:this|that))\b"
               r".{0,40}\bnow\b", re.IGNORECASE),
)
```

checked only against **assistant text** (mirrors `table_marker_present`'s assistant-only scoping — a
user message or tool_result merely containing one of these phrases must never count). This is a
natural-language pattern match, not an objectively verifiable fact — unlike triggers 1-3, a false
positive is possible (a legitimate design question can resemble the pattern).

**Scoping.** Only evaluated in sessions where a PR merged or an issue was created this session (the same
contexts triggers 1/2 already key on) — a deferral-question phrase entirely outside that context is far
more likely to be an unrelated, legitimate question than an instance of this anti-pattern, and scoping
keeps the false-positive surface bounded to sessions that already have follow-up-worthy activity.

**Resolution — deliberately NOT `enumeration_recorded`.** This is the one design decision that took a
false start to get right (see "A rejected first draft" below): resolution requires only
`skip_override` (an explicit "skip tiles" user instruction), **not** the broader
`enumeration_recorded` triggers 1/2 accept. The phrase's own presence already means, by this trigger's
definition, that the deferred item was punted to the user as a question rather than given a proper
"-> tiled" / "-> not tiled, because \<reason\>" disposition — that IS the violation, independent of
whatever else happened in the session.

**Emission — advisory, not blocking.** Because this is a heuristic text match rather than an objective
fact, a fire here does **not** block the stop via exit 2 (which would force Claude into a pointless
self-correction loop on a maybe-false alarm). It rides `_hookout.emit_advisory("Stop", ...,
audience="user")` — a `systemMessage` toast, exit 0 — so a human judges the specific case, the same way
the user caught the motivating incident directly in chat. `_hookout.py`'s channel contract
([ADR-103](103-shared-hookout-emitter.md)) makes this a one-line call once the routing decision is made;
see `_hookout.plan_emission`'s documented `STDOUT_MODEL_VISIBLE_EVENTS` table for why a `Stop` event has
no non-blocking *model*-visible channel, only the user-visible `systemMessage` one.

**Precedence when triggers overlap.** Only one exit code is possible per hook invocation. When any of
triggers 1-3 also fire in the same turn, their blocking exit-2 reminder is emitted as before, and the
deferral advisory is *skipped this turn* — not lost forever, since a persisting condition can still
surface on a later Stop once the harder trigger resolves. The harder enforcement wins; see `main()`'s
emission block.

### A rejected first draft (worth recording)

The first implementation reused `enumeration_recorded` for resolution, mirroring triggers 1/2 exactly.
Walking that draft through the actual motivating incident exposed the flaw before it shipped: in the
real session, *other* genuine follow-ups (two unrelated tiles) were correctly spawned in the very same
turn the deferral question was asked about a *different* item (PR10). `enumeration_recorded` is
session-global by design ("any `spawn_task` counts" — ADR-088's own accepted limitation) — so the
first-draft trigger would have been silently resolved by those unrelated spawns and would never have
fired for the one utterance it exists to catch. Dropping `enumeration_recorded` from the resolution
condition (leaving only `skip_override`) fixes this at the cost of a higher fire rate than triggers 1-3
— an accepted tradeoff specifically because this trigger is advisory, not blocking (see the module
docstring and `evaluate_deferral`'s own docstring for the full reasoning). This was caught by walking
the implementation against the real incident by hand, not by a `/review` pass — recorded here so a
future edit doesn't quietly reintroduce the same, previously-rejected shortcut.

## Consequences

- A future recurrence of the exact motivating pattern — a known follow-up asked about instead of tiled,
  even alongside other correctly-tiled work in the same turn — now surfaces as a `systemMessage` the
  user sees at Stop, rather than depending entirely on the user independently noticing and calling it
  out again.
- The command-keyed `post-merge-tile-checkpoint.py` reminder is more specific for every future merge,
  at zero marginal enforcement risk.
- `stop-tile-enumeration-gate.py` now has one trigger whose emission channel structurally diverges from
  its three siblings (advisory vs. blocking) — `main()`'s final emission block is now an `if
  messages: block / elif fire_defer: advisory` shape rather than a uniform "collect messages, block if
  any" loop. This is a deliberate, documented divergence (see Decision above), not an oversight.
- [dev-env#696](https://github.com/brownm09/dev-env/issues/696) — ADR-097's own forward-reference to "if
  a 4th trigger is ever proposed," anticipating reconsideration of a fully generic trigger-descriptor
  loop — is not addressed by this PR (see Alternatives below). This ADR does not close it.

### Testing

`test_stop_tile_enumeration_gate.py` grows from 116 to 135 tests, 0 failures; all 116 pre-existing tests
pass **unmodified**. 19 new tests:

- **Detection** (6): the three phrasings each independently detected; an unrelated design question
  ("should I use approach A or B") correctly NOT matched (not one of the bounded verb phrasings); a user
  record and a tool_result each containing the phrase correctly NOT counted (assistant-text-only scope).
- **`evaluate_deferral` composition** (6): fires after a merge; fires after an issue-create (the scoping
  condition's other branch); resolved by an explicit skip override; **the regression pin for the
  rejected-first-draft bug** — an unrelated `spawn_task` elsewhere in the session does NOT resolve the
  trigger (reproduces the motivating incident's exact shape: other genuine follow-ups tiled, the
  specific deferred item still asked about); no-op with no merge/issue-create context; no-op with no
  phrase present.
- **`format_deferral_reminder`** (1): ASCII/cp1252-encodable, per this file's established convention for
  every `format_*_reminder`.
- **Interaction** (1, pure): the deferral trigger fires independently of a sibling merge trigger already
  resolved via a genuine spawn.
- **Behavioral/e2e** (5): the motivating-incident shape end-to-end (merge resolved via spawn+table,
  deferral phrase about a different item) → exit 0 with a `systemMessage`, empty stderr; an
  un-enumerated merge with the deferral phrase also present → exit 2 naming the merge trigger, the
  advisory *not* also emitted (precedence); a cleanly-resolved session with no deferral phrase → exit 0,
  no `systemMessage` at all (no false-positive injection into an otherwise-silent session); the
  "should i" phrasing after an issue-create, resolved via explicit close + tabled spawn, isolating only
  the deferral trigger → exit 0 advisory (proves the pre-filter's second OR-branch and the issue-create
  scoping path end-to-end, not just via the pure-function layer); sentinel suppression (first fire emits
  the advisory, second stays silent).

Two of the four e2e fixture drafts initially failed against the real hook (not the deferral logic
itself): adding a bare `_asst_spawn()` to resolve the merge/issue trigger via `enumeration_recorded`
simultaneously arms trigger 3 (tiles-spawned-without-a-table), which then blocks since no table heading
was present — an interaction between this new trigger's test fixtures and the *existing* trigger 3 that
had to be worked out by adding the table heading to the "otherwise fully resolved" fixture. Recorded here
since it is exactly the kind of multi-trigger interaction this file's tests need to keep getting right as
more triggers accumulate.

## Limitations (documented, accepted)

- **Heuristic, not exhaustive.** The three phrasings in `_DEFERRAL_QUESTION_RES` are deliberately narrow
  — a real instance of this anti-pattern phrased differently (not matching any of the three) will not
  be caught. Extending the phrase list is expected maintenance, not a design flaw to fix upfront; err
  toward a few concrete, high-precision phrasings over a broad pattern that would raise the
  false-positive rate on an advisory channel meant to stay trustworthy.
- **Higher false-positive rate than triggers 1-3 by design** — see "Resolution" above. Accepted because
  the cost of a false positive here is a moment of the user's attention on a `systemMessage`, not a
  blocked or looping session.
- **No correlation between the deferred item and later tiling.** If the user later asks Claude (in a
  fresh turn) to go ahead and tile the exact item the deferral question named, this trigger has no way
  to recognize that as resolution short of an explicit "skip tiles" — it will still have fired once and
  set its own sentinel (suppressing a second fire this session), so this is a one-time, not a repeating,
  cost.
- **[dev-env#696](https://github.com/brownm09/dev-env/issues/696) not addressed.** See Alternatives.

## Alternatives considered

- **Key the trigger off ADR-059 sub-issue relationships** (check GitHub for a sibling sub-issue of the
  same top-level issue, still open) instead of text-pattern matching. Rejected: in the motivating
  incident, PR10 had no sub-issue yet at the time of the failure — there was nothing to detect via this
  path. A transcript-only linguistic check catches the failure regardless of whether a sibling issue
  exists yet, which a GitHub-relationship check structurally cannot.
- **Block via exit 2 like triggers 1-3.** Rejected: this is a natural-language pattern match, not an
  objectively verifiable fact (unlike "did a `gh pr merge` succeed" or "does this exact heading string
  appear"). Blocking on a heuristic with real false-positive risk would force Claude into a
  self-correction loop on legitimate questions that happen to resemble the pattern — the advisory
  channel exists precisely to let a human, not Claude, make that call.
- **Address [dev-env#696](https://github.com/brownm09/dev-env/issues/696)'s generic-loop refactor in
  this same PR**, since this is exactly the "4th trigger" ADR-097's Limitations section anticipated.
  Deferred, not addressed: trigger 4 diverges from 1-3 not only in return-shape (as 1-3 already do
  amongst themselves) but in **emission channel** (advisory vs. blocking) — a dimension the original
  #696 proposal never had to account for. A fully generic dispatch loop is further from a clean
  abstraction now, not closer; folding this decision into the present PR would risk a rushed,
  under-designed generalization. Left open for dedicated reconsideration, flagged explicitly here rather
  than silently dropped.
- **A single combined regex instead of a tuple of three.** Rejected for the same reason
  `_ENUM_MARKERS`/`_SKIP_RE` are already tuples of several narrow patterns rather than one dense
  alternation: each phrasing is independently reasoned about, tested, and extended without the others'
  boundaries interacting in a single expression.

## References

- [dev-env#772](https://github.com/brownm09/dev-env/issues/772) — tracking issue.
- [ADR-046](046-post-merge-followup-tiles.md) — the tile-now discipline this trigger mechanically
  enforces a specific violation of.
- [ADR-059](059-multi-pr-issue-hierarchy.md) — the multi-PR decomposition convention whose "next unit"
  is the motivating incident's concrete shape.
- [ADR-060](060-post-merge-tile-checkpoint-hook.md) — the command-keyed hook Layer 1 sharpens.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md),
  [ADR-094](094-tile-tables-and-issue-per-tile.md), [ADR-097](097-per-trigger-tile-gate-sentinels.md) —
  the three prior triggers and the per-trigger-sentinel architecture this ADR's trigger 4 is added onto.
- [ADR-103](103-shared-hookout-emitter.md) — the shared emitter whose channel-routing table makes the
  advisory-not-blocking emission a one-line call.
- [dev-env#696](https://github.com/brownm09/dev-env/issues/696) — the generic-trigger-loop
  reconsideration this ADR explicitly declines to address; still open.
