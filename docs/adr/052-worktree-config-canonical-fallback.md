# ADR-052 — Worktree Config Resolution: Canonical-Checkout Fallback for the Project-Add Hook

**Date:** 2026-06-22
**Status:** Accepted
**Tags:** hooks, post-tool-use, worktree, hook-config, github-project, gitignore, automation, reliability

---

## Context

`post-tool-use.py` fires after `gh issue create` / `gh pr create`, adds the new item to the configured GitHub Project, and exits 2 with the `gh project item-edit` commands for the project's `required_fields` (for dev-env, Impact/Why). It is opt-in per project via `.claude/hook-config.json` ([ADR-023](023-generic-required-fields-issue-hook.md)).

Even after [ADR-049](049-hook-payload-output-field.md) made the hook read command output from `stdout` (so it fires *at all*), it still silently no-op'd in **worktree sessions**:

- The hook reads its config with `load_config(cwd)`, which opened `<cwd>/.claude/hook-config.json`.
- That file is **gitignored** — `.gitignore` ignores the whole `.claude/` directory — so it is machine-local and lives only in the canonical checkout. [`git worktree add`](https://git-scm.com/docs/git-worktree) never checks out an ignored, untracked file, and the harness copies it into Claude-managed worktrees only **inconsistently** (≈ half of live worktrees, and **never** into sibling worktrees such as `dev-env-188`).
- When the file was absent, `load_config` returned `None` and the hook hit `if config is None: sys.exit(0)` — a silent exit *before* the project-add and *before* the Impact/Why exit-2 reminder. The session proceeded to file edits with no board item and no field prompt, forcing the manual fallback documented in `CLAUDE.md → GitHub Project`. Observed twice on 2026-06-20 (issues #369, #371).

This is **not** [ADR-015](015-suppress-hook-noise-in-claude-worktrees.md): that worktree guard lives only in the two `UserPromptSubmit` hooks; `post-tool-use.py` has no worktree guard and its output is not suppressed. The failure is purely that the config could not be found.

## Decision

`load_config(cwd)` resolves the **canonical checkout's** config when the cwd-local copy is absent, so worktree sessions behave like main-checkout sessions:

1. **Read the cwd-local copy first.** A present worktree-local copy (the ≈ half that get it) and every main-checkout / non-worktree session are unchanged. A project with no config *anywhere* still silently skips — opt-in is preserved.
2. **Claude-managed worktree → pure regex.** For a cwd under `<root>/.claude/worktrees/<name>`, `canonical_root_from_worktree` captures `<root>` with the proven prefix regex from [`pre-tool-use-worktree-path-check.py`](024-worktree-path-guard-hook.md) (tolerates `/` and `\`). It is pure (no I/O), so it is exercised offline by the unit tests, and it preserves the cwd path format verbatim before `os.path.join` — no Git-Bash→Node path re-resolution ([#334](https://github.com/brownm09/dev-env/issues/334)) hazard.
3. **Sibling worktree → git.** A sibling worktree (e.g. `dev-env-188`) is not under `.claude/worktrees/`, so the regex cannot derive its root. `canonical_root_via_git` runs [`git rev-parse --git-common-dir`](https://git-scm.com/docs/git-rev-parse#Documentation/git-rev-parse.txt---git-common-dir); the common dir is the canonical checkout's `.git`, whose parent is the canonical root. It returns `None` on any git failure (missing binary, timeout, non-zero exit), so `load_config` degrades to the same silent skip rather than raising.
4. **Cover it offline.** `claude/scripts/tests/test_post_tool_use.py` pins `canonical_root_from_worktree` across separators, a subdir, a main checkout, a sibling, a POSIX path, and empty/None, plus a hermetic temp-dir test of `load_config`'s canonical-worktree fallback end-to-end. The sibling path's resolution is extracted into the pure `_canonical_root_from_common_dir(cwd, common)` — also unit-tested (relative `.git`, absolute `<root>/.git`, whitespace-stripped stdout, non-`.git` basename, empty) — so only the `subprocess.run` wrapper in `canonical_root_via_git` shells out and is left uncovered per the repo's fixture-only convention (cf. `add_to_project`); it was validated once by a smoke test against a real config-less `dev-env-188`.

## Consequences

- Issue/PR creation in worktree sessions now reliably adds to project #3 and surfaces the Impact/Why prompt — eliminating the manual board-add the hook was meant to remove, for the worktree case it had always missed.
- Both worktree shapes are handled; the common case (Claude-managed worktree) costs zero extra subprocess, and git runs only on the rare sibling-worktree miss.
- The gitignored-machine-local-config-in-worktree failure mode is recorded once here so future hooks that read repo-local config anticipate it.
- **Out of scope:** a separate, deeper harness-level cause where PostToolUse hooks do not execute *at all* in some sessions ([#381](https://github.com/brownm09/dev-env/issues/381)) is orthogonal to config resolution and is not addressed here. This ADR makes the hook correct *when it runs*.

**Family:** [ADR-015](015-suppress-hook-noise-in-claude-worktrees.md) (worktree hook noise), [ADR-023](023-generic-required-fields-issue-hook.md) (config opt-in), [ADR-024](024-worktree-path-guard-hook.md) (source of the prefix regex), [ADR-049](049-hook-payload-output-field.md) (the prior fix that made the hook fire), [ADR-050](050-shared-hookio-sibling-hook-fixes.md) (sibling-hook fixes).

## Amendment (2026-07-02) — "gitignored" is dev-env's own convention, not universal

Context line 16 and Consequences line 34 above state flatly that the config "is gitignored." That was true of every project this ADR's author had checked at the time, but it is a per-project convention, not a property of `hook-config.json` itself: dev-env's own `.gitignore` ignores all of `.claude/`, but lifting-logbook deliberately tracks `.claude/hook-config.json` in git (so its Epic-ID table stays reviewable in PRs — see that repo's CLAUDE.md Backup-and-restore procedure). Discovered via [dev-env#527](https://github.com/brownm09/dev-env/issues/527), alongside [lifting-logbook#628](https://github.com/brownm09/lifting-logbook/pull/628).

This does not change this ADR's Decision: the worktree-canonical fallback logic here is correct and necessary regardless — it is simply a no-op for a project that tracks the file, since `git worktree add` checks out a tracked file normally and the cwd-local read on `load_config`'s first line already finds it (no fallback branch is ever reached). The fallback only *matters* for projects following dev-env's own gitignore convention. See [ADR-076](076-live-fetch-project-hook-single-select-options.md) for the related fix to the *cache-freshness* side of this same file (live-fetching `single_select` options rather than trusting a value that has no invalidation mechanism, gitignored or not).
