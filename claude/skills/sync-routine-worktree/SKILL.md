---
name: sync-routine-worktree
description: Sync the working tree of a target repo to origin/main before a scheduled-task routine reads repo-resident files. Routines invoke this as Step 0 to ensure they don't run against stale skills, contexts, or queue files when launched into a worktree branch cut from an older main.
---

Sync a target repo's working tree to current `origin/main`. Use this from a scheduled-task routine before reading any file that lives in the target repo (a skill, a context file, a queue file).

This skill is invoked **by other routines**, not directly by users. It is a building block, not an end-user command.

---

## Parameters (the invoking routine supplies these)

| Name | Required | Description | Example |
|---|---|---|---|
| `REPO` | yes | Absolute path to the target repo whose working tree must be synced. | `C:/Users/brown/Git/career-playbook` |
| `VERIFY_FILE` | no | Repo-relative path that must exist after sync. Used as a sanity check that the file the routine intends to read is present on `origin/main`. | `.claude/skills/batch-cover-letters/SKILL.md` |
| `PREFIX` | yes | Short string used to namespace push notifications so the user can tell which routine emitted the abort signal. | `nightly-cover-letters` |

If `VERIFY_FILE` is omitted, skip the post-sync existence check.

---

## Behavior

1. **Verify `$REPO` exists as a directory.** If not, push-notify `${PREFIX}: repo not found at ${REPO} — skipping run` and return **ABORT**.

2. **Fetch `origin/main`:**
   ```bash
   git -C "$REPO" fetch origin main
   ```
   On failure (network error, auth issue), push-notify `${PREFIX}: git fetch failed — check ${REPO}` and return **ABORT**.

3. **Determine the current branch:**
   ```bash
   BRANCH=$(git -C "$REPO" branch --show-current)
   ```

4. **Choose a sync strategy based on `BRANCH`:**

   - **Claude-managed worktree branch (`claude/*`):** these branches exist only to host an autonomous run; `--hard` reset is authorized.
     ```bash
     git -C "$REPO" reset --hard origin/main
     ```

   - **`main`:** fast-forward only, never merge.
     ```bash
     git -C "$REPO" pull --ff-only origin main
     ```

   - **Anything else** (a feature branch, a draft branch, etc.): rebase onto `origin/main`. If conflicts arise, abort the rebase and exit cleanly.
     ```bash
     git -C "$REPO" rebase origin/main
     ```
     If `git rebase` exits non-zero:
     ```bash
     git -C "$REPO" rebase --abort
     ```
     Then push-notify `${PREFIX}: sync conflict on ${BRANCH} — manual intervention required` and return **ABORT**.

5. **Verify the file the routine plans to read** (only if `VERIFY_FILE` is set):
   ```bash
   if [ ! -f "$REPO/$VERIFY_FILE" ]; then
     # push-notify and abort
   fi
   ```
   On missing file, push-notify `${PREFIX}: ${VERIFY_FILE} missing on origin/main after sync` and return **ABORT**.

6. **Return SUCCESS.** The caller proceeds with its own logic.

---

## Return semantics

The routine treats this skill as a guard. Two outcomes:

- **SUCCESS** — `$REPO` is now current with `origin/main`, `$VERIFY_FILE` (if specified) exists, and the routine continues.
- **ABORT** — the push notification has already been sent. The caller exits cleanly without modifying any inbox, opening any PR, or making any commits. Do not re-notify; do not retry; do not fall back to running against stale state.

---

## Why this exists

Scheduled-task routines that read repo-resident files at runtime can land in a Claude-managed worktree whose branch was cut from an older `main`. Without an explicit sync step:

- The routine reads stale skill or context files and runs against outdated logic, OR
- The file the routine wants to read does not exist yet on the worktree's branch and the routine aborts with a "file not found" report — wasting the run.

This skill centralizes the sync discipline so each routine declares only what it needs (`REPO`, `VERIFY_FILE`, `PREFIX`) without re-implementing branch detection, conflict handling, or the abort-with-notification protocol.

See `docs/adr/013-sync-routine-worktree-skill.md` for the full rationale.

---

## Scope boundary

This skill syncs a repo's working tree to `origin/main`. It does **not** handle:

- Draft-branch workflows (e.g., journal-compose's `draft/YYYY-MM-DD` branch). Those are routine-specific and stay in the routine itself; the routine can call this skill first to ensure `origin/main` is current, then perform its own draft-branch logic.
- Multi-repo coordination. If a routine reads from two repos, it invokes this skill twice with different `REPO` values.
- Pushing or merging anything. This is a read-only sync of the working tree.
