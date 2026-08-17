# ADR-100: Journal-Stub Checkpoint Stop Hook for Report/Analysis Sessions

**Date:** 2026-07-10
**Status:** Accepted
**Amended:** 2026-08-16, 2026-08-17 (two amendments — see Amendment sections below)
**Tags:** hooks, stop, journal, stubs, report-analysis, verification, transcript-scan, exit-code, stderr, advisory, adr-062, adr-088, adr-091, todowrite, taskcreate, issidechain, subagent-transcripts

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
(career-playbook, lifting-logbook, dev-env). `TodoWrite` was re-checked live in lifting-logbook
for this amendment (0 occurrences); ADR-091's own 400–600-file sample had already established 0
occurrences repo-wide, so it was not independently re-swept across every project again here. The
comment's second exclusion,
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

## Amendment 2 (2026-08-17) — resolve the isSidechain scope note: filter all three functions, confirmed a defensive no-op (dev-env#1023)

Amendment 1's "Scope note" (above) left open whether `substantive_tool_count()`, `wrote_stub()`,
and `opened_or_merged_pr()`'s `_bash_commands()` helper should filter `isSidechain` records, the
way `journal-stop-check.py`'s `pending_task_count()` / `open_background_agent_count()` already do
(ADR-091 Amendment 3). This amendment resolves that question with live-transcript evidence rather
than deferring it again.

**The question, restated.** A subagent spawned via the `Agent` tool has its own tool calls
recorded with `isSidechain: true`. If those records live in the *same* transcript this hook scans,
an unfiltered scan attributes the subagent's own investigative legwork to the main session's "did
substantive work happen" judgment — over-counting if that's wrong, but arguably *correct* counting
if a session that delegates investigation and then reports on the findings should get credit for
that delegated work (the "argument against filtering" dev-env#1023 itself posed).

**Methodology.** Unlike ADR-091 Amendment 3's 400–600-file *sample*, this investigation scanned
**every** transcript retained on the machine — exhaustive, not sampled, because the corpus (2,475
`.jsonl` files, ~30MB total) was small enough to make sampling an unnecessary approximation. The
first pass globbed `~/.claude/projects/**/*.jsonl` unscoped and found a spurious ~90% isSidechain
rate — a methodology bug, caught before drawing any conclusion from it: `~/.claude/projects` stores
each subagent's own conversation as a **separate file** (`<session-dir>/subagents/agent-<hash>.jsonl`),
not inlined into the parent's transcript. Globbing `**/*.jsonl` sweeps those subagent-owned files in
as if they were candidate main sessions, and a subagent file is trivially "isSidechain-heavy" from
the *inside* (every record in it, including any further sub-delegation, carries `isSidechain: true`
at the top level) — a population this hook's Stop-hook `transcript_path` never receives, since
`_hookutil.find_transcript()` globs `**/{session_id}.jsonl` and a subagent's filename is
`agent-<hash>.jsonl`, never `<session_id>.jsonl`. The corrected scan excludes every path under a
`subagents/` directory, keeping only the flat `<session_id>.jsonl` files a Stop hook actually reads.

**Findings — exhaustive, not sampled, machine-wide, all retained history:**

- **586 main-session transcripts** (every project directory on the machine: career-playbook and
  ~140 of its worktree sessions, cover-letter-runtime, dev-env, engineering-journal,
  gas-lifting-logbook, lifting-logbook, win11-init-tools, and 54 `C--WINDOWS-system32` scheduled/cron
  sessions), **245,197 total assistant/user records — zero `isSidechain: true` anywhere**, confirmed
  both via the hook's own `report_intent()`-gated scan and an independent raw `grep` sweep. Spans the
  machine's entire retained transcript history (oldest to newest mtime), not just recent sessions.
- **1,889 separate subagent transcripts** (`subagents/agent-<hash>.jsonl`), **94,184 total records —
  every single one `isSidechain: true`**, zero `isSidechain: false`. Directly inspected one pair: a
  subagent's own file (`subagents/agent-a9f628de38db52852.jsonl`) carries the **same** `sessionId` as
  its parent main session (for correlation) but is a **physically distinct file**; the parent's own
  transcript contains the `Agent` tool_use call that *spawned* it (itself `isSidechain: false`, since
  the main session is the one doing the delegating) but none of the spawned subagent's own
  Read/Grep/Bash activity.
- Of the **437 sessions where the hook's own `report_intent()` fires** (the actual population these
  three functions further evaluate): **0 contain any `isSidechain` record**, so filtering changes
  **0** `substantive_tool_count()` results, **0** `wrote_stub()` results, **0**
  `opened_or_merged_pr()` results, and **0** `evaluate()` fire decisions, in either direction.

