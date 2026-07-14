# ADR 003 — Config Artifacts in Version Control via Symlinks

**Date:** 2026-04-13  
**Status:** Accepted  
**Amended:** 2026-07-01, 2026-07-06, 2026-07-08, 2026-07-10 (see Amendment sections below)

---

## Context

Claude Code's global configuration lives in `~/.claude/` by default. Files edited there are machine-local: invisible to git, unauditable, and prone to drift across reinstalls or machines. Early in this setup, CLAUDE.md and settings.json were edited directly at `~/.claude/`, and changes were occasionally lost or inconsistently applied across sessions.

The challenge: Claude Code reads config from fixed paths (`~/.claude/CLAUDE.md`, `~/.claude/settings.json`) and there is no built-in mechanism to point it elsewhere.

---

## Decision

All version-controlled Claude Code artifacts are maintained in the `dev-env` repo under `claude/` and **symlinked** into `~/.claude/`:

| `~/.claude/` path | Source in `dev-env` |
|---|---|
| `CLAUDE.md` | `claude/CLAUDE.md` |
| `settings.json` | `claude/settings.json` |
| `scripts/` | `claude/scripts/` (directory junction) |
| `skills/` | `claude/skills/` (directory junction) |
| `hooks/` | `claude/hooks/` (directory junction) |
| `routines/` | `claude/routines/` (directory junction) |
| `templates/` | `claude/templates/` (directory junction) |

`~/.claude/scheduled-tasks/` is deliberately **not** in this table — see Amendment below.

Machine-local paths (`scratch/`, `projects/`, `plans/`, `sessions/`, `backups/`, `ide/`, `shell-snapshots/`) are excluded from version control and listed in `.gitignore`.

`setup.sh` creates the symlinks/junctions on a fresh machine.

---

## Consequences

- Every change to global Claude Code config must go through a `dev-env` PR — changes are auditable, reviewable, and rollback-able.
- Editing `~/.claude/CLAUDE.md` directly is incorrect; the source of truth is `dev-env/claude/CLAUDE.md`. Both files carry a comment header warning against direct edits.
- On Windows, directory junctions (`New-Item -ItemType Junction`) are used instead of symlinks for directories, because NTFS symlinks require elevated privileges.
- Machine-local setup (first-time junction creation) is documented in `setup.sh` and must be re-run after a fresh clone.

---

## Amendment (2026-07-01)

