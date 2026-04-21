---
name: daily-journal-compose
description: Assemble all today's session stubs across all projects into canonical daily journal entries and open PRs.
---

Compose today's engineering journal entries for all active projects.

**Objective:** Run /journal-compose for today's date once per project that has stubs, merging session stubs into canonical daily documents and opening a PR per project (or one combined PR if the skill handles all projects together).

**Steps:**
1. Determine today's date: run `date -u +%Y-%m-%d` in Git Bash (call it DATE).
2. Find all projects with stubs for today:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/*/DATE_*.stub.md 2>/dev/null
   ```
   Extract unique project directory names from the results.
3. If no stubs exist for any project, exit silently.
4. For each project that has stubs, invoke the journal-compose skill:
   `/journal-compose DATE`
   (The skill reads the current working directory's CLAUDE.md to determine the project path — run it from the appropriate project directory, or pass the project name if the skill supports it.)
5. The skill will:
   - Discover all `sessions/<project>/DATE_*.stub.md` files
   - Sort and merge them into the canonical 11-section document
   - Delete the stubs
   - Commit to the `draft/DATE` branch in `C:/Users/brown/Git/engineering-journal`
   - Open a PR to `main`
6. Return all PR URLs on completion.

**Constraints:**
- Engineering journal repo: `C:/Users/brown/Git/engineering-journal`
- Sessions root: `sessions/` — subdirectories are project names (e.g., `job-search`, `lifting-logbook`, `meta`)
- Never commit directly to `main`
- Use Git Bash syntax. Temp files go to `C:/Users/brown/.claude/scratch/`
- If a project's stubs were already composed today (canonical file already exists), skip that project
