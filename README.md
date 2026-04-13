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

Custom skills extend Claude Code with project-aware slash commands. Invoke with `/skill-name`.

| Skill | Command | Description |
|---|---|---|
| [`propose`](claude/skills/propose/SKILL.md) | `/propose <idea>` | Expands a one-line idea into a proposal doc, creates a GitHub issue, and updates ROADMAP.md. Targets `lifting-logbook`. |
| [`journal-compose`](claude/skills/journal-compose/SKILL.md) | `/journal-compose [draft-path]` | Composes the end-of-day engineering journal from the day's draft file. Produces the canonical 11-section document, updates READMEs, commits, and opens a PR. |
| [`research`](claude/skills/research/SKILL.md) | `/research [tag:] <decision> [--compare <alt>]` | Finds 1–3 primary sources for an engineering decision. Greps the shared source library first; spawns a subagent only on a cache miss. |

The source library for `/research` and `/journal-compose` lives at [`claude/skills/sources.md`](claude/skills/sources.md).

## Scripts

Hook scripts run automatically via Claude Code's `hooks` configuration in `settings.json`.

| Script | Trigger | Purpose |
|---|---|---|
| `post-tool-use.py` | After every tool call | Tracks token usage to the session JSONL log |
| `token-tracker.py` | Session start/stop | Writes and closes session records |
| `token-report.py` | On demand | Generates markdown and JSON token usage reports by date |
| `backfill-tokens.py` | On demand | Backfills token data for sessions predating the tracker |
| `pr-merge-reminder.py` | After tool calls | Reminds to open a PR when changes are pushed without one |

## Templates

Document templates consumed by skills at runtime.

| Template | Used by | Purpose |
|---|---|---|
| [`proposal.md`](claude/templates/proposal.md) | `/propose` | PRD template for feature proposals |
| [`pr-body.md`](claude/templates/pr-body.md) | `/propose`, `/journal-compose` | PR body structure guide |
| [`contributing.md`](claude/templates/contributing.md) | Manual / `/propose` | CONTRIBUTING.md template for project repos |

## Adding new configs

1. Add the file under a descriptive directory (e.g., `claude/scripts/`, `claude/skills/`)
2. Add a `ln -sf` or `mklink` line for it in `setup.sh` (if it needs symlinking)
3. Add a row to the relevant table above
4. Update `claude/CLAUDE.md` if the artifact changes session behavior
