---
name: retro-chain-refill
description: Check whether a repo's retro-action backlog-burn-down chain (dev-env#967) is still alive, and refill it with a fresh chained tile if it has died. The shared mutating step invoked by both biweekly-retro's Step 6.5 and the retro-chain-backstop daily routine -- idempotent by construction, since both callers act on the same live classification.
---

Check the liveness of each tracked repo's `retro-action` chain and refill any repo whose chain has
died. This skill **mutates** GitHub state (may file a new issue, spawns a `spawn_task` tile) and the
engineering-journal repo (writes and commits a tile shard) -- it is not a read-only check.

This skill is invoked **by other routines** (`biweekly-retro`'s Step 6.5, the `retro-chain-backstop`
routine), not directly by users. It is a building block, not an end-user command, in the same spirit
as `sync-routine-worktree`.

**Background.** [dev-env#967](https://github.com/brownm09/dev-env/issues/967) describes a
prompt-carried "CHAIN block" mechanism: a `spawn_task` tile anchored to a dedicated GitHub issue
carries a block instructing whichever session picks it up to tick the completed backlog item, pick
the next unchecked item from that repo's `retro-action` queue issue, and spawn the next link itself.
The mechanism is entirely prompt-carried and breaks silently and permanently on a dismissed chip, a
compacted session, an early exit, an API failure, or a human finishing the item by hand outside the
tile. This skill is the self-healing check: it classifies every tracked repo's chain via
`retro-chain-status.py`, then refills exactly the repos whose chain has genuinely died. See
[ADR-131](../../../docs/adr/131-retro-chain-idempotent-refill.md) for the full design rationale.

---

## Parameters (the invoking routine supplies these via the skill's `args`)

| Name | Required | Description | Example |
|---|---|---|---|
| `seeded_by` | yes | A short label recorded in the `chain.seeded_by` field of any shard this run writes, identifying which caller and run produced the refill. This is the provenance trail that lets a later reader tell a biweekly-seeded link apart from a backstop-seeded one. | `biweekly-retro 2026-08-08`, `retro-chain-backstop 2026-08-09` |
| `repos` | no | A comma-separated `owner/repo` subset to check, overriding the standard six-repo table below. Omit to check all six. | `brownm09/dev-env,brownm09/career-playbook` |

If `seeded_by` is omitted (a caller invoking this skill outside its two documented callers), fall
back to a generic `retro-chain-refill <YYYY-MM-DD>` label rather than leaving the field blank --
every shard this skill writes must carry a non-empty `seeded_by`.

---

## The six participant repos (canonical list -- lives only here)

| owner/repo | journal project dir | local clone |
|---|---|---|
| brownm09/career-playbook | career-playbook | C:/Users/brown/Git/career-playbook |
| brownm09/dev-env | dev-env | C:/Users/brown/Git/dev-env |
| brownm09/cover-letter-runtime | cover-letter-runtime | C:/Users/brown/Git/cover-letter-runtime |
| brownm09/win11-init-tools | win11-init-tools | C:/Users/brown/Git/win11-init-tools |
| brownm09/gas-lifting-logbook | gas-lifting-logbook | C:/Users/brown/Git/gas-lifting-logbook |
| merickvaughn/lifting-logbook | lifting-logbook | C:/Users/brown/Git/lifting-logbook |

Deliberately **not** unified with `biweekly-retro`'s own, separately-stale, eight-repo issue-routing
list in its Step 6 -- that list answers "where do this retro's action-item findings get filed,"
which is a different concern from "which repos participate in the chain mechanism." Unifying the two
lists is future cleanup, not part of this change.

---

## Behavior

### Step 1 -- Classify every repo

Run once, redirected straight into a scratch temp file (per the global no-`jq` convention) -- do
**not** also run the bare form first; that would run the full six-repo classification twice, at
~2x the `gh` calls and latency, and risks two snapshots that legitimately disagree given how fast
this repo's state moves:

```bash
TMPFILE="C:/Users/brown/.claude/scratch/retro_chain_status_$$.json"
py -3 C:/Users/brown/.claude/scripts/retro-chain-status.py \
    --repo brownm09/career-playbook \
    --repo brownm09/dev-env \
    --repo brownm09/cover-letter-runtime \
    --repo brownm09/win11-init-tools \
    --repo brownm09/gas-lifting-logbook \
    --repo merickvaughn/lifting-logbook \
    --journal-repo C:/Users/brown/Git/engineering-journal \
    > "$TMPFILE"
```

(Substitute the `repos` parameter's subset for the `--repo` flags above when supplied.) The script
lives in dev-env's `claude/scripts/`, reached here via the `~/.claude/scripts/` junction rather than
a repo-relative path, since neither caller of this skill syncs a dev-env worktree as part of its own
Step 0 -- the junction always reflects whatever commit the canonical dev-env worktree currently holds
on `main` (the standing "canonical dev-env worktree must stay on `main`" invariant), which is the
same reasoning `claude/CLAUDE.md`'s own `merge-stale-pr.sh` reference already relies on.

Read `$TMPFILE` with `node -e` for the per-repo objects. The script exits 0 always; a per-repo
failure lands as `{"status": "ERROR", "error": "<text>"}` on that repo's own entry (singular
`error`, nested under `status`, not a top-level `errors` field) rather than aborting the batch --
carry every such repo into Step 5's summary as "could not classify -- \<error text\>," with **no**
action taken, never silently dropped.

### Step 2 -- Triage each repo's classification

For each repo's `status`:

- **`ALIVE` / `QUEUE_EXHAUSTED` / `NO_QUEUE_FOUND` / `ALL_TILED`** -- record the status, take no
  action. `ALL_TILED` means every unchecked item's inline issue reference already has a shard on
  disk (already tiled or in flight) -- nothing to refill until one of those items' shard resolves.
- **`UNRESOLVED`** -- record the status and the script's `notes` (why the chain issue's state
  couldn't be confirmed). **Never spawn** for an `UNRESOLVED` repo -- the chain may still be alive;
  this is the same "don't guess" principle as `AMBIGUOUS` below, applied to a `gh`-failure gap
  instead of a same-window-shard gap.
- **`ERROR`** -- record the status and the script's `error` text (see Step 1). No action -- carry
  it into Step 5's summary exactly like `UNRESOLVED`/`AMBIGUOUS` rather than silently dropping it.
- **`AMBIGUOUS`** -- record the status and the script's `notes` (which same-window shard triggered
  it) for human review. **Never spawn** for an `AMBIGUOUS` repo -- this is a deliberate "don't guess"
  outcome, not a temporary gap.
