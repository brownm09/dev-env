# dev-env

Development environment configuration for cross-device use.

## Contents

| Path | Linked to | Purpose |
|---|---|---|
| `claude/CLAUDE.md` | `~/.claude/CLAUDE.md` | Claude Code global configuration |

## Setup

Clone the repo and run the setup script once on each machine:

```bash
git clone https://github.com/brownm09/dev-env.git ~/Git/dev-env
cd ~/Git/dev-env
bash setup.sh
```

The script creates symlinks from the expected config locations into this repo.
Any edits made through those symlinks update the repo file directly.

## Adding new configs

1. Add the file under a descriptive directory (e.g., `git/`, `vscode/`)
2. Add a `ln -sf` line for it in `setup.sh`
3. Add a row to the table above
