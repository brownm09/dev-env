# dev-env — Project Instructions

The global Claude Code configuration lives in [`claude/CLAUDE.md`](claude/CLAUDE.md),
symlinked to `~/.claude/CLAUDE.md`. All workflow rules, hook invariants, model selection
guidelines, and journal conventions are defined there and apply to every project.

## Reference Documentation

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Quick-reference tables for skills, hooks, and routines |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Detailed descriptions, invocation syntax, config options, and ADR links |
| [docs/TESTING.md](docs/TESTING.md) | Per-item behavioral detail for the `## Testing` index (what each test pins, scope gaps, incident history) |
| [docs/adr/](docs/adr/) | Design decisions behind rules in `claude/CLAUDE.md` |

## Testing

This is the canonical, complete index of dev-env verification commands — one numbered item
per test file or gate. Numbering is stable and append-only; it is collision-checked against
`origin/main` at merge time by `pre-merge-numbering-check.py`
([ADR-074](docs/adr/074-pre-merge-numbering-collision-check.md)) — if blocked, rebase,
renumber to the next free number, and re-merge. The global "Test before PR" rule in
[`claude/CLAUDE.md`](claude/CLAUDE.md) defers to this section.

**Run the whole suite** with `py -3 claude/scripts/run-hook-tests.py` — it glob-discovers
every test below (plus `claude/hooks/tests/`) and is exactly what
`.github/workflows/hook-tests.yml` runs on `windows-latest` for every `pull_request`
([ADR-103](docs/adr/103-shared-hookout-emitter.md)). When changing a script, also run its
own item's command below before `gh pr create`.

**Full per-item behavioral detail** — what each test pins, deliberate scope gaps, incident
history — lives in [docs/TESTING.md](docs/TESTING.md) under the **same item numbers**
(consult it before modifying a test or its script; extend it in the same PR that changes
the item). A one-line navigational map of the test directory is
[claude/scripts/tests/README.md](claude/scripts/tests/README.md).

