# ADR-051: Worktree Liveness Guard — Skip Active-Session Worktrees in Prune/Reclaim

**Date:** 2026-06-22
**Status:** Accepted
**Tags:** worktrees, liveness, prune, reclaim, transcript, session, data-loss, routines, safety, hooks

---

## Context

On 2026-06-21 (~23:42 local) the `prune-stale-worktrees` routine removed the Claude-managed
worktree `pensive-taussig-448bc7` **while a Claude Code session was actively running inside it**
(dev-env#384). `git worktree remove` deleted the `.git` link and the admin dir
`…/.git/worktrees/pensive-taussig-448bc7/` and deregistered the worktree — the session's
transcript was still being written at that moment (last line `23:42:10`). The orphaned session
then had every `Write`/`Edit` blocked by the ADR-024 worktree-path guard, and bare `git` from
the session cwd silently resolved to the canonical main checkout — unsafe.

`prune-merged-worktrees.py` is the only worktree *remover*: `reclaim-worktree-disk.py` and
`post-pr-merge-reclaim.py` (ADR-037/045) merely `shutil.rmtree` regenerable `node_modules`/`.turbo`.
Two pre-existing facts combined to remove a live worktree:

1. **`is_merged()` is true for a branch sitting *at* `origin/main`.** It runs
   `git merge-base --is-ancestor <branch> origin/main`, which returns 0 when the branch tip *is*
   `origin/main` (a commit is its own ancestor). A fresh Claude worktree that hasn't committed yet
   (0 commits ahead) is therefore classified "merged" — exactly the incident's state
   (`claude/pensive-taussig-448bc7` at `origin/main`, *not* merged-and-gone).
2. **No liveness signal.** The only active-session protection was `path == os.getcwd()`, which
   shields only the *pruning process's own* worktree. The routine runs out-of-process in its own
   worktree (ADR-013 sync preamble) and passes no `--protect-cwd`, so it cannot see a live session
   in any *other* worktree. With the tree momentarily clean, removal succeeded.

An out-of-process maintenance routine fundamentally cannot detect another worktree's session via
`os.getcwd()`/`--protect-cwd`. It needs a signal that crosses the process boundary. Claude Code
writes each session's transcript to `~/.claude/projects/<slug>/<uuid>.jsonl`, where `<slug>` is the
session cwd with every `:` `\` `/` `.` replaced by `-`. Each worktree has a distinct cwd, hence its
own transcript dir, so a recent newest-`*.jsonl` mtime there is a reliable cross-process heartbeat —
verified empirically: the deleted worktree's transcript stopped at the removal instant, while a
live worktree's transcript updates every few seconds. Claude Code exposes no per-session lock file
(only the `<uuid>.jsonl` plus a `subagents/` subdir), so the transcript mtime is the available
signal.

## Decision

Add a shared, policy-free liveness helper and call it from both maintenance scripts' per-worktree
loops, **in addition to** (never instead of) the existing merged/clean/cwd checks.

### Shared helper — `claude/scripts/_worktree_liveness.py`

Import-only module (no `_winsubp`, no `main()`) so its helpers unit-test offline:

- `encode_project_slug(path)` — replicates the `:`/`\`/`/`/`.` → `-` transcript-dir encoding.
- `transcript_dir_for(worktree, projects_root)` — exact-slug dir, else a fallback matching a
  `projects/` subdir ending in `-worktrees-<basename>` (the random worktree basename is globally
  unique, hedging a future encoding change; a collision only ever over-protects).
- `newest_jsonl_mtime(dir)` — newest `*.jsonl` mtime found **recursively** (so an active
  `<uuid>/subagents/*.jsonl` keeps a worktree live even when the top transcript is briefly quiet).
- `worktree_session_is_live(worktree, *, window_seconds, …)` — composes the above; `now`/
  `projects_root` injectable for tests.

**Fail-safe and additive.** A missing/empty/unreadable transcript dir yields `False` (not live ⇒
eligible) — identical to the pre-guard behavior for genuinely-idle worktrees, so cleanup of
abandoned worktrees keeps working. The guard only ever *adds* a skip, so a bug in it can at worst
over-protect (less cleanup), **never** remove or strip more than before.

### Window scales with the action's blast radius

Each script owns its window constant (the module is policy-free); both accept `--liveness-window-min`:

- **`prune-merged-worktrees.py` — 24h.** `git worktree remove` *severs* the session, so the
  destructive op gets the long guard. The only cost is a merged worktree lingering up to a day
  longer before removal.
- **`reclaim-worktree-disk.py` — 6h.** Stripping `node_modules` is self-healing (ADR-016 reinstalls
  on next use) and only disrupts a build/dev-server running *right now*, so a short window keeps
  disk reclamation aggressive against ENOSPC (the failure ADR-037/045 exist to prevent) and matches
  the routine's 6-hourly cadence. `--protect-cwd` already shields the current session; this guard
  additionally shields live sessions in *other* worktrees the routine cannot otherwise see.

The guard sits immediately after each loop's primary/cwd check, before the merged/dirty checks, so
the `active Claude session (recent transcript activity)` skip reason takes precedence.

### Deliberately *not* tightening `is_merged()`

Removing a clean, sessionless worktree whose branch never diverged is *correct* cleanup (prune's
job); tightening `is_merged()` to exclude at-`origin/main` branches would let abandoned fresh
worktrees accumulate forever. The liveness guard fixes the only harmful case — an *active* session —
which is precisely "in addition to the existing merged-branch check." The at-`origin/main`
classification stays.

## Consequences

- A worktree with transcript activity within its window is skipped by both prune and reclaim, across
  every repo the routine scans — so an out-of-process routine can no longer sever a live session in
  any repo, not just protect its own cwd.
- A recently-active *merged* worktree is removed/stripped one run later (once its transcript goes
  quiet past the window) instead of immediately — an intentional, safe delay.
- **Limitation:** a session left idle at the prompt longer than its window (24h prune / 6h reclaim)
  loses protection, because mtime cannot distinguish "abandoned" from "idle but will resume." A true
  process heartbeat would close this gap; Claude Code exposes none today. The windows are tuned so
  this is rare and the failure is the survivable one (a stripped `node_modules` reinstalls; a removed
  idle-merged worktree is recreatable).
- The two-window split is observable: in the verification dry-run, a worktree active within 24h but
  not 6h was protected by prune yet eligible for reclaim — the design working as intended.
- Adds one `is_dir()` + a small recursive `*.jsonl` glob per candidate worktree (few per repo);
  the `projects/` scandir fallback fires only when the exact slug is absent. Negligible.

## References

- [ADR-024](024-worktree-path-guard-hook.md) — the worktree-path guard whose orphaned-worktree
  addendum the severed session tripped; this ADR removes the upstream cause
- [ADR-037](037-worktree-disk-reclamation.md) — `reclaim-worktree-disk.py` + the 6-hourly routine
  the 6h reclaim window matches
- [ADR-045](045-pre-install-freespace-gate.md) — the ENOSPC pressure that argues for keeping the
  reclaim window short
- [ADR-013](013-sync-routine-worktree-skill.md) — why the routine runs out-of-process (and thus
  cannot see other sessions via cwd)
- dev-env#384 — the incident this ADR remediates; discovered alongside #381 (PostToolUse hooks not
  firing)
