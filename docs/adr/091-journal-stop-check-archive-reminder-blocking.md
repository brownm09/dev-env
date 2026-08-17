# ADR-091: Deliver the journal-stop-check Archive Reminder to Claude (exit 2 + stderr, not exit-0 stdout)

**Date:** 2026-07-08
**Status:** Accepted
**Amended:** 2026-07-09, 2026-08-15, 2026-08-16 (three amendments — see Amendment sections below)
**Tags:** hooks, stop, journal, archive, session-archiving, exit-code, stderr, claude-facing, adr-021, adr-088, false-positive, manifest, open-prs, session-scoping, sentinel, adr-064, cross-session, in-flight-work, todowrite, background-agent

---

## Context

After a journal stub is pushed to `engineering-journal`, `stub-push-archive-reminder.py`
(a PostToolUse/Bash hook) writes a `~/.claude/scratch/stub-pushed.flag` sentinel. The
`journal-stop-check.py` **Stop** hook then consumes that sentinel and emits an archive
reminder:

> "Stub committed and pushed to engineering-journal. Archive this session now: call
> `ccd_session_mgmt__archive_session` (use `list_sessions` to look up the current
> session_id if needed). Then stop."

That reminder is **Claude-facing**: it instructs the invocation of an MCP tool only Claude
can call (`mcp__ccd_session_mgmt__archive_session`) and then to stop. Both sibling
docstrings confirm the intent ("remind **Claude** to archive"). The intended behavior is
"Claude auto-calls `archive_session` at session end after a journal-stub push."

But the hook emitted the reminder via `print(...)` + `sys.exit(0)`. Per the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks), for a **Stop** hook,
exit-0 **stdout** is *"written to the debug log but not shown in the transcript"* and is
**not** added to Claude's context — only `UserPromptSubmit`, `UserPromptExpansion`, and
`SessionStart` get exit-0 stdout added to context. Only **exit 2 + stderr** feeds text back
to Claude on a Stop hook (blocking the stop so Claude continues with the stderr reason).

So the archive reminder never reached Claude. The intended session-archiving silently never
happened. This is the exact failure class [ADR-088](088-state-keyed-tile-enumeration-gate.md)
identified and fixed for the tile gate — *"an exit-0 advisory would add another ignorable
line … Advisory (exit 0) instead of blocking. Would be ignored — the exact mechanism-2
failure. Rejected."* ADR-088 even records the pre-existing state: *"`journal-stop-check.py`
… always exits 0 and emits its reminders on stdout."*

