# ADR-118: Persist Tile Payloads as Journal Shards and Re-Surface Them After a Restart

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
`cwd`), not the spawning session's, so tiles land in their respective projects. Seven
required fields (`TILE_REQUIRED_FIELDS` in `_journal_schema.py`): `issue`, `url`, `title`,
`tldr`, `prompt`, `cwd`, `spawned`. The middle four are the `spawn_task` arguments —
together they are what makes an *exact* re-spawn possible.

`stub` is **optional**, unlike its open-PR counterpart. An open-PR shard is always written by
a session that also writes a stub; a tile is not, because the tiling rule fires the moment a
follow-up is identified while the stub triggers are PR-open / PR-merge / report-generation. A
session that tiles something in passing legitimately writes no stub, and requiring the field
would force it to invent one. When present it is **project-qualified**
(`sessions/<project>/…stub.md`, the manifest convention) rather than the open-PR shard's bare
filename — the shard is filed under its *target* project, so the spawning session's stub may
live under a different one and a bare filename would not resolve.

**The payload must be serialized, never interpolated.** `prompt` is free prose — the entire
`spawn_task` prompt — which makes the `echo '{...}' > file` idiom the other shards use unsafe
here in three ways, two of them silent: a `"` or a Windows `\` path yields invalid JSON that
the reader skips without a word (losing the payload exactly when a restart needs it), and an
apostrophe closes the shell's single-quoted string so following metacharacters execute. Tile
prompts routinely quote text Claude did not author — issue bodies, `gh` output, error text.
The documented recipe therefore pipes the prose through a quoted heredoc into a JSON
serializer, and `mkdir -p` precedes it (`tiles/` does not exist until a project's first tile,
and the reader deletes it again whenever the last shard is pruned).

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

**The reader must validate `url` before it reaches `argv`.** `url` is git-committed,
cross-machine, and (until dev-env#870) unvalidated at write time, yet the reader parses it to
derive `--repo` for `gh`. The precedent it would otherwise copy — `reconcile-open-prs.py` —
splits the URL path with no host or character check. That is tolerable for open PRs but not
here, because the tile reconciler's remove branch **unlinks the shard**: a mis-resolved lookup
returning `CLOSED` destroys the payload. `gh --repo` also accepts a `HOST/OWNER/REPO` form, so
a crafted path can aim it at another host with the user's credentials. The reader must require
`netloc == "github.com"`, match owner/repo against `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`, take
the issue number from the filename rather than the URL, and **skip-and-keep** — never unlink —
when any check fails.

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

## Amendment 1 (2026-07-22, dev-env#869) — batched lookup; two further URL checks

Two things changed when the reader was actually built. Both are recorded here because they
contradict text above rather than merely elaborate it.

**The per-tile `gh issue view` is replaced by one `gh issue list --state all --json
number,state` per repo.** The Consequences section above says "One `gh issue view` per
pending tile on the first prompt of a session. Bounded by a per-run call cap." That is the
wrong shape, and specifically wrong at the moment it matters most: shards accumulate
un-pruned across the entire window between PR1 and this reader, so the *first* prompt after
the reader lands faces every tile ever written — exactly when a per-shard lookup is most
expensive. A per-run cap does not fix that; it just truncates. Batching per repo makes cost
scale with repo count instead, which is bounded by how many projects exist rather than by
how long the feature lay dormant. A wall-clock budget replaces the call cap, so a slow `gh`
degrades to "some repos unresolved, all kept, and said so" instead of the hook being killed
mid-flight and losing the whole index.

The transport is worth flagging: `gh issue list` is a GraphQL call, and this repo has
repeated, documented GraphQL exhaustion (dev-env#769, again during PR #872, and again during
this reader's own implementation session — measured at `graphql 0/5000` while REST `core`
sat at `4999/5000`). The hook fails safe there (every tile unresolved, every shard kept, the
message says so), so this is degradation rather than defect; moving the lookup to the REST
`core` bucket is dev-env#882. **→ Done; this paragraph is superseded by Amendment 3** (the
batching argument above it stands unchanged — only the transport moved).

**Two checks are added to the `url` validation the Decision section specifies.** The three
listed above (`netloc == "github.com"`, the owner/repo character class, issue number from the
filename) are necessary but not sufficient — both gaps were found by the validator's own
tests during implementation, not by review:

- a `netloc` check alone passes `ssh://github.com/o/r`, so the scheme must be `https`;
- `.` and `..` are spelled entirely from characters `^[A-Za-z0-9._-]+$` admits, so the regex
  alone accepts `https://github.com/../..` and hands `../..` to `gh --repo`.

