---
name: daily-journal-compose
description: Assemble yesterday's session stubs across all projects into canonical daily journal entries and open PRs.
schedule: "0 7 * * *"
---

Compose yesterday's engineering journal entries for all active projects. Run fully autonomously — do not ask the user anything.

**Objective:** If any project has stubs dated yesterday, run `/journal-compose` once (it fans out
across projects internally), then report the resulting PR URL(s).

**Steps:**
0. **Fetch the engineering-journal repo** (read-only) before reading any stubs:
   ```bash
   git -C C:/Users/brown/Git/engineering-journal fetch origin
   ```
   No canonical-checkout sync is needed here — `/journal-compose` isolates itself into its own
   detached worktree ([ADR-082](https://github.com/brownm09/dev-env/blob/main/docs/adr/082-journal-compose-worktree-isolation.md))
   and never reads from or writes to the canonical's working tree. Skipping the sync also avoids
   this routine mutating the canonical checkout itself (the prior `sync-routine-worktree` call
   could `rebase`/`reset --hard` it), the same class of hazard ADR-082 closes for the skill it
   invokes.

1. Determine **yesterday's local calendar date** in Git Bash — local time, not UTC, to match the
   stub-filename/branch-naming convention (`claude/CLAUDE.md`), and yesterday rather than today so
   this always targets a day that's genuinely complete: `/journal-compose`'s today-guard
   ([ADR-017](https://github.com/brownm09/dev-env/blob/main/docs/adr/017-journal-compose-today-guard.md))
   would otherwise refuse every run, since neither this routine nor its Windows Task Scheduler
   counterpart (`journal-compose-with-retry.sh`) ever passes `--force`
   ([ADR-084](https://github.com/brownm09/dev-env/blob/main/docs/adr/084-nightly-compose-targets-yesterday.md)).
   ```bash
   DATE=$(date -d yesterday +%Y-%m-%d)
   ```
2. Check the remote for yesterday's stubs. The canonical checkout permanently rests on `main` now
   (ADR-082) and never holds stubs directly — read from `origin/draft/${DATE}` instead of any
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
   - Creates an isolated compose worktree from `origin/draft/${DATE}` (ADR-082) — everything
     below happens inside that worktree, not the canonical checkout
   - Discovers all `sessions/<project>/${DATE}_*.stub.md` files for every project with stubs
   - Merges each project's stubs into the canonical 11-section document
   - Deletes the stubs and reconciles any resolved open-PR shards
   - Commits and opens one PR (covering every project composed in this run)
   If a canonical document for a project already exists for that date (i.e., stubs were already
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
