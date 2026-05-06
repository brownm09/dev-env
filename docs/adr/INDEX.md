# Architectural Decision Records

Design decisions behind the rules in `claude/CLAUDE.md`, hooks, skills, and configuration.
Consult the relevant ADR before overriding any rule, hook, skill, or config.

| # | Title | Date | Status | Tags |
|---|-------|------|--------|------|
| [001](001-per-session-stub-files.md) | Per-Session Stub Files for Journal Composition | 2026-03-27 | Accepted | journal, stubs, composition, concurrency |
| [002](002-journal-compose-session-isolation.md) | Journal-Compose Session Isolation | 2026-04-04 | Accepted | journal, composition, session-isolation, skill |
| [003](003-config-in-version-control.md) | Config Artifacts in Version Control via Symlinks | 2026-04-13 | Accepted | config, symlinks, version-control, dev-env, settings |
| [004](004-pr-review-reads-from-remote.md) | PR Review Reads from Remote, Not Local Worktree | 2026-04-17 | Accepted | git, pr-review, worktree, remote, correctness |
| [005](005-global-core-hooks-path.md) | Global `core.hooksPath` for Cross-Repo Invariants | 2026-04-19 | Accepted | git, hooks, core.hooksPath, cross-repo |
| [006](006-dev-env-sync-on-every-prompt.md) | UserPromptSubmit Dev-Env Sync on Every Prompt | 2026-04-19 | Accepted | hooks, dev-env-sync, UserPromptSubmit, prompts |
| [007](007-hook-command-invocation.md) | Hook Command Invocation: Direct `python3` vs `bash -c` Wrapper | 2026-04-27 | Accepted | hooks, python, bash, invocation, scripts |
| [008](008-plan-then-optimize-forcing-function.md) | Plan-Then-Optimize as an Embedded Skill Step | 2026-04-27 | Accepted | planning, token-efficiency, skills, workflow |
| [009](009-cover-letter-token-efficiency.md) | Cover Letter Token Efficiency: Inline Drafting and Session Reuse | 2026-05-01 | Accepted | cover-letter, token-efficiency, skills, drafting |
| [010](010-skill-tmpfile-allow-rule.md) | Skill Temp File Writes: `Bash(TMPFILE=*)` Allow Rule | 2026-05-02 | Accepted | skills, permissions, tmpfile, bash, settings |
| [011](011-adr-warrant-check.md) | ADR-Warrant Check at Plan, PR-Open, and PR-Merge Checkpoints | 2026-05-03 | Accepted | adr, workflow, git, checkpoints, documentation |
| [012](012-post-merge-checklist-board-done-roadmap-update.md) | Post-Merge Checklist: Board Done + Roadmap Update Rules | 2026-05-06 | Accepted | git, workflow, project-board, roadmap, post-merge |
| [013](013-sync-routine-worktree-skill.md) | Sync-to-Main as a Reusable Routine Skill | 2026-05-06 | Accepted | routines, skills, git, worktree, scheduled-tasks, sync |
