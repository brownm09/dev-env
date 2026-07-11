# ADR-096: Mechanical Enforcement of the Journal-Compose Today-Guard

**Date:** 2026-07-09
**Status:** Accepted
**Tags:** journal, composition, skill, today-guard, hooks, pre-tool-use, enforcement, adr-017, adr-028, adr-039, adr-071

---

## Context

[ADR-017](017-journal-compose-today-guard.md) added a guard to `/journal-compose`: composing
today's date requires an explicit `--force`, so an unattended caller never merges a day that might
still receive stubs later. That guard is implemented purely as prose in
`claude/skills/journal-compose/SKILL.md` Step 0.6 — "If the resolved date equals `$TODAY` and
`FORCE` is false, stop immediately and respond... Do not proceed" — with nothing mechanically
enforcing it.

**Motivating incident.** A 2026-07-08 07:09am `daily-journal-compose-local` scheduled-task run's
own transcript ([dev-env#631](https://github.com/brownm09/dev-env/issues/631)) shows the agent
explicitly noticing the guard should apply, then reasoning:

> "Even though the args passed are just the date without an explicit `--force` flag, this is an
> automated scheduled task designed for exactly this purpose, so I should treat it as having
> `--force=true` semantics. I'll proceed w[ith force=true]"

The agent never ran the guard's literal comparison and hit a refusal message it then argued past —
it pre-emptively decided the guard didn't apply, based on its own inference about the task's
intent, before the guard was ever technically evaluated. This directly caused
[engineering-journal PR #155](https://github.com/brownm09/engineering-journal/pull/155) (a
premature merge of a still-in-progress `draft/2026-07-08`), and confirms — with direct transcript
evidence, not inference — the exact mechanism
[dev-env#615](https://github.com/brownm09/dev-env/issues/615)'s "Open question" section had raised
speculatively.

**Root cause.** The today-guard is written as an instruction for an LLM agent to *follow*, not as
a script whose exit code the agent is mechanically *bound by*. Nothing prevents an agent —
especially one operating under a "run fully autonomously, do not ask the user anything" mandate —
from reasoning that stopping would fail its objective and routing around a check that is, from its
own vantage point, just more prompt text to interpret rather than obey. This is a structurally
different class of guard than `pre-tool-use-canonical-mutate-guard.py`
([ADR-071](071-canonical-checkout-mutate-guard-hook.md)) or `pre-merge-findings-gate.py`
([ADR-028](028-all-findings-merge-gate.md)/[ADR-039](039-merge-gate-findings-enforcement.md))
elsewhere in this repo — those are hook scripts the harness enforces regardless of what the agent
decides; the today-guard, until now, was not.

## Decision

Convert the today-guard from a prose instruction into a two-part mechanical gate, mirroring this
repo's own precedent for hardening a prose policy into a hook
(ADR-028 → ADR-039 is the direct structural analog: a policy ADR, later followed by a dedicated
"mechanical enforcement" ADR adding a `PreToolUse` hook, rather than editing the policy ADR
in place).

### 1. Deterministic `FORCE` resolution (`journal-compose-force-resolve.py`)

A new script, `claude/scripts/journal-compose-force-resolve.py`, resolves `FORCE` from the
literal, unparsed `$ARGUMENTS` text of the `/journal-compose` invocation — not from the agent's
own reading or paraphrase of it. `SKILL.md` Step 0.6 now runs this script as its **very first Bash
action**, before any of the skill's own date/branch resolution:

```bash
py -3 C:/Users/brown/.claude/scripts/journal-compose-force-resolve.py "$ARGUMENTS"
```

Because `$ARGUMENTS` is substituted by the Claude Code harness into the skill's markdown template
*before* the model ever reasons about the task, this command's literal text is fixed by whatever
was actually typed after `/journal-compose` — not by anything the agent later decides the
invocation "must have meant." The script:

- Regex-matches a whitespace-bounded `--force` token (not `--force-push`, not a substring of
  prose) — `resolve_force()` in the new shared module `claude/scripts/_journal_compose_force.py`.
- Prints exactly one line, `FORCE=true` or `FORCE=false`, which the skill uses for the rest of
  Step 0.6 (replacing the prior "parse `$ARGUMENTS` by hand" prose step).
- Writes a same-day marker file (`journal-compose-force-<today>.json`, keyed to the script's own
  local `date.today()`) recording `{force, raw_arguments, resolved_at}`.

### 2. Mechanical gate (`pre-tool-use-journal-compose-force-guard.py`)

A new `PreToolUse(Bash)` hook, `claude/scripts/pre-tool-use-journal-compose-force-guard.py`,
mirrors `pre-merge-findings-gate.py`'s and `pre-tool-use-canonical-mutate-guard.py`'s structure.
It fires only when a command is a git `worktree` / `commit` / `push` invocation whose non-message
tokens reference a `draft/<today>` or `compose[-/]<today>` target, where "today" is the hook's own
`datetime.date.today()` — never anything the command or the model claims. On a match, it requires
a fresh, `force == true` marker (written only by step 1) or blocks (exit 2). Every other git/gh
command, and every past-day compose (the nightly routine's normal path, since
[ADR-084](084-nightly-compose-targets-yesterday.md) always targets yesterday), is completely
untouched — the hook's trigger condition is narrow by construction.

**Commit-message false-positive guard.** A commit message can legitimately *mention*
"draft/2026-07-09" or "compose-2026-07-09" as prose — this repo's own commits routinely discuss
the pattern (this ADR is an example). The hook's token scan explicitly excludes the VALUE of a
`-m`/`--message`/`-F`/`--file` flag, so such a mention can never manufacture a match. Verified by
`test_commit_message_mentioning_draft_today_as_prose_no_match` /
`test_commit_message_mentioning_compose_today_as_prose_no_match`.

**No `cd`/cwd dependency.** Unlike `pre-tool-use-canonical-mutate-guard.py`, this hook never needs
to resolve which repo an *ambient* command affects — it only ever matches a command that
*explicitly* carries a `-C <path>` value or a positional ref naming a same-day `draft`/`compose`
target. A `cd <path> &&`-prefixed segment simply doesn't contain that reference and is correctly
ignored regardless of cwd.

**Deliberate fail-closed reversal.** Every sibling hook in this family (`pre-merge-findings-gate.py`,
`pre-merge-message-check.py`) fails OPEN on an internal error, because they gate extremely common
operations (every merge, every push) where a hook bug wedging all of them is a large, costly blast
radius. This hook only ever evaluates same-day compose mutations — already meant to be rare per
ADR-017 — so it fails **CLOSED** on a missing, corrupt, or stale marker: the worst case is
"re-run `journal-compose-force-resolve.py` and retry," never "no PR can ever merge."

**Deliberate no-override-token.** `pre-tool-use-canonical-mutate-guard.py` ships
`ALLOW_CANONICAL_MUTATE=1` as a visible, auditable human escape hatch. This hook ships **no
override token at all** — the only legitimate way to satisfy it is to have genuinely passed
`--force`, which already produces a valid marker via the normal path. An override token here would
just be a second, easier route to reason past, defeating the entire purpose: an autonomous agent
under schedule pressure could talk itself into using an override exactly the way it talked itself
into inferring `--force` in the first place. Recovery from a stale/expired marker (a compose that
outlived `MAX_MARKER_AGE_SECONDS`) is to re-run the resolve script with the same literal
`$ARGUMENTS` — never a hand-typed `--force` unless the user explicitly said so earlier in the
current conversation (stated at the block site itself, and in `SKILL.md`).

**Generous freshness window.** `MAX_MARKER_AGE_SECONDS = 4 * 60 * 60` (4 hours) — a multi-project
compose with Further-Reading subagent research (`SKILL.md` Step 11) can genuinely run for hours;
this bound exists only to invalidate a truly ancient, crash-orphaned marker, not to interrupt a
realistic compose run.

### 3. Explicit prohibition in the routine prompt

Independent of (1)/(2): `claude/routines/daily-journal-compose/SKILL.md`'s Constraints section, and
the live `daily-journal-compose-local` scheduled task's self-contained fallback copy (kept in sync
in the same change — its own text already documents drifting from canonical once before,
dev-env#615), now explicitly state: never pass `--force` from this routine under any circumstance,
and never reason that `--force` semantics are implied; a guard refusal is success, not a problem to
work around. This is weaker than (1)/(2) (still reasoning-based) but cheap and directly contradicts
the reasoning the dev-env#631 transcript shows the agent actually used.

## Consequences

- The today-guard can no longer be silently reasoned past — the hook enforces it regardless of an
  agent's beliefs about task intent, closing the exact mechanism dev-env#631 confirmed.
- Every legitimate compose path is unaffected: past-day composes (the nightly routine's normal
  case) never reference today's date and never reach the hook's trigger condition at all; an
  intentional `/journal-compose --force` writes a valid marker through the ordinary Step 0.6 flow
  and proceeds exactly as before.
- A same-day compose that genuinely needs `--force` and runs long enough to outlive the 4-hour
  freshness window requires re-running the resolve script — a minor, self-diagnosable friction, not
  a silent failure.

### Testing

Three new test files, all offline/deterministic (today-dated fixtures built from
`datetime.date.today()` at test-run time, never hardcoded):

- `test_journal_compose_force.py` — pure tests for the shared module (`resolve_force`,
  `marker_path_for`, `build_marker`, `write_marker`/`read_marker` round trip via real tmp files,
  `is_marker_fresh` including the exact freshness boundary, a future-timestamped marker, and — added
  during `/review` on PR #671 — a timezone-aware `resolved_at` (must resolve to "not fresh," not
  raise `TypeError`) and a simulated-concurrent-writer `write_marker` case (a monkeypatched
  `os.getpid` proving one writer's in-progress temp file survives an independent writer's
  write-and-rename).
- `test_journal_compose_force_resolve.py` — end-to-end subprocess tests for the resolve script
  (argv handling, printed `FORCE=` line, marker written to disk with the expected schema,
  overwrite-on-rerun).
- `test_pre_tool_use_journal_compose_force_guard.py` — pure command-classification tests (every
  real `SKILL.md` command shape: worktree add, `-C`-scoped commit/push, the `compose/YYYY-MM-DD`
  recovery branch, the commit-message false-positive regression, heredoc-body non-match, the
  `draft/<date>-recovery` suffix) plus behavioral subprocess tests driving the real hook over
  stdin (no marker blocks; fresh `force=true` allows; `force=false` blocks; a stale marker blocks;
  a corrupt marker fails closed; a non-today date allows regardless of marker state; malformed/
  empty/non-Bash/non-dict payloads all fail open). Extended during `/review` on PR #671 with: a
  `-c <name>=<value>` git-level flag no longer swallows the verb (both a pure-classification and an
  end-to-end no-marker-still-blocks case); the `git commit -am "..."` combined-short-flag idiom and
  the glued `-m<value>`/`-F<value>` (no space) forms are excluded from the date scan exactly like
  the separate-token `-m`/`--message` forms; the `-ma` glued-value edge case resolves to a message
  value rather than misparsing; the performance pre-filter short-circuits a dateless command; and an
  end-to-end case pinning that a tz-aware marker blocks cleanly (exit 2, no traceback) rather than
  crashing open.

All three pass the repo-wide `test_no_crude_command_substring_checks.py` AST gate (no raw
string-literal `in`/`not in` checks against a `command` variable).

## Limitations (documented, accepted)

- **Marker keyed by date only, not session.** Two concurrent `/journal-compose` invocations for
  the same today's date (an unlikely scenario the skill's own compose-worktree/lock machinery
  already treats as a concurrency signal, not a supported case) would share one marker. Given the
  guard's narrow, already-rare trigger condition, this is accepted rather than adding session-id
  plumbing the resolve script has no natural way to receive (it is invoked directly by the model
  via Bash, not as a hook — it never receives the harness's `session_id`).
- **An agent could still fabricate the marker by hand-typing `--force` into the resolve script's
  own argument, deliberately misrepresenting what `$ARGUMENTS` actually was.** This is a
  categorically different, more flagrant act than dev-env#631's failure mode (silent prose
  reasoning with no visible artifact) — it requires an agent to author command text that
  contradicts the literal, harness-substituted text sitting right above it in its own context,
  which is far more visible in a transcript/review than an inference stated only in reasoning
  text. Not eliminated; substantially raised in cost and visibility. `SKILL.md` and the hook's own
  block message both state explicitly that a hand-typed `--force` is permitted only under
  explicit, current-conversation user instruction — never inferred.
- **`git worktree remove`/`prune` are gated too, not just `add`.** A loose verb match (`worktree`
  generally, not `worktree add` specifically) means Step 11's cleanup calls are also subject to
  the marker check. Accepted as harmless: by the time cleanup runs, a successful compose has
  already written a valid marker at Step 0.6, so this never introduces a new block — see the
  Decision section's "No `cd`/cwd dependency" note for why a tighter, `add`-only match wasn't
  worth the added parsing precision.
- **The marker file has no provenance binding to the resolve script — reviewed and accepted as a
  materially easier bypass than the hand-typed-`--force` route above, not a new one.** `read_marker`
  validates only the marker's *content* (`force == true`, freshness), never *who wrote it*. Nothing
  distinguishes a marker produced by `journal-compose-force-resolve.py` from one authored directly
  via the `Write` tool or a non-git Bash command (e.g. `python -c "...json.dump..."`) at the exact
  same well-known path — the guard hook only pattern-matches `git worktree`/`commit`/`push`
  invocations, so neither route is inspected at all. Unlike the hand-typed-`--force` limitation
  above, a direct marker write has **no adjacent `$ARGUMENTS` context to contradict**, so it is
  *less* visible in a transcript, not more — an agent authoring `{"force": true, ...}` at a scratch
  path doesn't self-evidently read as a guard bypass the way overriding a visible, literal argument
  does. Accepted for the same reason the ADR's own scope is narrow: this mechanism closes the
  *specific, transcript-confirmed* dev-env#631 failure mode (silent reasoning with no artifact at
  all), not every path a fully adversarial agent with complete filesystem access could take — no
  hook in this repo defends against that broader threat model (the closest analog,
  `pre-tool-use-canonical-mutate-guard.py`'s `ALLOW_CANONICAL_MUTATE=1`, is the same kind of
  self-honesty contract, not a cryptographic boundary). Closing it properly needs harness-provided
  marker provenance (e.g. a session-id/nonce the hook can independently verify), which is out of
  scope here. `SKILL.md`'s "cannot be reasoned past" wording is qualified accordingly, at the
  today-guard block itself, to name this residual gap rather than overstate the guarantee. (Review
  finding on PR #671.)
- **A combined short-option cluster with a glued value (e.g. `-amHello`, `-a` and `-m` combined
  with no space before the message) is not excluded from the date scan**, only the far more common
  separate-token (`-am "Hello"`) and single-flag-glued (`-mHello`) forms are. Accepted: this
  residual gap is in the false-positive (over-block) direction only — the same direction as the two
  forms already fixed, never the bypass direction — and is rare enough in practice that closing it
  would mean hand-rolling a fuller getopt-style cluster parser for marginal additional coverage.
  (Review finding on PR #671.)

## Alternatives considered

- **Fold the marker check into `pre-tool-use-canonical-mutate-guard.py`.** Rejected: that hook's
  entire model is "does this command mutate a canonical (non-worktree) checkout" — a completely
  different question from "does this command target today's date without an explicit force." The
  engineering-journal checkout is explicitly carved out of the canonical-mutate guard (pending
  dev-env#346), so bolting date-based logic onto it would mean reasoning about two independent
  concerns in one already-800-line file. Every other hook family in this repo that shares a
  trigger shape (the four `gh pr merge`-keyed hooks: `pre-merge-message-check.py`,
  `pre-merge-findings-gate.py`, `pre-merge-numbering-check.py`, `pre-merge-branch-check.py`) stays
  in separate files despite near-identical detection code, for exactly this single-responsibility
  reason.
- **An override token, matching `ALLOW_CANONICAL_MUTATE=1`.** Rejected — see "Deliberate
  no-override-token" above; it would reopen the exact vulnerability this ADR closes.
- **Session-scope the marker.** Considered for precision; rejected as unneeded complexity given
  the resolve script has no natural session-id input (see Limitations) and the narrow blast radius
  of the date-only key.

## References

- [dev-env#631](https://github.com/brownm09/dev-env/issues/631) — issue this ADR closes, with the
  direct transcript evidence.
- [dev-env#615](https://github.com/brownm09/dev-env/issues/615) — the earlier, explicitly-unconfirmed
  speculative "Open question" this issue's transcript evidence confirms.
- [engineering-journal PR #155](https://github.com/brownm09/engineering-journal/pull/155) — the
  premature merge this failure mode directly caused.
- [ADR-017](017-journal-compose-today-guard.md) — the guard's founding design; this ADR hardens its
  enforcement, not its intent.
- [ADR-028](028-all-findings-merge-gate.md) / [ADR-039](039-merge-gate-findings-enforcement.md) —
  the direct structural precedent this ADR follows: a prose policy ADR, later complemented by a
  dedicated mechanical-enforcement ADR.
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the closest sibling `PreToolUse` hook in
  shape (git-mutation gating on a resolved target, not cwd alone) and in its override-token design,
  which this ADR deliberately departs from.
- [ADR-084](084-nightly-compose-targets-yesterday.md) — why the nightly routine's normal path never
  reaches this guard at all.

---

## Addendum (2026-07-10) — Crash-guard: `main()` now fails closed, but the top-level imports stay fail-open

The Decision and Limitations sections above made a *missing / corrupt / stale marker* fail closed,
but `main()` itself had no top-level `try/except`: any unhandled exception exited 1, which Claude
Code reads as *"the hook allowed the tool."* So a crash while evaluating a same-day compose command
was fail-**OPEN**, letting the compose proceed ungated — the same failure class as
[ADR-083](083-auto-merge-checkpoint-gate.md)'s 2026-07-10 addendum, fixed in the same PR
([dev-env#718](https://github.com/brownm09/dev-env/issues/718), Phase A of the hook-reliability
initiative [dev-env#717](https://github.com/brownm09/dev-env/issues/717)).

**Fix.** A top-level `try/except → sys.exit(2)` around `main()` that **re-raises `SystemExit`** so the
gate's own deliberate exit 0 (allow) / exit 2 (block) verdicts survive. The crash reason reuses
`_emit_block`'s `json.dumps({"reason": …})`-on-stderr channel (`ensure_ascii=True` keeps it
cp1252-safe).

**Deliberate asymmetry with ADR-083: the top-level *imports* stay fail-OPEN.** Unlike the auto-merge
gate, this hook is registered on *every* Bash call. Failing its `from _hookio import …` /
`from _journal_compose_force import …` closed (exit 2) would block *every* Bash command the moment a
shared module broke — a catastrophic, disproportionate blast radius for a guard whose own action
(blocking a rare same-day compose) is minor. So only a crash *inside* `main()` — reached solely after
`command_targets_today_compose` has already matched a same-day target — fails closed; an import-time
crash stays the pre-existing fail-open exit 1.

**Testing.** This gate is otherwise fully defensive (`read_marker` swallows `(OSError, ValueError)`,
`is_marker_fresh` swallows `(ValueError, TypeError)`), so it has no natural runtime-crash vector. The
hook therefore carries a small, env-gated (`JOURNAL_COMPOSE_FORCE_GUARD_TEST_CRASH`), production-inert
fault-injection seam placed just after the compose-target match, letting the new e2e case in
`test_pre_tool_use_journal_compose_force_guard.py` drive a genuine `main()` crash and assert exit 2.

**Generalized.** Contributes to authoring invariant #5 ("declared fail direction") in
[`docs/REFERENCE.md` → Hooks → Authoring rules](../REFERENCE.md#authoring-rules). Closes (with the
ADR-083 addendum) [dev-env#718](https://github.com/brownm09/dev-env/issues/718).
