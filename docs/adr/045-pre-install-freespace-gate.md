# ADR-045: Pre-Install Free-Space Gate + Prompt Post-Merge Reclamation

**Date:** 2026-06-18
**Status:** Accepted
**Tags:** disk, worktrees, node_modules, hooks, ENOSPC, npm-install, post-merge, runbook

---

## Context

On 2026-06-17 the `C:` drive reached **0 GB free** mid-`npm install` in a Claude-managed worktree
(dev-env#364). As in the dev-env#306 incident that motivated [ADR-037](037-worktree-disk-reclamation.md),
the exhaustion surfaced **indirectly** — not as a "disk full" dialog but as *truncated* native
dependencies: a background `npm install` reported **exit 0** while leaving packages partially extracted.
The downstream failures were confidently misleading and cost hours to diagnose:

- Jest's `Preset ts-jest not found` was really `bs-logger/dist/index.js` `MODULE_NOT_FOUND` deep in
  ts-jest's load chain.
- `@next/swc-win32-x64-msvc` was truncated to **32.5 MB** (valid binary is **136.8 MB**), producing a
  downstream `next.config.compiled.js` "Unexpected token 'export'" and failing all 15 Playwright tests
  with `ERR_CONNECTION_REFUSED`.

Node was 20.11.1 throughout, so this was genuine disk exhaustion — **not** the Node-24 incomplete-tarball
issue (lifting-logbook#373), a distinct root cause with overlapping symptoms.

**Why ADR-037's defenses were necessary but not sufficient.** ADR-037 already shipped exactly the
free-space early-warning the issue asks for — `disk-space-check.py` (warn < 20 GB, auto-reclaim < 10 GB)
and the 6-hourly `reclaim-worktree-disk` routine. Yet the disk still saturated two weeks later, because:

1. **`disk-space-check.py` only samples at prompt boundaries.** A long `npm install` runs *between*
   prompts; free space can fall from "tight" to zero during a single install with no prompt boundary to
   re-trigger the check.
2. **`worktree-npm-install.py` (ADR-016) ran the install unattended with no free-space pre-check.** It
   inspected only the install's return code — and npm can return 0 on a truncated extraction, so the
   corrupted tree passed as success. This is the actual corruption vector.
3. **ADR-037's reclamation is spawned *detached*** (so it never blocks a prompt). When free space is
   already critical, a detached reclaim *races* an install already in flight rather than preventing it.

**Dominant consumer (acceptance criterion 1), measured 2026-06-18.** `lifting-logbook/.claude/worktrees/`
held **60 worktrees** totalling **~14 GB** of `node_modules` (a direct `du`), averaging ~230 MB each —
*not* every worktree carries a full install, because ADR-037's routine reclaims idle ones; a
freshly-installed monorepo tree is ~1–2 GB, which is the per-worktree *upper* bound, not the typical
footprint. The project mandates a per-worktree install for its Husky `turbo` binary. Secondary:
lifting-logbook's own `node_modules` (~858 MB), Docker (~5.9 GB), Playwright (~685 MB), npm cache
(~684 MB). The dev-env worktrees themselves were negligible (~17 MB, no `node_modules`). At ~14 GB the
worktree bucket is still the largest single consumer — more than twice Docker's ~5.9 GB — confirming
ADR-037's diagnosis that idle worktree `node_modules` is the lever. (An earlier draft of this ADR cited
the ~1–2 GB-per-worktree upper bound as a ~30–60 GB aggregate; that was an extrapolation, not the
measured total — corrected to the measured ~14 GB per dev-env#366.)

## Decision

Two coordinated changes, plus a recovery runbook. Both extend — do not supersede — ADR-037.

### 1. Pre-install free-space gate with an escalation ladder — `worktree-npm-install.py`

Before running `npm ci`/`npm install`, the hook gates on free `C:` space via a pure, unit-tested
`install_decision(free_gb, reclaimed_free_gb)` helper:

- **≥ 10 GB free (`INSTALL_FLOOR_GB`):** install as before.
- **< 10 GB:** run a **synchronous** reclamation ladder, re-measuring after each rung:
  1. **Tier 1** — `reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --min-free-gb 10
     --protect-cwd <cwd>` (the existing script; strips regenerable `node_modules`/`.turbo` from idle
     eligible worktrees).
  2. **Tier 2** — `npm cache clean --force` (~700 MB, fully regenerable).
  - Recovered to **≥ 5 GB (`HARD_FLOOR_GB`):** install.
  - Still **< 5 GB:** **refuse the install** and emit a loud advisory naming the still-low figure and the
    heavier *manual* levers (`docker system prune`, worktree pruning) + the recovery runbook.

Reclamation is **synchronous** here (unlike `disk-space-check.py`'s detached spawn) precisely because the
install it guards is synchronous — a detached reclaim would race the very install it is meant to protect.
The gate **fails open**: any `disk_usage` error returns `proceed`, so the gate is never the reason an
install does not run. `docker system prune` is deliberately *excluded* from the automatic ladder — it
deletes images/volumes that may be costly to rebuild and is not transparently regenerable.

This converts a *silent corruption that costs hours* into *escalating self-repair, then a one-prompt
refusal*.

### 2. Prompt post-merge reclamation — `post-pr-merge-reclaim.py` (PostToolUse, Bash)

A merged worktree's branch is merged into `origin/main`, so its `node_modules` is immediately eligible
(ADR-037) and regenerable (ADR-016). Today it lingers up to 6 h until the routine runs. The new hook,
wired alongside the other post-merge hooks, detects a successful `gh pr merge` (via the pure
`is_successful_merge()` predicate, trusting stdout success markers because worktree merges exit non-zero
on local cleanup — same reasoning as `post-pr-merge-pull.py`/issue#275) and spawns
`reclaim-worktree-disk.py --scan-dir C:/Users/brown/Git --protect-cwd <cwd>` **detached** (no
`--min-free-gb` — the trigger is the idle event, not low space). It follows the ADR-007 windowless spawn
convention (`sys.executable`, never the `py` launcher).

**Why this hook does *not* remove the worktree directory/branch (a deliberate boundary).** Removing the
merged worktree's directory + branch cannot happen from within the live session that lives in it:

- Windows holds an OS lock on a process's current directory, so `git worktree remove` / `rmdir` against
  the active worktree fail with a sharing violation; even if removal succeeded, the session would be
  stranded on an invalid cwd.
- A branch checked out in a worktree cannot be deleted, and `gh pr merge --delete-branch` from a worktree
  aborts on `main is already checked out` in the canonical clone.
- A hook subprocess cannot drive the `EnterWorktree`/`ExitWorktree` tools (only the model can, and
  `ExitWorktree` is a no-op after `/compact`).

Deleting `node_modules`, by contrast, is a plain file delete valid even for the active worktree (it
reinstalls on next use), which is why prompt post-merge reclamation is safe. **Directory + branch removal
therefore stays the daily, out-of-process `prune-stale-worktrees` routine's job.** With `node_modules`
(the bulk) reclaimed promptly on merge, the lingering directory shell is tiny and is removed within a day.

### 3. Recovery runbook + failure signature — `docs/REFERENCE.md`

A new "Disk-Full (ENOSPC) Recovery" section captures the truncated-binary failure signature
(acceptance criterion 4), the dominant-consumer table (criterion 1), and the manual recovery steps
(criterion 3), cross-referenced from `claude/CLAUDE.md` → Platform & Environment.

Thresholds (10 / 5 GB) stay hardcoded named constants, consistent with ADR-037's deliberate choice
(single-machine global config; promote to `hook-config.json` only if tuning-without-code-change is needed).

## Consequences

- An unattended worktree install can no longer silently truncate `node_modules` on a near-full disk — it
  reclaims first, then refuses below the hard floor with an actionable message.
- The dominant consumer (idle worktree `node_modules`) is now reclaimed at three moments: proactively
  (6-hourly routine), reactively (disk-space-check < 10 GB), and **at the idle event** (post-merge) — and
  the heaviest install is gated on free space.
- Both hooks are fail-safe (safe-exit guard, exit 0 always); the post-merge reclaim is detached +
  `--protect-cwd`, so it never blocks a merge or touches the active worktree.
- `post-pr-merge-reclaim.py` is the third hook to spawn a detached Python process via `sys.executable`
  (after `awake-blocker.py` and `disk-space-check.py`); the dev-env#300 convention remains load-bearing.
- Two new offline helper tests (`tests/test_worktree_npm_install.py`, `tests/test_post_pr_merge_reclaim.py`)
  pin the pure decision logic; added to the project `## Testing` section.

## References

- [ADR-037](037-worktree-disk-reclamation.md) — the disk-reclamation foundation this extends
  (`reclaim-worktree-disk.py`, `disk-space-check.py`, the routine)
- [ADR-016](016-worktree-npm-auto-install.md) — `worktree-npm-install.py`, the auto-install hook gated here
- [ADR-007](007-hook-command-invocation.md) / dev-env#300 — windowless detached `sys.executable` spawn
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — advisory exit-0 / safe-exit conventions
- [ADR-034](034-error-message-diligence.md) — read past the misleading top-line error (the ts-jest case)
- dev-env#364 — the saturation/truncation incident this ADR remediates; lifting-logbook#373 — the distinct
  Node-24 incomplete-tarball failure with overlapping symptoms
