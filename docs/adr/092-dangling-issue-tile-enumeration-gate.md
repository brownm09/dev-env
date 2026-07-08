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

`evaluate()`'s existing `(fire_pr, resolved)` two-tuple contract is depended on by all 39
pre-existing tests and by `main()`. Changing its signature (e.g., to return a set of fired items, or
a "kind" tag) would touch every one of those call sites for a feature that is additive, not a
replacement. `evaluate_issues(records) -> (fire_issue, resolved)` is a byte-for-byte structural
mirror of `evaluate()`, added as a sibling; `main()` calls both and combines the results. This
keeps `evaluate()` **provably unchanged** (verified: the entire 39-test pre-existing suite required
zero modification and passes unmodified against the extended file) while giving the issue trigger
the identical evaluation shape, tested the identical way.

`evaluate_issues()`'s `resolved` return covers one case `evaluate()` has no equivalent of: a session
that created an issue and fully resolved it this session (merge, or explicit close) with **no
enumeration needed at all**, distinct from "nothing created" (both distinguished explicitly, not
collapsed — review of PR #639; see Performance below for why the distinction matters).

The cost is that `main()` recomputes `iter_bash_calls(records)` and `session_merged_prs(calls)` twice
(once inside each evaluator) rather than sharing one computation. This is a deliberate
simplicity-over-micro-optimization choice — see "Sharing `evaluate()`'s and `evaluate_issues()`'s
computation" under Alternatives considered.

### Detection design

- **`session_created_issues(calls) -> {issue_number: repo_or_None}`** — mirrors
  `session_merged_prs`'s `acted` dict *construction*: for every top-level `gh issue create` segment,
  extracts the created issue's number (and repo) from the issue URL in the command's output. A `gh
  issue create --help` invocation (the exact false-positive class ADR-050 Amendment 16, this
  session's other PR, closes for `post-tool-use.py`) naturally yields nothing here too — `--help`
  output contains no issue URL, so `created` stays empty for that call without needing an explicit
  `--help` guard. The captured repo is **not** currently consumed by any correlation logic (see
  Limitations) — a review of PR #639 flagged that the docstring's "mirrors … `acted`" framing could
  read as implying repo-aware resolution already exists, when it does not; the docstring now says so
  explicitly.
- **`session_resolved_issue_numbers(calls, merged_prs) -> set`** — an issue resolves via a Closes
  keyword on either a `gh pr create` **or** `gh pr edit` segment (the latter covers "create the PR,
  then attach the keyword afterward" — added after a review of PR #639 flagged that the original
  implementation only scanned `gh pr create`, missing this common edit-after-create flow; `gh pr
  edit`'s target PR number is read via the existing `_target_pr` helper `session_merged_prs` already
  uses for `merge`/`view`/`checks`, since `gh pr edit <number|url>`'s syntax is identical in shape),
  **or** via an explicit `gh issue close N` — including the **URL form** (`gh issue close <url>`,
  which `gh issue close --help` documents as an accepted argument shape alongside a bare number; the
  original implementation's bare-positional-only lookup missed it, since a URL's issue number is
  preceded by `/`, never whitespace, and so never satisfied the positional regex's boundary — flagged
  independently by two reviewers of PR #639, fixed by trying the issue-URL pattern first, mirroring
  `_target_pr`'s own URL-first-then-positional precedence). The keyword search is scoped to each
  individual `gh pr create`/`gh pr edit` segment's own text (including its own heredoc body, where
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
motivate it (pure-investigation sessions with no PR at all). Fixed by widening the guard to also
check for a genuine `gh issue create` invocation — every dangling-issue signal requires one to have
run this session.

The guard reuses the real `_ISSUE_CREATE_STMT_RE` detection regex directly (`.search()` against the
whole transcript text, rather than the per-segment `.match()` the real detector uses), not a
hand-written substring: an earlier draft checked for the literal single-space substring `"issue
create"`, which a review of PR #639 (confirmed independently by two reviewers) pointed out could
drift from the detector it guards — the real regex tolerates arbitrary whitespace (`\s+`), so a `gh
issue  create` (tab or double space) would satisfy the detector but silently fail a literal-substring
guard, under-firing for that session. Reusing the compiled detection regex itself makes the guard
provably unable to drift from what it's guarding.

## Consequences

- Investigation sessions that file issues but implement nothing now get the same mechanical
  follow-up nudge merge sessions already get — the dev-env#630/#631 gap is closed.
- The "routine" flow (file issue → implement in the same session → PR → merge, with the PR's own
  `Closes #N`) is unaffected: the issue resolves via the merged-PR path before Stop, so
  `evaluate_issues()` never fires for it. Verified directly (`test_e2e_issue_resolved_via_merge_allows`).
- One enumeration continues to satisfy the whole session, now across *both* trigger kinds — a
  session that merges a PR and also files a dangling issue, and enumerates once covering both, is not
  asked to enumerate twice (`test_combined_one_enumeration_satisfies_both`).

### Performance

`main()` recomputes `iter_bash_calls(records)` and `session_merged_prs(calls)` twice — once inside
`evaluate()`, once inside `evaluate_issues()` — rather than sharing one computation (see "Why a fully
independent `evaluate_issues()`" above, and the rejected shared-computation alternative below). A
review of PR #639 additionally pointed out that broadening the pre-filter to also admit a genuine `gh
issue create` invocation (not just "merged") means a **new class of sessions** now pays this doubled
per-Stop scan cost: previously, a session with no merge anywhere was filtered out before any parsing
at all; now, a session that e.g. creates an issue and immediately closes it via `gh issue close`, with
no merge anywhere, passes the pre-filter every turn. Without the "created-and-resolved sets the
sentinel too" fix described above, that session would never write the sentinel and would re-pay the
full parse-and-scan cost on every single subsequent Stop for the rest of the session — the fix closes
exactly that gap by treating "created, now fully resolved, nothing to enumerate" as a resolved state
(sentinel-setting), not silently identical to "nothing ever happened."

### Testing

`test_stop_tile_enumeration_gate.py` grows from 39 to 76 tests, 0 failures. All 39 pre-existing tests
pass **unmodified** (proving `evaluate()`/`format_reminder()`/`main()`'s merged-PR path is
byte-for-byte unaffected). Coverage: issue-creation detection (URL extraction, the `--help`
non-interaction with dev-env#636's fix, heredoc anchoring); resolution via each of GitHub's three
documented keyword stems in both present and past tense, case-insensitively, on both `gh pr create`
and `gh pr edit` (bare-number and PR-URL target forms); the "PR never merged, so the Closes mention
doesn't count" negative case (for both `gh pr create` and `gh pr edit`); the heredoc-PR-body idiom
this repo's own workflow uses; the unrelated-chained-segment non-leak case; explicit `gh issue close`
in both bare-number and URL forms; `evaluate_issues()`'s full composition (fire / enum-resolved /
skip-resolved / no-issue no-op / created-and-resolved-sets-the-sentinel / lowest-deterministic /
shared #700 bare-assertion rejection); `format_issue_reminder`'s cp1252-encodability; the
combined-trigger cases (independent firing, one enumeration satisfying both); and seven end-to-end
subprocess tests (dangling blocks, enum/skip/explicit-close/merge-resolution all allow, the
combined-message case, and the sentinel suppressing a second fire) mirroring the existing e2e layer's
HOME-isolated-sentinel pattern exactly.

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
  the A4-style `(repo, number)` correlation here if it proves to matter. `session_created_issues`
  already captures each created issue's repo (unused today — see Detection design above) as a
  starting point for that future work.
- **An explicit cross-repo Closes reference (`Closes owner/repo#N`) is never detected.**
  `_CLOSES_KEYWORD_RE` requires a bare `#N` immediately after the keyword, so `owner/repo#N` — the
  syntax GitHub itself documents for linking to an issue in a different repo — never matches (flagged
  during review of PR #639). Verified this is a false **negative**, not a false match: the regex
  cannot skip over the `owner/repo` text and match just the trailing `#N` as if it were a bare
  same-repo reference, so the failure mode is "the gate still thinks the issue is dangling and asks
  for an enumeration that turns out to be unnecessary" — safe, if occasionally redundant — never
  "the wrong issue gets silently marked resolved." Treated as an extension of the cross-repo-scoping
  limitation directly above rather than a separate gap warranting its own fix.
- **A Closes-keyword living only in a commit message is invisible.** GitHub also honors closing
  keywords in commit messages, not only the PR body/description. This gate is a pure Bash-*command*
  transcript scan — it has no notion of "which commits belong to which PR" beyond what a `gh pr
  create`/`gh pr edit` command's own text (including its `--body` value) contains, so a keyword that
  exists only in a `git commit -m "Closes #N"` message and never in the PR body itself will not be
  found. Reconstructing commit-to-PR association reliably would require a materially larger change
  (tracking every `git commit`/`git push` this session and correlating to a later PR by heuristics
  more fragile than a direct command-text scan); deferred rather than attempted here.
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
  per-Stop parsing cost on the common path — see Performance under Consequences for the concrete
  scenario this leaves on the table). Rejected for this PR: it would require either changing
  `evaluate()`'s signature (touching all 39 pre-existing tests and callers) or adding a new
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