Surfaced during the [dev-env#612](https://github.com/brownm09/dev-env/issues/612) /
[PR #613](https://github.com/brownm09/dev-env/pull/613) Stop-hook parallelism investigation;
tracked as [dev-env#622](https://github.com/brownm09/dev-env/issues/622).

## Decision

Convert **only the archive-reminder branch** of `journal-stop-check.py` to **exit 2 +
stderr**, mirroring `stop-tile-enumeration-gate.py` (ADR-088):

- When the stub-push sentinel is present, write the archive instruction to `stderr` and
  `sys.exit(2)` — the channel that reaches Claude for a Stop hook. Nothing is written to
  stdout on this path (Claude Code ignores a Stop hook's stdout on exit 2).
- The reminder text is extracted into a pure `archive_reminder_message()` and kept
  **ASCII-only** — Claude Code pipes hook output as cp1252 on Windows, so a non-cp1252
  character (an arrow, an em-dash) would raise `UnicodeEncodeError` and the whole reminder
  would vanish (same constraint `stop-tile-enumeration-gate.py` /
  `posttooluse-inert-advisory.py` observe).

### The other three message types stay NON-blocking (exit 0, stdout)

The same hook also emits an orphaned-draft-removed FYI, a **stale-draft advisory**, and an
**unmerged-draft-branch advisory**. These are **user-facing** and point at work for a
*later, dedicated* session:

- Journal composition is a dedicated-session operation that **must never be triggered
  proactively** (`claude/CLAUDE.md` → "Never compose proactively"). Blocking the stop to
  make Claude compose now would violate that rule.
- Merging a stale draft PR is separate work, not this session's job.

So those three remain exit-0 stdout advisories, unchanged. Only the archive reminder — the
one message that asks *Claude* to take an in-session action — blocks.

### Loop safety

The archive block fires **at most once** with no Stop-loop risk, via two guards:

1. **Consume-on-read (primary).** `consume_stub_pushed_sentinel()` deletes the sentinel
   *before* returning the reminder. Once the block fires, the flag is gone, so the next Stop
   finds no reminder and exits 0. (If the `unlink` fails, the function returns `None` and
   never blocks — the flag can't both persist *and* trigger a block.)
2. **`stop_hook_active` (backstop).** The consume is gated on `not stop_hook_active`, the
   documented Claude Code anti-loop flag: while Claude is continuing from a prior block the
   hook does not consume-or-block, so the reminder is delivered on a genuine (non-continuation)
   Stop rather than being consumed without delivery.

### No settings change

`journal-stop-check.py` is already registered in the `Stop` list in `claude/settings.json`,
invoked via `pyw -3`. The tile gate — also a blocking (exit 2 + stderr) Stop hook invoked
via `pyw -3` — proves that path delivers stderr correctly (ADR-007's 2026-06-01 pyw-stdio
decision). No registration or invocation change is needed. Stop hooks run in **parallel**
(ADR-088 → Stop-hook parallelism), so this second blocking hook does not short-circuit
`awake-blocker.py`'s sleep-lock release, and two blocking hooks can each exit 2 on the same
Stop with both stderr reasons merged and fed to Claude.

## Consequences

- After a journal-stub push, the Stop is blocked once and Claude actually receives the
  archive instruction — the intended session-archiving now happens.
- If the `ccd_session_mgmt__archive_session` MCP tool is unavailable in a given session
  (e.g. a headless/cron run without that server), the single block is harmless: Claude
  notes it cannot archive and stops; the flag is already consumed, so no re-block.
- `journal-stop-check.py` becomes the **second** blocking Stop hook (with
  `stop-tile-enumeration-gate.py`). ADR-088's "first and only blocking Stop hook" note is
  corrected accordingly.
- Rollback is a one-file revert of the branch logic; the sentinel-file contract
  (`stub-pushed.flag`, consumed once) is unchanged.

## Alternatives considered

- **Keep it exit-0 stdout (status quo).** Rejected — the reminder never reaches Claude; the
  feature is inert. This is the ADR-088 mechanism-2 failure.
- **JSON `{"decision":"block","reason":...}` / `additionalContext` instead of exit 2 +
  stderr.** A valid Stop-hook mechanism, but exit 2 + stderr is the pattern already proven
  in this codebase under `pyw -3` (the tile gate) and keeps the two blocking Stop hooks
  consistent. Rejected in favor of consistency and a proven path.
- **Block on the stale-draft / unmerged-branch advisories too.** Rejected — those are
  user-facing and point at dedicated-session follow-up work; blocking the stop for them
  would violate the "never compose proactively" rule and add no value.
- **Amend ADR-088 instead of a new ADR.** Rejected — the archive reminder is a distinct
  feature (session archiving, ADR-021 lineage) from the tile gate (post-merge tiles,
  ADR-046 lineage). It shares only the *failure class*. A separate ADR is more discoverable;
  it cites ADR-088 for the shared rationale rather than duplicating it, and ADR-088's stale
  parenthetical is corrected with a cross-reference.

## References

- [ADR-088](088-state-keyed-tile-enumeration-gate.md) — the Stop-hook exit-2 blocking
  pattern this mirrors, and the "exit-0 Stop advisory is invisible/ignored" rationale.
- [ADR-021](021-auto-stub-on-pr-push.md) — the journal-stub / archive-reminder flow this
  fixes the delivery of.
- [ADR-007](007-hook-command-invocation.md) — the `pyw -3` stdio decision that makes exit 2
  + stderr deliverable from a windowless Stop hook.
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the exit-code /
  stdout-vs-stderr / per-event Stop semantics quoted above.
- [dev-env#612](https://github.com/brownm09/dev-env/issues/612) /
  [PR #613](https://github.com/brownm09/dev-env/pull/613) — the investigation that surfaced
  this.
- [dev-env#622](https://github.com/brownm09/dev-env/issues/622) — the issue this ADR closes.

## Amendment 1 (2026-07-09) — gate the sentinel on a confirmed-resolved PR, not any stub push (dev-env#651)

This ADR fixed the archive reminder's *delivery* (exit 2 + stderr instead of inert exit-0
stdout). It did not touch — and its own Context section describes without questioning — the
reminder's *trigger condition*: `stub-push-archive-reminder.py` arms the sentinel whenever
HEAD's commit touches a `.stub.md` file, with no check on whether an associated PR is still
open. Before this ADR shipped, that gap was latent: the reminder never reached Claude
regardless, so an over-eager trigger caused no observable harm. Fixing delivery made the
pre-existing gap newly harmful the same day it shipped.

**Root cause.** dev-env#121 (the issue proposing this hook, "Auto-archive session after
post-merge stub push to engineering-journal") scoped the feature to the post-merge case in
its title, but its own three implementation steps — detect the push, confirm the
most-recent commit has a `.stub.md` file, exit 2 — never actually specified a
merge-confirmation check. The shipped hook faithfully implements those exact three steps;
the "post-merge" scoping lived only in the issue's title, not its acceptance criteria.

**Symptom.** Per `claude/CLAUDE.md`'s own documented workflow ("Write the journal stub
immediately after `gh pr create`"; "PR opened — follow the Stub file workflow immediately
after `gh pr create`. If no further work is planned … stop after writing the stub."; "PR
updated … update it in place"), a stub is routinely pushed mid-session — right after `gh pr
create`, and again after each subsequent push to that PR's branch — well before `/review`
and `gh pr merge`. Confirmed against this repo's own history for two independent same-day
sessions:

- **PR #635** (`sessions/dev-env/2026-07-08_185057.*`): commit `67565a9` (18:52) added the
  stub, manifest (`"prs_opened":[635],"prs_closed":[]`), and `open-prs/635.json` together,
  right after `gh pr create`. The PR did not merge until commit `c711c43` (19:10).
- **PR #633** (`sessions/dev-env/2026-07-08_183908.*`): commit `5eea9d3` (18:40) created the
  manifest with `"prs_opened":[633],"prs_closed":[]`. Commit `b8a0652` (18:44, "review
  finding fixed") pushed a stub-only update — no manifest file in that commit's own diff —
  while the manifest on disk still read unresolved. The PR merged two minutes later in
  commit `3dcc057` (18:46).

A Stop between either pair of commits would have blocked and instructed archiving a
worktree with an open, unreviewed PR. The second case is why the fix reads each touched
stub's paired manifest from its *current* on-disk content rather than from the triggering
commit's own diff-tree: the manifest is written once, alongside the stub, right after `gh pr
create`, and an ordinary mid-session stub update does not re-touch it even though the PR it
names is still open.

**Fix.** `stub-push-archive-reminder.py`'s `main()` gains a second gate, checked after the
existing stub-touch check and before the sentinel write: `head_commit_has_unresolved_pr`.
For every `.stub.md` path HEAD's commit touched that still exists on disk (a path this
commit *deleted* — e.g. journal-compose consuming an old stub — has no in-progress session
to protect and is skipped), it derives that stub's 1:1-paired manifest path
(docs/REFERENCE.md → "Manifest shard format": "named to pair 1:1 with the session's stub"),
reads that manifest's live content, and checks each entry via a new
`_journal_schema.has_unresolved_open_pr()` — true when `prs_opened` names a PR number not
also present in `prs_closed` (compared as strings, so an int- or str-typed number in either
list still matches; a non-list field value is treated as unresolved rather than silently
misparsed). A still-live stub with no manifest yet, an unreadable manifest, or an
unparseable line all conservatively return true (fail toward **not** arming the reminder) —
the cost of silently skipping one archive reminder is far lower than the cost of this bug:
`ccd_session_mgmt__archive_session` destroys the session's worktree, and a false trigger
fires before the review/merge work that worktree is still needed for. `_journal_shards.py`'s
per-PR shard reader was considered and rejected as the data source: it reads
`open-prs/<N>.json`, a different, repo-wide-per-PR schema, not scoped to which PR *this
session* opened — the manifest's `prs_opened`/`prs_closed` is already correctly scoped
(ADR-056's per-session sharding), and is updated in the same commit as the corresponding
`open-prs/<N>.json` deletion at merge time (confirmed against PR #635's own `c711c43`), so
it is a reliable proxy without a second read.

**Coverage:** `test_journal_schema.py` gains direct tests for `has_unresolved_open_pr()`
(opened-not-closed, resolved, never-opened, int/str type-mismatch match, missing keys,
non-dict entry, partial multi-PR overlap, and a non-list field value treated as
conservative-unresolved rather than silently misparsed). `test_stub_push_archive_reminder.py`
gains tests for the now-pure `most_recent_commit_has_stub(files)` (previously a git call,
intentionally untested; the git call now lives only in the new, still-untested
`head_commit_files()`), `manifest_path_for_stub()`, and `head_commit_has_unresolved_pr()`
against tmp-dir manifest fixtures — including a direct pin of the dev-env PR #633 shape
above (an unresolved PR recorded by an earlier commit, not the triggering commit's own
diff) and the conservative-on-ambiguity branches (missing manifest, unparseable line,
deleted stub path skipped).

**General lesson (continuing this ADR's own separation of delivery from trigger):** a fix
that makes a previously-inert message suddenly reach its recipient also suddenly exercises
every pre-existing condition that arms it — the trigger condition's own correctness had
never been load-bearing until delivery started working, so nothing forced it to be
re-examined when delivery was fixed. The specific shape here — checking "does this commit's
own diff show it" as a stand-in for "what is the current state of the thing this commit is
one snapshot of" — is the same proxy-instead-of-signal failure ADR-050's amendments kept
re-finding in a different guise.

**References:** [dev-env#651](https://github.com/brownm09/dev-env/issues/651) — the issue
this amendment closes. [dev-env#601](https://github.com/brownm09/dev-env/issues/601) — a
distinct, unrelated investigation into an automated commit/push/PR-open mechanism,
cross-referenced only (not the same bug).

## Amendment 2 (2026-08-15) — scope the sentinel to the pushing session's own session_id (dev-env#980)

This ADR's Consequences section previously stated "the sentinel-file contract
(`stub-pushed.flag`, consumed once) is unchanged." That contract is exactly what this
amendment changes: the single, global `~/.claude/scratch/stub-pushed.flag` file is not
scoped to any session, so it lets ANY concurrent session's Stop consume it, not only the
session that actually pushed the stub.

**Symptom.** Reproduced 2026-08-15: a `daily-journal-compose-local` scheduled run did only
read-only git operations against engineering-journal, yet its Stop event fired the
Claude-facing archive instruction — the actual push that armed the sentinel came from a
different, concurrent dev-env session (`draft: 2026-08-15 dev-env reconcile-project-board
session`). Two distinct failure directions follow from the same missing scoping:

- **False positive** — a session that never touched engineering-journal is told to archive
  itself (destroying its own worktree) because an unrelated session's push armed the shared
  flag.
- **Missed reminder** — consume-on-read means whichever session's Stop fires first globally
  eats the flag; the session that actually did the push can lose that race and never see its
  own reminder.

**Root cause.** Neither `stub-push-archive-reminder.py` (PostToolUse) nor
`journal-stop-check.py` (Stop) read the `session_id` field their own stdin JSON payload
already carries (every hook event includes it), even though the codebase already has a
directly-reusable convention for exactly this need: `_hookutil.sentinel_path(prefix,
session_id)` (ADR-064), used by several other per-session one-shot sentinels. This pair of
hooks used a plain, unscoped path instead — a gap that predates ADR-064 in this file, not a
regression it introduced.

**Fix.** Both hooks now read `session_id` from their payload:

- `stub-push-archive-reminder.py` writes to
  `_hookutil.sentinel_path(SENTINEL_PREFIX, session_id)` (`SENTINEL_PREFIX = "stub-pushed-"`,
  so the file becomes `stub-pushed-<session_id>.flag`) instead of the old global
  `stub-pushed.flag`. It also calls `_hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)`
  first, garbage-collecting any per-session flag older than 30 days — needed now that a
  crashed or otherwise never-Stopped session's flag has no other cleanup path (the old global
  file was a single, perpetually-reused path with no such accumulation risk). It also
  opportunistically `unlink(missing_ok=True)`s the legacy global path once, since
  `cleanup_stale_sentinels`'s prefix glob (`stub-pushed-*.flag`) never matches the old
  non-hyphenated filename — an in-flight legacy flag would otherwise sit in scratch
  unmatched by anything, forever (`/review` finding).
- `journal-stop-check.py`'s `consume_stub_pushed_sentinel` now takes `session_id` and derives
  the same per-session path via `_hookutil.sentinel_path`; a Stop event only ever consumes
  the sentinel matching its own `session_id`. It also now calls
  `_hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)` on every Stop — the more reliable of
  the two cleanup call sites, since this hook fires far more often than the writer's own
  (rare) success path (`/review` finding).
- **Missing `session_id` — forgo, never fall back to a shared path.** If either hook's
  payload lacks a `session_id` (an anomalous session), the writer skips writing any sentinel
  and the reader's `consume_stub_pushed_sentinel` returns `None` without touching the
  filesystem — deliberately chosen over a synthetic-fallback-id (the pattern some other
  `_hookutil.sentinel_path` callers use for a missing `session_id`), since a fallback here
  would just reintroduce a narrower version of the identical collision this amendment fixes:
  multiple anomalous sessions could still collide on the same fallback id.
- **Unsanitized `session_id` in a path a Stop hook `unlink()`s — a `/review` finding.**
  `session_id` is trusted harness-generated input (a UUID) on every other
  `_hookutil.sentinel_path` caller, all of which only `.exists()`/`.write_text()` their own
  file, but this hook's operation is a *delete*, and this PR converts the path from a fixed
  constant into a payload-derived one. Both hooks now reject a `session_id` containing
  anything outside `^[A-Za-z0-9_-]+$` (`_SAFE_SESSION_ID`) the same way they treat a missing
  one — forgo rather than compute a path from it — closing the (mitigated-but-real, since
  `session_id` is trusted input, not the reason it should stay unguarded) path-traversal
  surface a crafted value could otherwise open via embedded `../` separators.
- **`SENTINEL_PREFIX` duplication has no automated drift guard — a `/review` finding
  (mutation-verified: a divergent literal in one file leaves the whole suite green while
  silently and totally killing the archive-reminder mechanism, with no error anywhere).**
  A cross-module test in `test_journal_stop_check.py` now asserts
  `journal_stop_check.SENTINEL_PREFIX == stub_push_archive_reminder.SENTINEL_PREFIX`.
- The new per-session sentinel family is registered in `sweep-scratch-debris.py`'s
  `KNOWN_PATTERNS` (`("stub-pushed-", ".flag")`) — omitted in the original diff (`/review`
  finding); every other self-cleaning per-session `.flag` family is listed there as the
  manual backlog-clearing utility's registry.
- `docs/REFERENCE.md`'s Hooks-table rows for both scripts, and `docs/TESTING.md` items 16
  and 50, are updated in the same PR to describe the per-session filename, the
  `_SAFE_SESSION_ID` guard, and the new coverage below (`/review` findings — both were
  initially left describing the pre-fix global-sentinel behavior).

**Coverage.** `test_journal_stop_check.py` gains: `parse_session_id()` pure tests (mirroring
the existing `parse_stop_hook_active()` set); `_SAFE_SESSION_ID` accept/reject tests;
`consume_stub_pushed_sentinel` tests for the derive-from-`session_id` path (via the new
injectable `scratch` param, not a monkeypatch of `_hookutil.SCRATCH`), the
empty-`session_id`-returns-`None`-untouched path, and the unsafe-`session_id` path; the
cross-module `SENTINEL_PREFIX` parity test; and the direct dev-env#980 regression coverage —
`test_e2e_cross_session_sentinel_not_consumed` (session A's sentinel, session B's Stop -> no
block, A's flag left intact) and `test_e2e_no_session_id_never_blocks`. All three existing
e2e tests were updated to plant/consume under a consistent `session_id` matching the payload.
The three fixture-injection tests that pass an explicit `sentinel` override no longer also
pass a `session_id` alongside it — that argument was dead in those tests (the `sentinel`
branch short-circuits before `session_id` is read), a `/review` finding.

`test_stub_push_archive_reminder.py` gains two e2e tests for the writer-side guard, built on
a new `_init_journal_fixture()` helper (a minimal, real, git-committed engineering-journal
repo — an empty root commit, then a real commit adding one resolved stub+manifest pair; two
commits because `head_commit_files()`'s `git diff-tree HEAD` returns nothing for a repo's
very first, parentless commit, `/review`-caught during implementation):
`test_session_id_present_writes_sentinel_positive_control` (a valid `session_id` against the
fixture writes a sentinel — proves the fixture genuinely reaches the write step) and
`test_no_session_id_exits_clean_without_writing_sentinel` (the SAME fixture, no `session_id`
in the payload -> no sentinel anywhere in scratch). An earlier version of the negative test
used no git fixture at all and passed even with the guard deleted, since a nonexistent
`JOURNAL_REPO` already produced the identical "exit 0, no sentinel" outcome on its own,
independent of the guard — caught by `/review` via mutation testing (deleting the guard left
the test green) and confirmed fixed the same way (the rebuilt test fails when the guard is
mutated away; the positive control still passes).

**References:** [dev-env#980](https://github.com/brownm09/dev-env/issues/980) — the issue
this amendment closes. [dev-env#651](https://github.com/brownm09/dev-env/issues/651) /
Amendment 1 (above) and [dev-env#666](https://github.com/brownm09/dev-env/issues/666) are
related but distinct bugs on the same file, not touched by this amendment.
[ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — the `sentinel_path` /
`cleanup_stale_sentinels` convention this amendment adopts.

## Amendment 3 (2026-08-16) — augment the reminder's text with an in-flight-work caveat (dev-env#1002)

This ADR's Decision section converts the archive reminder's *delivery* channel (exit 2 +
stderr) and Amendments 1/2 fixed its *trigger condition* (a confirmed-resolved PR; the
pushing session's own session_id). None of the three touched the reminder's own *content*:
once the sentinel fires, the instruction to archive is unconditional, with no visibility into
whether the session has other approved, unfinished work in flight. The fix below never
changes *whether* or *how often* the reminder fires — only what it says.

**Symptom.** A 2026-08-16 session got the archive instruction mid-way through a large,
user-approved multi-round plan: a background compose agent was still running, and 3 of 4
work tiers were unstarted. The instruction was not followed —
`mcp__ccd_session_mgmt__archive_session`'s own tool description already requires the user's
explicit, non-speculative agreement before it acts, so the tool layer caught what the hook's
advice text did not — but nothing in the reminder itself acknowledged the conflict, and a
less careful session could plausibly have complied and killed its own in-flight background
work.

**Root cause.** `archive_reminder_message()` was authored (this ADR's original Decision)
purely as a delivery-channel fix for a message whose content was never in question at the
time — the only known trigger was a stub push, and a stub push has always meant "this
session's work is done." Neither this ADR nor Amendments 1/2 ever needed the reminder to
reason about *other* concurrent work, because at each of those fixes' write time the
reminder's unconditional "archive now" was still assumed correct whenever it fired at all.
Session-scale multi-round plans with backgrounded child agents (`Agent` tool,
`run_in_background: true`) are exactly the shape that assumption doesn't hold for, and
nothing before this amendment ever checked for it.

**Correction during `/review` before merge.** The PR that closes dev-env#1002 was drafted
against an assumed transcript vocabulary rather than this harness's real one, and `/review`'s
correctness/security pass independently verified every claim against real transcript data
under `~/.claude/projects` (400-600 files sampled) before the PR merged. Two of the four
findings were severe enough to make the shipped detection non-functional or badly degraded
against this harness's real data:

- The initial version scanned for a `TodoWrite` tool_use call. **This harness has no
  `TodoWrite` tool at all** — 0 occurrences across every transcript on the machine, versus
  thousands of `TaskCreate`/`TaskUpdate` calls (the actual task-list tool). That half of the
  caveat was permanently dead code; the PR's own motivating incident ("3 of 4 work tiers
  unstarted") was itself tracked via `TaskCreate`/`TaskUpdate`, so the fix as first drafted
  would not have caught the incident it was written for.
- The initial version required `run_in_background is True` strictly, treating an omitted flag
  as excluded. Per `pre-tool-use-nested-agent-background-guard.py`'s own docstring, the
  `Agent` tool *defaults* to `run_in_background: true`, and an omitted flag on a top-level
  (main-session) spawn — exactly what a Stop hook observes — is "normal, harmless, and the
  documented default pattern." Confirmed live: roughly a third of sampled omitted-flag `Agent`
  calls behaved identically to explicit-`true` calls (a later completion notification) and
  never like explicit-`false` calls.
- Completion resolution scanned only `type=="user"` transcript records. Confirmed live: of
  every distinct backgrounded-agent id with a completion notification somewhere in a
  600-transcript sample, **half never appear in a `type=="user"` record at all** — they exist
  only in `queue-operation` (`content`, a bare string) or `attachment` (`attachment.prompt`, a
  bare string) records. The `type=="user"`-only scan produced a majority-spurious "still open"
  rate.
- Neither counter filtered `isSidechain` records, so a subagent's own `TaskCreate`/`TaskUpdate`/
  `Agent` activity was attributed to the main session's in-flight work. Confirmed live: 40% of
  sampled `Agent` tool_use calls were `isSidechain: true`.
- `format_in_flight_note()` put the "requires explicit agreement, never speculative" invariant
  *inside* the count-derived sentence, so it was emitted only when the (already-broken)
  detection produced a positive count — on every detection miss, the message reverted verbatim
  to the exact unconditional-archive bug this fix exists to prevent.
- `in_flight_work_note()` passed an unvalidated `session_id` into `_hookutil.find_transcript`,
  which interpolates it directly into a glob pattern with no sanitization of its own — a
  pre-existing gap in the shared helper (also reachable, unvalidated, from
  `stop-experiment-verdict-gate.py`), not a regression unique to this PR, but one this PR's new
  caller made reachable through a second, undocumented path.

All five were fixed in the same PR before merge (see **Fix**, below, which describes the
corrected, shipped implementation) rather than filed as follow-ups — the two detection signals
are the entire point of this amendment, so a partial fix would ship a hook that still does not
do what its own tests claimed.

**Fix.** `journal-stop-check.py` gains a best-effort, additive-only caveat layer:

- `pending_task_count(records)` scans non-`isSidechain` assistant records for `TaskCreate` and
  `TaskUpdate` tool_use calls. A `TaskCreate` call's assigned task id is not present in its own
  `input` (only `subject`/`description`/`activeForm` are) — it is announced solely in the
  paired tool_result's text, `"Task #N created successfully: ..."` (confirmed live), so a
  second pass resolves each `TaskCreate` id to its assigned number via that tool_result. Unlike
  `TodoWrite`'s single-artifact "last call replaces the whole list" model, `TaskCreate` ADDS a
  task and `TaskUpdate` mutates exactly one task's status by id, so current state is folded
  across the whole call sequence (a newly created task defaults to `"pending"` until a
  `TaskUpdate` changes it) rather than read off the last call alone. Counts only
  `"pending"`/`"in_progress"` — `"completed"` and `"deleted"` (this harness's full observed
  status vocabulary) do not count.
- `open_background_agent_count(records)` collects the tool_use `id` of every non-`isSidechain`
  `Agent` call whose `input.run_in_background` is NOT explicitly `False` (`True` or omitted
  both count — see Correction, above) and checks each against `_notification_texts()` — which
  scans `type=="user"` text, `queue-operation.content`, and `attachment.attachment.prompt` (see
  Correction, above) — for the literal substring `<tool-use-id>{id}</tool-use-id>`, the
  harness's own task-notification shape for a finished background Agent call (confirmed by
  direct observation: a backgrounded call's own immediate tool_result only confirms the async
  launch, never its completion). An id with no such occurrence anywhere in the transcript
  counts as still open. The check is per-text-item, never a joined/flattened haystack, so two
  unrelated messages can never coincidentally concatenate into a false match at their boundary.
- `format_in_flight_note(pending_tasks, open_agents)` renders both counts (or `""` when both
  are zero) into one ASCII/cp1252-safe, count-derived sentence only — the explicit-agreement
  invariant lives unconditionally in `archive_reminder_message()` instead (see Correction,
  above), so it survives every detection-miss degrade path.
- `parse_transcript_path(raw)` mirrors `parse_session_id(raw)`'s tolerant-parsing shape for
  the Stop payload's `transcript_path` field (previously unparsed by this file).
- `in_flight_work_note(transcript_path_str, session_id, *, projects=None)` is the best-effort
  orchestrator: resolve a transcript path (the payload's `transcript_path` if it names a real
  file, else `_hookutil.find_transcript(session_id, projects=projects)`, else give up), read
  its text once, run a cheap substring pre-filter (neither `"TaskCreate"`, `"TaskUpdate"`, nor
  `"Agent"` present ⇒ both counts are provably 0, matching every sibling Stop hook's
  read-then-pre-filter-then-parse pattern instead of `_hookutil.load_records`'s unconditional
  full parse -- deliberately the tool NAME, not the `run_in_background` flag text: an omitted
  flag is now a valid backgrounded signal, and an omitted flag never appears as literal
  `run_in_background` text at all, so filtering on the flag name would silently reintroduce
  the omitted-flag miss this amendment fixes), parse only if the pre-filter passes, and return
  `format_in_flight_note()`'s caveat — or `""` on ANY failure. `main()` appends a non-empty
  note to the reminder text (`f"{reminder} {note}"`) before the existing stderr write and
  `sys.exit(2)`. `consume_stub_pushed_sentinel()`'s own signature, one-shot consume-on-read
  logic, and the `stop_hook_active` gating are all untouched — this is a purely additive layer
  on top of an already-well-tested mechanism.
- `_hookutil.find_transcript()` itself now validates `session_id` against the same
  `[A-Za-z0-9_-]+` pattern `journal-stop-check.py`'s own `consume_stub_pushed_sentinel()`
  already applied to its sentinel-delete path (dev-env#980), returning `None` without touching
  the filesystem otherwise — fixed at the shared-helper level so every current and future
  caller (`token-tracker.py`, `posttooluse-inert-advisory.py`, `stop-experiment-verdict-
  gate.py`, this file) is protected, not just this one.

**Coverage.** `test_journal_stop_check.py` gains pure tests for `pending_task_count` (built
from real-shaped `TaskCreate`/`TaskUpdate` tool_use + tool_result records, not a hand-built
`TodoWrite` shape: empty records, mixed statuses, create-then-update folding, all-completed,
a `deleted` status excluded, an unresolved `TaskCreate` with no matching tool_result,
`isSidechain` records excluded, malformed records mixed in not raising),
`open_background_agent_count` (no calls, resolved via each of the `user`/`queue-operation`/
`attachment` record shapes, unresolved, a foreground call excluded, an omitted flag counted as
backgrounded, `isSidechain` calls excluded, two calls with one resolved), `format_in_flight_note`
(both zero, tasks only, agents only, both, and the ASCII/cp1252 constraint asserted the same
way `test_archive_message_is_cp1252_encodable` is — and no longer asserting the invariant
clause, which moved to `archive_reminder_message()`), and `parse_transcript_path`
(present/missing/empty/malformed, mirroring `test_parse_session_id_*`). Fixture tests cover
`in_flight_work_note`'s own path-resolution branches (explicit `transcript_path`, a stale
non-file `transcript_path` falling back via the injectable `projects` param, neither resolves,
and a malformed transcript reached via a forced exception in the fail-open path, not only
`_parse_records`'s own line-dropping) against real tmp-dir transcript files. `_run_hook()`
gains an optional `records=` parameter that plants a JSONL transcript under the tmp `home` and
adds its path to the payload only when given, leaving every existing call site byte-for-byte
unaffected. End-to-end cases assert byte-exact stderr (not loose substring containment) for: a
planted pending task producing the base reminder plus the task-count phrase; a planted
unresolved backgrounded `Agent` call producing the agent-count phrase; and — the critical
no-false-positive regression case — a planted backgrounded `Agent` call WITH its matching
completion notification producing stderr byte-identical to the unmodified
`archive_reminder_message()` text, proving a resolved agent is never flagged as still open.
The pre-existing no-transcript e2e case (`test_e2e_flag_blocks_on_stderr_and_consumes`) needed
no changes: `_hookutil.find_transcript` returns `None` on a glob against a nonexistent
`~/.claude/projects` root rather than raising, so `in_flight_work_note` degrades to `""` and
the reminder is unmodified — confirmed directly rather than assumed.

**Alternative considered and rejected: suppress the reminder and re-arm the sentinel for a
later Stop.** The sentinel is already consumed (deleted) by `consume_stub_pushed_sentinel()`
by the time any in-flight check could run, so "suppress" would need a second write path back
into a mechanism whose one-shot consume-on-read simplicity is deliberate and has already had
two amendments (Amendments 1 and 2, above) fixing subtle bugs in exactly this area. Worse: if
the in-flight work never finishes or the session ends abruptly, a re-armed sentinel could sit
stale until `cleanup_stale_sentinels`'s 30-day max-age sweep silently discards it, losing the
reminder forever — worse than today's unconditional fire. Augmenting the message's text, not
gating whether it fires, satisfies the issue's own suggested fix ("skip the archive
recommendation (or explicitly note the conflict)") via its second, lower-risk option.

**References:** [dev-env#1002](https://github.com/brownm09/dev-env/issues/1002) — the issue
this amendment closes. [dev-env#935](https://github.com/brownm09/dev-env/issues/935) —
`pre-tool-use-nested-agent-background-guard.py`, the PreToolUse hook whose docstring documents
the `run_in_background` top-level-omitted-defaults-true behavior this amendment's
`open_background_agent_count()` relies on. [PR #1009](https://github.com/brownm09/dev-env/pull/1009)
— the `/review` comment there has the full verification commands and raw counts behind the
Correction section above.
