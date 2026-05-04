---
name: nightly-cover-letters
description: Nightly batch cover-letter processing — runs /batch-cover-letters on the career-playbook repo at 2 AM local. Produces a PR with completed artifacts and flags items needing Mike's review in _review_queue.md.
schedule: "0 7 * * *"
# CDT (Apr–Nov): 0 7 * * *  → 2:00 AM CDT (UTC−5) — update to "0 8 * * *" at Nov DST-end; revert Mar DST-start
# CST (Nov–Mar): 0 8 * * *  → 2:00 AM CST (UTC−6)
---

Run the batch cover-letter pipeline on the career-playbook repo.

Never call AskUserQuestion. Run fully autonomously.

## Step 0 — Verify repo

```bash
REPO="C:/Users/brown/Git/career-playbook"
```

Verify the repo directory exists:

```bash
if [ ! -d "$REPO" ]; then
  # send push notification and exit
fi
```

If the directory does not exist: send a push notification —
"nightly-cover-letters: repo not found at ${REPO} — skipping batch run" — and exit.

Check out `main` and ensure it is up to date:

```bash
git -C "$REPO" checkout main
```

If checkout fails (e.g., `main` is checked out in a worktree): send a push notification —
"nightly-cover-letters: git checkout main failed — is main in a worktree? Check ${REPO}" —
and exit.

```bash
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
