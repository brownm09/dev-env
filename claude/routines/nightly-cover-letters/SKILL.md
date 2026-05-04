---
name: nightly-cover-letters
description: Nightly batch cover-letter processing — runs /batch-cover-letters on the career-playbook repo at 11 PM local. Produces a PR with completed artifacts and flags items needing Mike's review in _review_queue.md.
schedule: "0 4 * * *"
# CDT (Apr–Nov): 0 4 * * *  → 11:00 PM CDT (UTC−5)
# CST (Nov–Mar): 0 5 * * *  → 11:00 PM CST (UTC−6)
---

Run the batch cover-letter pipeline on the career-playbook repo.

Never call AskUserQuestion. Run fully autonomously.

## Step 0 — Verify repo

```bash
REPO="C:/Users/brown/Git/career-playbook"
```

Verify the repo exists and the `main` branch is up to date:

```bash
git -C "$REPO" checkout main
git -C "$REPO" pull origin main
```

If the pull fails (network error, merge conflict): send a push notification —
"nightly-cover-letters: git pull failed — skipping batch run. Check repo state at ${REPO}" —
and exit.

## Step 1 — Run the batch skill

Read `${REPO}/.claude/skills/batch-cover-letters/SKILL.md` in full. Execute it as the active
skill with cwd set to `${REPO}`.

The batch skill handles all JD processing, artifact writing, `_review_queue.md` updates,
branch creation, committing, PR creation, and push notification. This routine's job is only
to ensure the repo is on a clean `main` before the skill runs.

## Constraints

- **Never call AskUserQuestion** — fully autonomous
- **Repo path:** `C:/Users/brown/Git/career-playbook`
- **Scratch dir:** `C:/Users/brown/.claude/scratch/` — all temp files; never `/tmp/`
- **No `jq`** — use `node -e` for JSON operations if needed
- **Model:** Sonnet
