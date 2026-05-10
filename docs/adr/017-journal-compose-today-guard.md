# ADR 017 — Journal-Compose Today-Date Guard (Branch-Detection Path Only)

**Date:** 2026-05-10  
**Status:** Accepted

---

## Context

`/journal-compose` resolves its target date from two sources: an explicit `$ARGUMENTS` value
(`/journal-compose YYYY-MM-DD`) or auto-detection from the current branch name (`draft/YYYY-MM-DD`).

When no argument is passed and `draft/<today>` is checked out, the skill silently resolves
today's date and proceeds to merge the draft PR. Any stub pushed later that day triggers the
pre-push hook (which blocks pushes to branches with merged PRs) and Claude emits a repeated
"push hook-blocked" message. The root cause was that the skill had no guard on the resolved date.

Two design options were considered for the guard scope:

1. **Block both paths** — refuse today's date whether from `$ARGUMENTS` or branch detection.
2. **Block branch-detection path only** — allow an explicit argument to override the guard.

Option 1 was implemented first, then revised after review. Blocking an explicit argument is
incorrect: a user who passes `/journal-compose 2026-05-10` is making a deliberate choice
(e.g., they have written all stubs for the day and want to compose now). The guard should
protect against the *accidental* case (branch auto-detection), not the *intentional* case.

---

## Decision

The today-date guard applies **only when the date was auto-detected from the branch name**.
If `$ARGUMENTS` is provided and matches today's date, the guard is skipped — the explicit date
is treated as a deliberate end-of-day composition request.

When the guard fires (branch-detection path, date == today), the skill stops immediately
before stub discovery, manifest reading, or lock acquisition, and responds:

> "`/journal-compose` targets completed days only. `draft/YYYY-MM-DD` is **today's** branch —
> stubs may still be written during later sessions today.
> Run `/journal-compose` at end of day, or pass an explicit past date:
> `/journal-compose YYYY-MM-DD`."

---

## Consequences

- Accidental mid-day composition via branch auto-detection is blocked cleanly with a clear
  recovery message.
- Intentional end-of-day composition (explicit date argument) is unaffected.
- The pre-push hook's "push hook-blocked" message will no longer recur from this cause.
- Users who want to compose today's journal early (all stubs written) pass an explicit date
  argument rather than relying on branch detection.

---

## References

- [dev-env#204](https://github.com/brownm09/dev-env/issues/204) — issue tracking this bug
- [dev-env#205](https://github.com/brownm09/dev-env/pull/205) — PR implementing the guard
- `claude/skills/journal-compose/SKILL.md` — Step 1, "Guard" block
- ADR 002 — Journal-Compose Session Isolation (related: the other hard stop in Step 0)
- `claude/hooks/pre-push` — the hook that blocks pushes to merged draft branches
