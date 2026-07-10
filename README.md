# dev-env

Development environment configuration for cross-device use.

## Contents

| Path | Linked to | Purpose |
|---|---|---|
| `claude/CLAUDE.md` | `~/.claude/CLAUDE.md` | Claude Code global configuration |
| `claude/settings.json` | `~/.claude/settings.json` | Claude Code hooks and permissions |
| `claude/scripts/` | `~/.claude/scripts/` (junction) | Hook scripts and utilities |
| `claude/skills/` | `~/.claude/skills/` (junction) | Custom slash command skills |
| `claude/routines/` | `~/.claude/routines/` (junction) | Scheduled-task source definitions — registering a live task is a separate step, see [Routines](#routines) |
| `claude/templates/` | `~/.claude/templates/` (junction) | Document templates, read at runtime by skills |

## Setup

Clone the repo and run the setup script once on each machine:

```bash
git clone https://github.com/brownm09/dev-env.git ~/Git/dev-env
cd ~/Git/dev-env
bash setup.sh
```

The script creates symlinks/junctions from the expected config locations into this repo.
Any edits made through those symlinks update the repo file directly.

## Skills

Custom slash commands loaded from `claude/skills/`. Invoke with `/skill-name [args]`.

| Command | Purpose |
|---|---|
| [`/propose <idea>`](claude/skills/propose/SKILL.md) | One-line idea → proposal doc → GitHub issue → ROADMAP entry. Per-project config via `.claude/propose.json`. |
| [`/journal-compose [YYYY-MM-DD]`](claude/skills/journal-compose/SKILL.md) | Composes the end-of-day engineering journal from stub files in an isolated worktree — the shared canonical checkout is never branch-switched or written to. Dedicated-session only. |
| [`/research [tag:] <decision> [--compare <alt>]`](claude/skills/research/SKILL.md) | Finds 1–3 primary sources. Greps shared source library first; spawns a subagent only on cache miss. |
| [`/review <PR-URL> [flags]`](claude/skills/review/SKILL.md) | Reviews a PR for correctness, security, reliability, maintainability, documentation reconciliation, test coverage (ADR-022), test integrity (ADR-029), and ADR-warrant check (ADR-011). Posts report as PR comment by default. |
| [`/journal-onboard [slug]`](claude/skills/journal-onboard/SKILL.md) | Scaffolds `sessions/<project>/` in engineering-journal and optionally creates `.claude/CLAUDE.md` in the project repo. |
| [`/memory-audit`](claude/skills/memory-audit/SKILL.md) | Reconciles agent memory against the version-controlled instructions and emits a table (per entry: durable? instruction-home? disposition). Catches never-ported durables, stale notes, and index drift. |

## Hooks

Hook scripts run automatically via Claude Code's `hooks` configuration in `claude/settings.json`.
Most hooks are advisory — they emit reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py`, which blocks `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree, **and** blocks any such call issued from an orphaned/disconnected worktree (one whose `.git` link is gone, so git silently resolves to the canonical repo).

Hooks that spawn subprocesses (`git`, `gh`, `bash`, …) must `import _winsubp` — a helper module at `claude/scripts/_winsubp.py` that patches `subprocess.Popen.__init__` to (1) set `CREATE_NO_WINDOW` so children don't flash a console window under `pythonw.exe`, and (2) default text-mode calls (`text=True`) with no explicit `encoding=` to UTF-8 decoding rather than the Windows cp1252 default, which can't represent every byte `gh`/`git` may emit. See [ADR-007](docs/adr/007-hook-command-invocation.md).

PostToolUse Bash hooks that read a command's output must use `read_command_output` from `claude/scripts/_hookio.py` — Claude Code's payload exposes output under `tool_response.stdout`/`stderr`, not `output`, so reading the wrong field silently disables the hook. See [ADR-049](docs/adr/049-hook-payload-output-field.md) and [ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md).

The worktree-maintenance scripts (`prune-merged-worktrees.py`, `reclaim-worktree-disk.py`) skip a worktree with a live Claude session via `worktree_session_is_live` from `claude/scripts/_worktree_liveness.py` — it reads the worktree's transcript-dir mtime under `~/.claude/projects/`, the only signal by which an out-of-process routine can detect (and avoid severing) an active session in *another* worktree. See [ADR-051](docs/adr/051-worktree-liveness-guard.md).

The journal open-PR hooks (`reconcile-open-prs.py`, `post-compact.py`) enumerate the per-PR shards `sessions/<project>/open-prs/<N>.json` and the legacy `open-prs.jsonl` through one shared reader, `claude/scripts/_journal_shards.py` (`iter_pr_shards` returns `(path, entry)` pairs so reconcile can `unlink` and post-compact can read; `read_legacy_entries` drains the legacy file) — so the two hooks can't drift on how shards are enumerated, sorted, and parsed, and the legacy format has a single retirement point. See [ADR-057](docs/adr/057-shared-journal-shard-reader.md).

Per-session sentinel helpers, transcript-locate, and the transcript-record readers are extracted into `claude/scripts/_hookutil.py` (Stop / UserPromptSubmit hook family — the analogue of `_hookio.py` for the PostToolUse family): `cleanup_stale_sentinels(prefix)`, `sentinel_path(prefix, session_id)`, `find_transcript(session_id)`, and the readers `load_records` / `_parse_records` / `iter_bash_calls` (returns `(command, output, cwd)`) / `_result_text` / `_content_items`. Used by `posttooluse-inert-advisory.py`, `stop-tile-enumeration-gate.py`, `reconcile-open-prs.py`, and `token-tracker.py`. See [ADR-064](docs/adr/064-shared-hookutil-sentinel-transcript-locate.md) (sentinels / transcript-locate) and [ADR-090](docs/adr/090-shared-transcript-readers-hookutil.md) (transcript-record readers).

`prune-merged-worktrees.py`, `post-pr-merge-pull.py`, `dev-env-sync.py`, and `journal-canonical-guard.py` share `claude/scripts/_worktree_topology.py`, which now hosts two related but distinct invariants: dev-env's canonical must **always** be `main` (the first three scripts detect a non-canonical worktree squatting `main` — which locks the canonical `~/Git/dev-env` off `main` and leaves the symlinked `~/.claude/` serving stale tooling — and **park** it back onto its own `claude/<slug>` branch, non-destructive since `git checkout -b` changes no working-tree files and frees the ref even for a dirty worktree; see [ADR-058](docs/adr/058-worktree-squatting-main-detection-correction.md)), while engineering-journal's canonical may legitimately sit on many branches and only a **hijacked** one (detached, or a stray `claude/*` branch) is ever corrected (`journal-canonical-guard.py`; see [ADR-093](docs/adr/093-journal-canonical-hijack-guard.md)).

| Event | Script | Purpose |
|---|---|---|
| UserPromptSubmit | `session-mode-prompt.py` | Injects a one-time mode-confirmation reminder into Claude's context on the first prompt of each new session |
| UserPromptSubmit | `dev-env-sync.py` | Fast-forward pulls dev-env to `origin/main` at session start; auto-returns a clean canonical worktree to `main` — including from a detached HEAD ([ADR-058 Amendment](docs/adr/058-worktree-squatting-main-detection-correction.md), dev-env#619) — or warns, naming a worktree squatting `main` + its park command, so `~/.claude/` tooling stays current. All advisories print to stdout (never stderr, which a `UserPromptSubmit` hook's exit-0 doesn't surface to Claude) and name local/remote SHAs + commit-behind counts, so a silently-blocked pull (e.g. a dirty working-tree file) can't drift unnoticed again ([ADR-098](docs/adr/098-dev-env-sync-advisories-to-stdout.md), dev-env#694) |
| UserPromptSubmit | `journal-canonical-guard.py` | Detects the engineering-journal canonical checkout hijacked onto a stray `claude/*` branch or detached HEAD (a scheduled-task worktree-provisioning defect, reproduced 2 mornings running) and restores it to `main`; leaves any other branch (e.g. `draft/YYYY-MM-DD`) untouched, since that repo's canonical is legitimately on many branches (see [ADR-093](docs/adr/093-journal-canonical-hijack-guard.md)). All advisories print to stdout (never stderr, which a `UserPromptSubmit` hook's exit-0 doesn't surface to Claude) — the identical fix ADR-098 made for the sibling `dev-env-sync.py` ([ADR-099](docs/adr/099-journal-canonical-guard-advisories-to-stdout.md), dev-env#699) |
| UserPromptSubmit | `new-day-journal-check.py` | Warns if stale `draft/*` journal branches exist on origin |
| UserPromptSubmit | `journal-onboard-check.py` | Warns when the active project has no journal home in engineering-journal |
| UserPromptSubmit | `turn-count-hook.py` | Warns when session context token count exceeds threshold |
| UserPromptSubmit | `idle-refresher.py` | On the user's return after an idle gap over the threshold (default 60 min; `idle_refresher_minutes` override), injects an `additionalContext` cue to open the reply with a refresher (what we were working on, current state, pending to-dos/tiles). Measures the gap from the last assistant turn in the transcript ([ADR-095](docs/adr/095-session-boundary-summaries-and-idle-refresher.md)) |
| UserPromptSubmit | `multi-worktree-alert.py` | Lists active worktrees in `repo:branch` format when ≥2 are open |
| UserPromptSubmit | `reconcile-open-prs.py` | Removes stale open-PR records whose PRs are now merged/closed — deletes per-PR `open-prs/<N>.json` shards ([ADR-056](docs/adr/056-per-session-sharding-journal-companion-files.md)) and drains the legacy `open-prs.jsonl`; emits surviving open PRs, and any still-uncommitted open-PR shard changes sitting in the canonical checkout ([ADR-082 Addendum](docs/adr/082-journal-compose-worktree-isolation.md)), as session context |
| UserPromptSubmit / PreToolUse (Bash) | `disk-space-check.py` | Watches free space on `C:` before each prompt and each Bash call; warns once per session below 20 GB and spawns detached `node_modules`/`.turbo` reclamation below 10 GB. The `PreToolUse(Bash)` registration closes the gap where a long tool-call-only stretch (no new prompt) could exhaust disk between `UserPromptSubmit` checks ([ADR-087](docs/adr/087-pretooluse-disk-space-check.md)) |
| UserPromptSubmit | `worktree-npm-install.py` | Auto-runs `npm ci`/`install` when a Claude worktree lacks `node_modules`; gates on free space first — below 10 GB it reclaims (idle worktrees, then npm cache) and re-checks, and below a 5 GB floor it refuses the install rather than risk a silently-truncated `node_modules` (ENOSPC, see [ADR-045](docs/adr/045-pre-install-freespace-gate.md)) |
| UserPromptSubmit / Stop / Notification | `awake-blocker.py` | Spawns a detached watcher that holds a Windows system-sleep lock while Claude is processing; releases on Stop or Notification |
| PreToolUse (Bash) | `pre-commit-branch-check.py` | Emits current branch as a checkpoint before `git commit`, plus a drift warning if it differs from the repo/branch recorded after the session's last Bash call (see [ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md)) |
| PreToolUse (Bash) | `pre-pr-create-check.py` | Emits test-verification checklist, documentation-gap warning, pre-existing-failure baseline advisory, and current branch/repo (plus a `--head` reminder and drift warning, [ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md)) before `gh pr create` |
| PreToolUse (Bash) | `pre-merge-message-check.py` | Blocks `gh pr merge` when `C:/Users/brown/.claude/merge-queue.md` has content — surfaces queued user messages for Claude to act on before merging (see [ADR-061](docs/adr/061-pre-merge-message-queue.md)) |
| PreToolUse (Bash) | `pre-merge-branch-check.py` | Emits current branch/repo as a checkpoint before `gh pr merge`, plus a drift warning if it differs from the repo/branch recorded after the session's last Bash call (see [ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md)) |
| PreToolUse (Bash) | `pre-merge-findings-gate.py` | Blocks `gh pr merge` when a `/review` comment reports open findings and the PR body records no disposition — mechanical enforcement of the all-findings merge gate (see [ADR-039](docs/adr/039-merge-gate-findings-enforcement.md)) |
| PreToolUse (Bash) | `pre-auto-merge-checkpoint-gate.py` | Only acts on `gh pr merge --auto`: blocks (fails closed, no override) unless the PR's single most recent comment carries both a clean-or-disposed `review-findings` marker and a complete, fresh `premerge-checkpoints` marker (ADR-warrant + doc-reconciliation) — mechanical pre-check restoring `--auto`'s "impossible to skip" property (see [ADR-083](docs/adr/083-auto-merge-checkpoint-gate.md)) |
| PreToolUse (Bash) | `pre-merge-numbering-check.py` | Blocks `gh pr merge` (dev-env only) when this branch's newly-added `CLAUDE.md` Testing-section or `docs/adr/INDEX.md` item number collides with a number `origin/main` has claimed since the branch's merge-base — concurrent PRs picking the same "next number" merge cleanly with no conflict, silently duplicating it (see [ADR-074](docs/adr/074-pre-merge-numbering-collision-check.md)) |
| PreToolUse (Bash) | `pre-tool-use-canonical-mutate-guard.py` | Blocks git-mutating commands (`checkout`, `commit`, `merge`, `reset`, etc.) when `cwd` is at a canonical (non-worktree) checkout root, or when a `-C`/`--git-dir`/`--work-tree` flag redirects one at a canonical checkout from elsewhere (e.g. `git -C <other-repo> checkout` from a worktree; dev-env#576, ADR-071 Amendment 2) — two sessions sharing one checkout can otherwise thrash HEAD out from under each other. The engineering-journal checkout is a temporary redirect-target carve-out (pending dev-env#346). Fails open outside a resolvable git repo; bypass with `ALLOW_CANONICAL_MUTATE=1` (see [ADR-071](docs/adr/071-canonical-checkout-mutate-guard-hook.md)) |
| PreToolUse (Bash) | `pre-tool-use-journal-compose-force-guard.py` | Blocks a git `worktree`/`commit`/`push` command that references a `draft/<today>` or `compose[-/]<today>` target (today from the hook's own clock) unless a fresh, `force=true` marker exists — written only by `journal-compose-force-resolve.py`, itself invoked with the literal, harness-substituted `$ARGUMENTS` text as the first action of `/journal-compose` Step 0.6. Mechanical enforcement of the journal-compose today-guard (ADR-017) — an agent can no longer reason its way past a prose-only guard (dev-env#631). Fails **closed** on a missing/stale/corrupt marker (deliberate reversal of this hook family's usual fail-open convention) and ships with **no override token** (see [ADR-096](docs/adr/096-journal-compose-mechanical-force-guard.md)) |
| PreToolUse (Write/Edit/NotebookEdit) | `pre-tool-use-worktree-path-check.py` | Blocks file writes whose absolute path targets the canonical repo root instead of the active worktree, and blocks all writes from an orphaned worktree whose `.git` link no longer resolves (see [ADR-024](docs/adr/024-worktree-path-guard-hook.md)) |
| PostToolUse (Bash) | `pr-merge-reminder.py` | Reminds to write a journal stub (and, for `gh pr create`, the `open-prs/<N>.json` shard) after `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR) |
| PostToolUse (Bash) | `post-merge-tile-checkpoint.py` | After a successful `gh pr merge`, emits a blocking reminder to spawn follow-up tiles via `spawn_task` for any out-of-scope fixes or deferred work surfaced during the session ([ADR-060](docs/adr/060-post-merge-tile-checkpoint-hook.md)) |
| PostToolUse (Bash) | `post-tool-use.py` | Auto-adds issues/PRs to configured GitHub Project; exits 2 with `required_fields` reminders |
| PostToolUse (Bash) | `post-pr-merge-pull.py` | Fast-forwards local `main` after `gh pr merge`; parks the just-merged worktree off `main` if `gh --delete-branch` left it squatting the ref ([ADR-058](docs/adr/058-worktree-squatting-main-detection-correction.md)) |
| PostToolUse (Bash) | `post-pr-merge-reclaim.py` | After a successful `gh pr merge`, spawns detached reclamation of `node_modules`/`.turbo` from now-idle worktrees (the dominant `C:` consumer), reclaiming at the idle event instead of waiting for the 6-hourly routine (see [ADR-045](docs/adr/045-pre-install-freespace-gate.md)) |
| PostToolUse (Bash) | `post-pr-merge-project.py` | Auto-moves linked issue to Done on configured GitHub Project after `gh pr merge` |
| PostToolUse (Bash) | `usage-snapshot.py` | Emits weekly/5-hour utilisation vs. daily soft targets and top-5 session exchanges after `gh pr merge` |
| PostToolUse (Bash) | `stub-push-archive-reminder.py` | Writes a sentinel flag after a stub is pushed to engineering-journal with no unresolved open PR from this session; Stop hook consumes it to remind Claude to archive |
| PostToolUse (Bash) | `post-tool-use-cwd-track.py` | Records the current repo root + branch after every Bash call to a per-session state file; feeds the drift-warning check in `pre-commit-branch-check.py`, `pre-pr-create-check.py`, and `pre-merge-branch-check.py` (see [ADR-085](docs/adr/085-bash-repo-branch-drift-detection.md)) |
| PostToolUse (Write) | `memory-write-advisory.py` | Reminds Claude to pair a durable memory write with an immortalization issue when a memory file is written without an issue/ADR/`CLAUDE.md` link; non-blocking advisory (see [ADR-048](docs/adr/048-memory-immortalization-issue-pairing.md)) |
| PostToolUse (Write/Edit/Bash) | `journal-shard-write-advisory.py` | Validates engineering-journal manifest and open-PR shards touched by a Write, Edit, or Bash call against the required-field schema, flagging missing fields, BOMs, and non-numeric open-PR filenames immediately in the writing session instead of at the next day's compose gate; non-blocking advisory (see [ADR-081](docs/adr/081-write-time-journal-shard-validation-hook.md)) |
| Stop | `token-tracker.py` | Aggregates session token usage to `scratch/token-sessions.jsonl` |
| Stop | `journal-stop-check.py` | On the stub-push sentinel flag, **blocks the stop (exit 2, reminder on stderr)** so Claude actually archives the session — a Stop hook's exit-0 stdout is not added to Claude's context ([ADR-091](docs/adr/091-journal-stop-check-archive-reminder-blocking.md)). Then, non-blocking (exit 0, stdout), checks stale open journal stubs and unmerged draft branches and cleans up orphaned drafts |
| Stop | `posttooluse-inert-advisory.py` | Safety net for [ADR-053](docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md): when a dev-env `gh issue/pr create` or `gh pr merge` ran but no PostToolUse hook fired all session (background/SDK-launched), emits a one-line advisory to apply the manual board fallback. Non-blocking (exit 0), once per session ([ADR-055](docs/adr/055-reliable-event-inert-posttooluse-advisory.md)) |
| Stop | `stop-tile-enumeration-gate.py` | State-keyed tile-enumeration gate: scans the just-ended transcript and **blocks the stop (exit 2)** on any of three independent triggers — **(1)** a PR reached MERGED state this session by any path, including auto-merge and pure `gh api` merges the command-keyed `post-merge-tile-checkpoint.py` is blind to, with no recorded tile-enumeration ("Follow-ups considered … → tiled/→ not tiled"; a bare "no follow-ups" does not satisfy it) ([ADR-088](docs/adr/088-state-keyed-tile-enumeration-gate.md)), **(2)** a `gh issue create` this session was left unresolved at Stop — not closed via a same-session merged PR's Closes/Fixes/Resolves keyword, nor explicitly closed — the pure-investigation-session case ([ADR-092](docs/adr/092-dangling-issue-tile-enumeration-gate.md)), or **(3)** a `spawn_task` tile was spawned this session but no assistant message carries the stable heading `### Tiles spawned this session` ([ADR-094](docs/adr/094-tile-tables-and-issue-per-tile.md) addendum). All three are the Stop-hook analog of `pre-merge-findings-gate`; the merged-PR trigger complements the command-keyed hook and, unlike it, still fires in background/SDK sessions ([ADR-053](docs/adr/053-posttooluse-hooks-inert-in-background-sessions.md)). One recorded enumeration (a real `spawn_task` or the prescribed text) satisfies triggers (1)/(2); trigger (3) is a stricter, separate bar requiring the table itself — a spawned tile alone resolves (1)/(2) but not (3). Fires once per session; honors "skip tiles" and `stop_hook_active` |
| PostCompact | `post-compact.py` | Emits compaction status line (trigger type + remaining tokens) |
| Git pre-push | `hooks/pre-push` | Warns when branch merge base diverges from `origin/main` in squash-merge repos; blocks engineering-journal pushes to already-merged draft branches; blocks pushes that drift `package-lock.json` from `package.json` (see [ADR-036](docs/adr/036-lockfile-drift-prevention.md)) |

## Routines

Autonomous scheduled agents. Their canonical source lives in `claude/routines/` (junctioned read-only to `~/.claude/routines/`), but registering or updating the *live* task is a separate, manual step via the `scheduled-tasks` MCP tool — the tool owns `~/.claude/scheduled-tasks/` directly and never reads through the junction. See [ADR-003 amendment](docs/adr/003-config-in-version-control.md). No user interaction once scheduled.

| Schedule | Routine | Purpose |
|---|---|---|
| Daily 7:09am local | `daily-journal-compose` | Assembles stub files into canonical journal entries and opens PRs |
| Daily 8am local | `prune-stale-worktrees` | Removes merged worktrees — both `claude/*` and, via `--include-named`, hand-named branches held to the same merged/dirty/liveness bar ([ADR-078](docs/adr/078-opt-in-named-branch-worktree-pruning.md)) — and parks any non-primary worktree squatting `main` back onto its own branch (freeing the ref, [ADR-058](docs/adr/058-worktree-squatting-main-detection-correction.md)) across all repos under `C:/Users/brown/Git`; skips any worktree with an active Claude session (transcript activity within 24h, see [ADR-051](docs/adr/051-worktree-liveness-guard.md)) |
| Every 6 hours | `reclaim-worktree-disk` | Strips regenerable `node_modules`/`.turbo` from idle Claude worktrees under `.claude/worktrees/`, reclaiming disk between weekly prune runs; skips any worktree with an active Claude session (transcript activity within 6h, see [ADR-051](docs/adr/051-worktree-liveness-guard.md)) |
| Nightly 8:00 UTC (3 AM CDT) | `nightly-research` | Researches pending topics from the queue and writes structured markdown notes to `research-notes/` |
| Biweekly (every other Sun 9am local) | `biweekly-retro` | Synthesizes a retrospective (global readout + per-repo sections + tracked ratio) from the trailing 4 weeks of journal entries; opens a report PR in `engineering-journal` and files deduped action-item issues in the correct repo per finding (cross-cutting → dev-env) |
| Daily 6am local | `reconcile-project-board` | Adds open issues missing from each configured repo's board (today: dev-env #3, lifting-logbook #2) across all repos under `C:/Users/brown/Git` with a `.claude/hook-config.json`, and surfaces any still missing a required field; backstop for issues filed in background/`spawn_task` sessions where the add-hook is inert ([ADR-068](docs/adr/068-reconcile-project-board-orphan-issues.md), [ADR-070](docs/adr/070-reconcile-project-board-scan-dir.md)) |
| Weekly (Mon 9am local) | `weekly-memory-audit` | Sweeps every project's memory store for never-ported durables, stale notes, and drift; auto-files deduped *promote* issues (label `memory-audit`) in the correct repo; commits a cross-project reconciliation report to `engineering-journal`. Read-only on memory — never edits or deletes a memory file. |

## Adding new configs

1. Add the file under a descriptive directory (e.g., `claude/scripts/`, `claude/skills/`)
2. If it needs symlinking, add its name to `CLAUDE_FILE_LINKS` or `CLAUDE_DIR_LINKS` near the top of `setup.sh` (both `setup_windows()` and `setup_unix()` iterate the same arrays)
3. Update the relevant table above **and** the corresponding section in [`docs/REFERENCE.md`](docs/REFERENCE.md)
4. Update `claude/CLAUDE.md` if the artifact changes session behavior

→ Full reference: **[docs/REFERENCE.md](docs/REFERENCE.md)**
