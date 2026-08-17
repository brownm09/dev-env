# ADR-022 — Test Coverage Gate: Require Declaration of New Testable Behavior Before PR

**Date:** 2026-05-16
**Status:** Accepted

---

## Context

The global "Test before PR" rule in `claude/CLAUDE.md` required that *existing* tests pass before opening a PR. It asked no question about whether the change introduced *new* testable behavior that lacked tests entirely. This meant the gate could be satisfied even when a new API endpoint or frontend feature shipped with zero test coverage.

The immediate incident was merickvaughn/lifting-logbook PR #255 (programs management page), which merged a substantial frontend feature with no Playwright or unit tests. The root cause, documented in lifting-logbook issue #265, was that no rule prompted the author or Claude to ask the coverage question.

Per-project CLAUDE.md files (e.g., lifting-logbook) can define change-type-specific coverage requirements (API endpoints → E2E test, bug fix → regression test, etc.), but they have no effect if the global gate never asks the question.

## Decision

Extend the "Test before PR" bullet in `claude/CLAUDE.md` with an explicit coverage gate question:

> Also ask whether the change introduces testable behavior not covered by existing tests. If yes, add tests before creating the PR, or explicitly document in the PR body why they are deferred.

This turns the existing gate from a *pass/fail check on existing tests* into a two-part check:
1. Existing tests pass.
2. New testable behavior is either covered or explicitly deferred with a written rationale.

The deferred-with-rationale escape hatch is intentional — it allows shipping infrastructure stubs or partial implementations under a tracked issue without forcing incomplete test coverage to become a blocker.

## Consequences

- Claude and human contributors must answer the coverage question before every `gh pr create`.
- PRs without tests for new behavior must document the deferral in the PR body — making the gap visible in the review record.
- Per-project coverage tables (like the one added to lifting-logbook CLAUDE.md in the same session) give the coverage gate concrete criteria to apply rather than relying on judgment alone.
- The `/review` skill gains an explicit Step 2d that inspects the diff for new testable behavior and flags as a blocking finding when neither tests nor a deferral rationale appear. This is LLM-judgment-based, not a deterministic check — it cannot replace author discipline, but it surfaces the gap in the review record.

## References

- merickvaughn/lifting-logbook issue #265 — full problem statement and per-change-type coverage requirements table
- merickvaughn/lifting-logbook PR #255 — the triggering incident (programs management page, no tests)
- [ADR-011](011-adr-warrant-check.md) — warrant check protocol that surfaced this ADR requirement
