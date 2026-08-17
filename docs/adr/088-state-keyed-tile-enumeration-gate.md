# ADR-088: State-Keyed Post-Merge Tile-Enumeration Gate (Stop Hook)

**Date:** 2026-07-06
**Status:** Accepted
**Tags:** hooks, stop, tiles, spawn-task, post-merge, enforcement, merged-state, transcript-scan, auto-merge, background-session, parallel-execution, sleep-lock, ADR-033, ADR-046, ADR-060

---

## Context

[ADR-046](046-post-merge-followup-tiles.md)'s 2026-07-05 addendum re-keyed the post-merge tile
checkpoint from "after `gh pr merge`" to **"when a PR reaches merged state, however it merged"**, and
made the enumeration a **forcing function**: record each considered follow-up as
`→ tiled (task_id / #N)` or `→ not tiled, because <reason>`, with "No follow-ups" valid *only* as the
visible result of that scan — never a bare assertion. [dev-env#598](https://github.com/brownm09/dev-env/pull/598)
landed that wording floor (`claude/CLAUDE.md`, ADR-046 addendum, `docs/REFERENCE.md`) and explicitly
**deferred the enforcement hook** to separate follow-up work — [dev-env#599](https://github.com/brownm09/dev-env/issues/599),
this ADR.

The gap the wording alone cannot close is mechanism 2 of the [dev-env#595](https://github.com/brownm09/dev-env/issues/595)
analysis: *no required artifact and no hook, so a skip is invisible*. There **is** an existing hook —
`post-merge-tile-checkpoint.py` ([ADR-060](060-post-merge-tile-checkpoint-hook.md)) — but it is
**command-keyed** (`"gh pr merge" in command`): it fires on a `gh pr merge` *you run* and is **blind to
auto-merge** landing the PR server-side, and to a pure `gh api` merge. Auto-merge is exactly the
motivating incident (lifting-logbook PR #700): the merge landed while the agent was away, the literal
`gh pr merge` command never ran, and "no follow-ups" was asserted with no enumeration. The review-findings
gate is the proven model for making a skip impossible to hide — `pre-merge-findings-gate`
([ADR-039](039-merge-gate-findings-enforcement.md)) blocks the merge until the PR body records a
disposition. The tile checkpoint needs the equivalent, but keyed to the merged **state** rather than the
merge **command**.

## Decision

Add `claude/scripts/stop-tile-enumeration-gate.py` — a **Stop** hook, registered in `claude/settings.json`
immediately after `posttooluse-inert-advisory.py`. At every Stop it scans the just-ended session
transcript and **blocks the stop (exit 2, reason on stderr)** when a PR reached MERGED state this session
by *any* path but **no** tile-enumeration artifact was recorded. It is the state-keyed complement to the
command-keyed ADR-060 hook, and the Stop-hook analog of `pre-merge-findings-gate`.

### Detection (pure transcript scan — no `gh`, no network, no subprocess)

**A PR merged this session** = the union of:
- **Direct evidence:** a top-level `gh pr merge` whose output carries gh's merge success marker
  (`_hookio.output_has_merge_marker` — covers a manual merge and the two-step workaround's
  `gh pr merge <N> --squash` first command), or a `gh api .../pulls/N/merge` whose result is
  `"merged": true`.
- **Auto-merge / pure-`gh api`:** a `gh pr view`/`gh pr checks` output showing `"state":"MERGED"`,
  **correlated** with a PR the session actually acted on (created via `gh pr create`, or targeted by any
  `gh pr merge` including a queued `--auto`). The correlation is what distinguishes "this PR auto-merged
  this session" from "I merely inspected an unrelated old merged PR" — the latter must never fire. The
  observed PR number is read from the authoritative JSON `"number"` (not a positional arg, which could be
  a flag value), and the correlation is by **`(repo, number)`** — an explicit `--repo`/URL repo on the
  view that differs from every repo the session acted the number in does not match, so a same-numbered PR
  in a *different* repo cannot false-fire; a `None` (cwd-inferred) repo on either side falls back to a
  number match, preserving every same-repo true positive (both hardened in the PR #604 review).

**An enumeration was recorded** (session-global) = an actual `spawn_task` tool call, **or** assistant
text carrying the prescribed markers (`Follow-ups considered …`, `-> not tiled, because …`,
`-> tiled` / `→ tiled`). A **bare "no follow-ups" does NOT satisfy the gate** — that is precisely the
#700 skip the ADR-046 refinement invalidated.

**Fire iff** a merged PR is present AND no enumeration was recorded AND no `skip tiles` /
`don't spawn tiles` / `no tiles` user instruction is present. All command-shape checks are
`_hookio.split_top_level`-anchored, so a `gh pr merge` mentioned only inside a heredoc body / quoted
argument / `$()` subshell is never mistaken for a real invocation (the dev-env#499 class).

### Why a Stop hook (not PostToolUse, not PreToolUse)

The tile-enumeration is a **post-merge artifact** written *after* the merge, in the wrap-up. Only at Stop
(turn end) is the whole picture available: "did this session merge a PR **and** record the enumeration?".
A PostToolUse hook firing at the merge command is too early — the enumeration has not been written yet —
and cannot see auto-merge at all. A PreToolUse gate (like the findings gate) does not fit either: there is
nothing to check *before* a merge that has no in-session command, and auto-merge presents no command to
gate. The Stop + transcript-scan shape is the same one `posttooluse-inert-advisory.py`
([ADR-055](055-reliable-event-inert-posttooluse-advisory.md)) already uses successfully.

### Why state-keyed rather than extending the command-keyed hook

The two hooks differ in event (Stop vs PostToolUse Bash), input (whole transcript vs a single command
payload), and logic (verify-an-artifact-at-turn-end vs nudge-on-a-command). Folding them into one script
keyed on `hook_event_name` would merge two distinct responsibilities — ADR-060 itself rejected merging the
tile checkpoint into `pr-merge-reminder.py` for the same single-responsibility reason. They are kept as
**complementary** hooks: the command-keyed one is the immediate in-the-moment nudge (before the post-merge
bookkeeping crowds the checkpoint out); the state-keyed one is the Stop-time verification that the
enumeration actually happened, covering every merge path. `post-merge-tile-checkpoint.py` is unchanged.

**Background-session bonus.** In sessions launched as background tasks / via `spawn_task` (SDK-driven),
*every* PostToolUse hook — including the command-keyed sibling — is silently inert
([ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)). The **Stop** event still dispatches
there, so this hook is the *only* tile enforcement that survives in exactly those sessions.

### Why exit 2 (blocking), fired at most once per session

Mechanism 2 of #595 is that advisory reminders are invisible and get ignored — an exit-0 advisory would
add another ignorable line. Exit 2 forces acknowledgment (Claude continues the turn with the stderr
reason), the same forcing model as `pre-merge-findings-gate`. Loop-safety comes from two guards: the
`stop_hook_active` flag (the Claude Code hooks reference's documented anti-loop mechanism — the hook exits
0 when Claude is already continuing from a prior block) **and** a once-per-session scratch sentinel
(mirrors `posttooluse-inert-advisory.py`). The gate therefore fires **once**: it makes the skip visible and
prompts the enumeration, without any risk of wedging the session.

### Failure mode

Fail-open: any error (unparseable transcript, missing file, unexpected shape) exits 0. A blocking Stop
hook must never wedge a session on its own bug; the wording floor in `claude/CLAUDE.md` remains the
backstop. Non-dict transcript records are dropped at parse time and the pure helpers carry `isinstance`
guards, so a single malformed line yields a deliberate skip rather than silently disabling the gate via
an uncaught helper exception (hardened in the PR #604 review).

### Stop-hook parallelism — exit 2 does not delay `awake-blocker`'s sleep-release

This was, when this ADR was written, the only **blocking** Stop hook in the `claude/settings.json`
Stop list ([ADR-091](091-journal-stop-check-archive-reminder-blocking.md) later made
`journal-stop-check.py`'s archive-reminder branch blocking too; the parallelism argument here holds for
any number of blocking Stop hooks — each exits 2 independently and all sibling hooks still run). The
list also registers `awake-blocker.py` (stop) *after* it — the hook that releases the
Windows system-sleep lock ([ADR-033](033-prevent-system-sleep-while-processing.md)). The natural worry
(raised in the PR #604 review) is that a blocking exit 2 here short-circuits the list and skips
`awake-blocker`, holding the sleep-lock until the `stop_hook_active` continuation next turn. **It does
not.** Per the Claude Code hooks documentation, *"all matching hooks run in parallel … every hook's
command runs to completion before Claude Code merges the results. One hook returning `deny` doesn't
stop sibling hooks from executing"* ([hooks-guide](https://code.claude.com/docs/en/hooks-guide)). The
settings.json Stop order is therefore **not an execution order** — it is non-deterministic parallel
dispatch — so `awake-blocker`'s `stop()` fires at every Stop regardless of this gate's exit 2, and
reordering it earlier would be a no-op.

The only genuine interaction is the mirror image, and it is negligible: because `awake-blocker`
*always* runs at Stop, the sleep-lock is released even when this gate blocks the stop, so the machine
could in principle sleep during the ~one-turn blocked-stop continuation. A system-sleep idle timeout is
minutes long versus that single continuation turn, and the next real `UserPromptSubmit` re-arms the
lock — not worth a code change. (When this ADR was written, `journal-stop-check.py` was *not* blocking
— it exited 0 and emitted its reminders on stdout;
[ADR-091](091-journal-stop-check-archive-reminder-blocking.md) has since made its **archive-reminder**
branch blocking (exit 2 + stderr) so that Claude-facing reminder actually reaches Claude, while its
stale-draft / unmerged-branch advisories stay exit 0.) See
[dev-env#612](https://github.com/brownm09/dev-env/issues/612).

## Consequences

- A manual `gh pr merge` now produces two reminders: the command-keyed immediate nudge (ADR-060) and, if
  the enumeration is still absent at turn end, this state-keyed block. A compliant agent that writes the
  enumeration in its wrap-up never triggers the second one.
- **Auto-merge and pure-`gh api` merges are now enforced** — the #700 blind spot is closed.
- **Background/SDK sessions are now enforced** for tiles, where no PostToolUse hook fires.
- A bare "no follow-ups" no longer passes silently; the agent must produce a visible enumeration or an
  actual tile.

## Limitations (documented, accepted)

- **Session-global enumeration.** One recorded enumeration satisfies the gate for the whole session; a
  session merging two PRs and enumerating only once is not re-flagged for the second. The gate targets the
  *total skip* (merged, nothing recorded), not per-merge enumeration quality — mirroring the findings
  gate's "a disposition step happened, not that each finding was genuinely closed" limitation (ADR-039).
- **Lenient text detection.** The enumeration markers are intentionally permissive to avoid false-blocking
  a compliant agent; because the gate fires only once, a rare false-block self-corrects in a single turn.
- **Requires an in-session observation of the merged state.** If auto-merge lands a PR and the session ends
  with *zero* in-session `gh pr view`/merge evidence, there is nothing in the transcript to key on. In
  practice the post-merge bookkeeping (board move, journal) observes the merged state, which is what the
  correlation keys on.
- **Per-turn scan cost.** The hook re-reads and re-scans the transcript on every Stop (Stop fires each
  turn) — it cannot resolve early the way `posttooluse-inert-advisory.py` does, because a merge can land
  on any later turn. A cheap `"merged"` substring pre-filter (every merge signal this hook detects
  contains that substring) short-circuits the common no-merge session before any JSON parse, and
  `split_top_level` is computed once per command, so the residual per-turn cost in a no-merge session is a
  single file read plus a substring check (both added in the PR #604 review). A merge-bearing session pays
  the full parse only once — the sentinel short-circuits subsequent Stops.

## Alternatives considered

- **A PostToolUse hook that watches for a MERGED-state observation in command output.** Fires too early
  (the enumeration is a later, post-merge artifact) and is inert in background sessions. Rejected.
- **Extend `post-merge-tile-checkpoint.py` with a state-keyed path.** Different event, input, and logic;
  violates single-responsibility (ADR-060's own rationale). Rejected in favor of a sibling.
- **Advisory (exit 0) instead of blocking.** Would be ignored — the exact mechanism-2 failure. Rejected.
- **Block-until-complied (no fire cap, rely on enumeration-detection to clear).** Stronger verification but
  carries Stop-loop risk if the agent's compliant text does not match the detector. Chose fire-once +
  `stop_hook_active` for loop-safety; can be revisited if once-per-session proves insufficient.
- **Share the transcript readers via a new module.** `load_records` / `iter_bash_calls` / `_result_text`
  are replicated from `posttooluse-inert-advisory.py` rather than extracted, matching the repo's tolerance
  for small-helper replication when sharing would over-couple two otherwise-independent hooks (cf.
  `_first_line`, intentionally duplicated across `_hookio.py` and
  `pre-tool-use-canonical-mutate-guard.py`). The genuinely shared bits — sentinels/transcript-locate
  (`_hookutil`, [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md)) and the merge-marker /
  segment parser (`_hookio`, [ADR-050](050-shared-hookio-sibling-hook-fixes.md)) — are imported.
  **Superseded (2026-07-08, [ADR-090](090-shared-transcript-readers-hookutil.md), dev-env#605):** this
  alternative was subsequently adopted — the readers now live in `_hookutil` (both PR #604 reviewers
  flagged the duplication; the repo's shared-module precedent for this class won out over the
  over-coupling concern). `_first_line` stays duplicated (a command-segment helper with per-hook
  intent, not a transcript reader).

## References

- [ADR-046](046-post-merge-followup-tiles.md) — the rule being enforced (2026-07-05 addendum: merged-state
  re-key + forced enumeration).
- [ADR-060](060-post-merge-tile-checkpoint-hook.md) — the command-keyed tile-checkpoint hook this
  complements.
- [ADR-039](039-merge-gate-findings-enforcement.md) / [ADR-028](028-all-findings-merge-gate.md) — the
  merge-gate enforcement model this mirrors.
- [ADR-055](055-reliable-event-inert-posttooluse-advisory.md) / [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)
  — the Stop-transcript-scan precedent and the inert-PostToolUse limitation this hook survives.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — `_hookio` merge-marker / `split_top_level`
  helpers.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — `_hookutil` sentinel / transcript-locate
  helpers.
- [dev-env#595](https://github.com/brownm09/dev-env/issues/595) — the three-mechanism analysis; scoped this
  hook as separate follow-up.
- [dev-env#598](https://github.com/brownm09/dev-env/pull/598) — the wording floor this enforces.
- [dev-env#599](https://github.com/brownm09/dev-env/issues/599) — issue this ADR closes.
- [dev-env#612](https://github.com/brownm09/dev-env/issues/612) — documents that Stop hooks run in
  parallel, so this gate's exit 2 does not delay `awake-blocker`'s sleep-release (the PR #604 review
  question this addendum answers).
- [lifting-logbook#700](https://github.com/merickvaughn/lifting-logbook/pull/700) — the auto-merge incident
  that motivated state-keying.
