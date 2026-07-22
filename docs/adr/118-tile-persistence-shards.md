# ADR-116: Persist Tile Payloads as Journal Shards and Re-Surface Them After a Restart

Date: 2026-07-22
Status: Accepted
Tags: tiles, spawn-task, persistence, shards, journal, hooks, UserPromptSubmit, crash-recovery, mcp, foreground-ui, claude-facing, adr-046, adr-053, adr-056, adr-057, adr-094, adr-098

## Context

A `spawn_task` tile is the one-click control for starting a follow-up session. It is also
ephemeral by construction. ADR-094 records the three harness facts that bound it: chip IDs
are not persisted across app restarts, there is no URL that links to a spawned session, and
no non-destructive API reports whether a chip was clicked (`dismiss_task` reveals it only by
consuming it).

The consequence: if Claude crashes or the app restarts before the user clicks a chip, the
chip is gone. Before this ADR there was **zero on-disk tile state anywhere** in dev-env — a
repo-wide search for the tile payload fields (`prompt`, `tldr`, `cwd`) returned nothing. The
only durable artifact was the paired GitHub issue that ADR-094 already requires. That issue
preserves the *follow-up*, so nothing is truly lost, but it does not preserve the *chip*:
recovery means finding the issue and manually restarting, which is exactly the friction the
tile existed to remove.

The user asked whether tile content could be stored on disk "as is done with journal
information," so that a single process could respawn tiles after a crash.

**The first half of that is straightforward; the second half is not possible.** Two harness
constraints, established by investigation across the hook and routine surface:

1. **`spawn_task` is an MCP tool callable only by Claude inside a session.** Nothing in this
   repo calls it. Every existing tile mechanism — `post-merge-tile-checkpoint.py`,
   `stop-tile-enumeration-gate.py` — only *detects* a call in the transcript or *emits text
   reminding Claude* to make one. There is no scripting path to the tool.
2. **The chip only renders in a foreground UI.** ADR-053 documents that a background/SDK-
   launched session can call `spawn_task` while the chip silently never appears, and
   identifies that launch class as the common factor. Scheduled routines run in exactly that
   class, so the obvious "nightly process re-spawns pending tiles" design would produce no
   chips even if it could reach the tool.

So a headless daemon is ruled out on two independent grounds. What *is* available is the
pattern the journal already uses for open PRs: persist per-item state to disk, then
re-surface it to Claude at a session boundary via a hook's exit-0 stdout, and let Claude act
on it. `reconcile-open-prs.py` (UserPromptSubmit) and `post-compact.py` (PostCompact) are
that pattern in production today for `sessions/<project>/open-prs/<N>.json`.

## Decision

Persist tile payloads as **per-tile journal shards** and re-surface un-activated ones at the
next session start, where Claude — the only actor that can — re-spawns them.

**Shard.** `sessions/<project>/tiles/<issue-number>.json`, one JSON object, keyed by the
paired GitHub issue number. Issue-per-tile (ADR-094) guarantees the issue exists, and it
doubles as the reconciliation key. `<project>` is the tile's **target** project (from its
`cwd`), not the spawning session's, so tiles land in their respective projects. Eight
required fields (`TILE_REQUIRED_FIELDS` in `_journal_schema.py`): `issue`, `url`, `title`,
`tldr`, `prompt`, `cwd`, `stub`, `spawned`. The middle four are the `spawn_task` arguments —
together they are what makes an *exact* re-spawn possible.

**`task_id` is deliberately not stored.** A chip ID is dead after restart, so persisting one
saves a value that is worthless precisely when the shard is needed. This is ADR-094's
rejected "task_id record only" alternative, and it stays rejected.

**Writer.** Claude writes the shard immediately after each `spawn_task` call, per a rule in
`claude/CLAUDE.md` — exactly parallel to "opening PR #N writes `open-prs/<N>.json`". This
matches the established division of labour: no script writes journal shards; hooks only
read, surface, and delete them. A hook-based auto-write is not available anyway, since
`settings.json` PostToolUse matchers cover `Bash|PowerShell|Write|Edit` and not MCP tools,
and PostToolUse is inert in background sessions (ADR-053).