Both fail closed (skip-and-keep), like every other check on that path. The host comparison is
also case-insensitive, since host names are case-insensitive
([RFC 3986 §3.2.2](https://www.rfc-editor.org/rfc/rfc3986#section-3.2.2)) — normalization
rather than a relaxation of the `== "github.com"` requirement.

The current-state record for all of this is
[`docs/REFERENCE.md` → Tile shards](../REFERENCE.md#tile-shards-sessionsprojecttilesissue-numberjson).

## Amendment 2 (2026-07-22, dev-env#870) — the shard trigger is session-global, not per-tile

The Follow-ups section above specifies "a fifth `stop-tile-enumeration-gate.py` trigger for a
`spawn_task` call with **no corresponding shard write**." The implementation cannot honour
"corresponding" and does not try; it fires only when a tile was spawned and **no** tile-shard
write is evidenced anywhere in the session. Two independent reasons, both discovered while
building it:

1. **A spawn cannot be matched to a shard.** `spawn_task`'s tool input carries `title`,
   `tldr`, `prompt`, and `cwd` — no issue number. The shard filename *is* the issue number.
   There is nothing in the transcript to join them on, so per-spawn correspondence is not
   merely expensive to compute but undetermined.
2. **Counting instead (N spawns ⇒ N shard paths) breaks on the documented recipe.** The write
   recipe pipes the prompt through a JSON serializer, and a session with several tiles
   naturally writes *one* script that emits all of them. That is not hypothetical: the session
   that shipped `reconcile-pending-tiles.py` wrote three shards from a single `py -3 <script>`
   call whose command text contained no `tiles/` path at all — the paths appeared only in the
   script's stdout. A counting gate would have blocked that session for work it did correctly.

The same observation forced the detector to scan **tool output**, not just tool inputs. Evidence
is accepted from a `Write`/`Edit` `file_path`, a Bash/PowerShell `command`, or Bash output.

Over-matching is the deliberate failure direction throughout — a stray `tiles/12.json` mention
that is not a real write merely means the trigger does not fire, whereas a missed real write is a
false block on a compliant session. This mirrors the session-global bar every other trigger in
that hook already uses (ADR-088's accepted limitation), and it still catches the failure the
enforcement phase exists to prevent: the *total* skip, where a chip is spawned and its payload is
never persisted at all.

The write-time half needed no such compromise: `journal-shard-write-advisory.py` validates a tile
shard's actual on-disk bytes, so it checks the full field set and flags a filename/`issue`
disagreement directly — including the consequence, since `reconcile-pending-tiles.py` treats such
a shard as corrupt and will never prune it.

## Amendment 3 (2026-07-22, dev-env#882) — the lookup moves to REST; two hazards it introduces

Amendment 1 flagged its own transport as a known weakness and named this as the follow-up:
`gh issue list` is a GraphQL call, and this repo has repeated, *measured* GraphQL exhaustion —
dev-env#769/#773 (project-board operations), again during PR #872, and again during the reader's
own implementation session at `graphql 0/5000` while REST `core` sat at `4999/5000`. An exhausted
bucket failed every lookup, so no shard was ever pruned and the pending-tile index filled with
already-finished tiles. The hook handled that safely — unresolved means *kept*, never unlinked,
and the message said so — so it was a degradation rather than a defect, but a total one, and it
landed on precisely the work the hook exists to support.

**`fetch_repo_issue_states` now reads `GET /repos/{owner}/{repo}/issues` over REST**, superseding
Amendment 1's transport paragraph (its batching argument stands unchanged — this is the same one
lookup per repo, on a different bucket). `core` is 5000/hr and near-untouched here, and it is
*not* what Projects v2 contends for: those operations are GraphQL-only with no REST surface at all
(dev-env#769), so taking this hook off the shared bucket helps both.

**REST-only, with no GraphQL fallback.** A fallback would add a second untested failure path and
double worst-case latency inside a hook that stalls the session's first prompt, to defend against
a `core` outage that has never been observed — and a `core` failure almost always means auth or
network is down, which GraphQL would not survive either. The conservative contract is unchanged
either way: any failure on any path still keeps the shard.

**Two REST-specific hazards, both silent, both deliberately relocated into pure code.** The
transport function is an untested subprocess boundary by this repo's fixture-only convention, so
leaving either rule inside it would have made both untestable — exactly the kind of bug a naive
test passes straight through. Both now live in a pure `issue_states_from_rows`:

- **REST models a pull request as an issue.** `GET /issues` returns both, distinguished only by a
  `pull_request` key; `gh issue list` filtered them server-side, which is why the original reader
  needed no such check. Issues and PRs share **one** number sequence per repo, so the hazard is
  not a collision between two live objects (that cannot happen): it is a shard whose number
  happens to name a PR, which without the filter resolves to that PR's state and is unlinked on a
  closed one. Key *presence* classifies, never its value — so if the payload projection ever
  changes shape, the failure direction is everything-unresolved-and-kept, not a mis-prune.
- **REST returns `state` lowercase** (`open`/`closed`) where GraphQL returned uppercase, and
  `should_remove_tile` compares `"CLOSED"` without case-folding. The predicate stays strict and
  the boundary normalises, rather than the reverse: a case-folding predicate would delete the one
  cheap regression pin that catches normalisation being dropped. Getting this wrong leaves the
  hook **inert** rather than fixed — fail-safe in direction, but a complete loss of pruning that
  nothing else reports. The test therefore runs raw REST rows all the way to the `unlink`, since
  a per-function assertion still passes when the normalisation is gone.

A third, smaller pure helper (`should_stop_paging`) bounds the walk: REST caps `per_page` at 100
where the GraphQL call took a single `--limit 200`, so pagination is now explicit. It stops on a
short page, on every requested number being resolved, or on a row below the lowest requested
number — the normal case is one page, a pending tile's issue being recent by construction. This is
why `lookup_states` now passes each repo's requested numbers to `fetch`. Per-page timeout and the
lookup budget are balanced so `MAX_ISSUE_PAGES * GH_CALL_TIMEOUT == LOOKUP_BUDGET_SECONDS`, which
makes one hanging repo exhaust the budget exactly and needs no bookkeeping inside the page loop —
strictly better than the previous 15s pairing, where a 15s hang left elapsed 15 < 20 and a second
repo's 15s call still started.

**One security note, in the improving direction.** ADR-118's `url` validation exists because
`gh --repo` accepts a `HOST/OWNER/REPO` form, making an unvalidated shard `url` a
credential-redirect primitive. Interpolating the validated repo into a REST *path* retires that
surface — `repos/<owner>/<repo>/issues` cannot name a host. Every check in `repo_from_issue_url`
stays regardless: it is still how the repo is derived, and defence in depth is free here.

---

## Amendment 4 (2026-07-23, dev-env#904) — the write recipe must prescribe `cwd`'s slash direction

**Closes:** [dev-env#904](https://github.com/brownm09/dev-env/issues/904)

Three tile shards written on 2026-07-23 (`sessions/dev-env/tiles/{898,899,900}.json`) carried
`"cwd": "C:Users<U+0008>rownGitdev-env"` — a value naming no directory. The full incident, the
validation layer added in response, and the two deliberate non-flags are in
[ADR-081](081-write-time-journal-shard-validation-hook.md) Amendment 2. What belongs *here* is
what it revealed about this ADR's own write recipe.

**The recipe guarded the wrong field.** The Decision section's rule is *"build the JSON with a
serializer, never `echo`"*, and it is justified entirely by `prompt`: free prose, so interpolating
it produces invalid JSON or escapes into the shell. That reasoning is correct and stays. But it
made `prompt` look like *the* hazardous field, when `cwd` is the other free-form one — and the
only one that is a Windows path. Switching from `echo` to a serializer satisfies the rule as
written while still corrupting `cwd`, because the hazard `cwd` faces is not shell *word* splitting
but the **string-literal layer of whatever language the serializer is written in**:

```
node -e "…JSON.stringify({cwd: "C:\Users\brown\Git\dev-env"})…"
                                    ^^      ^^      ^^
                          JS literal eats \U and \G, turns \b into U+0008
->  C:Users<U+0008>rownGitdev-env
```

The documented `py -3 -c` form never produced this — Python's literal parser *raises* on `\U`
rather than silently dropping it — so following the recipe exactly was already sufficient. Nothing
said so, and the global CLAUDE.md's `node -e` JSON idiom (the standing workaround for `jq` being
unavailable) actively points the other way. Two independent sessions reached for `node -e` and
produced the identical value on the same day.

**Amended rule.** `cwd` is written with **forward slashes** — `C:/Users/brown/Git/dev-env`, the
form the schema example in `docs/REFERENCE.md` already used. This is not a style preference: a
forward-slash path is valid on Windows, valid in JSON, and contains no character that any of the
escaping layers between a command line and `JSON.stringify` will consume, so it removes the
failure mode instead of validating for it. `docs/REFERENCE.md` -> Tile shards now states this
where the recipe lives, and generalises the never-`echo` warning from "`prompt` is prose" to
"no field crosses a shell-quoting boundary" — which is what the heredoc-fed `py -3 -c` form was
always for.

**Why the shard's payload is the thing at stake.** This ADR's premise is that the paired issue is
the *anchor* and the shard is the *payload* — the issue survives a crash, but only the shard makes
the re-spawn **exact**. `cwd` is one of the four `spawn_task` arguments that "exact" is made of,
and it is the one that decides *which repo the work happens in*. A corrupt `cwd` therefore does
not degrade the payload, it voids it: the re-spawn either fails outright or silently lands
somewhere else. Nothing downstream would ever have reported this — `reconcile-pending-tiles.py`
reads `url` and the filename, never `cwd`, and no compose-time gate reads tile shards at all —
which is why the validation half of the fix had to live in the write-time hook.
