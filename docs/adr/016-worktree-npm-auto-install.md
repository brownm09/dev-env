# ADR-016: Auto-Install npm Packages in Claude-Managed Worktrees

**Date:** 2026-05-09
**Status:** Accepted

---

## Context

Claude Code worktrees are created at `<repo>/.claude/worktrees/<name>/`. Each worktree is an
independent working directory — `node_modules` is never inherited from the main repo checkout.
Because worktrees are created without running any package-install step, the first time Claude
runs tests or imports a package in a fresh worktree, the session encounters pre-existing
failures like:

> pre-existing failures due to missing npm packages [...] in this worktree — unrelated to my changes

This is noise that masks real test failures, wastes time diagnosing the environment before
work begins, and requires a manual `npm install` step that Claude must either remember to run
or be told to run.

Claude Code does not expose a `WorktreeCreate` or `EnterWorktree` hook event. Sessions also
often start *already inside* a worktree (the harness places Claude there directly at session
launch), so a `PostToolUse` hook on the `EnterWorktree` tool call would miss those cases.
`UserPromptSubmit` fires on the first user message regardless of how the session started —
the correct event for a one-time setup action that must complete before Claude begins any work.

## Decision

Add a `UserPromptSubmit` hook (`worktree-npm-install.py`) that:

1. Detects a Claude-managed worktree by checking for `.claude` and `worktrees` as consecutive
   path components in the cwd (`.claude/worktrees` is a stable convention used by the harness
   and established in ADR-015).
2. Confirms `package.json` exists in the cwd (repo is an npm project).
3. Exits silently if `node_modules` already exists — the directory itself is the sentinel.
4. Runs `npm ci` when `package-lock.json` exists (reproducible, faster) or `npm install`
   otherwise.
5. Emits a `systemMessage` confirming success or explaining failure.

The hook is **global** (registered in `claude/settings.json`) so it applies to every npm-based
repo without per-project configuration. It is a no-op in non-npm repos, non-worktree sessions,
and worktrees where packages are already installed.

## Consequences

- The first prompt in any fresh Claude-managed worktree of an npm repo triggers a package
  install automatically. Claude can proceed directly to working without a setup detour.
- On failure (npm errors, timeout), the hook emits a warning systemMessage and exits 0 —
  it never blocks Claude from responding.
- npm install/ci for large monorepos can take 30–120 seconds, adding latency to the first
  prompt in a fresh worktree. This is a one-time cost per worktree and is preferable to
  silent test failures.
- Non-npm repos and already-installed worktrees pay only the cost of three `Path.exists()`
  checks per prompt until `node_modules` appears — negligible.
- The `node_modules` check as sentinel means re-running `npm install` (e.g., after adding a
  package) still requires a manual step or deletion of `node_modules` first. This is
  acceptable: the hook targets *missing* installs, not *stale* ones.

## References

- [ADR-015](015-suppress-hook-noise-in-claude-worktrees.md) — established the worktree detection
  pattern (`.claude/worktrees` in path) used here
- [ADR-006](006-dev-env-sync-on-every-prompt.md) — rationale for `UserPromptSubmit` as the
  hook event for session-start setup actions
- [npm ci documentation](https://docs.npmjs.com/cli/v10/commands/npm-ci) — why `npm ci` is
  preferred over `npm install` in automated contexts
