---
name: reconcile-project-board
description: Add open issues missing from each configured repo's GitHub project board (across every repo under C:/Users/brown/Git with a .claude/hook-config.json) and surface any still missing a required field. Backstop for issues filed in background/spawn_task sessions where the add-hook is inert.
schedule: "0 6 * * *"
---

Reconcile every configured repo's GitHub project board against its own open issues. Run fully
autonomously — do not ask the user anything, and **never guess Impact or Why (or any other
required field)**.

> **Autonomous-run guard (do not strip when regenerating the live copy).** This is an unattended
> scheduled run with no human present. Do **not** open with a greeting, a question, or any "how can I
> help" / "what would you like to work on" reply — your **first output must be a tool call** (begin with
> the first step below). The live scheduled-task copy must carry this same imperative at the very top
> *and* bottom of its prompt, because the greeting-instead-of-execute failure it guards against happens
> *before* any canonical read-through step is reached. Rationale and incident history: the
> [`prune-stale-worktrees` reliability caveat](../prune-stale-worktrees/SKILL.md),
> [dev-env#698](https://github.com/brownm09/dev-env/issues/698), and
> [dev-env#703](https://github.com/brownm09/dev-env/issues/703) (which confirmed the frontmatter `model:`
> pin is **inert** — the scheduler ignores it — making this imperative the sole effective, model-agnostic
> mitigation). See the **Restorable live-copy imperative** at the bottom of this file.

**Why:** `post-tool-use.py` auto-adds each newly-created issue to its repo's board, but
PostToolUse hooks are inert in background / `spawn_task` / SDK-launched sessions
([ADR-053](../../docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md)), so issues
filed from such a session are silently never boarded — missing Impact, Why, and the Status
workflow. This routine is the nightly backstop that catches them, across every repo that has
adopted board tracking, not just dev-env (dev-env #447, #462; remediation of the gap ADR-053
documents, generalized by [ADR-070](../../docs/adr/070-reconcile-project-board-scan-dir.md)).

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`,
   `VERIFY_FILE=claude/scripts/reconcile-project-board.py`, `PREFIX=reconcile-project-board`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the reconcile script in scan-dir mode (live — it adds orphans to each configured
   repo's board and sets no field values):
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/reconcile-project-board.py" --scan-dir C:/Users/brown/Git
   ```
   The script discovers every git repo directly under `C:/Users/brown/Git` with a
   `.claude/hook-config.json` (`repo`/`project_number`/`project_owner` set) and reconciles
   each one; repos without one are skipped silently — board tracking is opt-in.
2. Report the script output per repo: how many orphan issues were added (vs. attempted — a
   partial failure shows as `Added M/N ... (K failed)`), and the per-issue list of issues
   still missing a required field (the script prints the exact `gh project item-edit`
   commands for each, under its own `Repo: <name>` heading).
3. Read the final aggregate `RESULT:` line (`repos_scanned=N repos_skipped=K repos_failed=F
   orphans_added=M add_failed=J needs_attention=L dry_run=...`) and send a push notification
   when any of:
   - `needs_attention` is greater than 0 — list the issue numbers (with their repo) that
     need a required field, so the user (or a later interactive session) can fill them.
     **Do not set the field yourself** — it requires human judgment per the no-guessing rule.
   - `add_failed` is greater than 0 — an orphan failed to add (e.g. a transient `gh` error);
     note it self-heals on the next nightly run since the issue stays orphaned, but surface
     it so a persistent failure doesn't go unnoticed.
   - `repos_failed` is greater than 0 — a configured repo's `gh` call failed for a reason
     other than a missing scope (network blip, access change, deleted repo); name the repo
     from the per-repo output above so it can be investigated.

**Constraints:**
- The script is **add-only + report-only**: it adds orphans and prints the `gh project
  item-edit` commands for missing fields, but never sets a field value and never mutates
  single-select *options* (so it cannot trip the option-mutation hazard documented in the
  global CLAUDE.md). Safe to run unattended.
- Requires the `project` gh scope. A missing scope is a token-level failure, not a per-repo
  one — the script detects it on the first repo it affects and aborts the **entire** scan
  immediately (every remaining repo would fail identically) rather than repeating the same
  error per repo. If the script prints the `gh auth refresh -s project` hint and exits 1,
  send a push notification with that hint and stop — do not attempt the refresh unattended.
- A single repo's non-scope `gh` failure (network blip, deleted repo, access change) does
  **not** abort the scan — it's isolated to that repo (counted in `repos_failed`) and the
  scan continues with the rest.
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`.

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It lives verbatim
only in the machine-local live copy (`~/.claude/scheduled-tasks/reconcile-project-board/SKILL.md`, which
is **not** version-controlled), so the exact deployed strings are captured here — a machine rebuild, or
a live-copy regeneration from this canonical file, restores the hardened guard **deterministically**
rather than reconstructing it from memory. When (re)creating the live copy, paste the **top** block as
its first line (immediately after the YAML frontmatter) and the **bottom** block as its last line; keep
both verbatim, including the ASCII `--` in the top block and the em dash in the bottom block.

_Top — first line of the live prompt:_

```text
EXECUTE NOW -- DO NOT GREET. This is an autonomous scheduled run; no human is present. Do NOT reply with a greeting, a question, or any variant of "how can I help" / "what would you like to work on" -- a concrete task is defined below and your FIRST output MUST be a tool call (begin with the first step below). If you catch yourself about to acknowledge, greet, or ask what to do, stop and begin executing the first step instead.
```

_Bottom — last line of the live prompt:_

```text
REMINDER: Begin immediately. Your first action is a tool call for the first step below — not a text reply. Do not greet or ask what to work on.
```
