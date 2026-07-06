# ADR-086: Pre-Compose Liveness Guard Against a Still-Active Journal-Writing Session

**Date:** 2026-07-05
**Status:** Accepted
**Tags:** journal, composition, routines, liveness, concurrency, hooks, correction, ADR-051, ADR-084

---

## Context

ADR-084 fixed the nightly automated journal-compose so it actually runs (previously it silently
guard-blocked on every invocation). That fix activated a previously-theoretical race, documented in
ADR-084's Consequences section: a session that starts before local midnight and is still
uncommitted past ~7am the next morning writes its engineering-journal stub under *yesterday's*
date. If the automated compose (`claude/routines/daily-journal-compose/SKILL.md`, invoked via
`claude/scripts/journal-compose-with-retry.sh`) merges that date's `draft/YYYY-MM-DD` branch before
the session commits and pushes its stub, the session's later push hits the pre-push hook's
merged-draft-branch block. Not data loss — the existing `draft/YYYY-MM-DD-recovery` runbook
(`docs/REFERENCE.md`) already covers recovering a merged branch that needs more commits — but
nothing checked for an in-progress session before the automated compose proceeded.

[dev-env#586](https://github.com/brownm09/dev-env/issues/586) tracked this as a follow-up.
Investigation into a fix surfaced two design questions ADR-084 deliberately deferred:

1. **What counts as evidence of an active session?** ADR-051's `worktree_session_is_live()`
   (`claude/scripts/_worktree_liveness.py`) signals liveness via transcript mtime for *one specific
   worktree path* — the pattern that motivated this issue's original framing. It doesn't transfer
   cleanly here: a session that might write to yesterday's draft branch could be running from *any*
   project's worktree (lifting-logbook, career-playbook, dev-env, etc.), not a single journal
   worktree. There's no one path to check, and scanning system-wide session transcripts for any
   recent activity would be broad and noisy (most active sessions have nothing to do with the
   journal).
2. **What should happen when evidence is found — block, or warn?** journal-compose's Step 0.6
   already runs a divergence guard (a local ref ahead of origin means unpushed-but-committed stubs
   exist) and a compose-worktree concurrency lock check, both before creating the isolated compose
   worktree (ADR-082). The divergence guard already catches "committed but not pushed." The actual
   gap is narrower: a session that hasn't committed *at all* yet.

Because `engineering-journal` is a single shared checkout — sessions across every project write to
it via `git -C C:/Users/brown/Git/engineering-journal ...`, not a per-session worktree of the
journal itself (`claude/CLAUDE.md`'s Stub file workflow) — an uncommitted stub for the target date
shows up as a dirty working tree in that one, well-known location. This is a much simpler and more
direct signal than cross-worktree transcript scanning.

A further check during implementation found that `journal-compose-with-retry.sh`'s retry loop
decides whether to retry based on `claude -p`'s own process exit code (`[ $? -eq 0 ]`), and Claude
Code's public CLI reference does not specify whether that exit code reflects an in-session task
abort as opposed to only a CLI-level crash — the `-p`/`--print` entry defers to the Agent SDK
documentation, which does not state this either. Rather than depend on an unverified mechanism for
unattended nightly automation, the check is placed directly in the bash wrapper as a deterministic
pre-check, decoupled from any assumption about how a skill-internal abort propagates through
`claude -p`.

---

## Decision

**Two-layer check, both reusing the same underlying script
(`claude/scripts/check-journal-compose-liveness.py`):**

1. **Primary — `claude/scripts/journal-compose-with-retry.sh`.** Before each `claude -p` invocation
   except the last, pipe `git -C "$EJ" status --porcelain` through the check script for the target
   date. If it exits non-zero (dirty), skip that attempt without spending a `claude -p` call, log
   it, and sleep/retry exactly as an ordinary failed attempt would. On the final attempt, proceed
   regardless of the check's result — accepting the residual risk, which the existing recovery
   runbook covers — rather than let the day's journal never compose automatically at all.
2. **Defense-in-depth — `claude/skills/journal-compose/SKILL.md` Step 0.6.** The same check runs
   against `$EJ` immediately after the existing divergence guard and before compose-worktree
   creation, following the same `exit 1` abort convention as the divergence guard and
   concurrency-lock check already in that step. This protects a manual/interactive
   `/journal-compose` invocation, which never goes through the retry wrapper at all.

**The check script itself stays pure I/O**, reading `git status --porcelain` output from stdin
rather than shelling out to git itself — the caller runs git, matching the established convention
(`_hookio.py`, `_journal_shards.py`) of keeping subprocess calls out of the tested unit so tests
don't need subprocess mocking. Its one exported predicate,
`has_uncommitted_target_date_changes(porcelain_output, date)`, checks whether any changed path
contains `/{date}_` (the stub/manifest shard naming convention is `YYYY-MM-DD_HHMMSS.stub.md` /
`.manifest.jsonl`), handling git's rename arrow (`OLD -> NEW`) by checking the destination path. A
second helper, `format_abort_message(date)`, is factored out purely so its output can be pinned as
ASCII/cp1252-safe by a dedicated test.

### Why git-status, not a generalized `worktree_session_is_live()`

- **Narrower gap, narrower signal.** The divergence guard already catches committed-but-unpushed
  stubs. The only remaining gap is uncommitted content, which is exactly what `git status
  --porcelain` reports — no need to infer session activity indirectly through transcript
  timestamps when the actual artifact (a dirty working tree) is directly observable.
- **One well-known path, not a worktree fan-out.** `worktree_session_is_live()` takes a worktree
  path because prune/reclaim operate per-worktree. There is no equivalent "the worktree that might
  write to this draft branch" — any project's worktree could. Checking the one shared
  `engineering-journal` checkout directly sidesteps needing to enumerate or guess at candidate
  worktrees system-wide.
- **Lower false-positive rate.** A system-wide transcript-liveness scan would flag any recently
  active Claude Code session anywhere, the large majority of which have nothing to do with the
  journal. Git status only flags the checkout that would actually receive the conflicting push.

### Why soft-fail-with-eventual-proceed, not a hard block

- **Reuses existing infrastructure.** `journal-compose-with-retry.sh` already retries 3× with
  5-minute delays on any non-zero exit. Treating a dirty check as an ordinary retryable failure
  needed no new blocking primitive.
- **Avoids wedging automation on an abandoned session.** A hard, unbounded block risks a session
  that started stub-writing but never committed (crashed, abandoned, forgot) permanently
  preventing that date's automated compose. Proceeding on the final attempt bounds the wait to the
  existing 15-minute retry budget and falls back to the already-working recovery runbook rather
  than requiring manual intervention every time.
- **Decouples from the unverified `claude -p` exit-code question.** Running the check directly in
  the wrapper's bash loop — rather than only inside the skill and hoping the abort propagates
  through `claude -p`'s own exit code — makes the automated path's retry behavior fully
  deterministic and independently testable, regardless of how Claude Code's non-interactive mode
  handles an in-session abort.

---

## Consequences

- The nightly automated compose no longer merges a draft branch while the shared
  `engineering-journal` checkout has uncommitted stub/manifest content for that date, for up to
  ~10 minutes (two retry delays) before proceeding anyway.
- A manual `/journal-compose <date>` invocation gets the same protection via Step 0.6, independent
  of whether it's run through the retry wrapper.
- **The liveness guard and the transient-failure retry share one fixed budget (review finding,
  PR #587).** A liveness-triggered skip consumes a retry attempt exactly like a genuine `claude -p`
  failure does. In the worst case (both non-final attempts skip for liveness), only the final
  attempt ever invokes `claude -p` — if *that* attempt then hits a transient API failure, there is
  no attempt left to absorb it, where before this guard existed the wrapper had all 3 tries
  available for transient issues alone. This is an accepted trade, not a bug: it only bites when a
  session is legitimately active on two consecutive attempts *and* the final `claude -p` call also
  hits a transient failure — already a narrow intersection — and the existing recovery runbook
  covers the resulting failure the same way it covers every other exhausted-retries case.
- No change to the divergence guard, the compose-worktree concurrency lock, or the today-guard
  (ADR-017) — this ADR adds a new, narrower check alongside them.
- **Testing.** New pure-helper test `claude/scripts/tests/test_check_journal_compose_liveness.py`
  (dev-env `## Testing` item 43) exercises `has_uncommitted_target_date_changes()` and
  `format_abort_message()` offline — no subprocess, no git, no filesystem.
  `journal-compose-with-retry.sh`'s bash changes are verified via the repo's script path-hygiene
  lint and shellcheck (items 5 and 7); no dedicated fixture test is added for the wrapper script
  itself, consistent with ADR-084's precedent for the same file (a small, non-branching-heavy
  change to a Task-Scheduler wrapper). `journal-compose/SKILL.md`'s Step 0.6 addition is markdown
  with no automated test, consistent with ADR-082/ADR-084's precedent for this same file.
- **Observability.** The check's stderr message (`format_abort_message`) is deliberately
  ASCII/cp1252-safe (pinned by a dedicated test), matching the convention established by other
  advisory-emitting scripts in this repo (`journal-shard-write-advisory.py`,
  `posttooluse-inert-advisory.py`) — the message may be printed from redirected-stdout contexts (a
  `tee`'d log file, a `2>&1` capture) where a non-ASCII character risks an encoding surprise. The
  wrapper script's existing `log()` calls remain the diagnostic surface for the retry loop; the new
  liveness-guard branch logs through the same function.
- **Security.** N/A — no new credentials, secrets, or auth surface; the check reads local git
  status only.
- **Resilience.** Directly improves resilience against the ADR-084-identified race, bounded by the
  existing retry budget rather than introducing an unbounded new failure mode.
- **Performance.** Negligible — one `git status --porcelain` call added to at most 2 of the
  wrapper's 3 attempts, and once to Step 0.6.
- **Data integrity.** N/A — no schema or migration surface; this is precisely a data-integrity
  *protection* for the journal's draft-branch merge sequencing, not a schema change.

---

## Alternatives rejected

- **Generalized cross-worktree session-liveness check** (system-wide, mirroring
  `worktree_session_is_live()` across every project worktree). Rejected — highest false-positive
  rate and most complex to build/maintain, for a signal (git status on the one shared checkout)
  that's already directly observable and unambiguous.
- **Hard block until clean, no retry budget.** Rejected — an abandoned or crashed session's
  uncommitted stub would wedge that date's automated compose indefinitely, trading a rare and
  already-recoverable race for a more frequent manual-intervention burden.
- **Defer entirely; rely on the recovery runbook.** Considered, since ADR-084 called the race
  "narrow under typical session timing" and a working recovery path already exists. Rejected in
  favor of the lightweight primary check above, once investigation showed the actual fix was small
  and reused existing retry infrastructure rather than requiring new blocking machinery.
- **Depend on Step 0.6's `exit 1` propagating through `claude -p`'s own exit code as the sole
  mechanism**, with no separate wrapper-script check. Rejected after confirming Claude Code's
  public documentation doesn't specify this behavior — building unattended nightly automation on an
  unverified assumption was judged worse than adding a few lines of deterministic bash that
  sidestep the question entirely.

---

## References

- `claude/scripts/check-journal-compose-liveness.py` — the shared check, both call sites
- `claude/scripts/journal-compose-with-retry.sh` — primary check, before each retry attempt
- `claude/skills/journal-compose/SKILL.md` — Step 0.6, defense-in-depth check
- `claude/scripts/tests/test_check_journal_compose_liveness.py`
- [dev-env#586](https://github.com/brownm09/dev-env/issues/586) — motivating issue, with the
  design-options writeup this ADR resolves
- [ADR-051](051-worktree-liveness-guard.md) — the `worktree_session_is_live()` pattern that
  motivated the original framing, and why it doesn't transfer directly
- [ADR-084](084-nightly-compose-targets-yesterday.md) — the fix that activated this race, and
  where it was first named
- [ADR-082](082-journal-compose-worktree-isolation.md) — Step 0.6's divergence guard and
  compose-worktree concurrency lock, alongside which this check is placed
- [ADR-017](017-journal-compose-today-guard.md) — the today-guard, unaffected by this change