1. **Hook-script syntax check** — run from the repo root to verify all hook scripts parse. Run: `py -3 -c "import ast,sys; [ast.parse(open(f,encoding='utf-8').read(),f) for f in sys.argv[1:]]" claude/scripts/*.py`
2. **`pyw -3` stdio verification** — Windows-only; confirms `pythonw.exe` honors parent-supplied pipes (the ADR-007 invariant). Run: `py -3 claude/scripts/tests/test_pyw_stdio.py`
3. **Pre-push hook self-test** — required when changing `claude/hooks/pre-push`. Run: `bash claude/hooks/tests/test-pre-push-lockfile.sh`
4. **Docs-only guard** — for docs-only changes to `claude/CLAUDE.md`. Run: `grep -n 'date -u' claude/CLAUDE.md` and confirm every match is an internal operational-artifact context (lock files, log timestamps), never stub-filename or branch-name descriptions.
5. **Script path-hygiene lint** — required when adding or changing any `claude/scripts/*.sh` or `claude/hooks/*` shell script. Run: `bash claude/scripts/tests/check-script-path-hygiene.sh`
6. **`get-project-item.sh` smoke test** — required when changing `claude/scripts/get-project-item.sh`. Run: `bash claude/scripts/tests/test-get-project-item.sh`
7. **shellcheck gate** — recommended when changing any shell script. Run: `bash claude/scripts/tests/run-shellcheck.sh`
8. **usage-snapshot classifier test** — required when changing `claude/scripts/usage-snapshot.py`. Run: `py -3 claude/scripts/tests/test_usage_snapshot.py`
9. **worktree-npm-install gate test** — required when changing `claude/scripts/worktree-npm-install.py`. Run: `py -3 claude/scripts/tests/test_worktree_npm_install.py`
10. **post-pr-merge-reclaim test** — required when changing `claude/scripts/post-pr-merge-reclaim.py`. Run: `py -3 claude/scripts/tests/test_post_pr_merge_reclaim.py`
11. **memory-write-advisory test** — required when changing `claude/scripts/memory-write-advisory.py`. Run: `py -3 claude/scripts/tests/test_memory_write_advisory.py`
12. **post-tool-use test** — required when changing `claude/scripts/post-tool-use.py`. Run: `py -3 claude/scripts/tests/test_post_tool_use.py`
13. **_hookio shared-read test** — required when changing `claude/scripts/_hookio.py`. Run: `py -3 claude/scripts/tests/test_hookio.py`
14. **post-pr-merge-project test** — required when changing `claude/scripts/post-pr-merge-project.py`. Run: `py -3 claude/scripts/tests/test_post_pr_merge_project.py`
15. **post-pr-merge-pull test** — required when changing `claude/scripts/post-pr-merge-pull.py`. Run: `py -3 claude/scripts/tests/test_post_pr_merge_pull.py`
16. **stub-push-archive-reminder test** — required when changing `claude/scripts/stub-push-archive-reminder.py`. Run: `py -3 claude/scripts/tests/test_stub_push_archive_reminder.py`
17. **worktree-liveness guard test** — required when changing `claude/scripts/_worktree_liveness.py` or the prune/reclaim scripts' use of it. Run: `py -3 claude/scripts/tests/test_worktree_liveness.py`
18. **posttooluse-inert-advisory test** — required when changing `claude/scripts/posttooluse-inert-advisory.py`. Run: `py -3 claude/scripts/tests/test_posttooluse_inert_advisory.py`
19. **reconcile-open-prs test** — required when changing `claude/scripts/reconcile-open-prs.py`. Run: `py -3 claude/scripts/tests/test_reconcile_open_prs.py`
20. **post-compact open-PR + pending-tile reader test** — required when changing `claude/scripts/post-compact.py`. Run: `py -3 claude/scripts/tests/test_post_compact.py`
21. **journal-shards shared-reader test** — required when changing `claude/scripts/_journal_shards.py`. Run: `py -3 claude/scripts/tests/test_journal_shards.py`
22. **worktree-topology test** — required when changing `claude/scripts/_worktree_topology.py` or the squat-detection paths in `prune-merged-worktrees.py` / `post-pr-merge-pull.py` / `dev-env-sync.py` / `journal-canonical-guard.py`. Run: `py -3 claude/scripts/tests/test_worktree_topology.py`
23. **post-merge-tile-checkpoint test** — required when changing `claude/scripts/post-merge-tile-checkpoint.py`. Run: `py -3 claude/scripts/tests/test_post_merge_tile_checkpoint.py`
24. **pre-merge-message-check test** — required when changing `claude/scripts/pre-merge-message-check.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_message_check.py`
25. **manifest field validator test** — required when changing `claude/scripts/validate-manifest.py`. Run: `py -3 claude/scripts/tests/test_validate_manifest.py`
26. **prune-merged-worktrees test** — required when changing `claude/scripts/prune-merged-worktrees.py`. Run: `py -3 claude/scripts/tests/test_prune_merged_worktrees.py`
27. **_hookutil shared-helper test** — required when changing `claude/scripts/_hookutil.py`. Run: `py -3 claude/scripts/tests/test_hookutil.py`
28. **pr-merge-reminder test** — required when changing `claude/scripts/pr-merge-reminder.py`. Run: `py -3 claude/scripts/tests/test_pr_merge_reminder.py`
29. **reconcile-project-board test** — required when changing `claude/scripts/reconcile-project-board.py`. Run: `py -3 claude/scripts/tests/test_reconcile_project_board.py`
30. **reclaim-worktree-disk test** — required when changing `claude/scripts/reclaim-worktree-disk.py`. Run: `py -3 claude/scripts/tests/test_reclaim_worktree_disk.py`
31. **_repo_scan shared-module test** — required when changing `claude/scripts/_repo_scan.py` or the `--scan-dir` discovery path in `prune-merged-worktrees.py` / `reclaim-worktree-disk.py` / `reconcile-project-board.py`. Run: `py -3 claude/scripts/tests/test_repo_scan.py`
32. **worktree-path-check test** — required when changing `claude/scripts/pre-tool-use-worktree-path-check.py`. Run: `py -3 claude/scripts/tests/test_worktree_path_check.py`
33. **canonical-mutate-guard test** — required when changing `claude/scripts/pre-tool-use-canonical-mutate-guard.py`. Run: `py -3 claude/scripts/tests/test_canonical_mutate_guard.py`
34. **merge-stale-pr self-test** — required when changing `claude/scripts/merge-stale-pr.sh`. Run: `bash claude/scripts/tests/test-merge-stale-pr.sh`
35. **worktree-canon shared-module test** — required when changing `claude/scripts/_worktree_canon.py`. Run: `py -3 claude/scripts/tests/test_worktree_canon.py`
36. **`_winsubp` shared-module test** — required when changing `claude/scripts/_winsubp.py`. Run: `py -3 claude/scripts/tests/test_winsubp.py` + `py -3 claude/scripts/tests/test_pyw_stdio.py`
37. **numbering-collision check test** — required when changing `claude/scripts/pre-merge-numbering-check.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_numbering_check.py`
38. **pre-merge-findings-gate test** — required when changing `claude/scripts/pre-merge-findings-gate.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_findings_gate.py` + `bash claude/scripts/tests/test-merge-findings-gate.sh`
39. **Crude command-substring-check regression test** — required when adding or changing any `claude/scripts/*.py` file. Run: `py -3 claude/scripts/tests/test_no_crude_command_substring_checks.py`
40. **journal-shard-write-advisory test** — required when changing `claude/scripts/journal-shard-write-advisory.py`. Run: `py -3 claude/scripts/tests/test_journal_shard_write_advisory.py`
41. **`_journal_schema` shared-module test** — required when changing `claude/scripts/_journal_schema.py`. Run: `py -3 claude/scripts/tests/test_journal_schema.py`
42. **`_bash_state` shared-module test** — required when changing `claude/scripts/_bash_state.py`. Run: `py -3 claude/scripts/tests/test_bash_state.py`
43. **pre-commit-branch-check test** — required when changing `claude/scripts/pre-commit-branch-check.py`. Run: `py -3 claude/scripts/tests/test_pre_commit_branch_check.py`
44. **pre-pr-create-check test** — required when changing `claude/scripts/pre-pr-create-check.py`. Run: `py -3 claude/scripts/tests/test_pre_pr_create_check.py`
45. **pre-merge-branch-check test** — required when changing `claude/scripts/pre-merge-branch-check.py`. Run: `py -3 claude/scripts/tests/test_pre_merge_branch_check.py`
46. **check-journal-compose-liveness test** — required when changing `claude/scripts/check-journal-compose-liveness.py`. Run: `py -3 claude/scripts/tests/test_check_journal_compose_liveness.py`
47. **disk-space-check test** — required when changing `claude/scripts/disk-space-check.py`. Run: `py -3 claude/scripts/tests/test_disk_space_check.py`
48. **stop-tile-enumeration-gate test** — required when changing `claude/scripts/stop-tile-enumeration-gate.py`. Run: `py -3 claude/scripts/tests/test_stop_tile_enumeration_gate.py`
49. **setup-link-loop test** — required when changing `setup.sh`'s `CLAUDE_FILE_LINKS` / `CLAUDE_DIR_LINKS` arrays or its `link_claude_windows()` / `link_claude_unix()` functions. Run: `bash claude/scripts/tests/test-setup-link-loop.sh`
50. **journal-stop-check test** — required when changing `claude/scripts/journal-stop-check.py`. Run: `py -3 claude/scripts/tests/test_journal_stop_check.py`
51. **idle-refresher test** — required when changing `claude/scripts/idle-refresher.py`. Run: `py -3 claude/scripts/tests/test_idle_refresher.py`
52. **pre-auto-merge-checkpoint-gate test** — required when changing `claude/scripts/pre-auto-merge-checkpoint-gate.py`. Run: `py -3 claude/scripts/tests/test_pre_auto_merge_checkpoint_gate.py` + `bash claude/scripts/tests/test-auto-merge-checkpoint-gate.sh`
53. **`_journal_compose_force` shared-module test** — required when changing `claude/scripts/_journal_compose_force.py`. Run: `py -3 claude/scripts/tests/test_journal_compose_force.py`
54. **journal-compose-force-resolve end-to-end test** — required when changing `claude/scripts/journal-compose-force-resolve.py`. Run: `py -3 claude/scripts/tests/test_journal_compose_force_resolve.py`
55. **journal-compose-force-guard test** — required when changing `claude/scripts/pre-tool-use-journal-compose-force-guard.py`. Run: `py -3 claude/scripts/tests/test_pre_tool_use_journal_compose_force_guard.py`
56. **dev-env-sync test** — required when changing `claude/scripts/dev-env-sync.py`. Run: `py -3 claude/scripts/tests/test_dev_env_sync.py`
57. **journal-canonical-guard stdout-routing test** — required when changing `claude/scripts/journal-canonical-guard.py`. Run: `py -3 claude/scripts/tests/test_journal_canonical_guard.py`
58. **stop-journal-stub-checkpoint test** — required when changing `claude/scripts/stop-journal-stub-checkpoint.py`. Run: `py -3 claude/scripts/tests/test_stop_journal_stub_checkpoint.py`
59. **pre-bash-drift-check test** — required when changing `claude/scripts/pre-bash-drift-check.py`. Run: `py -3 claude/scripts/tests/test_pre_bash_drift_check.py`
60. **_hookout emitter test** — required when changing `claude/scripts/_hookout.py`. Run: `py -3 claude/scripts/tests/test_hookout.py`
61. **hook output-contract + ASCII-literal gate** — required when changing `claude/scripts/tests/test_hook_output_contract.py` or the shared `claude/scripts/tests/_hook_wiring.py` (the settings.json parser all three PR3 gates — items 61/62/63 — share; run all three when changing it). Run: `py -3 claude/scripts/tests/test_hook_output_contract.py`
62. **hook safe-exit structural gate** — required when changing `claude/scripts/tests/test_hook_safe_exit_guard.py` (or `_hook_wiring.py`, item 61). Run: `py -3 claude/scripts/tests/test_hook_safe_exit_guard.py`
63. **settings-hook wiring lint** — required when changing `claude/scripts/tests/test_settings_hook_wiring.py` (or `_hook_wiring.py`, item 61) or the `hooks` block of `claude/settings.json`. Run: `py -3 claude/scripts/tests/test_settings_hook_wiring.py`
64. **run-hook-tests runner test** — required when changing `claude/scripts/run-hook-tests.py`. Run: `py -3 claude/scripts/tests/test_run_hook_tests.py`
65. **token-tracker test** — required when changing `claude/scripts/token-tracker.py`. Run: `py -3 claude/scripts/tests/test_token_tracker.py`
66. **journal-draft-worktree-guard test** — required when changing `claude/scripts/pre-tool-use-journal-draft-worktree-guard.py`. Run: `py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py`
67. **hook-liveness-check test** — required when changing `claude/scripts/hook-liveness-check.py`. Run: `py -3 claude/scripts/tests/test_hook_liveness_check.py`
68. **hook-heartbeat-guard structural gate** — required when changing which hooks are wired in `claude/settings.json`, or any wired hook's `main()`. Run: `py -3 claude/scripts/tests/test_hook_heartbeat_guard.py` + `py -3 claude/scripts/tests/test_hook_liveness_check.py`
69. **journal-onboard-check sentinel test** — required when changing `claude/scripts/journal-onboard-check.py`. Run: `py -3 claude/scripts/tests/test_journal_onboard_check.py`
70. **session-mode-prompt sentinel test** — required when changing `claude/scripts/session-mode-prompt.py`. Run: `py -3 claude/scripts/tests/test_session_mode_prompt.py`
71. **sweep-scratch-debris test** — required when changing `claude/scripts/sweep-scratch-debris.py`. Run: `py -3 claude/scripts/tests/test_sweep_scratch_debris.py`
72. **baseline-tests gc test** — required when changing `claude/scripts/baseline-tests.sh`. Run: `bash claude/scripts/tests/test-baseline-tests-gc.sh`
73. **`_repo_target` shared-module test** — required when changing `claude/scripts/_repo_target.py` or the six hooks that delegate to it (`post-pr-merge-project.py`, `pr-merge-reminder.py`, `posttooluse-inert-advisory.py`, `post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`, `post-tool-use.py`). Run: `py -3 claude/scripts/tests/test_repo_target.py`
74. **session-mode-report test** — required when changing `claude/scripts/session-mode-report.py`. Run: `py -3 claude/scripts/tests/test_session_mode_report.py`
75. **Unreachable-code lint (pylint `unreachable` / W0101)** — required before any PR touching `claude/scripts/*.py` or `claude/scripts/tests/*.py`. Run: `bash claude/scripts/tests/run-pylint-unreachable.sh`
76. **Testing-index parity gate** — required when changing this `## Testing` index or `docs/TESTING.md`; asserts both files carry identical, contiguous item numbers and titles (the ADR-114 two-file sync rule). Run: `py -3 claude/scripts/tests/test_testing_index_parity.py`
77. **experiment-verdict-gate test** — required when changing `claude/scripts/stop-experiment-verdict-gate.py`. Run: `py -3 claude/scripts/tests/test_stop_experiment_verdict_gate.py`
78. **worktree-recovery recipe + runbook-parity gate** — required when changing `claude/scripts/_worktree_recovery.py`, the orphan block message in `claude/scripts/pre-tool-use-worktree-path-check.py`, or the `docs/REFERENCE.md` "Worktree deregistration recovery" runbook. Run: `py -3 claude/scripts/tests/test_worktree_recovery.py`
79. **remote-read hygiene lint** — required when adding or changing anything under `claude/` that reads a file from a remote ref; flags a `git show <ref>:<path>` paired with `2>/dev/null` (the dev-env#602 / #877 MSYS-mangling false-absent class, ADR-120). Run: `bash claude/scripts/tests/check-remote-read-hygiene.sh`
80. **reconcile-pending-tiles test** — required when changing `claude/scripts/reconcile-pending-tiles.py`. Run: `py -3 claude/scripts/tests/test_reconcile_pending_tiles.py`
81. **new-day-journal-check day-rollover + stale-canonical-recovery test** — required when changing `claude/scripts/new-day-journal-check.py`. Run: `py -3 claude/scripts/tests/test_new_day_journal_check.py`
82. **composed-output stray-terminal-scan test** — required when changing `claude/scripts/_composed_output_scan.py` or `claude/scripts/validate-composed-output.py`. Run: `py -3 claude/scripts/tests/test_composed_output_scan.py`
83. **journal-compose-replay conflict-recovery test** — required when changing `claude/scripts/journal-compose-replay.sh` or the Step 10.5 recovery block in `claude/skills/journal-compose/SKILL.md`. Run: `bash claude/scripts/tests/test-journal-compose-replay.sh`
84. **README index-parity gate** — required when changing `claude/scripts/tests/test_readme_index_parity.py`, or when adding/removing/renaming any file in `claude/scripts/tests/` or `claude/scripts/` (it gates each directory's README against the directory: row coverage, orphan rows, header counts, section `(N)` counts). Run: `py -3 claude/scripts/tests/test_readme_index_parity.py`
85. **skill-file-size-guard test** — required when changing `claude/scripts/pre-tool-use-skill-file-size-guard.py`. Run: `py -3 claude/scripts/tests/test_skill_file_size_guard.py`
86. **skill-file-size-advisory test** — required when changing `claude/scripts/skill-file-size-advisory.py`. Run: `py -3 claude/scripts/tests/test_skill_file_size_advisory.py`
87. **`_skill_file_size` shared-module test** — required when changing `claude/scripts/_skill_file_size.py`. Run: `py -3 claude/scripts/tests/test_skill_file_size.py`
88. **journal-shell-write-guard test** — required when changing `claude/scripts/pre-tool-use-journal-shell-write-guard.py`. Run: `py -3 claude/scripts/tests/test_journal_shell_write_guard.py`
89. **session-start-sync test** — required when changing `claude/scripts/session-start-sync.py` or `claude/scripts/_worktree_liveness.py`. Run: `py -3 claude/scripts/tests/test_session_start_sync.py` + `py -3 claude/scripts/tests/test_worktree_liveness.py`
90. **`_gh_issue_state` shared-module test** — required when changing `claude/scripts/_gh_issue_state.py`. Run: `py -3 claude/scripts/tests/test_gh_issue_state.py`
91. **retro-chain-status test** — required when changing `claude/scripts/retro-chain-status.py`. Run: `py -3 claude/scripts/tests/test_retro_chain_status.py`
92. **journal-canon shared-module test** — required when changing `claude/scripts/_journal_canon.py` or the four hooks that delegate to it (`pre-tool-use-canonical-mutate-guard.py`, `journal-canonical-guard.py`, `pre-tool-use-journal-draft-worktree-guard.py`, `pre-tool-use-worktree-path-check.py`) — the module's own ADR-133 safety claim is that all four consumer suites stay green across a change to it, so re-run them together with its own suite. Run: `py -3 claude/scripts/tests/test_journal_canon.py` + `py -3 claude/scripts/tests/test_canonical_mutate_guard.py` + `py -3 claude/scripts/tests/test_journal_canonical_guard.py` + `py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py` + `py -3 claude/scripts/tests/test_worktree_path_check.py`
93. **sibling-hooks hardened-IO migration test** — required when changing any of the 12 sibling hooks covered here, `_hookio.py`'s `read_command`/`read_tool_input_field`/`read_cwd`/`read_exit_code`, or `_hookutil.py`'s `record_heartbeat`/`HOOK_HEARTBEAT_DIR_OVERRIDE` (see `docs/TESTING.md` item 93 for the full file list, the two-arm AST detector design, and the deliberate scope boundaries — the exit_code-fallback-branch exclusion and its accepted-coercion trade-off, pinned separately in items 12 and 28). Run: `py -3 claude/scripts/tests/test_sibling_hooks_hardened_io.py` + `py -3 claude/scripts/tests/test_worktree_path_check.py` + `py -3 claude/scripts/tests/test_hookio.py` + `py -3 claude/scripts/tests/test_hookutil.py`
94. **`_shell_write_detect` shared-module test** — required when changing `claude/scripts/_shell_write_detect.py`; the module's ADR-138 safety claim is that the ADR-129 guard it was extracted from stays green across the move, so run that consumer suite alongside its own (the same pairing item 92 uses for `_journal_canon.py`). Run: `py -3 claude/scripts/tests/test_shell_write_detect.py` + `py -3 claude/scripts/tests/test_journal_shell_write_guard.py`
95. **shell-content-write-guard test** — required when changing `claude/scripts/pre-tool-use-shell-content-write-guard.py` or `claude/scripts/session-mode-prompt.py`'s bypass-mode carve-out (ADR-138 lands in both — the guard blocks the shape, the prompt hook delivers the precedence rule). Run: `py -3 claude/scripts/tests/test_shell_content_write_guard.py` + `py -3 claude/scripts/tests/test_session_mode_prompt.py`
96. **replay-shell-content-guard test** — required when changing `claude/scripts/replay-shell-content-guard.py`; the on-demand reader that replays the ADR-138 guard over recorded session transcripts and reports its block rate, mechanism mix, override use, and failure-rate enrichment (ADR-138 Amendment 1). Re-run the script itself after any detector change to the guard — `py -3 claude/scripts/replay-shell-content-guard.py --gap`. Run: `py -3 claude/scripts/tests/test_replay_shell_content_guard.py`
97. **journal-project-repo-map test** — required when changing `claude/scripts/journal-project-repo-map.py` or the Step 8a Source 3 block in `claude/skills/journal-compose/SKILL.md` (ADR-032 Amendment 1 lands in both — the script resolves the mapping and names every skip, the skill consumes `query_order` and surfaces those skips to the user). Run: `py -3 claude/scripts/tests/test_journal_project_repo_map.py`
98. **`_settings_sync` shared-module test** — required when changing `claude/scripts/_settings_sync.py`, its `OWNED_KEYS`/`SEED_KEYS` classification, `claude/settings.shared.json`, or `setup.sh`'s `seed_claude_settings()`. Also re-run the three gates that read the shared file through `_hook_wiring.py` (items 61/62/63), `test_pyw_stdio.py` (item 2), `test_hook_liveness_check.py` (item 67), and the setup link-loop (item 49) — all five were repointed off `claude/settings.json` by ADR-139. Run: `py -3 claude/scripts/tests/test_settings_sync.py` + `bash claude/scripts/tests/test-setup-link-loop.sh`
99. **`_gh_project` shared-module test** — required when changing `claude/scripts/_gh_project.py`. Run: `py -3 claude/scripts/tests/test_gh_project.py`

## Observability

dev-env has **no long-running runtime to instrument** — it is a configuration repo whose
"runtime" is short-lived hook scripts and skills invoked by Claude Code. There is no
application logger, no log aggregation, and no traces. This section exists to satisfy the
global per-project `## Observability` requirement and to tell the *Plan-then-optimize → Pass 3*
Observability dimension what to verify here instead.

Hooks and scripts observe the Claude Code hook contract rather than a logging stack:

- **Stream choice is event-type- and intent-dependent — not a blanket "diagnostics go to
  stderr" rule.** A **blocking** hook uses stderr + exit 2 (the one channel every hook event
  forwards to Claude on a non-zero exit). A **non-blocking advisory** (always exits 0) must use
  whichever stream that *specific* event type forwards on exit 0 — for `UserPromptSubmit`/
  `UserPromptExpansion`/`SessionStart` that's **stdout** (stderr is not surfaced there); other
  event types (e.g. `Stop`) forward neither stream on exit 0, so an always-exit-0 advisory is
  invisible regardless of stream and must block (exit 2 + stderr) to be seen at all. Getting
  this backwards is silent by construction: `dev-env-sync.py`'s exit-0 warnings sat on stderr
  for months, hiding a fast-forward-pull failure for 36+ hours and 21+ commits of drift before
  anyone noticed (dev-env#694, [ADR-098](docs/adr/098-dev-env-sync-advisories-to-stdout.md));
  `journal-stop-check.py`'s archive reminder had the mirror-image `Stop`-hook bug
  ([ADR-091](docs/adr/091-journal-stop-check-archive-reminder-blocking.md)). Base contract:
  [ADR-027](docs/adr/027-userpromptsubmit-blocking-hook-conventions.md); invocation model:
  [ADR-007](docs/adr/007-hook-command-invocation.md).
- **The equivalent of "is it observable / correct at its boundaries" is the verification
  suite in `## Testing` above** — the hook-script syntax check, the `pyw -3` stdio test, and
  the pre-push self-test. A change to a hook or script must keep those green.

What the Pass 3 Observability dimension should verify for a dev-env change: any new or changed
hook/script routes its output to the stream its event type and blocking-intent actually deliver
to Claude on the exit code it uses (see the stream-choice bullet above — never assume stderr is
always the safe/diagnostic channel), chooses its exit code deliberately (0 = advisory, non-zero
= blocking), and is covered by the relevant `## Testing` self-test. Pure docs/config changes
(like this one) answer "N/A — no runtime."

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
| `~/.claude/scripts/` | `claude/scripts/` (directory junction) |
| `~/.claude/skills/` | `claude/skills/` (directory junction) |
| `~/.claude/hooks/` | `claude/hooks/` (directory junction) |
| `~/.claude/routines/` | `claude/routines/` (directory junction) |
| `~/.claude/templates/` | `claude/templates/` (directory junction) |

**Machine-local only — never commit:**

`scratch/`, `projects/`, `sessions/`, `backups/`, `ide/`, `plans/`, `shell-snapshots/`

**Machine-local *and* partly repo-owned — `~/.claude/settings.json`:**

`~/.claude/settings.json` is a **real, machine-local file the Claude Code app writes**, not a
symlink into this repo ([ADR-139](docs/adr/139-machine-local-settings-with-shared-source-sync.md)).
It used to be symlinked, which made the app dirty a tracked file and blocked the canonical's
fast-forward *permanently* — serving stale hooks and skills machine-wide, since
`~/.claude/{scripts,skills,hooks}` are junctions into that same checkout (dev-env#1049).

dev-env tracks `claude/settings.shared.json` instead and syncs it **into** the live file.
Which keys belong where:

| Class | Keys | Behavior |
|---|---|---|
| **Owned** (tracked) | `hooks`, `permissions` | Replaced wholesale on every sync — deletions propagate, and the app cannot silently drop an allow rule. |
| **Seed** (tracked) | `model`, `effortLevel` | Written only when absent, so a `/config` change sticks ([ADR-079](docs/adr/079-backup-restore-convention.md) rule 3). |
| **Machine-local** (never tracked) | `theme`, `tui`, `agentPushNotifEnabled`, `inputNeededNotifEnabled`, `skipWorkflowUsageWarning`, `autoMode` | Never read, written, or removed by the sync. `autoMode.environment` describes personal data and must stay out of git. |

**Adding a key to `claude/settings.shared.json` requires classifying it** in `OWNED_KEYS` or
`SEED_KEYS` in [`claude/scripts/_settings_sync.py`](claude/scripts/_settings_sync.py) — an
unclassified key is reported as a warning and *not applied*, rather than silently ignored.

**Machine-local is not the same as correctly scoped.** A key the app writes that carries
**project-specific** content — an auto-mode environment scan, anything naming a particular repo's
paths, visibility, or policies — does not belong in `~/.claude/settings.json` either, even though
that file is now machine-local and out of git: the user-scope file loads for **every** project's
session on this machine. Relocate it to that project's own `.claude/settings.local.json`, the only
per-project personal-settings slot Claude Code has (verify the project's `.gitignore` excludes it
first). This is a global rule, stated in full in
[`claude/CLAUDE.md`](claude/CLAUDE.md) → Platform & Environment.

`dev-env-sync.py` runs the sync every prompt. To apply it by hand after editing the shared file:

```bash
py -3 ~/.claude/scripts/_settings_sync.py
```

**Rule:** Any addition or modification to a dev-env-owned artifact — new hook script, new skill, settings change, CLAUDE.md edit — must be committed to `brownm09/dev-env` via branch and PR before the session ends. Do not leave global tooling as untracked files.

**Rule:** The canonical dev-env worktree (`~/Git/dev-env`) must stay on `main` at all times. All dev-env changes go through a separate worktree (use `EnterWorktree` or `git worktree add`). Reason: `~/.claude/settings.json` and `~/.claude/scripts/` are symlinked/junctioned to the canonical worktree's working tree — checking out a feature branch there makes newly merged hooks and scripts invisible until the worktree returns to main. `dev-env-sync` will warn on every prompt when this rule is violated.

**Routines note:** `~/.claude/routines/` is a read-only junction mirror of `claude/routines/` — it exists so a routine can read its own canonical source at run time, not so the scheduler auto-registers it. The `scheduled-tasks` MCP tool never reads or writes through it: that tool owns a **separate, real, non-linked** directory, `~/.claude/scheduled-tasks/`, with one subdirectory per *registered* task (reusable routines and one-off/manual tasks side by side — deliberately not a version-controlled mirror, since one-offs have no business in git). Authoring or editing `claude/routines/<name>/SKILL.md` and merging it does **not** update a live task. After merging, separately call `create_scheduled_task` / `update_scheduled_task` (`scheduled-tasks` MCP tool) so the live prompt matches. Prefer the self-referencing pattern `weekly-memory-audit` already uses: have the live prompt read its own canonical `claude/routines/<name>/SKILL.md` at run time and follow it when present, falling back to an embedded copy — this keeps the live task self-healing against future drift instead of silently diverging, as `daily-journal-compose` did until [dev-env#464](https://github.com/brownm09/dev-env/issues/464). See [ADR-003 amendment](docs/adr/003-config-in-version-control.md) and [dev-env#344](https://github.com/brownm09/dev-env/issues/344).

**Routine authoring — sync-to-main preamble.** Any routine that reads repo-resident files (a skill, a context file, a queue file) at run time must invoke the `sync-routine-worktree` skill as Step 0, before reading any of those files. Scheduled tasks fire into Claude-managed worktrees whose branches were cut from whatever `main` was at worktree creation; without an explicit sync the routine reads stale files or aborts because a recently-merged file is missing on the worktree branch. The sync skill handles fetch, branch-class-aware sync (Claude-managed worktree / `main` / other), file existence verification, and abort-with-push-notification on conflict — routines pass `REPO`, `VERIFY_FILE`, and `PREFIX` and treat the return as a guard. See `claude/skills/sync-routine-worktree/SKILL.md` and `claude/routines/nightly-cover-letters/SKILL.md` for the canonical pattern. Rationale: `docs/adr/013-sync-routine-worktree-skill.md`.

**Doc-reconciliation checkpoint** (three moments, same as ADR-warrant): (1) immediately after a plan is approved; (2) immediately after `gh pr create` returns; (3) immediately before `gh pr merge`. At each checkpoint, ask: does this change add, remove, rename, or modify the behavior of a skill, hook, script, or routine? If yes, verify that `README.md` and the Documentation Maintenance table above are satisfied in this PR. **If warranted updates are missing, add them before merging.** Rationale: `docs/adr/019-doc-reconciliation-enforcement.md`.

**Downstream artifacts that name specific dev-env skills/hooks/routines** (update in the same PR as a rename or retirement):

- `tech-leadership-reference/ai-adoption/ai-adoption-readiness-framework.md` — Appendix C names `/propose`, `/review`, `/journal-compose`, `/research`, and the `prune-stale-worktrees` and nightly journal compose routines as live-state evidence.

**Repo path:** `C:/Users/brown/Git/dev-env`

## GitHub Project

All new dev-env issues and PRs must be added to the **Dev Env** project and given an Impact rating and Why description before work begins. The general single-select option-mutation hazard that applies to **every** project is documented in the global `claude/CLAUDE.md` → Dev-Env & Project Boards section; the dev-env-specific IDs and procedures are below.

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

**Workflow — automated via PostToolUse hook:** After `gh issue create` or `gh pr create` succeeds, `post-tool-use.py` adds the issue or PR to project #3 and exits code 2, printing the exact `gh project item-edit` commands to set Impact and Why. **Run those commands immediately — before any file edits.** The hook treats both identically ([ADR-023](docs/adr/023-generic-required-fields-issue-hook.md)).

**Fallback (if the hook did not fire or the item-add failed):** run the three steps manually (substitute the PR URL for the issue URL when the item is a PR). Requires project scope — add once if needed: `gh auth refresh -s project`. A *wholesale* non-fire — no `[project-hook]` output at all after `gh issue create` or `gh pr create`, and `spawn_task` chips also not rendering — most often means the session was launched as a background task / via `spawn_task`, where **every** PostToolUse hook is silently inert ([ADR-053](docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md)); these manual steps are the recovery.

```bash
# 1. Add issue/PR to project, capture item ID
URL="<issue-or-pr-url>"
TMPFILE="C:/Users/brown/.claude/scratch/tmp_item_$$.json"
gh project item-add 3 --owner brownm09 --url "$URL" --format json > "$TMPFILE"
ITEM_ID=$(node -e "const d=JSON.parse(require('fs').readFileSync('$TMPFILE','utf8')); console.log(d.id);")
rm -f "$TMPFILE"

# 2. Set Impact   (08de2558=High  6320e8a6=Medium  d8a85c2f=Low)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkNc --single-select-option-id <option-id>

# 3. Set Why (one sentence — the cost of not fixing it)
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTF_lAHOAjEKvM4BWKFezhRgkN0 --text "<why this matters>"

# 4. Cache the item ID (dev-env#1057, ADR-141) so a later lookup (e.g. to move
#    Status) is a zero-cost hit instead of another full-board fetch. The regex and
#    key format must stay byte-identical to _gh_project.py's _ISSUE_URL_RE /
#    _cache_key (project-owner/project-number then repo#number, both lower-cased) —
#    a drifted copy here would silently never hit what that module writes.
GPI_URL="$URL" GPI_ITEM_ID="$ITEM_ID" node -e "
  const fs = require('fs'), path = require('path');
  const m = process.env.GPI_URL.match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/(?:issues|pull)\/(\d+)\/?\$/);
  if (!m) process.exit(0);
  const [, owner, repo, number] = m;
  const cacheFile = 'C:/Users/brown/.claude/scratch/project-item-cache.json';
  let cache = {};
  try { cache = JSON.parse(fs.readFileSync(cacheFile, 'utf8')); } catch (e) {}
  cache['brownm09/3|' + (owner + '/' + repo).toLowerCase() + '#' + number] = process.env.GPI_ITEM_ID;
  fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
  const tmp = cacheFile + '.' + process.pid + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cache, null, 2), 'utf8');
  fs.renameSync(tmp, cacheFile);
"
```

**GraphQL-only, no REST fallback.** Every command in this section — `gh project item-add`,
`item-edit`, `item-list` — is GraphQL-only; GitHub Projects v2 has no REST API surface at all
(Projects *classic* did; v2 does not). This is unlike `gh pr merge`/`gh pr comment`/`gh pr view
--json`, which all have documented REST equivalents (see [docs/REFERENCE.md → Git Workflow
Runbooks](docs/REFERENCE.md#git-workflow-runbooks)) for when the GraphQL bucket is exhausted. When
GraphQL hits 0/5000, every project-board operation above — adding an issue/PR, setting
Impact/Why/Status, looking up an item ID — is completely blocked with **no workaround** except
waiting for the bucket to reset (`gh api rate_limit --jq .resources.graphql.reset`, up to ~1 hour).
If this happens mid-session, note the board update as an outstanding manual step and defer it
rather than hunting for a REST substitute that doesn't exist. See
[dev-env#769](https://github.com/brownm09/dev-env/issues/769).

To look up an item ID by issue or PR number `<N>` (e.g., to move status in a later session), use
`get-project-item.sh` rather than querying the board directly — it checks a local cache first
(dev-env#1057, [ADR-141](docs/adr/141-project-item-id-creation-time-cache.md)) and costs **zero**
`gh` calls on a hit (populated automatically at creation time and backfilled wholesale by
`reconcile-project-board.py`'s sweeps), falling back to the full `--limit 1000` board fetch only
on a genuine cache miss — never the unconditional full fetch this section used to document inline:

```bash
ITEM_ID=$(bash ~/.claude/scripts/get-project-item.sh <N>)
```

**Move status** — set the Status field (`PVTSSF_lAHOAjEKvM4BWKFezhRgkMY`) to In Progress (`47fc9ee4`) when work begins, Done (`98236657`) after the PR merges:

```bash
gh project item-edit --project-id PVT_kwHOAjEKvM4BWKFe --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAjEKvM4BWKFezhRgkMY --single-select-option-id <status-option-id>
```
