# ADR 017 — Journal-Compose Today-Date Guard (All Paths; `--force` Opt-Out)

**Date:** 2026-05-10  
**Status:** Accepted (updated 2026-05-10 — extended guard scope and pre-push hook date exception)

---

## Context

`/journal-compose` resolves its target date from two sources: an explicit `$ARGUMENTS` value
(`/journal-compose YYYY-MM-DD`) or auto-detection from the current branch name (`draft/YYYY-MM-DD`).

**Original problem (PR #205):** When no argument is passed and `draft/<today>` is checked out,
the skill silently resolves today's date and proceeds to merge the draft PR mid-day. Any stub
pushed later that day triggers the pre-push hook (which blocks pushes to branches with merged
PRs) and Claude emits a repeated "push hook-blocked" message. PR #205 added a guard on the
auto-detection path only.

**Remaining gap:** An explicit invocation like `/journal-compose 2026-05-10` on May 10 bypassed
the guard entirely. The original ADR 017 rationale was that an explicit argument is "a deliberate
choice." In practice, the distinction between "accidental" and "deliberate" cannot be inferred
from the argument alone — it requires explicit user intent.

**Related problem:** After an accidental mid-day compose squash-merges `draft/YYYY-MM-DD`, the
pre-push hook blocked all further same-day pushes to that branch — including legitimate recovery
pushes. The hook made no distinction between same-day branches (still live) and prior-day
branches (genuinely stale).

---

## Decision

### 1. Journal-compose guard — all date inputs, `--force` opt-out

The today-date guard now applies to **all** date inputs — both auto-detected and explicitly
passed via `$ARGUMENTS`. Passing today's date explicitly no longer bypasses the guard.

To compose today's journal deliberately (all stubs written, end of day), the caller must pass
`--force`:

- `/journal-compose --force` — auto-detects today's branch, proceeds
- `/journal-compose 2026-05-10 --force` — explicit date with force, proceeds
- `/journal-compose 2026-05-10` on May 10 — **blocked** (no `--force`)
- `/journal-compose 2026-05-09` — always proceeds (prior day, no flag needed)

When the guard fires (today's date, no `--force`), the skill stops immediately before stub
discovery, manifest reading, or lock acquisition, and responds:

> "`/journal-compose` targets completed days only. `draft/YYYY-MM-DD` is **today's** branch —
> stubs may still be written during later sessions today.  
> To compose today's journal intentionally (all stubs written, end of day):
> `/journal-compose --force`"

The `daily-journal-compose` nightly routine is unaffected: it runs at midnight UTC and always
passes yesterday's local calendar date — never today's — so the guard never fires for it.

### 2. Pre-push hook — skip merged-PR check for same-day branches

When the branch name is `draft/YYYY-MM-DD` and that date matches today's local date, the
merged-PR check is skipped and the push is allowed. Prior-day branches still hit the check.

Rationale: a same-day branch may have an accidental merged PR (mid-day compose kerfuffle) but
recovery pushes to it are still legitimate on the same calendar day. Stale-branch noise only
becomes a problem when a branch is pushed on a day after it was merged — the session that
resurrected it is long gone and the orphaned commits are unattended.

---

## Consequences

- Accidental mid-day composition is blocked on all invocation paths.
- Intentional end-of-day composition requires `--force` — makes deliberate intent explicit.
- The nightly routine requires no changes.
- Same-day recovery pushes after an accidental compose are no longer hook-blocked.
- CLAUDE.md end-of-day instruction updated: `Run /journal-compose --force`.

---

## References

- [dev-env#204](https://github.com/brownm09/dev-env/issues/204) — original bug
- [dev-env#205](https://github.com/brownm09/dev-env/pull/205) — initial guard (branch-detection path only)
- [dev-env#213](https://github.com/brownm09/dev-env/issues/213) — issue tracking this extension
- [SKILL.md](../../claude/skills/journal-compose/SKILL.md) — Step 1, "Guard" block
- [pre-push](../../claude/hooks/pre-push) — merged-branch push guard with same-day exception
- [ADR 002](002-journal-compose-session-isolation.md) — Journal-Compose Session Isolation (related: the other hard stop in Step 0)
