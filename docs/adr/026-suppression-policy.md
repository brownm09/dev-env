# ADR-026 — Suppression Policy: No Silent Workarounds for Type and Lint Errors

**Date:** 2026-05-24
**Status:** Accepted
**Tags:** code-quality, typescript, eslint, workflow, suppression, pre-pr

---

## Context

PR [lifting-logbook#338](https://github.com/brownm09/lifting-logbook/pull/338) added six TypeScript suppressions (`!` non-null assertions, `?? null` coercions) to `apps/api/prisma/seed.ts` to satisfy the compiler under `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`. The suppressions silenced the errors without changing the underlying code structure. The existing "Test before PR" rule only required that tests pass — it did not distinguish between tests passing because the code is correct and tests passing because suppressions were added.

Left unaddressed, suppressions accumulate and normalize. Each one makes the next feel more acceptable, eroding the value of strict TypeScript and ESLint settings over time. The pre-PR checklist had no mechanism to surface new suppressions before a PR was opened.

---

## Decision

Add a `## Code Quality` section to `claude/CLAUDE.md` with three binding rules:

1. **No suppression without justification.** Every suppression that lands in a PR must be accompanied by a PR-body note naming the specific lines and the invariant the suppression relies on. "The compiler is wrong" is not a justification; "index is always in bounds because the loop bounds equal the tuple length" is.

2. **Pre-existing errors must be filed, not silenced.** Before adding a suppression, determine whether the error predates the current branch by running the test suite on the base. If the error exists without the current branch's changes, file a GitHub issue (or batch it into an existing one) and leave the error unmodified. Only suppressions for errors *introduced by the current branch* are permissible — and only with Rule 1 justification.

3. **Pre-PR suppression grep (required before `gh pr create`).** Run:
   ```bash
   git diff origin/main -- . | grep -E '(ts-ignore|ts-expect-error|eslint-disable|![.[]|!\s*[;,)]|\?\? null)'
   ```
   Any match must map to a Rule 1 justification note in the PR body, or the suppression must be replaced with a proper fix. A PR that adds suppressions with no PR-body justification is not mergeable.

A one-line cross-reference is also appended to the "Test before PR" bullet in `## Git Workflow` so the suppression check appears at the same decision point.

A *suppression* is defined as: `!` (non-null assertion), `?? null` to coerce away `undefined`, `// @ts-ignore`, `// @ts-expect-error`, `eslint-disable` (line or block), or an explicit type cast whose purpose is to silence an error rather than to perform a legitimate narrowing.

---

## Consequences

**Positive:**
- Suppressions become visible artifacts that require acknowledgment before merge.
- Pre-existing errors can no longer accumulate silently across PRs — they must be tracked in issues.
- The grep check takes under 5 seconds and runs at the same point as the existing test command.
- The grep pattern covers end-of-expression assertions (`!;`, `!,`, `!)`) and chained access (`!.`, `![`). It does not catch every possible `!` form; a manual scan of added `!` occurrences in the diff is still required to confirm completeness.

**Negative:**
- Every suppression, even clearly safe ones (e.g., a non-null assertion on a loop-bounded index), requires a PR-body note. This is a deliberate friction cost.
- Identifying whether an error is pre-existing requires running the test suite twice (once with and once without the branch changes). For large repos this adds time; the check is behaviorally enforced rather than automated.

---

## References

- [TypeScript `noUncheckedIndexedAccess` — TypeScript 4.1 release notes](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-4-1.html#checked-indexed-accesses---nouncheckedindexedaccess)
- [ESLint `eslint-disable` comments — ESLint documentation](https://eslint.org/docs/latest/use/configure/rules#using-configuration-comments-1)
- [lifting-logbook#338](https://github.com/brownm09/lifting-logbook/pull/338) — the PR that prompted this ADR
- [lifting-logbook#343](https://github.com/brownm09/lifting-logbook/issues/343) — batch issue to clean up the suppressions introduced in #338
