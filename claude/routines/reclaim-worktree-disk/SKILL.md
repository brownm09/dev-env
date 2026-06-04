---
name: reclaim-worktree-disk
description: Strip regenerable node_modules/.turbo from idle Claude worktrees across all repos under C:/Users/brown/Git, reclaiming disk between weekly prune runs.
schedule: "0 */6 * * *"
---

Reclaim regenerable disk artifacts from idle Claude session worktrees across all git repos under `C:/Users/brown/Git`. Run fully autonomously — do not ask the user anything.

**Objective:** For every git repo directly under `C:/Users/brown/Git`, strip `node_modules` and `.turbo` from Claude-managed worktrees (those under `.claude/worktrees/`) that are **idle** — clean working tree **and** (branch merged into `origin/main` **or** zero commits ahead of `origin/main`). These artifacts are regenerable: `worktree-npm-install.py` (ADR-016) reinstalls `node_modules` on the next prompt in any Claude-managed worktree, so reclamation is self-healing. This reclaims the bulk of duplicated dependencies without removing the worktree, preventing C: saturation between the weekly `prune-stale-worktrees` runs (dev-env#306). Report reclaimed totals per repo and a combined total.

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`, `VERIFY_FILE=claude/scripts/reclaim-worktree-disk.py`, `PREFIX=reclaim-worktree-disk`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the reclaim script in scan-dir mode:
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/reclaim-worktree-disk.py" \
     --scan-dir C:/Users/brown/Git
   ```
2. Report the per-repo output: how much was reclaimed in each repo, and the reason for each skipped worktree.
3. If the combined total reclaimed is meaningful (≥ 1 GB), send a push notification summarizing the amount reclaimed so the user has visibility into disk hygiene.

**Constraints:**
- The script deletes only `node_modules` and `.turbo` — never the worktree, branch, or any git state.
- Only worktrees under `.claude/worktrees/` are considered (they auto-reinstall via ADR-016). The primary repo and manual sibling worktrees are left untouched.
- Dirty worktrees, the primary worktree, the current/protected worktree, and worktrees with unpushed commits ahead of `origin/main` (and not merged) are never touched.
- Repos with no GitHub remote are silently skipped.
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`.