- **`NEEDS_REFILL`** -- before treating this as actionable, cross-check `list_sessions` for a session
  whose title, branch, or cwd already matches this repo and its candidate/anchor issue. A match means
  a session is already working the item (chip dismissed or not) -- reclassify this repo as alive for
  this run and record it distinctly from a script-confirmed `ALIVE`, e.g. `ALIVE (list_sessions
  match, not shard-confirmed)`, so a human reading the summary can tell the difference (this is the
  same best-effort "started" heuristic [ADR-118](../../../docs/adr/118-tile-persistence-shards.md)
  already documents elsewhere, with the same honest limitation: it reduces, but does not eliminate,
  a false positive). Only a repo that survives both the script's classification and this cross-check
  proceeds to Step 3.
- **Any other status** -- treat like `ERROR`: record it, take no action, carry it into Step 5's
  summary. Never silently drop an unrecognized status.

### Step 3 -- Refill each still-`NEEDS_REFILL` repo

For each repo, working from the top of its queue issue's live (re-read, not retro-time-frozen)
unchecked-item list:

1. **Resolve the anchor.** If the item's text names a candidate issue inline (e.g. `#NN`), verify it
   live -- `gh issue view <N> --repo <owner>/<repo>` -- resolves to an issue in this repo that is
   **open** and **not a pull request**. `retro-chain-status.py` does not itself validate
   `candidate_issue`, by design: classification and mutation happen at different moments, and this
   repo's state moves fast enough between them that only a fresh, live check here is trustworthy
   (`merickvaughn/lifting-logbook`'s currently-cited anchor `#814` is exactly this failure -- an
   already-merged pull request, not an issue). If no candidate exists, or the candidate fails
   verification, file a fresh issue in `<owner>/<repo>` labeled `retro-action` and use it as the
   anchor -- the same convention `biweekly-retro`'s own Step 6 already uses for queue issues.
