# ADR-150: Context Growth as a Journal-Stub Trigger, Measured from the First Turn

**Date:** 2026-10-30
**Status:** Accepted
**Tags:** journal, stubs, update-trigger, hooks, UserPromptSubmit, turn-count-hook, context-size, compaction, crash-recovery, advisory, fail-open, hook-config, claude-facing, adr-062, adr-100, adr-129

---

## Context

Journal stubs are written at fixed events: a PR opened, a push to an open PR, a merge, a
report, a strategic decision ([ADR-062](062-journal-report-analysis-trigger.md),
[ADR-100](100-stop-journal-stub-checkpoint-hook.md)). A long session that works between those
events leaves its stub out of date. On 2026-10-29/30 a win11-init-tools session spent hours on
freeze diagnosis and machine configuration after its PR merged. It passed 400k tokens before
anyone updated the stub, and the Claude desktop app restarted in the middle of it. The stub was
written only because the user asked
([dev-env#1160](https://github.com/brownm09/dev-env/issues/1160)).

`turn-count-hook.py`, a UserPromptSubmit hook, already reads the transcript on every prompt to
print its `[session-size]` warning. That makes context size the cheapest available measure of
how much unrecorded work a session has accumulated.

**Measurement, 190 main-session transcripts, last 30 days on this machine:**

| # | Quantity | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|---|
| 1 | Context on the session's first assistant turn | 130,852 | — | 173,370 | — | 184,799 |
| 2 | Peak context | — | — | 402,674 | 676,080 | 963,291 |
| 3 | Growth from first turn to peak | — | 127,182 | 234,345 | 508,795 | 786,355 |
| 4 | Cumulative growth, the shipped measure (Decision 2; 191 transcripts, re-run during review) | — | 132,945 | 252,743 | 534,504 | 1,372,659 |

62 of the 190 sessions compacted at least once. Row 4 is larger than row 3 at the top because a
compacted session's cumulative growth keeps counting past its peak. The maximum was 2,483,616.

Row 1 is the finding that shapes the design. Before any work happens, the loaded instructions
already put a session at roughly 130k–185k tokens. A threshold on *absolute* context, such as
the issue's example "first crossing of 150k", fires on the first prompt of 189 of the 190
sessions. It would measure the size of `CLAUDE.md`, not the work done.

## Decision

1. **Extend `turn-count-hook.py`; add no new hook.** On this machine, starting a Python
   interpreter dominates hook latency (0.5–4.3 s per start under Defender + Acronis, measured on
   [dev-env#851](https://github.com/brownm09/dev-env/issues/851)). The hook already scans the
   transcript once per prompt. That one pass now returns both the current context, which feeds
   `[session-size]`, and the growth figure below. There is no extra process and no second full
   scan. The stub path is looked up only when a band fires, with a reverse read that stops at the
   newest stub write (it walks the whole file only when the session has written none). When the
   #851 UserPromptSubmit dispatcher lands, this logic moves with the rest of the hook.
2. **Measure cumulative growth since the first turn.** Growth is the sum of every increase in
   context between consecutive main-thread assistant turns. A compaction's drop is never
   subtracted, so work done after a compaction keeps counting toward the next band, and a
   re-climb past an absolute size cannot re-fire a band. Sidechain (subagent) records are
   skipped, because their smaller context would read as a drop followed by a spurious rise. This
   also makes `[session-size]` read the main thread's context rather than a subagent's.
   Zero-usage records are skipped too. Claude Code writes synthetic assistant turns ("No response
   requested.", a usage-limit notice, an API error) with every usage field 0, and counting one
   would add the whole context back as growth. The `/review` of PR #1164 found such records in
   38 of 192 local transcripts, adding 179 spurious bands.
3. **Bands: the first at 150k tokens of growth, then every further 100k.** These are the
   numbers the issue proposed, now applied to growth. Measured with the shipped formula (row 4),
   137 of 191 sessions would have been nudged at least once, 757 nudges in total (4.0 per session
   on average). The most was 24, in the session with 2.48M of cumulative growth. The rate is fixed
   per unit of work, one nudge per 100k tokens of real context growth, so the count tracks how
   much a session did. A first draft of this ADR quoted 462 nudges and a maximum of 8, computed
   from row 3; `/review` caught that row 3 is the "last minus first" measure rejected below. A
   150k step would give 532 nudges with a maximum of 16, at the cost of up to 150k of
   unrecorded work. The incident session would have been nudged by about 320k
   absolute and again by about 420k. Projects can override both values with
   `journal_stub_context_first_tokens` and `journal_stub_context_step_tokens` in
   `.claude/hook-config.json`. A non-positive or non-integer value falls back to that key's
   default.
4. **Each band fires once.** The highest band already nudged is stored in
   `~/.claude/scratch/ctx-stub-<session_id>.txt`. Growth that jumps several bands in one turn
   produces one nudge. A missing or unsafe session id never fires, so sessions without an id
   cannot share one state file and suppress each other's nudges.
5. **The nudge is advisory, fails open, and follows ADR-129.** It goes to stdout with exit 0,
   which UserPromptSubmit injects as context. It names the stub the session last wrote with
   Write/Edit, to be updated in place with the Edit tool. If there is none, it says to create one
   with the Write tool, per the Stub file workflow. It says never to write a stub through a shell
   heredoc, echo or redirect ([ADR-129](129-journal-shell-write-guard.md)), and to commit with an
   explicit pathspec. When the stub is already current, no edit is needed. The text is ASCII.
6. **Rule home.** `claude/CLAUDE.md` → Engineering Journal → Update triggers lists this as a
   trigger next to the event triggers.

## Consequences

- A long non-PR stretch now costs at most about one band of unrecorded work, instead of an
  unbounded amount, when a crash, compaction or app restart cuts it off.
- Sessions that hit a PR or merge trigger soon after a band will sometimes get a redundant
  nudge. The nudge says no edit is needed in that case, and a nudge that costs a glance is
  cheaper than lost work.
- Each nudge that leads to an edit costs a stub Edit, a commit and a push. That is about 25
  nudges per day across all sessions (757 in 30 days), each standing for 100k tokens of work, so
  the cost of a nudge is small next to the context it protects. If it proves noisy, the
  hook-config keys tune it without a code change.
- The stub lookup sees only Write/Edit calls. A stub written by a shell command is not found and
  the nudge says to create one. ADR-129 already blocks shell writes to stubs, so this misses
  nothing written under the current rules.
- If the shared `CLAUDE.md` content grows, the baseline grows with it, and growth-relative bands
  are unaffected. Absolute bands would have drifted.

## Alternatives Considered

- **Absolute context thresholds (the issue's example).** Rejected: row 1 of the measurement shows
  they fire on the first prompt of nearly every session, and they would drift as the loaded
  instructions grow.
- **Growth as last turn minus first turn.** Simpler, but a compaction drops context back to about
  the baseline, so a compacted session would never reach its next band again. The incident shape,
  hours of work in one long session, includes exactly those sessions (62 of 190 compacted).
  Cumulative growth costs two extra lines in the same pass.
- **A sibling UserPromptSubmit hook.** Rejected: one more Python start per prompt on a machine
  where that start is the dominant latency (#851), plus a second full transcript scan.
- **A Stop hook.** Rejected: Stop fires at the end of every turn, including mid-task turns, and a
  Stop hook that wants Claude to see its message has to block (exit 2,
  [ADR-091](091-journal-stop-check-archive-reminder-blocking.md)). UserPromptSubmit already reads
  the transcript and delivers an exit-0 stdout advisory.
- **Suppressing the nudge when a stub was written recently.** Deferred: the nudge text already
  tells Claude to skip a current stub. Adding the check means correlating record positions with
  band crossings, which is more code than a redundant nudge costs.

## References

- [dev-env#1160](https://github.com/brownm09/dev-env/issues/1160) — the issue.
- [dev-env#851](https://github.com/brownm09/dev-env/issues/851) — hook dispatcher, and the
  interpreter-start measurement.
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the UserPromptSubmit
  `transcript_path` payload field, and exit-0 stdout added as context.
- [ADR-062](062-journal-report-analysis-trigger.md), [ADR-100](100-stop-journal-stub-checkpoint-hook.md),
  [ADR-129](129-journal-shell-write-guard.md).
