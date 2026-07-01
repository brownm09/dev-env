---
name: reconcile-project-board
description: Add open dev-env issues missing from the Dev Env board (#3) and surface any still missing Impact/Why. Backstop for issues filed in background/spawn_task sessions where the add-hook is inert.
schedule: "0 6 * * *"
---

Reconcile the **Dev Env** GitHub project board (#3) against the repo's open issues. Run fully
autonomously — do not ask the user anything, and **never guess Impact or Why**.

**Why:** `post-tool-use.py` auto-adds each newly-created dev-env issue to the board, but
PostToolUse hooks are inert in background / `spawn_task` / SDK-launched sessions
([ADR-053](../../docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md)), so issues
filed from such a session are silently never boarded — missing Impact, Why, and the Status
workflow. This routine is the nightly backstop that catches them (dev-env #447; remediation of
the gap ADR-053 documents).

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`,
   `VERIFY_FILE=claude/scripts/reconcile-project-board.py`, `PREFIX=reconcile-project-board`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the reconcile script (live — it adds orphans to the board and sets no field values):
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/reconcile-project-board.py"
   ```
   The script reads `.claude/hook-config.json` from the canonical dev-env checkout (it
   canonicalizes its own worktree path), so no `--repo-root` is needed.
2. Report the script output: how many orphan issues were added (vs. attempted — a partial
   failure shows as `Added M/N ... (K failed)`), and the per-issue list of issues still
   missing Impact and/or Why (the script prints the exact `gh project item-edit` commands
   for each).
3. Read the final `RESULT:` line (`orphans_added=M add_failed=K needs_attention=N
   dry_run=...`) and send a push notification when either:
   - `needs_attention` is greater than 0 — list the issue numbers that need Impact/Why, so
     the user (or a later interactive session) can fill them. **Do not set Impact/Why
     yourself** — they require human judgment per the no-guessing rule.
   - `add_failed` is greater than 0 — an orphan failed to add (e.g. a transient `gh`
     error); note it self-heals on the next nightly run since the issue stays orphaned,
     but surface it so a persistent failure doesn't go unnoticed.

**Constraints:**
- The script is **add-only + report-only**: it adds orphans and prints the `gh project
  item-edit` commands for missing fields, but never sets a field value and never mutates
  single-select *options* (so it cannot trip the option-mutation hazard documented in the
  global CLAUDE.md). Safe to run unattended.
- Requires the `project` gh scope. If the script prints the `gh auth refresh -s project` hint
  and exits 1, send a push notification with that hint and stop — do not attempt the refresh
  unattended.
- This routine reconciles dev-env's board only. Generalizing to every project-configured repo
  is tracked as a follow-up (see dev-env #447).
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`.
