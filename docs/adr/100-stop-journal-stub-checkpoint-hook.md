# ADR-100: Journal-Stub Checkpoint Stop Hook for Report/Analysis Sessions

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** hooks, stop, journal, stubs, report-analysis, verification, transcript-scan, exit-code, stderr, advisory, adr-062, adr-088, adr-091, todowrite, taskcreate

---

## Context

The global CLAUDE.md **"Report / analysis generated"** journal trigger ([ADR-062](062-journal-report-analysis-trigger.md)) makes a report, investigation write-up, verification / deploy-check, audit, comparison, or findings summary its own journal boundary: a stub is required even when the session opens no PR.

Every *other* stub trigger already has a mechanical reminder behind it:

- `gh pr create` / `gh pr merge` → `pr-merge-reminder.py` (PostToolUse, exit 2)
- `git push` to an open-PR branch → the push reminder in the same hook
- a stub pushed to engineering-journal → `journal-stop-check.py`'s archive reminder (Stop, exit 2 — [ADR-091](091-journal-stop-check-archive-reminder-blocking.md))
- a merged PR / dangling issue / un-tabled tile → `stop-tile-enumeration-gate.py` (Stop, exit 2 — [ADR-088](088-state-keyed-tile-enumeration-gate.md), [ADR-092](092-dangling-issue-tile-enumeration-gate.md))

The report/analysis trigger was the exception: **prose-only, with no enforcement.** A session that produced a report, an investigation write-up, or a production-fix verification but opened no PR got no reminder, so the stub was silently skipped whenever it slipped Claude's mind.

