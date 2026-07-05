# ADR-084: Nightly Journal-Compose Targets Yesterday's Local Date, Not `--force`

**Date:** 2026-07-05
**Status:** Accepted
**Tags:** journal, composition, routines, today-guard, scheduling, correction, ADR-017, ADR-082

---

## Context

Both automated nightly-compose entry points resolved their target date to **today** and never
passed `--force`:

- `claude/routines/daily-journal-compose/SKILL.md` computed `DATE=$(date -u +%Y-%m-%d)` — today,
  in **UTC** — and invoked `/journal-compose ${DATE}` with no `--force`.
- `claude/scripts/journal-compose-with-retry.sh` (the Windows Task Scheduler wrapper documented in
  `docs/REFERENCE.md` under "daily-journal-compose") invoked a bare
  `claude -p "Run /journal-compose. Merge the result. Create a stub for today."` — no date
  argument at all, falling through to Step 0.6's implicit branch-detection (which also resolves to
  today), and no `--force` either.

`/journal-compose` Step 0.6's today-guard ([ADR-017](017-journal-compose-today-guard.md)) refuses
to compose a same-day branch unless `--force` is passed. Since both entry points resolve to today
and neither passes `--force`, the guard fires on every run — the automated ~7am compose has
almost certainly never successfully composed a journal. `docs/REFERENCE.md` confirms the
routine's actual schedule is `0 7 * * *` (7:09am **local**, with scheduler jitter).

**This is a drift, not a new problem.** ADR-017 already asserts the intended design:

> The `daily-journal-compose` nightly routine is unaffected: it runs at midnight UTC and always
> passes yesterday's local calendar date — never today's — so the guard never fires for it.

