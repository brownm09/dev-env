---
name: prune-stale-worktrees
description: Remove Claude session worktrees whose branches have been merged into main.
schedule: "0 0 * * 0"
---

Prune stale Claude session worktrees in the dev-env repo. Run fully autonomously — do not ask the user anything.

**Objective:** Remove all `claude/*` worktrees whose branches are fully merged into `origin/main` and have no uncommitted changes. Report the pruned/skipped summary.

**Steps:**
1. Run the prune script from the dev-env repo root:
   ```bash
   python C:/Users/brown/Git/dev-env/claude/scripts/prune-merged-worktrees.py
   ```
2. Report the output: how many worktrees were pruned, how many skipped, and the reason for each skip.
3. If `git worktree list` in dev-env shows any `claude/*` branches that the script skipped due to "not merged" status, list them and send a push notification summarizing the count and branch names so the user can investigate.

**Constraints:**
- Dev-env repo: `C:/Users/brown/Git/dev-env`
- Script uses `git branch -d` (not `-D`) and `git worktree remove` (no `--force`) — safe by default
- Never remove the current session's worktree or any non-`claude/*` branch worktree
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`
