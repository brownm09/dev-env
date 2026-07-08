# ADR-092: Dangling-Created-Issue Trigger for the Tile-Enumeration Gate

**Date:** 2026-07-08
**Status:** Accepted
**Tags:** hooks, stop, tiles, spawn-task, issue-create, enforcement, investigation-sessions, transcript-scan, adr-046, adr-088

---

## Context

[ADR-088](088-state-keyed-tile-enumeration-gate.md) added `stop-tile-enumeration-gate.py`, a Stop
hook that scans the just-ended session transcript and blocks the stop (exit 2) when a PR reached
MERGED state this session by any path but no tile-enumeration artifact was recorded. It is the
state-keyed enforcement of [ADR-046](046-post-merge-followup-tiles.md)'s "capture post-merge
follow-ups as tiles" rule, and it closed a real gap: a merge with no forcing function to enumerate
follow-ups reliably produced a bare, unexamined "no follow-ups" assertion (the lifting-logbook#700
skip).

That gate only fires on one trigger: a PR reaching merged state. A session that does pure
investigation — files one or more well-scoped GitHub issues with confirmed problems and concrete
proposed fixes, but implements and merges nothing — has no mechanical nudge to spin up follow-up
work for those issues at all. They just sit on the tracker with no forcing function analogous to
what a merged PR already gets.