The routine as shipped does neither: it targets today, and does so via UTC rather than local time
(a second, independent bug — the stub-filename and branch-naming convention in `claude/CLAUDE.md`
is local time, so a UTC-computed date can name the wrong branch near local midnight, in either
direction depending on the user's UTC offset). Both defects were found while implementing
[PR #563](https://github.com/brownm09/dev-env/pull/563) (ADR-082, journal-compose worktree
isolation) but deliberately deferred there — see ADR-082 → "Judgment calls → Routine's
`DATE`/`--force` mismatch is explicitly out of scope" — since fixing them requires a product
decision, not a mechanical one: should the automated run target yesterday, or always pass
`--force`?

---

## Decision

**Both automated entry points target yesterday's local calendar date, computed with `date -d
yesterday +%Y-%m-%d` (no `-u`). Neither ever passes `--force`.**

- `claude/routines/daily-journal-compose/SKILL.md` Step 1: `DATE=$(date -d yesterday +%Y-%m-%d)`,
  replacing `DATE=$(date -u +%Y-%m-%d)`. Wording throughout the routine ("today's stubs", "if a
  canonical document already exists for today") is updated to "yesterday" / "that date" to match.
- `claude/scripts/journal-compose-with-retry.sh`: adds `DATE=$(date -d yesterday +%Y-%m-%d)` and
  passes it explicitly in the prompt (`Run /journal-compose ${DATE}. ...`), rather than relying on
  Step 0.6's implicit branch-detection fallback (which, with more than one draft branch present —
  e.g., a new session already started before 7am local — would otherwise ask the user which to
  compose, a question a non-interactive `claude -p` invocation can never answer).
- `LOG_FILE`'s date and the `log()` function's timestamp in the retry wrapper are **left on `date
  -u`, unchanged** — that is an internal operational artifact (log file naming/timestamping), which
  `claude/CLAUDE.md` explicitly carves out as UTC's reserved use. Only the *compose target date* —
  a business-meaning date that must agree with stub filenames and branch names — moves to local
  time.

This restores ADR-017's original intent rather than introducing a new one, and requires no change
to `/journal-compose` itself or to its today-guard.

### Why yesterday, not `--force`

`--force` was the other candidate: pass it unconditionally from both automated entry points,
permanently overriding the guard for every automated run. Rejected because:

- **It defeats the guard for the one caller most likely to need it.** The today-guard exists to
  stop composition of a day that might still receive stubs. An automated job — with no human in
  the loop to notice a still-accumulating draft — is exactly the caller that benefits most from
  that check remaining live, not the one that should permanently bypass it.
- **"Yesterday" needs no override at all.** At ~7am local, the previous local calendar day is
  genuinely complete under the ordinary assumption that sessions don't run through the night into
  the next morning. Targeting a date that structurally satisfies the guard is strictly better than
  targeting today and silencing the check that would have caught a violated assumption.
- **It matches the design ADR-017 already committed to.** Re-deriving "yesterday, local" from
  scratch would have reopened a question this repo already answered; the actual defect was the
  implementation drifting from that answer, not the answer being wrong.

---

## Consequences

- The automated ~7am nightly compose can now actually succeed unattended — previously it
  silently no-op'd (guard-blocked) on every run, and only the manual end-of-day `/journal-compose
  --force` habit ever produced a composed journal.
- Fixes the secondary UTC/local mismatch as a side effect: the routine's date now always agrees
  with the local-time convention stub filenames and branch names already use.
- `claude/scripts/journal-compose-with-retry.sh` no longer depends on Step 0.6's implicit
  branch-detection fallback, which also removes the latent risk of that fallback finding more than
  one candidate `draft/YYYY-MM-DD` branch and trying to ask a question a non-interactive `claude -p`
  session can never answer.
- No change to `/journal-compose`, its today-guard, or the pre-push hook's same-day exception
  (ADR-017) — this ADR is entirely about the two callers' input, not the guard itself.
- **Testing.** No `.py` files change. `journal-compose-with-retry.sh` is a `claude/scripts/*.sh`
  file — verified via the repo's script path-hygiene lint and shellcheck (dev-env `## Testing`
  items 5 and 7); it introduces no `$HOME`-rooted path and no new `node` call. The routine
  `SKILL.md` is markdown with no automated test, consistent with ADR-082's precedent ("No `.py`/`.sh`
  files change — this is a skill-markdown ... change. Verification is a full occurrence-grep ...
  a manual step-consistency walkthrough, and the next real end-of-day compose as the actual
  integration test"); the same approach applies here. No dedicated fixture test exists for
  `journal-compose-with-retry.sh` (unlike `merge-stale-pr.sh`'s `test-merge-stale-pr.sh`) — deferred
  as disproportionate to a four-line change to a Task-Scheduler wrapper with no branching logic of
  its own.
- **Observability.** N/A in the hook/script-log sense — see dev-env's `## Observability` section.
  The retry wrapper's own `log()` calls (unchanged) remain the diagnostic surface for its 3-attempt
  retry loop.
- **Security.** N/A — no new credentials, secrets, or auth surface.
- **Resilience.** Strictly improves resilience: the automated path now completes instead of
  guard-blocking every time; the retry wrapper's explicit date also removes its dependency on an
  ambiguous multi-branch scan that a non-interactive session cannot resolve interactively.
- **Performance.** No measurable change — same number of `git`/`gh` calls, one date computation
  changes from `date -u` to `date -d yesterday`.
- **Data integrity.** N/A — no schema or migration surface.

---

## Alternatives rejected

- **Always pass `--force` from both automated entry points.** Rejected — see "Why yesterday, not
  `--force`" above; permanently overriding the guard for the automated caller defeats its purpose
  for the one caller that most needs it.
- **Change the routine's `schedule` to run at true UTC midnight** (matching ADR-017's literal
  wording) instead of fixing the date computation. Rejected — the schedule is documented and
  confirmed to run at ~7am **local** (`docs/REFERENCE.md`), which is itself a reasonable time for a
  nightly job; the actual defect is the date computation disagreeing with that schedule, not the
  schedule itself. Changing scheduling semantics was also not requested and is a separate concern
  from date resolution.
- **Leave `journal-compose-with-retry.sh`'s implicit (no-argument) invocation as-is** and rely
  solely on `/journal-compose` Step 0.6's remote branch-scan to resolve the date. Rejected — that
  scan can find more than one candidate `draft/YYYY-MM-DD` branch (e.g., a new local-time day's
  first stub already pushed before the routine's ~7am fire), in which case Step 0.6 lists
  candidates and asks the user — a question a non-interactive `claude -p` session has no channel to
  answer. An explicit date argument removes the ambiguity entirely.

---

## References

- `claude/routines/daily-journal-compose/SKILL.md` — Step 1
- `claude/scripts/journal-compose-with-retry.sh`
- [dev-env#577](https://github.com/brownm09/dev-env/issues/577) — motivating issue
- [ADR-017](017-journal-compose-today-guard.md) — the today-guard; already stated the "yesterday,
  local" intent this ADR restores
- [ADR-082](082-journal-compose-worktree-isolation.md) — where this defect was discovered and
  deferred ("Judgment calls → Routine's `DATE`/`--force` mismatch is explicitly out of scope")
- [dev-env#467](https://github.com/brownm09/dev-env/issues/467) / [PR #563](https://github.com/brownm09/dev-env/pull/563) — the worktree-isolation work during which this was found
- `docs/REFERENCE.md` → Routines → `daily-journal-compose` — confirms the actual `0 7 * * *`
  schedule runs at ~7am local, not UTC midnight
