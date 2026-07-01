# ADR 068 — Reconcile the Project Board Against Orphaned Issues (Backstop for the Inert Add-Hook)

**Date:** 2026-06-30
**Status:** Accepted
**Tags:** routines, github-project, post-tool-use, hook-config, reconciliation, background-sessions, automation

---

## Context

`post-tool-use.py` (a `PostToolUse` hook) auto-adds each newly-created dev-env issue to the
**Dev Env** project board (#3) and prints the `gh project item-edit` commands to set its
required fields (Impact / Why). That is the happy path for issues filed in an ordinary
interactive session.

But **`PostToolUse` hooks are inert in background / `spawn_task` / SDK-launched sessions**
([ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)). An issue filed from such a
session is therefore **silently never added to the board** — it misses its Impact rating, its
Why description, and the Status workflow entirely. The drift is invisible: nothing surfaces
the gap until a human or a later session happens to notice an untracked issue.

Confirmed instance (2026-06-30): the cross-project memory audit ([#363](https://github.com/brownm09/dev-env/issues/363))
ran in a background session and filed #434, #435, #436 — all with `projectItems: []`. A later
session porting them had to manually `gh project item-add` + set Impact/Why for all three
before work could proceed. A live check while authoring this ADR found #439 and #368 already
orphaned too, confirming the leak is ongoing, not a one-off.

This is the *remediation* for the gap [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md)
documents (and complements the upstream re-check tracked in
[#424](https://github.com/brownm09/dev-env/issues/424)): the hook can't fire in those sessions,
so the board needs a backstop that runs where the hook can't.

---

## Decision

Add a **config-driven, add-only + report-only reconcile engine** plus a **nightly routine** that
runs it — mirroring the existing `prune-merged-worktrees.py` (script) + `prune-stale-worktrees`
(routine) split.

**`claude/scripts/reconcile-project-board.py`:**

1. Reads `.claude/hook-config.json` — the *same* file `post-tool-use.py` reads — so the project
   number/owner/node-id and the required-field IDs never drift between the add-hook and the
   reconciler. A repo with no such config is not reconcilable (exit 1). The default repo root is
   the *canonical* checkout, derived from the script's own path so a Claude-managed-worktree
   invocation (where the gitignored hook-config is absent) still finds the machine-local config —
   the same canonicalization `post-tool-use.py` performs.
2. Lists open issues (`gh issue list`) and board items (`gh project item-list`), and computes the
   **set difference**: orphans = open issues whose number is not among the board's Issue items
   for this repo.
3. Adds each orphan to the board (`gh project item-add`).
4. **Reports** — but does **not** guess — the orphans (each needs all required fields) and any
   *pre-existing* open board items already missing a required field, emitting the exact
   `gh project item-edit` commands plus the option IDs from config. Ends in a machine-readable
   `RESULT: orphans_added=N add_failed=N needs_attention=M dry_run=…` line the routine reads.
5. `--dry-run` reports without adding. Missing-`project`-scope `gh` failures are detected and
   surface the `gh auth refresh -s project` hint instead of a raw stderr dump.

**`claude/routines/reconcile-project-board/` (`schedule: 0 6 * * *`):** syncs its worktree to
`origin/main` (Step 0 `sync-routine-worktree`, per [ADR-013](013-sync-routine-worktree-skill.md)),
runs the script live, and push-notifies when `needs_attention > 0` so the user fills Impact/Why.

**Why add-only + report-only.** Adding an orphan to a board is mechanical and reversible.
*Choosing* an Impact rating or writing a Why is a judgment call — guessing it unattended would
pollute the board with low-quality metadata that reads as deliberate. So the reconciler adds
(safe) and surfaces the fields for a human, and it **never** calls `updateProjectV2Field`, so it
structurally cannot trip the single-select option-mutation hazard (global CLAUDE.md → Dev-Env &
Project Boards).

**Why config-driven rather than hardcoding project #3's field IDs.** The add-hook and the
reconciler must agree on the field IDs forever; reading the one config both share removes the
drift risk and makes the engine reusable for any project that adopts the `required_fields` config.

---

## Alternatives Considered

**A per-session `UserPromptSubmit` hook (the shape ADR-018 chose for open-prs).** A hook would
catch orphans at the start of every interactive session — maximal freshness. Rejected as the
*primary* mechanism: it adds a `gh issue list` + `gh project item-list` round-trip and a block of
"these issues need Impact/Why" context to *every* session start, and the failure it guards is not
urgent — an orphaned issue loses no data, it just isn't tracked yet. ADR-018 accepted per-session
cost because stale open-PR data actively misleads a session from turn 1; board orphans don't, so
the cheaper nightly cadence is the right trade. (The engine is a plain script, so a hook wrapper
remains a cheap future option if nightly proves too slow.)

**Auto-fill Impact/Why from the issue body.** Could infer Impact from labels / heuristics. Rejected:
the brief is explicit — *do not guess Impact/Why silently*. A wrong-but-confident rating is worse
than a visible gap.

**Multi-repo scan now (`--scan-dir`, like prune/reclaim).** The same inert-hook gap affects every
project board, not just dev-env. Deferred to a follow-up (#447) to keep this PR scoped to the
board the brief names; the pure helpers are written to generalize cleanly.

---

## Consequences

- Issues filed from background / `spawn_task` / SDK sessions are reliably boarded within a day,
  closing the [ADR-053](053-posttooluse-hooks-inert-in-background-sessions.md) leak without
  needing the upstream hook fix.
- The reconciler is safe to run unattended **and** by hand (`--dry-run` for a read-only preview):
  add-only, never mutates options, never guesses a field value.
- One `gh issue list` + one `gh project item-list` + one `item-add` per orphan, nightly —
  negligible cost (orphans are rare; the dry-run at authoring found 5).
- The required-field IDs live in `.claude/hook-config.json` alone; the add-hook and the reconciler
  read the same source, so they cannot drift.
- A new `## Testing` self-test (`test_reconcile_project_board.py`) pins the set-difference,
  field-detection, and no-guessing contracts offline; the `gh` boundary stays unmocked per the
  repo convention.
- Generalized to all project-configured repos via `--scan-dir` (dev-env#462,
  [ADR-070](070-reconcile-project-board-scan-dir.md)).