**Motivating incident.** A dev-env session on 2026-07-08 did a deep investigation, filed
[dev-env#630](https://github.com/brownm09/dev-env/issues/630) and
[dev-env#631](https://github.com/brownm09/dev-env/issues/631) (both substantial, both with concrete
proposed fixes, both rated Impact=High on the project board), and ended without spawning a tile for
either — the user had to notice the gap and ask for it explicitly. Closed by
[dev-env#638](https://github.com/brownm09/dev-env/issues/638), this ADR.

## Decision

Extend `claude/scripts/stop-tile-enumeration-gate.py` **in place** with a second, independent
trigger: a `gh issue create` ran this session and the created issue was **not resolved** by session
end. "Resolved" means either:

- A same-session merged PR (per the existing `session_merged_prs()`) whose `gh pr create` command
  contains a GitHub auto-close keyword — `Close(s/d)` / `Fix(es/ed)` / `Resolve(s/d)` `#N` (GitHub's
  documented keyword set — see
  [Linking a pull request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/linking-a-pull-request-to-an-issue))
  — for that issue, **or**
- An explicit `gh issue close N` ran this session.

Both triggers share the exact same remediation: a recorded tile-enumeration artifact (a `spawn_task`
tool call, or the prescribed "Follow-ups considered … → tiled (task_id / #N) / → not tiled, because
<reason>" text). `enumeration_recorded()` and `skip_override()` are reused **completely unchanged** —
one enumeration satisfies either trigger, or both, for the whole session (the same session-global
model ADR-088 already documents as an accepted limitation, extended one step further — see
Limitations below).

### Why extend this file rather than write a new one

ADR-088 itself declined to fold the state-keyed gate into the command-keyed
`post-merge-tile-checkpoint.py` (ADR-060), reasoning that the two differ in **event** (Stop vs.
PostToolUse), **input shape** (whole transcript vs. a single command payload), and **logic**
(verify-an-artifact-at-turn-end vs. nudge-on-a-command) — different enough that merging them would
violate single-responsibility.

None of those three differences apply here. The dangling-issue trigger is:

- The same **event** (Stop).
- The same **input** (the whole transcript, already parsed once per Stop).
- The same **logic shape** — "did something reach an end state without a recorded disposition?" —
  just a different precondition (`gh issue create` + no resolution, instead of a PR reaching MERGED).

Concretely, the two triggers already share every piece of mechanism that matters: the sentinel
(`SENTINEL_PREFIX`, `_mark_fired`), the `stop_hook_active` loop guard, the fail-open `try`/`except`
wrapper, `enumeration_recorded()`, and `skip_override()`. Splitting into a second file would either
duplicate all of that (the exact class of drift ADR-090 undid for the transcript readers a few hours
earlier) or require extracting a shared base the two files would then both depend on — more
indirection for zero behavioral gain, since both hooks fire from the same `main()` at the same Stop
event anyway. `session_merged_prs()` itself already ORs three internally-distinct detection
mechanisms (direct marker, `gh api` merge, observed auto-merge) into one function; adding a fourth
detection mechanism for a *different* end-state (issue resolution) inside the same module is
consistent with that existing internal structure, not a new precedent.

The filename (`stop-tile-enumeration-gate.py`) does not need to change: it already describes the
*mechanism* being enforced (tile enumeration), not the specific trigger that requires it — accurate
before and after this change.

### Why a fully independent `evaluate_issues()` rather than folding into `evaluate()`

`evaluate()`'s existing `(fire_pr, resolved)` two-tuple contract is depended on by all 38
pre-existing tests and by `main()`. Changing its signature (e.g., to return a set of fired items, or
a "kind" tag) would touch every one of those call sites for a feature that is additive, not a
replacement. `evaluate_issues(records) -> (fire_issue, resolved)` is a byte-for-byte structural
mirror of `evaluate()`, added as a sibling; `main()` calls both and combines the results. This
keeps `evaluate()` **provably unchanged** (verified: the entire 38-test pre-existing suite required
zero modification and passes unmodified against the extended file) while giving the issue trigger
the identical evaluation shape, tested the identical way.

The cost is that `main()` recomputes `iter_bash_calls(records)` and `session_merged_prs(calls)` twice
(once inside each evaluator) rather than sharing one computation. This is a deliberate
simplicity-over-micro-optimization choice — see Performance under Consequences.

### Detection design

- **`session_created_issues(calls) -> {issue_number: repo_or_None}`** — mirrors
  `session_merged_prs`'s `acted` dict: for every top-level `gh issue create` segment, extracts the
  created issue's number (and repo) from the issue URL in the command's output. A `gh issue create
  --help` invocation (the exact false-positive class ADR-050 Amendment 16, this session's other PR,
  closes for `post-tool-use.py`) naturally yields nothing here too — `--help` output contains no
  issue URL, so `created` stays empty for that call without needing an explicit `--help` guard.
- **`session_resolved_issue_numbers(calls, merged_prs) -> set`** — the Closes-keyword search is
  scoped to each individual `gh pr create` segment's own text (including its own heredoc body, where
  this repo's own `--body "$(cat <<'EOF' ... EOF)"` idiom typically places the keyword), never the
  whole raw command string. This mirrors `session_merged_prs`'s own per-segment scoping discipline
  (the PR #604 review's A4 hardening) and is necessary, not cosmetic: a naive whole-command search
  would let an unrelated `Closes #N` mention on a different chained segment leak into the wrong PR's
  resolution (tested explicitly — see Testing below).
- **`session_unresolved_created_issues(calls, merged_prs) -> set`** — `set(created) - resolved`.
- **Multiple dangling issues**: `evaluate_issues()` fires on `min(unresolved)`, mirroring
  `evaluate()`'s existing lowest-PR-number determinism for multiple merged PRs.
- **Combined reminder**: `main()` calls both `evaluate()` and `evaluate_issues()`; if either (or
  both) fire, both reminders are emitted together in one exit-2 stderr write, separated by a blank
  line. A session that both merges a PR (unenumerated) and leaves an issue dangling gets one combined
  message naming both, not two separate blocking turns.

### Pre-filter correctness fix

The existing cheap pre-filter (`if "merged" not in text.lower(): sys.exit(0)`) assumed every
detectable signal contains the substring "merged" — true for the PR trigger alone, but **not** for a
session whose only relevant activity is `gh issue create` with no merge anywhere. Left unfixed, the
broadened gate would silently never scan for the dangling-issue trigger in exactly the sessions that
motivate it (pure-investigation sessions with no PR at all). Fixed by widening the guard to
`if "merged" not in lower and "issue create" not in lower: sys.exit(0)` — every dangling-issue signal
requires a `gh issue create` invocation to have run this session, and that command's own text always
contains "issue create" case-insensitively, so the widened substring check is a sound (if
correspondingly looser) guard for both triggers together.

## Consequences

- Investigation sessions that file issues but implement nothing now get the same mechanical
  follow-up nudge merge sessions already get — the dev-env#630/#631 gap is closed.
- The "routine" flow (file issue → implement in the same session → PR → merge, with the PR's own
  `Closes #N`) is unaffected: the issue resolves via the merged-PR path before Stop, so
  `evaluate_issues()` never fires for it. Verified directly (`test_e2e_issue_resolved_via_merge_allows`).
- One enumeration continues to satisfy the whole session, now across *both* trigger kinds — a
  session that merges a PR and also files a dangling issue, and enumerates once covering both, is not
  asked to enumerate twice (`test_combined_one_enumeration_satisfies_both`).

### Testing

`test_stop_tile_enumeration_gate.py` grows from 39 to 71 tests, 0 failures. All 39 pre-existing tests
pass **unmodified** (proving `evaluate()`/`format_reminder()`/`main()`'s merged-PR path is
byte-for-byte unaffected). New coverage: issue-creation detection (URL extraction, the `--help`
non-interaction with dev-env#636's fix, heredoc anchoring); resolution via each of GitHub's three
documented keyword stems in both present and past tense, case-insensitively; the "PR never merged, so
the Closes mention doesn't count" negative case; the heredoc-PR-body idiom this repo's own workflow
uses; the unrelated-chained-segment non-leak case; explicit `gh issue close`; `evaluate_issues()`'s
full composition (fire / enum-resolved / skip-resolved / no-op / lowest-deterministic / shared #700
bare-assertion rejection); `format_issue_reminder`'s cp1252-encodability; the combined-trigger cases
(independent firing, one enumeration satisfying both); and seven end-to-end subprocess tests
(dangling blocks, enum/skip/explicit-close/merge-resolution all allow, the combined-message case, and
the sentinel suppressing a second fire) mirroring the existing e2e layer's HOME-isolated-sentinel
pattern exactly.

## Limitations (documented, accepted)

- **Session-global enumeration, now across two trigger kinds.** One recorded enumeration satisfies
  the gate for the whole session regardless of how many merges and/or dangling issues occurred —
  this was already ADR-088's own documented limitation for multiple merged PRs; extending it to also
  cover the issue trigger is a deliberate consistency choice, not a new gap. The gate targets the
  *total skip* (something unresolved, nothing recorded), not per-item enumeration quality.
- **Sentinel suppresses re-scanning for the rest of the session once satisfied.** Once
  `_mark_fired()` runs (either a fire-and-block or a resolved-and-suppress), every later Stop in the
  same session short-circuits before reading the transcript again — a *second*, later, unrelated
  dangling issue (or merge) would not be independently re-caught. This is the existing, pre-ADR-092
  behavior of the sentinel mechanism (not something this change makes worse); it is the same
  trust-once-demonstrated tradeoff the session-global enumeration model already accepts.
- **No cross-repo scoping on issue-resolution correlation.** Unlike `session_merged_prs`'s
  `(repo, number)`-aware correlation (the A4 hardening from the PR #604 review), the resolution check
  here matches purely on issue *number*. A session working across multiple repos that coincidentally
  creates issue #N in one repo and separately resolves an unrelated issue #N in another repo this
  same session would misread the first as resolved. Considered low-risk in practice — it requires
  working across repos *and* colliding issue numbers in the same session — and deliberately left
  unhardened for this first pass rather than gold-plating a narrow edge case; a future PR can port
  the A4-style `(repo, number)` correlation here if it proves to matter.
- **Requires an in-session `gh issue create`.** An issue created in a *previous* session and left
  dangling is invisible to this gate — by design, this hook (like its merged-PR sibling) only
  observes what happened in the just-ended session's own transcript.

## Alternatives considered

- **Detect "is this an investigation session" and scope the trigger to that.** Rejected: no
  reliable mechanical signal distinguishes "investigation" from any other session shape, and the
  existing merged-PR trigger's entire value comes from being a simple, deterministic, mechanical
  check — reusing that same discipline (fire whenever *any* issue created this session remains
  unresolved, no session-type inference) is simpler and covers the motivating case without a fuzzy
  heuristic that could both over- and under-fire.
- **A new, separate hook file.** Rejected — see "Why extend this file" above: the two triggers share
  event, input, and logic shape; a second file would either duplicate the shared mechanism or need
  its own extraction of a shared base, for no behavioral difference from adding a sibling function in
  the same module.
- **Share `evaluate()`'s and `evaluate_issues()`'s `iter_bash_calls`/`session_merged_prs` computation
  via a common helper `main()` computes once.** Considered for the performance benefit (halves the
  per-Stop parsing cost on the common path). Rejected for this PR: it would require either changing
  `evaluate()`'s signature (touching all 38 pre-existing tests and callers) or adding a new
  ~parallel entry point solely for `main()`'s use, for a linear-scan cost that is not the dominant
  cost of this hook (transcript I/O and JSON parsing already dominate). Left as a candidate follow-up
  if the per-turn scan cost (already a documented ADR-088 limitation) ever proves to matter in
  practice, rather than optimizing prematurely against an unmeasured cost.
- **Repo-scope the issue-resolution correlation from day one (the A4 treatment).** Considered and
  deferred — see Limitations above; the added complexity is not justified without evidence the
  narrow cross-repo collision case actually occurs.

## References

- [dev-env#638](https://github.com/brownm09/dev-env/issues/638) — issue this ADR closes.
- [dev-env#630](https://github.com/brownm09/dev-env/issues/630),
  [dev-env#631](https://github.com/brownm09/dev-env/issues/631) — the motivating incident: filed,
  Impact=High, and left with no follow-up tile.
- [ADR-046](046-post-merge-followup-tiles.md) — the underlying "capture follow-ups as tiles" rule
  this extends to a second checkpoint.
- [ADR-088](088-state-keyed-tile-enumeration-gate.md) — the merged-PR trigger and shared mechanism
  (sentinel, loop guard, fail-open, enumeration/skip-override detection) this ADR extends.
- [ADR-090](090-shared-transcript-readers-hookutil.md) — the transcript-reader consolidation this
  ADR's "why not a new file" reasoning directly follows.
- [GitHub: Linking a pull request to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/linking-a-pull-request-to-an-issue)
  — the documented auto-close keyword set (`close`/`closes`/`closed`, `fix`/`fixes`/`fixed`,
  `resolve`/`resolves`/`resolved`) `session_resolved_issue_numbers` matches against.
