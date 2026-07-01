# ADR-029 — Test Integrity Policy: No Silent Degradation of Existing Tests

**Date:** 2026-05-27
**Status:** Accepted
**Tags:** testing, quality, review, suppression-parallel, workflow, pre-pr

---

## Context

The global "Test before PR" rule in `claude/CLAUDE.md` requires that tests pass before opening a PR, and the Test Coverage Gate (ADR-022) requires that new testable behavior come with corresponding tests. Together they guard against *missing* tests on *new* behavior. Neither guards against *degrading* existing tests to manufacture a green run.

An author (or agent) needing to get a PR green can satisfy the letter of the rule by:

- Marking failing tests as skipped (`it.skip`, `xit`, `xdescribe`, `test.skip`, `describe.skip`, `.todo`, `pending`).
- Deleting test files or whole `describe` / `it` blocks.
- Lowering coverage thresholds in `jest.config.*`, `.nycrc`, `vitest.config.*`, or equivalents.
- Adding `--passWithNoTests`, `--bail`, or `--testPathIgnorePatterns` to a test invocation or CI command.
- Hardcoding implementation values to satisfy a specific test input rather than a general contract (skew).

The suppression policy (ADR-026) addresses an analogous failure mode for type and lint errors: silenced rather than fixed. That ADR established a three-rule template — justification, file-don't-silence, pre-PR grep — that maps cleanly to the test-integrity problem.

Test runs today are also opaque: the session sees pass/fail but not N-passed/N-skipped/N-failed, so a "green" run that silently skipped half the suite is indistinguishable from a real pass.

---

## Decision

Add a `### Test integrity policy` subsection to the `## Code Quality` section of `claude/CLAUDE.md`, parallel in structure to the existing Suppression policy:

1. **No integrity violation without justification.** Any skip marker, deleted test, lowered coverage threshold, or bypass flag that lands in a PR must be accompanied by a PR-body note naming the specific tests/thresholds and stating why removal or degradation is appropriate.

2. **Skipped counts must be visible.** The test-before-PR run must emit a `Tests: N passed, N skipped, N failed (duration)` summary line, included verbatim in the PR body under the Testing section. A non-zero skipped count requires per-skip justification.

3. **Pre-PR test-integrity grep (required before `gh pr create`).** Run alongside the suppression grep:
   ```bash
   git diff origin/main -- . | grep -E '(it\.skip|xit\(|xdescribe\(|test\.skip|describe\.skip|\.todo\(|pending\(|passWithNoTests|--bail|testPathIgnorePatterns)'
   git diff --diff-filter=D --name-only origin/main -- '*.test.*' '*.spec.*' 'tests/**' 'e2e/**'
   git diff origin/main -- jest.config.* .nycrc vitest.config.* | grep -E 'threshold|coverage'
   ```
   Any match must map to a Rule 1 justification or be reverted. A PR that adds integrity violations with no PR-body justification is not mergeable.

A one-line cross-reference is also appended to the "Test before PR" bullet in `## Git Workflow` so the integrity check appears at the same decision point as the suppression check.

Add a corresponding **Step 2e — Test Integrity Gate Check** to `claude/skills/review/SKILL.md`, running immediately after Step 2d. Step 2e scans the diff for the same patterns as the pre-PR grep plus implementation-skew heuristics, and verifies the test-run summary line is present in the PR body. Violations are classified as **Blocking [correctness]**; ambiguous skew is raised as a question.

A *test integrity violation* is defined as: adding a skip marker, deleting a test file or `describe`/`it` block, lowering a coverage threshold, adding a bypass flag (`--passWithNoTests`, `--bail`, `--testPathIgnorePatterns`), or hardcoding implementation values to satisfy a specific test input rather than a general contract.

---

## Consequences

**Positive:**
- Test degradations become visible artifacts requiring acknowledgment before merge — the same forcing function ADR-026 applies to suppressions.
- Skipped-test counts surface in the session, the PR body, and the engineering journal stub. A "green" run that silently skipped half the suite is no longer indistinguishable from a real pass.
- The grep check takes under 5 seconds and runs at the same decision point as the existing suppression grep.
- The `/review` Step 2e provides a second-pass catch when a pre-PR check is skipped or misses a pattern.
- Implementation skew (`if (input === 'test-value') return expected`) is named explicitly as a violation rather than left to reviewer judgment.

