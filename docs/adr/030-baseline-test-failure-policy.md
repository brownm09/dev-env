# ADR-030 — Pre-existing Test Failure Policy: Baseline + Fix-on-Touch

**Date:** 2026-05-27
**Status:** Accepted
**Tags:** testing, quality, pre-pr, baseline, fix-on-touch, workflow

---

## Context

ADR-022 (Test Coverage Gate) and ADR-029 (Test Integrity Policy) together guard against *missing* tests on new behavior and *degradation* of existing tests. Neither addresses *inherited red state* — tests that were already failing when a branch was cut.

The observed failure mode in lifting-logbook: a session sees `N failed` in the test output, correctly identifies that the failures are unrelated to the current change, declines to fix them (the right call in isolation), and opens the PR. The next branch repeats the same dance. Pre-existing failures accumulate silently across many sessions, costing tokens on repeated avoidance decisions and never getting filed, prioritized, or fixed.

The existing gates have no concept of "was this red before I started?" — they only diff the current run against a binary pass/fail expectation. Without a per-branch baseline, there is no way to distinguish a failure introduced on this branch from a failure inherited from `main`.

A related risk: a session that *does* introduce a new failure can hide it by pointing at the existing red state ("tests were already failing"). Without a baseline, that claim cannot be falsified cheaply.

---

## Decision

Add a **Pre-existing test failure policy** subsection to `## Code Quality` in `claude/CLAUDE.md`, structured as three rules paralleling the suppression policy (ADR-026) and test integrity policy (ADR-029):

1. **Rule 1 — Baseline at branch creation.** When `baseline_test_failure_tracking: true` is set in a repo's `.claude/hook-config.json`, the `new-branch` shell function runs `baseline-tests snapshot` immediately after `git checkout -b`. The snapshot captures `{file, test_name, first_line, fingerprint}` tuples for every currently-failing test and writes them to `C:/Users/brown/.claude/scratch/baseline_<repo>_<branch>.json`. Fingerprint = `sha1(file + "::" + test_name + "::" + first_line)` — survives whitespace drift, breaks on real semantic change.

2. **Rule 2 — Fix-on-touch threshold.** When the pre-PR diff classifies a failure as `preexisting-touched` (i.e., it predates the branch but lives in a file the branch already modifies), the session fixes it inline if the fix is **≤ ~20 LOC or ≤ ~15 minutes** by Claude's judgment. Otherwise the session files a GitHub issue (or appends to a rolling "Pre-existing test failures" tracking issue) and references it in the PR body. The numeric proxy is deliberately judgment-based, not a hard line — the goal is to prevent both yak-shaving and silent inheritance.

3. **Rule 3 — Pre-PR baseline diff.** Before `gh pr create`, run `baseline-tests diff`. The script classifies failures into three groups:
   - **`new`** — fingerprint not in the baseline. **Blocks the PR.**
   - **`preexisting-touched`** — fingerprint in baseline AND file is modified on this branch. **Must be fixed inline (Rule 2) or filed.** Outstanding entries must be listed in the PR body.
   - **`preexisting-untouched`** — fingerprint in baseline, file unmodified. **Note only**, no blocking action.

A new helper script `claude/scripts/baseline-tests.sh` implements both subcommands. `claude/scripts/new-branch.sh` invokes `baseline-tests snapshot` opportunistically when the opt-in flag is set; the snapshot runs synchronously so the operator sees it complete before the first edit. `claude/scripts/pre-pr-create-check.py` adds an advisory section pointing at `baseline-tests diff` when a baseline file exists for the current branch.

`hook-config.json` gains two new optional fields:
- `baseline_test_failure_tracking: true|false` (default false — opt-in per project)
- `test_command: "<shell command emitting Jest --json output>"` (default `npx jest --json --silent`)

The hard gate is the behavioral rule in CLAUDE.md, consistent with ADR-026 and ADR-029. The helper script and hook are visibility and ergonomics, not enforcement.

---

## Rejected Alternatives

**Baseline only, no fix-on-touch.** Cheap to add and fully visible (`baseline-tests diff` would still classify into groups), but pre-existing failures never get fixed — they only get *named*. Backlog grows forever; the only forcing function is the operator's conscience. Insufficient.