2. **Re-check for a shard collision immediately before writing.** After the anchor issue number `N`
   is resolved, confirm no shard already exists at
   `sessions/<project>/tiles/N.json` in the engineering-journal canonical checkout (Step 1's
   classification snapshot can be stale by the time this write actually happens). If a shard is
   already there -- an unrelated, older tile sitting at that path (the exact
   `win11-init-tools/tiles/55.json` collision from 2026-07-22) -- do **not** overwrite it. Move to
   the next unchecked item in the queue and restart from sub-step 1. If the queue's checklist is
   exhausted without finding a collision-free item, record this repo as effectively
   `QUEUE_EXHAUSTED` for this run and take no further action.
3. **Compose the `spawn_task` prompt.** Self-contained, per the cross-session hand-off standard
   ([ADR-113](../../../docs/adr/113-cross-session-handoff-tiles.md)): name the repo, the anchor issue
   (URL and number), the actual backlog item text, and enough surrounding context that the spawned
   session needs nothing beyond the prompt itself. End the prompt with the CHAIN block below,
   verbatim, with `<QUEUE_ISSUE_URL>` and `<OWNER>`/`<REPO>` filled in with this repo's real values.
4. **`spawn_task`** the tile: `title` a short chip label, `tldr` a one-to-two sentence summary,
   `prompt` the composed text from sub-step 3, `cwd` this repo's local clone path from the table
   above (forward slashes, per [ADR-118](../../../docs/adr/118-tile-persistence-shards.md) Amendment
   4).
5. **Write the shard immediately** -- `sessions/<project>/tiles/N.json`, with the **Write tool**,
   never a shell redirect or heredoc
   ([ADR-129](../../../docs/adr/129-journal-shell-write-guard.md)) -- carrying all seven
   `TILE_REQUIRED_FIELDS` (`issue`, `url`, `title`, `tldr`, `prompt`, `cwd`, `spawned`) plus the
   optional `chain` field this mechanism adds
   ([ADR-118](../../../docs/adr/118-tile-persistence-shards.md) Amendment 6):
   ```json
   "chain": {"queue_issue": "<queue issue URL>", "seeded_by": "<this run's seeded_by parameter>"}
   ```
   `mkdir -p` the project's `tiles/` directory first if it does not yet exist -- this creates no
   content, so it stays outside the shell-write guard's scope.

### Step 4 -- Commit the new shard(s)

Ensure today's `draft/YYYY-MM-DD` branch exists in the engineering-journal canonical checkout at
`C:/Users/brown/Git/engineering-journal`, following the exact procedure in `claude/CLAUDE.md` →
Engineering Journal → Stub file workflow (cut fresh from `origin/main` with the SHA-equality branch
validation if this is the first touch of the journal today; checkout + pull if it already exists) --
**never a dedicated worktree**, per that section's rule against isolating the Stub file workflow into
one, which applies identically here: a tile shard is one of the four journal content-file kinds
committed on that same shared branch. This target is independent of whatever branch the invoking
routine's own report lives on (e.g. `biweekly-retro`'s `retro/${RUN_DATE}` PR branch) -- tile shards
always live on the shared `draft/YYYY-MM-DD` branch per the existing global convention, never on a
routine's own report-PR branch.

For each shard actually written in Step 3, `git add` and `git commit` with an **explicit per-file
pathspec** -- never the bare `tiles/` directory, which risks sweeping a concurrent session's own
in-flight shard into this commit -- then `git push`. Skip this step entirely if Step 3 wrote no
shard this run (nothing to commit).

### Step 5 -- Return a summary to the caller

Return a per-repo table: repo, the status this run settled on, and the action taken. This is what
`retro-chain-backstop`'s Step 2 push-notifies and what `biweekly-retro`'s Step 7 folds into its own
notification -- this skill does **not** send a push notification itself; reporting is the caller's
responsibility.

