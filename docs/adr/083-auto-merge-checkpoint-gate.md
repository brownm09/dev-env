# ADR-083 — Mechanical Pre-Check Gate for `gh pr merge --auto` Checkpoints

**Date:** 2026-07-05
**Status:** Accepted — implemented in the PR that closes [dev-env#574](https://github.com/brownm09/dev-env/issues/574).
See "Implementation" below for what shipped and where it diverged from this design. **The Context
and Implementation sections below describe `allow_auto_merge` state as of their respective dates —
for current per-repo status, see the dated addenda at the end of this file (2026-07-06, 2026-07-07)
before citing either section as present-tense fact.**
**Tags:** git, pr, merge, auto-merge, workflow, hooks, pre-tool-use, review, adr-warrant,
doc-reconciliation
**Related:** [ADR-031](031-auto-merge-disabled.md), [ADR-039](039-merge-gate-findings-enforcement.md),
[ADR-011](011-adr-warrant-check.md), [ADR-019](019-doc-reconciliation-enforcement.md),
[ADR-028](028-all-findings-merge-gate.md), [ADR-071](071-canonical-checkout-mutate-guard-hook.md)

---

## Context

ADR-031's 2026-07-04 addendum ([dev-env#565](https://github.com/brownm09/dev-env/issues/565))
resolved `gh pr merge --auto`'s "Per-PR escape hatch" as **permanently inert by design**: the
repo-level `allow_auto_merge` toggle stays `false` across every `brownm09/*` repo because it is
currently the *only mechanical backstop* preventing `--auto` from being invoked before six
in-session merge-time rules have run. It named the precise condition that would justify
revisiting the question:

> a mechanical pre-check that verifies, at the moment `--auto` is invoked, that `/review`,
> ADR-warrant checkpoint 3, and doc-reconciliation checkpoint 3 have already completed in the
> current session... Only these three are named because they are the only ones a
> pre-invocation check could ever verify: the other three rules (`usage-snapshot.py`, the
> post-merge journal stub, project-board automation) are tied to the physical merge moment
> itself, which `--auto` always defers to an async, out-of-session event.

This ADR is that design. [dev-env#574](https://github.com/brownm09/dev-env/issues/574) scoped it
as research/design work: propose the mechanism, don't necessarily ship it in the same sitting.
That scoping turned out to be the right call — the honest answer for two of the three
checkpoints is that **no mechanical signal for them exists anywhere today**, which is a design
problem in its own right, not just a hook-plumbing exercise.

---

## What a `PreToolUse` hook can and cannot verify

A `PreToolUse` hook on `Bash` sees `tool_name`, `tool_input.command`, `cwd`, and `session_id` on
stdin, plus whatever it chooses to fetch live (via `gh`, `git`, etc.). It has no access to the
conversation, no way to inspect what Claude reasoned about, and no API for "did skill X run this
session." **The only thing a hook can ever mechanically verify is that some artifact exists whose
existence is contingent on the checkpoint having been consciously addressed.** Every enforcement
mechanism already in this repo — the suppression grep, the test-integrity grep, ADR-039's
`/review` marker — reduces to this same shape: pick an artifact, check for its presence.

This reframes the design question from "how does a hook know a checkpoint happened" (unanswerable
in general) to "what artifact should each of the three checkpoints produce, and where should it
live" (answerable, and the actual content of this ADR).

For `/review`, that artifact already exists: the `<!-- review-findings: blocking=N
non_blocking=M -->` marker comment ADR-039 already ships and gates. For ADR-warrant checkpoint 3
and doc-reconciliation checkpoint 3, **no such artifact exists today.** Both are pure prose
bullets in `claude/CLAUDE.md` / project CLAUDE.md files that a session self-executes as reasoning,
with nothing produced at the end of it. (Doc-reconciliation has a *partial* mechanical presence —
`/review` Step 2b already turns a missing README/REFERENCE.md update into a blocking finding, which
does flow into the existing marker's `blocking` count — but that's the Step-2b *early-warning*
check, not a record that checkpoint-3's "immediately before merge" re-evaluation specifically
happened. ADR-warrant checkpoint 3 has no mechanical presence of any kind, partial or otherwise:
`/review`'s SKILL.md has no ADR-warrant step at all.) Designing the artifact for these two is the
actual hard part of this task.

---

## Candidate mechanisms evaluated

### (a) Session-local marker file — evaluated, not recommended

A file (keyed by `session_id`, written by `/review` and by the checkpoint-3 logic, read and
cleared by the hook) is the task's first candidate. Two sub-questions decide it:

**Who writes the marker for ADR-warrant / doc-reconciliation?** There is no existing
artifact-producing action at checkpoint 3 to piggyback on the way `/review`'s marker piggybacks on
`/review` itself (a thing Claude is already doing for its own sake). A session-local file doesn't
change that — Claude would still have to *remember* to invoke a "write the marker" step, which is
exactly as skippable as remembering to evaluate the checkpoint in the first place. This isn't a
strike against local files specifically (the same "who writes it" question applies to option (b)
below too), but it does mean a local file buys nothing extra here.

**Where local files lose to a PR-durable artifact, concretely:**

1. **Worktree/session lifetime mismatch.** `prune-merged-worktrees.py` treats worktrees as
   ephemeral, and a session can `/compact` or end between "checkpoint evaluated" and "`--auto`
   invoked." A session-scoped file has no guaranteed lifetime spanning that gap unless
   deliberately placed outside the worktree and keyed carefully — solvable, but it's solving a
   problem a PR comment doesn't have at all (a PR comment outlives every worktree and every
   session by construction).
2. **Not independently auditable.** Every other mechanical gate in this repo leaves a trace a
   human can read directly on GitHub (the suppression/integrity greps run against a visible diff;
   ADR-039's disposition section is a PR-body paragraph). A local file is invisible to anyone
   glancing at the PR — a strictly worse transparency story for no compensating benefit.
3. **Session-scoping is actually the wrong invariant, not just an inconvenient one.** A
   plausible, legitimate workflow is "review the PR now, come back in a later session once CI has
   been green a while, then `--auto`." A strictly session-scoped marker would force re-running all
   three checkpoints on every session boundary even though nothing about the PR changed —
   a false negative a PR-durable artifact doesn't produce. The real invariant to protect is
   "has *this PR, in its current form*, had these checkpoints evaluated" — which is a property of
   the PR, not of any one session. (The freshness check in the Decision below still catches the
   case that matters: the PR changing *after* the marker was written.)

**Verdict:** rejected. A session-local file solves none of (a) doesn't create the missing artifact
for the two checkpoints that need one, and (b) is strictly worse than a PR-durable artifact on
every axis that matters here.

### (b) PR-durable marker, extending ADR-039's proven pattern — recommended

ADR-039 already shipped, tested, and battle-proven exactly this shape for `/review`:
`gh pr view --json comments,body` → find the last comment carrying a machine marker → check the
PR body for a disposition when findings are outstanding. It's reused by `pre-merge-numbering-check.py`
and `pr-merge-reminder.py` via shared `_hookio` primitives, and it has its own regression suite
(`test_pre_merge_findings_gate.py`, `test-merge-findings-gate.sh`, using the `MERGE_GATE_TEST_JSON`
seam to exercise every branch without a cross-platform `gh` stub).

The natural extension: since ADR-011 and ADR-019 both define checkpoint 3 as "immediately before
`gh pr merge`," and `/review` is *already* run at exactly that point in the documented flow
(`gh pr create → stub → /compact → /review --post-comment → address findings → merge`), giving
`/review` two more things to evaluate and record costs nothing structurally — it's one more fact
recorded in an artifact Claude is already producing, at exactly the right moment, for exactly the
right reason. This is detailed in the Decision section below.

### (c) Other alternatives considered and rejected

- **Human-applied label/approval** (e.g., require a human to add an `auto-merge-approved` label
  via the web UI before `--auto` is allowed). Rejected: reintroduces the human-in-the-loop wait
  ADR-031's escape hatch exists to avoid in the first place; nothing in the ADR-031/565 history
  suggests a human checkpoint is wanted here. Same shape as ADR-039's rejected "block on missing
  review entirely" — scope creep past what this ADR is trying to solve.
- **A CI job that independently re-verifies the three checkpoints** as a required status check.
  Rejected: ADR-warrant and doc-reconciliation are judgment calls made by reading a diff for
  architectural significance. Encoding that in a CI-runnable script duplicates `/review`'s
  Opus-level reasoning in a shell script, worse and costlier to maintain than reusing the
  reasoning `/review` already does. And a CI job can only check for marker text too (it has no
  more insight into "did the session actually think about this" than the hook does) — it
  collapses back to option (b) with an extra moving part (a new CI dependency this repo's hook
  ecosystem doesn't otherwise need) for no benefit.
- **Extend `pre-merge-findings-gate.py` in place** (one script, branch on `--auto` presence for
  stricter behavior) rather than a new sibling script. Rejected in favor of a new script: this
  codebase's own convention is one hook, one responsibility (`pre-merge-message-check.py`,
  `pre-merge-findings-gate.py`, and `pre-merge-numbering-check.py` are three separate scripts for
  three separate merge-time checks, all chained in `settings.json`). A new sibling script keeps the
  new, *stricter, fail-closed* logic isolated from the existing, working, fail-open gate — a bug in
  the new code can't regress the proven one, and the change is independently revertable.

---

## Decision (recommended design)

### 1. Extend `/review` to produce the missing artifact for the other two checkpoints

Add a step to `claude/skills/review/SKILL.md`, structurally identical to the existing Step
2b (Documentation Reconciliation Check) — call it **Step 2f, ADR-Warrant Check** — applying
ADR-011's four criteria (touches a rule/hook/skill/settings under `claude/`; introduces or
restructures a `claude/` directory; establishes/changes a workflow rule other CLAUDE.md files
reference; rationale would be hard to recover from `git log` alone) to the diff already fetched in
Step 2. Reuse Step 2b's existing doc-reconciliation judgment for the second checkpoint — no new
step needed there, just a new place to record its outcome.

Extend the Step 8 output template with a second trailing marker line, always emitted (not
conditional on the author intending to use `--auto` — `/review` can't know that in advance, and
emitting it unconditionally is what makes retroactive `--auto` use possible on any previously
reviewed PR):

```
<!-- premerge-checkpoints: adr_warrant=<written|not-warranted> doc_reconciliation=<updated|not-applicable> -->
```

- `adr_warrant=written` — a new or amended ADR is included in this PR (or a prior PR already
  covers it, cited by number). (Named `written`, not `filed`, to avoid colliding with this
  repo's near-universal use of "file" for GitHub issues — a review finding on this ADR itself.)
- `adr_warrant=not-warranted` — Step 2f evaluated the four criteria and none applied.
- `doc_reconciliation=updated` — README.md/REFERENCE.md changes are present per Step 2b/2c.
- `doc_reconciliation=not-applicable` — the diff doesn't touch `claude/skills/**`,
  `claude/hooks/**`, `claude/scripts/**`, or `claude/routines/**` (or the project has no
  Documentation Maintenance table at all).

Both fields must be present with one of their two valid values for the marker to count as
"evaluated." A `/review` comment predating this change has no `premerge-checkpoints` line at all —
indistinguishable, by design, from "never evaluated" (see point 3).

### 2. New hook: `claude/scripts/pre-auto-merge-checkpoint-gate.py`

A `PreToolUse` hook on `Bash`, wired in `settings.json` immediately after
`pre-merge-findings-gate.py`. Detection, in order:

1. Reuse `_hookio.scan_top_level` / the existing `is_pr_merge_command` predicate to find a
   top-level `gh pr merge` statement (not one merely mentioned inside a quoted string, `$()`
   subshell, or heredoc — same false-positive class `pre-merge-findings-gate.py` already guards).
2. If no `--auto` token is present on that statement (bare `--auto`, or `--auto=<value>`),
   **exit 0 immediately.** Plain merges are unaffected; `pre-merge-findings-gate.py` remains their
   only gate, unchanged. Mirror `is_mutating_gh_segment`'s existing `--delete-branch=false`
   handling: an explicit `--auto=false`/`=0`/`=no` is a genuine no-op (no auto-merge requested at
   all) and must not trigger the stricter path. `--disable-auto` (a distinct, real `gh pr merge`
   flag that *turns off* a pending auto-merge) is never in scope — undoing a pending auto-merge is
   a safe operation in every case, so it must stay zero-friction.

   `--auto` is confirmed live (via `gh pr merge --help` during this design) as a plain boolean
   flag with no short form; the `=value` form's support is *not* independently verified against
   `--auto` specifically — it's assumed by general Cobra/pflag convention (booleans accept
   `--flag=value` even when `--help` doesn't show it) and by this repo's own precedent of relying
   on that same assumption for `--delete-branch=false` in `is_mutating_gh_segment`. Follow-up
   item 3 below calls out closing this assumption for good by exercising `--auto=false` against a
   real `gh` invocation once the hook exists.
3. Reuse `is_merge_help_only` to exit 0 on `gh pr merge --help`, same as the sibling hook.
4. Resolve the target PR with the same `_parse_merge_target` logic `pre-merge-findings-gate.py`
   already has — import it rather than re-deriving, so the two hooks can never drift on how they
   identify "which PR."
5. `gh pr view --json comments,body,number,commits` (one field more than the sibling hook's
   `comments,body,number` — `commits` is needed for the freshness check in point 5 below; both
   fields verified live against a real merged PR during this design — see the field-name callout
   at the end of this section).

**Single-comment requirement (mechanical, not optional).** Unlike `pre-merge-findings-gate.py`,
which independently finds "the last comment carrying the `review-findings` marker," this hook
must find **the single most recent comment that carries both markers together**. Do not implement
this as two independent last-comment searches (one for `review-findings`, one for
`premerge-checkpoints`) — in the normal flow both are always emitted together by the same Step 8
template invocation, so the two searches never diverge in practice, but an implementation that
searches independently would accept a *fresh* clean review paired with a *stale*
`premerge-checkpoints` marker from an earlier, now-outdated review (e.g. one whose second marker
line was dropped by a `/review` bug, or a hand-edited comment) — precisely the freshness bypass
point 5 exists to close, reached from a different angle. A comment carrying only one of the two
markers does not satisfy the gate at all.

Pass condition (**all** of the following, evaluated against that one comment — mirroring
ADR-039's existing shape but requiring more):

- It carries `<!-- review-findings: blocking=N non_blocking=M -->` showing a clean review
  (`N+M == 0`), or a recorded disposition exists in the PR body (`_DISPOSED_RE`) when `N+M > 0` —
  **identical condition** to `pre-merge-findings-gate.py`'s own pass case; import and reuse its
  marker regex and disposition regex rather than re-implementing them.
- It also carries `<!-- premerge-checkpoints: adr_warrant=... doc_reconciliation=... -->`
  with both fields present.
- It is **fresh**: its `createdAt` is not older than the PR's current head commit's
  `committedDate` (see point 5) — freshness is checked against this one comment's timestamp,
  never a different comment's.

Any other outcome — no comment carries both markers together, a stale qualifying comment, or a
`gh`/network error — **exit 2**, naming exactly which condition failed and pointing at the two
remedies: (re-)run `/review <PR-URL> --post-comment` (now emitting both markers together), or
fall back to the always-available primary path — plain `gh pr merge` (no `--auto`) after CI is
green, which this gate does not touch at all.

**Field names used above, verified live during this design** (not assumed): `gh pr view --json
comments` → each comment has `createdAt` (ISO-8601, e.g. `"2026-07-04T06:52:08Z"`, confirmed
against dev-env PR #572). `gh pr view --json commits` → an array; the last element's
`committedDate` (also ISO-8601, same PR: `"2026-07-04T06:54:43Z"`) is the PR's current head-commit
timestamp.

### 3. Fail closed, not fail open — a deliberate departure from ADR-039

ADR-039's gate fails **open** on any `gh`/network error, and that's the right call *for the gate
it is*: it guards *every* merge, the routine high-frequency path where a live session is still
right there as a backup, so briefly losing enforcement to a transient outage is better than
blocking all legitimate merges repo-wide. This new gate inverts that calculus on both axes that
justified it:

- `--auto` is the rare, opt-in path — failing closed here costs the common (plain-merge) path
  nothing at all.
- The entire reason `--auto` is being gated is that invoking it **removes every other in-session
  backstop the moment it succeeds** (per ADR-031's addendum). A fail-open gate here would silently
  reopen exactly the hole the 2026-07-04 addendum refuses to reopen "for convenience" — a `gh`
  blip would become a free pass to skip all three checkpoints *and* get the unattended async
  merge. That is a strictly worse failure mode than the plain-merge gate's fail-open case, where
  the worst outcome is "this one merge didn't get the disposition check, but a human/session is
  still watching it happen."
- The always-available fallback (plain `gh pr merge`, wait for CI) means fail-closed can never
  trap anyone — it costs, at most, the 2–10 minutes ADR-031's original Rationale already accepted
  as an insignificant price for keeping all six rules intact.

### 4. No override token

Unlike `pre-tool-use-canonical-mutate-guard.py`'s `ALLOW_CANONICAL_MUTATE=1`, this gate should ship
with **no** bypass. That override exists for a fact only a human can plausibly verify and the hook
structurally cannot (whether another session is concurrently active in the same checkout). There
is no analogous fact here — the three checkpoints are either satisfiably evaluated (run `/review`)
or they are not, and the non-`--auto` path is a full substitute with no functional loss beyond
convenience. A bypass token would just become a standing, memorizable "always prefix with X" habit
— reintroducing the discipline-only failure mode the 2026-07-04 addendum explicitly declined to
accept for the sake of convenience.

This deliberately does not claim the gate is tamper-proof — see the "PR comments are a procedural
convention, not an authenticated channel" limitation in Consequences below, inherited unchanged
from ADR-039. "No analogous fact only a human can verify" is about not needing a *legitimate*
bypass, not a claim that nothing could type the marker text by hand.

### 5. Freshness check, and why it's needed even though ADR-039 doesn't have one

ADR-039's gate has a known, accepted staleness gap: "last comment wins" means a clean review
followed by new, buggy commits still passes. That's tolerable there because a live session is
present at the actual merge moment to notice something's off. It is not tolerable here, because
`--auto` removes that safety net by definition — a marker written against an earlier version of
the diff would otherwise validate a merge of code nobody has looked at. The fix is cheap: compare
the checkpoint marker comment's `createdAt` against the PR's current head commit's `committedDate`
(both already fetched in one `gh pr view` call — see point 2's field-name callout). If the head
commit postdates the marker, treat the PR exactly as if no marker existed at all — block, with a
message naming which is stale.

### 6. This hook is necessary, not sufficient — the toggle is a separate decision

Shipping this hook does **not**, by itself, make `--auto` work anywhere. `allow_auto_merge` is
still `false` on every `brownm09/*` repo (confirmed by the 2026-07-04 survey), and that GitHub
repo setting is checked *before* any Bash command ever reaches this hook — a `gh pr merge --auto`
call fails at the GitHub API (`GraphQL: Auto merge is not allowed for this repository`) exactly as
it did on lifting-logbook PR #664, regardless of whether this hook would have allowed it. This
hook is the *prerequisite* the 2026-07-04 addendum named — clearing it does not, by itself, decide
that any specific repo should flip the toggle. That's a separate, smaller decision (likely a short
ADR-031 addendum, or its own short ADR) to make once this hook exists, has shipped, and has a track
record — not something to bundle into the hook's own PR. One practical upside of the split: because
`claude/settings.json` hooks apply globally across every `brownm09/*` repo (the `~/.claude/`
symlink/junction map), shipping this hook once unlocks a repo-by-repo opt-in later with zero
additional hook work per repo — flip the toggle on a single pilot repo, watch it, and expand from
there.

---

## Consequences

**Positive.**

- Restores a path to `--auto`'s convenience without trading away any of the three checkpoints a
  pre-check can verify — the explicit bar the 2026-07-04 addendum set.
- Reuses proven, tested machinery end to end: `_hookio.scan_top_level`/`is_merge_help_only`,
  `pre-merge-findings-gate.py`'s marker/disposition regexes and PR-target resolution, and
  `/review`'s existing Step 2b doc-reconciliation judgment. No new parsing engine, no new marker
  syntax beyond one additional HTML-comment line.
- The `/review`-centric design means the two new checkpoints get evaluated by the same Opus
  reasoning pass that already reads the full diff for correctness/security/reliability — not a
  separate, cheaper, more error-prone mechanical approximation.
- Global-hook, per-repo-toggle rollout shape de-risks adoption: one hook ships once; trust is
  built incrementally, repo by repo, on the `allow_auto_merge` axis alone.

**Negative / accepted limitations.**

- **Same accepted limitation as ADR-039, extended to two more checkpoints:** this verifies that a
  conscious disposition was *recorded*, not that its content is *true*. A session could write
  `adr_warrant=not-warranted` when an ADR genuinely was warranted, and no mechanical check catches
  that — a content-judgment problem, not a mechanical one. ADR-039 accepted the identical trade for
  findings dispositions; this design makes the same trade for the same reason (per-finding /
  per-judgment verification would require parsing free-text intent and is disproportionate to the
  value here).
- **PR comments are a procedural convention, not an authenticated channel.** Neither this hook nor
  `pre-merge-findings-gate.py` checks comment authorship — both regex over comment *bodies* only.
  Anyone or anything with comment access to the PR could hand-write a comment containing the exact
  marker text and satisfy the gate without `/review` ever running. This is not a new hole ADR-083
  introduces (ADR-039 has had it, unremarked, since it shipped) — but this gate leans on it more
  heavily than ADR-039 does: it is the *sole* thing standing between `--auto` and an unattended
  merge, with no override and no fail-open safety valve, so the same gap matters more here. The
  design accepts this because it assumes the repo's operator (not an adversarial third party)
  controls what gets posted to their own PRs — real comment-authenticity verification would be
  disproportionate scope creep for a single-maintainer repo, but that assumption should be stated
  rather than left implicit.
- **Governs only the Claude Code-mediated path.** A human merging via the GitHub web UI's own
  auto-merge toggle bypasses this hook entirely (no Bash tool call to intercept) — but that path is
  moot everywhere the repo-level toggle is off, and is a pre-existing, accepted gap for every
  `PreToolUse` hook in this repo, not new to this one.
- **Freshness only catches new commits.** A marker can go stale for reasons other than new commits
  (e.g., a newly disclosed CVE in an unrelated dependency) that this design does not and cannot
  detect — the same point-in-time limitation any review process has.
- **Adds one more thing `/review` has to get right.** Step 2f is one more mechanical-trigger +
  Opus-judgment step layered onto an already-large `SKILL.md`. Justified by reuse (no new
  infrastructure) but worth naming as ongoing maintenance surface.

---

## Follow-up work (tracked by [dev-env#574](https://github.com/brownm09/dev-env/issues/574))

This ADR is design-only. Landing the design requires, in (likely) more than one PR:

1. `claude/skills/review/SKILL.md` — add Step 2f (ADR-Warrant Check), extend Step 8's template
   with the `premerge-checkpoints` marker line, update Step 6's finding categories if ADR-warrant
   gaps should also surface as blocking findings (open question — ADR-019's doc-reconciliation
   gap does; whether ADR-warrant gaps should follow the same pattern is a judgment call for the
   implementation session, not pre-decided here).
2. `claude/scripts/pre-auto-merge-checkpoint-gate.py` — new hook per the Decision above, importing
   shared logic from `pre-merge-findings-gate.py` rather than duplicating it.
3. Tests: a pure-function `test_pre_auto_merge_checkpoint_gate.py` (marker/flag parsing, freshness
   comparison, `--auto`/`--auto=true`/`--auto=false` detection) plus a behavioral self-test in the
   `MERGE_GATE_TEST_JSON`-seam style, covering at minimum: no `--auto` → allow; `--auto` + all
   checks fresh → allow; missing `/review` marker → block; missing `premerge-checkpoints` marker →
   block; stale marker (commit postdates it) → block; `gh` error → **block** (the flipped default
   vs. the sibling gate); explicit `--auto=false` → allow without any checks. Also confirm, once
   against a real `gh pr merge --auto=false --help`-style dry check, that `gh` itself actually
   accepts the `=value` form on `--auto` — closing the assumption noted in point 2 above, which
   this design deliberately did not treat as independently verified.
4. `settings.json` — wire the new hook after `pre-merge-findings-gate.py`.
5. `claude/CLAUDE.md` — replace the current "do not attempt `gh pr merge --auto`" blanket guidance
   (added by the 2026-07-04 addendum) with the new conditional path once the hook exists.
6. `docs/REFERENCE.md` — add the new hook to the Hooks section (per this repo's own
   doc-reconciliation rule — Step 2f/2b would catch a miss here on the implementation PR itself).
7. A **separate**, later ADR-031 addendum (or small standalone ADR) making the actual per-repo
   `allow_auto_merge: true` decision, once the hook above has shipped and had a chance to prove
   itself. Not bundled into the hook's own PR — see Decision point 6.

This issue should stay open after this ADR merges; it closes only once the implementation (items
1–4 above, at minimum) lands.

---

## Implementation

Items 1–6 above shipped in a single PR closing [dev-env#574](https://github.com/brownm09/dev-env/issues/574)
(item 7 — the per-repo `allow_auto_merge` toggle decision — remains separate and untouched, per
Decision point 6). Three resolutions this design left open, made during implementation:

1. **Single PR, not the "likely more than one PR" this design predicted.** The hook and `/review`'s
   SKILL.md changes define one shared contract (the `premerge-checkpoints` marker's exact
   fields) — splitting risked a half-shipped, inconsistent contract on `main` between merges.
2. **Step 6 (`/review`'s finding classification) now lists ADR-warrant gaps from Step 2f as
   Blocking**, category `[documentation]` (not a new category — an ADR is itself a documentation
   artifact, and adding a fifth/sixth tag would have rippled through Step 4's category enum for no
   compensating benefit). Resolves the open question this design left in Follow-up item 1, on the
   same reasoning ADR-011 itself gives for being "enforcement-style... rather than advisory."
3. **A third literal value, `missing`, exists for both `adr_warrant` and `doc_reconciliation`**,
   beyond the two "valid" values this design specified. Needed because the marker line is always
   emitted but a field can be in a real third state (a gap found and still unresolved when Step 8
   runs) — and because the hook's marker regex requires at least one non-whitespace character per
   field, a blank value would be indistinguishable from the whole marker line being absent (i.e.
   from a pre-ADR-083 review). `missing` preserves that distinction for a human reading the
   comment while still correctly failing the hook's validity check.

**The `--auto=value` assumption (Follow-up item 3) is now confirmed, not assumed.**
`gh pr merge 999999 --auto=false --repo brownm09/dev-env` returned `GraphQL: Could not resolve to
a PullRequest with the number of 999999.` — a PR-resolution error, not a flag-parsing error,
confirming `gh` accepts the `--flag=value` form on `--auto` exactly as it does for
`--delete-branch`.

---

## References

- [ADR-031 — Auto-Merge Disabled Across All Repos](031-auto-merge-disabled.md) — the policy this
  design is a prerequisite for partially reopening.
- [ADR-039 — Mechanical Enforcement of the All-Findings Merge Gate](039-merge-gate-findings-enforcement.md) —
  the direct prior art this design extends.
- [ADR-011 — ADR-Warrant Check at Plan, PR-Open, and PR-Merge Checkpoints](011-adr-warrant-check.md) —
  defines checkpoint 3 for ADR-warrant.
- [ADR-019 — Documentation Reconciliation Enforcement](019-doc-reconciliation-enforcement.md) —
  defines checkpoint 3 for doc-reconciliation, and the existing Step 2b/2c mechanism this design
  reuses.
- [ADR-028 — All-Findings Merge Gate](028-all-findings-merge-gate.md) — the policy ADR-039
  mechanically enforces and this design extends the same way.
- [ADR-071 — Canonical-Checkout Mutate-Guard Hook](071-canonical-checkout-mutate-guard-hook.md) —
  precedent for both the override-token pattern (adopted) and its absence here (declined, see
  Decision point 4).
- [Claude Code hooks — PreToolUse and exit codes](https://docs.anthropic.com/en/docs/claude-code/hooks) —
  exit code 2 from a `PreToolUse` hook blocks the tool call; stdout JSON `systemMessage` surfaces
  advisories.
- [GitHub CLI — `gh pr view`](https://cli.github.com/manual/gh_pr_view) — `--json
  comments,body,number,commits` supplies every field this design reads; comment `createdAt` and
  commit `committedDate` shapes verified live against dev-env PR #572 during this design (2026-07-05).
- [GitHub CLI — `gh pr merge`](https://cli.github.com/manual/gh_pr_merge) — `--auto` and
  `--disable-auto` flag semantics verified live via `gh pr merge --help` during this design
  (2026-07-05).
- [GitHub Docs — Automatically merging a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request) —
  the `allow_auto_merge` repo setting Decision point 6 depends on.

---

## Addendum (2026-07-06) — A GitHub-side required check as a complementary gate for the review checkpoint

Now that Items 1–6 have shipped (see Implementation above), the Consequences section's accepted
limitation — *"Governs only the Claude Code-mediated path. A human merging via the GitHub web
UI's own auto-merge toggle bypasses this hook entirely"* — is worth revisiting with a concrete
answer, rather than left as a named-but-unaddressed gap.

**Why this still matters even though item 7 hasn't happened yet.** The gap is harmless today only
because `allow_auto_merge` stays `false` everywhere (per the Implementation note above, item 7
"remains separate and untouched"). The instant any repo's later, separate item-7 decision flips
that toggle, a human — or GitHub's own web UI — can click "Enable auto-merge" on any PR and it
will merge once that repo's existing CI-based required checks go green, with **zero** enforcement
of the review-findings-disposition checkpoint: the shipped hook only ever intercepts a Bash tool
call inside a Claude Code session, and a GitHub-triggered merge never makes one.

**Why this doesn't reopen the rejected "CI job as required status check" alternative.** This
design's own "Candidates evaluated" section rejected a CI job re-verifying *all three*
checkpoints, primarily because two of them (ADR-warrant, doc-reconciliation) are judgment calls
that would require duplicating Opus-level reasoning in a script. That reasoning does not extend
to the review-findings checkpoint on its own: it reduces to pure marker-text detection (the same
`<!-- review-findings: ... -->` regex and PR-body disposition check the hook already runs) —
something a CI job can evaluate with **no fidelity loss** relative to the hook, because both read
the identical PR data through the identical regex. The "collapses back to option (b) for no
benefit" conclusion was correct for the Claude-Code-mediated path (where the hook already gates
it before any Bash call executes) — it does not hold for the web-UI/human-triggered path, where
the hook provides no protection at all and a required check is the only mechanism capable of
providing any.

**Recommendation.** When a specific repo's item-7 decision (flipping `allow_auto_merge`) is made,
pair it with a GitHub Actions required status check — e.g. "Review Gate" — that mirrors the
hook's exact marker/disposition/freshness semantics for the review-findings checkpoint only, added
to that repo's branch protection. This complements the hook rather than replacing it: the hook
remains the only mechanism covering the ADR-warrant and doc-reconciliation checkpoints for the
Claude-Code-mediated path, which a required check still cannot verify, for the reasons the
Candidates-evaluated section already gives. This is being prototyped now, independently of any
item-7 decision, in [lifting-logbook#718](https://github.com/brownm09/lifting-logbook/issues/718)
— note that repo's implementation goes further than recommended here by also making review
mandatory (no marker = fail), which is a separate policy choice specific to that repo, not a claim
this addendum makes generally.

**Not a decision.** This addendum does not change the Decision or Consequences above, and does not
itself flip any toggle. It records a design refinement for whoever makes a specific repo's item-7
call next, so the web-UI gap is weighed explicitly rather than rediscovered.

---

## Addendum (2026-07-07) — Follow-up item 7 has been exercised for lifting-logbook, ahead of the recommended pairing

A live per-repo check (`gh api repos/brownm09/<repo> --jq .allow_auto_merge`), run 2026-07-06/07
against every `brownm09/*` repo referenced in dev-env or in `claude/CLAUDE.md`, found:

| Repo | `allow_auto_merge` |
|---|---|
| lifting-logbook | **`true`** |
| dev-env | `false` |
| career-playbook | `false` |
| engineering-journal | `false` |
| win11-init-tools | `false` |

So the blanket claim elsewhere in this ADR ("`allow_auto_merge` stays `false` across every
`brownm09/*` repo") and in the global CLAUDE.md ("not yet made for any repo") is now **stale for
lifting-logbook specifically**. This addendum replaces that blanket framing with the corrected one:
**check live state per repo** — a repo-settings toggle leaves no git history, so a snapshot claim
in a doc will drift out from under it exactly as this one did. The global CLAUDE.md's Auto-merge
bullet has been updated accordingly in the same change that added this addendum.

**What's known about how this happened.** GitHub repo-setting toggles (web UI or `gh repo
edit`/`gh api PATCH`) leave no git history trail, and personal (non-org) accounts have no
settings-change audit-log API, so the exact moment and actor can't be reconstructed after the fact.
lifting-logbook has no `.github/settings.yml` (repo settings aren't declarative there), ruling out
a config-as-code explanation. Circumstantial evidence ties the flip to the same work stream as
[lifting-logbook#718](https://github.com/brownm09/lifting-logbook/issues/718) ("Add mandatory
Review Gate required status check," opened 2026-07-06T03:59 — exactly the item-7 decision this
ADR's 2026-07-06 addendum above was written to anticipate): sub-issue
[#720](https://github.com/brownm09/lifting-logbook/issues/720) and the workflow it ships
([lifting-logbook PR #722](https://github.com/brownm09/lifting-logbook/pull/722)) were opened the
same day, and a `gh pr merge --squash --auto` was run successfully in a lifting-logbook session
that same day too — which requires `allow_auto_merge: true` at the GitHub API level regardless of
the local hook's own checkpoint gate. This reads as **intentional-in-direction, not unnoticed
drift**.

**But it's premature relative to this ADR's own recommended sequencing.** The 2026-07-06 addendum's
Recommendation above was explicit: pair the toggle with a required "Review Gate" GitHub Actions
check, specifically because the toggle alone reopens the web-UI/GitHub-triggered-merge gap this
whole ADR exists to close only for the Claude-Code-mediated path. As of this writing, lifting-logbook
has the toggle **on** but the paired check is **not yet in place**: `review-gate.yml` exists only
on an open, unmerged PR ([lifting-logbook#722](https://github.com/brownm09/lifting-logbook/pull/722)) —
not on `main` — and even once merged it still needs sub-issue 2 of #718 (the branch-protection
mutation) before it's *required*. Until both land, a human merging via lifting-logbook's web UI, or
GitHub's own auto-merge firing once CI goes green, bypasses all review-findings enforcement on that
repo. This is not hypothetical — it is lifting-logbook's live state at the time of this addendum.

**Disposition.** No toggle was changed by this addendum or its companion CLAUDE.md edit — this is
a documentation correction only, consistent with the "Not a decision" framing of the 2026-07-06
addendum above. Whether to leave lifting-logbook's toggle on while #722 is in flight, or revert it
to `false` until the required-check pairing completes, is a judgment call for whoever owns that
repo's rollout timeline — flagged here, not resolved here. Tracked via
[dev-env#607](https://github.com/brownm09/dev-env/issues/607).

**Follow-up item 7 status, updated:** no longer "not yet made for any repo." Made, in effect, for
lifting-logbook — ahead of its own recommended safety pairing; still `false`/undecided for every
other repo checked. Track lifting-logbook's remaining piece (making Review Gate required) via
[lifting-logbook#718](https://github.com/brownm09/lifting-logbook/issues/718)/[#720](https://github.com/brownm09/lifting-logbook/issues/720)/[#722](https://github.com/brownm09/lifting-logbook/pull/722),
not a new dev-env-side issue.

---

## Addendum (2026-07-08) — lifting-logbook#722 merged; the paired check ships and reports, but is not yet required

The 2026-07-07 addendum above described lifting-logbook#722 as "an open, unmerged PR... not on
`main`." That is no longer accurate as of this addendum: PR
[lifting-logbook#722](https://github.com/brownm09/lifting-logbook/pull/722) merged 2026-07-08
(`0d9dce4cbfbc2147276b68f58c4d8ae866632f10`). `.github/workflows/review-gate.yml` is now live on
lifting-logbook's `main`, reporting a real `Review Gate` pass/fail commit status on every PR —
confirmed live during the merge itself. Sub-issue
[lifting-logbook#720](https://github.com/brownm09/lifting-logbook/issues/720) is closed.

**This closes the *informational* half of the gap, not the *enforcement* half.** The check is not
yet wired into branch protection as a required status check — nothing blocks a merge today when it
fails; it is reporting-only. `allow_auto_merge` also remains `true`. Top-level
[lifting-logbook#718](https://github.com/brownm09/lifting-logbook/issues/718) stays **open** for
exactly this reason: its second sub-issue (the branch-protection mutation that makes Review Gate
required) has not yet been filed. Until that lands, a human merging via lifting-logbook's web UI, or
GitHub's own auto-merge firing on green CI, still bypasses all review-findings enforcement on that
repo — same live gap the 2026-07-07 addendum flagged, now partially, not fully, closed.

No toggle was changed by this addendum. Disposition of the remaining piece is unchanged from above:
a judgment call for whoever owns lifting-logbook's rollout timeline, tracked via
[lifting-logbook#718](https://github.com/brownm09/lifting-logbook/issues/718).
