---
name: prune-stale-worktrees
description: Remove Claude session worktrees whose branches have been merged into main, across all repos under C:/Users/brown/Git.
schedule: "0 8 * * *"
---

Prune stale Claude session worktrees across all git repos under `C:/Users/brown/Git`. Run fully autonomously — do not ask the user anything.

**Objective:** For every git repo directly under `C:/Users/brown/Git`, remove all `claude/*` worktrees whose branches are fully merged into `origin/main` and have no uncommitted changes. Also remove any non-primary worktrees accidentally checked out on `main`. Report the pruned/skipped summary per repo and a combined total.

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`, `VERIFY_FILE=claude/scripts/prune-merged-worktrees.py`, `PREFIX=prune-stale-worktrees`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the prune script in scan-dir mode:
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/prune-merged-worktrees.py" \
     --scan-dir C:/Users/brown/Git
   ```
2. Report the per-repo output: how many worktrees were pruned and skipped in each repo, and the reason for each skip.
3. If any repo shows `claude/*` branches skipped due to "not merged" status, list them and send a push notification summarizing the count and branch names so the user can investigate.

**Constraints:**
- Script uses `git branch -d` (not `-D`) and `git worktree remove` (no `--force`) — safe by default
- Repos with no GitHub remote are silently skipped
- Never remove the current session's worktree or any non-`claude/*` branch worktree
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`
