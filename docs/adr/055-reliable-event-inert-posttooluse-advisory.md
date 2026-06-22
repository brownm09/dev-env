# ADR-055 — Reliable-Event Safety Net: A Stop-Hook Advisory for Inert PostToolUse Hooks

**Date:** 2026-06-22
**Status:** Accepted
**Tags:** hooks, post-tool-use, stop-hook, transcript, observability, reliability, background-task, spawn-task, github-project

---

## Context

[ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) documented an upstream
Claude Code Desktop limitation: in sessions launched as **background tasks / via `spawn_task`**
(SDK-driven), **every** `PostToolUse` settings hook is silently inert — no project-board add
after `gh issue create` / `gh pr create`, no Done-move / journal-stub reminder / usage snapshot
after `gh pr merge`, no memory-write advisory — while `UserPromptSubmit`, `PreToolUse`, and
`Stop` hooks from the same `~/.claude/settings.json` still fire. No PostToolUse hook-code change
can fix it, because the hooks never dispatch (anthropics/claude-code#42336, #53494).

ADR-053 §Decision item 5 proposed — and deferred — a **reliable-event safety net**: a hook on
an event that *does* fire in affected sessions, detecting when a board-relevant `gh` command ran
but its PostToolUse side-effect did not, and surfacing a one-line advisory. This is the
follow-up, tracked as [#390](https://github.com/brownm09/dev-env/issues/390) (which the inert
session [#381](https://github.com/brownm09/dev-env/issues/381) motivated).

The recovery path already exists (dev-env `CLAUDE.md` → GitHub Project → Fallback). The gap is
purely one of **visibility**: a human/agent only invokes the fallback if they *notice* the
silent miss. So this is an observability enhancement, not a correctness fix, and is scoped
accordingly.

### Forensic basis (how the detection works without a `gh` call)

The harness writes a transcript `attachment` record whenever a hook produces output. ADR-053's
own forensics counted these per `hookEvent`. Confirmed against retained transcripts:

- A PostToolUse hook that ran leaves an `attachment` whose `attachment.hookEvent == "PostToolUse"`
  — both the exit-0 `hook_success` shape (e.g. `usage-snapshot.py`) and the exit-2
  `hook_blocking_error` shape (e.g. `post-tool-use.py`). Every `hook_blocking_error` seen carries
  `hookEvent == "PostToolUse"`.
- In an inert session, the count of such attachments is **zero** for the whole session.

So "**a dev-env board action ran AND zero `PostToolUse` attachments exist all session**" is the
inert signature — readable from the transcript the `Stop` payload already points at, with **no
`gh` call and no `project` scope**.

Two transcript facts shaped the detector:

- The created issue/PR URL *is* preserved in the tool-result the model saw (stdout), so a
  successful dev-env create is detected with high confidence.
- gh's `Squashed and merged pull request #N` success marker is printed to **stderr** and is
  **not** preserved in the transcript (0 occurrences across retained transcripts; only the
  issue-#275 worktree cleanup tail `Exit code 1 / failed to delete local branch ... checked out
  at` survives). So merge detection keys off the `gh pr merge` command + dev-env PR scope +
  absence of a hard-merge-failure, not the marker.

## Decision

Add a **`Stop` hook**, `claude/scripts/posttooluse-inert-advisory.py`, that scans the
just-ended session's transcript and prints a one-line, non-blocking advisory when a dev-env
(project #3) board action ran but no PostToolUse hook fired all session.

1. **Event → `Stop`.** `Stop` fires reliably once per session-end *including one-shot
   background / `spawn_task` sessions* — the population that actually breaks. `UserPromptSubmit`
   (the rejected alternative) needs a next prompt those one-shot sessions never get, so it would
   miss the dominant failure case. `Stop` also mirrors ADR-053's own forensic method (count
   PostToolUse attachments in the just-ended transcript) and is a sibling of
   `journal-stop-check.py`. Fires at most once per session via a `scratch/` sentinel
   (`posttooluse-inert-advised-<session_id>.flag`).

2. **Detection is transcript-only — no `gh` call, no `project` scope** (ADR-053 design Q2). The
   inert signal is **zero `attachment` records with `hookEvent == "PostToolUse"`** alongside a
   detected dev-env board action. Reusing the harness's own attachment record is cheaper and
   more direct than re-querying board state, and avoids the `project` scope a board lookup needs.

3. **Advisory only — never block** (ADR-053 design Q3; the dev-env Observability convention).
   Prints to **stdout, exit 0**. It is *not* a blocking `Stop` hook (which would `exit 2` to
   stderr and keep the session from stopping). The advisory names the action(s) and points to
   the dev-env `CLAUDE.md` → GitHub Project → Fallback.

4. **No false positives** (ADR-053 design Q4). Only **high-confidence dev-env actions** trigger:
   a `gh issue/pr create` whose output carries a `github.com/brownm09/dev-env/(issues|pull)/N`
   URL (a guaranteed `post-tool-use.py` exit-2 in a healthy session — dev-env's config sets
   `repo`, `status_field_id`, `done_option_id`, and the workflow always writes `Closes #N`), or a
   `gh pr merge` for a dev-env PR with no hard-merge-failure. Critically, **any** PostToolUse
   attachment all session short-circuits to silent — so the legitimate different-repo / no-config
   silent-skip paths ([ADR-049](049-hook-payload-output-field.md)) can never trip it: in a
   healthy session at least one PostToolUse hook leaves a record.

5. **ASCII-only advisory text.** Claude Code pipes hook stdout as **cp1252**; a character outside
   it (the first draft used `->` rendered as `→`, plus an em-dash and curly quotes) makes
   `print()` raise `UnicodeEncodeError`, which the hook's top-level `exit-0` guard swallows — the
   advisory would vanish, the *exact* silent-failure class this hook exists to surface. The text
   is ASCII by construction, a unit test pins it cp1252-encodable, and `main()` also reconfigures
   stdout to `errors="replace"` defensively.

### Scope

Detection is **dev-env-only** (project #3): the created URL / merged PR must be `brownm09/dev-env`.
The inert state is session-global, so a dev-env action is a sufficient witness; scoping the
*detector* to dev-env keeps precision high and matches the advisory's target (the dev-env
`CLAUDE.md` fallback). Other configured projects (lifting-logbook, career-playbook) have their own
boards and fallbacks; extending the detector to them is a clean future increment (generalise the
URL/PR scope to a configured-repo set) and is intentionally out of scope here.

## Consequences

- The silent gap from ADR-053 becomes **visible** in affected sessions: when PostToolUse is inert
  and a dev-env issue/PR was created or merged, the session-end advisory names it and points to
  the manual fallback. The fallback remains the system of record (this hook does not perform the
  board add/move).
- The detector is best-effort and **dev-env-scoped**: a bare `gh pr merge` naming no PR from a
  non-dev-env cwd, or board work in another project, is not surfaced. Accepted for a v1 safety net.
- Zero new network/`gh` cost: one transcript read at `Stop`, the same cost class as
  `token-tracker.py`. No `project` scope required.
- A genuinely inert session still gets no *automated* board mutation — only an advisory. A true
  fix can only land upstream; [#381](https://github.com/brownm09/dev-env/issues/381) tracks it.
- Covered by `claude/scripts/tests/test_posttooluse_inert_advisory.py` (pure helpers, offline),
  registered in the dev-env `## Testing` section.

## Alternatives considered

- **`UserPromptSubmit` instead of `Stop`** — rejected: misses one-shot background sessions (no
  next prompt). It would self-heal multi-turn sessions earlier, but those are the minority of the
  failing population; `Stop` covers all of them. (A future increment could add it as a *second*
  emitter for in-session self-healing, sharing the same pure helpers.)
- **Query board state with `gh project item-list` / `gh issue view`** — rejected: needs the
  `project` scope and a network call per check, and the transcript already carries an authoritative
  "did the hook fire" signal (the attachment) at zero cost.
- **Detect the merge via gh's success marker** — rejected: the marker is on stderr and not
  preserved in the transcript; command + dev-env PR scope + no-hard-failure is the reliable signal.

See [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) (the limitation),
[ADR-049](049-hook-payload-output-field.md) / [ADR-050](050-shared-hookio-sibling-hook-fixes.md)
(the distinct output-field bug), and [ADR-007](007-hook-command-invocation.md) (the `pyw -3`
launcher / stdout model).
