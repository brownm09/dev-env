---
name: reclaim-worktree-disk
description: Strip regenerable node_modules/.turbo from idle Claude worktrees across all repos under C:/Users/brown/Git, reclaiming disk between weekly prune runs.
schedule: "0 */6 * * *"
---

Reclaim regenerable disk artifacts from idle Claude session worktrees across all git repos under `C:/Users/brown/Git`. Run fully autonomously — do not ask the user anything.

> **Autonomous-run guard (do not strip when regenerating the live copy).** This is an unattended
> scheduled run with no human present. Do **not** open with a greeting, a question, or any "how can I
> help" / "what would you like to work on" reply — your **first output must be a tool call** (begin with
> the first step below). The live scheduled-task copy must carry this same imperative at the very top
> *and* bottom of its prompt, because the greeting-instead-of-execute failure it guards against happens
> *before* any canonical read-through step is reached. Rationale and incident history: the
> [`prune-stale-worktrees` reliability caveat](../prune-stale-worktrees/SKILL.md),
> [dev-env#698](https://github.com/brownm09/dev-env/issues/698), and
> [dev-env#703](https://github.com/brownm09/dev-env/issues/703) (which confirmed the frontmatter `model:`
> pin is **inert** — the scheduler ignores it — making this imperative the sole effective, model-agnostic
> mitigation). See the **Restorable live-copy imperative** at the bottom of this file.

**Objective:** For every git repo directly under `C:/Users/brown/Git`, strip `node_modules` and `.turbo` from Claude-managed worktrees (those under `.claude/worktrees/`) that are **idle** — clean working tree **and** (branch merged into `origin/main` **or** zero commits ahead of `origin/main`). These artifacts are regenerable: `worktree-npm-install.py` (ADR-016) reinstalls `node_modules` on the next prompt in any Claude-managed worktree, so reclamation is self-healing. This reclaims the bulk of duplicated dependencies without removing the worktree, preventing C: saturation between the weekly `prune-stale-worktrees` runs (dev-env#306). Report reclaimed totals per repo and a combined total.

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`, `VERIFY_FILE=claude/scripts/reclaim-worktree-disk.py`, `PREFIX=reclaim-worktree-disk`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the reclaim script in scan-dir mode:
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/reclaim-worktree-disk.py" \
     --scan-dir C:/Users/brown/Git
   ```
2. Report the per-repo output: how much was reclaimed in each repo, and the reason for each skipped worktree.
3. If the combined total reclaimed is meaningful (≥ 1 GB), send a push notification summarizing the amount reclaimed so the user has visibility into disk hygiene.

**Constraints:**
- The script deletes only `node_modules` and `.turbo` — never the worktree, branch, or any git state.
- Only worktrees under `.claude/worktrees/` are considered (they auto-reinstall via ADR-016). The primary repo and manual sibling worktrees are left untouched.
- Dirty worktrees, the primary worktree, the current/protected worktree, worktrees with an active Claude session (transcript activity within 6h — shorter than prune's 24h since stripping is self-healing; ADR-051), and worktrees with unpushed commits ahead of `origin/main` (and not merged) are never touched.
- Repos with no GitHub remote are silently skipped.
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`.

---

> **Dual-copy registration caveat (dev-env#344, [ADR-003 amendment](../../../docs/adr/003-config-in-version-control.md)).**
> This file is the **canonical, version-controlled** definition
> (`dev-env/claude/routines/reclaim-worktree-disk/`, surfaced at
> `~/.claude/routines/reclaim-worktree-disk/` via the directory junction). The scheduler reads a
> **separate** live copy at `~/.claude/scheduled-tasks/reclaim-worktree-disk/SKILL.md`, materialized
> by the `create_scheduled_task` MCP tool — the two do **not** auto-sync by default. This routine was
> authored with its intended `0 */6 * * *` schedule above but was never actually registered as a
> live task at all ([dev-env#597](https://github.com/brownm09/dev-env/issues/597)) — its
> self-healing disk reclamation had, as far as that investigation could tell, never run
> automatically. Per the convention ADR-003's amendment establishes, the live copy now reads this
> file at run time and defers to it when present, falling back to an embedded copy only when it is
> missing or unreadable.

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It lives verbatim
only in the machine-local live copy (`~/.claude/scheduled-tasks/reclaim-worktree-disk/SKILL.md`, which
is **not** version-controlled), so the exact deployed strings are captured here — a machine rebuild, or
a live-copy regeneration from this canonical file, restores the hardened guard **deterministically**
rather than reconstructing it from memory. When (re)creating the live copy, paste the **top** block as
its first line (immediately after the YAML frontmatter) and the **bottom** block as its last line; keep
both verbatim, including the ASCII `--` in the top block and the em dash in the bottom block.

_Top — first line of the live prompt:_

```text
EXECUTE NOW -- DO NOT GREET. This is an autonomous scheduled run; no human is present. Do NOT reply with a greeting, a question, or any variant of "how can I help" / "what would you like to work on" -- a concrete task is defined below and your FIRST output MUST be a tool call (begin with the first step below). If you catch yourself about to acknowledge, greet, or ask what to do, stop and begin executing the first step instead.
```

_Bottom — last line of the live prompt:_

```text
REMINDER: Begin immediately. Your first action is a tool call for the first step below — not a text reply. Do not greet or ask what to work on.
```
