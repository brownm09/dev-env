# ADR-126: Mechanical Guard Against Nested Agent-Tool Spawns Relying on the Implicit Background Default

**Date:** 2026-08-03
**Status:** Accepted
**Tags:** hooks, pre-tool-use, agent-tool, subagent, background, orphaned-spawn, enforcement, career-playbook, cross-project, adr-090

---

## Context

The `Agent` tool defaults to `run_in_background: true`. When a subagent spawns its own child
subagent without setting `run_in_background` explicitly, the child does not block the spawning
turn — the parent can return before the child finishes, and the orphaned child's completion
routes to `general-purpose`/`main` instead of back to the subagent that spawned it, silently
stalling until a human notices and manually resumes it via `SendMessage`.

**This is a confirmed, recurring defect — not a one-off.** career-playbook's
[ADR-090](https://github.com/brownm09/career-playbook/blob/main/docs/adr/090-synchronous-subagent-spawns.md)
documents three separate instances of the identical failure mode:

1. A 2026-05-13 precedent ("3 of 10 agents stall after PDF extraction," career-playbook's
   now-superseded ADR-028).
2. The 2026-07-16 incident (career-playbook PR #749): four letters drafted via delegated
   `/cover-letter` runs backgrounded their blind-audit children, returned prematurely with
   half-assembled artifacts, and orphaned the audits to `main`. Fixed by mandating
   `run_in_background: false` on every skill-spawned child, enforced entirely by prose —
   reminders embedded in `SKILL.md` text that a subagent must correctly re-derive and re-apply
   at every nesting level it itself introduces.
3. **The identical bug recurring three more times the SAME DAY** the prose-only fix shipped
   (Engine, Coursera, thrively applications), before every spawn site had been updated to the
   new convention.

The defect is architecturally generic to any project whose Claude Code skills spawn subagents via
the `Agent` tool — not specific to career-playbook. Any other project doing the same thing (e.g.
lifting-logbook) carries the identical exposure today with zero protection, and nothing prevents
a *future* skill (in any project) from reintroducing the exact pattern career-playbook's own
prose-only fix already failed to fully close on day one.

**Why a hook, not more prose.** Prose enforcement depends on a subagent correctly re-deriving and
applying a rule it must rediscover at every nesting level it itself introduces — including at
structurally open-ended delegation points with no step budget at all. It already demonstrably
failed three times in one day even with the rule freshly written down. A `PreToolUse` hook makes
the property a fact about the harness instead of a fact about whether a particular subagent
happened to remember a particular instruction.

**Why global (dev-env), not project-local.** The failure mode has no career-playbook-specific
content — no file paths, no skill names, no domain knowledge. It is purely a property of how the
`Agent` tool's spawn default interacts with nesting. dev-env's `claude/` directory is
symlink/junction-mapped to `~/.claude/`, so a hook added here applies to every project's session
automatically — the established mechanism for a cross-project rule, as opposed to career-playbook's
two existing `PreToolUse` hooks (`block-artifact-merge.py`, `block-letter-violations.py`), which are
rightly local because they encode career-playbook-specific domain rules.

## Decision

A new `PreToolUse` hook, `claude/scripts/pre-tool-use-nested-agent-background-guard.py`, registered
under a new `"Agent"` matcher in `claude/settings.json` (sibling to the existing `Bash`/
`PowerShell`/`Write`/`Edit`/`NotebookEdit` matchers — the first hook in this repo to match the
`Agent` tool).

### Narrow, high-precision gate: nested AND omitted, not "every background spawn"

Two independent conditions, both required, before the hook blocks:

1. **`agent_id` is present in the payload.** Per Claude Code's own documentation, this field is
   present only when the hook fires *inside a subagent call* — the documented, reliable nesting
   signal. A top-level spawn from the main session is completely untouched: backgrounding there is
   the normal, encouraged, documented default pattern ("Agents run in the background by default"),
   and the tool description explicitly walks through the trust-but-verify workflow for exactly that
   case. Punishing it would be a large, unjustified behavior change for ordinary ad hoc usage.
2. **`tool_input.run_in_background` is omitted entirely**, not merely falsy or `true`. An explicit
   `run_in_background: true` on a nested spawn is a *deliberate* choice — Claude Code's own docs
   endorse this exact pattern ("a reviewer subagent that dispatches a verifier per finding") — and
   passes through untouched. An explicit `run_in_background: false` is exactly the fix career-playbook's
   own convention already requires and passes through untouched. **Only an omitted field blocks** —
   the hook targets the specific failure signature named in career-playbook's ADR-090 itself
   ("`cover-letter/SKILL.md` never set an execution mode on any of them"), not a broader restriction
   on background nested work this repo has no basis to impose.

This means the gate has no false-positive surface against any pattern Claude Code's own
documentation describes as legitimate — it closes exactly the gap between "the model forgot to
choose" and "the model chose," which is the entire defect.

### Blocking, not advisory — because the architecture offers nothing else on `PreToolUse`

`claude/scripts/_hookout.py`'s own output-contract table is explicit: on `PreToolUse` (and
`PostToolUse`/`Stop`), plain exit-0 stdout is transcript-only (never reaches the model), and a
`systemMessage` JSON toast reaches only the human, not the subagent that made the call. The *only*
channel that reaches the model on `PreToolUse` is exit-2 stderr — a hard block. There is no
"advisory that the offending subagent can see" option to choose instead; getting a correction back
to the subagent in the moment requires blocking it. Given the gate's narrow precision (above), this
is a reasonable trade: the failure modes it guards against are all "silently discovered hours
later," never "urgent right now," so failing open on any uncertainty about the payload costs
little, while failing to block the one real trigger condition costs a multi-hour silent stall.

### Fail-open on everything else

A crash, an unparseable payload, a `tool_name` other than `"Agent"`, a missing/non-dict
`tool_input`, or an empty `agent_id` all pass through (exit 0) — payload-shape issues unrelated to
the guard's own decision. Unlike `pre-tool-use-journal-compose-force-guard.py`'s deliberate
fail-closed reversal (justified there by an already-rare, narrow trigger with a cheap recovery
path), this hook keeps the repo's usual fail-open convention: it is registered on every `Agent`
call across every project, so a hook bug wedging every subagent spawn machine-wide would be a far
larger, more disproportionate blast radius than the narrow harm (a nested spawn passing through
uncaught) that failing open on an edge case risks.

### Shipped in two stages, not one

**Stage 1 (merged, PR #936):** an observation-only smoke test — the identical field-extraction
logic, but logging instead of blocking, always exiting 0. This was deliberate: no `PreToolUse` hook
in this repo had ever matched the `Agent` tool before, so whether Claude Code's harness actually
fires a hook for a *nested* `Agent` spawn (not just a top-level one) was empirically unconfirmed,
not merely a documentation claim to trust blind for a hook that, once wired for real, blocks tool
calls globally.

**Confirmed live, with real data, before Stage 2 was written:**

- A synthetic top-level `Agent` call (via direct stdin injection) logged `agent_id: null`, exactly
  as the docs describe.
- A **real, live, interactive nested spawn** — a general-purpose subagent instructed to spawn its
  own child — produced a third log line with `agent_id` populated (the parent subagent's own
  spawned-agent ID) and `run_in_background_present: true, value: false`, confirming both that the
  hook fires for a genuinely nested call and that the harness correctly reports the nesting signal
  the whole design depends on.
- The unattended/scheduled-session case (the highest-value target, since that is where nobody is
  present to notice a stall) was *not* separately re-verified end-to-end for this specific matcher
  — `PreToolUse` hooks are already documented reliable in that launch class generally (unlike
  `PostToolUse`, per [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)), and the
  live interactive confirmation above exercises the identical harness mechanism. Recorded here as a
  residual, non-blocking verification gap rather than silently assumed closed.

**Stage 2 (this ADR):** the real enforcement logic replaces the smoke test's body. No settings.json
registration change was needed — the matcher was already wired in Stage 1.

## Consequences

- The exact, three-times-confirmed failure mode (a nested spawn silently relying on the implicit
  background default) can no longer occur unnoticed in any project: the harness blocks it and tells
  the offending subagent exactly what to do, regardless of whether that subagent's own prompt
  correctly remembered a written convention.
- No behavior change for any spawn that already sets `run_in_background` explicitly — which, per
  career-playbook's own audit, is already every documented spawn site in its five skills. This hook
  guards the cases prose *can't* reach: a subagent's own ad hoc, undocumented nested spawn, and any
  future skill (in any project) that doesn't yet know the convention.
- career-playbook's ADR-090 is amended (not superseded) to record that its prose-only fix now has a
  mechanical backstop; see the amendment there for the career-playbook-side follow-up (retry-once
  bounds on several open-ended "do not retry indefinitely" instructions, explicit step budgets on
  two structurally unbounded delegation points, and a diagnostics extension making a future stall
  in the batch pipeline — currently invisible — traceable to the exact step and timestamp it
  stopped responding).

## Limitations (documented, accepted)

- **The gate cannot distinguish "forgot" from "never learned the convention."** Both produce the
  identical block message either way, which is the correct behavior in both cases — the message is
  self-contained and explains the fix regardless of why the field was omitted.
- **A subagent could still explicitly pass `run_in_background: true` on a call that should have been
  synchronous**, defeating the guard's intent while satisfying its letter. This is a deliberate,
  accepted trade: distinguishing "genuinely independent parallel work" from "should have been
  synchronous but the model chose background anyway" requires understanding the *semantic*
  relationship between the spawning subagent's task and its child's — information this hook, which
  only ever sees a single tool call's JSON payload, structurally cannot have. Closing this
  residual gap would require either a semantic judgment this hook is not positioned to make, or
  restructuring the affected skills onto the `Workflow` tool's tracked orchestration (considered and
  explicitly out of scope — see Alternatives).
- **No override token.** Unlike `pre-tool-use-canonical-mutate-guard.py`'s
  `ALLOW_CANONICAL_MUTATE=1`, this hook ships no bypass. Given the fix is always a one-line addition
  to the same tool call (`run_in_background: false` or `true`), there is no legitimate scenario
  that needs to skip the check rather than just satisfy it.

## Alternatives considered

- **A blanket rule: block every nested spawn that defaults to background, regardless of the
  explicit-vs-omitted distinction.** Rejected: this would also block the documented, legitimate
  parallel-verifier-fan-out pattern, trading a real defect for a new, broader friction with no
  corresponding benefit — the omitted-vs-explicit distinction is exactly what makes the gate
  precise instead of blunt.
- **Advisory only (log and warn, never block).** Rejected after reading `_hookout.py`'s own
  contract: there is no channel on `PreToolUse` that reaches the model without also blocking, so
  "advisory" here would in practice mean "invisible to the one party who needs to see it" — a
  human-only toast does nothing for an unattended run, which is the highest-value target.
- **Migrate the affected skills onto the `Workflow` tool's scripted orchestration**, which tracks
  background tasks natively and would remove this whole class of prose-dependent nesting bug by
  construction. Considered and explicitly deferred: a large rearchitecture of every affected skill
  in every project, out of proportion to a backstop. Left as a possible future direction, not a
  recommendation.
- **Fold this into an existing hook** (e.g. `pre-tool-use-canonical-mutate-guard.py`). Rejected for
  the same single-responsibility reason that file's own docstring and this repo's broader
  convention already establish (see, e.g., the four separate `gh pr merge`-keyed hooks that share a
  trigger shape but stay in separate files): this hook's trigger condition (`Agent` tool, nesting,
  background default) has no relationship to any existing hook's domain.

## References

- [career-playbook ADR-090](https://github.com/brownm09/career-playbook/blob/main/docs/adr/090-synchronous-subagent-spawns.md) —
  the incident history and prose-only fix this hook backstops mechanically.
- [career-playbook PR #749](https://github.com/brownm09/career-playbook/pull/749) — the original,
  motivating incident.
- [dev-env#935](https://github.com/brownm09/dev-env/issues/935) — issue this ADR closes.
- [dev-env#936](https://github.com/brownm09/dev-env/pull/936) — the Stage 1 observation-only smoke
  test and its live-confirmation evidence.
- [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) — why this had to be a
  `PreToolUse` hook, not `PostToolUse`, for the unattended-session case to matter at all.
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the closest sibling `PreToolUse`
  blocking-gate hook in overall shape; this hook departs from its override-token design (see
  Limitations) for the reason stated there.
- [ADR-103](103-shared-hookout-emitter.md) — the output-contract module (`_hookout.py`) this hook's
  block path uses, and the reason "advisory" was not an available design point on `PreToolUse`.
