# ADR-134 — Opt-In-by-Default File-Level Retry for run-hook-tests.py

**Date:** 2026-08-16
**Status:** Accepted
**Closes:** [dev-env#994](https://github.com/brownm09/dev-env/issues/994)
**Tags:** testing, ci, run-hook-tests, flaky-tests, retry, workflow, windows-runner, adr-029, adr-026, adr-103, dev-env-994
**Related:** [ADR-103](103-shared-hookout-emitter.md), [ADR-029](029-test-integrity-policy.md), [ADR-026](026-suppression-policy.md), [ADR-030](030-baseline-test-failure-policy.md)

---

## Context

[dev-env#994](https://github.com/brownm09/dev-env/issues/994) reported that four test files —
`test_pre_tool_use_nested_agent_background_guard.py`, `test_skill_file_size.py`,
`test_skill_file_size_advisory.py`, `test_skill_file_size_guard.py` — intermittently fail when
run via the full suite (`py -3 claude/scripts/run-hook-tests.py`) but pass 100% standalone.
`.github/workflows/hook-tests.yml` runs this exact script on every PR, so an intermittent,
non-reproducible failure here reads as a real CI red X unrelated to a PR's actual change, costing
investigation time on every future PR unlucky enough to trigger it.

Investigation (dev-env#994) was exhaustive and found no code-level bug:

- The full suite was run 4 times locally (1 manual + 3 scripted back-to-back). **All 4 passed
  100% clean, 0 failures** — the flakiness could not be reproduced locally at all.
- 3 independent Explore agents read all 4 failing test files, their SUTs, and ~20
  alphabetically-adjacent predecessor test files in the suite's run order. **None found any
  shared-state pollution mechanism** — no fixed-path lock/sentinel/cache files, no unclosed
  tempdirs, no real-repo-tree size/count dependencies, no leaked git state, no env-var leakage
  across subprocess boundaries. This is structurally expected: `run-hook-tests.py` launches every
  test *file* as its own independent OS subprocess (`subprocess.run([sys.executable, path],
  cwd=REPO_ROOT, ...)`), so there is no shared Python process / `sys.modules` state between files
  at all — only the real filesystem and real OS-level resources persist across the run.
- `test_skill_file_size.py` makes **zero** subprocess calls internally (pure in-process function
  calls + `tempfile.TemporaryDirectory()` I/O only), which rules out "internal subprocess
  timeout" as the *sole* explanation for the group — one of the four failing files can't time out
  a subprocess it never spawns. The other three each spawn their hook-under-test via a hardcoded
  `timeout=30` in their own `_run_hook` test helper.
- Working conclusion: Windows-runner transient resource/timing contention (subprocess-spawn
  overhead, antivirus/indexer scanning of freshly-created temp files, cumulative load from 85+
  sequential subprocess-launching test files run before these) — most plausible on GitHub
  Actions `windows-latest`, consistent with it never reproducing locally.

Given the root cause is very likely an environmental effect outside this repo's control (and
possibly not fully fixable by editing script code at all), the proportionate response is a
visible, non-masking retry mechanism in the test runner itself, rather than continuing to chase a
root cause that four separate investigative passes could not pin to a line of code.

## Decision

1. **New pure helper `run_with_retries(run_one, max_retries, on_attempt=None)`** in
   `claude/scripts/run-hook-tests.py`. Calls the zero-arg `run_one()` (real call site:
   `functools.partial(_run_one, path, bash_bin, timeout)`) up to `1 + max_retries` times,
   retrying only while status == `"fail"`. `"pass"` and `"skip"` always return immediately with
   `retries_used == 0` — a self-skip is **mechanically** never retried, a property of the loop's
   own control flow, not a bolted-on special case. Returns the **final** attempt's
   `(status, elapsed, output)` plus `retries_used`, so an eventual pass' output is never diluted
   by an earlier failed attempt's, and a final failure's dumped diagnostic is the one that
   actually explains the reported failure. `on_attempt`, if given, is a notification-only
   callback (no return value, no effect on the retry decision) so the helper itself stays I/O-free
   and unit-testable with a canned mock — no real subprocess spawning needed in its test suite.

2. **New `--max-retries` CLI flag, default 2** (3 total attempts per file). High enough to
   absorb a second independent contention event; low enough that a genuinely broken file still
   reports red within 3 fast attempts (these are assertion failures, not hangs, per the
   investigation's determinism finding — no randomness or shared mutable state exists anywhere
   in this suite). `--max-retries 0` restores today's exact strict behavior byte-for-byte — the
   escape hatch for hand-bisecting a suspected regression.

3. **Full visibility, never silent.** Every re-attempt prints a `RETRY` line; an eventual pass
   or a final fail is suffixed `[retried Nx]`; a new `Retried: flaky-passed=[...]
   hard-failed=[...]` summary line appears whenever any retry occurred at all. The existing
   `Suite:`/`Tests:`/`Failed:` summary lines are unchanged in format (final-status counts only) —
   the PR-body `Tests: N passed, N skipped, N failed (duration)` convention keeps working
   verbatim. On the all-pass, no-retry-needed path (the common case, and always true under
   `--max-retries 0`), the entire output stream is provably byte-identical to before this change.

4. **Applies uniformly to Python and bash test files.** No Python-only carve-out: the
   contention theory (Windows subprocess-spawn overhead) applies identically to any OS
   subprocess spawn, `_run_one`/`_command_for` already dispatch on suffix internally, and an
   asymmetry where a bash gate's transient failure hard-fails the suite while an adjacent Python
   test's identical failure auto-heals would have no evidence basis.

5. **Complementary hardening (not the fix on its own):** the three subprocess-spawning failing
   test files' internal `_run_hook`/`_run_hook_with_home` timeouts are bumped from `timeout=30`
   to `timeout=90` — near-zero cost on the happy path (these subprocesses normally complete in
   under a second), extra headroom under contention, stacked underneath the file-level retry.
   Deliberately does not by itself explain `test_skill_file_size.py` (no subprocess calls), which
   is exactly why the file-level retry, not a timeout bump, is the primary fix.

## Why this does not violate ADR-029 (Test Integrity Policy) or ADR-026 (Suppression Policy)

ADR-029 targets mechanisms that let a PR *look* green while *hiding or weakening* what a test
actually checked: skip markers, deleted tests/blocks, lowered coverage thresholds, bypass flags,
implementation-value hardcoding. A retry does **none** of these — it re-runs the exact same,
unmodified test file, with its own unmodified assertions, and only records a pass if that
specific attempt's code independently reports success on a full, faithful run. It changes *how
many chances a file gets before the suite calls it a final failure*, never *what counts as a pass
for a single attempt*.

ADR-029 Rule 2 requires skipped counts to stay visible; the retry mechanism honors that spirit by
construction rather than by exception — every retry is printed live, every flaky-pass carries a
`[retried Nx]` tag and appears in the `Retried:` line, and every hard-failure-after-retries is
both in `Failed:` and separately called out in `Retried:`. Nothing is folded silently into a
plain `PASS`. The same reasoning distinguishes it from ADR-026 (suppression policy): no assertion
is silenced, ignored, or cast away — the code path that decides pass/fail for a single attempt is
untouched.

## When a maintainer should suspect retry-masking vs. genuine flake

- **Genuine-flake signature:** a file passes on retry in some CI runs and passes clean in
  others, with no code change to the file or its SUT between occurrences — consistent with this
  investigation's confirmed determinism (no randomness, no shared mutable state) across all four
  originally-reported files.
- **Suspect-masking signatures:**
  - A file whose *first* attempt fails on **every** run and only a later attempt ever passes —
    that is a reproducible dependency/ordering bug, not occasional flake, and should be
    investigated as a real defect rather than left to auto-heal.
  - A file's failure message *changes shape* between attempts (a different assertion, a
    different exception) rather than looking like the same timeout/contention signature each
    time.
  - A file's flake rate visibly increasing right after a change to that file or its SUT —
    suggests a newly introduced race, not noisier CI infrastructure.
  - The same filename recurring in `Retried:` across many CI runs over time — a maintainer
    periodically skimming CI history should treat a chronically-flaky file as a bug report to
    file, not as background noise to tune out.
- **Rollback / escape hatch:** `--max-retries 0` restores today's exact strict behavior for
  bisecting a suspected regression by hand.

## Considered alternatives

- **Bisect the exact predecessor test(s) causing pollution.** Attempted first (dev-env#994's
  own suggested next step). Rejected as inconclusive after 3 independent thorough Explore
  passes over every plausible predecessor found nothing — continuing to chase this with no new
  evidence would not be a good use of further investigation time, especially given the
  structural argument (each test file is its own OS subprocess, so no Python-level state can
  leak) already rules out the most common pollution shapes.
- **Increase only the internal `timeout=30` values, no runner-level retry.** Rejected as
  incomplete on its own — cannot explain `test_skill_file_size.py`, which spawns no subprocess
  at all. Kept as a complementary, low-risk hardening (Decision item 5), not the primary fix.
- **Mark the four files as permanently runner-skipped (`SKIP_TESTS`).** Rejected — this *would*
  be a real ADR-029-relevant weakening (removing real coverage from CI, not just re-attempting
  it), unlike a retry. These tests pin real, valuable behavior; skipping them outright trades a
  false-negative-CI problem for a real coverage gap, which is strictly worse.
- **Silently retry with no visible accounting.** Rejected — would violate the spirit of ADR-029
  Rule 2 even though it's not a literal skip marker; a maintainer skimming CI output should be
  able to see that flakiness happened at all, not just get a clean `PASS` that hides it.

## Consequences

- **Positive:** smooths the four originally-reported flaky files (and any future similarly-shaped
  ones) without weakening what "pass" means for a single attempt; visible accounting prevents the
  "silent green" failure mode ADR-029 exists to guard against.
- **Negative:** a genuinely broken file now takes up to 3x longer to report red (bounded, since
  these are assertion failures, not hangs). A chronically-flaky file could, in principle, mask a
  real intermittent bug if nobody ever reads the `Retried:` line over time — this is not
  eliminated by the mechanism itself; the actual backstop is a human (or a future periodic
  routine) treating a recurring `Retried:` entry as a bug report, per the guidance above.
- New unit tests (`claude/scripts/tests/test_run_hook_tests.py`) pin `run_with_retries`'s
  contract exhaustively via a canned zero-arg-callable mock, no real subprocess spawning.
- `docs/TESTING.md` item 64's body gains a paragraph describing the new coverage; no `CLAUDE.md`
  Testing-index edit needed (`test_testing_index_parity.py` only diffs item numbers and bold
  titles, and item 64's title is unchanged).

## References

- [dev-env#994](https://github.com/brownm09/dev-env/issues/994) — the filed issue and its
  investigation.
- [ADR-103](103-shared-hookout-emitter.md) — `run-hook-tests.py`'s original design context.
- [ADR-029](029-test-integrity-policy.md) — the policy this decision is explicitly distinguished
  from.
- [ADR-026](026-suppression-policy.md) — likewise.
- [ADR-030](030-baseline-test-failure-policy.md) — a related but distinct policy (pre-existing
  *deterministic* failures inherited across branches) that this ADR does not modify or overlap
  with; this ADR concerns *non-deterministic* single-run flakiness within one suite invocation.
- `claude/scripts/run-hook-tests.py`, `claude/scripts/tests/test_run_hook_tests.py` — the
  changed runner and its test suite.
