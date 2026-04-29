# ADR 005 — Global `core.hooksPath` for Cross-Repo Invariants

**Date:** 2026-04-19  
**Status:** Accepted

---

## Context

Certain pre-push safety checks — specifically the squash-merge divergence guard — are needed in every repo, not just one. Git's per-repo `.git/hooks/pre-push` is not version-controlled and must be manually reinstalled after every clone. Committing hooks to individual repos couples global workflow rules to project-specific codebases.

The alternative (a `post-clone` script or repo template) requires tooling outside the existing Claude Code setup and adds installation friction.

---

## Decision

Set `git config --global core.hooksPath ~/.claude/hooks/` so all repos on the machine run hooks from a single version-controlled location.

The global pre-push hook (`claude/hooks/pre-push`) chains to any per-repo `.git/hooks/pre-push` at the end of its execution, preserving compatibility with Husky, Lefthook, or corporate hook tooling already installed in individual repos.

**Pre-existing `core.hooksPath` check:** Before setting, verify no system-level or tool-managed value already exists (`git config --system core.hooksPath`, `git config --global core.hooksPath`). If another tool owns the global value, coordinate rather than overwrite — two tools cannot share `core.hooksPath`.

---

## Consequences

- All new cross-repo invariant checks go in `claude/hooks/`; they take effect in every repo without per-repo installation.
- Per-repo hooks (`.git/hooks/`) continue to work — the global hook chains to them.
- `setup.sh` sets `core.hooksPath` as part of the bootstrap sequence.
- Hook scripts in `claude/hooks/` are version-controlled in dev-env and covered by ADR 003.
- Hook commands in `settings.json` (Claude Code lifecycle hooks) are separate from git hooks and governed by ADR 007.

---

## References

- Engineering journal: `sessions/dev-env/2026-04-19-hook-infrastructure.md`
- `dev-env/setup.sh` — sets `core.hooksPath` during bootstrap
- `claude/hooks/pre-push` — squash-merge divergence guard with per-repo chain
- `claude/CLAUDE.md` § Git Workflow — "Pre-push hook wiring (one-time setup)"
