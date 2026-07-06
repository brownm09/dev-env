---
name: prune-stale-worktrees
description: Remove Claude session worktrees whose branches have been merged into main, across all repos under C:/Users/brown/Git.
schedule: "0 8 * * *"
---

Prune stale Claude session worktrees across all git repos under `C:/Users/brown/Git`. Run fully autonomously — do not ask the user anything.

**Objective:** For every git repo directly under `C:/Users/brown/Git`, remove all worktrees (both `claude/*` and hand-named branches, e.g. `feat/`/`fix/`/`docs/` — via `--include-named`) whose branches are fully merged into `origin/main` and have no uncommitted changes. Also remove any non-primary worktrees accidentally checked out on `main`. Report the pruned/skipped summary per repo and a combined total.

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`, `VERIFY_FILE=claude/scripts/prune-merged-worktrees.py`, `PREFIX=prune-stale-worktrees`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the prune script in scan-dir mode, including named (non-`claude/*`) branches
   ([ADR-078](../../../docs/adr/078-opt-in-named-branch-worktree-pruning.md)):
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/prune-merged-worktrees.py" \
     --scan-dir C:/Users/brown/Git --include-named
   ```
2. Report the per-repo output: how many worktrees were pruned and skipped in each repo, and the reason for each skip.
3. If any repo shows `claude/*` branches skipped due to "not merged" status, list them and send a push notification summarizing the count and branch names so the user can investigate.

**Constraints:**
- Script uses `git branch -d` (not `-D`) and `git worktree remove` (no `--force`) — safe by default
- Repos with no GitHub remote are silently skipped
- Never remove the current session's worktree, regardless of branch name
- With `--include-named`, a hand-named branch is held to the exact same merged/dirty/liveness bar as a `claude/*` branch — no looser check is applied (ADR-078)
- Never remove a worktree with an active Claude session — transcript activity within 24h. This routine runs out-of-process and cannot see other sessions via cwd, so the script's transcript-mtime guard is what protects them (ADR-051)
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`

---

> **Dual-copy registration caveat (dev-env#344, [ADR-003 amendment](../../../docs/adr/003-config-in-version-control.md)).**
> This file is the **canonical, version-controlled** definition
> (`dev-env/claude/routines/prune-stale-worktrees/`, surfaced at
> `~/.claude/routines/prune-stale-worktrees/` via the directory junction). The scheduler reads a
> **separate** live copy at `~/.claude/scheduled-tasks/prune-stale-worktrees/SKILL.md`, materialized
> by the `create_scheduled_task` MCP tool — the two do **not** auto-sync by default. This routine
> already drifted once ([dev-env#597](https://github.com/brownm09/dev-env/issues/597)): the live
> copy ran a stale pre-ADR-078 invocation missing `--include-named`, silently skipping 78 of 88
> registered worktrees every day despite running successfully. Per the convention ADR-003's
> amendment establishes, the live copy now reads this file at run time and defers to it when
> present, falling back to an embedded copy only when it is missing or unreadable — so a future
> edit here takes effect immediately without a separate re-registration step, though the embedded
> fallback should still be refreshed when this file's *steps* change materially.
