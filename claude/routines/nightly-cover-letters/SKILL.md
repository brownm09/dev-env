---
name: nightly-cover-letters
description: Nightly batch cover-letter processing — runs /batch-cover-letters on the career-playbook repo at 2 AM local. Produces a PR with completed artifacts and flags items needing Mike's review in _review_queue.md.
schedule: "0 7 * * *"
# CDT (Apr–Nov): 0 7 * * *  → 2:00 AM CDT (UTC−5) — update to "0 8 * * *" at Nov DST-end; revert Mar DST-start
# CST (Nov–Mar): 0 8 * * *  → 2:00 AM CST (UTC−6)
---

Run the batch cover-letter pipeline on the career-playbook repo.

Never call AskUserQuestion. Run fully autonomously.

## Step 0 — Sync the working tree

Read `~/.claude/skills/sync-routine-worktree/SKILL.md` and execute its **Behavior** section
end-to-end with the parameters below. The skill brings the career-playbook working tree current
with `origin/main` and verifies the batch skill file is present. It handles repo existence,
fetch failure, branch-class-aware sync (Claude-managed worktree branch / `main` / other), and
abort-on-rebase-failure with push notification.

Parameters:
- `REPO` = `C:/Users/brown/Git/career-playbook`
- `VERIFY_FILE` = `.claude/skills/batch-cover-letters/SKILL.md`
- `PREFIX` = `nightly-cover-letters`

If the sync skill returns **ABORT**, exit immediately. The notification has already been sent;
do not re-notify or fall back to running against stale state.

## Step 1 — Run the batch skill

Read `${REPO}/.claude/skills/batch-cover-letters/SKILL.md` in full. Execute it as the active
skill with cwd set to `${REPO}`.

The batch skill handles all JD processing, artifact writing, `_review_queue.md` updates,
branch creation, committing, PR creation, and push notification. This routine's job is only
to ensure the working tree is current with `origin/main` before the skill runs.

## Constraints

- **Never call AskUserQuestion** — fully autonomous
- **Repo path:** `C:/Users/brown/Git/career-playbook`
- **Scratch dir:** `C:/Users/brown/.claude/scratch/` — all temp files; never `/tmp/`
- **No `jq`** — use `node -e` for JSON operations if needed
- **Model:** Sonnet
