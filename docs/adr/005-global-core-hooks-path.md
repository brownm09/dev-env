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

## Amendment 1 (2026-11-06): a clone's local `core.hooksPath` bypasses this decision

The engineering-journal checkout had a local `core.hooksPath` pointing at its own `.git/hooks`, which held only `*.sample` files. Git's [`core.hooksPath`](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corehooksPath) replaces the hooks directory, and local config beats global, so `claude/hooks/pre-push` never ran there. A push to the already-merged `draft/2026-10-30` went through on 2026-11-06 ([dev-env#1261](https://github.com/brownm09/dev-env/issues/1261)).

**Where the value came from: not proven, and probably an older Claude Code.**

- dev-env doesn't set it. `setup.sh` and `setup-elevated.ps1` only write the global value.
- The desktop app bundle (4.16336.0.0) doesn't write it. Its only uses of the setting are `-c core.hooksPath=<null device>` flags that turn hooks off for a single command.
- The installed Claude Code CLI (2.1.309 and 2.1.317) doesn't create it from nothing. Its worktree setup reads the main repo's value and, only if that value is relative, writes back the absolute form. With no local value it writes nothing.
- The same function still builds a `<repo>/.husky` path it no longer uses. Together with the values found in [dev-env#1108](https://github.com/brownm09/dev-env/issues/1108) (absolute, `<repo>\.git\hooks` or `<repo>\.husky`), that points to older versions picking one of those two directories at worktree creation. No older binary was available to confirm this.

**Decision: remove it in the journal checkout rather than route the guard another way.** Nothing needs the value: the directory held no active hooks, and no dev-env script depends on it. `claude/hooks/` holds only `pre-push`, so turning global hooks on can't affect the journal's commits. Every scripted push still works: stub pushes target today's draft, which the guard allows; `reconcile-late-stubs.py` and `merge-stale-pr.sh` push to branches with no merged PR; branch deletes skip the guard; and `/journal-compose` already handles the guard's refusal.

**Alternatives rejected.** Adding a chaining `pre-push` to the journal's `.git/hooks` would keep a per-clone file that doesn't survive a fresh clone, which is the problem this ADR set out to avoid. Having `setup.sh` unset local values would overwrite overrides that may be deliberate, such as a repo's own Husky setup. The doctor names the fix instead, and the person running it decides.

**Scope.** This changes the journal checkout only. dev-env's canonical checkout still has the same override, tracked in [dev-env#1108](https://github.com/brownm09/dev-env/issues/1108).

---

## References

- Engineering journal: `sessions/dev-env/2026-04-19-hook-infrastructure.md`
- `dev-env/setup.sh` — sets `core.hooksPath` during bootstrap
- `claude/hooks/pre-push` — squash-merge divergence guard with per-repo chain
- `claude/CLAUDE.md` § Git Workflow — "Pre-push hook wiring (one-time setup)"
