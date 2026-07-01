# Architectural Decision Records

Design decisions behind the rules in `claude/CLAUDE.md`, hooks, skills, and configuration.
Consult the relevant ADR before overriding any rule, hook, skill, or config.

| # | Title | Date | Status | Tags |
|---|-------|------|--------|------|
| [001](001-per-session-stub-files.md) | Per-Session Stub Files for Journal Composition | 2026-03-27 | Accepted | journal, stubs, composition, concurrency |
| [002](002-journal-compose-session-isolation.md) | Journal-Compose Session Isolation | 2026-04-04 | Accepted | journal, composition, session-isolation, skill |
| [003](003-config-in-version-control.md) | Config Artifacts in Version Control via Symlinks | 2026-04-13 (amended 2026-07-01) | Accepted | config, symlinks, version-control, dev-env, settings, scheduled-tasks, routines |
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
| [024](024-worktree-path-guard-hook.md) | PreToolUse Hook to Block Canonical-Root Writes from Worktrees | 2026-05-23 (amended 2026-06-06) | Accepted | hooks, worktrees, pre-tool-use, file-safety, write, edit, orphaned-worktree |
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
| [036](036-lockfile-drift-prevention.md) | Lockfile-Drift Prevention: Global Pre-Push Hook + Dependency-Edit Rule | 2026-06-03 | Accepted | hooks, pre-push, npm, lockfile, package-lock, ci, global-rule, code-quality, enforcement-context |
| [037](037-worktree-disk-reclamation.md) | Automated Disk Reclamation for Idle Worktree node_modules | 2026-06-03 | Accepted | disk, worktrees, node_modules, hooks, routines, UserPromptSubmit, saturation, automation |
| [038](038-durable-preferences-documented-in-repo.md) | Durable Preferences Must Be Documented in the Repo, Not Only in Memory | 2026-06-04 | Accepted | workflow, memory, claude-behavior, documentation, global-rule, code-quality |
| [039](039-merge-gate-findings-enforcement.md) | Mechanical Enforcement of the All-Findings Merge Gate | 2026-06-04 | Accepted | review, workflow, hooks, pre-tool-use, pr, merge, blocking-rule, enforcement |
| [040](040-global-claudemd-layering-and-slimming.md) | Global CLAUDE.md Layering: Project-Specific Content Belongs in Project CLAUDE.md | 2026-06-05 | Accepted | claude-md, layering, dev-env, documentation, testing, global-rule, token-efficiency |
| [041](041-no-terminal-spawn-in-windows-scripts.md) | No Spawning New Terminal Windows in Windows Scripts | 2026-06-06 | Accepted | code-quality, global-rule, windows, powershell, claude-behavior, review |
| [042](042-plan-risk-dimension-audit-and-observability-section.md) | Plan Risk-Dimension Audit Checklist + Per-Project Observability Section | 2026-06-06 | Accepted | planning, audit, observability, security, global-rule, workflow, claude-md |
| [043](043-keep-warm-scheduled-task-for-token-freshness.md) | Keep-Warm Scheduled Task for OAuth Token Freshness | 2026-06-15 | Accepted | hooks, windows, scheduled-task, oauth, token-refresh, usage-snapshot, automation |
| [044](044-eliminate-usage-snapshot-gap-on-demand-refresh.md) | Eliminate the Usage-Snapshot Gap via On-Demand CLI Refresh | 2026-06-16 | Accepted | hooks, oauth, token-refresh, usage-snapshot, post-tool-use, lazy-refresh |
| [045](045-pre-install-freespace-gate.md) | Pre-Install Free-Space Gate + Prompt Post-Merge Reclamation | 2026-06-18 | Accepted | disk, worktrees, node_modules, hooks, ENOSPC, npm-install, post-merge, runbook |
| [046](046-post-merge-followup-tiles.md) | Post-Merge Follow-Up Tiles | 2026-06-20 | Accepted | git-workflow, post-merge, follow-ups, spawn-task, tiles |
| [047](047-standardize-gh-credential-helper.md) | Standardize git's GitHub Credential Helper on `gh` for Agent Sessions | 2026-06-20 | Accepted | git, credential-manager, worktree, agent-session, windows, gh-cli, workflow, global-rule |
| [048](048-memory-immortalization-issue-pairing.md) | Memory Writes Must Be Paired with an Immortalization Issue | 2026-06-20 | Accepted | workflow, memory, claude-behavior, documentation, global-rule, hooks, skill |
| [049](049-hook-payload-output-field.md) | PostToolUse Bash Hooks Read Output from `stdout`, Not `output` | 2026-06-21 | Accepted | hooks, post-tool-use, tool_response, payload, github-project, automation, reliability |
| [050](050-shared-hookio-sibling-hook-fixes.md) | Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes | 2026-06-21 (amended 2026-07-01) | Accepted | hooks, post-tool-use, tool_response, payload, github-project, automation, reliability, dry, usage-snapshot, pr-merge-reminder, gh-pr-view, api-fallback |
| [051](051-worktree-liveness-guard.md) | Worktree Liveness Guard — Skip Active-Session Worktrees in Prune/Reclaim | 2026-06-22 | Accepted | worktrees, liveness, prune, reclaim, transcript, session, data-loss, routines, safety, hooks |
| [052](052-worktree-config-canonical-fallback.md) | Worktree Config Resolution: Canonical-Checkout Fallback for the Project-Add Hook | 2026-06-22 | Accepted | hooks, post-tool-use, worktree, hook-config, github-project, gitignore, automation, reliability |
| [053](053-posttooluse-hooks-inert-in-background-sessions.md) | PostToolUse Hooks Are Inert in Background / SDK-Launched Sessions (Upstream Limitation) | 2026-06-22 | Accepted | hooks, post-tool-use, claude-code-harness, background-task, spawn-task, sdk, upstream-limitation, observability, reliability |
| [054](054-concurrency-safe-shared-journal-file-updates.md) | Concurrency-Safe Updates to Shared Journal Files (Manifest + open-prs.jsonl) | 2026-06-22 | Superseded by [056](056-per-session-sharding-journal-companion-files.md) | journal, stubs, manifest, open-prs, concurrency, data-loss, workflow, global-rule |
| [055](055-reliable-event-inert-posttooluse-advisory.md) | Reliable-Event Safety Net: A Stop-Hook Advisory for Inert PostToolUse Hooks | 2026-06-22 | Accepted | hooks, post-tool-use, stop-hook, transcript, observability, reliability, background-task, spawn-task, github-project |
| [056](056-per-session-sharding-journal-companion-files.md) | Per-Session Sharding of Journal Manifest + open-PR Tracking | 2026-06-22 (amended 2026-06-30, 2026-07-01) | Accepted | journal, stubs, manifest, open-prs, concurrency, data-loss, workflow, global-rule, sharding, git-index, pathspec |
| [057](057-shared-journal-shard-reader.md) | Shared `_journal_shards` Reader for open-PR Tracking | 2026-06-22 | Accepted | journal, open-prs, hooks, post-compact, reconcile, dry, maintainability, sharding |
| [058](058-worktree-squatting-main-detection-correction.md) | Detect & Auto-Correct a Worktree Squatting `main` (Canonical Off `main`) | 2026-06-22 (amended 2026-07-01) | Accepted | worktrees, main, squat, canonical, prune, dev-env-sync, post-merge, park, safety, hooks, symlinks |
| [059](059-multi-pr-issue-hierarchy.md) | Multi-PR Decomposition: Top-Level Issue + Sub-Issue Hierarchy with Tile Context | 2026-06-23 | Accepted | issues, decomposition, tiles, workflow, global-rule |
| [060](060-post-merge-tile-checkpoint-hook.md) | Post-Merge Tile Checkpoint Hook | 2026-06-28 | Accepted | hooks, post-tool-use, tiles, spawn-task, post-merge, enforcement, ADR-046 |
| [061](061-pre-merge-message-queue.md) | Pre-Merge User Message Queue | 2026-06-28 | Accepted | hooks, pre-tool-use, merge, workflow, bypass-mode, feedback, global-rule |
| [062](062-journal-report-analysis-trigger.md) | Report / Analysis Generation as a Journal Update Trigger | 2026-06-29 | Accepted | journal, stubs, reports, update-trigger, workflow, global-rule |
| [063](063-always-plan-rule-permission-mode-does-not-bypass-planning.md) | Always-Plan Rule: Permission Mode Does Not Bypass Planning | 2026-06-30 | Accepted | workflow, planning, permission-mode, bypass, auto, claude-behavior, global-rule |
| [064](064-shared-hookutil-sentinel-transcript-locate.md) | Shared `_hookutil` Module for Per-Session Sentinel and Transcript-Locate Helpers | 2026-06-30 | Accepted | hooks, stop-hook, UserPromptSubmit, sentinel, transcript, dry, maintainability, shared-module |
| [065](065-scope-push-reminder-to-target-repo.md) | Scope the git-push Journal Reminder to the Push-Target Repo | 2026-06-30 | Accepted | hooks, post-tool-use, git-push, journal, cross-repo, correction |
| [066](066-worktree-session-safety-rules.md) | Worktree Session Safety: Origin Verification, Bash cd Prevention, Deregistration Recovery | 2026-06-30 | Accepted | git, worktree, workflow, memory, documentation, global-rule |
| [067](067-scope-merge-keyed-hooks-to-target-repo.md) | Scope Merge-Keyed Hook Operations to the Merge-Target Repo | 2026-06-30 | Accepted | hooks, post-tool-use, gh-pr-merge, cross-repo, correction |
| [068](068-reconcile-project-board-orphan-issues.md) | Reconcile the Project Board Against Orphaned Issues (Backstop for the Inert Add-Hook) | 2026-06-30 | Accepted | routines, github-project, post-tool-use, hook-config, reconciliation, background-sessions, automation |
| [069](069-weekly-memory-audit-routine.md) | Weekly Memory Audit Routine | 2026-06-30 | Accepted | routines, memory, scheduled-tasks, autonomy, dedup, ADR-038, ADR-048, ADR-013 |
| [070](070-reconcile-project-board-scan-dir.md) | Generalize reconcile-project-board to Multi-Repo via --scan-dir | 2026-07-01 | Accepted | routines, github-project, hook-config, reconciliation, multi-repo, scan-dir, automation |
| [071](071-canonical-checkout-mutate-guard-hook.md) | PreToolUse Hook to Block Git-Mutating Bash Commands in a Canonical (Non-Worktree) Checkout | 2026-07-01 | Accepted | hooks, worktrees, pre-tool-use, bash, git, concurrency, canonical-checkout, rate-limit |
| [072](072-shared-repo-scan-module.md) | Shared `_repo_scan` Module for `find_git_repos()` Directory Discovery | 2026-07-01 | Accepted | worktrees, github-project, scan-dir, dry, maintainability, shared-module |
