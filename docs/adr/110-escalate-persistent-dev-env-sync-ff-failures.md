# ADR-110: Escalate Persistent (Multi-Session) dev-env-sync Fast-Forward-Pull Failures

**Date:** 2026-07-16
**Status:** Accepted
**Tags:** hooks, UserPromptSubmit, dev-env-sync, escalation, persistence, scratch-state, silent-failure, claude-facing, adr-098, adr-006

---

## Context

`dev-env-sync.py` is the `UserPromptSubmit` hook that fast-forward-pulls the canonical dev-env
checkout (`C:/Users/brown/Git/dev-env`) to `origin/main` on every prompt, so the `~/.claude/`
symlinked/junctioned `CLAUDE.md`, hooks, and scripts always reflect the latest merged tooling.
It always exits 0 (never blocks the prompt). Since [ADR-098](098-dev-env-sync-advisories-to-stdout.md)
every advisory prints to **stdout** (the model-visible channel for an exit-0 `UserPromptSubmit`
hook), so a `git pull --ff-only` failure is at least *visible* every prompt.

But "visible every prompt" is not the same as "noticed." When the working tree has a **dirty
tracked file that an incoming commit also touches**, `git pull --ff-only` refuses (it would
clobber uncommitted work) and the hook emits a **same-severity, every-prompt warning** — byte-for-byte
identical whether this is a one-off transient or has been failing for days. That single
severity has now let the canonical silently fall many commits behind **twice**, with the same
blast radius each time: because `~/.claude/scripts/` and `~/.claude/settings.json` are junctioned
to this checkout's working tree, **every merged dev-env fix is inert on the machine** for the
whole duration of the staleness.

