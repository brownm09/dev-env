---
name: daily-journal-compose
description: Assemble all today's session stubs across all projects into canonical daily journal entries and open PRs.
---

Compose today's engineering journal entries for all active projects. Run fully autonomously — do not ask the user anything.

**Objective:** For each project directory that contains stubs dated today, run /journal-compose sequentially, then report all PR URLs.

**Steps:**
1. Determine today's date in Git Bash:
   ```bash
   DATE=$(date -u +%Y-%m-%d)
   ```
2. Find all stub files for today across all projects:
   ```bash
   ls C:/Users/brown/Git/engineering-journal/sessions/*/${DATE}_*.stub.md 2>/dev/null | sort
   ```
   Stub filenames follow the pattern `YYYY-MM-DD_HHMMSS.stub.md` where `HHMMSS` is the UTC session start time.
3. If no stubs exist, exit silently with no output.
4. Extract the unique project directory names from the matched paths (the path segment between `sessions/` and the filename).
5. For each project directory in sorted order, run `/journal-compose ${DATE}` with that project in scope. The `/journal-compose` skill:
   - Discovers all `sessions/<project>/${DATE}_*.stub.md` files, sorted by filename (i.e., by UTC start time)
   - Merges them into the canonical 11-section document
   - Deletes the stubs
   - Commits to the `draft/${DATE}` branch in `C:/Users/brown/Git/engineering-journal`
   - Opens a PR to `main`
   If a canonical document for that project already exists for today (i.e., stubs were already composed), skip that project.
6. Collect and return all PR URLs produced.

**Constraints:**
- Engineering journal repo: `C:/Users/brown/Git/engineering-journal`
- Sessions root: `sessions/` — subdirectories are project names (e.g., `job-search`, `lifting-logbook`, `meta`)
- Never commit directly to `main`
- Use Git Bash syntax. Temp files go to `C:/Users/brown/.claude/scratch/`
- Never prompt the user. If stubs span multiple projects, compose each one sequentially without asking.
