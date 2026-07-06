# ADR-087: Extend disk-space-check.py to PreToolUse(Bash) to Close the Mid-Turn Free-Space Gap

**Date:** 2026-07-05
**Status:** Accepted
**Tags:** hooks, disk, worktrees, node_modules, pre-tool-use, user-prompt-submit, multi-event-hook, adr-085-fast-follow

---

## Context

[dev-env#592](https://github.com/brownm09/dev-env/issues/592) picks up a fast-follow that ADR-085
named but deliberately did not build (that ADR's own scope was Bash repo/branch drift detection, an
independent concern). ADR-085's Context section states it directly:

> dev-env already has a disk-pressure preflight safety net (`claude/scripts/disk-space-check.py`, a
> `UserPromptSubmit` hook warning at 20 GB free and auto-reclaiming at 10 GB on `C:`)... It has one
> structural limitation worth naming: `UserPromptSubmit` fires once per user prompt, not per tool
> call, so exhaustion occurring *within* a single long agentic turn can outrun it. Tightening that to
> a `PreToolUse(Bash)` check is cheap (`shutil.disk_usage()` is a syscall, not a subprocess spawn) but
> is independent of this ADR's mechanism — a candidate fast-follow, not built here.

This is the same failure class ADR-037 and ADR-045 already defend against — disk exhaustion
surfacing as silently truncated `node_modules` (dev-env#306, dev-env#364) — but from a different
angle. ADR-037's `disk-space-check.py` and ADR-045's `worktree-npm-install.py` pre-install gate both
assume the harness gets a chance to re-check between the moment space gets tight and the moment
something heavy happens. `UserPromptSubmit` firing once per prompt (not per tool call) is exactly the
gap: a long agentic turn can run dozens of Bash calls — including an `npm install` — with no
intervening prompt to re-trigger the warning or the reclaim spawn.

## Decision

Register `claude/scripts/disk-space-check.py` as a **second** hook entry, under `hooks.PreToolUse` →
the existing `"matcher": "Bash"` array in `claude/settings.json` (9th entry in that array), in
addition to its existing `UserPromptSubmit` registration. No new script file.

This works because `main()` was already hook-event-agnostic: it reads only `session_id` and `cwd`
from the stdin JSON payload and ignores `hook_event_name`/`tool_name`/`tool_input` entirely — so the
exact same code runs correctly whether the invoking event is `UserPromptSubmit` or
`PreToolUse(Bash)`. The per-session marker-file gate
(`scratch/disk_space_check_<session_id>_<band>.flag`) is keyed by `session_id` + band only, not by
which hook event fired it, so "at most once per session per band" holds globally across both
entries — whichever fires first for a session silently covers the other.

Two small changes to make the reused script fully accurate and testable:

1. **Docstring rewrite** — describes firing on both events and why, rather than claiming to be
   `UserPromptSubmit`-only.
2. **Extract `classify_free_space(free_gb, warn_gb, act_gb) -> "act"|"warn"|"ok"`** from `main()`'s
   inline `if free < ACT_GB: ... elif free < WARN_GB: ...` — a behavior-preserving refactor (identical
   boundaries: a reading exactly equal to a threshold has not yet crossed into that band) that makes
   the classification logic unit-testable offline, matching the repo's established pure-decision-
   function convention (e.g. `install_decision()` in ADR-045).

## Judgment calls

### Reuse the existing script, not a new one — the `awake-blocker.py` precedent, not the ADR-071 one

ADR-071 explicitly chose a **new** script over folding into ADR-024's existing hook, reasoning that
the two had "a different matcher... a near-inverse trigger condition... and a different failure
mode" — genuinely different decision trees sharing only a superficial theme. That reasoning does not
apply here: this change has the **identical** decision tree (same thresholds, same marker gate, same
reclaim spawn) with only the *triggering event* differing. `awake-blocker.py` is the closer
precedent — one script already registered across three event types (`UserPromptSubmit`, `Stop`,
`Notification`) with one behavior and one doc-row family (see `docs/REFERENCE.md`'s `(start)`/`(stop)`
paired rows). Splitting `disk-space-check.py` into two files would duplicate the threshold constants,
the marker-gating helpers, and the reclaim-spawn logic for no behavioral gain.

### Confirmed non-blocking `systemMessage` already works from `PreToolUse` before relying on it

Before committing to "reuse as-is," verified that a `PreToolUse(Bash)` hook exiting 0 with
`{"systemMessage": ...}` on stdout is already a proven, working pattern in this exact repo —
`pre-commit-branch-check.py` does precisely this (non-blocking branch-checkpoint message on every
`git commit`). This meant no new, unproven hook-output shape was being introduced by wiring the same
script under a second event.

### No threshold or reclaim-mechanism changes

`WARN_GB`/`ACT_GB` (20/10) and the reclaim-spawn arguments are untouched. This change is purely about
*when* the existing check runs, not what it does once it runs.

### Marker-file gate intentionally shared across both event types, not split per-event

An alternative design would key the marker file by `(session_id, band, hook_event)` so each event
type gets its own "fired once" slot. Rejected: the whole point of the fix is to catch exhaustion the
`UserPromptSubmit` check might have missed — if the `UserPromptSubmit` check already warned this
session, a `PreToolUse(Bash)` call re-warning about the same still-low reading moments later would be
noise, not signal. Sharing one gate per `(session_id, band)` means the two entries cooperate as a
single logical check with two trigger points, not two independent checks.

### No new race risk from firing on every Bash call

Bash tool calls within one Claude Code session are processed sequentially, not concurrently, so two
`PreToolUse(Bash)` invocations can never race each other's marker-file read/write for the same
session — the existing single-threaded gate (already relied on by the `UserPromptSubmit` registration
across a whole session's prompts) needed no hardening for the new call frequency.

## Consequences

- **Closes the gap ADR-085 named**: a long tool-call-only stretch within one turn now gets a
  free-space re-check before every Bash call, not just at the next prompt boundary.
- **Performance**: one extra `shutil.disk_usage()` syscall (no subprocess spawn) per Bash call —
  negligible, and consistent with the eight other `PreToolUse(Bash)` hooks already on this hot path
  (`pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-message-check.py`,
  `pre-merge-branch-check.py`, `pre-merge-findings-gate.py`, `pre-auto-merge-checkpoint-gate.py`,
  `pre-merge-numbering-check.py`, `pre-tool-use-canonical-mutate-guard.py`).
- **No behavior change to the `UserPromptSubmit` registration** — same thresholds, same messages,
  same spawn mechanism; only the classification's internals moved into a named, tested function.
- **Testing**: `claude/scripts/tests/test_disk_space_check.py` — the first dedicated test file for
  this hook — pins `classify_free_space()`'s three bands and both threshold boundaries offline. The
  `disk_usage` syscall, marker-file I/O, and detached reclaim spawn remain untested per the repo's
  established convention of not mocking those boundaries (matches `test_worktree_npm_install.py`).
- **Resilience**: unchanged — the existing safe-exit guard (`try/except Exception: sys.exit(0)`) and
  the `OSError`-tolerant `_free_gb()` call already cover both registrations; `PreToolUse` exit 0 never
  blocks the Bash call regardless of which band fired.
- **Security / Data integrity**: N/A — no new authz, input-validation, secrets, or schema surface;
  same trusted local syscall and same detached-spawn argument shape as before.
- **ADR warranted** because the change modifies `claude/settings.json` hook wiring and a hook
  script's registered trigger surface — the same warranting shape as ADR-024/045/071.

## References

- `claude/scripts/disk-space-check.py` — implementation (docstring + `classify_free_space()`)
- `claude/scripts/tests/test_disk_space_check.py` — self-test
- `claude/settings.json` — hook wiring (9th entry in the `PreToolUse` → `Bash` matcher array)
- [ADR-085](085-bash-repo-branch-drift-detection.md) — names this as a candidate fast-follow in its
  Context section
- [ADR-037](037-worktree-disk-reclamation.md) — `disk-space-check.py`'s original ADR (thresholds,
  marker-gating convention, detached-spawn mechanism), unchanged by this decision
- [ADR-045](045-pre-install-freespace-gate.md) — the sibling pre-install free-space gate and the
  `install_decision()` pure-function testing precedent this ADR's `classify_free_space()` follows
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the "new script, not folded in" precedent
  this decision deliberately departs from, and why
- [ADR-033](033-prevent-system-sleep-while-processing.md) — `awake-blocker.py`, the precedent for one
  script registered under multiple hook events
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — per-session marker-file convention
- [dev-env#592](https://github.com/brownm09/dev-env/issues/592) — motivating issue
- [dev-env#306](https://github.com/brownm09/dev-env/issues/306),
  [dev-env#364](https://github.com/brownm09/dev-env/issues/364) — the underlying disk-exhaustion
  incidents this whole hook family defends against