**Motivating incident (dev-env#702).** A lifting-logbook session on 2026-07-10 verifying PR #770's production CORS/IAM fix produced a verification write-up, opened/merged no PR, and wrote no stub. The omission went unnoticed until the user asked "did you update the journal?" — exactly the failure mode the report/analysis trigger exists to prevent, with nothing firing to catch it.

## Decision

Add a global **Stop** hook, `stop-journal-stub-checkpoint.py`, that scans the just-ended transcript and blocks the stop (exit 2, reminder on stderr) when a report/analysis/verification session is ending with no stub. It is registered in the `Stop` array of `claude/settings.json`, so it applies to every project.

### Detection — keyword intent + substantive-work threshold

`evaluate(records)` fires only when **all** hold:

1. **Report intent** — a *genuine user prompt* matches a report/analysis keyword (`report`, `analysis`/`analyze`/`analyse` — deliberately **not** `analytics`, a product feature; `investigat*`, `audit*`, `compar*`, `findings`, `write-up`, `summar*`) or a verify/deploy keyword (`verif*`, `production <fix|verif|…>`, `deploy(ment) verif/check`, `check the deploy` — bare `deploy` is **excluded** so a plain "the deploy broke" dev task doesn't fire). Scoped to `type == "user"` records, skipping `isMeta` / `isCompactSummary` synthetic records and slash-command wrappers — a keyword in tool output, assistant text, or command machinery never counts. This is the primary false-positive discriminator. (The `analytics` / bare-`deploy` narrowings landed in `/review` on PR #706; see Limitations for the residual class.)
2. **Substantive work** — at least `SUBSTANTIVE_THRESHOLD` (= 5) substantive tool calls (Read / Grep / Glob / Bash / Edit / Write / NotebookEdit / WebFetch / WebSearch — reads count, since report sessions are read-dominated), so a trivial keyworded lookup does not fire.
3. **No PR opened or merged** — those already nudge; suppress the double-reminder. Anchored `gh pr create` / `gh pr merge` detection via the shared `_hookio.scan_top_level` + `is_help_only` (so a heredoc-body mention or a `--help`-only invocation is not miscounted).
4. **No stub written** — no Write/Edit to a `*.stub.md` path, and no Bash command referencing one (a pre-written stub staged with `git add`).
5. **Not a `/review` session** — review-only sessions are exempt (their findings live on the PR, not a free-standing report).
6. **No skip override** — a genuine user "skip journal" / "no stub" instruction waives it up front.

Detection was chosen as **keyword + threshold** rather than a pure work-heuristic because a false exit-2 has real cost (Claude must either write an unneeded stub or reason past the block), so the keyword anchor keeps false positives low; the threshold guards the low end against a trivial keyworded question.

### Why exit 2 (blocking-once), not a non-blocking advisory

Per [ADR-091](091-journal-stop-check-archive-reminder-blocking.md), a Stop hook's **exit-0 stdout is written to the debug log, not added to Claude's context** — it is invisible to Claude. The only Stop delivery that reaches Claude is **exit 2 + stderr**, which is exactly what the PR-event and tile reminders already use. So "the same reminder PR events get" is, mechanically, an exit-2 reminder.

It is advisory *in spirit*, not a hard gate: a once-per-session scratch sentinel (`journal-stub-checkpoint-<session_id>.flag`, written **before** emitting so a re-entrant Stop cannot double-block) fires it at most once and never repeats; the `stop_hook_active` loop guard prevents re-blocking once Claude is continuing; and the reminder carries a one-line dismissal telling Claude how to wave off a false positive. A single sentinel suffices — this hook has one trigger, unlike the tile gate's three ([ADR-097](097-per-trigger-tile-gate-sentinels.md)).

### Why a Stop hook (not PostToolUse, not UserPromptSubmit)

There is no `gh` command to hang a PostToolUse hook on — the whole point is a session with *no* PR command. PostToolUse hooks are also inert in background / SDK-launched sessions ([ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)). A UserPromptSubmit nudge would arrive on the *next* prompt — too late for this session's stub, and one-shot background sessions have no next prompt ([ADR-055](055-reliable-event-inert-posttooluse-advisory.md)). The **Stop** event fires reliably at session end, including in background / `spawn_task` sessions — the right event for coverage of the population that most needs it.

### Failure mode

Pure transcript scan — no `gh` calls, no network, no subprocess. Fail-open: empty/malformed stdin, a missing transcript, a parse/scan error, and the outer `__main__` guard all exit 0. A cheap pre-filter (the intent regexes `.search`-ed over the raw transcript text) short-circuits before parsing; it reuses the exact detector regexes, so it can never drift below the detector and wrongly fast-exit a fire-worthy session.

### Stop-hook parallelism

All Stop hooks run in parallel ([ADR-088 → Stop-hook parallelism](088-state-keyed-tile-enumeration-gate.md)); adding this one does not reorder or short-circuit the existing five, and `awake-blocker.py`'s sleep-lock release still runs regardless of this hook's exit code.

## Consequences

- The report/analysis/verification stub trigger now has the same mechanical backstop the PR triggers have; the dev-env#702 class of silent miss is caught at session end.
- One extra Stop hook runs per turn. A cheap keyword-stem pre-filter (`_PREFILTER_RE`) skips the transcript parse when **no** stem appears anywhere — but these stems (`report`/`analy`/`verif`/`deploy`/`summar`/…) are common in ordinary assistant prose and tool output, so on substantive sessions the parse usually *does* run, and — because the no-genuine-intent path writes no sentinel — it re-runs on every Stop for the rest of the session. The cost is bounded (one transcript read + line-wise `json.loads` per Stop, the same worst case as the sibling tile gate) and accepted; the pre-filter's value is the genuinely keyword-free session, not the common one. (This corrects the original "single regex scan, no parse" framing — a review-of-PR-#706 finding.)
- A false positive costs exactly one dismissable nudge per session; the dismissal line and the "skip journal" override keep the friction bounded.
- The global `claude/CLAUDE.md` "Report / analysis generated" bullet now names **verification / deploy-check** explicitly (the class the incident missed) and cites this hook.

## Limitations (documented, accepted)

- **Keyword-based intent.** A report request phrased with no keyword ("look into why p99 regressed") does not fire — an accepted false negative, the trade for low false-positive noise. The keyword lists are tunable module constants.
- **Post-compaction intent loss.** If the originating prompt survives only as an `isCompactSummary`, intent is not detected (synthetic records are skipped by design). Accepted, mirroring the tile gate's same decision.
- **Session-global, not per-project.** A stub written for project B suppresses the nudge for a report about project A in the same session. Mirrors the tile gate's documented session-global limitation.
- **"Will PR later."** A session that did report-shaped work it intends to land in a *later* PR fires (no PR this session yet); the dismissal line covers it. Arguably not a false positive — CLAUDE.md treats a report as a journal boundary independent of PRs.
- **Incidental-keyword false positives.** The keyword lists match a report/verify *word*, not a report/verify *request*, so an ordinary dev prompt that mentions one incidentally ("add a reports tab", "refactor the comparison helper", "verify this compiles then commit") can fire. The two highest-incidence collisions were narrowed in `/review` on PR #706 (`analytics`, bare `deploy`), but a residual class remains. Accepted because the nudge is advisory, dismissable in one line, and fires at most once per session — a false positive costs one dismissable nudge, the trade the "keyword + threshold" detection was chosen for. Tightening further (requiring a request/imperative shape) is a possible follow-up, weighed against the false-negative risk of over-narrowing.
- **Fires at the first qualifying turn, not session end; once per session.** A Stop hook fires at *every* turn-end. The FIRE condition (intent + ≥5 tools + no PR/stub) is typically satisfied mid-session, so the nudge lands at the first such turn and — being once-per-session (the sentinel is written on the fire) — is not re-delivered at true session end. A multi-turn report session therefore receives the reminder early; if Claude dismisses it as "still working," the end-of-session moment is not re-flagged. This is inherent to a Stop hook whose trigger is *non-terminal* — unlike the tile gate's, which becomes true only at merge (a naturally late event). Re-arming would mean re-blocking every turn until a stub exists (worse for an advisory), so once-early is the chosen trade; a mid-session nudge still plants the reminder that the motivating incident lacked entirely. (Surfaced in `/review` on PR #706.)

## Alternatives considered

- **Truly non-blocking (exit-0 stdout).** Matches the literal "non-blocking" framing of the request but is invisible to Claude per ADR-091 — effectively a no-op. Rejected.
- **Pure work-heuristic (no keyword).** Any substantive non-merge session with no stub nudges. Rejected: fires noisily on ordinary mid-task coding sessions that simply haven't opened their PR yet.
- **UserPromptSubmit next-prompt nudge.** Rejected: too late for this session's stub, and misses one-shot background sessions (no next prompt) — the same reasoning ADR-055 records.
- **Extend `journal-stop-check.py`.** Rejected: that hook mixes a blocking archive branch with non-blocking advisories and keys on a cross-hook push sentinel; a focused single-purpose sibling matches the repo's Stop-hook convention.

## References

- [ADR-062](062-journal-report-analysis-trigger.md) — the prose rule this hook mechanizes
- [ADR-091](091-journal-stop-check-archive-reminder-blocking.md) — exit-2 + stderr is the only Stop delivery that reaches Claude
- [ADR-088](088-state-keyed-tile-enumeration-gate.md) / [ADR-092](092-dangling-issue-tile-enumeration-gate.md) / [ADR-097](097-per-trigger-tile-gate-sentinels.md) — the sibling state-keyed Stop gate this is modeled on
- [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) / [ADR-055](055-reliable-event-inert-posttooluse-advisory.md) — Stop fires in background sessions; PostToolUse does not
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) / [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) / [ADR-090](090-shared-transcript-readers-hookutil.md) — the shared `_hookio` / `_hookutil` modules reused
- dev-env#702 — the report-analysis stub-enforcement gap
- lifting-logbook PR #770 — the motivating prod-verification session (2026-07-10)

## Amendment 1 (2026-08-16) — correct the substantive-tool exclusion comment to this harness's real tool names (dev-env#1020)

`substantive_tool_count()`'s `_SUBSTANTIVE_TOOLS` frozenset is, and remains, a closed
allowlist of nine tool names (`Bash`, `Read`, `Grep`, `Glob`, `Edit`, `Write`, `NotebookEdit`,
`WebFetch`, `WebSearch`) — anything not named is already excluded by omission, so this
amendment changes no runtime behavior. It corrects only the comment documenting *why* two
tool families are excluded, which named tools that do not exist in this harness's real
transcript vocabulary.

**Symptom.** The comment above `_SUBSTANTIVE_TOOLS` read: "TodoWrite (bookkeeping) and
Task/spawn_task/mcp__* (delegation) are excluded so a single delegation can't inflate the
count past the threshold on its own."

**Root cause.** Both named exclusions were stale. Per dev-env#1002's `/review` finding (this
ADR's own [ADR-091 Amendment 3](091-journal-stop-check-archive-reminder-blocking.md#amendment-3-2026-08-16--augment-the-reminders-text-with-an-in-flight-work-caveat-dev-env1002)),
**this harness has no `TodoWrite` tool at all** — 0 occurrences across every transcript on
the machine, versus thousands of `TaskCreate`/`TaskUpdate` calls (the actual task-list tool).
Confirmed independently live for this amendment (not merely cited from the sibling fix):
`TaskCreate`/`TaskUpdate` occur in the tens per session across many sampled transcript files
(career-playbook, lifting-logbook, dev-env); `TodoWrite` — 0. The comment's second exclusion,
bare `"Task"` for the subagent-spawning tool, was *also* stale: 0 occurrences of `"name":
"Task"` across every sampled project, versus the real delegation tool name, `"Agent"` (18
occurrences in dev-env's own transcripts alone, 97 in career-playbook's). Since
`_SUBSTANTIVE_TOOLS` is an allowlist rather than an explicit exclusion list, neither staleness
was a functional bug — `TaskCreate`/`TaskUpdate`/`Agent` were already correctly excluded by
never being allowlisted — but the comment's rationale was undiscoverable for the tools that
actually appear.

**Re-derivation, not a find/replace.** dev-env#1020 explicitly asked whether the fix should
*retarget* the exclusion (keep `TaskCreate`/`TaskUpdate` uncounted, just rename the comment) or
*re-derive* the bookkeeping-vs-substantive judgment from scratch, since `TaskCreate`/
`TaskUpdate`'s call shape is not a 1:1 match for `TodoWrite`'s: `TodoWrite` is a single
whole-list-replace call per update, while `TaskCreate` adds one task and `TaskUpdate` mutates
one task's status per call — a session tracking an 8-task plan can produce 15+ such calls, an
order of magnitude more granular than `TodoWrite` would have produced for the same plan. That
granularity was weighed as a reason *for* counting (each call is smaller, so more of them might
better approximate real incremental work) and rejected: `TaskCreate` calls happen *before* any
task's real work starts (pure planning), and even `TaskUpdate("completed")` calls are, at best,
redundant with the `Read`/`Bash`/`Edit`/`Write` calls that did the task's real work (already
counted) — never additional signal on their own. Counting them would let planning volume alone
cross `SUBSTANTIVE_THRESHOLD` with zero investigative work done, which is exactly the "trivial
lookup" false-positive class `SUBSTANTIVE_THRESHOLD` exists to filter out (see Decision, point
2, above). The exclusion stays; only its documented rationale changed.

