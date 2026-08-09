---
name: retro-chain-backstop
description: Every day, check whether each tracked repo's retro-action backlog-burn-down chain (dev-env#967) is still alive, and refill it if the chain has died -- the self-healing daily backstop for the prompt-carried CHAIN mechanism biweekly-retro seeds.
schedule: "0 21 * * *"
# 21:00 LOCAL time (the scheduled-tasks scheduler evaluates cron in local time, not UTC).
# Deliberately NOT the 04:00-07:00 block already occupied by prune-stale-worktrees (04:00) and
# reconcile-project-board (06:00) / reclaim-worktree-disk's 6-hourly cycle (00/06/12/18) -- that
# window already has its own documented run-reliability history (dev-env#698, dev-env#703: silent
# greet-instead-of-execute failures observed there). 21:00 also gives same-day repair latency for a
# chain that dies mid-day, rather than waiting until the next morning, at the identical "once daily"
# cost either slot would carry.
---

Check every tracked repo's `retro-action` chain liveness and refill any repo whose chain has died.
Run **fully autonomously** -- never call `AskUserQuestion`, never wait for input, never prompt for
approval.

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

**Why:** the CHAIN mechanism [dev-env#967](https://github.com/brownm09/dev-env/issues/967) built is
entirely prompt-carried -- it breaks silently and permanently on a dismissed chip, a compacted
session, an early exit, an API failure, or a human finishing the item by hand outside the tile.
`biweekly-retro` now seeds and refills chains too (its Step 6.5), but that only runs every other
Sunday; a chain that dies on, say, a Tuesday would otherwise sit dead for up to twelve days before the
next biweekly run notices. This routine is the daily backstop that catches it same-day. See
[ADR-131](../../../docs/adr/131-retro-chain-idempotent-refill.md) for the full design, and the
`retro-chain-refill` skill this routine's Step 1 invokes for the actual classify-and-refill logic.

**Steps:**

0. Sync the engineering-journal working tree. Read `~/.claude/skills/sync-routine-worktree/SKILL.md`
   and execute its Behavior section end-to-end with these parameters:
   - `REPO` = `C:/Users/brown/Git/engineering-journal`
   - `VERIFY_FILE` = `sessions/meta/README.md`
   - `PREFIX` = `retro-chain-backstop`

   On **SUCCESS**, continue. On **ABORT**, exit cleanly -- the push notification has already been
   sent; do not check any repo, do not spawn any tile.

1. Refill any dead chain. Set `RUN_DATE=$(date +%Y-%m-%d)`, then read
   `~/.claude/skills/retro-chain-refill/SKILL.md` and execute its Behavior section end-to-end with
   these parameters:
   - `seeded_by` = `retro-chain-backstop ${RUN_DATE}`
   - `repos` = (omit -- use the standard six-repo table in that skill)

   Capture the per-repo summary it returns for Step 2.

2. Report. Send a push notification built from Step 1's returned summary:

   ```
   retro-chain-backstop ${RUN_DATE} complete -- <N> repos checked.
   <A> alive, <Q> queue exhausted, <U> no queue found, <M> ambiguous (human review needed), <R> refilled.
   <one line per refilled or ambiguous repo, e.g. "dev-env: refilled -> issue #NNN" or
   "career-playbook: AMBIGUOUS -- see notes">
   ```

   Clean up any scratch files this run created.

**Constraints:**

- The standard six-repo participant table lives only in `claude/skills/retro-chain-refill/SKILL.md`
  -- not duplicated here.
- **Never** call `AskUserQuestion`. An `AMBIGUOUS` classification is reported, never resolved
  unilaterally.
- Temp files (if needed) go to `C:/Users/brown/.claude/scratch/`.
- No `jq` -- use `node -e` for JSON parsing.
- Platform: Windows 11, Git Bash syntax.
- **App-open caveat:** scheduled tasks run while the Claude app is open; if it was closed when the
  task was due, the run happens on next launch.

---

> **Dual-copy registration caveat.** This file is the **canonical, version-controlled** definition
> (`dev-env/claude/routines/retro-chain-backstop/`, surfaced at
> `~/.claude/routines/retro-chain-backstop/` via the directory junction). The scheduler reads a
> **separate** live copy at `~/.claude/scheduled-tasks/retro-chain-backstop/SKILL.md`, materialized
> by the `create_scheduled_task` MCP tool -- the two do **not** auto-sync. Per the convention
> [ADR-003's amendment](../../../docs/adr/003-config-in-version-control.md) establishes (already
> applied to `prune-stale-worktrees`/`reclaim-worktree-disk`), the live copy reads this canonical
> file at run time and defers to it when present, falling back to an embedded copy only when it is
> missing or unreadable -- applied here **from first registration**, rather than retrofitted after a
> drift incident, since this routine is new and has no drift history to retrofit against. A future
> edit here therefore takes effect live without a separate re-registration step; the embedded
> fallback should still be refreshed when this file's *steps* change materially. See dev-env#344.

---

**Restorable live-copy imperative ([dev-env#703](https://github.com/brownm09/dev-env/issues/703) item 3, [dev-env#767](https://github.com/brownm09/dev-env/issues/767)).**
The execute-now / do-not-greet mitigation ([dev-env#698](https://github.com/brownm09/dev-env/issues/698))
is the **only** effective, model-agnostic guard against an autonomous scheduled run greeting instead of
executing — the frontmatter `model:` pin is confirmed **inert** (dev-env#703 item 2). It lives verbatim
only in the machine-local live copy (`~/.claude/scheduled-tasks/retro-chain-backstop/SKILL.md`, which
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
