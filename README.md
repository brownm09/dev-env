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
| [`/review <PR-URL> [flags]`](claude/skills/review/SKILL.md) | Reviews a PR for correctness, security, reliability, and maintainability. Posts report as PR comment by default. |
| [`/cover-letter [JD]`](claude/skills/cover-letter/SKILL.md) | Drafts a cover letter for a job application. Runs fit screening and style check as Haiku subagents. |
| [`/fit-screen [JD]`](claude/skills/fit-screen/SKILL.md) | Fit-screens a job description. Returns PROCEED / FLAG / SKIP. |

## Hooks

Hook scripts run automatically via Claude Code's `hooks` configuration in `claude/settings.json`.
All hooks are advisory — none block tool execution.

| Event | Script | Purpose |
|---|---|---|
| UserPromptSubmit | `dev-env-sync.py` | Fast-forward pulls dev-env to `origin/main` at session start |
| UserPromptSubmit | `new-day-journal-check.py` | Warns if stale `draft/*` journal branches exist on origin |
| UserPromptSubmit | `turn-count-hook.py` | Warns when session context token count exceeds threshold |
| UserPromptSubmit | `multi-worktree-alert.py` | Lists active worktrees in `repo:branch` format when ≥2 are open |
| PreToolUse (Bash) | `pre-commit-branch-check.py` | Emits current branch as a checkpoint before `git commit` |
| PreToolUse (Bash) | `pre-pr-create-check.py` | Emits test-verification checklist before `gh pr create` |
| PostToolUse (Bash) | `pr-merge-reminder.py` | Reminds to write a journal stub after `gh pr create` or `gh pr merge` |
| PostToolUse (Bash) | `post-tool-use.py` | Auto-adds issues/PRs to configured GitHub Project |
| PostToolUse (Bash) | `post-pr-merge-pull.py` | Fast-forwards local `main` after `gh pr merge` |
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
| Sunday midnight UTC | `prune-stale-worktrees` | Removes merged `claude/*` worktrees |
| Nightly 3:00 AM local | `nightly-research` | Researches pending topics from the queue and writes structured markdown notes to `research-notes/` |

## Adding new configs

1. Add the file under a descriptive directory (e.g., `claude/scripts/`, `claude/skills/`)
2. Add a `ln -sf` or `mklink` line for it in `setup.sh` (if it needs symlinking)
3. Update the relevant table above **and** the corresponding section in [`docs/REFERENCE.md`](docs/REFERENCE.md)
4. Update `claude/CLAUDE.md` if the artifact changes session behavior

→ Full reference: **[docs/REFERENCE.md](docs/REFERENCE.md)**