**Consequence for the open question.** Both the "argument for filtering" (avoid over-counting a
subagent's own legwork as the main session's effort) and the "argument against filtering" (delegated
work a session then reports on should count) are moot on the current harness: a subagent's tool
calls are **structurally absent** from the main transcript this hook scans, filter or no filter — the
"argument against" was never actually implementable by leaving the functions unfiltered, because the
delegated tool calls they'd need to see in order to grant that credit were never in the scanned data
to begin with, before or after this fix.

**Aside — the likely source of ADR-091 Amendment 3's "40%" figure.** That amendment's own
methodology is not independently re-verifiable here (only its conclusion, quoted in Amendment 1
above, was available to this investigation), but the *shape* of a ~40% isSidechain rate among
"sampled `Agent` tool_use calls" is consistent with the identical `subagents/`-inclusive glob this
amendment's own first pass mistakenly used — a subagent that itself further delegates records that
nested `Agent` call, `isSidechain: true`, inside its own file, and an unscoped sample would pull that
population in alongside genuine main-session `Agent` calls. This is offered as a plausible
explanation for the discrepancy, not a re-litigation of that amendment: `journal-stop-check.py`'s own
filtering is correct regardless of how that percentage was originally computed, for the same
defensive reason this amendment applies filtering here (see Decision, below) — neither amendment's
conclusion depends on the exact historical figure being exactly right.

