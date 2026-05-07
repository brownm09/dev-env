# ADR-015: Suppress Hook Noise in Claude-Managed Worktree Sessions

**Date:** 2026-05-07
**Status:** Accepted

---

## Context

Two `UserPromptSubmit` hooks fire in every Claude Code session, regardless of context:

- `new-day-journal-check.py` — makes a `git ls-remote` call to the engineering-journal repo
  and emits warnings about unmerged draft branches and resurrected stubs.
- `multi-worktree-alert.py` — emits a full sibling worktree list as a `systemMessage`.

When multiple sessions run in parallel (each in a Claude-managed worktree under
`.claude/worktrees/<name>`), both hooks inject state into every session's context window.
The warnings are only actionable in main-checkout sessions of dev-env or engineering-journal;
in feature-work worktree sessions they are noise that inflates token cost on every prompt.

## Decision

Add a guard to both hooks: if the session's `cwd` contains `.claude` and `worktrees` as
consecutive path components, exit silently (`sys.exit(0)`).

Claude-managed worktrees are always created at `<repo>/.claude/worktrees/<name>`, making
`.claude/worktrees/` a reliable, stable identifier. This path structure is established by
the Claude Code harness and is not used for any other purpose.

The guard fires before any git or network I/O, so suppressed sessions pay no subprocess cost.

## Consequences

- Journal warnings (unmerged drafts, stale artifacts, resurrected stubs) are suppressed in
  all Claude-managed worktree sessions and continue to fire in main-checkout sessions.
- The worktree sibling list is suppressed in Claude-managed worktree sessions; the system
  prompt already identifies the current worktree, so the list adds no orienting value there.
- Any future hook that should also be suppressed in worktree sessions can reuse the same
  guard pattern:
  ```python
  _parts = Path(cwd).parts
  if ".claude" in _parts and "worktrees" in _parts:
      sys.exit(0)
  ```
- Main-checkout sessions are unaffected; both hooks continue to fire normally there.
