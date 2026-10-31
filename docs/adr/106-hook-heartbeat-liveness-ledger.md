# ADR-106: Hook Heartbeat/Liveness Ledger

**Date:** 2026-07-13 (amended 2026-07-13)
**Status:** Accepted
**Tags:** hooks, heartbeat, liveness, reliability, UserPromptSubmit, silent-failure, monitoring, shared-module, adr-064, adr-103, adr-717

---

## Context

The hook-reliability initiative's earlier phases ([ADR-103](103-shared-hookout-emitter.md) and
its Phase C migrations) close the *"is this hook's output routed to a channel the model or user
can actually see"* gap — a hook that fires now reliably gets its advisory seen. They do nothing
for a different, prior question: **is the hook firing at all?**

Two hooks answered that question the hard way:

- `post-tool-use.py` (the PostToolUse project-board hook) was silently dead for **months**
  ([dev-env#377](https://github.com/brownm09/dev-env/issues/377)) — a payload-shape regression
  meant every invocation read an empty `output` field and exited having done nothing, with no
  error, no log, and no way to notice short of manually testing the exact code path it covered.
- `usage-snapshot.py` went dark for **8 days**
  ([dev-env#355](https://github.com/brownm09/dev-env/issues/355)) before an unrelated
  investigation happened to surface it.

Both were discovered only by accident. Nothing in the hook system — not `claude/settings.json`'s
wiring, not the per-hook test suites, not the output-contract gates ADR-103 added — answers "did
this hook actually run in the last N days?" A hook can be perfectly wired, pass every unit test,
and route its output through the correct channel, and still be dead in production if the
interpreter crashes before reaching its first meaningful statement, if `pyw -3` fails to resolve
the script, or if a future Claude Code version changes the invocation contract in a way this
repo's tests don't exercise. The output-contract gates ([ADR-103](103-shared-hookout-emitter.md))
verify a hook's code is *correct*; nothing verifies it is *running*. This is Phase D (PR8) of the
hook-reliability initiative ([dev-env#717](https://github.com/brownm09/dev-env/issues/717)
top-level, [dev-env#745](https://github.com/brownm09/dev-env/issues/745) this sub-issue).

## Decision

Add a **heartbeat ledger**: every wired hook records a timestamp file on each invocation; a new
`UserPromptSubmit` hook reads the ledger and warns when a wired hook has gone quiet longer than
its expected cadence.

### Writer: `_hookutil.record_heartbeat(hook_name)`

Added to `claude/scripts/_hookutil.py` (the shared Stop/UserPromptSubmit-family sibling module —
[ADR-064](064-shared-hookutil-sentinel-transcript-locate.md)), alongside the existing per-session
sentinel helpers it already provides:

```python
def record_heartbeat(hook_name: str, heartbeat_dir: Path | None = None) -> None:
    ...
```

Writes `~/.claude/scratch/hook-heartbeat/<hook_name>.ts` (default; overridable for tests)
containing the current Unix timestamp, via a **per-process tmp file + `os.replace` atomic swap**
— no locks, no subprocess, matching this module's existing sentinel-file convention. Best-effort:
swallows all I/O errors, so a heartbeat write can never be the reason a hook fails.

**Call-site placement is the load-bearing decision.** Every one of the 41 currently-wired hook
scripts (see the settings.json Hooks tables in README.md / REFERENCE.md) gets exactly one call —
`_hookutil.record_heartbeat("<hook-name>")` — as the **first statement of `main()`**, before any
other logic, including before the hook's own stdin read. This is deliberate on three counts:

1. **It must fire on every real invocation, regardless of what the rest of the hook does.** A
   heartbeat records "the hook was invoked," not "the hook found something to warn about" — most
   invocations of most advisory hooks are legitimate no-ops (nothing to warn about this turn), and
   those must still count as liveness.
2. **It must fire even if the rest of `main()` crashes.** Placing it first means a heartbeat is
   recorded before any exception the hook's own safe-exit guard (`except Exception: sys.exit(0)`)
   would otherwise swallow silently — turning "the hook ran and then crashed" into a distinguishable
   signal from "the hook never ran at all" (both currently look identical from the outside: silence).
3. **It must NOT fire on a bare `import` of the module.** Every hook script is also imported
   directly by its own test file to unit-test pure helpers offline (`importlib.import_module`).
   Placing the call inside `main()` — never at module level — means running `py -3
   claude/scripts/tests/test_foo.py` never pollutes the heartbeat ledger; only a real invocation
   (production `pyw -3 foo.py`, or an end-to-end test that drives `main()` via subprocess) does.

`hook_name` is always a literal the hook's own author controls (its script basename minus `.py`),
never untrusted input — same trust model as `sentinel_path`'s `session_id` parameter, no
sanitization needed.

### Reader: `hook-liveness-check.py` (new `UserPromptSubmit` hook)

Reads `claude/settings.json`'s `hooks` block to discover which scripts are **currently wired**
and under which events (`wired_hook_events`), reads each non-exempt hook's heartbeat file
(`stale_hooks`), and warns via `_hookout.emit_advisory("UserPromptSubmit", msg,
audience="model")` when one or more are missing or older than **`DEFAULT_CADENCE_DAYS = 7`**.

- **Deriving the wired set from `settings.json` itself** (rather than a hardcoded list) means a
  newly-added hook is automatically covered with zero further edits, and a hook removed from
  wiring stops being checked automatically — the ledger's scope always matches production
  reality, the same self-updating property `_hook_wiring.py`'s existing consumers
  (`test_hook_output_contract.py` / `test_hook_safe_exit_guard.py` / `test_settings_hook_wiring.py`,
  all ADR-103 PR3) already rely on for their own "every wired hook" scans.
- **Exemption is by event set, not by script name.** A hook wired **exclusively** to
  `PostCompact` and/or `Notification` — events that fire rarely and legitimately (a compaction, a
  specific idle/permission notification) — is exempt from the cadence check entirely, since
  "hasn't fired in 7 days" is expected, not a symptom. A hook wired to a rare event **and** any
  other event (e.g. `awake-blocker.py`: `Notification` + `UserPromptSubmit` + `Stop`) is **not**
  exempt — the presence of a normal-cadence registration means normal-cadence silence is still
  worth flagging.
- **Model-visible by default.** Per this initiative's standing design decision ("warnings
  model-visible first, systemMessage where human awareness suffices" — the #717 plan), a stale
  hook is something Claude itself can act on: check `claude/settings.json` wiring, check the
  script for a crash, check whether the invocation path changed. `audience="model"` on
  `UserPromptSubmit` delivers via `additionalContext` (a context event, the channel
  `_hookout`/ADR-103 already supports) rather than a `systemMessage` toast the model never sees.
  This is the first production call site of `_hookout.emit_advisory(..., audience="model")` —
  every existing migrated hook (Phase C) uses `audience="user"`.
- **`hook-liveness-check.py` heartbeats itself.** As a newly-wired `UserPromptSubmit` hook, it is
  itself subject to the same ledger — its own `main()` calls
  `_hookutil.record_heartbeat("hook-liveness-check")` first, so a future regression that makes
  *this* hook stop firing is not invisible merely because it's the one watching everything else.

### Settings-parsing scope decision (deliberately not shared with `_hook_wiring.py`)

`claude/scripts/tests/_hook_wiring.py` (the settings.json parser the three PR3 gates already
share) exposes an equivalent `wired_script_events()`. This ADR deliberately does **not** import
it or extract a shared production module for `hook-liveness-check.py` to reuse — three reasons:

1. It lives in `tests/`, a test-support module by design (its own docstring: this keeps the AST
   gates' "every wired hook" scans from treating it as a wired hook itself); a production hook
   importing from `tests/` inverts that boundary and complicates the `pyw -3` single-directory
   `sys.path` resolution ([ADR-007](007-hook-command-invocation.md)).
2. The two use cases have different return-value needs — the PR3 gates need full `HookEntry`
   tuples (matcher, timeout, raw command) for their timeout/resolution checks;
   `hook-liveness-check.py` only ever needs "script name → set of events." Duplicating ~20 lines
   of dict-walking is cheaper and clearer than generalizing a shared shape neither consumer
   fully needs.
3. **SSOT consolidation for this exact class of near-duplicate parsing/regex helpers is already
   the explicit, separately-scoped subject of this initiative's Phase E** (PR11:
   `feat/shared-repo-target-resolution`; PR12: `fix/helper-and-regex-ssot`) — folding a
   settings.json-parser merge into PR8 would be scope creep into work already planned and
   sequenced elsewhere. `hook-liveness-check.py`'s own `wired_hook_events` /
   `hook_name_from_command` are noted here as a candidate for that future consolidation pass, not
   resolved now.

## Consequences

- A wired hook that silently stops firing — the exact `post-tool-use.py` (#377) / `usage-snapshot.py`
  (#355) failure class — now has a positive, mechanical signal: within `DEFAULT_CADENCE_DAYS`, the
  next prompt in any session surfaces `[hook-liveness] N wired hook(s) have not recorded a
  heartbeat in over 7 days`, naming each one and its last-seen age (or "never recorded").
- One new file per wired hook under `~/.claude/scratch/hook-heartbeat/` (machine-local,
  never committed, matching every other sentinel/state file `_hookutil.py` and `_bash_state.py`
  already write there). No GC job is added in this PR — heartbeat files are tiny (one float each,
  ~15 bytes), overwritten in place forever (not accumulated per-session like the sentinel `.flag`
  files `cleanup_stale_sentinels` already prunes), so there is no unbounded-growth concern to
  clean up. A hook permanently removed from `settings.json` leaves an orphaned `.ts` file behind
  with no reader — cosmetic, not a correctness or growth problem; left for the scratch-GC
  follow-up (PR10, `fix/scratch-state-gc`) to sweep opportunistically if desired.
- 41 existing hook files each gain exactly one line (`_hookutil.record_heartbeat("<name>")`) as
  the first statement of `main()`, plus an `import _hookutil` where not already present. No
  behavioral change to any hook's existing logic, output, or exit code — verified by the full
  offline suite (`py -3 claude/scripts/run-hook-tests.py`) passing unchanged before and after the
  sweep, including the AST safe-exit-guard and output-contract gates (ADR-103 PR3), which treat
  the added line as inert (it is neither a stream write nor an exit).
- `DEFAULT_CADENCE_DAYS = 7` is a judgment call, not derived from data — chosen to comfortably
  exceed this repo's normal multi-day gaps between certain rare-but-legitimate code paths firing
  (e.g. `pre-merge-numbering-check.py` only does meaningful work when a numbering collision
  actually exists) while still catching a #355-shaped 8-day silence. Revisit if real-world
  false-positive/false-negative experience says otherwise.
- Verification is empirical, not just unit-tested: after a day of normal use,
  `ls ~/.claude/scratch/hook-heartbeat/` should show a fresh timestamp for every wired,
  non-exempt hook; renaming one hook's `.ts` file should produce the staleness warning on the
  next prompt (both confirmed manually during this PR — see the PR body's Testing section).

## Alternatives considered

- **A single last-invocation-time index file (one JSON object, all hooks) instead of one file per
  hook.** Rejected — every write would need to read-modify-write the whole file, reintroducing
  exactly the concurrent-write hazard the per-file design avoids for free (many hook processes
  can fire within the same turn, e.g. the PreToolUse(Bash) group's 12 hooks). One file per hook
  means each writer only ever touches its own file; no coordination needed.
- **A lock file / mutex around the heartbeat write.** Rejected — unnecessary. `os.replace` is
  atomic on both POSIX and Windows (the destination is atomically replaced, torn writes are not
  observable), and each writer's tmp file has a PID-qualified name, so two concurrent writers for
  the *same* hook name (a genuinely rare case — the same script invoked twice in true parallel)
  never collide on the tmp file itself; the final `os.replace` is a last-write-wins race with no
  corruption risk either way.
- **Derive liveness from the transcript (scan for `PostToolUse`/`Stop` attachment records, the
  approach `posttooluse-inert-advisory.py` already uses for a narrower, single-session
  "did-PostToolUse-fire-this-session" check — [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)/[ADR-055](055-reliable-event-inert-posttooluse-advisory.md)).**
  Rejected as the general mechanism — that approach is inherently per-session and scoped to
  events with transcript attachment records; it cannot answer "has this `UserPromptSubmit` hook
  fired in the last 7 days across every session," and re-parsing transcripts across sessions to
  reconstruct a cross-session heartbeat is far more expensive than a 15-byte file write per
  invocation. The heartbeat ledger and the existing inert-PostToolUse advisory solve genuinely
  different problems (this-session liveness of a `PostToolUse`-attachment-dependent hook, vs.
  cross-session/cross-time liveness of every wired hook) and are complementary, not redundant.
- **A fixed, hardcoded list of wired hook names in `hook-liveness-check.py`**, instead of parsing
  `claude/settings.json` at read time. Rejected — a hardcoded list silently drifts the moment a
  hook is added or removed from wiring (exactly the kind of copy-drift this whole initiative
  exists to close, per ADR-103's context). Parsing `settings.json` directly keeps the checked set
  always in sync with production wiring with no further edits.
- **Extract a shared production settings.json-parsing module now**, consolidating with
  `tests/_hook_wiring.py`. Deferred — see *Settings-parsing scope decision* above; this is
  explicitly Phase E's job (PR11/PR12), not PR8's.

## References

- [dev-env#377](https://github.com/brownm09/dev-env/issues/377) — `post-tool-use.py` silently
  dead for months; the primary motivating incident.
- [dev-env#355](https://github.com/brownm09/dev-env/issues/355) — `usage-snapshot.py` dark for 8
  days; the secondary motivating incident.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — `_hookutil.py`, the sibling
  module `record_heartbeat` is added to, and whose sentinel-file conventions (tmp+atomic-swap
  style, best-effort error swallowing) it follows.
- [ADR-103](103-shared-hookout-emitter.md) — the shared `_hookout` emitter
  `hook-liveness-check.py`'s advisory is routed through, and the output-contract/safe-exit gates
  (PR3) this PR's new hook and 41-file sweep must (and do) pass unchanged.
- [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) /
  [ADR-055](055-reliable-event-inert-posttooluse-advisory.md) — the narrower, per-session,
  transcript-based liveness check this ledger complements rather than replaces (see *Alternatives
  considered*).
- [dev-env#717](https://github.com/brownm09/dev-env/issues/717) — the top-level hook-reliability
  initiative issue.
- [dev-env#745](https://github.com/brownm09/dev-env/issues/745) — this sub-issue (PR8, Phase D).
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the `UserPromptSubmit`
  `additionalContext` / `systemMessage` channel semantics `_hookout.emit_advisory` relies on.

## Addendum (2026-07-13) — `/review` findings: self-check, debounce, cross-check test, structural gate

`/review` on PR #752 (this ADR's own introducing PR) surfaced four findings, all fixed in the same
PR before merge — recorded here because each changes a design decision this ADR documents, not
just an implementation detail.

**1. `hook-liveness-check.py` could go silently dark on its own settings.json parse failure
(reliability).** The original `main()` called `_hookutil.record_heartbeat("hook-liveness-check")`
first (correct — matches every other hook), then on a `SETTINGS_PATH` read/parse failure silently
`sys.exit(0)`'d. Because the heartbeat call already ran, this hook's *own* liveness ledger entry
stayed perfectly fresh even while the mechanism it implements was doing nothing — the exact
"silently dead, discovered only by accident" failure class (dev-env#377, dev-env#355) reintroduced
in the tool meant to catch it, with the one place that could not self-detect it being the one place
that mattered most. **Fix:** a self-check after parsing — if `"hook-liveness-check"` (this hook's
own name) is not present in the parsed `wired_hook_events()` result, that is itself anomalous (a
real settings.json always has this hook wired, since the hook is running), and now emits a distinct
`format_self_check_failure()` advisory (`audience="model"`) rather than a silent exit. Covers both
sub-cases: the JSON literally failing to parse, and a parse that succeeds but degrades to something
that doesn't include this hook's own wiring. Two new end-to-end tests in
`test_hook_liveness_check.py` drive both paths via a `HOOK_LIVENESS_SETTINGS_PATH` test seam.

**2. No structural enforcement that a wired hook actually calls `record_heartbeat` correctly
(maintainability).** The 41-file sweep that wired every hook into the ledger was a one-off,
uncommitted AST transformation script — correct for this PR (verified file-by-file, see the PR's
own review disposition), but nothing stops a 42nd hook added by hand, or a future edit reordering
an existing hook's `main()`, from silently omitting or misplacing the call. **Fix:** a new
structural gate, `claude/scripts/tests/test_hook_heartbeat_guard.py`, mirroring
`test_hook_safe_exit_guard.py` / `test_hook_output_contract.py`'s established pattern — an AST
check, run against every currently wired hook via `tests/_hook_wiring.py`, asserting
`_hookutil.record_heartbeat("<own-name>")` is the literal first statement of `main()` (after any
docstring). Unlike those two precedent gates, it ships with an **empty allowlist from day one**:
PR8 made every wired hook compliant in the same change that introduced the gate, so there is no
pre-existing debt to migrate — a newly wired hook must be compliant from its first commit. This
gate is also what caught `hook-liveness-check.py`'s own initial implementation using a module-level
`OWN_HOOK_NAME` variable instead of the literal `"hook-liveness-check"` in its heartbeat call — a
real, if harmless, inconsistency with the other 41 hooks' convention, fixed immediately by the
gate that exists to prevent exactly this class of drift.

**3. No regression guard against `hook-liveness-check.py`'s own settings.json parser drifting from
`tests/_hook_wiring.py`'s near-identical one (maintainability).** This ADR's *Settings-parsing
scope decision* (above) explains why the two parsers stay separate until Phase E; it did not,
originally, explain how drift between them would be caught in the meantime. **Fix:** a new test,
`test_wired_hook_events_agrees_with_hook_wiring_module`, runs both `wired_hook_events()` and
`_hook_wiring.wired_script_events()` against the **real** `claude/settings.json` (not just a shared
static fixture) and asserts their outputs agree (modulo the `.py`-suffix convention difference).
This does not eliminate the duplication the deferral accepts, but it does mean a future
settings.json schema change applied to one parser and not the other fails a test immediately,
rather than drifting silently until Phase E.

**4. No discussion of the per-prompt cost of the new `UserPromptSubmit` registration
(performance).** `hook-liveness-check.py` is a 13th script in that event's hook group (each paying
its own `pyw -3` interpreter startup — a standing per-call-startup concern this repo tracks
separately, dev-env#715), and `stale_hooks()` reads one heartbeat file per non-exempt wired hook
(~40 as of this PR) on top of the settings.json parse — real synchronous I/O that, unmitigated,
would repeat on **every single prompt in every session**, not just once per session like the
staleness signal actually needs (a 7-day cadence has no meaningful per-prompt resolution). **Fix:**
debounced to once per session via a `_hookutil` sentinel file, the same established pattern
`reconcile-open-prs.py` / `session-mode-prompt.py` already use (`_already_ran(session_id)` /
`_mark_done(session_id)`, keyed by the `session_id` the `UserPromptSubmit` payload already carries,
with a synthetic fallback key when absent). The self-check failure paths (finding 1) are debounced
identically — a broken settings.json doesn't fix itself mid-session, so re-emitting the same
diagnostic every prompt would be alert-fatigue-inducing without adding information. Four new
end-to-end tests cover the debounce (same-session second call silenced; a different session still
fires) plus the two self-check-failure paths.

None of these four changes altered the *Decision* section's design above — the heartbeat writer,
the exemption-by-event-set rule, and the settings.json-derived (not hardcoded) wired set are all
unchanged. They harden the *reader*'s (`hook-liveness-check.py`'s) own reliability, close a
maintainability gap in how the invariant is enforced going forward, and address a performance
concern the original PR body did not discuss — all found by the same review process this
initiative's other PRs (ADR-098, ADR-099, ADR-100) also relied on to catch per-site channel bugs.

## Amendment 2 (2026-10-30) — the heartbeat also records how long each fire took

The addendum above is the first change to this decision; this is the second.

**Why.** The ledger answers "did this hook fire?" but not "what did that fire cost?" The
2026-07 optimization roadmap ([dev-env#840](https://github.com/brownm09/dev-env/issues/840))
proposes gating hot-path hooks ([#849](https://github.com/brownm09/dev-env/issues/849)) and
running each event's hooks in one dispatcher process
([#851](https://github.com/brownm09/dev-env/issues/851)). Both need per-hook timing before and
after the change, and [#848](https://github.com/brownm09/dev-env/issues/848) asked for it to
come from this ledger. A 2026-10-30 measurement on the user's machine found a no-op Python
launch taking 0.5–4.3 s, because Defender and Acronis scan every process start
([#851 comment](https://github.com/brownm09/dev-env/issues/851#issuecomment-6526180164)). So
the number that matters is the launch, not the hook body, and the ledger has to measure both.

**What changed.** When the hook is the process's `__main__` script (a real launch: the
ADR-148 launcher runs it as `__main__` with its own `__file__`), `record_heartbeat` also
registers an `atexit` callback, once. At process exit it appends one JSON line to
`<ledger>/<hook>.dur.jsonl`, next to the `.ts` heartbeat:

- `run_ms`: from the heartbeat call (the first statement of `main()`) to process exit. `atexit`
  runs on a normal return, `sys.exit` and an uncaught exception alike, and doesn't change the
  exit code.
- `startup_ms`: from the launch to the heartbeat call, which covers the py launcher,
  interpreter start and the hook's module-level imports. `pyw -3` starts `pythonw.exe` as a
  child, so the launch time is the parent's creation time when the parent's image is
  `py.exe` or `pyw.exe`. Otherwise it's this process's own. Windows only, via `ctypes`
  (`GetProcessTimes`); `null` elsewhere.
- `t`: the Unix time of the write.

The `__main__` condition matters for anything that runs a hook's `main()` inside another
process: a test calling it in a loop, or the #851 dispatcher running many handlers in one
process. Arming there would record one sample spanning every later call. Those callers get the
heartbeat but no exit record. They time their own calls and use the public
`record_duration(hook_name, run_ms, startup_ms=None, heartbeat_dir=None)`. One consequence for
#851: before it, `run_ms` covers `main()` to process exit; after it, a dispatcher's per-handler
`run_ms` covers just the handler. The two are close for a hook whose only post-`main()` work is
interpreter teardown, but compare them knowingly. A new on-demand script, `hook-timing-report.py`, prints per-hook medians and 90th
percentiles from these files. The `.ts` file format and `hook-liveness-check.py` are unchanged.

**Three constraints shaped it:**

1. **Sampling the launch time.** Importing `ctypes` measured 10–75 ms on the user's machine,
   because the antivirus scans the DLL load. Paying that on every fire of every hook would add
   the overhead #851 exists to remove. So `startup_ms` is measured on about one fire in
   `STARTUP_SAMPLE_EVERY = 16`, and the rest record `null`. `run_ms` costs only the append.
   The report shows how many startup samples back each row.
2. **Bounded without a GC job.** Once a file passes `DURATION_MAX_BYTES` (256 KiB), the
   writer rewrites it to its newest `DURATION_KEEP_LINES` (2,000) via tmp + `os.replace`.
   Retention is by line count, not time: a hook that fires on every tool call keeps a day or
   two, so a baseline must be snapshotted (the report's output, pasted into the issue) before the
   change it measures ships. The report shows how many hours back each hook's data reaches.
   Appends and compaction take no lock, so concurrent fires of one hook can lose or tear a few
   lines; the report skips torn lines. When `os.replace` fails (on Windows, while another
   process holds the target open) the tmp file is removed rather than left behind. The `.ts`
   write now shares that helper, which stops the same leak there: the production ledger held
   about 400 orphaned `.ts.<pid>.tmp` files when this was written. `sweep-scratch-debris.py` stays away
   from these files, as it does from the `.ts` files.
3. **The atexit callback must never speak.** An exception escaping an atexit callback prints a
   traceback to stderr. On a blocking hook's exit 2, Claude Code would hand that traceback to
   the model as part of the block reason. The callback swallows everything. It also does
   nothing when the ledger directory no longer exists: a test that drives a hook's `main()`
   against a temporary ledger has deleted it by the time the test process exits, and
   recreating it would leave debris in `%TEMP%`.

**Known blind spot.** A fire that Claude Code kills at its hook timeout never reaches `atexit`
and writes nothing, so the 90th percentile is a lower bound on the slow tail.

**Alternatives rejected.** *Putting the duration in the `.ts` file* would change a format
`hook-liveness-check.py` parses as a bare float, for no gain. *One shared log for all hooks*
brings back the concurrent read-modify-write hazard this ADR's per-file design avoids.
*Timing from outside* (a harness that launches hooks and times them) measures a synthetic run,
not the production fires this ledger sees. It stays the right tool for a controlled
before/after, and #851 uses one, but it can't replace in-situ data. *Measuring the launch on
every fire* is the cost described in constraint 1.

Tests: [`docs/TESTING.md`](../TESTING.md) items 27 (`_hookutil`) and 104 (the report).

## Amendment 3 (2026-10-30) — tests never write the production ledger

**Why.** `HOOK_HEARTBEAT_DIR_OVERRIDE` (dev-env#1031/#1033) let a test redirect a hook's
heartbeat, but nothing required a test to use it. A probe of `record_heartbeat` found 15 test
files writing the real ledger on every suite run, 43 fires from `test_canonical_mutate_guard.py`
alone ([#1163](https://github.com/brownm09/dev-env/issues/1163)). That refreshed `.ts` heartbeats,
which can hide a hook that has stopped firing for up to the 7-day cadence, and since Amendment 2
it also mixed test fires into the timing data that #849 and #851 compare.

**What changed.** A test that runs a real hook calls `isolate_heartbeat()` from
`claude/scripts/tests/_heartbeat_isolation.py` at module level, or exports the override itself
(the bash gates). The structural gate `test_hook_heartbeat_isolation.py` fails any test file that
names a wired hook and runs it without one; its calibration is in
[`docs/TESTING.md`](../TESTING.md) item 106.

**Rejected: setting the override once in `run-hook-tests.py`.** One line, but
`test_hookutil.py`'s precedence tests pass an explicit `heartbeat_dir` and need the variable
unset, and a test run directly with `py -3` outside the runner would still leak.
