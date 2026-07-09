# ADR-091: Deliver the journal-stop-check Archive Reminder to Claude (exit 2 + stderr, not exit-0 stdout)

**Date:** 2026-07-08
**Status:** Accepted
**Amended:** 2026-07-09 (one amendment — see Amendment section below)
**Tags:** hooks, stop, journal, archive, session-archiving, exit-code, stderr, claude-facing, adr-021, adr-088, false-positive, manifest, open-prs

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
