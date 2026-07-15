---
name: nightly-cover-letters
description: Nightly batch cover-letter processing — runs /batch-cover-letters on the career-playbook repo at 2 AM local. Produces a PR with completed artifacts and flags items needing Mike's review in _review_queue.md.
schedule: "0 7 * * *"
# CDT (Apr–Nov): 0 7 * * *  → 2:00 AM CDT (UTC−5) — update to "0 8 * * *" at Nov DST-end; revert Mar DST-start
# CST (Nov–Mar): 0 8 * * *  → 2:00 AM CST (UTC−6)
---

Run the batch cover-letter pipeline on the career-playbook repo.

Never call AskUserQuestion. Run fully autonomously.

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

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It would live
verbatim only in the machine-local live copy (`~/.claude/scheduled-tasks/nightly-cover-letters/SKILL.md`
— **not currently registered** as a live scheduled task; this capture is for whenever it is registered,
and the live copy is **not** version-controlled). The exact strings are captured here so a future
registration restores the hardened guard **deterministically** rather than reconstructing it from
memory. When (re)creating the live copy, paste the **top** block as its first line (immediately after
the YAML frontmatter) and the **bottom** block as its last line; keep both verbatim, including the ASCII
`--` in the top block and the em dash in the bottom block.

_Top — first line of the live prompt:_

```text
EXECUTE NOW -- DO NOT GREET. This is an autonomous scheduled run; no human is present. Do NOT reply with a greeting, a question, or any variant of "how can I help" / "what would you like to work on" -- a concrete task is defined below and your FIRST output MUST be a tool call (begin with the first step below). If you catch yourself about to acknowledge, greet, or ask what to do, stop and begin executing the first step instead.
```

_Bottom — last line of the live prompt:_

```text
REMINDER: Begin immediately. Your first action is a tool call for the first step below — not a text reply. Do not greet or ask what to work on.
```
