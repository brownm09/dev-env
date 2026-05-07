# ADR-014: Auto-Move GitHub Project Item to Done on PR Merge

**Date:** 2026-05-07  
**Status:** Accepted  
**Tags:** hooks, github-project, post-tool-use, hook-config, automation

---

## Context

After every `gh pr merge`, the linked issue's GitHub Project board item must be manually
moved from In Progress to Done. This was discovered while closing out PR #190
(lifting-logbook) — issues #178, #179, #180 were all ROADMAP Shipped but still showed
In Progress on the board because no automation updated project status on merge.

Two automation options exist for this:
1. **GitHub Actions** — a workflow on `pull_request` (closed + merged) calls the GraphQL
   API. Reliable but requires a workflow file in every participating repo and fires on
   any merge (not just Claude sessions).
2. **PostToolUse hook** — extends the existing hook infrastructure that already fires on
   `gh pr merge` (see `pr-merge-reminder.py`). Keeps automation in the single config
   layer (`dev-env`), no per-repo workflow files needed.

The existing `post-tool-use.py` hook already demonstrates the pattern: opt-in via
`.claude/hook-config.json`, run a `gh` subprocess, emit a structured message via exit 2.
This pattern is well-understood and has caused no incidents across
`post-tool-use.py`, `post-pr-merge-pull.py`, and `pr-merge-reminder.py`.

## Decision

Add a new `post-pr-merge-project.py` PostToolUse hook that:
- Fires when a `gh pr merge` command exits 0
- Reads `.claude/hook-config.json` for the project configuration
- Parses `Closes/Fixes/Resolves #N` from the PR body to find the linked issue
- Calls `gh project item-edit` to move that issue's item to Done

Two new `hook-config.json` fields are introduced: `status_field_id` and `done_option_id`.
Projects opt in by adding these fields alongside the existing project fields. Projects
without these fields are silently skipped.

The GitHub Actions option was **rejected** for this use case because:
- It would require adding a workflow file to every participating repo
- Claude sessions trigger merges via `gh pr merge` in the terminal, making the hook the
  natural interception point
- The hook approach is consistent with the existing `post-tool-use.py` precedent

## Consequences

- **Eliminates** the manual `gh project item-edit` step after every merge
- **Adds** two new fields to `hook-config.json` schema: `status_field_id`, `done_option_id`
- Projects **opt in** by updating their `hook-config.json`; repos without config are unaffected
- If `Closes #N` is absent from the PR body, the hook exits 0 silently — the manual step
  is still needed for those PRs (but existing workflow rules already require `Closes #N`)
- If the `gh project item-edit` call fails (e.g., item already Done, network error),
  the hook exits 2 with a fallback command so the operator can retry manually
