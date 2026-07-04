---
name: daily-journal-compose
description: Assemble all today's session stubs across all projects into canonical daily journal entries and open PRs.
schedule: "0 7 * * *"
---

Compose today's engineering journal entries for all active projects. Run fully autonomously — do not ask the user anything.

**Objective:** If any project has stubs dated today, run `/journal-compose` once (it fans out
across projects internally), then report the resulting PR URL(s).

**Steps:**
0. **Fetch the engineering-journal repo** (read-only) before reading any stubs:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal fetch origin
   ```
   No canonical-checkout sync is needed here — `/journal-compose` isolates itself into its own
   detached worktree ([ADR-081](https://github.com/brownm09/dev-env/blob/main/docs/adr/081-journal-compose-worktree-isolation.md))
   and never reads from or writes to the canonical's working tree. Skipping the sync also avoids
   this routine mutating the canonical checkout itself (the prior `sync-routine-worktree` call
   could `rebase`/`reset --hard` it), the same class of hazard ADR-081 closes for the skill it
   invokes.

1. Determine today's date in Git Bash:
   ```bash
   DATE=$(date -u +%Y-%m-%d)
   ```
2. Check the remote for today's stubs. The canonical checkout permanently rests on `main` now
   (ADR-081) and never holds stubs directly — read from `origin/draft/${DATE}` instead of any
   local working tree:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal show-ref --verify --quiet \
     refs/remotes/origin/draft/${DATE} || exit 0
   git -C C:/Users/brown/Git/engineering-journal ls-tree -r "origin/draft/${DATE}" --name-only \
     | grep -E "^sessions/[^/]+/${DATE}_[0-9]{6}\.stub\.md$" | sort
   ```
   Stub filenames follow the pattern `YYYY-MM-DD_HHMMSS.stub.md` where `HHMMSS` is the **local**
   session start time (per `claude/CLAUDE.md`'s stub-filename convention — not UTC).
3. If the `ls-tree`/`grep` above produced no output (or the branch didn't exist, per the
   `show-ref` guard above), exit silently with no output.
4. Extract the unique project directory names from the matched paths (the path segment between `sessions/` and the filename).
5. Run `/journal-compose ${DATE}` **once** — its own multi-project mode fans out per project
   internally and composes them in parallel; do not loop and invoke it once per project (that
   directly contradicts the skill's own "do NOT compose projects sequentially" guidance). The
   `/journal-compose` skill:
   - Creates an isolated compose worktree from `origin/draft/${DATE}` (ADR-081) — everything
     below happens inside that worktree, not the canonical checkout
   - Discovers all `sessions/<project>/${DATE}_*.stub.md` files for every project with stubs
   - Merges each project's stubs into the canonical 11-section document
   - Deletes the stubs and reconciles any resolved open-PR shards
   - Commits and opens one PR (covering every project composed in this run)
   If a canonical document for a project already exists for today (i.e., stubs were already
   composed), the skill skips that project on its own.
6. Collect and return the PR URL(s) produced.

**Constraints:**
- Engineering journal repo: `C:/Users/brown/Git/engineering-journal`
- Sessions root: `sessions/` — subdirectories are project names (e.g., `job-search`, `lifting-logbook`, `meta`)
- Never commit directly to `main`
- Use Git Bash syntax. Temp files go to `C:/Users/brown/.claude/scratch/`
- Never prompt the user. If stubs span multiple projects, the single `/journal-compose` call's
  own multi-project mode handles them in parallel — do not ask, and do not invoke the skill more
  than once per run.