**Negative:**
- Every legitimate test removal (e.g., deleting tests for a removed feature) requires a PR-body note. This is a deliberate friction cost — the alternative is silent removal.
- The skew-detection step (Step 2e #4) relies on LLM judgment about whether a hardcoded constant looks like a test input. False positives will occur; the non-blocking finding category absorbs them.
- The required summary line (`Tests: N passed, N skipped, N failed`) adds a small line of PR-body content even for trivial PRs. Acceptable since the line doubles as evidence the test command was actually run.
- The grep patterns target common JavaScript/TypeScript test frameworks (Jest, Vitest, Mocha); pytest / Go / Rust equivalents are not currently covered and would need separate patterns when those languages are in scope. A scope note in `claude/CLAUDE.md` Rule 3 surfaces this at the decision point.
- Two grep patterns over-match by design and require manual review of matches: `--bail` matches any CLI bail flag (not just test-runner contexts), and the threshold/coverage grep flags any change to those fields including improvements (threshold *increases*). Narrowing either pattern would require parsing numeric diffs or context, which is more brittle than letting the author classify a small number of matches. The over-match is documented in CLAUDE.md Rule 3 as "Known false-positive classes."

---

## Amendment (2026-07-01) — word-boundary fix for the `xit\(` false positive

Rule 3's grep pattern used bare `xit\(` to catch Mocha's `xit(` skip function. Because the check is a plain substring match, `xit\(` also matches inside any identifier ending in `...exit(` — most commonly Python's `exit(` / `sys.exit(` / `os._exit(` calls used throughout this repo's hook scripts as part of the safe-exit-guard convention, and any function name ending in `_exit(` (e.g. `..._on_clean_exit()`).

This produced real false positives, each requiring a manual "these are not skip markers" note in the PR body: [dev-env#493](https://github.com/brownm09/dev-env/pull/493) (`sys.exit(0)`) and [dev-env#497](https://github.com/brownm09/dev-env/pull/497) (`sys.exit(0)` plus a `..._on_clean_exit()` function name).

Fix: anchor the pattern on a word boundary — `\bxit\(` instead of `xit\(` — using GNU grep's `\b` extension (available in `-E` mode; confirmed on this repo's Git Bash / GNU grep). Verified empirically:

```
$ printf 'sys.exit(0)\nos._exit(1)\ndef clean_exit():\n  xit(%s\n' "'foo', () => {})" | grep -E '\bxit\('
  xit('foo', () => {})
```

`sys.exit(0)`, `os._exit(1)`, and `clean_exit():` no longer match — there is no word boundary between `e` and `x` in either case — while a real Mocha `xit(` call still matches, since it is always preceded by whitespace, a quote, a paren, or line start, never by another word character. `claude/CLAUDE.md` Rule 3 was updated to `\bxit\(`; this ADR's original Decision code block above is left unchanged as the historical record. See [dev-env#501](https://github.com/brownm09/dev-env/issues/501).

---

## References

- [ADR-022 — Test Coverage Gate Before PR](022-test-coverage-gate-before-pr.md) — companion gate for *missing* tests on new behavior
- [ADR-026 — Suppression Policy](026-suppression-policy.md) — structural template (three rules + pre-PR grep + parallel `/review` step)
- [ADR-028 — All-Findings Merge Gate](028-all-findings-merge-gate.md) — merge-time enforcement that includes Step 2e findings
- [Jest CLI options — `--passWithNoTests`](https://jestjs.io/docs/cli#--passwithnotests) — official documentation of the bypass flag the policy targets
- [Jest configuration — `coverageThreshold`](https://jestjs.io/docs/configuration#coveragethreshold-object) — the threshold field the policy guards against lowering
- [dev-env#270](https://github.com/brownm09/dev-env/issues/270) — the issue that prompted this ADR