The original table above listed `scheduled-tasks/` → `claude/routines/` (directory junction). That was backwards on two counts, discovered while investigating why a live scheduled task (`daily-journal-compose-local`, see [dev-env#464](https://github.com/brownm09/dev-env/issues/464)) had silently drifted from its canonical `claude/routines/` definition:

1. **Wrong path.** The real junction is `~/.claude/routines/` → `claude/routines/` — confirmed via `Get-Item -Force` (`LinkType=Junction`, `Target=C:\Users\brown\Git\dev-env\claude\routines`). `~/.claude/scheduled-tasks/` is a **separate, real, non-linked directory** that the `scheduled-tasks` MCP tool owns and writes to directly — it was never symlinked or junctioned, and `setup.sh` never attempted to create one there.
2. **Wrong mechanism implied.** Even the corrected mapping doesn't make a routine "live" by itself. The `scheduled-tasks` MCP tool always reads and writes `~/.claude/scheduled-tasks/<taskId>/SKILL.md`, regardless of what exists under `~/.claude/routines/`. Authoring (or editing) `claude/routines/<name>/SKILL.md` and merging it to `main` is necessary but not sufficient — a **separate, explicit** `create_scheduled_task` / `update_scheduled_task` call is required to materialize or refresh the live copy. Nothing enforces the two stay in sync afterward.

This was first correctly diagnosed in [dev-env#344](https://github.com/brownm09/dev-env/issues/344) (filed 2026-06-09) but the correction was never carried into the docs, so the wrong claim persisted for three weeks and directly enabled the `daily-journal-compose-local` drift: the live task was registered early on and never updated when the canonical routine was later fully specced out, including a Step-0 sync safety step ([ADR-013](013-sync-routine-worktree-skill.md)) the live copy consequently lacked.

**Why not make `scheduled-tasks/` itself a junction (closing the gap structurally instead of documenting it)?** `~/.claude/scheduled-tasks/` mixes reusable routines with one-off/manual task instances (smoke tests, single-issue implementation runs, one-time reminders) that have no business being version-controlled. A whole-directory junction is all-or-nothing — it would force every one-off task into `claude/routines/` (repo noise) or require relocating them outside the junctioned directory entirely (the `scheduled-tasks` tool gives no such configuration knob; the storage path is fixed). Keeping registration a deliberate, separate step is the correct shape given that mix; the fix here is to document it accurately and adopt a self-healing convention instead:

**Convention going forward:** a live task's prompt should read its own canonical `claude/routines/<name>/SKILL.md` at run time and follow it when present, falling back to an embedded copy only when it isn't reachable. `weekly-memory-audit` already does this (see its "dual-copy registration caveat" note and [ADR-069](069-weekly-memory-audit-routine.md)); `daily-journal-compose-local` was updated to match in the same session that produced this amendment.

---

## Amendment (2026-07-06)

The self-referencing convention adopted 2026-07-01 was applied to `daily-journal-compose-local`
in the same session that wrote it, but was **not** retroactively swept across the other routines
already registered at the time. Investigating dev-env#597 (Git Bash fork failures + an 88-worktree
pileup) found the exact same gap, twice more, in routines that predate the convention:

1. **`prune-stale-worktrees`** — the live copy was a hardcoded, pre-ADR-078 snapshot missing
   `--include-named`. It ran successfully every day but silently skipped 78 of 88 registered
   worktrees, since it never picked up the canonical routine's later fix.
2. **`reclaim-worktree-disk`** — authored with its own intended `0 */6 * * *` schedule in
   frontmatter, but never actually registered as a live task at all — it had never run
   automatically.

Both are now fixed to follow the established convention (canonical file read at run time via a
Step 0.5, embedded fallback only when unreachable) — see their SKILL.md files' own "dual-copy
registration caveat" notes.

**This is now the second independent occurrence of the same gap** (`daily-journal-compose-local`
being the first). The convention itself is sound, but relies on each routine's author remembering
to apply it and remains silent about routines that predate it. A full audit of the remaining
registered routines (`nightly-cover-letters`, `biweekly-retro`, `nightly-research`,
`reconcile-project-board`) for the same gap was out of scope for dev-env#597 and has **not** been
done as part of this amendment — flagged as follow-up work, not resolved here.

---

## Amendment (2026-07-08)

Investigating why `/propose`'s Step 3 and Step 11 reads of `~/.claude/templates/proposal.md` and
`pr-body.md` were failing on a machine (a `lifting-logbook` session, 2026-07-06) found that
`templates/` was never in the Decision table above, never in `setup.sh`'s link loop (`setup_windows()`
and `setup_unix()` both enumerate `scripts skills hooks` plus a separately-linked `routines` —
`templates` is in neither), and never in `claude/CLAUDE.md`'s copy of this table either — despite
`claude/templates/` (`proposal.md`, `pr-body.md`, `contributing.md`, `project-claude.md`,
`propose-config.json`) being fully committed since 2026-04-13 (#10), the same day this ADR was
written.

`setup.sh`'s cross-platform bootstrap was added 11 days later (2026-04-24) and simply never
enumerated `templates` — a day-one gap, not a regression, that both amendments above carried
forward unnoticed because neither touched this table's completeness against the actual `claude/`
directory listing.

**This is the second occurrence of this ADR's junction-table-itself-is-wrong gap class** — the
2026-07-01 amendment's scheduled-tasks topology correction is the first. (The 2026-07-06
amendment is a related but distinct gap: the routine self-reference *convention* not being
swept across pre-existing routines, not the junction table being incomplete or wrong.) Fixed by:

- Adding `templates` to `setup.sh`'s link loop in both `setup_windows()` and `setup_unix()`.
- Adding the `templates/` row to the Decision table above and to `claude/CLAUDE.md`'s copy.
- Creating the junction directly on the affected machine rather than waiting for a future
  `setup.sh` re-run.

Checked the remaining top-level `claude/` entries for the same gap: `usage-config.json` is read
via an absolute repo path in `claude/scripts/usage-snapshot.py`, not through `~/.claude/`. The
claim originally made here — that `setup-prompt.md` has "no programmatic reference at all" — was
itself wrong; see the addendum below. `templates/` (plus the `setup-prompt.md` checklist gap
found afterward) were the only actual gaps.

**Addendum (dev-env#614):** `claude/setup-prompt.md`'s Step 3 verification checklist
independently enumerates the same linked set (one `ls -la` per path) and carried the identical
`templates/` omission — a fourth occurrence of this gap class, missed by this amendment's own
"checked the remaining entries" pass because that pass looked for a *programmatic* dependency and
missed a *manual* checklist enumerating the same list. Fixed in the same PR that closes the
underlying testability gap this class of incident kept exposing: `setup.sh`'s two enumerations
(`setup_windows()` / `setup_unix()`) are now one shared `CLAUDE_FILE_LINKS` / `CLAUDE_DIR_LINKS`
array pair, and `claude/scripts/tests/test-setup-link-loop.sh` (CLAUDE.md Testing item 49) pins
that enumeration plus the extracted `link_claude_windows()` / `link_claude_unix()` functions'
output — so the next occurrence of this gap class is caught mechanically rather than needing a
fifth manual amendment.

---

## Amendment (2026-07-10) — scheduled tasks run under the app's global model, not `settings.json`

Investigating why the `prune-stale-worktrees` daily run on 2026-07-10 04:03 *greeted instead of
executing* ([dev-env#698](https://github.com/brownm09/dev-env/issues/698), fixed in
[#700](https://github.com/brownm09/dev-env/pull/700)) surfaced a scheduler-**runtime** behavior
distinct from — and compounding — the dual-copy convention gaps documented in the amendments above.

**Finding.** The `scheduled-tasks` scheduler runs each task under the **app's global model
default**, and **ignores the model declared in any project `settings.json`**. That global default
can change out from under a registered task with no config edit of the user's, so a task's effective
model can **silently drift**: here, six correct daily runs on `claude-opus-4-8` (07-04 → 07-09)
followed by a 07-10 run that came up on `claude-sonnet-5` and replied *"I'm ready to help. What
would you like to work on?"* (0 tool calls). The lower-capability model is not deterministically
broken (Sonnet 5 ran a *different* scheduled task correctly the same day) — it is an **intermittent
instruction-following lapse** on the XML-wrapped autonomous prompt, and a greet-instead-of-execute
run produces **no error and no notification**.

**Cross-cutting, not prune-specific.** This affects **every** scheduled task identically
(`reconcile-project-board`, `biweekly-retro`, `weekly-memory-audit`, `daily-journal-compose`, …), so
it is recorded centrally here and summarized in
[`docs/REFERENCE.md` → Routines](../REFERENCE.md#routines) — a maintainer debugging a *different*
task's model switch would not think to look in one routine's caveat. (The tactical #700 fix also
left a per-routine copy in `claude/routines/prune-stale-worktrees/SKILL.md`.)

**Mitigation pattern (for any routine).**

1. **Frontmatter `model:` pin** (e.g. `model: claude-opus-4-8`) in both the canonical and live
   copies — but **confirmed inert** (see the Verification note below): the scheduler does not honor
   frontmatter `model:`, consistent with the `create_scheduled_task` / `update_scheduled_task` MCP
   tools exposing no model parameter and `list_scheduled_tasks` no model field. The pin is kept but
   annotated inert (harmless when ignored; would activate only if the scheduler ever gains
   frontmatter-model support), and the imperative below is the sole *effective* mitigation.
2. **Execute-now / do-not-greet imperative** at the very **top and bottom** of the *live* prompt —
   the model-agnostic backstop, now **confirmed effective** (see the Verification note below). It
   must live in the live copy, not only the canonical one, because
   the greeting happens *before* any Step-0 canonical read-through is reached. Because the live copy
   is machine-local and not version-controlled, its exact strings are captured verbatim in the
   canonical routine's caveat for deterministic restore
   ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3).

**Verification (dev-env#703 item 2, 2026-07-14).** Confirmed against the four unattended
`prune-stale-worktrees` runs after the #700 fix: **all four (07-11 → 07-14) came up on
`claude-sonnet-5`, none on the pinned `claude-opus-4-8`** — the frontmatter pin is **inert**; the
scheduler ignores it. All four nonetheless **executed the routine in full** (17–18 tool calls each,
opening with an execution-intent line, not the 07-10 greeting), so the **do-not-greet imperative is
the sole effective, model-agnostic mitigation** and is empirically working. The pin is retained but
annotated inert in both copies (harmless; would activate only if the scheduler ever honors
frontmatter `model:`). Verified by replaying each run's transcript (model + first-output + tool-call
count); method and evidence on [dev-env#703](https://github.com/brownm09/dev-env/issues/703).

---

## References

- Engineering journal: `sessions/meta/2026-04-13-post-tool-use-hook-and-settings-into-dev-env.md`
- `dev-env/setup.sh` — bootstrap script for symlinks/junctions
- `claude/CLAUDE.md` § Dev-Env — symlink table and ownership rules
- [dev-env#344](https://github.com/brownm09/dev-env/issues/344) — original diagnosis of the reversed topology
- [dev-env#464](https://github.com/brownm09/dev-env/issues/464) — the drift incident that surfaced the undocumented consequence
- [dev-env#597](https://github.com/brownm09/dev-env/issues/597) — second occurrence, in `prune-stale-worktrees` and `reclaim-worktree-disk`
- [dev-env#606](https://github.com/brownm09/dev-env/issues/606) — second occurrence of the junction-table gap class, `templates/` never linked
- [dev-env#614](https://github.com/brownm09/dev-env/issues/614) — smoke test added for the link-loop enumeration; also caught the `setup-prompt.md` checklist's identical `templates/` omission
- [dev-env#698](https://github.com/brownm09/dev-env/issues/698) — `prune-stale-worktrees` greet-instead-of-execute run that surfaced the scheduler-model-selection finding (2026-07-10 amendment)
- [dev-env#700](https://github.com/brownm09/dev-env/pull/700) — tactical greet-guard fix (execute-now imperative + `model:` pin)
- [dev-env#703](https://github.com/brownm09/dev-env/issues/703) — central documentation of the model-selection finding, `model:`-pin efficacy verification, and live-imperative restorability