| Repo | Status | Action |
|---|---|---|
| brownm09/dev-env | ALIVE | none |
| brownm09/career-playbook | AMBIGUOUS | flagged for human review -- \<notes\> |
| merickvaughn/lifting-logbook | NEEDS_REFILL | refilled -> issue #NNN, shard committed |
| ... | ... | ... |

---

## The CHAIN block

Every tile this skill spawns ends with this block, verbatim, with `<QUEUE_ISSUE_URL>`, `<OWNER>`,
and `<REPO>` substituted for the real values. This is the **current** version -- two changes from the
2026-08-08 original block, both load-bearing: the shell-serializer wording is replaced with the
current Write-tool mandate (the original wording was already stale the day it shipped --
[ADR-129](../../../docs/adr/129-journal-shell-write-guard.md) retired the serializer recipe the same
week), and a new step 4 requires the same live PR/closed-issue validation this skill's own Step 3
performs before a *future* tile reuses an inline `#NNN` reference (closing the
`merickvaughn/lifting-logbook#814` failure mode for every link spawned from here forward, not only
for this skill's own refills):

```text
=== CHAIN (do this before you finish, whether or not the work above fully landed) ===
This tile is one link in an automatic backlog burn-down chain. A scheduled routine
(retro-chain-backstop) also self-heals this chain daily if this block is ever
skipped -- but do this anyway; it is the fast path. Before ending the session:
1. If the work above completed, tick its checkbox in the queue issue: <QUEUE_ISSUE_URL>
2. Re-read that issue's action-item checklist and pick the FIRST still-unchecked
   item (a real `- [ ]` checklist line -- not an "Escalations" bullet; those
   reference already-tracked issues, not spawnable new work). Use the live
   ranking -- do not use a name frozen at retro time, since priorities move
   between runs.
3. Check `C:/Users/brown/Git/engineering-journal/sessions/<project>/tiles/` for
   an existing shard for that item, and `list_sessions` for a session already
   working it. Skip it and take the next unchecked item if it is already tiled
   or in flight.
4. If the item names a pre-existing issue inline (e.g. "#NN"), verify with
   `gh issue view` that it resolves to an OPEN issue in this repo (not closed,
   not a pull request) before reusing it as the anchor. Otherwise -- or if no
   reference resolves -- file a new one in <OWNER>/<REPO> (label `retro-action`)
   so the tile has a durable anchor per ADR-094.
5. `spawn_task` a tile for that item, carrying this entire CHAIN block forward
   verbatim (updating the issue URLs), then write its shard to
   `sessions/<project>/tiles/<issue-number>.json` per ADR-118 using the Write
   tool -- never a shell redirect or heredoc (ADR-129). Include a "chain" field:
   {"queue_issue": "<QUEUE_ISSUE_URL>", "seeded_by": "<how this tile was spawned>"}
   so the self-healing routine can recognize this shard as part of the chain.
Do not ask whether to spawn the next tile. Spawn it. If the checklist has no
unchecked items left, say so explicitly and spawn nothing.
```

**The six tile shards already spawned on 2026-08-08 are not retroactively edited.** Their `prompt`
field is a historical record of what was actually sent to `spawn_task` under the original block text
-- the updated block above applies only to links spawned going forward.

---

## Why this exists

Without this skill, `biweekly-retro` has no seeding step at all (its Step 6 files queue issues and
stops), so a repo whose chain died between biweekly runs got no successor until a human noticed. This
skill is the shared classify-then-refill logic both `biweekly-retro`'s Step 6.5 and the daily
`retro-chain-backstop` routine invoke, so the two callers can never disagree about whether a given
repo needs a refill -- see [ADR-131](../../../docs/adr/131-retro-chain-idempotent-refill.md) for the
full design.

---

## Scope boundary

This skill does **not** handle:

- **Stale/unresolvable tile-shard expiry or triage** (dev-env#967 item 3) -- out of scope, filed as a
  separate follow-up.
- **Splitting a retro's §3 action items into product vs. process** (dev-env#967 item 4) -- unrelated
  concern, out of scope.
- **Sending its own push notification.** The caller reports Step 5's returned summary in whatever
  shape fits its own reporting convention.
- **Registering itself as a scheduled task**, or any other standing configuration change -- this
  skill only ever runs as prose invoked by an already-running session.
