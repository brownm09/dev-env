---
name: prune-stale-worktrees-ll
description: Remove Claude session worktrees in lifting-logbook whose branches have been merged into main.
schedule: "0 1 * * 0"
---

Prune stale Claude session worktrees in the lifting-logbook repo. Run fully autonomously — do not ask the user anything.

**Objective:** Remove all `claude/*` worktrees in lifting-logbook whose branches are fully merged into `origin/main` and have no uncommitted changes. Also remove any non-primary worktrees accidentally checked out on `main`. Report the pruned/skipped summary.

**Steps:**
1. Run the prune script targeting lifting-logbook:
   ```bash
   python C:/Users/brown/Git/dev-env/claude/scripts/prune-merged-worktrees.py \
     --repo-path C:/Users/brown/Git/lifting-logbook
   ```
   The `--repo-path` flag tells the script which repo's worktrees to scan and which GitHub remote to query for squash-merged PRs.
2. Report the output: how many worktrees were pruned, how many skipped, and the reason for each skip.
3. If `git worktree list` in lifting-logbook shows any `claude/*` branches that the script skipped due to "not merged" status, list them and send a push notification summarizing the count and branch names so the user can investigate.

**Constraints:**
- Lifting-logbook repo: `C:/Users/brown/Git/lifting-logbook`
- Script uses `git branch -d` (not `-D`) and `git worktree remove` (no `--force`) — safe by default
- Never remove the current session's worktree or the primary worktree
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`