**Fix-on-touch only, no baseline.** Without a baseline there is no way to distinguish "you broke this" from "this was already broken" — every red test in a touched file would fall under the rule. Forces yak-shaving on every PR, even when the touched file's broken test predates the branch by months. Violates the global rule against scope creep ("don't add features, refactor, or introduce abstractions beyond what the task requires").

**Block all PRs while `main` is red.** Strongest forcing function: nobody opens a PR until the baseline is empty. Punishes whoever happens to branch next for someone else's mess. In a solo-dev context, that's always the same person — turns one bad day's test debt into a permanent merge block. Rejected.

**Fingerprint by full stack trace instead of first error line.** More precise (no false negatives on near-identical failures), but breaks every time a stack trace line number shifts. False positives explode after any refactor. The first-line fingerprint accepts some over-reporting in exchange for stability across mechanical edits.

**Store the baseline in the project repo (`.claude/baselines/<branch>.json`) instead of scratch.** Survives across machines and OS reinstalls, but baselines are per-branch and branches are short-lived — the data is regenerable from `origin/main` at any time. Adds repo-level state with no durable value. Scratch is the right home.

---

## Consequences

**Positive:**
- Pre-existing failures become countable: every `baseline-tests diff` run prints `new / preexisting-touched / preexisting-untouched / fixed` counts. The "fixed" count gives kudos when a session opportunistically cleans up.
- The fix-on-touch threshold is judgment-based but bounded — the ~20 LOC / ~15 min proxy gives Claude enough latitude to handle one-line fixes inline without writing yak-shave compilers to evaluate edge cases.
- The baseline + diff structure makes "tests were already failing" a falsifiable claim, not an assertion.
- Opt-in per project via `hook-config.json` keeps the feature dormant in repos where it would just add latency (small repos with no test debt, repos with no Jest).
- Backlog visibility: failures filed under a rolling tracking issue can be queried with one `gh issue view`.

