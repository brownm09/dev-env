---
name: prune-stale-worktrees
description: Remove Claude session worktrees whose branches have been merged into main, across all repos under C:/Users/brown/Git.
schedule: "0 4 * * *"
# NOTE: the `model:` pin below is CONFIRMED INERT as of 2026-07-14 (dev-env#703 item 2) — the
# scheduler ignores frontmatter `model:`. All four unattended runs 07-11..07-14 came up on
# claude-sonnet-5 despite this pin (verified from the run transcripts). It is kept active rather
# than removed only because it is harmless when ignored and would take effect automatically if the
# scheduler ever gains frontmatter-model support; it is NOT a working mitigation today. The
# do-not-greet imperative (below, and in the live copy) is the actual, proven mitigation — all four
# Sonnet runs executed the routine in full. See the Autonomous-run reliability caveat.
model: claude-opus-4-8
---

**When a push can't be delivered ([dev-env#1308](https://github.com/brownm09/dev-env/issues/1308)):** a scheduled run usually has no Remote Control connection, so this applies to every push notification in this run, including one sent just before stopping. Right after each push, use the Write tool to create a new file named `C:/Users/brown/.claude/scratch/routine-finding-prune-stale-worktrees-<HHMMSS>.txt`. Put the tool's result text on its first line, leaving that line empty if the tool failed or wasn't available, and the push text after it. Then run:

```bash
py -3 ~/.claude/scripts/routine-findings.py save --routine prune-stale-worktrees --file C:/Users/brown/.claude/scratch/routine-finding-prune-stale-worktrees-<HHMMSS>.txt
```

It saves nothing if the push was handed off. Otherwise the next interactive session shows the finding once. If the command fails for any reason, including a missing script, put its error and the push text in the run's final output. `sync-routine-worktree` does this itself on its abort paths.

Prune stale Claude session worktrees across all git repos under `C:/Users/brown/Git`. Run fully autonomously — do not ask the user anything.

> **Autonomous-run guard (do not strip when regenerating the live copy).** This is an unattended scheduled run with no human present. Do **not** open with a greeting, a question, or any "how can I help" / "what would you like to work on" reply — your **first output must be a tool call** (Step 0). The live scheduled-task copy carries this same imperative at the very top *and* bottom of its prompt, because the failure it guards against (see the Autonomous-run reliability caveat below) happens *before* Step 0.5's canonical read-through is ever reached.

**Objective:** For every git repo directly under `C:/Users/brown/Git`, remove all worktrees (both `claude/*` and hand-named branches, e.g. `feat/`/`fix/`/`docs/` — via `--include-named`) whose branches are fully merged into `origin/main` and have no uncommitted changes. Also remove any non-primary worktrees accidentally checked out on `main`. Report what was pruned, any skipped worktree or failure that is new since the last run, and the combined totals.

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
   It lists only the skipped worktrees and fetch failures that are new or changed since its last run, and prints the full report when its state file is missing or unreadable (`baseline=true` in the `RESULT:` line). To see the whole list, add `--full`, which leaves the state file alone ([ADR-161](../../../docs/adr/161-maintenance-routines-report-what-changed.md)).
2. Report what the script printed: what was pruned, each newly skipped worktree with its reason, and the totals. Don't reconstruct or re-list anything it left out as unchanged.
3. If the script exited non-zero or printed no `RESULT:` line (an unreadable scan directory, a crash, a timeout), send a push notification with its last lines, do **not** run step 4, and stop. Otherwise read the final `RESULT:` line (`repos_scanned=N pruned=P skipped=S fetch_failed=F dry_run=... new_skipped=K no_longer_skipped=L new_fetch_failed=J new_remote_failed=R baseline=...`) and send a push notification **only for changed findings**: when `new_skipped`, `new_fetch_failed` or `new_remote_failed` is greater than 0, listing only the newly skipped worktrees and the repos whose fetch or GitHub remote newly failed, as the script printed them. When all three are 0, send no notification, however large `skipped` or `fetch_failed` are, because those totals repeat what was already reported.
4. Once the notification is sent, or none was needed, commit this run's findings so the next run compares against them:
   ```bash
   python "$WORKTREE_ROOT/claude/scripts/_last_run.py" commit C:/Users/brown/.claude/scratch/last-run/prune-merged-worktrees.json
   ```
   A push that wasn't delivered but was saved by `routine-findings.py save` (above) counts as handled. Skip this step only when the finding reached neither: the next run then reports the same changes again.

**Constraints:**
- Script uses `git branch -d` (not `-D`) and `git worktree remove` (no `--force`) — safe by default
- A repo whose GitHub remote can't be read is skipped, and reported when that first happens (`new_remote_failed`)
- Never remove the current session's worktree, regardless of branch name
- With `--include-named`, a hand-named branch is held to the exact same merged/dirty/liveness bar as a `claude/*` branch — no looser check is applied (ADR-078)
- Never remove a worktree with an active Claude session — transcript activity within 24h. This routine runs out-of-process and cannot see other sessions via cwd, so the script's transcript-mtime guard is what protects them (ADR-051). A session whose transcript is under another folder is covered by a second guard on the worktree's own git state, over the same 24h (ADR-051 Amendment 1)
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
> frontmatter pin is the only per-task model lever available. **Confirmed inert (dev-env#703 item 2,
> 2026-07-14):** all four unattended runs 07-11→07-14 came up on `claude-sonnet-5` despite the pin —
> the scheduler does not honor frontmatter `model:`. The do-not-greet imperative is therefore the
> **sole effective, model-agnostic mitigation**, and it is confirmed working: all four Sonnet runs
> executed the routine in full rather than greeting. The pin is kept (annotated inert in the
> frontmatter) only because it is harmless and would activate if the scheduler ever honors it.
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
