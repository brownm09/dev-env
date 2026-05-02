# Architectural Decision Records

Design decisions behind the rules in `claude/CLAUDE.md`, hooks, skills, and configuration.
Consult the relevant ADR before overriding any rule, hook, skill, or config.

| # | Title | Date | Status |
|---|-------|------|--------|
| [001](001-per-session-stub-files.md) | Per-Session Stub Files for Journal Composition | 2026-03-27 | Accepted |
| [002](002-journal-compose-session-isolation.md) | Journal-Compose Session Isolation | 2026-04-04 | Accepted |
| [003](003-config-in-version-control.md) | Config Artifacts in Version Control via Symlinks | 2026-04-13 | Accepted |
| [004](004-pr-review-reads-from-remote.md) | PR Review Reads from Remote, Not Local Worktree | 2026-04-17 | Accepted |
| [005](005-global-core-hooks-path.md) | Global `core.hooksPath` for Cross-Repo Invariants | 2026-04-19 | Accepted |
| [006](006-dev-env-sync-on-every-prompt.md) | UserPromptSubmit Dev-Env Sync on Every Prompt | 2026-04-19 | Accepted |
| [007](007-hook-command-invocation.md) | Hook Command Invocation: Direct `python3` vs `bash -c` Wrapper | 2026-04-27 | Accepted |
| [008](008-plan-then-optimize-forcing-function.md) | Plan-Then-Optimize as an Embedded Skill Step | 2026-04-27 | Accepted |
| [009](009-cover-letter-token-efficiency.md) | Cover Letter Token Efficiency: Inline Drafting and Session Reuse | 2026-05-01 | Accepted |
| [010](010-skill-file-writes-use-write-tool.md) | Skill File Writes: Use the Write Tool, Not Bash | 2026-05-02 | Accepted |