**Fix.** `_SUBSTANTIVE_TOOLS`'s membership is unchanged. The comment now names `TaskCreate`/
`TaskUpdate` as the real, heavily-used bookkeeping tool excluded (with the live counts above)
and `Agent`/`spawn_task`/`mcp__*` as the real delegation family excluded (bare `"Task"`
dropped). `claude/scripts/tests/test_stop_journal_stub_checkpoint.py`'s exclusion test was
rebuilt from real-shaped `TaskCreate`/`TaskUpdate`/`Agent` tool_use records rather than a
hand-built `TodoWrite` fixture (mirroring `test_journal_stop_check.py`'s equivalent rebuild in
PR #1009), and a new `evaluate()`-level test pins the specific scenario the re-derivation
reasoned about: 8 `TaskCreate` + 8 `TaskUpdate` calls plus only 3 real reads stays below
`SUBSTANTIVE_THRESHOLD` and does not fire.

**Scope note.** This hook's `substantive_tool_count()`, `wrote_stub()`, and
`opened_or_merged_pr()`'s `_bash_commands()` helper do not filter `isSidechain` records, unlike
`journal-stop-check.py`'s `pending_task_count()` / `open_background_agent_count()` (fixed in
[ADR-091 Amendment 3](091-journal-stop-check-archive-reminder-blocking.md#amendment-3-2026-08-16--augment-the-reminders-text-with-an-in-flight-work-caveat-dev-env1002)).
Deliberately **not** fixed here: unlike the archive-reminder's in-flight-work caveat (where
counting a finished subagent's activity as the *main* session's still-open work is unambiguously
wrong), it is genuinely unclear whether a subagent's delegated investigative work should count
toward *this* hook's "did substantive work happen this session" judgment — an argument exists
that delegated investigation the session then reports on **should** count. Filed as an open
question rather than assumed either way; see the tracking issue referenced from dev-env#1020's
follow-up.

**References:** [dev-env#1020](https://github.com/brownm09/dev-env/issues/1020) — the issue
this amendment closes. [dev-env#1002](https://github.com/brownm09/dev-env/issues/1002) /
[PR #1009](https://github.com/brownm09/dev-env/pull/1009) — the sibling correction in
`journal-stop-check.py` this amendment mirrors.
