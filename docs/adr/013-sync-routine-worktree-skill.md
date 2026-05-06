# ADR 013 — Sync-to-Main as a Reusable Routine Skill

**Date:** 2026-05-06
**Status:** Accepted
**Tags:** routines, skills, git, worktree, scheduled-tasks, sync

---

## Context

Scheduled-task routines (`claude/routines/`) are launched by the Claude Code scheduler into a Claude-managed worktree under `.claude/worktrees/<name>/`. The worktree's branch is cut from whatever `main` was at the moment the worktree was created — which may predate skill changes, context file edits, or queue updates that have since merged to `main`.

This bit on 2026-05-06: the scheduled `batch-cover-letter-test` task (an out-of-tree test routine) fired into a worktree whose branch did not yet contain the `batch-cover-letters` skill in career-playbook (still on a feature branch when the worktree was created). The routine had to abort with a "skill not found" report instead of running the pipeline against the seven-JD inbox. The skill and its filename-handling fix had already merged to career-playbook `main` (career-playbook#102, career-playbook#109) — the routine simply could not see them from inside the stale worktree. Issue: dev-env#188.

A survey of existing tracked routines confirmed the pattern is general:

| Routine | Reads what at runtime | Sync state before this ADR |
|---|---|---|
| `nightly-cover-letters` | `${career-playbook}/.claude/skills/batch-cover-letters/SKILL.md` | Inline `git checkout main && git pull origin main` — assumes the routine is operating on the main repo, not a worktree |
| `daily-journal-compose` | Stub files in `${engineering-journal}/sessions/<project>/` | None |
| `nightly-research` | `${research-notes}/research-queue.md` | None |
| `prune-stale-worktrees` | (operates on git state directly) | Not applicable |

`nightly-cover-letters`'s inline sync silently fails when the routine fires from a worktree (its `git checkout main` either hits the "main is checked out elsewhere" error or noisily switches branches in a worktree it doesn't own). `daily-journal-compose` and `nightly-research` have no sync at all and would inherit the same staleness problem if their worktrees fall behind.

## Decision

Extract the sync logic into a parameterized dev-env skill at `claude/skills/sync-routine-worktree/SKILL.md`. Routines that read repo-resident files at runtime invoke this skill as Step 0, passing `REPO`, `VERIFY_FILE`, and `PREFIX`, and treat the result as a guard — proceed on SUCCESS, exit cleanly on ABORT.

The skill encapsulates:

1. Repo existence verification.
2. `git fetch origin main`.
3. Branch-class-aware sync:
   - **Claude-managed worktree branch (`claude/*`)** → `git reset --hard origin/main` (the branch exists only to host the run; reset is authorized).
   - **`main`** → `git pull --ff-only origin main`.
   - **Other branch with commits** → `git rebase origin/main`; on conflict, `git rebase --abort` and notify.
4. Optional post-sync existence check for `VERIFY_FILE`.
5. Push notification on any abort path, with consistent message format prefixed by the calling routine's name.

Routine authors document the requirement in `claude/CLAUDE.md` so future routines inherit the discipline.

## Consequences

**Positive:**

- One implementation of branch-class detection and conflict handling. A bug fix in the sync skill propagates to all consumers; routine authors stop re-deriving the heuristic.
- Routines that previously had no sync (`daily-journal-compose`, `nightly-research`) can adopt the pattern incrementally — each conversion is a one-line skill invocation plus parameter values.
- The skill's scope boundary is narrow ("sync a working tree to `origin/main`"), so it composes cleanly with routine-specific concerns. Journal-compose's `draft/YYYY-MM-DD` branch logic, for example, stays in the journal-compose skill — the sync skill ensures `origin/main` is current first, then journal-compose checks out or creates the draft branch on top of fresh main.
- Push notification message format is centralized and consistent across all routines.

**Negative:**

- The sync logic is now a level of indirection away from the routine. A reader has to follow the skill reference to see the actual git commands.
- Routines now have a build-time dependency on the dev-env skill (via the `~/.claude/skills/` junction). If the skill is deleted or renamed, every consuming routine breaks. Mitigation: the skill is in version control under dev-env and referenced by name in `claude/CLAUDE.md`; renames must update both.
- Future routines authors need to know the skill exists. Mitigation: the routine-authoring pointer in `claude/CLAUDE.md` makes the sync-to-main preamble part of the documented routine pattern.

## Alternatives Considered

### Inline duplication in each routine

Cost: 15–20 lines of sync prose per routine, three known consumers, drift risk.
Benefit: each routine is self-contained.
Rejected: with three known consumers and more likely (Mike's scheduled-task usage is growing), the drift risk and the cost of fixing a bug in three places outweighs the readability cost of the indirection.

### A shell script in `claude/scripts/sync-worktree-to-main.sh`

Cost: branch detection in shell is messier; the abort-with-push-notification path can't be done from a shell script (push notification is a Claude tool call, not a shell capability), so the routine would still need inline notification logic on the abort path.
Benefit: self-contained executable.
Rejected: the partial-extraction problem leaves duplication on the most error-prone path (conflict handling and notification).

### Embed sync logic in every consuming skill (`batch-cover-letters`, `journal-compose`, etc.) instead of in the routine

Cost: skills get invoked from interactive user sessions too, where syncing to `origin/main` is wrong (the user may be on a feature branch on purpose). Routine-context vs. user-context detection adds complexity to every skill.
Benefit: no separate sync skill needed.
Rejected: the sync need is specific to autonomous, runs-in-a-worktree-it-didn't-create routine contexts. Putting it in skills puts it in the wrong layer.

## Scope Boundary

This skill syncs a single repo's working tree to `origin/main`. It does not handle:

- **Draft-branch workflows** (e.g., journal-compose's `draft/YYYY-MM-DD`). Those stay in the consuming skill; the consuming skill calls this sync skill first to ensure `origin/main` is current, then performs its own draft-branch logic on top.
- **Multi-repo coordination.** A routine that reads from two repos invokes the sync skill twice with different `REPO` values.
- **Pushing or merging.** This is read-side: it brings the working tree current with what's already on the remote.

## Migration

- `nightly-cover-letters` is migrated in the same PR that introduces this ADR (PR closing dev-env#188).
- `daily-journal-compose` and `nightly-research` are not migrated in this PR; their conversions are tracked separately and can land any time before their next reported staleness incident.
- The non-version-controlled `batch-cover-letter-test` test routine continues to use its inline sync until either it is moved into version control under `claude/routines/` or it is converted to invoke the new skill. Either way it is outside the scope of this ADR.

## References

- dev-env#188 — the issue this ADR closes.
- career-playbook#102, career-playbook#109 — the merged-to-main work that surfaced the staleness incident.
- `claude/skills/sync-routine-worktree/SKILL.md` — the skill itself.
- `claude/routines/nightly-cover-letters/SKILL.md` — the canonical first consumer.
- ADR 008 — Plan-Then-Optimize as an Embedded Skill Step (similar pattern of pulling cross-cutting discipline into a skill that other workflows invoke).
