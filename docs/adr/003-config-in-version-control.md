# ADR 003 — Config Artifacts in Version Control via Symlinks

**Date:** 2026-04-13  
**Status:** Accepted

---

## Context

Claude Code's global configuration lives in `~/.claude/` by default. Files edited there are machine-local: invisible to git, unauditable, and prone to drift across reinstalls or machines. Early in this setup, CLAUDE.md and settings.json were edited directly at `~/.claude/`, and changes were occasionally lost or inconsistently applied across sessions.

The challenge: Claude Code reads config from fixed paths (`~/.claude/CLAUDE.md`, `~/.claude/settings.json`) and there is no built-in mechanism to point it elsewhere.

---

## Decision

All version-controlled Claude Code artifacts are maintained in the `dev-env` repo under `claude/` and **symlinked** into `~/.claude/`:

| `~/.claude/` path | Source in `dev-env` |
|---|---|
| `CLAUDE.md` | `claude/CLAUDE.md` |
| `settings.json` | `claude/settings.json` |
| `scripts/` | `claude/scripts/` (directory junction) |
| `skills/` | `claude/skills/` (directory junction) |
| `hooks/` | `claude/hooks/` (directory junction) |
| `scheduled-tasks/` | `claude/routines/` (directory junction) |

Machine-local paths (`scratch/`, `projects/`, `plans/`, `sessions/`, `backups/`, `ide/`, `shell-snapshots/`) are excluded from version control and listed in `.gitignore`.

`setup.sh` creates the symlinks/junctions on a fresh machine.

---

## Consequences

- Every change to global Claude Code config must go through a `dev-env` PR — changes are auditable, reviewable, and rollback-able.
- Editing `~/.claude/CLAUDE.md` directly is incorrect; the source of truth is `dev-env/claude/CLAUDE.md`. Both files carry a comment header warning against direct edits.
- On Windows, directory junctions (`New-Item -ItemType Junction`) are used instead of symlinks for directories, because NTFS symlinks require elevated privileges.
- Machine-local setup (first-time junction creation) is documented in `setup.sh` and must be re-run after a fresh clone.

---

## References

- Engineering journal: `sessions/meta/2026-04-13-post-tool-use-hook-and-settings-into-dev-env.md`
- `dev-env/setup.sh` — bootstrap script for symlinks/junctions
- `claude/CLAUDE.md` § Dev-Env — symlink table and ownership rules
