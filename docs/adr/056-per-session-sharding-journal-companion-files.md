# ADR-056 — Per-Session Sharding of Journal Manifest + open-PR Tracking

**Date:** 2026-06-22
**Status:** Accepted
**Supersedes:** [ADR-054](054-concurrency-safe-shared-journal-file-updates.md)
**Tags:** journal, stubs, manifest, open-prs, concurrency, data-loss, workflow, global-rule, sharding

---

## Context

[ADR-001](001-per-session-stub-files.md) replaced the single mutable per-day draft with per-session
immutable stub files (`YYYY-MM-DD_HHMMSS.stub.md`), so parallel sessions never overwrite each other's
stub. That isolation was never extended to the stubs' two companion files:

- `sessions/<project>/YYYY-MM-DD.manifest.jsonl` — one JSON line per session.
- `sessions/<project>/open-prs.jsonl` — one JSON line per still-open PR, carried forward across days.

Both are single per-day files that *every* session appends to and edits in place. [ADR-054](054-concurrency-safe-shared-journal-file-updates.md)
/ [#389](https://github.com/brownm09/dev-env/pull/389) made those edits concurrency-safe by **documented
discipline**: pull the draft branch first, then surgically mutate only this session's entry (matched by
stub filename or PR number) read from the *current on-disk file*, never a whole-file rewrite from memory.

That makes the clobber hazard **disallowed** but still **possible**. A single whole-file rewrite from a
stale or in-memory copy still silently destroys a concurrent session's entry — the 2026-06-22 incident,
where Session A regenerated the whole manifest via `cat >` and wiped Session B's just-appended line. The
protection is a convention, not a mechanical guarantee, and the journal is the durable record: an uncaught
clobber permanently loses session history. ADR-054's *Considered alternatives* recorded sharding as the
stronger structural fix, deferred. This ADR implements it.

## Decision

Shard both companion files per the ADR-001 stub model, so **no session ever writes a file another session
also writes**.

### Manifest → per-session shard

`sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl` — one single-line JSON object per session, paired
1:1 with the stub `YYYY-MM-DD_HHMMSS.stub.md`, same field schema and `.manifest.jsonl` suffix as the old
per-day lines. A session writes **only its own shard**; even the `prs_closed:[N]` update after a
same-session merge is an edit to that session's own file. `/journal-compose` globs
`YYYY-MM-DD_*.manifest.jsonl`, merges them in filename order (= chronological), and deletes the shards at
day end.

### open-PR tracking → per-PR shard

`sessions/<project>/open-prs/<N>.json` — one JSON object per open PR (`{pr,url,topic,stub,opened}`), keyed
by **PR number**. Opening PR #N writes `open-prs/<N>.json`; merging or closing it **deletes that file**.

The shard is keyed by PR number, **not by session**, on purpose: the entity that gets removed is the PR,
and the remover is frequently a *different* session than the opener — or the `reconcile-open-prs.py` hook,
not a session at all. Per-PR keying makes removal a whole-file `unlink` that structurally cannot touch any
other PR's record. A per-session open-PR shard would force the remover to edit the *opener's* shard,
reintroducing exactly the cross-session write this ADR eliminates. (Within a `sessions/<project>/`
directory all PRs belong to that project's single repo, so the bare PR number is a unique key; the repo is
still carried inside the object's `url` so cross-repo reconciliation stays correct.)

### Why this is structurally safe

Two concurrent sessions now create **disjoint files** (`<A>.manifest.jsonl` / `<B>.manifest.jsonl`;
`open-prs/386.json` / `open-prs/387.json`), which git merges cleanly with no conflict and no clobber.
Removal is a per-file delete that git records independently of every surviving file. The pull-first +
surgical-edit discipline of ADR-054 is therefore no longer load-bearing and is **retired** — this ADR
supersedes ADR-054. (Ordinary pull-before-push git hygiene still applies, as it does to the stubs.)

### Backward-compatible transition

Every reader (`reconcile-open-prs.py`, `post-compact.py`, `/journal-compose`, the Start-here dashboard
aggregation) **unions** the legacy file with the shards; every writer emits **only** shards. Existing data
is never force-migrated:

- open-PR shards are deleted as their PRs merge, and legacy `open-prs.jsonl` lines are removed by the same
  reconcile path that already rewrote that file (a safe read-filter-write), so the legacy file drains to
  empty and is removed;
- manifest shards (and any legacy per-day manifest) are deleted at compose;
- the in-flight `draft/YYYY-MM-DD` is read by the union path and is **not** rewritten mid-day.

`reconcile-late-stubs.py` already moves any `*.manifest.jsonl` (so per-session manifest shards are handled
with no change) and already excludes open-PR records (the target branch's copy is authoritative — equally
true for an `open-prs/` directory).

## Considered alternatives

- **Keep the ADR-054 discipline (status quo).** Rejected as the standing state: it is convention, not a
  guarantee, and the incident proved a single stale rewrite still clobbers.
- **Per-session open-PR shard** (one file per session listing that session's open PRs). Rejected: removal
  of a PR opened by Session A is usually done by Session B or by the hook, which would then have to edit
  A's shard — the cross-session write this ADR exists to remove. PR-keying aligns the shard boundary with
  the removal boundary.
- **Single-object `.manifest.json`** (drop the `l`). Rejected: keeping the `.manifest.jsonl` suffix lets
  `validate-jsonl.js` and `reconcile-late-stubs.py`'s existing `*.manifest.jsonl` matching keep working;
  the only change is the per-session timestamp prefix in the filename.
- **Forced one-shot migration of existing files.** Rejected in favor of the back-compat union read: a
  cutover would have to rewrite the in-flight draft mid-day — risking the very clobber being fixed. Lazy
  drain is reversible and lower-risk.

## Consequences

- The manifest / open-PR clobber hazard is **structurally impossible**, not merely disallowed: concurrent
  sessions touch disjoint files and removal is a per-PR delete.
- The ADR-054 surgical-edit helpers and the "Shared-file exception" rule are retired; `claude/CLAUDE.md`
  and `docs/REFERENCE.md` document the shard write/delete instead.
- A backward-compatible window exists where readers accept both legacy files and shards. It is
  self-limiting: the legacy files drain to empty and are removed.
- The companion engineering-journal change (`validate-jsonl.js` shard schemas + an optional, reversible
  data migration) is tracked separately in [engineering-journal#128](https://github.com/brownm09/engineering-journal/issues/128);
  the dev-env tooling does not depend on it — the validator merely gains coverage of the new shards.
- This completes the ADR-001 → ADR-054 → ADR-056 line: ADR-001 isolated the stubs, ADR-054 made the shared
  files safe **by discipline**, ADR-056 makes them safe **by structure**.

## References

- [ADR-001](001-per-session-stub-files.md) — per-session stub files (the isolation this extends).
- [ADR-054](054-concurrency-safe-shared-journal-file-updates.md) — the documented discipline this
  supersedes; its *Considered alternatives* recorded this sharding as the deferred structural fix.
- [ADR-018](018-reconcile-open-prs-hook.md) — the `reconcile-open-prs.py` hook, updated here to read the
  `open-prs/` shard directory.
- `claude/CLAUDE.md` → Engineering Journal → Stub file workflow; `docs/REFERENCE.md` → Engineering Journal
  Internals (shard schemas + write/delete steps).
- Issue [#392](https://github.com/brownm09/dev-env/issues/392); supersedes [#389](https://github.com/brownm09/dev-env/pull/389).
- Incident: engineering-journal `draft/2026-06-22`.
