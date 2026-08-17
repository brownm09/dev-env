# ADR-089 — Privilege-Restricted Test Defaults

**Date:** 2026-07-02
**Status:** Accepted
**Tags:** testing, quality, security, isolation, global-rule, workflow

---

## Context

Root-caused a production bug in lifting-logbook ([issue #644](https://github.com/merickvaughn/lifting-logbook/issues/644) / [PR #645](https://github.com/merickvaughn/lifting-logbook/pull/645)): a NestJS global interceptor (`RlsInterceptor`) silently never established Postgres Row-Level Security context for any request, for 3+ weeks after the RLS migration landed, because it constructor-injected an `@Optional()` dependency that resolved to `null` due to a DI-instantiation-order race between globally-bound enhancers and same-module factory providers.

The bug was invisible everywhere except real production traffic:

- **Local dev** connects as the Postgres superuser (`docker-compose.yml`'s `POSTGRES_USER: postgres`), which bypasses RLS by Postgres design — enforcement literally does not apply to superuser/`BYPASSRLS` roles, regardless of whether the session GUC is set correctly.
- **Every automated DB E2E test but one** connects the same way (the Testcontainers bootstrap superuser exposed by `jest.global-setup.js`).
- **The one test suite that specifically exercises the restricted role** (`rls.db.e2e.spec.ts`) does so two ways, neither of which could catch this: one block calls Prisma directly and manually sets the session GUC itself, bypassing the real interceptor and DI container entirely; the other manually constructs the interceptor (`new RlsInterceptor(cls, reflector, prisma)`), handing it an already-resolved dependency directly rather than letting NestJS's real container resolve it — sidestepping the exact instantiation-order mechanism where the bug lives.
- **Staging** is provisioned identically to production (the same Terraform resource creates the restricted role for both environments) and had the identical live bug, but its automated smoke tests are read-only (page loads, redirects, an auth check) — nothing ever performs a write, so nothing could surface a bug that only manifests on write (or on reading genuinely-existing data).

The lesson generalizes well past NestJS or Postgres RLS: whenever a project enforces a security or isolation guarantee via a specific runtime identity distinct from the default/admin one — a restricted DB role, a scoped service account, a sandboxed permission set, a tenant-scoped API credential — a test or dev environment that defaults to the *privileged* identity is structurally blind to an entire category of enforcement bugs, no matter how much other coverage exists. The existing Pass 3 risk-dimension audit's Security dimension ("authz, input validation, secret handling, sensitive-data/PII exposure") had no explicit checkpoint for this.

A related but distinct point surfaced by the same incident: the failure mode was *silent*. Postgres's fail-closed RLS behavior makes "the GUC is unset" indistinguishable from "this row genuinely doesn't exist yet" from the caller's perspective. A security control that fails closed (the correct default) but also fails *silently* — no error, no log line, no metric — is nearly undetectable in normal operation, because the symptom looks identical to correct behavior on a new/empty resource.

---

## Decision

Add a **Privilege-restricted test defaults** subsection to `## Code Quality` in `claude/CLAUDE.md`, positioned alongside the existing Suppression/Test-integrity/Pre-existing-failure policies (same "checkable rule + rationale incident" structure):

> **Privilege-restricted test defaults.**
>
> When a project enforces a security or isolation guarantee via a specific runtime identity distinct from the default/admin one (a restricted DB role, a scoped service account, a sandboxed permission set, a tenant-scoped credential), test suites and local dev must default to that restricted identity — the privileged/bypass identity is the opt-in exception, not the default. A test that passes under an admin/superuser/root connection proves nothing about whether the boundary it's supposed to exercise actually enforces anything.
>
> Before adding coverage for a permission or isolation boundary, confirm the test actually runs under the identity the boundary is scoped to — not just that the code path is exercised. A test suite (or manually-constructed test harness) that resolves the dependency graph by hand rather than through the real framework/DI machinery is equally blind to bugs that live specifically in that machinery's resolution order or timing.
>
> **Why:** lifting-logbook's RLS enforcement was inert in production for 3+ weeks (issue #644) because every test and local-dev environment ran as Postgres superuser, which bypasses Row-Level Security by design — the one enforcement bug that existed was invisible everywhere except real user traffic, and cost a multi-hour investigation to find.
>
> **How to apply:** this extends the Plan-then-optimize → Pass 3 risk-dimension audit's Security dimension. For any change touching a permission, authorization, or isolation boundary, explicitly confirm test coverage runs under the restricted identity, not just the admin/superuser one — state this as part of the Security dimension's audit line rather than leaving it implicit.

Also extend the Pass 3 Security dimension's one-line description in `## Context & Token Efficiency` → Plan-then-optimize (currently "authz, input validation, secret handling, sensitive-data/PII exposure") to add "confirm permission/isolation tests run under the restricted identity, not the admin one" as an explicit sub-check, so the rule surfaces at the point where Security is already being reasoned about rather than living only in a Code Quality subsection someone has to remember to consult.

No new hook or script — this is a behavioral rule for Claude to apply during the Security risk-dimension audit and during test-authoring, not something mechanically greppable the way the suppression/test-integrity checks are (there's no generic static signature for "this test used the wrong credential").

---

## Rejected Alternatives

**A lint rule or grep pattern flagging "superuser"/"admin"/"root" strings in test setup.** Appealing for consistency with the suppression/test-integrity policies' grep-based enforcement, but the credential-naming convention is different in every project and every kind of isolation boundary (DB roles, IAM policies, sandboxed permissions, tenant scoping) — a pattern specific enough to avoid false positives would be specific to one project's naming, not a portable rule. Rejected in favor of a behavioral audit checkpoint, matching how the existing Security dimension is already handled (judgment-based, not pattern-matched).

**Leave it as a memory-only note.** Violates the global CLAUDE.md's own Durable Preferences & Memory rule directly — this is exactly the class of durable, cross-session, cross-project rule that must not live only in memory. Also the entire reason this ADR exists.

**Scope the rule narrowly to "database roles" only.** Would technically cover the motivating incident but misses the generalization the user explicitly asked for ("across any and all of my projects") — IAM policy boundaries, sandboxed execution, and tenant-scoped credentials share the identical failure shape and deserve the same rule.

**Require 100% of tests to run under the restricted identity (ban the privileged connection from tests entirely).** Too strong — seeding, fixture cleanup, and cross-cutting assertions legitimately need a privileged connection in many designs (see `rls.db.e2e.spec.ts`'s own pattern: an owner client for seed/cleanup, an app-role client for enforcement assertions). The rule targets *defaults*, not an absolute ban — the privileged connection remains available as a named, deliberate opt-in.

---

## Consequences

**Positive:**
- Closes a real, demonstrated blind spot: a bug that produced zero test failures despite 34 passing test suites and 420 passing tests in the affected repo.
- Generalizes cleanly to any project with a privilege-separated runtime identity, not just lifting-logbook's Postgres RLS setup — the rule is stated in terms of the *pattern* (restricted vs. privileged identity), not the specific mechanism.
- Reinforces an existing audit checkpoint (Pass 3 Security dimension) rather than inventing a new one, so it's more likely to actually get checked rather than becoming another rule that decays from disuse.

**Negative:**
- Not mechanically enforceable the way the suppression/test-integrity grep checks are — relies on Claude actually applying the audit line during the Security dimension review, which is a judgment call, not a script. If this proves insufficient in practice, a project-specific lint/static-check could be added per-project (see Rejected Alternatives), but that's deferred until a concrete false-negative recurrence justifies the cost.
- Retrofitting existing test suites to default to a restricted identity is real, non-trivial work per project; this ADR only mandates the rule going forward, it does not retroactively fix every project's test defaults. (lifting-logbook's own retrofit was tracked in [lifting-logbook#646](https://github.com/merickvaughn/lifting-logbook/issues/646) and has since merged via [PR #658](https://github.com/merickvaughn/lifting-logbook/pull/658) — cited here as a worked example, not outstanding work.)

---

## References

- [ADR-026 — Suppression Policy](026-suppression-policy.md) — structural template (checkable rule + rationale incident + Code Quality placement)
- [ADR-029 — Test Integrity Policy](029-test-integrity-policy.md) — sibling policy on tests proving what they claim to prove
- [ADR-042 — Plan Risk-Dimension Audit and Observability Section](042-plan-risk-dimension-audit-and-observability-section.md) — defines the Pass 3 Security dimension this ADR extends
- [PostgreSQL docs — Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) — primary source: RLS enforcement does not apply to superuser or `BYPASSRLS` roles, the mechanism at the root of the motivating incident
- [NestJS docs — Custom providers](https://docs.nestjs.com/fundamentals/custom-providers) — `useFactory` / `@Optional()` injection semantics referenced by the motivating incident
- [lifting-logbook#644](https://github.com/merickvaughn/lifting-logbook/issues/644) / [lifting-logbook#645](https://github.com/merickvaughn/lifting-logbook/pull/645) — the motivating incident and its fix
- [dev-env#540](https://github.com/brownm09/dev-env/issues/540) — the issue that prompted this ADR
