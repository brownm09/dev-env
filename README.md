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

## Hooks

Hook scripts run automatically via Claude Code's `hooks` configuration in `claude/settings.json`.
Most hooks are advisory — they emit reminders but do not block tool execution. The exception is `pre-tool-use-worktree-path-check.py`, which blocks `Write`, `Edit`, and `NotebookEdit` calls that target the canonical repo root instead of the active worktree.

| Event | Script | Purpose |
|---|---|---|
| UserPromptSubmit | `session-mode-prompt.py` | Injects a one-time mode-confirmation reminder into Claude's context on the first prompt of each new session |
| UserPromptSubmit | `dev-env-sync.py` | Fast-forward pulls dev-env to `origin/main` at session start |
| UserPromptSubmit | `new-day-journal-check.py` | Warns if stale `draft/*` journal branches exist on origin |
| UserPromptSubmit | `journal-onboard-check.py` | Warns when the active project has no journal home in engineering-journal |
| UserPromptSubmit | `turn-count-hook.py` | Warns when session context token count exceeds threshold |
| UserPromptSubmit | `multi-worktree-alert.py` | Lists active worktrees in `repo:branch` format when ≥2 are open |
| UserPromptSubmit | `reconcile-open-prs.py` | Removes stale entries from `open-prs.jsonl` whose PRs are now merged/closed; emits surviving open PRs as session context |
| PreToolUse (Bash) | `pre-commit-branch-check.py` | Emits current branch as a checkpoint before `git commit` |
| PreToolUse (Bash) | `pre-pr-create-check.py` | Emits test-verification checklist, documentation-gap warning, and pre-existing-failure baseline advisory before `gh pr create` |
| PreToolUse (Write/Edit/NotebookEdit) | `pre-tool-use-worktree-path-check.py` | Blocks file writes whose absolute path targets the canonical repo root instead of the active worktree |
| PostToolUse (Bash) | `pr-merge-reminder.py` | Reminds to write a journal stub after `gh pr create`, `gh pr merge`, or `git push` (when the pushed branch has an open PR) |
| PostToolUse (Bash) | `post-tool-use.py` | Auto-adds issues/PRs to configured GitHub Project; exits 2 with `required_fields` reminders |
| PostToolUse (Bash) | `post-pr-merge-pull.py` | Fast-forwards local `main` after `gh pr merge` |
| PostToolUse (Bash) | `post-pr-merge-project.py` | Auto-moves linked issue to Done on configured GitHub Project after `gh pr merge` |
| PostToolUse (Bash) | `usage-snapshot.py` | Emits weekly/5-hour utilisation vs. daily soft targets and top-5 session exchanges after `gh pr merge` |
| PostToolUse (Bash) | `stub-push-archive-reminder.py` | Writes a sentinel flag after a stub is pushed to engineering-journal; Stop hook consumes it to remind Claude to archive |
| Stop | `token-tracker.py` | Aggregates session token usage to `scratch/token-sessions.jsonl` |
| Stop | `journal-stop-check.py` | Checks sentinel flag and stale open journal stubs at session end; emits closing reminder if stub was pushed this session |
| PostCompact | `post-compact.py` | Emits compaction status line (trigger type + remaining tokens) |
| Git pre-push | `hooks/pre-push` | Warns when branch merge base diverges from `origin/main` in squash-merge repos |

## Routines

Autonomous scheduled agents in `claude/routines/`. No user interaction.

| Schedule | Routine | Purpose |
|---|---|---|
| Daily midnight UTC | `daily-journal-compose` | Assembles stub files into canonical journal entries and opens PRs |
| Daily 8am local | `prune-stale-worktrees` | Removes merged `claude/*` worktrees and stale `main` checkouts across all repos under `C:/Users/brown/Git` |
| Nightly 8:00 UTC (3 AM CDT) | `nightly-research` | Researches pending topics from the queue and writes structured markdown notes to `research-notes/` |

## Adding new configs

1. Add the file under a descriptive directory (e.g., `claude/scripts/`, `claude/skills/`)
2. Add a `ln -sf` or `mklink` line for it in `setup.sh` (if it needs symlinking)
3. Update the relevant table above **and** the corresponding section in [`docs/REFERENCE.md`](docs/REFERENCE.md)
4. Update `claude/CLAUDE.md` if the artifact changes session behavior

→ Full reference: **[docs/REFERENCE.md](docs/REFERENCE.md)**
