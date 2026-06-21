# dev-env

Development environment configuration for cross-device use.

## Contents

| Path | Linked to | Purpose |
|---|---|---|
| `claude/CLAUDE.md` | `~/.claude/CLAUDE.md` | Claude Code global configuration |
| `claude/settings.json` | `~/.claude/settings.json` | Claude Code hooks and permissions |
| `claude/scripts/` | `~/.claude/scripts/` (junction) | Hook scripts and utilities |
| `claude/skills/` | `~/.claude/skills/` (junction) | Custom slash command skills |
| `claude/templates/` | read at runtime by skills | Document templates |

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
| [`/journal-compose [YYYY-MM-DD]`](claude/skills/journal-compose/SKILL.md) | Composes the end-of-day engineering journal from stub files. Dedicated-session only. |
| [`/research [tag:] <decision> [--compare <alt>]`](claude/skills/research/SKILL.md) | Finds 1–3 primary sources. Greps shared source library first; spawns a subagent only on cache miss. |
| [`/review <PR-URL> [flags]`](claude/skills/review/SKILL.md) | Reviews a PR for correctness, security, reliability, maintainability, documentation reconciliation, test coverage (ADR-022), and test integrity (ADR-029). Posts report as PR comment by default. |
| [`/journal-onboard [slug]`](claude/skills/journal-onboard/SKILL.md) | Scaffolds `sessions/<project>/` in engineering-journal and optionally creates `.claude/CLAUDE.md` in the project repo. |
| [`/memory-audit`](claude/skills/memory-audit/SKILL.md) | Reconciles agent memory against the version-controlled instructions and emits a table (per entry: durable? instruction-home? disposition). Catches never-ported durables, stale notes, and index drift. |

## Hooks

Hook scripts run automatically via Claude Code's `hooks` configuration in `claude/settings.json`.
Most hooks are advisory — they emit reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py`, which blocks `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree, **and** blocks any such call issued from an orphaned/disconnected worktree (one whose `.git` link is gone, so git silently resolves to the canonical repo).

Hooks that spawn subprocesses (`git`, `gh`, `bash`, …) must `import _winsubp` — a helper module at `claude/scripts/_winsubp.py` that patches `subprocess.Popen.__init__` to set `CREATE_NO_WINDOW` so children don't flash a console window under `pythonw.exe`. See [ADR-007](docs/adr/007-hook-command-invocation.md).

PostToolUse Bash hooks that read a command's output must use `read_command_output` from `claude/scripts/_hookio.py` — Claude Code's payload exposes output under `tool_response.stdout`/`stderr`, not `output`, so reading the wrong field silently disables the hook. See [ADR-049](docs/adr/049-hook-payload-output-field.md) and [ADR-050](docs/adr/050-shared-hookio-sibling-hook-fixes.md).