- **[dev-env#697](https://github.com/brownm09/dev-env/issues/697)** (2026-07-10): 21 commits /
  ~41h behind, blocked by a dirty `claude/skills/sources.md` (uncommitted `/research` output). The
  fast-forward failed on *every single prompt of every session* for ~41 hours; the identical warning
  simply blended into the noise. Among the 21 inert commits was PR #661 — the
  `journal-canonical-guard.py` hook whose absence a *separate* investigation that same day traced
  back to this exact staleness.
- **[dev-env#795](https://github.com/brownm09/dev-env/issues/795)** (2026-07-16): 5 commits behind,
  blocked by a dirty `claude/skills/journal-compose/SKILL.md` — the second occurrence of the
  identical pattern, six days after the first.

#697's "proposed fix #2" named exactly this escalation idea and was never implemented; this ADR
implements it. The failure class is fundamentally one of **persistence**, and the hook had no
notion of it: a dirty-file `--ff-only` conflict *never* self-heals (unlike a transient
concurrent-pull race, which resolves to "already up-to-date" on the very next prompt), yet the
warning that a genuinely-stuck canonical produces is indistinguishable from that transient blip.

## Decision

Give the hook a notion of **persistence** and escalate a failure that is not a one-off, while
keeping ADR-098's stream/exit contract (stdout, exit 0, never blocking) and its plain-`print`
mechanism unchanged.

- **Track failures in a single repo-level scratch state file**
  `~/.claude/scratch/dev_env_sync_ff_failure.json`, holding `first_failure_at`,
  `consecutive_count`, and `last_failure_at`. The file is **repo-level, not per-session** — the
  central decision. The whole point of the issue is *multi-session* persistence; a per-session
  state file (the `_bash_state.py` / sentinel convention) would reset on every new session and
  never accumulate, so the two real incidents (which spanned many sessions) would never have
  escalated. The state's *storage convention* is split across the two established sibling
  patterns (a `/review` finding on PR #800 corrected an earlier draft that wrongly attributed
  the atomic write to `_bash_state.py`): the **read** path mirrors `_bash_state.read_state`
  (best-effort, malformed/non-dict/non-UTF-8 JSON read back as `None` → fresh run, `scratch=`
  injection for offline tests), while the **atomic tmp-file + `os.replace` write** mirrors
  `_hookutil.record_heartbeat` — a per-PID tmp keeps two racing writers from clobbering each
  other, and the atomic swap keeps a concurrent reader from ever seeing a torn file (which would
  otherwise decode to `None` → a spurious fresh run that *overwrites* the accumulated state).
  `_bash_state.write_state` is a *direct, non-atomic* `write_text`, which this deliberately does
  not follow. Only the *keying* differs from both (one file for the canonical, not one per
  session).

- **Update the state on the fast-forward-pull-failure branch; clear it on every determinate
  not-actively-failing outcome** — already-up-to-date, a successful pull, a **diverged** local
  `main`, and an **off-`main`** canonical (the topology block's entry). Only the *transient*
  fetch-failure early exit (a network blip, no determinate verdict) leaves the state untouched,
  so `consecutive_count` counts *consecutive fast-forward-pull failures specifically*. This is
  what makes the counter self-cleaning against the transient case: a concurrent-session pull that
  momentarily loses the `--ff-only` race resolves to "already up-to-date" on the next prompt,
  which clears the state; only the genuinely-stuck dirty-file case keeps failing and accumulates.
  Clearing on diverged/off-`main` (added after a `/review` finding on PR #800) closes a
  stale-timer edge: a resolved failure whose resolution passed through one of those determinate
  states could otherwise leave an old `first_failure_at` on disk, making a *later, unrelated*
  failure escalate immediately with a bogus multi-hour duration. A genuinely still-dirty file
  simply re-accumulates a fresh run once the canonical is back on `main` and the pull fails again
  — a conservative under-report, the right bias for an escalation signal. The one residual (a
  resolution immediately followed by a sustained fetch-outage before any clearing prompt) is a
  deliberately-accepted, extremely narrow over-report on an advisory-only signal that still names
  the correct blocking file and behind-count.

- **Escalate when `consecutive_count >= 3` OR the failure has persisted `>= 2h`** (OR semantics,
  matching the issue's "≥ N prompts or ≥ M hours"). Thresholds are module constants
  (`ESCALATE_AFTER_CONSECUTIVE_FAILURES`, `ESCALATE_AFTER_HOURS`) passed as parameters to the
  pure `should_escalate()` decider (so boundary tests are exact and offline). Below threshold the
  hook prints the existing one-off `format_pull_failure_message` unchanged (a true transient stays
  quiet-ish); at/over it prints the new **`format_escalated_pull_failure_message`**, which
  prominently names: the consecutive-prompt count and duration, the commits-behind count + local/
  remote short SHAs, the **STALE-tooling blast radius** stated explicitly (`~/.claude/` is
  junctioned to this working tree), the **blocking file path(s)** parsed from git's own stderr
  (`parse_blocking_files`, handling both the "local changes" and "untracked files" abort shapes),
  a remediation pointer to the #697/#795 precedent, and finally git's own diagnostic verbatim.

- **Keep plain `print()` to stdout + exit 0 — deliberately not `_hookout.emit_advisory`.**
  [ADR-103](103-shared-hookout-emitter.md) consolidated the output-contract table into
  `_hookout`, and `_hookout.emit_advisory("UserPromptSubmit", text, audience="model")` would be a
  valid model-visible channel. But it emits the JSON `hookSpecificOutput.additionalContext`
  envelope, which [ADR-098](098-dev-env-sync-advisories-to-stdout.md) **explicitly considered and
  rejected** for this file, in favor of matching the file's own pre-existing, proven-working
  plain-text convention (the #694 reporter quoted the plain-text success message back verbatim —
  empirical proof it reaches the model). Reversing that accepted decision is out of scope for an
  escalation feature and would need its own ADR; introducing a second output mechanism into the
  same file (some messages `print`, one via `emit_advisory`) would make it internally inconsistent
  for no functional gain. The escalated message therefore honors ADR-098's *contract* (stdout,
  exit 0, ASCII-safe) via the same `print()` the other six advisories use. It is written pure-ASCII
  (no em dash, no smart quotes) and pinned `.isascii()` in tests, so it survives Claude Code's
  cp1252 hook-output pipe on Windows without relying on the JSON channel's `ensure_ascii` escaping.

- **Concurrency: detect, don't lock** (consistent with ADR-098's same stance on the shared
  canonical). Two sessions' `UserPromptSubmit` hooks can race the shared state file. The write is
  atomic (per-PID tmp + `os.replace`) so a racing read never sees a torn file, and a malformed
  read fails to `None` (fresh run). A lost `consecutive_count` increment only *undercounts* — it
  can delay escalation slightly but never false-trigger it — and the **time arm is robust to it
  entirely**: `first_failure_at` is set once and elapsed time accumulates regardless of how many
  increments race away, so a persistent failure still escalates on schedule. No lock file is
  introduced (same reasoning as ADR-098: the race is rare and benign, and a proper advisory lock
  is a materially larger change than this warrants).

- **Cleanup:** a best-effort `_hookutil.cleanup_stale_sentinels(FAILURE_STATE_PREFIX, ext=".json")`
  sweep runs at the top of `main()` as a 30-day backstop. The state file normally self-clears on
  the next healthy pull, so this only matters for a machine abandoned mid-failure and never prompted
  again for 30+ days.

### Scope

Deliberately limited to the fast-forward-pull-failure branch of `dev-env-sync.py` — the exact
condition both incidents hit. The other advisory conditions (off-`main` topology, divergence) are
distinct, rarer, and already surface their own specific warnings; they are not folded into the
persistence counter. `journal-canonical-guard.py` (the sibling `UserPromptSubmit` hook) is not
touched — it has no analogous per-prompt-repeating silent-failure mode.

## Consequences

- A genuinely-stuck canonical now escalates to a visually distinct, self-diagnosing advisory
  within a few prompts (or ≤2h), instead of an indefinite run of identical same-severity warnings —
  closing the gap that let the canonical drift 21 commits / ~41h and 5 commits before anyone acted.
- A true one-off transient (a momentary concurrent-pull race) is unaffected: it resolves on the
  next prompt and clears the state before the count ever reaches the threshold, so the escalation
  never cries wolf.
- Two new best-effort scratch I/O operations (one read, one write) on the already-rare
  `local != remote` path, plus two cheap globs (`.json` + `.tmp` cleanup) per prompt. Negligible
  relative to the `fetch`/`pull` subprocesses already run.
- The escalation is advisory-only (exit 0), so it never blocks a prompt even when the canonical is
  badly stale — matching the hook's standing "Exit 0 always" contract.
- A new machine-local scratch file family (`dev_env_sync_ff_failure*.json`) exists; it is ephemeral,
  self-clearing, and swept after 30 days, so it needs no migration and leaves no durable state.
- **Review hardening (PR #800 `/review`, two independent opus reviewers).** Beyond the two
  design-level changes folded into Decision above (attribution correction; clear-on-diverged/off-`main`),
  the review drove five implementation-robustness fixes, all with tests: the escalated message guards
  `behind == 0` against the "0 commits behind … STALE" self-contradiction PR #701 fixed in the sibling
  formatters; both failure formatters `ascii_sanitize` the echoed `git_stderr` so a non-cp1252 filename/
  locale can't `UnicodeEncodeError` the advisory away (the exact ADR-098 cp1252 class); `read_failure_state`
  catches `UnicodeDecodeError` (a `ValueError`) so an externally-corrupted state file degrades to a fresh
  run rather than defeating the feature; the escalate-vs-plain decision was extracted into the pure,
  unit-tested `build_failure_response` helper (the state machine is no longer only exercised through the
  git-shelling `main()`); and a second `cleanup_stale_sentinels(ext=".tmp")` sweep reaps an orphaned
  atomic-write tmp the `.json` glob couldn't match (the dev-env#768 debris class).

## Alternatives considered

- **Per-session state file (the `_bash_state.py` / sentinel convention).** Rejected as the core
  design error it would be: the issue is explicitly about *multi-session* persistence, and a
  per-session file resets every session, so neither real incident (both spanning many sessions)
  would ever have accumulated enough to escalate.
- **Migrate the whole file to `_hookout.emit_advisory`.** Rejected — it would reverse ADR-098's
  explicit, reasoned rejection of the JSON `additionalContext` envelope for this file without its
  own superseding ADR, and mixing `print` + `emit_advisory` in one file adds inconsistency for no
  functional gain. The escalation honors ADR-098's contract via the same `print()` mechanism the
  file already uses. (If a future initiative migrates *all* of this file's advisories onto
  `_hookout` uniformly, that is the right moment to revisit ADR-098's channel choice — as one
  decision, in one ADR.)
- **Block the prompt (exit 2) once escalated, to force action.** Rejected — a stale canonical is a
  state the user needs to *know about*, not one that should erase their in-flight prompt; this
  matches ADR-098's identical rejection of blocking for this hook's advisory conditions.
- **Escalate on the very first failure.** Rejected — it would fire on genuine transient
  concurrent-pull races (which self-heal on the next prompt), reintroducing exactly the
  cry-wolf noise the persistence gate exists to avoid. Three consecutive failures (or 2h) reliably
  distinguishes a stuck dirty-file conflict from a momentary race.
- **A lock file to serialize concurrent `dev-env-sync.py` invocations.** Rejected as out of scope,
  identically to ADR-098: the race is rare and benign here (the time arm is robust to lost
  increments), and a proper advisory lock is a larger change than this warrants.

## References

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the exit-code /
  stdout-vs-stderr / per-event-type semantics the stream/exit contract relies on.
- [ADR-098](098-dev-env-sync-advisories-to-stdout.md) — the stdout-not-stderr fix and plain-`print`
  channel choice this ADR extends (and whose `_hookout`-envelope rejection it honors).
- [ADR-006](006-dev-env-sync-on-every-prompt.md) — why this hook runs on every prompt.
- [ADR-103](103-shared-hookout-emitter.md) — the `_hookout` output-contract emitter this ADR
  deliberately does *not* adopt here, and why.
- [dev-env#797](https://github.com/brownm09/dev-env/issues/797) — the issue this ADR closes.
- [dev-env#697](https://github.com/brownm09/dev-env/issues/697) — the first incident (21 commits /
  ~41h) and the origin of "proposed fix #2" this ADR implements.
- [dev-env#795](https://github.com/brownm09/dev-env/issues/795) — the second incident (5 commits),
  the recurrence that motivated finally implementing the escalation.

## Amendment 1 (2026-10-10): escalate the off-main states too, and tell the user

**Incident** ([dev-env#1140](https://github.com/brownm09/dev-env/issues/1140)). On 2026-10-09 the
canonical checkout was found on `config/always-plan-rule`, 266 commits behind `main`. Its raw
`.git/logs/HEAD` is one unbroken chain from a direct commit in the canonical on 2026-06-23 to the
recovery checkout on 2026-10-09, so HEAD did not move for 108 days. For all of that time the hook
running was the June copy of this script, because `~/.claude/scripts/` is junctioned to the stale
working tree. That copy reached the `warn-dirty` branch on every prompt (the app kept dirtying
the then-tracked `claude/settings.json`) and printed the warning to stderr, where an exit-0
`UserPromptSubmit` hook is invisible. ADR-098 had already fixed exactly that, on 2026-07-10, but
the fix lived in commits the stuck checkout could never pull.

**What this ADR left uncovered.** The escalation above covers only a failing fast-forward on
`main`. The off-main states (`warn-dirty`, `warn-squatter`, an unreadable worktree list, a failed
auto-return) still produced one same-severity line on every prompt, forever, with no duration and
no commits-behind count, because that path returns before it fetches. That is the very
"visible every prompt is not the same as noticed" failure this ADR was written to end.

**Decision.**

1. **Measure the off-main states, with local git only.** Report how far HEAD is behind
   `origin/main` (`rev-list --count HEAD..origin/main`) and how long ago HEAD last moved (the
   newest reflog entry, `git reflog show -1 --date=unix --format=%gd HEAD`). The time since the
   last move is a lower bound on time off `main`.
   - There is deliberately **no fetch** on this path. On Windows a fetch can hang past any
     per-call timeout, on a credential prompt or a grandchild process holding the pipe. The
     harness would then kill the hook and lose the very warning this path exists for.
   - The shared `.git`'s `origin/main` is kept current by every worktree session's fetches. A
     stale one can only under-count.
   - Both measurements are stateless, so the off-main path needs no scratch file and no
     cross-session bookkeeping.
   - Each measurement fails on its own. A failure reads "unmeasured", never a false 0, and never
     drops the warning.
2. **Escalate** to a distinct `STALE CANONICAL` advisory once HEAD hasn't moved for >= 2h **or**
   the checkout is >= 10 commits behind.
   - The 2h reuses this ADR's time arm. The 10-commit arm catches a branch that keeps getting
     commits, which reset the reflog clock.
   - An unmeasured arm never escalates by itself.
   - A **measured 0 behind** suppresses the time arm. A branch cut from the current `origin/main`
     serves current tooling, so an alarm there would contradict its own "0 commits behind". It
     escalates on the first prompt after `origin/main` moves on.
3. **Escalations reach the user, not only the model.** The channel is chosen **per run, not per
   advisory**. The hook collects everything a run says, and once anything has escalated (this
   ADR's PERSISTENT FAILURE or the new STALE CANONICAL), the run emits one JSON object:
   - **Model:** the full text of every advisory in the run, as `additionalContext`.
   - **User:** only a **one-line summary** of each escalation, as `systemMessage`. A
     several-line notification repeated on every prompt would train the reader to skip it.

   When nothing escalated, the run keeps ADR-098's plain stdout text. That text is now
   `ascii_sanitize`d, because one non-cp1252 character (a commit subject with an arrow) would
   otherwise raise and lose the whole batch. The plain off-main warnings also carry the new
   measurement line.
4. **One shared time limit for the whole run** (the `session-start-sync.py` pattern), with network
   calls made non-interactive (`GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`). Output is
   emitted once, at the end, so a run the harness kills loses everything it collected. No
   combination of git calls may approach the hook's 30s timeout. The crash fallback emits only
   its own line, and only if nothing has been written yet. A second write would put two JSON
   objects on stdout.

**This revises the channel choice ADR-098 and this ADR made.** Both rejected the JSON envelope
"for no functional gain." There is a gain now. Plain stdout cannot reach the user at all, and
whether the model relays a warning depends on what the model is doing. An autonomous or
background session working on another repo may never mention it. The model-visible channel does
work when the model notices it: #1049's PERSISTENT FAILURE was seen and filed. The user channel is
the backstop for when it doesn't. `session-start-sync.py` (ADR-130) already sends every other
repo's drift advisory to both audiences. It skips dev-env on purpose, deferring to this hook, so
dev-env alone was getting the weaker treatment.

The other objection, that the file would end up with two output mechanisms, is answered
structurally. `main()` no longer prints. It collects every advisory into an `Output` and leaves in
one emission, and `render_output()` picks the channel for the whole batch. Mixing plain text and
JSON on stdout would be worse than inconsistent: Claude Code parses stdout as JSON only when all
of it is JSON.

**Residual risk, accepted.** The detector still lives in the tree it monitors. A drift onto a
branch older than this amendment runs that branch's own copy of the hook. Since ADR-139 that case
is no longer silent, for an unrelated reason: the machine-local settings wire hooks whose scripts
an old tree lacks, `pyw` exits 2 on a missing script, and so matching prompts and tool calls are
blocked. That is loud but disruptive, and whether a missing hook script should fail open is a
separate question. The two causes of this incident are now closed directly: ADR-071 blocks
`checkout`/`commit` in a canonical checkout, and ADR-139 means the app no longer dirties a tracked
file.

**Alternatives considered.**

- **Block the prompt (exit 2) after N days off `main`.** Rejected for the reason given in the
  section above and in ADR-098: blocking erases the user's in-flight prompt for a state they
  can't fix from inside that prompt. A `systemMessage` on every prompt is unmissable without
  being destructive.
- **Track off-main duration in a scratch state file, like the ff-failure arm.** Rejected. The
  reflog already records when HEAD moved, survives concurrent sessions with no race, and needs no
  cleanup.
- **An out-of-tree canary**, an inline hook command in `settings.shared.json` that doesn't depend
  on the junctioned scripts, so a stale tree can't disable it. Rejected for now. It would break
  the `pyw -3 <script>.py` invocation form that the wiring lint, the heartbeat ledger and ADR-007
  all assume. And it would still be wiped by any post-ADR-139 branch's own settings sync. The
  ADR-071 and ADR-139 fixes remove the causes it would guard against.

**Update (2026-10-10): the missing-script half of the residual risk is closed by
[ADR-148](148-missing-hook-script-fails-open-via-launcher.md).** Every hook now runs through
`~/.claude/hook-launch.py`, a launcher installed outside the junction, which fails open (exit 0
plus a `systemMessage`) when a wired script is missing, so a drift onto an older tree yields
warnings rather than blocked prompts and tool calls. The sync also refuses to wire hooks whose
scripts are absent. The detector itself still lives in the tree it monitors; that part of the
residual risk is unchanged.
