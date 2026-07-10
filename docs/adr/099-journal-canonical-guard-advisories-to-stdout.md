# ADR-099: Deliver journal-canonical-guard.py Advisories on stdout (Not stderr) on This Always-Exit-0 UserPromptSubmit Hook

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** hooks, UserPromptSubmit, journal-canonical-guard, exit-code, stdout, stderr, claude-facing, silent-failure, adr-091, adr-093, adr-098

---

## Context

`journal-canonical-guard.py` ([ADR-093](093-journal-canonical-hijack-guard.md)) detects the
engineering-journal canonical checkout sitting on the dev-env#630 hijack signature — detached
HEAD, or a stray `claude/<slug>` branch belonging to no live worktree — and restores it to
`main`. It always exits 0 (never blocks the prompt) and, prior to this change, routed every
warning (an unreadable worktree list, a squatting worktree blocking the return to `main`, a
dirty hijacked canonical, a failed auto-return checkout) to **stderr**. Only the successful
auto-return message used a plain `print()` to stdout.

This is the **identical defect** [ADR-098](098-dev-env-sync-advisories-to-stdout.md) fixed in
the sibling hook `dev-env-sync.py`, merged one day earlier by the same PR (#661) and sharing
the same `_worktree_topology.py`-based design. ADR-098's own Context section, investigating
[dev-env#694](https://github.com/brownm09/dev-env/issues/694), found this hook's parallel bug
"by inspection" while fixing `dev-env-sync.py` and deliberately scoped it out — filed as
[dev-env#699](https://github.com/brownm09/dev-env/issues/699) — rather than doubling that PR's
footprint for a file its motivating issue never named. This ADR is that follow-up.

Per the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the same
primary source ADR-091 first quoted for the mirror-image bug on a `Stop` hook, and ADR-098
quoted again for `dev-env-sync.py` — only `UserPromptSubmit`, `UserPromptExpansion`, and
`SessionStart` get their exit-0 **stdout** added to Claude's context; stderr is not surfaced
for those event types on exit 0. `journal-canonical-guard.py` is a `UserPromptSubmit` hook that
always exits 0, so every one of its warnings has been silently discarded since it merged
(PR #661, 2026-07-09) — for as long as whatever condition it describes (a hijacked branch the
canonical can't auto-return from because a squatter holds `main`, an unreadable worktree list,
a failed checkout) persists. Unlike `dev-env-sync.py`, no currently-active live incident forced
this discovery — the sibling bug was found by direct code inspection during the dev-env#694
investigation, not by a separately reproduced failure here.

## Decision

Route every advisory in `journal-canonical-guard.py` to **stdout**, keeping the hook's existing
**exit 0 always** contract unchanged:

- All four warning paths (worktree-list-unreadable, warn-squatter, warn-dirty,
  auto-return-checkout-failed) drop `file=sys.stderr` and print to stdout via a plain
  `print(...)`, matching the file's own pre-existing, proven-working success-message
  convention — the exact same mechanical change ADR-098 made in `dev-env-sync.py`.
- **No emoji-to-`WARNING:` text substitution is needed here.** Unlike `dev-env-sync.py` (which
  used a non-cp1252-safe `⚠️` prefix ADR-098 had to replace), every warning in this file already
  reads `[journal-canonical-guard] WARNING: ...` in plain ASCII — confirmed by scanning the
  file's full text for any character outside the ASCII range; the only non-ASCII characters
  present are em dashes (`—`, U+2014) inside docstrings/comments, never inside a `print(...)`
  argument, and U+2014 is itself cp1252-representable (0x97) regardless. So this fix is purely
  the stream change, exactly as dev-env#699 anticipated.
- **No SHA/commit-behind diagnostics are added**, unlike `dev-env-sync.py`'s parallel fix. That
  enrichment targeted dev-env#694's specific unresolved ambiguity (an unreproduced "Pulled 0
  commits" report needing a self-diagnosing commit-count comparison). This hook's warnings are
  about worktree/branch **topology** (a hijacked branch, a squatting worktree), not commit
  counts — there is no analogous ambiguity here to resolve, so no new diagnostic fields were
  invented to manufacture parity with ADR-098's scope.
- `main()`'s call site already wraps in `try`/`except Exception: sys.exit(0)` (added in PR #661,
  the same PR that introduced this file) — unchanged by this fix, and itself the precedent
  ADR-098 cites for adding the equivalent wrapper to `dev-env-sync.py`.

### Testing: a deliberate, narrow departure from ADR-093's "no dedicated test file" precedent

ADR-093 explicitly decided against a dedicated test file for this hook's orchestration,
reasoning that it has "zero local pure logic (everything delegates to
`_worktree_topology.py`)" and that the topology-decision correctness is already covered by
`test_worktree_topology.py`. That reasoning is **unaffected** by this change — this fix adds no
new pure logic to `journal-canonical-guard.py` either (no formatter helpers, unlike ADR-098),
so the topology-decision testing story is unchanged.

But this fix targets a **different, orthogonal axis** ADR-093 never evaluated: not "is the
topology decision correct," but "which stream does the resulting message reach." A new,
narrowly-scoped end-to-end test file, `claude/scripts/tests/test_journal_canonical_guard.py`,
drives the real script via subprocess against a disposable throwaway git repo (using the
`JOURNAL_CANONICAL_GUARD_REPO_PATH` test seam PR #661 already built in, previously unused by
any test) and asserts stdout/stderr directly — the only way to actually prove this class of bug
is fixed rather than merely asserted fixed. Written test-first: confirmed to fail on exactly
the two warning paths this fix touches (warn-dirty, warn-squatter) against the pre-fix code,
and to pass against the fixed code. Two of the four warning paths (worktree-list-unreadable,
auto-return-checkout-failed) are deliberately not exercised end-to-end — matching this repo's
established convention for hard-to-construct git-failure-injection paths (dev-env `## Testing`
items 22/26/30) — see the new test file's own docstring for why each is fragile to construct
reliably.

## Consequences

- A hijacked-and-dirty canonical, or one blocked from auto-returning by a squatting worktree, is
  now visible to Claude — and therefore to the user — on the very next prompt, instead of
  silently repeating every turn indefinitely (the same class of fix ADR-098 delivered for
  `dev-env-sync.py`).
- `journal-canonical-guard.py` and `dev-env-sync.py` now share an identical, consistent
  stdout-only advisory convention — a future reader inspecting one no longer sees the other as
  an unexplained exception.
- The new end-to-end test file is the first departure from ADR-093's "no dedicated test file"
  stance for this hook, narrowly scoped to the stream-routing axis only; the topology-decision
  testing story ADR-093 established is untouched.
- No new subprocess calls added (unlike ADR-098's `git rev-list --count` additions) — this fix
  has no performance cost beyond the pre-existing behavior.

## Alternatives considered

- **Keep the messages on stderr, add an exit-2 blocking path instead (mirroring ADR-091's fix
  direction).** Rejected for the same reason ADR-098 rejected it: every one of this hook's
  conditions is advisory (a hijacked-but-dirty canonical, a squatting worktree) — pre-existing
  states the user needs to *know about*, not states that should erase their in-flight prompt.
- **Add SHA/commit-behind diagnostics to match ADR-098's enrichment.** Rejected — that
  enrichment answered a specific unresolved ambiguity in dev-env#694 (an unreproduced commit
  count) that has no analogue in this hook's topology-based warnings. Adding it here would be
  manufacturing parity with ADR-098's scope rather than solving a real problem, contrary to
  dev-env#699's own explicit scope note.
- **Skip the new test file entirely, matching ADR-093's precedent literally.** Considered,
  given ADR-093's explicit "no dedicated test file" decision for this exact file. Rejected
  because that decision was reasoned specifically about topology-decision correctness (already
  covered elsewhere) — it did not evaluate, and does not preclude, testing the orthogonal
  stream-routing concern this fix addresses. A manual one-time verification (ADR-093's own
  precedent for its scope) would prove the fix works *now* but not guard against a future
  regression (e.g., a fifth warning site added later that forgets the stdout convention) — the
  exact class of silent failure this whole ADR chain (091, 098, 099) exists to close.
- **Amend ADR-093 instead of a new ADR.** Rejected for the same reason ADR-098 chose a new ADR
  over amending ADR-091: this is a distinct fix (stream routing) from ADR-093's original
  subject (hijack detection and correction design), sharing only the failure *class*. A
  separate, cross-referenced ADR is more discoverable and keeps the growing 091 → 098 → 099
  chain for this exact failure class legible as its own thread.

## References

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the exit-code /
  stdout-vs-stderr / per-event-type semantics this ADR, ADR-091, and ADR-098 all rely on.
- [ADR-091](091-journal-stop-check-archive-reminder-blocking.md) — the first occurrence of this
  failure class (mirror-image bug on a `Stop` hook) and the primary-source quote.
- [ADR-098](098-dev-env-sync-advisories-to-stdout.md) — the sibling fix this ADR mirrors,
  applied to `dev-env-sync.py`; its own Scope section named this hook as an identical,
  deliberately-deferred defect.
- [ADR-093](093-journal-canonical-hijack-guard.md) — this hook's own design ADR; its "no
  dedicated test file" testing rationale is explicitly addressed (not overridden) above.
- [dev-env#699](https://github.com/brownm09/dev-env/issues/699) — the issue this ADR closes.
- [dev-env#694](https://github.com/brownm09/dev-env/issues/694) — the investigation that
  surfaced this hook's identical defect by inspection while fixing its sibling.
- [PR #701](https://github.com/brownm09/dev-env/pull/701) — `dev-env-sync.py`'s stdout fix
  (ADR-098), open concurrently with this change; independent (different files, no overlap).
