# ADR-101: Elapsed-Time-Gated cwd/Branch Drift Check on Every Bash Call

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** hooks, pre-tool-use, bash, git, drift-detection, silent-failure, elapsed-time, shared-module, adr-085-fast-follow

---

## Context

[dev-env#682](https://github.com/brownm09/dev-env/issues/682) reports that mid-session, a Bash
tool's cwd silently reverted from a worktree (`.claude/worktrees/dev-env-655`) back to the
canonical dev-env root, with no error surfaced — caught only when a later relative-path `grep`
failed. It happened immediately after two long-running (~5-7 min) background `Agent` calls
completed. No mutating command was attempted while drifted, so no harm resulted this time, but a
relative-path command run against a drifted cwd could silently hit the wrong repo/branch.

**Investigation: this is a reproducible, systemic Claude Code harness gap, not a one-off.**
[ADR-085](085-bash-repo-branch-drift-detection.md) already cites two upstream issues on this exact
symptom class:

- [anthropics/claude-code#37920](https://github.com/anthropics/claude-code/issues/37920) — an open,
  unfixed `bash.exe.stackdump` (Git Bash/MSYS2) crash; one reporter found 6 identical-signature
  stackdumps with no resource pressure mentioned.
- [anthropics/claude-code#11067](https://github.com/anthropics/claude-code/issues/11067) — the
  harness's own "Shell cwd was reset to `<dir>`" self-correction message can repeat without
  actually fixing the execution directory. Reported on **macOS**, so this class is not
  Windows/MSYS2-specific.

This investigation independently found three more upstream reports, all confirmed-but-closed
"not planned":

- [anthropics/claude-code#42837](https://github.com/anthropics/claude-code/issues/42837) — cwd
  stops persisting between Bash calls despite being configured to persist.
- [anthropics/claude-code#31471](https://github.com/anthropics/claude-code/issues/31471) — a
  `statusLine` config resets shell cwd between Bash tool calls.
- [anthropics/claude-code#30906](https://github.com/anthropics/claude-code/issues/30906) —
  `EnterWorktree` followed by session resume reverts cwd to the repository root.

Five independent reports, multiple platforms, multiple distinct trigger shapes. dev-env's own
`claude/CLAUDE.md` already documents a narrower, related case: once `EnterWorktree`/`ExitWorktree`
fires once in a session, plain `cd` stops reliably persisting for the rest of that session. And —
first-party, same-day evidence — a fresh `bash.exe.stackdump` (the exact ADR-085 crash signature)
was found sitting untracked in dev-env's own canonical checkout during this investigation, timed
plausibly (not provably) consistent with an earlier interruption of this same session's own
background research agent.

**Honest limitation, stated up front.** Some upstream reports — especially #11067 — show the
harness's *self-reported* cwd staying "correct" in the hook payload while the actual shell
execution silently drifts elsewhere. No hook that reads the payload's `cwd` field can ever detect
that sub-case; it has no visibility into where the shell process actually ran a command. What
*can* be detected is drift in the harness's own tracked/reported state across a gap between two
Bash calls — exactly what ADR-085's `_bash_state.py` mechanism already does, for three specific
commands. This decision widens *when* that comparison runs; it does not change what it is able to
see.

**Why ADR-085's existing coverage misses dev-env#682's case.** ADR-085 built the full mechanism —
`current_repo_state()`, `write_state()`/`read_state()`, `format_drift_warning()` in
`claude/scripts/_bash_state.py`, written after *every* Bash call by
`post-tool-use-cwd-track.py` — but deliberately scoped the *comparison* to three high-consequence,
command-content-gated checkpoints (`git commit` / `gh pr create` / `gh pr merge`), reasoning that
"only these commands act on stale state in a way that matters" and that running the comparison on
every Bash call would double the per-call subprocess cost. dev-env#682 is new evidence that this
scope leaves a real gap: a plain `grep`, test runner, or build command run against a silently
drifted cwd is also dangerous, and today gets zero drift-visibility.

## Decision

Add a fourth checkpoint, `claude/scripts/pre-bash-drift-check.py` (`PreToolUse`, `Bash` matcher),
that fires on *every* Bash call but gates its own `git rev-parse` subprocess cost on **elapsed
time** since the last recorded Bash state, rather than on command content:

1. Read stdin JSON; bail (exit 0) on a non-`Bash` tool, or an empty `session_id`/`cwd`.
2. `age = _bash_state.state_age_seconds(session_id)` — a new helper: stats the existing per-session
   state file's mtime, returns `time.time() - mtime`, or `None` if the file doesn't exist yet
   (first Bash call of the session). Pure file stat, no subprocess.
3. `should_check_drift(age, MIN_GAP_SECONDS=60.0)` — pure gate function: `False` for `None` or any
   `age <= 60`, `True` otherwise. Back-to-back Bash calls — the overwhelming majority in any
   session — return `False` here and the hook exits immediately, no subprocess spawned.
4. Only when the gate passes: `_bash_state.drift_warning_for(session_id, cwd)` — a new wrapper
   (see Judgment calls) combining the same `current_repo_state()`/`read_state()`/
   `format_drift_warning()` sequence ADR-085 already uses — then print
   `{"systemMessage": "[bash-drift-check] ..."}` if the returned warning is non-`None`. Exit 0
   always — advisory only, never blocks, matching ADR-085's other three checkpoints and its stated
   reasoning (the mechanism cannot distinguish a legitimate `EnterWorktree`/`cd` switch from a
   silent crash-induced revert).

This works correctly for the observed trigger shape specifically because `PreToolUse` fires
*before* the gap-ending call's own `PostToolUse` write can overwrite ("launder") the comparison
forward — the recorded state this hook compares against is the pre-gap, pre-drift baseline, not
state a later call already refreshed. See Judgment calls for the precision limits of this.

**Wired** as the 11th (last) entry in the existing `PreToolUse` → `"matcher": "Bash"` array in
`claude/settings.json`, appended after `disk-space-check.py` rather than grouped with the other
`_bash_state.py`-family hooks (`pre-commit-branch-check.py`, `pre-pr-create-check.py`,
`pre-merge-branch-check.py`, which sit inside an unrelated `gh pr merge`-triggered cluster at
positions 3-7). `disk-space-check.py` is the better neighbor: it shares this hook's actual trigger
*shape* — unconditional on every Bash call, cheap internal self-gate, no command regex — which is
what the existing array's clustering is actually organized around, not which shared module a hook
imports.

## Judgment calls

### A new ADR, not an "ADR-085 Amendment"

The closest precedent for "extending ADR-085's coverage" is
[ADR-087](087-pretooluse-disk-space-check.md), which extends `disk-space-check.py` to a second
`PreToolUse(Bash)` registration — a fast-follow *explicitly named in ADR-085's own Context
section*, the same relationship this decision has to ADR-085. ADR-087 still shipped as a
standalone new ADR file, not an amendment; ADR-085 itself was never edited for it. This repo's
actual "amendment" convention ([ADR-050](050-shared-hookio-sibling-hook-fixes.md),
[ADR-071](071-canonical-checkout-mutate-guard-hook.md)'s two amendments) is reserved for extending
an *already-shipped hook's own file* in place — same mechanism, same file, a wider net on the same
decision. This decision ships a new hook file, new settings wiring, and a new test file — the same
shape ADR-087 was — so it gets its own ADR number rather than growing ADR-085's.

### The comparison always launders against the immediately-preceding write — inherited, not introduced

`format_drift_warning` always compares "current" against whatever `post-tool-use-cwd-track.py`
wrote after the *immediately preceding* Bash call, never against some earlier known-good baseline.
This is a pre-existing property of the whole ADR-085 family: if a drift happens, one ordinary
non-checkpoint call runs, then `git commit` runs, `pre-commit-branch-check.py`'s comparison sees
`recorded` (already post-drift, from the intervening call) vs. `current` (also post-drift) — no
difference, false negative. This decision's elapsed-time gate correctly catches the *specific*
shape dev-env#682 describes (a long gap, then the first call after it) precisely because
`PreToolUse` fires before that first call's own `PostToolUse` write launders the record forward.
What it does **not** catch: a drift with no accompanying time gap, immediately followed by a rapid
burst of sub-60s-spaced calls — each call re-arms the write before the age gate would ever trip,
and the drift stays invisible until either a command-content checkpoint fires or a later gap
occurs. This is a different shape of the same limitation ADR-085 already accepted ("does not catch
every case"), not a new one this decision introduces.

### 60-second threshold: a margin-of-safety default, not fit to the one observed incident

The dev-env#682 incident's gap was 5-7 minutes; 60 seconds leaves large margin without hardcoding
to that one incident's specific duration — the five upstream reports researched above show several
different trigger shapes, not all necessarily duration-matched to this one. The threshold trades
off *subprocess-call frequency* against *detection latency*, not false-positive rate:
`format_drift_warning` still no-ops on "no repo/branch change" regardless of how eagerly the gate
fires, so a smaller number only costs more `git rev-parse` calls, never more spurious warnings.

### Broadening from 3 rare checkpoints to every post-gap call raises how often the warning fires on a *legitimate* change — traced, and found self-limiting

`/review` flagged that widening the comparison from 3 command-gated moments to "every Bash call
after a ≥60s gap" gives a deliberate `EnterWorktree`/`cd` or `git checkout`, followed by any
qualifying gap (a long build, a run of Read/Grep/Edit calls, an Agent call), far more
opportunities to trip the advisory than the 3 original checkpoints ever had — risking alarm
fatigue that could dull the signal exactly when a real revert occurs.

Traced through rather than dismissed: `post-tool-use-cwd-track.py` still rewrites the recorded
state after *every* Bash call, unconditionally, independent of this hook's own gate. So the
first Bash call after a legitimate switch-plus-gap does trip one warning (correct — this is
the same "verify branch" prompt ADR-085 already asks for, now automatic) — but that same call's
own `PostToolUse` write immediately re-baselines the record to the new, now-current state. A
second call, even after another ≥60s gap, compares current-vs-current and sees no drift. The
broadened surface produces **one** advisory per genuine transition, not a recurring nag for a
stable, unchanged intentional state — the every-call gate changes how many transitions get
noticed, not how many times an unchanged state gets re-flagged. Considered, and not built:
suppressing the warning specifically when the *current* state resolves to a `.claude/worktrees/`
path (a cheap, plausible "this looks like a deliberate switch" signal) — asymmetric in exactly
the direction that matters, since it would only ever suppress the *safe* direction (into a
worktree) and never weaken the warning for the *dangerous* one (worktree silently reverting to
canonical, dev-env#682's actual shape) — but not implemented here: the self-limiting property
above already keeps the practical false-positive rate low without it, and adding an unproven
heuristic + its own test surface isn't justified against a cost this trace shows is smaller than
first feared. Worth revisiting only if real-world use shows otherwise.

### Rejected alternative: a separate, dedicated throttle-marker file

Considered mirroring `disk-space-check.py`'s own `_marker_path`-per-band pattern with a dedicated
"last drift-checked" marker, decoupled from `bash_state.json`'s per-call rewrite, specifically to
try to close the rapid-fire-after-drift gap named above. Rejected after tracing it through: the
comparison still launders against whatever the immediately-preceding call wrote regardless of how
the "when to check" gate is implemented, so a second marker buys no additional detection power —
only extra complexity, plus an awkward semantic inversion (a *missing* throttle marker would have
to mean "check now," the opposite of what a missing `bash_state.json` means everywhere else in this
hook family).

### New file, not folded into `post-tool-use-cwd-track.py` or an existing checkpoint hook

`post-tool-use-cwd-track.py`'s own module docstring states its contract directly: *"Exit 0
always — pure side-channel recording, never blocks or messages."* Folding a conditional
`systemMessage` print into it would break that documented contract for the first time — the same
shape of argument ADR-085 itself already used to justify keeping `pre-merge-branch-check.py`
separate from `pre-merge-message-check.py` ("a different mechanism, a different output channel...
would require... awkwardly splicing two unrelated response styles into one script"). Folding into
any of the three existing checkpoint hooks has an additional problem: `pre-commit-branch-check.py`
firing generically on an unrelated `ls` because 60 seconds elapsed would be actively misleading
given its name and its sole, documented trigger (`git commit`). A fourth sibling, differing only in
its trigger *axis* (elapsed time vs. command content), matches this repo's one-hook-per-concern
convention (see ADR-024/ADR-071's identical reasoning for staying separate files despite both being
worktree/canonical-root guards).

### `drift_warning_for()`: closing a second layer of duplication `/review` caught

Adding this hook made the `current_repo_state()` → `read_state()` → `format_drift_warning()`
three-call orchestration sequence duplicated, verbatim, across all four checkpoint hooks — `/review`
pointed out this is exactly the class of risk `_bash_state.py`'s own module docstring already
names as having caused one real bug during ADR-085's own development (a display-placeholder
return value in one copy that didn't match the others' `None` convention). Consolidating
`current_repo_state()` itself didn't close this: the *orchestration around it* was still
re-composed by hand at every call site. Fixed by extracting `_bash_state.drift_warning_for(session_id,
cwd) -> (repo_root, branch, drift_warning)` and updating all four consumers —
`pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-branch-check.py`, and this
hook — to call it instead. `repo_root`/`branch` are still returned (not just the warning) because
three of the four consumers show "current branch/repo" as their own primary message content,
independent of whether there's drift.

### Edge cases in `state_age_seconds`

Missing state file (a session's first Bash call) → `None`, treated as "skip" by the caller — this
also matches `read_state`'s existing None-on-missing convention and avoids a wasted subprocess call
on every session's opening command. A future/skewed mtime (clock resync, NTP correction, or
tampering) is not special-cased: `time.time() - mtime` simply yields a negative float, which
`should_check_drift` treats the same as "too recent to check" — failing toward *not* checking,
consistent with this whole hook family's fail-open ethos. An `OSError` on `.stat()` is swallowed
and returns `None`, mirroring `write_state`'s existing `except OSError: pass` pattern. No race
condition exists between concurrent Bash calls updating/reading this file within one session — Bash
tool calls in a Claude Code session are processed sequentially, the same property ADR-087 already
relies on for its own per-session marker-file gate.

## Consequences

- **A drift affecting any Bash command — not just `git commit`/`gh pr create`/`gh pr merge` — now
  gets a visible, advisory warning**, at the first opportunity after a ≥60s gap, directly answering
  dev-env#682's "a relative-path command could silently hit the wrong repo/branch" concern for the
  gap-shaped trigger class it observed.
- **Does not fix the root cause.** The upstream Claude Code cwd-tracking/crash behavior is
  unchanged and outside this repo's reach — this is a symptom-visibility mitigation, the same
  framing ADR-085 already established and explicitly does not claim to be a cure.
- **Does not catch every case** — see the "comparison always launders" and "honest limitation"
  points above. A rapid-fire-after-drift sequence with no gap, or a drift where the harness's own
  reported `cwd` field never itself changes, both stay invisible to this mechanism by construction.
- **Testing.** `claude/scripts/tests/test_bash_state.py` gains four cases for the new
  `state_age_seconds()`: missing file → `None`; a file aged ~90s into the past → age ≈ 90; a future
  mtime → negative age, no raise; an unstattable scratch path → `None`, no raise — plus a case for
  `drift_warning_for()` confirming it composes the three underlying calls correctly (a drift-free
  round trip returns the current `(repo_root, branch)` with `None` for the warning; an empty
  `session_id` still resolves `(repo_root, branch)` but always returns `None` for the warning,
  matching every consumer's pre-existing `if session_id:` guard).
  `claude/scripts/tests/test_pre_bash_drift_check.py` (new) pins `should_check_drift`'s five
  decision cases (`None`, below/at/above the threshold boundary, negative) and `build_message`'s
  tagging — pure-function-only, matching this hook family's established convention (`main()`'s I/O
  and `current_repo_state()`'s subprocess call are not separately unit-tested, same scope note as
  `pre-commit-branch-check.py`/`pre-pr-create-check.py`/`pre-merge-branch-check.py`'s own test
  files). `format_drift_warning` itself is not re-tested here — already fully covered by
  `test_bash_state.py`.
- **Observability.** Advisory `PreToolUse` hook → exit 0 + `{"systemMessage": ...}` on stdout,
  the same, already-proven pattern `pre-commit-branch-check.py` uses (confirmed working before
  reuse, matching ADR-087's own verification step). Includes the documented safe-exit guard
  (`try/except Exception: sys.exit(0)`) — notably, two of the three existing sibling checkpoint
  hooks omit this guard; this new file does not repeat that gap.
- **Security.** N/A — no new secret handling or authz surface; reads only git state already
  visible in the session transcript, same as ADR-085.
- **Resilience / failure modes.** Fails open on every path: malformed JSON, missing
  `session_id`/`cwd`, a `git` call that fails or times out, an unstattable state file — all degrade
  to "no comparison possible" rather than raising, matching this hook family's established
  convention.
- **Performance.** Zero added **`git`** subprocess cost for the overwhelming majority of Bash calls
  (those under 60s of the prior call) — the gate itself is a single file `.stat()`. Only a call
  following a genuine ≥60s gap pays one additional `git rev-parse` subprocess (~10-30ms with
  `CREATE_NO_WINDOW`), on top of the one `post-tool-use-cwd-track.py` already spawns unconditionally
  on every call. `/review` correctly noted this is narrower than "zero added cost" outright: the
  `pyw -3` interpreter launch to *run* this hook at all is itself an unconditional per-call
  subprocess spawn — the 11th such spawn already present in the `PreToolUse`/`Bash` array before
  this PR, not a cost this decision introduces, but real and larger on Windows than the `git` call
  the gate avoids. Amortizing one interpreter startup across the whole `_bash_state`-family hook
  array (a single dispatcher instead of N separate `pyw -3` invocations) would address it, but
  reaches all 10 pre-existing entries in that array, not just this one — out of scope for this PR;
  tracked as a follow-up rather than built speculatively here.
- **Data integrity.** N/A — read-only against the existing per-session state file; this hook never
  writes to it.
- **ADR warranted** because this introduces a new hook script wired into `claude/settings.json` and
  a new shared-module function — the same warranting shape as ADR-085/ADR-087/ADR-071.

---

## References

- `claude/scripts/pre-bash-drift-check.py` — implementation
- `claude/scripts/_bash_state.py` — extended with `state_age_seconds()` and `drift_warning_for()`
- `claude/scripts/pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-branch-check.py`
  — updated to call `drift_warning_for()` instead of re-composing its three-call sequence
- `claude/scripts/tests/test_pre_bash_drift_check.py`,
  `claude/scripts/tests/test_bash_state.py` — test coverage
- `claude/settings.json` — hook wiring (11th entry in the `PreToolUse` → `Bash` matcher array)
- `claude/CLAUDE.md` — extended "Verify branch before editing and before every commit" bullet
- [dev-env#682](https://github.com/brownm09/dev-env/issues/682) — motivating issue
- [ADR-085](085-bash-repo-branch-drift-detection.md) — the mechanism this decision extends
  (`_bash_state.py`, the three command-content-gated checkpoints), and its Context section's own
  citations of [anthropics/claude-code#37920](https://github.com/anthropics/claude-code/issues/37920)
  and [#11067](https://github.com/anthropics/claude-code/issues/11067)
- [anthropics/claude-code#42837](https://github.com/anthropics/claude-code/issues/42837),
  [#31471](https://github.com/anthropics/claude-code/issues/31471),
  [#30906](https://github.com/anthropics/claude-code/issues/30906) — three further confirmed,
  closed-as-not-planned cwd-persistence regressions found during this investigation
- [ADR-087](087-pretooluse-disk-space-check.md) — the precedent for "fast-follow of an ADR-085
  Context-section-named gap ships as its own ADR, not an amendment," and for a `PreToolUse(Bash)`
  hook gating its own work on a cheap per-call check
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the "new script, not folded into an
  existing hook" precedent this decision follows for the same reasons
- [dev-env#573](https://github.com/brownm09/dev-env/issues/573) — the original motivating issue for
  ADR-085's mechanism
- [Claude Code Hooks documentation](https://code.claude.com/docs/en/hooks) — `PreToolUse` payload
  schema (`cwd`, `session_id`) and exit-code/output contract