| Event | Script | Purpose |
|---|---|---|
| UserPromptSubmit | `session-mode-prompt.py` | Injects a one-time mode-confirmation reminder into Claude's context on the first prompt of each new session |
| UserPromptSubmit | `dev-env-sync.py` | Fast-forward pulls dev-env to `origin/main` at session start |
| UserPromptSubmit | `new-day-journal-check.py` | Warns if stale `draft/*` journal branches exist on origin |
| UserPromptSubmit | `journal-onboard-check.py` | Warns when the active project has no journal home in engineering-journal |
| UserPromptSubmit | `turn-count-hook.py` | Warns when session context token count exceeds threshold |
| UserPromptSubmit | `multi-worktree-alert.py` | Lists active worktrees in `repo:branch` format when ≥2 are open |
| UserPromptSubmit | `reconcile-open-prs.py` | Removes stale entries from `open-prs.jsonl` whose PRs are now merged/closed; emits surviving open PRs as session context |
| UserPromptSubmit | `disk-space-check.py` | Watches free space on `C:`; warns once per session below 20 GB and spawns detached `node_modules`/`.turbo` reclamation below 10 GB |
| UserPromptSubmit | `worktree-npm-install.py` | Auto-runs `npm ci`/`install` when a Claude worktree lacks `node_modules`; gates on free space first — below 10 GB it reclaims (idle worktrees, then npm cache) and re-checks, and below a 5 GB floor it refuses the install rather than risk a silently-truncated `node_modules` (ENOSPC, see [ADR-045](docs/adr/045-pre-install-freespace-gate.md)) |
| UserPromptSubmit / Stop / Notification | `awake-blocker.py` | Spawns a detached watcher that holds a Windows system-sleep lock while Claude is processing; releases on Stop or Notification |
| PreToolUse (Bash) | `pre-commit-branch-check.py` | Emits current branch as a checkpoint before `git commit` |
| PreToolUse (Bash) | `pre-pr-create-check.py` | Emits test-verification checklist, documentation-gap warning, and pre-existing-failure baseline advisory before `gh pr create` |
| PreToolUse (Bash) | `pre-merge-findings-gate.py` | Blocks `gh pr merge` when a `/review` comment reports open findings and the PR body records no disposition — mechanical enforcement of the all-findings merge gate (see [ADR-039](docs/adr/039-merge-gate-findings-enforcement.md)) |
| PreToolUse (Write/Edit/NotebookEdit) | `pre-tool-use-worktree-path-check.py` | Blocks file writes whose absolute path targets the canonical repo root instead of the active worktree, and blocks all writes from an orphaned worktree whose `.git` link no longer resolves (see [ADR-024](docs/adr/024-worktree-path-guard-hook.md)) |
| PostToolUse (Bash) | `pr-merge-reminder.py` | Reminds to write a journal stub after `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR) |
| PostToolUse (Bash) | `post-tool-use.py` | Auto-adds issues/PRs to configured GitHub Project; exits 2 with `required_fields` reminders |
| PostToolUse (Bash) | `post-pr-merge-pull.py` | Fast-forwards local `main` after `gh pr merge` |
| PostToolUse (Bash) | `post-pr-merge-reclaim.py` | After a successful `gh pr merge`, spawns detached reclamation of `node_modules`/`.turbo` from now-idle worktrees (the dominant `C:` consumer), reclaiming at the idle event instead of waiting for the 6-hourly routine (see [ADR-045](docs/adr/045-pre-install-freespace-gate.md)) |
| PostToolUse (Bash) | `post-pr-merge-project.py` | Auto-moves linked issue to Done on configured GitHub Project after `gh pr merge` |
| PostToolUse (Bash) | `usage-snapshot.py` | Emits weekly/5-hour utilisation vs. daily soft targets and top-5 session exchanges after `gh pr merge` |
| PostToolUse (Bash) | `stub-push-archive-reminder.py` | Writes a sentinel flag after a stub is pushed to engineering-journal; Stop hook consumes it to remind Claude to archive |
| PostToolUse (Write) | `memory-write-advisory.py` | Reminds Claude to pair a durable memory write with an immortalization issue when a memory file is written without an issue/ADR/`CLAUDE.md` link; non-blocking advisory (see [ADR-048](docs/adr/048-memory-immortalization-issue-pairing.md)) |
| Stop | `token-tracker.py` | Aggregates session token usage to `scratch/token-sessions.jsonl` |
| Stop | `journal-stop-check.py` | Checks sentinel flag and stale open journal stubs at session end; emits closing reminder if stub was pushed this session |
| PostCompact | `post-compact.py` | Emits compaction status line (trigger type + remaining tokens) |
| Git pre-push | `hooks/pre-push` | Warns when branch merge base diverges from `origin/main` in squash-merge repos; blocks engineering-journal pushes to already-merged draft branches; blocks pushes that drift `package-lock.json` from `package.json` (see [ADR-036](docs/adr/036-lockfile-drift-prevention.md)) |

## Routines

Autonomous scheduled agents in `claude/routines/`. No user interaction.

| Schedule | Routine | Purpose |
|---|---|---|
| Daily midnight UTC | `daily-journal-compose` | Assembles stub files into canonical journal entries and opens PRs |
| Daily 8am local | `prune-stale-worktrees` | Removes merged `claude/*` worktrees and stale `main` checkouts across all repos under `C:/Users/brown/Git` |
| Every 6 hours | `reclaim-worktree-disk` | Strips regenerable `node_modules`/`.turbo` from idle Claude worktrees under `.claude/worktrees/`, reclaiming disk between weekly prune runs |
| Nightly 8:00 UTC (3 AM CDT) | `nightly-research` | Researches pending topics from the queue and writes structured markdown notes to `research-notes/` |
| Biweekly (every other Sun 9am local) | `biweekly-retro` | Synthesizes a retrospective (global readout + per-repo sections + tracked ratio) from the trailing 4 weeks of journal entries; opens a report PR in `engineering-journal` and files deduped action-item issues in the correct repo per finding (cross-cutting → dev-env) |

## Adding new configs

1. Add the file under a descriptive directory (e.g., `claude/scripts/`, `claude/skills/`)
2. Add a `ln -sf` or `mklink` line for it in `setup.sh` (if it needs symlinking)
3. Update the relevant table above **and** the corresponding section in [`docs/REFERENCE.md`](docs/REFERENCE.md)
4. Update `claude/CLAUDE.md` if the artifact changes session behavior

→ Full reference: **[docs/REFERENCE.md](docs/REFERENCE.md)**