**Reader.** `reconcile-pending-tiles.py`, a `UserPromptSubmit` hook modeled on
`reconcile-open-prs.py`: once per session via a sentinel, walk `sessions/*/tiles/*.json`,
reconcile each against `gh issue view`, `unlink` shards whose issue is `CLOSED`, keep the
rest, and emit a compact index on **stdout at exit 0** (the channel that is model-visible on
that event — getting this backwards is the ADR-098 failure mode). It surfaces the *index*,
not the payloads; Claude reads a shard for the full prompt only when actually re-spawning,
keeping turn-1 context small.

**Shared reader, not a copy.** `_journal_shards.py` generalises to `iter_numeric_shards`,
with `iter_pr_shards` and `iter_tile_shards` as named delegations, and `shard_pr_number`
retained as an alias of a generic `shard_number`. ADR-057 extracted that module precisely
because two copies of "glob, keep numeric stems, sort numerically, parse tolerantly" had
already drifted (lexical vs. numeric sort). Adding a second shard kind by copying the reader
would have recreated that bug on schedule.

## Consequences

- A crash or restart no longer costs the chip. The next session surfaces the pending tile
  and Claude can re-spawn it with the original prompt and cwd.
- Tile prompts become git-committed content in the engineering-journal repo. That is no
  different in kind from stubs, which already carry full session content, but the standing
  rule applies: no secrets in tile prompts.
- One `gh issue view` per pending tile on the first prompt of a session. Bounded by a
  per-run call cap; worth watching if tiles accumulate.
- **"Un-activated" is an approximation, and this is the honest limitation.** Because no
  non-destructive API reports whether a chip was clicked, the hook infers "still pending"
  from "issue still open." A tile whose work already started but whose issue is open will be
  re-surfaced. The mitigation is a `list_sessions` title/branch check before re-spawning —
  the same best-effort "started" heuristic ADR-094 already documents for the tile-table
  Status column. It reduces the false positive; it does not eliminate it. The worst case is
  a duplicate chip the user dismisses, which is strictly better than the lost tile this ADR
  exists to prevent.
- The store is opt-out-able by construction: shards are inert data, and unwiring the hook
  from `settings.json` disables the feature without migration.

## Alternatives considered

- **A headless process that re-spawns tiles.** The literal request. Rejected as impossible,
  not merely undesirable: `spawn_task` is reachable only from inside a session, and the chip
  requires a foreground UI (ADR-053). Recording this here so the idea is not re-proposed.
- **Reuse the GitHub issues alone — no payload store.** Query open "tiled" issues at session
  start and rebuild from the issue body. Lighter, and works via `gh` in more session types.
  Rejected because re-spawn would reconstruct an approximation of the prompt rather than
  restoring the original, and the issue body is written for a human reader, not as a spawn
  payload. The issue remains the durable *anchor*; the shard is the durable *payload*.
- **Do nothing — document the recovery path.** Defensible, since issue-per-tile already
  prevents information loss and only convenience is at stake. Rejected because the
  convenience *is* the feature: ADR-046 and ADR-113 both rest on the claim that the chip is
  strictly lower-friction than a manual restart, and a chip that evaporates on restart
  undercuts that in exactly the situation (a crash) where restarting is most costly.
- **Key the shard by `task_id` or a generated UUID.** Rejected: `task_id` is dead after
  restart, and a UUID would need a side table to reach the issue. The issue number is
  already unique, already required, already meaningful, and already queryable.
- **Store shards under `~/.claude/scratch/`.** Rejected: scratch holds per-session sentinels
  that are swept at 30 days and is not version-controlled. Tile shards need to survive a
  restart and be visible across sessions, which is what the journal repo already provides.

## Follow-ups

**Phasing.** This ADR records the whole decision; it ships in three PRs under dev-env#867.
The shard format, `_journal_shards` generalisation, and the `claude/CLAUDE.md` write rule land
first (dev-env#868). Until the reader merges, shards are written but never pruned or
surfaced — dormant, not wrong.

- `reconcile-pending-tiles.py` — the `UserPromptSubmit` reader described above, plus the
  matching `post-compact.py` read (dev-env#869).
- Enforcement (dev-env#870): tile-shard validation in `journal-shard-write-advisory.py`, and
  a fifth `stop-tile-enumeration-gate.py` trigger for a `spawn_task` call with no
  corresponding shard write.
- If the harness ever exposes a non-destructive "was this chip activated" query, replace the
  issue-state approximation with it and delete the duplicate-chip caveat above.
