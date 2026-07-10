---
name: prune-stale-worktrees
description: Remove Claude session worktrees whose branches have been merged into main, across all repos under C:/Users/brown/Git.
schedule: "0 8 * * *"
model: claude-opus-4-8
---

Prune stale Claude session worktrees across all git repos under `C:/Users/brown/Git`. Run fully autonomously — do not ask the user anything.

> **Autonomous-run guard (do not strip when regenerating the live copy).** This is an unattended scheduled run with no human present. Do **not** open with a greeting, a question, or any "how can I help" / "what would you like to work on" reply — your **first output must be a tool call** (Step 0). The live scheduled-task copy carries this same imperative at the very top *and* bottom of its prompt, because the failure it guards against (see the Autonomous-run reliability caveat below) happens *before* Step 0.5's canonical read-through is ever reached.

**Objective:** For every git repo directly under `C:/Users/brown/Git`, remove all worktrees (both `claude/*` and hand-named branches, e.g. `feat/`/`fix/`/`docs/` — via `--include-named`) whose branches are fully merged into `origin/main` and have no uncommitted changes. Also remove any non-primary worktrees accidentally checked out on `main`. Report the pruned/skipped summary per repo and a combined total.

**Steps:**
0. Determine the current worktree's git root, then sync it to `origin/main`:
   ```bash
   WORKTREE_ROOT=$(git rev-parse --show-toplevel)
   ```
   Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`, `VERIFY_FILE=claude/scripts/prune-merged-worktrees.py`, `PREFIX=prune-stale-worktrees`.
   - If it returns **ABORT**, stop — the push notification has already been sent.
1. Run the prune script in scan-dir mode, including named (non-`claude/*`) branches
   ([ADR-078](../../../docs/adr/078-opt-in-named-branch-worktree-pruning.md)):
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/prune-merged-worktrees.py" \
     --scan-dir C:/Users/brown/Git --include-named
   ```
2. Report the per-repo output: how many worktrees were pruned and skipped in each repo, and the reason for each skip.
3. If any repo shows `claude/*` branches skipped due to "not merged" status, list them and send a push notification summarizing the count and branch names so the user can investigate.

**Constraints:**
- Script uses `git branch -d` (not `-D`) and `git worktree remove` (no `--force`) — safe by default
- Repos with no GitHub remote are silently skipped
- Never remove the current session's worktree, regardless of branch name
- With `--include-named`, a hand-named branch is held to the exact same merged/dirty/liveness bar as a `claude/*` branch — no looser check is applied (ADR-078)
- Never remove a worktree with an active Claude session — transcript activity within 24h. This routine runs out-of-process and cannot see other sessions via cwd, so the script's transcript-mtime guard is what protects them (ADR-051)
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`

---

> **Dual-copy registration caveat (dev-env#344, [ADR-003 amendment](../../../docs/adr/003-config-in-version-control.md)).**
> This file is the **canonical, version-controlled** definition
> (`dev-env/claude/routines/prune-stale-worktrees/`, surfaced at
> `~/.claude/routines/prune-stale-worktrees/` via the directory junction). The scheduler reads a
> **separate** live copy at `~/.claude/scheduled-tasks/prune-stale-worktrees/SKILL.md`, materialized
> by the `create_scheduled_task` MCP tool — the two do **not** auto-sync by default. This routine
> already drifted once ([dev-env#597](https://github.com/brownm09/dev-env/issues/597)): the live
> copy ran a stale pre-ADR-078 invocation missing `--include-named`, silently skipping 78 of 88
> registered worktrees every day despite running successfully. Per the convention ADR-003's
> amendment establishes, the live copy now reads this file at run time and defers to it when
> present, falling back to an embedded copy only when it is missing or unreadable — so a future
> edit here takes effect immediately without a separate re-registration step, though the embedded
> fallback should still be refreshed when this file's *steps* change materially.

---

> **Autonomous-run reliability caveat (dev-env#698).** On 2026-07-10 the 04:03 run silently
> no-opped: the model replied *"I'm ready to help. What would you like to work on?"* (0 tool calls)
> despite receiving the full, well-formed prompt. Root cause was a **silent model switch** — the six
> prior daily runs (07-04 → 07-09) all ran on `claude-opus-4-8` and executed correctly (9–13 tool
> calls each); the 07-10 run came up on `claude-sonnet-5` and greeted instead. The scheduler
> **ignores the project `settings.json` model** and uses the app's global default, which began
> resolving to Sonnet 5 that day. Sonnet 5 is not deterministically broken (it ran a *different*
> scheduled task fine the same day) — this is an **intermittent instruction-following lapse** on the
> XML-wrapped autonomous prompt, and a greeting-instead-of-execute run produces **no error and no
> notification**. Two mitigations, both applied: (1) an **execute-now / do-not-greet imperative** at
> the very top and bottom of the *live* scheduled-task prompt — it must live there, not only here,
> because the greeting happens before Step 0.5 reads this canonical file; and (2) a
> `model: claude-opus-4-8` **frontmatter pin** (added to both copies). The MCP
> `update_scheduled_task` / `create_scheduled_task` tools expose no model parameter, so the
> frontmatter pin is the only per-task model lever available — confirm on the next run whether the
> scheduler actually honors it; if not, the imperative alone is the model-agnostic backstop.
> The scheduler-ignores-`settings.json`-model finding is cross-cutting (it affects every scheduled
> task) and is recorded centrally in the [ADR-003 amendment (2026-07-10)](../../../docs/adr/003-config-in-version-control.md)
> and [`docs/REFERENCE.md` → Routines](../../../docs/REFERENCE.md#routines).

---

**Restorable live-copy imperative (dev-env#703 item 3).** The execute-now / do-not-greet mitigation
lives verbatim only in the machine-local live copy
(`~/.claude/scheduled-tasks/prune-stale-worktrees/SKILL.md`, which is **not** version-controlled).
The exact deployed strings are captured here so a machine rebuild — or a live-copy regeneration from
this canonical file — restores the hardened guard **deterministically** rather than reconstructing
it from memory. When (re)creating the live copy, paste the **top** block as its first line
(immediately after the YAML frontmatter) and the **bottom** block as its last line; keep both
verbatim, including the ASCII `--` in the top block and the em dash in the bottom block.

_Top — first line of the live prompt:_

```text
EXECUTE NOW -- DO NOT GREET. This is an autonomous scheduled run; no human is present. Do NOT reply with a greeting, a question, or any variant of "how can I help" / "what would you like to work on" -- a concrete task is defined below and your FIRST output MUST be a tool call (begin with STEP 0). If you catch yourself about to acknowledge, greet, or ask what to do, stop and run the first Bash command instead.
```

_Bottom — last line of the live prompt:_

```text
REMINDER: Begin immediately. Your first action is a tool call for STEP 0 — not a text reply. Do not greet or ask what to work on.
```
