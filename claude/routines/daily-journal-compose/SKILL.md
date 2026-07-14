---
name: daily-journal-compose
description: Assemble yesterday's session stubs across all projects into canonical daily journal entries and open PRs.
schedule: "0 7 * * *"
---

Compose yesterday's engineering journal entries for all active projects. Run fully autonomously — do not ask the user anything.

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
- **Never pass `--force` from this routine, under any circumstance, and never reason that `--force`
  semantics are implied.** If `/journal-compose` refuses because of the today-guard, that is
  **success** — it means there is nothing yet composable for that date, not a problem to work
  around. This routine's own date math (step 1 above) already guarantees the target is always
  yesterday, so the guard should never fire here at all; if it does, something upstream (the date
  computation, a config-sync race) is wrong, and the fix is to diagnose that — never to add
  `--force`. A prior run of this exact routine reasoned its way past this guard on a task-framing
  inference alone, without the guard ever actually refusing; the guard is now also mechanically
  enforced ([ADR-096](https://github.com/brownm09/dev-env/blob/main/docs/adr/096-journal-compose-mechanical-force-guard.md))
  so it can no longer be talked past, but this instruction is the cheap first line of defense. See
  [dev-env#631](https://github.com/brownm09/dev-env/issues/631).

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It lives verbatim
only in the machine-local live copy (`~/.claude/scheduled-tasks/daily-journal-compose-local/SKILL.md`,
which is **not** version-controlled), so the exact deployed strings are captured here — a machine
rebuild, or a live-copy regeneration from this canonical file, restores the hardened guard
**deterministically** rather than reconstructing it from memory. When (re)creating the live copy, paste
the **top** block as its first line (immediately after the YAML frontmatter) and the **bottom** block as
its last line; keep both verbatim, including the ASCII `--` in the top block and the em dash in the
bottom block.

_Top — first line of the live prompt:_

```text
EXECUTE NOW -- DO NOT GREET. This is an autonomous scheduled run; no human is present. Do NOT reply with a greeting, a question, or any variant of "how can I help" / "what would you like to work on" -- a concrete task is defined below and your FIRST output MUST be a tool call (begin with the first step below). If you catch yourself about to acknowledge, greet, or ask what to do, stop and begin executing the first step instead.
```

_Bottom — last line of the live prompt:_

```text
REMINDER: Begin immediately. Your first action is a tool call for the first step below — not a text reply. Do not greet or ask what to work on.
```
