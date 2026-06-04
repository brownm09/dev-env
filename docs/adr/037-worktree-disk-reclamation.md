# ADR-037: Automated Disk Reclamation for Idle Worktree node_modules

**Date:** 2026-06-03
**Status:** Accepted
**Tags:** disk, worktrees, node_modules, hooks, routines, UserPromptSubmit, saturation, automation

---

## Context

On 2026-06-03 the `C:` drive filled to 100% (476 GB used, 0 bytes free) mid-session,
hard-blocking all work: `npm install`, `npm run build`, git commits, Prisma migrations, and
even writing a plan file failed with `ENOSPC: no space left on device` (dev-env#306).

Measured root cause: **33 Claude-managed git worktrees** under
`lifting-logbook/.claude/worktrees/`, each carrying its own full monorepo `node_modules`. The
project mandates `npm install` in every worktree (its Husky pre-commit hook needs a local
`turbo` binary), so at ~1–2 GB per worktree that is an estimated 30–60 GB of duplicated,
mostly-stale dependencies. Docker (~4.7 GB) and temp dirs (~1 GB) were not meaningful
contributors.

Existing tooling did not keep pace:

- `prune-merged-worktrees.py` / the daily `prune-stale-worktrees` routine
  ([ADR-015](015-suppress-hook-noise-in-claude-worktrees.md) context) removes a worktree
  *directory* only once its branch is **merged** — and only then is its `node_modules`
  reclaimed. Idle-but-unmerged worktrees retain their full `node_modules` indefinitely.
- A daily/weekly cadence cannot prevent saturation that accumulates over a few days of heavy
  worktree creation.

Key enabling fact: `node_modules` and `.turbo` are fully **regenerable**.
`worktree-npm-install.py` ([ADR-016](016-worktree-npm-auto-install.md)) already reinstalls
`node_modules` on the next prompt in any Claude-managed worktree. So stripping these
artifacts from an idle worktree is self-healing — the worktree simply reinstalls when next
used. This property holds *only* for worktrees under `.claude/worktrees/` (that is the path
the auto-install hook keys on); manual sibling clones do not auto-reinstall.

## Decision

Add three coordinated artifacts that reclaim regenerable artifacts from idle Claude-managed
worktrees automatically — on a frequent schedule **and** as a free-space safety net.

### 1. Core script — `claude/scripts/reclaim-worktree-disk.py`

Strips `node_modules` (top-level and nested monorepo package dirs) and `.turbo` from
**eligible** worktrees. A worktree is eligible only when **all** hold:

- it is under `.claude/worktrees/` (Claude-managed → auto-reinstall safety net applies);
- it is not the primary worktree and not the protected cwd (the active session);
- its working tree is clean (`git status --porcelain` empty); **and**
- its branch is merged into `origin/main` **OR** has zero commits ahead of `origin/main`.

Dirty worktrees and worktrees with unpushed commits ahead of main are never touched. The
script mirrors the structure and safety helpers of `prune-merged-worktrees.py`
(`--dry-run` / `--repo-path` / `--scan-dir`, origin-slug auto-detection) and adds
`--min-free-gb N` (no-op unless the drive is below N GB) and `--protect-cwd PATH`. It deletes
only regenerable directories — never the worktree, branch, or git state.

The eligibility gate is **path-based** (`.claude/worktrees/`), not branch-prefix-based
(`claude/*` as the prune script uses). Reclamation only deletes regenerable artifacts, so the
branch name is irrelevant; what matters is whether the auto-reinstall safety net covers the
worktree. This correctly includes Claude worktrees with `feat/*`/`config/*` branches and
excludes manual sibling clones.

### 2. Threshold hook — `claude/scripts/disk-space-check.py` (UserPromptSubmit)

Cheap `shutil.disk_usage("C:/")` check on every prompt:

- **< 20 GB free:** emit a one-time `systemMessage` warning.
- **< 10 GB free:** spawn the core script detached
  (`--scan-dir C:/Users/brown/Git --min-free-gb 10 --protect-cwd <cwd>`) so the heavy delete
  never blocks the prompt, and emit a `systemMessage`.

Advisory only (exit 0 always; exceptions swallowed). Each band fires at most once per session
via a `session_id`-keyed marker file, per [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md).
The detached spawn uses `sys.executable` (not the `py` launcher) to avoid the grandchild
console flash (dev-env#300), enforced by `tests/test_pyw_stdio.py`.

### 3. Routine — `claude/routines/reclaim-worktree-disk` (cron `0 */6 * * *`)

Runs the core script in `--scan-dir` mode every 6 hours, filling the gap between daily prune
runs. Invokes `sync-routine-worktree` ([ADR-013](013-sync-routine-worktree-skill.md)) as
Step 0 and push-notifies when ≥ 1 GB is reclaimed.

Thresholds (20/10 GB) and cadence (6 h) are hardcoded named constants — this is single-machine
global config, so constants are simpler than a `hook-config.json` field. They are easily
promoted to config later if tuning-without-code-change becomes necessary.

## Consequences

- Idle worktree `node_modules`/`.turbo` is reclaimed both proactively (every 6 h) and
  reactively (below 10 GB free), so the disk no longer drifts to saturation between weekly
  prunes.
- Worktrees with uncommitted or unpushed-ahead work are never touched — only trivially
  regenerable artifacts belonging to idle work are removed.
- A worktree whose artifacts were stripped pays a one-time `npm ci`/`install` on next use
  (via ADR-016) — the same cost as a fresh worktree, and only if it is reused.
- The `disk-space-check.py` hook adds three `disk_usage`/`exists` checks per prompt —
  negligible. It spawns reclamation at most once per session per band.
- This is the third hook to spawn a detached Python process (after `awake-blocker.py`); the
  `sys.executable`-not-`py` convention (dev-env#300) is now load-bearing for two scripts.
- Reclamation does **not** remove the worktree directory itself — that remains the job of
  `prune-stale-worktrees`. The two are complementary: prune removes merged worktrees; reclaim
  shrinks idle ones that are not yet removable.

## References

- [ADR-016](016-worktree-npm-auto-install.md) — `worktree-npm-install.py`, the auto-reinstall
  counterpart that makes reclamation self-healing
- [ADR-015](015-suppress-hook-noise-in-claude-worktrees.md) — the `.claude/worktrees` path
  detection pattern reused for the eligibility gate
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — UserPromptSubmit advisory
  output and `session_id`-keyed per-session marker conventions
- [ADR-013](013-sync-routine-worktree-skill.md) — `sync-routine-worktree` Step-0 preamble for
  routines that read repo-resident files
- [ADR-033](033-prevent-system-sleep-while-processing.md) / dev-env#300 — the detached
  `sys.executable` spawn pattern (windowless, no grandchild console flash)
- dev-env#306 — the saturation incident this ADR remediates