**Decision — filter `isSidechain` in all three functions, framed as defensive consistency, not a
bug fix.** Given the findings, this is **not** a live-bug fix (no session's outcome changes) — it is
a zero-risk consistency-and-defensiveness change:

- It matches `journal-stop-check.py`'s already-reviewed convention for the identical field on the
  identical class of transcript data, so a future reader of either hook doesn't have to wonder why
  they diverge.
- It closes the open question Amendment 1 explicitly deferred, rather than deferring it again with
  no new information — dev-env#1023 asked for a decision, not a re-statement of the ambiguity.
- It defends against a **future** harness change: if a later Claude Code version ever inlines
  sidechain content back into the main transcript (plausibly the architecture ADR-091 Amendment 3's
  own environment had), this hook's counters would silently start mis-attributing subagent work the
  moment that happens, with nothing forcing a re-examination — the same "an assumption stops being
  examined once it stops being load-bearing" pattern ADR-091 Amendment 1's own "General lesson"
  describes for a different hook. Filtering now means that re-examination is already done.
- The fix is 3 one-line additions (`if rec.get("isSidechain"): continue`), each a direct copy of an
  already-reviewed pattern — near-zero implementation risk to weigh against the above.

**Applied uniformly, including the two existence checks.** `substantive_tool_count()` is an
effort-measurement question (did *this session* do the work), for which filtering is the same
unambiguous case ADR-091 Amendment 3 already made for its own two counters. `wrote_stub()` and
`opened_or_merged_pr()` are different in kind — existence checks (does a stub/PR already exist) —
so filtering there carries a real, if narrow, theoretical trade-off: in a hypothetical future
inlined-sidechain world, a subagent that wrote the stub or ran `gh pr create`/`gh pr merge` on the
session's behalf would no longer satisfy the check, causing a false-positive re-nudge. This is
accepted for three reasons: (1) the documented Engineering Journal workflow treats stub-writing and
PR-open/merge as main-session bookkeeping steps, not delegated legwork, so the scenario is
against-convention to begin with; (2) even *today*, unfiltered, these events can't be produced by a
subagent's tool calls landing in the main transcript anyway (same structural absence as
`substantive_tool_count()`'s case), so filtering doesn't newly create the gap — it only prepares for
the same hypothetical future the effort-measurement case does; and (3) the cost of that false
positive is exactly the ordinary one-dismissable-nudge cost every other false positive in this
advisory hook already carries (see the base ADR's Consequences), not the "destroys the session"
stakes that made `journal-stop-check.py`'s archive reminder specifically dangerous. Uniform filtering
keeps the file internally consistent rather than filtering one function and not its siblings for a
narrow, currently-unobservable edge case.

**Alternatives considered:**

- **Leave as-is, document the ambiguity (dev-env#1023's option 2).** Rejected: the investigation
  didn't just fail to resolve the ambiguity, it dissolved it — both the over- and under-counting
  scenarios the ambiguity was about are provably impossible on the current harness. Documenting a
  now-resolved ambiguity as still-open would be leaving stale uncertainty on the page.
- **Weighted / partial-credit counting for delegated work (dev-env#1023's option 3, "something in
  between").** Rejected: adds real complexity (a new scoring dimension, more to test and reason
  about) for zero observed benefit today, and no clean semantic case survives the findings above
  either — a subagent's own legwork isn't gradient-attributable to the main session; it's either
  main-session effort or it isn't, and it structurally never appears in this hook's scanned data
  either way.
- **Filter only `substantive_tool_count()`, leave `wrote_stub()`/`opened_or_merged_pr()`
  unfiltered.** Considered given the existence-check asymmetry discussed above, but rejected in favor
  of uniform filtering — the asymmetry's real-world cost is already zero today and bounded (one
  dismissable nudge) even in the hypothetical future case, while a split policy leaves a harder
  question for the next reader ("why does this file filter isSidechain in one function but not its
  siblings?") than the uniform policy does.

**Coverage.** `test_stop_journal_stub_checkpoint.py` gains, mirroring the real-shaped-fixture
convention Amendment 1 established: `test_substantive_ignores_sidechain_records` and
`test_substantive_mixed_sidechain_and_main` (isSidechain-only work counts 0; a mix counts only the
non-isSidechain calls, regardless of isSidechain volume); `test_pr_create_sidechain_not_counted` and
`test_pr_merge_sidechain_not_counted`; `test_wrote_stub_sidechain_write_not_counted` and
`test_wrote_stub_sidechain_bash_not_counted`; an `evaluate()`-level regression,
`test_evaluate_sidechain_only_work_does_not_cross_threshold` (report intent + 10 isSidechain-only
reads + 0 main-session reads stays a no-op); and an end-to-end case,
`test_e2e_sidechain_only_work_allows`, driving the real hook over stdin. A shared `_sidechain(rec)`
fixture helper (returns a copy of a built record with `isSidechain: True` set) keeps every existing
builder function and test byte-for-byte unaffected. 73 tests total (up from 65 after Amendment 1).

**References:** [dev-env#1023](https://github.com/brownm09/dev-env/issues/1023) — the issue this
amendment closes, filed as a direct follow-up from Amendment 1's own scope note.
[dev-env#1020](https://github.com/brownm09/dev-env/issues/1020) / Amendment 1 (above) — the scope
note this amendment resolves. [ADR-091 Amendment
3](091-journal-stop-check-archive-reminder-blocking.md#amendment-3-2026-08-16--augment-the-reminders-text-with-an-in-flight-work-caveat-dev-env1002)
— the sibling hook's isSidechain-filtered counters this amendment brings this hook's three functions
into consistency with, and the likely source of the "40%" figure discussed in the Aside above.