**Negative:**
- The snapshot run adds one full-suite execution at branch creation. In lifting-logbook (sub-minute per Explore agent's findings) the cost is negligible; in larger repos it would dominate `new-branch` latency and the opt-in flag should stay off. A `BASELINE_TESTS_SKIP=1` env-var escape valve bypasses the snapshot for a single `new-branch` invocation without disabling the flag globally — useful when the suite is temporarily slow or when a quick branch is needed for a non-code change.
- Jest is the only test runner supported in the first implementation. Pytest, Go `testing`, and Rust have different JSON output formats (or none) and each needs its own parser. The CLAUDE.md scope note flags this so a clean snapshot in a non-Jest repo is not interpreted as evidence the policy applies.
- The fingerprint is heuristic. Test renames will look like "new failures" until the baseline is refreshed; legitimate test rewrites that change the error message will also break the match. Accepted: better to slightly over-report (forcing a re-snapshot) than to silently miss.
- The ~20 LOC / ~15 min threshold is a number, not a contract. Sessions will sometimes fix things they should have filed, or file things they could have fixed. The judgment is intentional — this ADR will be revisited if the threshold proves too lenient or too strict.
- The feature only works when `new-branch` is the entry point. Branches cut via raw `git checkout -b` skip the snapshot — the pre-PR hook surfaces "no baseline" as an advisory but does not retroactively create one.

---

## References

- [ADR-022 — Test Coverage Gate Before PR](022-test-coverage-gate-before-pr.md) — sibling gate for *missing* tests on new behavior
- [ADR-026 — Suppression Policy](026-suppression-policy.md) — structural template (three rules + opt-in + behavioral enforcement)
- [ADR-029 — Test Integrity Policy](029-test-integrity-policy.md) — sibling gate for *degraded* tests; this ADR completes the trio (missing / degraded / inherited)
- [Jest CLI — `--json`](https://jestjs.io/docs/cli#--json) — official documentation of the structured output the baseline parser consumes
- [Jest JSON output schema — `aggregatedResult`](https://github.com/jestjs/jest/blob/main/packages/jest-test-result/src/types.ts) — primary source for the `testResults[].assertionResults[]` shape parsed by `baseline-tests.sh`
- [dev-env#282](https://github.com/brownm09/dev-env/issues/282) — the issue that prompted this ADR

---

## Amendment (2026-07-16) — Branch-Existence-Based Cleanup for Baseline Snapshots (dev-env#778)

**Gap.** `baseline_<repo>_<branch>.json` (Rule 1) had no cleanup mechanism at all — it accumulated forever, one file per branch ever created in an opted-in repo. This was surfaced during dev-env#768/PR#777, a broader initiative that added self-cleaning sweeps to every other per-session/per-day sentinel and marker family in `~/.claude/scratch/`. That PR deliberately *excluded* this family from its own age-based sweep (see `sweep-scratch-debris.py`'s module docstring) and filed the gap as dev-env#778 rather than papering over it with the same mechanism used everywhere else.

**Why not age-based, like everything else.** Every other sentinel family that PR cleaned up is scoped to a session or a calendar day, so "older than N days" is a safe proxy for "no longer needed." A baseline snapshot is scoped to a *branch's lifetime* instead — a long-lived branch (a large refactor, say) can legitimately need its baseline for weeks. Sweeping by age would silently delete a still-needed snapshot, and the next `baseline-tests diff` on that branch would either fail outright (no baseline found) or — worse — a freshly re-captured "baseline" would silently bake the branch's own already-introduced failures in as if they had pre-existed, defeating the whole point of Rule 3's `new` vs. `preexisting` classification.

**Fix.** A new `baseline-tests gc` subcommand (and a same-named internal function `cmd_gc`) sweeps `baseline_<repo>_*.json` files for the *current* repo whose recorded `branch` — read from the JSON envelope itself, never reverse-parsed from the filename — no longer exists as a branch, locally or on `origin`:

- **Local check:** `git rev-parse --verify --quiet refs/heads/<branch>`.
- **Remote check:** `git ls-remote --heads origin <branch>`, only when the local check comes up empty.
- **Deletion requires both checks to affirmatively agree the branch is gone.** A branch is *kept* whenever it's found in either place, **and — the design's central guarantee — whenever the remote check itself fails** (no network, no `origin` remote, auth failure). `branch_exists_remotely` returns a three-way result (exists / confirmed-absent / check-failed); only confirmed-absent counts toward deletion. This mirrors the conservative-on-uncertainty posture this codebase already applies to other irreversible-ish cleanup (e.g. `sweep-scratch-debris.py never counts a failed `unlink()` as removed).
- A malformed baseline file, or a well-formed one missing the `branch` field, is likewise kept rather than guessed at.
- `gc` runs automatically (best-effort — a `gc` hiccup never fails the snapshot itself) at the end of `cmd_snapshot`, since writing a new baseline is the natural moment to sweep old ones for the same repo. It is also directly invocable (`baseline-tests gc`) for on-demand or scripted use.

**Consequences.**
- **Positive:** the accumulation gap is closed without reintroducing the age-based failure mode Rule 1's design space explicitly rejected for this family. No new opt-in flag or config is needed — cleanup piggybacks on the same `snapshot` invocation Rule 1 already requires at branch creation.
- **Negative:** `gc` only ever runs for the repo it's invoked from (via `repo_name()`, matching how `baseline_path()` already names files) — a repo whose branches never get a fresh `new-branch`/`snapshot` call (e.g. abandoned mid-initiative) keeps accumulating until the next snapshot in that repo. This is accepted as a bounded, self-limiting risk: no worse than the pre-fix baseline of "never," and the standalone `gc` subcommand is available for manual/scripted sweeping if a repo's backlog needs clearing without waiting for the next branch.
- **Negative:** the remote check adds one `git ls-remote` network round-trip per candidate file whose branch is already gone locally, on every `snapshot` call. Bounded by the number of stale local baselines for that repo (typically small), and skipped entirely for any baseline whose branch still exists locally.

**Testing.** `claude/scripts/tests/test-baseline-tests-gc.sh` (see `CLAUDE.md` → Testing, item 72) drives the real script against throwaway git fixtures (a bare `origin` + a working clone) — a baseline is kept when its branch exists locally, kept when it exists only on `origin`, removed only when confirmed gone in both places, kept whenever the remote check itself fails, and never touched when it belongs to a different repo or fails to parse.

See [dev-env#778](https://github.com/brownm09/dev-env/issues/778).
