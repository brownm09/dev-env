# Scripts Index — `claude/scripts/`

This directory holds dev-env's hook scripts, shared library modules, and on-demand utility
scripts: **95 files** at the top level (wired Claude Code hooks, shared `_foo.py` modules, and
utility/setup scripts across `.py`/`.sh`/`.ps1`), indexed per file below
([dev-env#830](https://github.com/brownm09/dev-env/issues/830)). The top-level file count and the
per-section `(N)` counts below are gated by `tests/test_readme_index_parity.py` (dev-env#901).

**Not `claude/scripts/tests/`.** That subdirectory has its own index —
[`claude/scripts/tests/README.md`](tests/README.md)
([dev-env#822](https://github.com/brownm09/dev-env/issues/822)) — covering the test files there
(`test_*.py`, bash gates, and the shared `_hook_wiring.py`). This README covers only the files
directly in `claude/scripts/`; `__pycache__/` is a gitignored build artifact, not content.

**This file is a navigational map, not the authoritative behavioral description.** For exhaustive
trigger conditions, decision tables, and incident history, see
[`docs/REFERENCE.md`](../../docs/REFERENCE.md) → **Hooks** and **Utilities** sections, and the
root [`README.md`](../../README.md) → Hooks table for the canonical `Event | Script | Purpose`
list. The linked ADRs in [`docs/adr/INDEX.md`](../../docs/adr/INDEX.md) carry the design
rationale. Where this file's one-liner and `docs/REFERENCE.md`'s fuller description ever
disagree, `docs/REFERENCE.md` wins.

**Running things.** Every `.py` file runs as `py -3 claude/scripts/<file>.py [args]` from the
repo root (hooks are invoked by Claude Code itself via `pyw -3`, per
[ADR-007](../../docs/adr/007-hook-command-invocation.md) — don't invoke them that way by hand).
Every `.sh` file runs as `bash claude/scripts/<file>.sh [args]`. To run the whole test suite that
exercises these scripts: `py -3 claude/scripts/run-hook-tests.py`.

---

## Shared support modules

`_foo.py` files — imported by other scripts, never directly invoked or wired as a hook
themselves. None of these get their own row in `docs/REFERENCE.md`'s Hooks/Utilities tables
(they're described in prose paragraphs at the top of the Hooks section instead); this table is
the one place all 19 are listed together. Each has its own test file in
[`tests/README.md`](tests/README.md) → Shared support modules.

| Module | Purpose | Consumers |
|---|---|---|
| `_bash_state.py` | Per-session repo/branch state file, plus the shared drift-warning formatter and elapsed-time gate. | `post-tool-use-cwd-track.py` (writer); `pre-commit-branch-check.py`, `pre-pr-create-check.py`, `pre-merge-branch-check.py`, `pre-bash-drift-check.py` (readers) |
| `_composed_output_scan.py` | Fence- and code-span-aware scanner for stray terminal output in composed journal markdown (`scan_text`, `find_signature_hits`, `strip_code_spans`). | `validate-composed-output.py` |
| `_gh_issue_state.py` | Shared GitHub issue/PR REST state helpers (`repo_from_issue_url`, `issue_number_from_url`, `issue_states_from_rows`, `should_stop_paging`, `is_closed`, `check_issue_state`) extracted from `reconcile-pending-tiles.py`: strict URL validation, PR-vs-issue row filtering, and `state` case normalization. | `reconcile-pending-tiles.py`, `retro-chain-status.py` |
| `_gh_project.py` | `gh project item-add` subprocess wrapper (`add_to_project`), UTF-8-safe. | `post-tool-use.py`, `reconcile-project-board.py` |
| `_hookio.py` | PostToolUse command-output reader (`read_command_output`), merge-marker detection (incl. the two-step REST merge fallback, `is_rest_merge_command`/`output_has_rest_merge_marker`), the `scan_top_level`/`is_help_only` command-shape parsers. | 5 PostToolUse hooks + `pr-merge-reminder.py` + `stop-tile-enumeration-gate.py` |
| `_hookout.py` | Shared advisory/block emitter — one encoding of the stdout/stderr/exit-code channel table every hook should route through (`emit_advisory`, `emit_block`, `ascii_sanitize`). | `journal-stop-check.py`, `posttooluse-inert-advisory.py`, `token-tracker.py`, `dev-env-sync.py`, others mid-migration |
| `_hookutil.py` | Sentinel-file helpers, transcript-locate, transcript-record readers, and `record_heartbeat()` (called first-thing by every wired hook's `main()`). | Most Stop/UserPromptSubmit hooks |
| `_journal_canon.py` | Shared engineering-journal canonical-path resolution (`resolve_journal_path`, `normalize_journal_path`) — env-override-or-default plus one canonical comparison normalization, single-sourcing what four hooks each independently duplicated. | `pre-tool-use-canonical-mutate-guard.py`, `journal-canonical-guard.py`, `pre-tool-use-journal-draft-worktree-guard.py`, `pre-tool-use-worktree-path-check.py` |
| `_journal_compose_force.py` | Marker read/write/freshness helpers behind `/journal-compose`'s mechanical `--force` enforcement. | `journal-compose-force-resolve.py` (writer), `pre-tool-use-journal-compose-force-guard.py` (reader) |
| `_journal_schema.py` | Shared manifest/open-PR/tile shard schema + validation. Presence checks (`missing_required_fields`, `missing_open_pr_fields`, `missing_tile_fields`) and *shape* checks for a present-but-wrong value (`malformed_manifest_fields` — `tokens`; `malformed_tile_fields` — `cwd`/`stub`/`task_id`), plus `parse_manifest_text` / `decode_shard_bytes`. | `validate-manifest.py`, `journal-shard-write-advisory.py`, `stub-push-archive-reminder.py` |
| `_journal_shards.py` | Shared numeric-shard + legacy `open-prs.jsonl` reader (`iter_numeric_shards`, with `iter_pr_shards` / `iter_tile_shards` as named delegations; `read_legacy_entries`), plus `project_dirs` — the `sessions/<project>/` walk both reconcile hooks run before reading either shard kind. | `reconcile-open-prs.py`, `reconcile-pending-tiles.py`, `post-compact.py` |
| `_repo_scan.py` | Shared `find_git_repos()` directory-scan helper for every `--scan-dir` mode. | `prune-merged-worktrees.py`, `reclaim-worktree-disk.py`, `reconcile-project-board.py` |
| `_repo_target.py` | Shared `--repo` (incl. host-prefixed/URL forms) / PR-URL / issue-URL / positional-number / REST-merge-path resolver for `gh` commands — ends the per-hook ADR-050 amendment treadmill. | `post-pr-merge-project.py`, `pr-merge-reminder.py`, `posttooluse-inert-advisory.py`, `post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`, `post-tool-use.py` |
| `_shell_write_detect.py` | Shared shell-syntax write-detection primitives (`mask_first_line_quotes` with its load-bearing `<<` neutralization, `neutralize_unquoted_escaped_quotes`, `find_redirect_targets`, `next_token`, `first_line`, `segments_or_whole`, `tokenize_posix`, `tokenize_powershell`). Extracted from `pre-tool-use-journal-shell-write-guard.py` as a pure move so the two content-write guards share one copy of quote-state logic ADR-129 Amendment 1 already had to fix twice. Answers only "what does this command's shell syntax say", never "is that a problem". | `pre-tool-use-journal-shell-write-guard.py`, `pre-tool-use-shell-content-write-guard.py` |
| `_skill_file_size.py` | Shared `SKILL.md` basename match (`is_skill_md`) and `.claude/hook-config.json` loader (`load_config` — returns `(warn_bytes, limit_bytes)`, one shared fallback contract for both fields). | `pre-tool-use-skill-file-size-guard.py`, `skill-file-size-advisory.py` |
| `_winsubp.py` | Windows subprocess defaults (`CREATE_NO_WINDOW`, forced UTF-8 text mode) every subprocess-spawning script must `import`. | ~20 subprocess-using scripts |
| `_worktree_canon.py` | Shared worktree-path-to-canonical-root regex/resolution (`canonical_root_from_worktree`, `is_worktree_path`). | `post-tool-use.py`, `reconcile-project-board.py`, `pre-tool-use-canonical-mutate-guard.py`, `pre-tool-use-worktree-path-check.py`, `usage-snapshot.py` |
| `_worktree_liveness.py` | Active-session liveness check — stops prune/reclaim from severing a worktree with a live Claude session in it. Also exposes an `exclude_session_id` param so a hook running *as* one of the candidate sessions can ask "is some other session here" instead of always matching itself (ADR-130). | `prune-merged-worktrees.py`, `reclaim-worktree-disk.py`, `session-start-sync.py` |
| `_worktree_recovery.py` | Single-sourced orphaned-worktree recovery recipe (`RECOVERY_STEPS`, `recovery_recipe`) — the block message a stuck session reads, pinned against the `docs/REFERENCE.md` runbook so the two can't drift ([ADR-116](../../docs/adr/116-single-source-worktree-recovery-recipe.md)). | `pre-tool-use-worktree-path-check.py` |
| `_worktree_topology.py` | Worktree-on-`main` squat detection/diagnosis and park-target decisions. | `prune-merged-worktrees.py`, `post-pr-merge-pull.py`, `dev-env-sync.py`, `journal-canonical-guard.py` |

---

## Wired hooks & their domain utilities

The 45 scripts Claude Code invokes automatically via `claude/settings.json`, grouped with the
utility scripts that serve the same workflow area — mirroring
[`tests/README.md`](tests/README.md)'s domain grouping so the two indexes read the same way.
**Event** names the hook registration(s) from `settings.json`; utility scripts show their
invocation instead.

### Engineering journal & stub workflow (23)

| Script | Event / Invocation | Purpose |
|---|---|---|
| `journal-canonical-guard.py` | UserPromptSubmit | Corrects the engineering-journal canonical checkout when it's hijacked onto a detached HEAD or a stray `claude/*` branch; leaves legitimate branches (e.g. `draft/YYYY-MM-DD`) alone. |
| `new-day-journal-check.py` | UserPromptSubmit | Warns about stale `draft/*` branches on `origin/engineering-journal`, and about a day rollover with the canonical still on an older draft branch (ADR-119); when that stale branch is also fully clean and HEAD hasn't moved in ≥15min, auto-restores the canonical to `main` (ADR-119 Amendment 1, dev-env#911 — the one check here that mutates rather than only advises). Checks 4 and 5 are the only ones that also run in worktree sessions. |
| `journal-onboard-check.py` | UserPromptSubmit | Warns once per session when the active project has no `sessions/<project>/` home in engineering-journal yet. |
| `reconcile-open-prs.py` | UserPromptSubmit | Removes open-PR shards/legacy entries for PRs now merged/closed; surfaces survivors + any uncommitted shard changes, classified into confirmed-merged deletions / in-flight / unverified / skipped (ADR-119), as session context. One REST read per PR (`repos/<o>/<r>/pulls/<n>`) on the `core` bucket so a GraphQL outage does not stop all pruning — REST-only, superseding ADR-119's fallback; deliberately *unbatched*, unlike the tile reconciler below, because a lingering shard is exactly what this hook prunes and a bounded paged walk misses the oldest one (dev-env#888). MERGED is recovered from the separate `merged`/`merged_at` signal, since REST has no MERGED state. A shared `WorkBudget` caps total lookup time, and survivors it could not confirm are reported as an explicit unresolved count. |
| `reconcile-pending-tiles.py` | UserPromptSubmit | Removes tile shards whose paired issue is closed; surfaces the surviving pending tiles as a compact index so a chip lost to an app restart can be re-spawned. Validates each shard's `url` before it reaches `gh`; one paged REST read per repo, not per shard, on the `core` bucket so a GraphQL outage does not stop all pruning (dev-env#882). Also surfaces, and once confirmed recommends committing, any already-deleted tile shard sitting uncommitted in the canonical (dev-env#958). |
| `stub-push-archive-reminder.py` | PostToolUse (Bash/PowerShell) | After a clean journal stub push with no unresolved open PR, writes the sentinel `journal-stop-check.py` consumes to prompt archiving. |
| `journal-shard-write-advisory.py` | PostToolUse (Write/Edit/Bash) | Validates a manifest, open-PR, or tile shard's on-disk bytes against the schema right after it's written (missing fields, BOMs, filename mismatches, and — for a tile's `cwd` — a path mangled into naming no directory). |
| `journal-stop-check.py` | Stop | Blocks the stop (exit 2) on the stub-push sentinel so Claude actually archives the session; also non-blocking stale-draft/unmerged-branch advisories. |
| `stop-journal-stub-checkpoint.py` | Stop | Blocks the stop when a report/analysis/verification session did substantive work but leaves no journal stub, no PR, and isn't a `/review` session. |
| `pre-tool-use-journal-compose-force-guard.py` | PreToolUse (Bash) | Mechanically blocks a same-day `/journal-compose` git operation unless a fresh `--force` marker already exists. |
| `pre-tool-use-journal-draft-worktree-guard.py` | PreToolUse (Bash) | Blocks isolating the shared `draft/YYYY-MM-DD` branch into its own worktree anywhere except the engineering-journal canonical. |
| `pre-tool-use-journal-shell-write-guard.py` | PreToolUse (Bash/PowerShell) | Blocks a shell-based content-write (redirect, heredoc, or a PowerShell write cmdlet) targeting a stub/manifest/open-PR/tile path shape — use the Write/Edit tool instead. |
| `post-compact.py` | PostCompact | Emits the compaction status line; on manual `/compact`, also reminds Claude to `/review` each open PR from the project's open-PR records, and lists any pending tile shards (read-only — never prunes). |
| `check-journal-compose-liveness.py` | `git status --porcelain \| py -3 check-journal-compose-liveness.py YYYY-MM-DD` | Detects an in-flight session still writing stubs for the date `/journal-compose` is about to merge. |
| `journal-compose-force-resolve.py` | `py -3 journal-compose-force-resolve.py "$ARGUMENTS"` (first Bash action of `/journal-compose` Step 0.6) | Mechanically resolves `--force` from the literal, harness-substituted invocation text and records it to today's marker file. |
| `journal-compose-with-retry.sh` | Windows Task Scheduler (replaces the nightly routine) | Retries `/journal-compose <yesterday>` up to 3 times on failure, 5 minutes apart. |
| `journal-compose-replay.sh` | `bash journal-compose-replay.sh <WT> <PREV> <pathspec>...` (`/journal-compose` Step 10.5 conflict recovery) | Replays the draft branch's adds/modifies/deletes onto the clean compose branch, partitioned by whether `origin/main` also changed each path — contested paths are 3-way merged or stopped on, never overwritten. |
| `reconcile-late-stubs.py` | `py -3 reconcile-late-stubs.py <draft/YYYY-MM-DD>` | Moves stubs pushed to an already-merged draft branch onto the earliest unmerged (or today's) branch, then deletes the stale source branch. |
| `validate-manifest.py` | `py -3 validate-manifest.py <manifest-path> ...` | Pre-compose validator (`/journal-compose` Step 0.7): every manifest entry has all five required fields. |
| `validate-composed-output.py` | `py -3 validate-composed-output.py <markdown-path> ...` | Pre-commit scanner (`/journal-compose` Step 8b): composed files contain no stray terminal output. Fence- and code-span-aware; advisory (reports, never edits). [ADR-121](../../docs/adr/121-composed-output-stray-terminal-scan.md) |
| `journal-project-repo-map.py` | `py -3 journal-project-repo-map.py <engineering-journal-root> [--json <outfile>]` | Resolves each `sessions/<project>/` directory to a GitHub `owner/repo` slug for `/journal-compose` Step 8a Source 3. Reads the root README's canonical `**Repo:**`+`**Journal:**` pairing, falling back to the project README's `Repo:`/`Repository:` line; shape-validates every slug before it reaches a `gh` command line, and names every project it could not resolve (`SOURCE3_SKIP`) so a mapping drift is loud rather than an empty dashboard. [ADR-032 Amendment 1](../../docs/adr/032-journal-start-here-dashboard.md) |
| `merge-stale-pr.sh` | `bash merge-stale-pr.sh <PR-URL>` | Remediates a stale engineering-journal draft PR: checks out, warns on a missing journal file, deletes orphaned drafts, rebases, squash-merges. |
| `retro-chain-status.py` | `py -3 retro-chain-status.py --repo <owner/repo> [--repo ...] [--journal-repo PATH]` | Read-only per-repo classifier for the retro-action chained-tile backlog (dev-env#967): reports ALIVE/UNRESOLVED/NO_QUEUE_FOUND/QUEUE_EXHAUSTED/ALL_TILED/AMBIGUOUS/NEEDS_REFILL; never mutates anything, a per-repo failure lands in that repo's own `{"status": "ERROR", "error": "..."}` entry. Engine behind the `retro-chain-refill` skill. |

### PR / merge workflow (19)

| Script | Event / Invocation | Purpose |
|---|---|---|
| `pre-commit-branch-check.py` | PreToolUse (Bash), `git commit` | Shows the current branch as a checkpoint before commit, plus a drift warning against the last-recorded Bash state. |
| `pre-pr-create-check.py` | PreToolUse (Bash), `gh pr create` | Emits the test/doc-reconciliation/baseline checklist plus branch + drift warning before PR creation. |
| `pre-merge-message-check.py` | PreToolUse (Bash), `gh pr merge` | Blocks the merge if `merge-queue.md` has queued user feedback to act on first. |
| `pre-merge-branch-check.py` | PreToolUse (Bash), `gh pr merge` | Shows the current branch/repo as a checkpoint before merge, plus a drift warning. |
| `pre-merge-findings-gate.py` | PreToolUse (Bash), `gh pr merge` | Blocks the merge when a `/review` comment reports open findings with no recorded disposition in the PR body. |
| `pre-auto-merge-checkpoint-gate.py` | PreToolUse (Bash), `gh pr merge --auto` | Fail-closed, no-override gate requiring a fresh PR comment with both a clean findings marker and a complete checkpoints marker. |
| `pre-merge-numbering-check.py` | PreToolUse (Bash), `gh pr merge` (dev-env repo only) | Blocks a merge whose new `CLAUDE.md`/ADR-index numbers collide with numbers `origin/main` claimed since the branch point. |
| `pre-tool-use-canonical-mutate-guard.py` | PreToolUse (Bash/PowerShell) | Blocks git-mutating commands (checkout, commit, merge, reset, `gh pr merge --delete-branch`, …) run against a canonical (non-worktree) checkout. |
| `pre-bash-drift-check.py` | PreToolUse (Bash), every call | A fourth, elapsed-time-gated cwd/branch drift checkpoint alongside the three above, covering non-commit/merge Bash calls. |
| `pr-merge-reminder.py` | PostToolUse (Bash/PowerShell), `gh pr create`/`merge`/`git push` | Reminds Claude to write a journal stub (and the open-PR shard, for creates), scoped to the actual target repo. |
| `post-merge-tile-checkpoint.py` | PostToolUse (Bash/PowerShell), confirmed `gh pr merge` or the two-step REST merge fallback | Blocking reminder to spawn follow-up tiles via `spawn_task` immediately after a merge. |
| `post-pr-merge-pull.py` | PostToolUse (Bash/PowerShell), confirmed `gh pr merge` or the two-step REST merge fallback | Fast-forwards local `main`; parks a worktree left squatting `main` by `--delete-branch`. |
| `post-pr-merge-reclaim.py` | PostToolUse (Bash/PowerShell), confirmed `gh pr merge` or the two-step REST merge fallback | Spawns detached `node_modules`/`.turbo` reclamation from now-idle merged worktrees. |
| `post-tool-use-cwd-track.py` | PostToolUse (Bash/PowerShell), every call | Records `{repo_root, branch, cwd}` to a per-session state file, feeding the four drift checks above. |
| `usage-snapshot.py` | PostToolUse (Bash/PowerShell), `gh pr merge` or the two-step REST merge fallback | Emits a weekly/5-hour usage snapshot + top-5 costliest exchanges after every merge. |
| `stop-tile-enumeration-gate.py` | Stop | Blocks the stop when a merged PR, a dangling issue, an untabled tile spawn, or a tile spawned without its shard has no recorded follow-up enumeration. |
| `new-branch.sh` | `new-branch <name>` (shell function) | Creates a branch rooted at `origin/main`; snapshots the pre-existing-failure baseline when opted in. |
| `baseline-tests.sh` | `baseline-tests <snapshot\|diff\|gc>` | Captures/diffs/garbage-collects pre-existing test failure baselines for the fix-on-touch policy (ADR-030). |
| `merge-ready.sh` | `bash merge-ready.sh [owner/repo ...]` | Lists, per repo, open PRs that are green + mergeable + waiting on nothing vs. still open but not ready; read-only, defaults to `merickvaughn/lifting-logbook`. |

### GitHub project board (4)

| Script | Event / Invocation | Purpose |
|---|---|---|
| `post-tool-use.py` | PostToolUse (Bash/PowerShell), `gh issue create`/`gh pr create` | Auto-adds the item to the configured GitHub Project and prints the `required_fields` commands to run. |
| `post-pr-merge-project.py` | PostToolUse (Bash/PowerShell), confirmed `gh pr merge` or the two-step REST merge fallback | Auto-moves the linked issue (`Closes #N`) to Done on the configured board. |
| `reconcile-project-board.py` | `py -3 reconcile-project-board.py [--repo-root\|--scan-dir] [--dry-run]` | Finds open issues missing from the board and open board items missing a required field; add-only, never guesses a value. |
| `get-project-item.sh` | `ITEM_ID=$(bash get-project-item.sh <issue-number> ...)` | Resolves a GitHub Project item node ID from an issue/PR number. |

### Worktrees & disk management (7)

| Script | Event / Invocation | Purpose |
|---|---|---|
| `disk-space-check.py` | UserPromptSubmit / PreToolUse (Bash) | Watches free space on `C:`; warns below 20 GB, spawns detached reclamation below 10 GB. |
| `worktree-npm-install.py` | UserPromptSubmit | Auto-runs `npm ci`/`install` in a worktree missing `node_modules`; gates on free space, refuses below a 5 GB floor. |
| `pre-tool-use-worktree-path-check.py` | PreToolUse (Write/Edit/NotebookEdit) | Blocks a write whose absolute path escapes to the canonical root, or any write from an orphaned worktree. |
| `multi-worktree-alert.py` | UserPromptSubmit | Lists active worktrees in `repo:branch` format when ≥2 are open. |
| `prune-merged-worktrees.py` | `py -3 prune-merged-worktrees.py [--dry-run] [--repo-path\|--scan-dir] [--include-named]` | Removes merged/idle worktrees and parks any squatting `main`; engine behind the `prune-stale-worktrees` routine. |
| `reclaim-worktree-disk.py` | `py -3 reclaim-worktree-disk.py [--dry-run] [--repo-path\|--scan-dir] [--min-free-gb N]` | Strips regenerable `node_modules`/`.turbo` from idle worktrees; engine behind the `reclaim-worktree-disk` routine and the disk-check hook's detached spawn. |
| `sweep-scratch-debris.py` | `py -3 sweep-scratch-debris.py [--apply] [--max-age-days N]` | One-time/on-demand force-sweep of accumulated per-session sentinel/marker files in `~/.claude/scratch/`. |

### Session state, reliability & token tracking (22)

| Script | Event / Invocation | Purpose |
|---|---|---|
| `pre-tool-use-shell-content-write-guard.py` | PreToolUse (Bash/PowerShell) | Blocks a command that routes an *inline literal* to a file — or into a `gh`/`git` prose argument (`--body-file -`, `-F -`) — when that literal is multi-line or carries an apostrophe, backtick, or backslash; the content-shaped generalization of the journal guard. Program-output redirection (`gh … > "$TMPFILE"`, `npm test > out.log`) and an interpreter's stdin (`py -3 - <<'PY'`) are never matched, structurally. Bypass with `ALLOW_SHELL_CONTENT_WRITE=1`. See [ADR-138](../../docs/adr/138-shell-content-write-guard.md). |
| `replay-shell-content-guard.py` | `py -3 replay-shell-content-guard.py [--gap] [--json] [--samples N] [--scan-dir DIR]` | Replays the guard above over recorded session transcripts and reports block rate, mechanism mix, override use, and the **failure-rate enrichment ratio** (blocked-set shell-parse-failure rate ÷ corpus baseline; `>1` concentrates real failures, `~1` means over-matching). Read-only. ADR-138 Amendment 1's answer to "is it over-matching?" — re-run after any detector change to the guard. |
| `pre-tool-use-nested-agent-background-guard.py` | PreToolUse (Agent) | Blocks a nested `Agent`-tool spawn (`agent_id` present) that omits `run_in_background` entirely — the exact failure signature behind career-playbook's ADR-090 orphaned-subagent stalls. Any explicit value (`true` or `false`) passes through untouched, and a top-level spawn is untouched entirely. See [ADR-126](../../docs/adr/126-nested-agent-spawn-background-guard.md). |
| `pre-tool-use-skill-file-size-guard.py` | PreToolUse (Write/Edit) | Blocks a Write/Edit that would leave a `SKILL.md` file over a configurable byte ceiling (default 256KB). See [ADR-127](../../docs/adr/127-skill-file-size-guard.md). |
| `session-mode-prompt.py` | UserPromptSubmit | One-time per-session reminder of the active permission mode (plan/bypass/auto). |
| `dev-env-sync.py` | UserPromptSubmit | Fast-forward pulls dev-env to `origin/main`; auto-returns a clean canonical to `main` or warns; escalates a persistent pull failure. |
| `session-start-sync.py` | SessionStart (`startup`/`resume`) | Generalizes `dev-env-sync.py`'s fetch/ff-only-or-warn mechanic to any repo a session starts in (any dev-env checkout, canonical or worktree, stays covered by `dev-env-sync.py`); auto-fast-forwards only a canonical/sole checkout on its own default branch, clean of tracked changes and ff-safe, with no other session active there recently, else emits a loud advisory naming why not. [ADR-130](../../docs/adr/130-session-start-fetch-ff-only-or-warn.md) |
| `turn-count-hook.py` | UserPromptSubmit | Warns when session context token/turn count exceeds threshold. |
| `idle-refresher.py` | UserPromptSubmit | After a long idle gap, injects a cue to open the reply with a refresher of prior state. |
| `hook-liveness-check.py` | UserPromptSubmit | Warns when a wired hook's heartbeat has gone stale (hasn't recorded in its expected cadence). |
| `awake-blocker.py` | UserPromptSubmit / Stop / Notification | Holds a Windows system-sleep lock via a detached watcher while Claude is processing. |
| `memory-write-advisory.py` | PostToolUse (Write) | Reminds Claude to pair a durable memory write with an immortalization issue when none is linked. |
| `skill-file-size-advisory.py` | PostToolUse (Write/Edit) | Non-blocking nudge when a `SKILL.md` write/edit lands at/above a lower watermark (default 200KB), ahead of the hard-block guard's ceiling. See [ADR-127](../../docs/adr/127-skill-file-size-guard.md). |
| `stop-experiment-verdict-gate.py` | Stop | Blocks the stop when an assistant message states a process-experiment conclusion with no `/experiment-audit` run and no skip override (the verdict half of the Experimental Rigor protocol, ADR-115). |
| `posttooluse-inert-advisory.py` | Stop | Safety net for background/SDK sessions where PostToolUse hooks never fired despite a `gh issue/pr create`/`merge`. |
| `token-tracker.py` | Stop | Aggregates session token usage to `scratch/token-sessions.jsonl`. |
| `token-report.py` | `py -3 token-report.py [--date\|--days\|--project\|--latest\|--show-subagents]` | Generates markdown/JSON token usage reports from the token-tracker log. |
| `backfill-tokens.py` | `py -3 backfill-tokens.py` | Backfills token data for sessions predating the token-tracker hook; idempotent on `session_id`. |
| `session-mode-report.py` | `py -3 session-mode-report.py [--since\|--interactive-only\|--non-plan-only\|--log]` | Reports which sessions started outside `plan` mode, from the session-mode-prompt hook's log. |
| `run-hook-tests.py` | `py -3 run-hook-tests.py [--list] [--timeout N] [--max-retries N]` | Discovers and runs the whole hook/script test suite; engine behind the `hook-tests` CI workflow. `--max-retries` (default 2) visibly re-runs a failing file before counting it a final failure, absorbing transient CI flakiness without masking a real regression (dev-env#994, [ADR-134](../../docs/adr/134-run-hook-tests-retry-mechanism.md)). |
| `register-keep-token-warm.ps1` | `powershell -File register-keep-token-warm.ps1 [-IntervalHours N] [-Unregister]` | Per-machine, run-once: registers the scheduled task that keeps the OAuth token fresh. |
| `keep-token-warm.ps1` | Scheduled-task payload (not run by hand) | Triggers a CLI OAuth-token refresh every few hours so `usage-snapshot.py` rarely needs an on-demand refresh — unless a desktop-app auth-status probe detects the refresh would be futile, in which case it exits early instead. |

---

## Adding a new script

1. Drop the file in this directory (or `tests/` for its test — see
   [`tests/README.md`](tests/README.md) → Adding a new test file).
2. **If it's a Claude Code hook:** wire it in `claude/settings.json` in the same commit, follow
   the [Authoring rules](../../docs/REFERENCE.md#authoring-rules) (safe-exit guard, `pyw -3`
   invocation, `_winsubp` import, declared fail direction, output-contract channel), and add rows
   to the root [`README.md`](../../README.md) Hooks table and
   [`docs/REFERENCE.md`](../../docs/REFERENCE.md)'s Hooks section — required by the
   `CLAUDE.md` Documentation Maintenance table.
3. **If it's a shared `_foo.py` module or a utility script:** add it to
   [`docs/REFERENCE.md`](../../docs/REFERENCE.md)'s Utilities section (or its shared-module
   prose) if it has externally-relevant behavior.
4. Add a one-line row to this file in the matching section (shared module, or the domain closest
   to what the script does). This file is not part of the `CLAUDE.md` Documentation Maintenance
   table's required-updates list — keeping it current is good hygiene, not a merge gate.
