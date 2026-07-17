# Test Suite Index — `claude/scripts/tests/`

This directory holds the dev-env hook/script test suite: 65 `test_*.py` files, 9 bash gates, and
one shared test-support module (`_hook_wiring.py`) — 75 files with no per-file index until now
([dev-env#822](https://github.com/brownm09/dev-env/issues/822)).

**Not the only test directory.** `claude/hooks/tests/` is a second, much smaller test directory
that `run-hook-tests.py` (below) also discovers and runs — e.g. `test-pre-push-lockfile.sh` lives
there, not here. It's out of scope for this index (tracked separately in
[dev-env#314](https://github.com/brownm09/dev-env/issues/314)); if a full suite run shows a file
you can't find a row for below, that's why.

**This file is a navigational map, not the authoritative behavioral description.** For exhaustive
per-file detail — what each test pins, what's deliberately out of scope, incident history — see
the root [`CLAUDE.md`](../../../CLAUDE.md) → `## Testing` section (numbered items, one per
file or file pair) and the linked ADRs in [`docs/adr/INDEX.md`](../../../docs/adr/INDEX.md).
[`docs/REFERENCE.md`'s "Script verification suite"](../../../docs/REFERENCE.md#script-verification-suite)
table covers a curated ~9-file subset with invocation strings and longer descriptions; this file
covers all 75, one line each.

**Running tests.** Every `test_*.py` runs as `py -3 claude/scripts/tests/<file>` from the repo
root; every `*.sh` runs as `bash claude/scripts/tests/<file>`. To run everything at once:
`py -3 claude/scripts/run-hook-tests.py` (add `--list` to see what would run without running it).
Discovery is glob-based (`test_*.py` / `*.sh`, `_`-prefixed files excluded) — a new test file is
picked up automatically by the runner. **This README is not.** Add a row below in the same PR
that adds a test file.

---

## Shared support modules

Tests for internal `_foo.py` libraries that live in `claude/scripts/` and get imported by
multiple hooks, rather than being wired as a hook themselves. `_hook_wiring.py` is the one
exception: it lives in *this* directory because it's test-support infrastructure, not
production code.

| Test file | Covers | Purpose |
|---|---|---|
| `test_bash_state.py` | `_bash_state.py` | Per-session repo/branch state file plus the drift-warning formatter shared by the four commit/PR-create/merge/every-Bash-call checkpoint hooks. |
| `test_hookio.py` | `_hookio.py` | Shared PostToolUse command-output reader, merge-marker detection, and the `scan_top_level` command-shape parser five PostToolUse hooks import. |
| `test_hookout.py` | `_hookout.py` | The shared hook advisory/block emitter — one encoding of the stdout/stderr/exit-code channel table every hook should route through. |
| `test_hookutil.py` | `_hookutil.py` | Sentinel-file, transcript-locate, transcript-record-reader, and heartbeat-recording helpers shared across Stop/PostToolUse hooks. |
| `test_journal_compose_force.py` | `_journal_compose_force.py` | Marker read/write/freshness helpers behind the journal-compose today-guard's mechanical `--force` enforcement. |
| `test_journal_schema.py` | `_journal_schema.py` | Shared manifest/open-PR shard schema and validation helpers, used by the write-time advisory hook and the pre-compose validator. |
| `test_journal_shards.py` | `_journal_shards.py` | Shared open-PR shard + legacy `open-prs.jsonl` reader used by `reconcile-open-prs.py` and `post-compact.py`. |
| `test_repo_scan.py` | `_repo_scan.py` | Shared `find_git_repos()` directory-scan helper used by every `--scan-dir` mode across the worktree/board scripts. |
| `test_repo_target.py` | `_repo_target.py` | Shared `--repo`/PR-URL/issue-URL/positional-number resolver — ends the per-hook ADR-050 amendment treadmill for this concern. |
| `test_winsubp.py`, `test_pyw_stdio.py` | `_winsubp.py` | Windows subprocess defaults (`CREATE_NO_WINDOW`, forced UTF-8 text mode) every subprocess-spawning hook applies; the latter probes real `pyw -3` stdio behavior end-to-end. |
| `test_worktree_canon.py` | `_worktree_canon.py` | Shared worktree-path-to-canonical-root regex/resolution, used by the project-board hook pair and the two PreToolUse worktree guards. |
| `test_worktree_liveness.py` | `_worktree_liveness.py` | Active-session liveness check that stops prune/reclaim routines from severing a worktree with a live Claude Code session in it. |
| `test_worktree_topology.py` | `_worktree_topology.py` | Worktree-on-`main` squat detection/diagnosis and park-target decisions shared by prune, sync, and the journal-canonical guard. |
| — | `_hook_wiring.py` | Not itself a test. Parses `claude/settings.json` into wired hooks/events/timeouts once, shared by the four structural gates below. |

## Structural / fleet-wide gates

Tests that reason about every wired hook at once — settings wiring, output-stream contracts,
exit-code safety, heartbeat compliance — rather than one script's behavior.

| Test file | Covers | Purpose |
|---|---|---|
| `test_hook_heartbeat_guard.py` | every wired hook | Structural gate: every wired hook calls `_hookutil.record_heartbeat("<own-name>")` as the first statement of `main()`. |
| `test_hook_output_contract.py` | every wired hook | AST gate: no hook emits to a stream/exit-code combination Claude Code actually discards (stderr-on-exit-0, stdout-on-exit-2, `additionalContext` on a non-context event). |
| `test_hook_safe_exit_guard.py` | every wired hook | Structural gate: every wired hook has a top-level `try/except` in `__main__` that deterministically exits in its declared fail direction (open vs. closed). |
| `test_no_crude_command_substring_checks.py` | all `claude/scripts/*.py` | AST gate against `if "<cli>" not in command`-shaped string checks that false-match inside heredocs/quotes/subshells — the recurring false-positive class behind many ADR-050 amendments. |
| `test_run_hook_tests.py` | `run-hook-tests.py` | Tests the pure discovery/classification helpers behind the test-suite runner itself — the engine behind `py -3 claude/scripts/run-hook-tests.py` and the `hook-tests` CI workflow. |
| `test_settings_hook_wiring.py` | `claude/settings.json` | Lint: every `(event, matcher, hook)` entry resolves to a real script and carries a timeout at or above its budget floor. |
| `check-script-path-hygiene.sh` | all `claude/scripts/*.sh`, `claude/hooks/*` | Lints for a `$HOME`-rooted scratch path piped to `node` (Git Bash vs. Node-on-Windows path-resolution mismatch, dev-env#334). |
| `run-pylint-unreachable.sh` | all `claude/scripts/*.py` and `claude/scripts/tests/*.py` | Runs pylint's `unreachable` (W0101) check alone — dead-code-after-return/raise, independent of type annotations. |
| `run-shellcheck.sh` | all repo shell scripts | Shellcheck at `--severity=error`, blocking; self-skips if shellcheck isn't installed locally. |

## Tests for individual hooks & scripts

### Engineering journal & stub workflow

| Test file | Covers | Purpose |
|---|---|---|
| `test_check_journal_compose_liveness.py` | `check-journal-compose-liveness.py` | Detects an in-flight session still writing stubs for the date `/journal-compose` is about to merge. |
| `test_journal_canonical_guard.py` | `journal-canonical-guard.py` | Detects/auto-corrects the engineering-journal canonical checkout being left on a hijacked branch. |
| `test_journal_compose_force_resolve.py` | `journal-compose-force-resolve.py` | CLI glue that writes the today-guard's `--force` marker to disk. |
| `test_journal_draft_worktree_guard.py` | `pre-tool-use-journal-draft-worktree-guard.py` | Blocks isolating the `draft/YYYY-MM-DD` stub workflow into its own worktree — it must stay on the shared canonical. |
| `test_journal_onboard_check.py` | `journal-onboard-check.py` | One-time per-session onboarding nudge for a project with no journal home yet. |
| `test_journal_shard_write_advisory.py` | `journal-shard-write-advisory.py` | Write-time PostToolUse validation of manifest/open-PR shards against the schema (BOMs, missing fields) as they're written. |
| `test_journal_stop_check.py` | `journal-stop-check.py` | Stop-hook archive reminder plus stale-draft/unmerged-branch advisories at session end. |
| `test_post_compact.py` | `post-compact.py` | Reads and unions open-PR shards + legacy `open-prs.jsonl` at PostCompact time. |
| `test_pre_tool_use_journal_compose_force_guard.py` | `pre-tool-use-journal-compose-force-guard.py` | Mechanically blocks a same-day `/journal-compose` git operation unless a fresh `--force` marker exists. |
| `test_reconcile_open_prs.py` | `reconcile-open-prs.py` | Reconciles open-PR shards against live GitHub state (removes shards for merged/closed PRs). |
| `test_stop_journal_stub_checkpoint.py` | `stop-journal-stub-checkpoint.py` | Stop-hook checkpoint: a report/analysis/verification session that did substantive work must leave a stub. |
| `test_stub_push_archive_reminder.py` | `stub-push-archive-reminder.py` | Push-error guard plus unresolved-open-PR detection behind the "archive the branch" reminder after a journal push. |
| `test_validate_manifest.py` | `validate-manifest.py` | Pre-compose validator: every manifest shard/legacy entry has all five required fields. |

### PR / merge workflow

| Test file | Covers | Purpose |
|---|---|---|
| `test_canonical_mutate_guard.py` | `pre-tool-use-canonical-mutate-guard.py` | Blocks git-mutating Bash/PowerShell commands run against a canonical (non-worktree) checkout. |
| `test_post_merge_tile_checkpoint.py` | `post-merge-tile-checkpoint.py` | Reminds to enumerate follow-up tiles immediately after a merge. |
| `test_post_pr_merge_project.py` | `post-pr-merge-project.py` | Moves the merged PR's project-board item to Done. |
| `test_post_pr_merge_pull.py` | `post-pr-merge-pull.py` | Fast-forwards local `main` (or parks a squatting worktree) after a merge. |
| `test_post_pr_merge_reclaim.py` | `post-pr-merge-reclaim.py` | Triggers disk reclamation after a successful merge. |
| `test_pr_merge_reminder.py` | `pr-merge-reminder.py` | Journal-stub reminder after a `gh pr create`/`merge`/`push`, scoped to the actual target repo. |
| `test_pre_auto_merge_checkpoint_gate.py`, `test-auto-merge-checkpoint-gate.sh` | `pre-auto-merge-checkpoint-gate.py` | Fail-closed gate on `gh pr merge --auto`: requires a fresh PR comment with both a clean review-findings marker and a complete premerge-checkpoints marker. |
| `test_pre_commit_branch_check.py` | `pre-commit-branch-check.py` | Shows the current branch plus a repo/branch drift warning as a visible checkpoint before commit. |
| `test_pre_merge_branch_check.py` | `pre-merge-branch-check.py` | Shows the current branch plus a drift warning as a visible checkpoint before merge. |
| `test_pre_merge_findings_gate.py`, `test-merge-findings-gate.sh` | `pre-merge-findings-gate.py` | Blocks `gh pr merge` unless open `/review` findings have a recorded disposition. |
| `test_pre_merge_message_check.py` | `pre-merge-message-check.py` | Surfaces any queued user message before a merge proceeds. |
| `test_pre_merge_numbering_check.py` | `pre-merge-numbering-check.py` | Blocks a merge that would collide CLAUDE.md Testing-item numbers or ADR numbers with `origin/main`. |
| `test_pre_pr_create_check.py` | `pre-pr-create-check.py` | Pre-PR checklist (testing, suppression, baseline, doc-reconciliation) plus branch/drift display. |
| `test_stop_tile_enumeration_gate.py` | `stop-tile-enumeration-gate.py` | Stop-hook gate: blocks ending a session with a merged PR, a dangling issue, or an untabled tile spawn unless the follow-up enumeration ran. |

### Worktrees & disk management

| Test file | Covers | Purpose |
|---|---|---|
| `test_disk_space_check.py` | `disk-space-check.py` | Free-space classifier (ok/warn/act) wired to both `UserPromptSubmit` and `PreToolUse(Bash)`. |
| `test_prune_merged_worktrees.py` | `prune-merged-worktrees.py` | Removes worktrees whose branch is merged (or, opt-in, whose diff is all-ephemeral) and not live. |
| `test_reclaim_worktree_disk.py` | `reclaim-worktree-disk.py` | Deletes `node_modules`/`.turbo` from idle, merged, or dirty-excluded worktrees to reclaim disk. |
| `test_sweep_scratch_debris.py` | `sweep-scratch-debris.py` | On-demand sweep of accumulated per-session marker/sentinel files under `scratch/`. |
| `test_worktree_npm_install.py` | `worktree-npm-install.py` | Pre-install free-space gate for a new worktree's `npm install` (refuses rather than silently truncating on low disk). |
| `test_worktree_path_check.py` | `pre-tool-use-worktree-path-check.py` | Blocks a Write/Edit from an orphaned worktree, or one whose absolute path escapes to the canonical root. |

### GitHub project board

| Test file | Covers | Purpose |
|---|---|---|
| `test_post_tool_use.py` | `post-tool-use.py` | Adds a newly created issue/PR to the project board and prints the field-set commands. |
| `test_reconcile_project_board.py` | `reconcile-project-board.py` | Finds open issues missing from the board and open board items missing a required field. |

### Session state, reliability & token tracking

| Test file | Covers | Purpose |
|---|---|---|
| `test_dev_env_sync.py` | `dev-env-sync.py` | Fast-forwards the canonical checkout's `main` on every prompt; escalates a persistent pull failure. |
| `test_hook_liveness_check.py` | `hook-liveness-check.py` | Warns when a wired hook's heartbeat ledger has gone stale (hasn't recorded in its expected cadence). |
| `test_idle_refresher.py` | `idle-refresher.py` | Injects a "here's what we were doing" cue after a long idle gap between prompts. |
| `test_memory_write_advisory.py` | `memory-write-advisory.py` | Flags a durable memory write that has no paired immortalization issue. |
| `test_posttooluse_inert_advisory.py` | `posttooluse-inert-advisory.py` | Stop-hook safety net: detects a background/SDK-launched session where PostToolUse hooks never fired at all. |
| `test_pre_bash_drift_check.py` | `pre-bash-drift-check.py` | Elapsed-time-gated repo/branch drift check on every Bash call, not just the three merge-adjacent checkpoints. |
| `test_session_mode_prompt.py` | `session-mode-prompt.py` | One-time per-session reminder of the active permission mode. |
| `test_session_mode_report.py` | `session-mode-report.py` | On-demand report (not a hook) auditing which sessions started outside `plan` mode. |
| `test_token_tracker.py` | `token-tracker.py` | Per-session token/cost aggregation and the once-per-session locate-failure advisory. |
| `test_usage_snapshot.py` | `usage-snapshot.py` | OAuth-token expiry classification plus the merge-confirmation predicate for the post-tool-use usage snapshot. |

### Utility & setup scripts

Scripts invoked directly (by a person or a skill), not wired as Claude Code hooks.

| Test file | Covers | Purpose |
|---|---|---|
| `test-baseline-tests-gc.sh` | `baseline-tests.sh` | Branch-existence-based cleanup (`gc`) of stale pre-existing-test-failure baseline snapshots. |
| `test-get-project-item.sh` | `get-project-item.sh` | Execution smoke test (not just `bash -n`) resolving an issue number to a project item ID. |
| `test-merge-stale-pr.sh` | `merge-stale-pr.sh` | Drives the stale-journal-PR remediation script against throwaway fixture repos with `gh` stubbed. |
| `test-setup-link-loop.sh` | `setup.sh` | Drives the extracted `link_claude_windows`/`link_claude_unix` functions against a throwaway `$HOME`. |

---

## Adding a new test file

1. Name it `test_*.py` (pure Python, offline-testable where possible) or `*.sh` (a bash gate or a
   behavioral driver against a real hook via stdin/subprocess) and drop it in this directory —
   `run-hook-tests.py` discovers it automatically, no wiring needed.
2. Add a one-line module docstring (Python) or header comment (bash) stating what it covers —
   several rows above were written straight from these.
3. Add a row to the relevant table above (or a new subsection, if it's a genuinely new category).
4. If the change is significant enough to need incident/ADR context beyond one line, that detail
   belongs in the root `CLAUDE.md` → `## Testing` section, not here.
