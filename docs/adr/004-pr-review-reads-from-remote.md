# ADR 004 — PR Review Reads from Remote, Not Local Worktree

**Date:** 2026-04-17  
**Status:** Accepted

---

## Context

**Incident (lifting-logbook#86, commit `50f7ac6`):** A follow-up review session reported three findings as still open after a PR author had already committed fixes. The fixes existed on the remote PR branch, but the review step read files from the local working tree, which was checked out to a different branch. The report was wrong — all blockers had been addressed — but the incorrect report delayed the merge.

Root cause: in a multi-worktree environment, the local working tree's branch at review time is not guaranteed to be the PR branch. Even without worktrees, a developer may have checked out another branch between the time the PR was pushed and the time the review runs.

---

## Decision

When checking whether a PR has addressed review findings, or when reading PR branch file state for any reason:

1. Run `git fetch origin <headRefName>` first.
2. Read files via `git show origin/<branch>:<path>` — never from the local working tree.

This rule applies to: review skill follow-up checks, merge-readiness assessments, and any step that compares "what the PR contains" against a checklist.

---

## Consequences

- Every PR review follow-up begins with a `git fetch` — adds a small network round-trip but eliminates false "still open" results.
- Local working tree state is irrelevant to PR review; the remote branch is authoritative.
- The rule is codified in `claude/CLAUDE.md` § Git Workflow to ensure it is applied outside the review skill as well.
- Multi-worktree setups are the highest-risk environment for this failure mode; the `multi-worktree-alert.py` hook (ADR 006 context) surfaces active worktrees on every prompt as a related safeguard.

---

## References

- Engineering journal: `sessions/dev-env/2026-04-19-hook-infrastructure.md` (incident analysis)
- `dev-env` commit `28f728e` — `fix: read PR branch state from remote, not local worktree`
- `claude/CLAUDE.md` § Git Workflow — "PR branch state must come from the remote"
- `claude/skills/review/SKILL.md` — review follow-up procedure
