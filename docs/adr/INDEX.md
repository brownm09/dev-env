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
| [007](007-hook-command-invocation.md) | Hook Command Invocation: `py -3` Launcher on Windows | 2026-04-27 (amended 2026-05-26) | Accepted | hooks, python, bash, invocation, scripts |
| [008](008-plan-then-optimize-forcing-function.md) | Plan-Then-Optimize as an Embedded Skill Step | 2026-04-27 | Accepted | planning, token-efficiency, skills, workflow |
| [009](009-cover-letter-token-efficiency.md) | Cover Letter Token Efficiency: Inline Drafting and Session Reuse | 2026-05-01 | Accepted | cover-letter, token-efficiency, skills, drafting |
| [010](010-skill-tmpfile-allow-rule.md) | Skill Temp File Writes: `Bash(TMPFILE=*)` Allow Rule | 2026-05-02 | Accepted | skills, permissions, tmpfile, bash, settings |
| [011](011-adr-warrant-check.md) | ADR-Warrant Check at Plan, PR-Open, and PR-Merge Checkpoints | 2026-05-03 | Accepted | adr, workflow, git, checkpoints, documentation |
| [012](012-post-merge-checklist-board-done-roadmap-update.md) | Post-Merge Checklist: Board Done + Roadmap Update Rules | 2026-05-06 | Accepted | git, workflow, project-board, roadmap, post-merge |
| [013](013-sync-routine-worktree-skill.md) | Sync-to-Main as a Reusable Routine Skill | 2026-05-06 | Accepted | routines, skills, git, worktree, scheduled-tasks, sync |
| [014](014-auto-move-project-item-done-on-merge.md) | Auto-Move GitHub Project Item to Done on PR Merge | 2026-05-07 | Accepted | hooks, github-project, post-tool-use, hook-config, automation |
| [015](015-suppress-hook-noise-in-claude-worktrees.md) | Suppress Hook Noise in Claude-Managed Worktree Sessions | 2026-05-07 | Accepted | hooks, worktree, UserPromptSubmit, token-efficiency, context-pollution |
| [016](016-worktree-npm-auto-install.md) | Auto-Install npm Packages in Claude-Managed Worktrees | 2026-05-09 | Accepted | hooks, worktree, UserPromptSubmit, npm, node_modules, automation |
| [017](017-journal-compose-today-guard.md) | Journal-Compose Today-Date Guard (All Paths; `--force` Opt-Out) | 2026-05-10 | Accepted | journal, composition, skill, today-guard, branch-detection, pre-push |
| [018](018-reconcile-open-prs-hook.md) | Auto-Reconcile open-prs.jsonl Against GitHub State | 2026-05-10 | Accepted | hooks, open-prs, UserPromptSubmit, github, token-efficiency |
| [019](019-doc-reconciliation-enforcement.md) | Documentation Reconciliation Enforcement | 2026-05-10 | Accepted | documentation, skills, hooks, workflow, review, checkpoints |
| [020](020-doc-coverage-in-review.md) | Documentation Coverage Check as LLM-Judged Step in /review | 2026-05-11 | Accepted | documentation, review, skill, semantic, readme, llm-judgment |
| [021](021-auto-stub-on-pr-push.md) | Auto-Write Journal Stub on git push to Open PR Branch | 2026-05-11 | Accepted | journal, stubs, hooks, post-tool-use, git-push, automation |
| [022](022-test-coverage-gate-before-pr.md) | Test Coverage Gate: Require Declaration of New Testable Behavior Before PR | 2026-05-16 | Accepted | testing, coverage, workflow, git, pr, blocking-rule |
| [023](023-generic-required-fields-issue-hook.md) | Generic `required_fields` Config for Issue/PR Project-Board Hook | 2026-05-16 | Accepted | hooks, github-project, post-tool-use, hook-config, automation, workflow |
| [024](024-worktree-path-guard-hook.md) | PreToolUse Hook to Block Canonical-Root Writes from Worktrees | 2026-05-23 | Accepted | hooks, worktrees, pre-tool-use, file-safety, write, edit |
| [025](025-default-plan-mode.md) | Default Plan Mode | 2026-05-23 | Accepted | config, settings, plan-mode, workflow, defaults |
| [026](026-suppression-policy.md) | Suppression Policy: No Silent Workarounds for Type and Lint Errors | 2026-05-24 | Accepted | code-quality, typescript, eslint, workflow, suppression, pre-pr |
| [027](027-userpromptsubmit-blocking-hook-conventions.md) | UserPromptSubmit Hook Output: stderr for Blocking, Per-Session Marker Files | 2026-05-27 | Accepted | hooks, UserPromptSubmit, stderr, per-session-state, claude-code-contract |
| [028](028-all-findings-merge-gate.md) | All-Findings Merge Gate: Address Blocking and Non-Blocking Before Merge | 2026-05-27 | Accepted | review, workflow, git, pr, blocking-rule, non-blocking |
| [029](029-test-integrity-policy.md) | Test Integrity Policy: No Silent Degradation of Existing Tests | 2026-05-27 | Accepted | testing, quality, review, suppression-parallel, workflow, pre-pr |
| [030](030-baseline-test-failure-policy.md) | Pre-existing Test Failure Policy: Baseline + Fix-on-Touch | 2026-05-27 | Accepted | testing, quality, pre-pr, baseline, fix-on-touch, workflow |
| [031](031-auto-merge-disabled.md) | Auto-Merge Disabled Across All Repos | 2026-05-28 | Accepted | git, pr, merge, workflow, hooks, post-merge |
| [032](032-journal-start-here-dashboard.md) | Top-of-README "Start here" Dashboard in journal-compose | 2026-05-28 | Accepted | journal, composition, skill, readme, manifest, dashboard |
| [033](033-prevent-system-sleep-while-processing.md) | Prevent System Sleep While Claude Is Processing | 2026-05-29 | Accepted | hooks, windows, sleep, UserPromptSubmit, Stop, Notification, background-process |
| [034](034-error-message-diligence.md) | Error Message Diligence | 2026-06-01 | Accepted | workflow, diagnosis, ci, error-handling, claude-behavior, global-rule |
| [035](035-git-push-delete-web-session-constraint.md) | `git push --delete` Fails in Claude Code Web Sessions | 2026-06-03 | Accepted | git, web-session, sandbox, proxy, shallow-clone, branch-deletion, workflow, global-rule |
| [036](036-lockfile-drift-prevention.md) | Lockfile-Drift Prevention: Global Pre-Push Hook + Dependency-Edit Rule | 2026-06-03 | Accepted | hooks, pre-push, npm, lockfile, package-lock, ci, global-rule, code-quality |
| [037](037-worktree-disk-reclamation.md) | Automated Disk Reclamation for Idle Worktree node_modules | 2026-06-03 | Accepted | disk, worktrees, node_modules, hooks, routines, UserPromptSubmit, saturation, automation |
| [038](038-durable-preferences-documented-in-repo.md) | Durable Preferences Must Be Documented in the Repo, Not Only in Memory | 2026-06-04 | Accepted | workflow, memory, claude-behavior, documentation, global-rule, code-quality |
