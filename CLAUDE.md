# dev-env — Project Instructions

The global Claude Code configuration lives in [`claude/CLAUDE.md`](claude/CLAUDE.md),
symlinked to `~/.claude/CLAUDE.md`, and its rules apply here. Like that file, this one holds
rules: their stories live in [`docs/REFERENCE.md` → Project Instruction Detail](docs/REFERENCE.md#project-instruction-detail)
and [`docs/TESTING.md`](docs/TESTING.md), and `test_claude_md_size.py` caps its size
([ADR-159](docs/adr/159-instruction-files-hold-rules-not-stories.md)).

## Reference Documentation

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Quick-reference tables for skills, hooks, and routines |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Detailed descriptions, invocation syntax, config options, ADR links, and the reasoning behind each global rule (Instruction Detail) and each rule here (Project Instruction Detail) |
| [docs/TESTING.md](docs/TESTING.md) | Per-item behavioral detail for the `## Testing` index (what each test pins, scope gaps, incident history) |
| [docs/adr/](docs/adr/) | Design decisions behind rules in `claude/CLAUDE.md` |

## Testing

This is the index of dev-env verification commands. Items 1 to 118 keep their numbers because
other documents cite them, and the list takes no new ones. Each item names the change that
requires it, then its command: a bare filename is in `claude/scripts/`, and a path with a slash
is from the repo root. What
each test pins, its scope gaps and its history are in [docs/TESTING.md](docs/TESTING.md) under
the **same item number**: read it before changing a test or its script, and extend it in the same
PR.

**To add a test, edit only the test file.** Put a `Required when ...` line and a `Run: ...` line
naming the file in its first 10 lines, each starting its own line, and add nothing to this section,
`docs/TESTING.md` or `claude/scripts/tests/README.md`
([ADR-074 Amendment 1](docs/adr/074-pre-merge-numbering-collision-check.md)). A new check must be
such a file, not an inline command. Cite a newer test by its file name. Item 76 refuses a
numbered line past 118 and item 84 refuses a new README row, a test in `claude/scripts/tests/`
with neither a row nor an entry, or a script in `claude/scripts/` with neither a row nor a
`Purpose:` line; nothing gates `claude/hooks/tests/`. To find a script's tests:
`git grep -l <script-name> -- claude/scripts/tests claude/hooks/tests`. No hit doesn't mean no
test.

**Run the whole suite** with `py -3 claude/scripts/run-hook-tests.py`, exactly what
`.github/workflows/hook-tests.yml` runs on `windows-latest` for every `pull_request`
([ADR-103](docs/adr/103-shared-hookout-emitter.md)). When changing a script, also run its item's
command, or the `Run:` line of each test `git grep` finds, before `gh pr create`. A one-line map
of the older tests is [claude/scripts/tests/README.md](claude/scripts/tests/README.md).

1. **Hook-script syntax check** — all hook scripts parse; run from the repo root. Run: `py -3 -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) for f in sys.argv[1:]]" claude/scripts/*.py`
2. **`pyw -3` stdio verification** — Windows-only and needs a console, so CI skips it: run it locally. Run: `py -3 claude/scripts/tests/test_pyw_stdio.py`
3. **Pre-push hook self-test** — `claude/hooks/pre-push`. Run: `bash claude/hooks/tests/test-pre-push-lockfile.sh`
4. **Docs-only guard** — docs-only changes to `claude/CLAUDE.md`. Run: `grep -n 'date -u' claude/CLAUDE.md`; every match must be an internal operational artifact (lock files, log timestamps), never a stub filename or branch name.
5. **Script path-hygiene lint** — any `claude/scripts/*.sh` or `claude/hooks/*` shell script. Run: `bash claude/scripts/tests/check-script-path-hygiene.sh`
6. **`get-project-item.sh` smoke test** — `get-project-item.sh`. Run: `bash claude/scripts/tests/test-get-project-item.sh`
7. **shellcheck gate** — recommended for any shell script. Run: `bash claude/scripts/tests/run-shellcheck.sh`
8. **usage-snapshot classifier test** — `usage-snapshot.py`. Run: `py -3 claude/scripts/tests/test_usage_snapshot.py`
9. **worktree-npm-install gate test** — `worktree-npm-install.py`. Run: `py -3 claude/scripts/tests/test_worktree_npm_install.py`
10. **post-pr-merge-reclaim test** — `post-pr-merge-reclaim.py`. Run: `py -3 claude/scripts/tests/test_post_pr_merge_reclaim.py`
11. **memory-write-advisory test** — `memory-write-advisory.py`. Run: `py -3 claude/scripts/tests/test_memory_write_advisory.py`
12. **post-tool-use test** — `post-tool-use.py`. Run: `py -3 claude/scripts/tests/test_post_tool_use.py`
13. **_hookio shared-read test** — `_hookio.py`, `_hookinput.py`. Run: `py -3 claude/scripts/tests/test_hookio.py`
14. **post-pr-merge-project test** — `post-pr-merge-project.py`. Run: `py -3 claude/scripts/tests/test_post_pr_merge_project.py`
15. **post-pr-merge-pull test** — `post-pr-merge-pull.py`. Run: `py -3 claude/scripts/tests/test_post_pr_merge_pull.py`
16. **stub-push-archive-reminder test** — `stub-push-archive-reminder.py`. Run: `py -3 claude/scripts/tests/test_stub_push_archive_reminder.py`
17. **worktree-liveness guard test** — `_worktree_liveness.py`, or the prune/reclaim scripts' use of it. Run: `py -3 claude/scripts/tests/test_worktree_liveness.py`
18. **posttooluse-inert-advisory test** — `posttooluse-inert-advisory.py`. Run: `py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py`
19. **reconcile-open-prs test** — `reconcile-open-prs.py`. Run: `py -3 claude/scripts/tests/test_reconcile_open_prs.py`
20. **post-compact open-PR + pending-tile reader test** — `post-compact.py`. Run: `py -3 claude/scripts/tests/test_post_compact.py`
21. **journal-shards shared-reader test** — `_journal_shards.py`. Run: `py -3 claude/scripts/tests/test_journal_shards.py`
22. **worktree-topology test** — `_worktree_topology.py`, or the squat-detection paths in `prune-merged-worktrees.py`, `post-pr-merge-pull.py`, `dev-env-sync.py`, `journal-canonical-guard.py`. Run: `py -3 claude/scripts/tests/test_worktree_topology.py`
23. **post-merge-tile-checkpoint test** — `post-merge-tile-checkpoint.py`. Run: `py -3 claude/scripts/tests/test_post_merge_tile_checkpoint.py`
24. **pre-merge-message-check test** — `pre-merge-message-check.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_message_check.py`
25. **manifest field validator test** — `validate-manifest.py`. Run: `py -3 claude/scripts/tests/test_validate_manifest.py`
26. **prune-merged-worktrees test** — `prune-merged-worktrees.py`, `_default_branch.py`. Run: `py -3 claude/scripts/tests/test_prune_merged_worktrees.py`
27. **_hookutil shared-helper test** — `_hookutil.py`. Run: `py -3 claude/scripts/tests/test_hookutil.py`
28. **pr-merge-reminder test** — `pr-merge-reminder.py`. Run: `py -3 claude/scripts/tests/test_pr_merge_reminder.py`
29. **reconcile-project-board test** — `reconcile-project-board.py`. Run: `py -3 claude/scripts/tests/test_reconcile_project_board.py`
30. **reclaim-worktree-disk test** — `reclaim-worktree-disk.py`, `_default_branch.py`. Run: `py -3 claude/scripts/tests/test_reclaim_worktree_disk.py`
31. **_repo_scan shared-module test** — `_repo_scan.py`, or the `--scan-dir` discovery path in `prune-merged-worktrees.py`, `reclaim-worktree-disk.py`, `reconcile-project-board.py`. Run: `py -3 claude/scripts/tests/test_repo_scan.py`
32. **worktree-path-check test** — `pre-tool-use-worktree-path-check.py`. Run: `py -3 claude/scripts/tests/test_worktree_path_check.py`
33. **canonical-mutate-guard test** — `pre-tool-use-canonical-mutate-guard.py`. Run: `py -3 claude/scripts/tests/test_canonical_mutate_guard.py`
34. **merge-stale-pr self-test** — `merge-stale-pr.sh`. Run: `bash claude/scripts/tests/test-merge-stale-pr.sh`
35. **worktree-canon shared-module test** — `_worktree_canon.py`. Run: `py -3 claude/scripts/tests/test_worktree_canon.py`
36. **`_winsubp` shared-module test** — `_winsubp.py`. Run: `py -3 claude/scripts/tests/test_winsubp.py` + `py -3 claude/scripts/tests/test_pyw_stdio.py`
37. **numbering-collision check test** — `pre-merge-numbering-check.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_numbering_check.py`
38. **pre-merge-findings-gate test** — `pre-merge-findings-gate.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_findings_gate.py` + `bash claude/scripts/tests/test-merge-findings-gate.sh`
39. **Crude command-substring-check regression test** — adding or changing any `claude/scripts/*.py`. Run: `py -3 claude/scripts/tests/test_no_crude_command_substring_checks.py`
40. **journal-shard-write-advisory test** — `journal-shard-write-advisory.py`. Run: `py -3 claude/scripts/tests/test_journal_shard_write_advisory.py`
41. **`_journal_schema` shared-module test** — `_journal_schema.py`. Run: `py -3 claude/scripts/tests/test_journal_schema.py`
42. **`_bash_state` shared-module test** — `_bash_state.py`. Run: `py -3 claude/scripts/tests/test_bash_state.py`
43. **pre-commit-branch-check test** — `pre-commit-branch-check.py`. Run: `py -3 claude/scripts/tests/test_pre_commit_branch_check.py`
44. **pre-pr-create-check test** — `pre-pr-create-check.py`. Run: `py -3 claude/scripts/tests/test_pre_pr_create_check.py`
45. **pre-merge-branch-check test** — `pre-merge-branch-check.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_branch_check.py`
46. **check-journal-compose-liveness test** — `check-journal-compose-liveness.py`. Run: `py -3 claude/scripts/tests/test_check_journal_compose_liveness.py`
47. **disk-space-check test** — `disk-space-check.py`. Run: `py -3 claude/scripts/tests/test_disk_space_check.py`
48. **stop-tile-enumeration-gate test** — `stop-tile-enumeration-gate.py`. Run: `py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py`
49. **setup-link-loop test** — repo-root `setup.sh`'s link arrays, link functions, backup/restore path, Windows preflight, `win_cmd` or dispatch guard (function names in docs/TESTING.md item 49). Run: `bash claude/scripts/tests/test-setup-link-loop.sh`
50. **journal-stop-check test** — `journal-stop-check.py`. Run: `py -3 claude/scripts/tests/test_journal_stop_check.py`
51. **idle-refresher test** — `idle-refresher.py`. Run: `py -3 claude/scripts/tests/test_idle_refresher.py`
52. **pre-auto-merge-checkpoint-gate test** — `pre-auto-merge-checkpoint-gate.py`. Run: `py -3 claude/scripts/tests/test_pre_auto_merge_checkpoint_gate.py` + `bash claude/scripts/tests/test-auto-merge-checkpoint-gate.sh`
53. **`_journal_compose_force` shared-module test** — `_journal_compose_force.py`. Run: `py -3 claude/scripts/tests/test_journal_compose_force.py`
54. **journal-compose-force-resolve end-to-end test** — `journal-compose-force-resolve.py`. Run: `py -3 claude/scripts/tests/test_journal_compose_force_resolve.py`
55. **journal-compose-force-guard test** — `pre-tool-use-journal-compose-force-guard.py`. Run: `py -3 claude/scripts/tests/test_pre_tool_use_journal_compose_force_guard.py`
56. **dev-env-sync test** — `dev-env-sync.py`. Run: `py -3 claude/scripts/tests/test_dev_env_sync.py`
57. **journal-canonical-guard stdout-routing test** — `journal-canonical-guard.py`. Run: `py -3 claude/scripts/tests/test_journal_canonical_guard.py`
58. **stop-journal-stub-checkpoint test** — `stop-journal-stub-checkpoint.py`. Run: `py -3 claude/scripts/tests/test_stop_journal_stub_checkpoint.py`
59. **pre-bash-drift-check test** — `pre-bash-drift-check.py`. Run: `py -3 claude/scripts/tests/test_pre_bash_drift_check.py`
60. **_hookout emitter test** — `_hookout.py`. Run: `py -3 claude/scripts/tests/test_hookout.py`
61. **hook output-contract + ASCII-literal gate** — `claude/scripts/tests/test_hook_output_contract.py`, or `claude/scripts/tests/_hook_wiring.py` (then also run items 62, 63 and 110). Run: `py -3 claude/scripts/tests/test_hook_output_contract.py`
62. **hook safe-exit structural gate** — `claude/scripts/tests/test_hook_safe_exit_guard.py`, or `claude/scripts/tests/_hook_wiring.py` (item 61). Run: `py -3 claude/scripts/tests/test_hook_safe_exit_guard.py`
63. **settings-hook wiring lint** — `claude/scripts/tests/test_settings_hook_wiring.py`, `claude/scripts/tests/_hook_wiring.py` (item 61), or the `hooks` block of `claude/settings.shared.json`. Run: `py -3 claude/scripts/tests/test_settings_hook_wiring.py`
64. **run-hook-tests runner test** — `run-hook-tests.py`. Run: `py -3 claude/scripts/tests/test_run_hook_tests.py`
65. **token-tracker test** — `token-tracker.py`. Run: `py -3 claude/scripts/tests/test_token_tracker.py`
66. **journal-draft-worktree-guard test** — `pre-tool-use-journal-draft-worktree-guard.py`. Run: `py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py`
67. **hook-liveness-check test** — `hook-liveness-check.py`. Run: `py -3 claude/scripts/tests/test_hook_liveness_check.py`
68. **hook-heartbeat-guard structural gate** — which hooks `claude/settings.shared.json` wires, or any wired hook's `main()`. Run: `py -3 claude/scripts/tests/test_hook_heartbeat_guard.py` + `py -3 claude/scripts/tests/test_hook_liveness_check.py`
69. **journal-onboard-check sentinel test** — `journal-onboard-check.py`. Run: `py -3 claude/scripts/tests/test_journal_onboard_check.py`
70. **session-mode-prompt sentinel test** — `session-mode-prompt.py`. Run: `py -3 claude/scripts/tests/test_session_mode_prompt.py`
71. **sweep-scratch-debris test** — `sweep-scratch-debris.py`. Run: `py -3 claude/scripts/tests/test_sweep_scratch_debris.py`
72. **baseline-tests gc test** — `baseline-tests.sh`. Run: `bash claude/scripts/tests/test-baseline-tests-gc.sh`
73. **`_repo_target` shared-module test** — `_repo_target.py`, or the six hooks that delegate to it: `post-pr-merge-project.py`, `pr-merge-reminder.py`, `posttooluse-inert-advisory.py`, `post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`, `post-tool-use.py`. Run: `py -3 claude/scripts/tests/test_repo_target.py`
74. **session-mode-report test** — `session-mode-report.py`. Run: `py -3 claude/scripts/tests/test_session_mode_report.py`
75. **Unreachable-code lint (pylint `unreachable` / W0101)** — any PR touching `claude/scripts/*.py` or `claude/scripts/tests/*.py`. Run: `bash claude/scripts/tests/run-pylint-unreachable.sh`
76. **Testing-index parity gate** — this index or `docs/TESTING.md`: both carry identical, contiguous item numbers and titles, none past 118 and none used twice. Run: `py -3 claude/scripts/tests/test_testing_index_parity.py`
77. **experiment-verdict-gate test** — `stop-experiment-verdict-gate.py`. Run: `py -3 claude/scripts/tests/test_stop_experiment_verdict_gate.py`
78. **worktree-recovery recipe + runbook-parity gate** — `_worktree_recovery.py`, the orphan block message in `pre-tool-use-worktree-path-check.py`, or the `docs/REFERENCE.md` "Worktree deregistration recovery" runbook. Run: `py -3 claude/scripts/tests/test_worktree_recovery.py`
79. **remote-read hygiene lint** — anything under `claude/` that reads a file from a remote ref. Run: `bash claude/scripts/tests/check-remote-read-hygiene.sh`
80. **reconcile-pending-tiles test** — `reconcile-pending-tiles.py`. Run: `py -3 claude/scripts/tests/test_reconcile_pending_tiles.py`
81. **new-day-journal-check day-rollover + stale-canonical-recovery test** — `new-day-journal-check.py`, `_journal_late.py`. Run: `py -3 claude/scripts/tests/test_new_day_journal_check.py`
82. **composed-output stray-terminal-scan test** — `_composed_output_scan.py`, `validate-composed-output.py`. Run: `py -3 claude/scripts/tests/test_composed_output_scan.py`
83. **journal-compose-replay conflict-recovery test** — `journal-compose-replay.sh`, or the Step 10.5 recovery block in `claude/skills/journal-compose/SKILL.md`. Run: `bash claude/scripts/tests/test-journal-compose-replay.sh`
84. **README index-parity gate** — `claude/scripts/tests/test_readme_index_parity.py`, or adding, removing or renaming any file in `claude/scripts/tests/` or `claude/scripts/`. Run: `py -3 claude/scripts/tests/test_readme_index_parity.py`
85. **skill-file-size-guard test** — `pre-tool-use-skill-file-size-guard.py`. Run: `py -3 claude/scripts/tests/test_skill_file_size_guard.py`
86. **skill-file-size-advisory test** — `skill-file-size-advisory.py`. Run: `py -3 claude/scripts/tests/test_skill_file_size_advisory.py`
87. **`_skill_file_size` shared-module test** — `_skill_file_size.py`. Run: `py -3 claude/scripts/tests/test_skill_file_size.py`
88. **journal-shell-write-guard test** — `pre-tool-use-journal-shell-write-guard.py`. Run: `py -3 claude/scripts/tests/test_journal_shell_write_guard.py`
89. **session-start-sync test** — `session-start-sync.py`, `_worktree_liveness.py`. Run: `py -3 claude/scripts/tests/test_session_start_sync.py` + `py -3 claude/scripts/tests/test_worktree_liveness.py`
90. **`_gh_issue_state` shared-module test** — `_gh_issue_state.py`. Run: `py -3 claude/scripts/tests/test_gh_issue_state.py`
91. **retro-chain-status test** — `retro-chain-status.py`. Run: `py -3 claude/scripts/tests/test_retro_chain_status.py`
92. **journal-canon shared-module test** — `_journal_canon.py`, or the four hooks that delegate to it: `pre-tool-use-canonical-mutate-guard.py`, `journal-canonical-guard.py`, `pre-tool-use-journal-draft-worktree-guard.py`, `pre-tool-use-worktree-path-check.py`. Run all five suites: `py -3 claude/scripts/tests/test_journal_canon.py` + `py -3 claude/scripts/tests/test_canonical_mutate_guard.py` + `py -3 claude/scripts/tests/test_journal_canonical_guard.py` + `py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py` + `py -3 claude/scripts/tests/test_worktree_path_check.py`
93. **sibling-hooks hardened-IO migration test** — any of the 12 sibling hooks (listed in docs/TESTING.md item 93), `_hookio.py`'s `read_command`/`read_tool_input_field`/`read_cwd`/`read_exit_code`, or `_hookutil.py`'s `record_heartbeat`/`HOOK_HEARTBEAT_DIR_OVERRIDE`. Run: `py -3 claude/scripts/tests/test_sibling_hooks_hardened_io.py` + `py -3 claude/scripts/tests/test_worktree_path_check.py` + `py -3 claude/scripts/tests/test_hookio.py` + `py -3 claude/scripts/tests/test_hookutil.py`
94. **`_shell_write_detect` shared-module test** — `_shell_write_detect.py`. Run: `py -3 claude/scripts/tests/test_shell_write_detect.py` + `py -3 claude/scripts/tests/test_journal_shell_write_guard.py`
95. **shell-content-write-guard test** — `pre-tool-use-shell-content-write-guard.py`, or `session-mode-prompt.py`'s bypass-mode carve-out. Run: `py -3 claude/scripts/tests/test_shell_content_write_guard.py` + `py -3 claude/scripts/tests/test_session_mode_prompt.py`
96. **replay-shell-content-guard test** — `replay-shell-content-guard.py`; after any detector change to the guard, also re-run `py -3 claude/scripts/replay-shell-content-guard.py --gap`. Run: `py -3 claude/scripts/tests/test_replay_shell_content_guard.py`
97. **journal-project-repo-map test** — `journal-project-repo-map.py`, or the Step 8a Source 3 block in `claude/skills/journal-compose/SKILL.md`. Run: `py -3 claude/scripts/tests/test_journal_project_repo_map.py`
98. **`_settings_sync` shared-module test** — `_settings_sync.py` (for `ensure_launcher`/`hooks_guard` also run item 103; for `seed_hook_config`, item 12), `claude/settings.shared.json`, `claude/hook-config.shared.json`, or repo-root `setup.sh`'s `seed_claude_settings()`. Also run items 2, 49, 61, 62, 63 and 67. Run: `py -3 claude/scripts/tests/test_settings_sync.py` + `bash claude/scripts/tests/test-setup-link-loop.sh`
99. **`_gh_project` shared-module test** — `_gh_project.py`. Run: `py -3 claude/scripts/tests/test_gh_project.py`
100. **journal-compose-meta test** — `journal-compose-meta.py`, the meta handling in `claude/skills/journal-compose/SKILL.md` (the steps are listed in docs/TESTING.md item 100; also run item 83 when Step 10.5 changes), the "Meta journal" trigger list in `claude/CLAUDE.md`, or the meta rule in `claude/routines/daily-journal-compose/SKILL.md`. Run: `py -3 claude/scripts/tests/test_journal_compose_meta.py`
101. **scratch-rm-allow test** — `pre-tool-use-scratch-rm-allow.py`, or `_hookout.py`'s `plan_allow`/`emit_allow` (then also run item 60). Run: `py -3 claude/scripts/tests/test_scratch_rm_allow.py`
102. **replay-scratch-rm-allow test** — `replay-scratch-rm-allow.py`, or the hook's `check_command`/`Reject` surface (then also run item 101); after any change to the hook's lexer, re-run `py -3 claude/scripts/replay-scratch-rm-allow.py`. Run: `py -3 claude/scripts/tests/test_replay_scratch_rm_allow.py`
103. **hook-launch test** — `_hook_launch.py`, the command form of any hook in `claude/settings.shared.json`, or `_settings_sync.py`'s `ensure_launcher` (then also run item 98). Run: `py -3 claude/scripts/tests/test_hook_launch.py`
104. **hook-timing-report test** — `hook-timing-report.py`, or `_hookutil.py`'s `record_duration` line format (then also run item 27). Run: `py -3 claude/scripts/tests/test_hook_timing_report.py`
105. **turn-count-hook journal-stub band test** — `turn-count-hook.py`. Run: `py -3 claude/scripts/tests/test_turn_count_hook.py`
106. **heartbeat-ledger isolation gate** — adding or changing any test that runs a real wired hook, or `claude/scripts/tests/_heartbeat_isolation.py`; no test may write the production ledger `~/.claude/scratch/hook-heartbeat/`. Run: `py -3 claude/scripts/tests/test_hook_heartbeat_isolation.py`
107. **hook-dispatch test** — `hook-dispatch.py`, or which hooks a dispatcher command runs in `claude/settings.shared.json` (then also run items 2, 61-63, 67, 68 and 98). Run: `py -3 claude/scripts/tests/test_hook_dispatch.py`
108. **auto-resume test** — `_auto_resume.py`, `session-start-auto-resume.py`, `auto-resume-launcher.py`, `register-auto-resume.ps1` (for that one, also parse-check it and do the manual steps in docs/TESTING.md item 108). Run: `py -3 claude/scripts/tests/test_auto_resume.py`
109. **hook static-scan test** — adding or changing any `claude/scripts/*.py` that uses subprocess or spawns Python, or any hook command in `claude/settings.shared.json`. Run: `py -3 claude/scripts/tests/test_hook_static_scans.py`
110. **dev-env-doctor test** — `dev-env-doctor.py`, repo-root `setup.sh`'s link arrays, `claude/scripts/tests/_hook_wiring.py`, or the command form of the hooks in `claude/settings.shared.json`. Run: `py -3 claude/scripts/tests/test_dev_env_doctor.py`
111. **reconcile-late-stubs test** — `reconcile-late-stubs.py`, `_journal_late.py`. Run: `py -3 claude/scripts/tests/test_reconcile_late_stubs.py`
112. **token attribution and pricing test** — `_token_pricing.py` (then also run item 65), `backfill-tokens.py`, or `token-report.py`'s `--attribution` mode or session tables. Run: `py -3 claude/scripts/tests/test_token_attribution.py`
113. **window-flash-monitor test** — `window-flash-monitor.py`, `register-window-flash-monitor.ps1`. The `--live` run opens real windows and takes focus, so it is local only. Run: `py -3 claude/scripts/tests/test_window_flash_monitor.py` + `py -3 claude/scripts/tests/test_window_flash_monitor.py --live`
114. **claude-wrappers test** — `bin/claude`, `bin/claude.cmd`. Run: `bash claude/scripts/tests/test-claude-wrappers.sh`
115. **claude-md-size test** — `claude/CLAUDE.md`, this file, or the `## Instruction Detail` and `## Project Instruction Detail` headings in `docs/REFERENCE.md`. Run: `py -3 claude/scripts/tests/test_claude_md_size.py`
116. **deferred-followup-sweep test** — `deferred-followup-sweep.py`. Run: `py -3 claude/scripts/tests/test_deferred_followup_sweep.py`
117. **duplicate-issue retrieval scorer test** — anything under `experiments/2026-11-issue-dup-retrieval/`. Run: `py -3 claude/scripts/tests/test_issue_dup_eval.py`
118. **closing-negation test** — `_closing_negation.py` (then also run items 38 and 116). Run: `py -3 claude/scripts/tests/test_closing_negation.py`

## Experiments

Comparative claims about a process change follow the global `## Experimental Rigor` section and `/experiment-audit`. This repo's experiments keep their harness and results under `experiments/<yyyy-mm>-<slug>/`, and their pre-registration on the tracking issue. dev-env is public, so anything from a private repo is committed as issue numbers and metrics only.

| # | Experiment | Corpus | Instruments and calibration references | Results home |
|---|---|---|---|---|
| 1 | Duplicate-issue retrieval ([#1260](https://github.com/brownm09/dev-env/issues/1260), re-run [#1282](https://github.com/brownm09/dev-env/issues/1282)) | `labels.json`: declared duplicates, avoided duplicates, declared non-duplicates | Scorer and retry merge through `score.py`'s real path, known-good and known-bad fixtures (item 117); live search, `calibrate_search.py`; vector ranking, `calibrate_vector.py` as a smoke check | `experiments/2026-11-issue-dup-retrieval/`, re-run in `rerun/` |

Default tier: Tier 1 for anything that would change a standing rule, hook or skill; Tier 0 for a one-off look.

## Observability

dev-env has **no long-running runtime to instrument**: no application logger, log aggregation or
traces. Its runtime is short-lived hook scripts and skills, which follow the Claude Code hook
contract ([ADR-027](docs/adr/027-userpromptsubmit-blocking-hook-conventions.md); invocation
model, [ADR-007](docs/adr/007-hook-command-invocation.md)):

- **Use the stream the event type delivers on the exit code you chose; stderr isn't always the
  safe channel.** A **blocking** hook uses stderr and exit 2. A **non-blocking advisory** (always
  exit 0) uses the stream its event forwards on exit 0: **stdout** for `UserPromptSubmit`,
  `UserPromptExpansion` and `SessionStart`. Other events, such as `Stop`, forward neither stream
  on exit 0, so an advisory there must block (exit 2 + stderr) to be seen. Getting this backwards
  is silent ([ADR-098](docs/adr/098-dev-env-sync-advisories-to-stdout.md),
  [ADR-091](docs/adr/091-journal-stop-check-archive-reminder-blocking.md)).
  [Detail](docs/REFERENCE.md#project-detail-hook-output-streams)
- **The verification suite in `## Testing` stands in for boundary observability.** A change to a
  hook or script keeps it green.

What the Pass 3 Observability dimension verifies here: a new or changed hook or script routes its
output to the stream its event type and blocking intent deliver, chooses its exit code
deliberately (0 advisory, non-zero blocking), and is covered by a `## Testing` self-test. Pure
docs or config changes answer "N/A — no runtime."

## Documentation Maintenance

When a PR modifies any of the paths below, update the listed reference docs **in the same PR**.

| Change | Required updates |
|---|---|
| Add / remove / rename a skill in `claude/skills/` | Skills table in `README.md` + `docs/REFERENCE.md` Skills section |
| Add / remove / rename an event-driven hook script in `claude/hooks/` (or a script in `claude/scripts/` that fires on a Claude Code hook event) | Hooks table in `README.md` + `docs/REFERENCE.md` Hooks section |
| Add / remove / rename a utility/on-demand script in `claude/scripts/` (no hook event — invoked manually or from a skill) | Utilities table (On-demand scripts) in `docs/REFERENCE.md` |
| Add / remove / rename a routine in `claude/routines/` | Routines table in `README.md` + `docs/REFERENCE.md` Routines section |
| Change `hook-config.json` schema (new field, removed field, type change) | Configuration subsection in `docs/REFERENCE.md` |
| Change a skill's invocation syntax or options | Skill entry in `docs/REFERENCE.md` |
| Rename or move any file linked in `README.md` or `docs/REFERENCE.md` | Update the link in both files |

## Dev-Env Architecture

`~/.claude/` is split between two categories. Treat them differently.

**Owned by `brownm09/dev-env` — symlinked, version-controlled:**

| Path | dev-env source |
|---|---|
| `~/.claude/CLAUDE.md` | `claude/CLAUDE.md` |
| `~/.claude/scripts/` | `claude/scripts/` (directory symlink) |
| `~/.claude/skills/` | `claude/skills/` (directory symlink) |
| `~/.claude/hooks/` | `claude/hooks/` (directory symlink) |
| `~/.claude/routines/` | `claude/routines/` (directory junction) |
| `~/.claude/templates/` | `claude/templates/` (directory symlink) |

`setup.sh` creates these (older installs may hold junctions instead; both resolve the same).
`py -3 ~/.claude/scripts/dev-env-doctor.py` verifies the whole layout on any machine; adding a
second one: [`docs/REFERENCE.md` → Adding a Second Machine](docs/REFERENCE.md#adding-a-second-machine).

**Machine-local only — never commit:** `scratch/`, `projects/`, `sessions/`, `backups/`, `ide/`,
`plans/`, `shell-snapshots/`, `routine-findings/`

**`~/.claude/hook-launch.py` is installed by dev-env, deliberately outside every junction.** Every
hook runs as `pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/<name>.py`,
so a wired script that is missing fails **open** instead of blocking every matching tool call
([ADR-148](docs/adr/148-missing-hook-script-fails-open-via-launcher.md)).
`_settings_sync.ensure_launcher` copies it from `claude/scripts/_hook_launch.py` on every sync,
and the sync refuses to apply `hooks` while a script they name is absent. Never delete the file
by hand; restore it with `py -3 C:/Users/brown/.claude/scripts/_settings_sync.py` from a hook-free
terminal. [Detail](docs/REFERENCE.md#project-detail-dev-env-architecture)

**`~/.claude/settings.json` is a real, machine-local file the app writes, not a symlink**
([ADR-139](docs/adr/139-machine-local-settings-with-shared-source-sync.md)). dev-env tracks
`claude/settings.shared.json` and syncs it **into** the live file:

| Class | Keys | Behavior |
|---|---|---|
| **Owned** (tracked) | `hooks`, `permissions` | Replaced wholesale on every sync — deletions propagate, and the app cannot silently drop an allow rule. |
| **Seed** (tracked) | `model`, `effortLevel`, `autoCompactWindow`, `env` | Written only when absent, so a `/config` change sticks ([ADR-079](docs/adr/079-backup-restore-convention.md) rule 3). `env` is seeded as one block: a machine that already has one keeps it whole and gets no new shared variables. |
| **Machine-local** (never tracked) | `theme`, `tui`, `agentPushNotifEnabled`, `inputNeededNotifEnabled`, `skipWorkflowUsageWarning`, `autoMode` | Never read, written, or removed by the sync. `autoMode.environment` describes personal data and must stay out of git. |

**Adding a key to `claude/settings.shared.json` requires classifying it** in `OWNED_KEYS` or
`SEED_KEYS` in [`claude/scripts/_settings_sync.py`](claude/scripts/_settings_sync.py); an
unclassified key is reported as a warning and *not applied*. A key the app writes with
project-specific content belongs in that project's `.claude/settings.local.json`, not the user
file (global rule, *Platform & Environment*). `dev-env-sync.py` runs the sync every prompt; to
apply it by hand after editing the shared file: `py -3 ~/.claude/scripts/_settings_sync.py`.
[Detail](docs/REFERENCE.md#project-detail-dev-env-architecture)

**Rule:** Commit every addition or change to a dev-env-owned artifact (hook script, skill,
settings, `CLAUDE.md`) to `brownm09/dev-env` via branch and PR before the session ends; never
leave global tooling untracked.

**Rule:** The canonical dev-env worktree (`~/Git/dev-env`) must stay on `main` at all times. All
dev-env changes go through a separate worktree (`EnterWorktree` or `git worktree add`), because
`~/.claude/` links into the canonical's working tree, so a feature branch there hides newly
merged hooks and scripts. `dev-env-sync` warns on every prompt while this is broken.

**Routines:** merging a change to `claude/routines/<name>/SKILL.md` does **not** update the live
task. The `scheduled-tasks` MCP tool owns a separate, real directory, `~/.claude/scheduled-tasks/`;
`~/.claude/routines/` is only a read-only mirror. After merging, call `update_scheduled_task` (or
`create_scheduled_task`) so the live prompt matches, and prefer a live prompt that reads its own
canonical `SKILL.md` at run time and falls back to an embedded copy, as `weekly-memory-audit` does
([ADR-003 amendment](docs/adr/003-config-in-version-control.md),
[dev-env#344](https://github.com/brownm09/dev-env/issues/344)).
[Detail](docs/REFERENCE.md#project-detail-dev-env-architecture)

**Routine authoring:** a routine that reads repo-resident files at run time invokes the
`sync-routine-worktree` skill as Step 0, before reading them, passing `REPO`, `VERIFY_FILE` and
`PREFIX` and treating the return as a guard. Pattern: `claude/routines/nightly-cover-letters/SKILL.md`;
rationale: [ADR-013](docs/adr/013-sync-routine-worktree-skill.md).

**Doc-reconciliation checkpoint**, at the three ADR-warrant moments (after plan approval, after
`gh pr create`, before `gh pr merge`): if the change adds, removes, renames or changes the
behavior of a skill, hook, script or routine, `README.md` and the Documentation Maintenance table
above must be satisfied in this PR, before merging
([ADR-019](docs/adr/019-doc-reconciliation-enforcement.md)).

**Downstream artifacts that name specific dev-env skills/hooks/routines** (update in the same PR
as a rename or retirement): `tech-leadership-reference/ai-adoption/ai-adoption-readiness-framework.md`,
whose Appendix C names `/propose`, `/review`, `/journal-compose`, `/research`, and the
`prune-stale-worktrees` and nightly journal compose routines.

**Repo path:** `C:/Users/brown/Git/dev-env`

## GitHub Project

All new dev-env issues and PRs must be added to the **Dev Env** project and given an Impact rating and Why description before work begins. The single-select option-mutation hazard that applies to every project is in the global `claude/CLAUDE.md` → Dev-Env & Project Boards.

**Project IDs:**
- Project number: `3`, owner: `brownm09`
- Project node ID: `PVT_kwHOAjEKvM4BWKFe`

**Field IDs:**

| Field | ID | Options |
|---|---|---|
| Status | `PVTSSF_lAHOAjEKvM4BWKFezhRgkMY` | Todo=`f75ad846`, In Progress=`47fc9ee4`, Done=`98236657` |
| Impact | `PVTSSF_lAHOAjEKvM4BWKFezhRgkNc` | High=`08de2558`, Medium=`6320e8a6`, Low=`d8a85c2f` |
| Why | `PVTF_lAHOAjEKvM4BWKFezhRgkN0` | (text) |

**Impact guidelines:**

| Level | Meaning |
|---|---|
| High | Causes manual recovery work or token waste on every occurrence |
| Medium | Recurs periodically or silently degrades correctness over time |
| Low | Nice-to-have; low frequency or easily worked around |

**Workflow — automated via PostToolUse hook:** After `gh issue create` or `gh pr create`
succeeds, `post-tool-use.py` adds the issue or PR to project #3 and exits 2, printing the
`gh project item-edit` commands that set Impact and Why. **Run those commands immediately —
before any file edits** ([ADR-023](docs/adr/023-generic-required-fields-issue-hook.md)). Its
config, the gitignored `.claude/hook-config.json`, is copied from tracked
[`claude/hook-config.shared.json`](claude/hook-config.shared.json) (change project IDs there, in
a PR); if it's missing, the hook warns
([ADR-052 Amendment 2](docs/adr/052-worktree-config-canonical-fallback.md)).

**Fallback, when the hook didn't fire or the add failed:** add the item, set Impact and Why, and
cache the item ID with the commands in
[Project detail: GitHub Project board](docs/REFERENCE.md#project-detail-github-project-board)
(the file is too large to read whole: `grep -n "Project detail: GitHub Project board"
docs/REFERENCE.md`, then read from that line). They need the `project` scope
(`gh auth refresh -s project`). No `[project-hook]` output usually means the chain's last command
exited non-zero, which runs no PostToolUse hook
([ADR-053's 2026-11-07 amendment](docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md)).

**When GraphQL rejects a call, use REST,** which spends the separate `core` bucket, even when
`gh api rate_limit` shows GraphQL budget left. REST takes numeric field IDs: Status=`341872838`,
Impact=`341872855`, Why=`341872861`, with the option IDs above. Commands and four pitfalls are in
the same detail section ([dev-env#1239](https://github.com/brownm09/dev-env/issues/1239)).

**Look up an item ID by issue or PR number with `get-project-item.sh`,** never
`gh project item-list` (about 1,011 GraphQL points on this board). It reads a local cache first,
costing zero `gh` calls on a hit ([ADR-141](docs/adr/141-project-item-id-creation-time-cache.md)),
and falls back to a one-item query:

```bash
ITEM_ID=$(bash ~/.claude/scripts/get-project-item.sh <N>)
```

**Move status** — set the Status field (`PVTSSF_lAHOAjEKvM4BWKFezhRgkMY`) to In Progress (`47fc9ee4`) when work begins, Done (`98236657`) after the PR merges:

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY --single-select-option-id <status-option-id>
```
