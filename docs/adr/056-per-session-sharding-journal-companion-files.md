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

---

## Addendum (2026-06-30) — Explicit pathspec required on every commit step

ADR-056's "Why this is structurally safe" section states that disjoint shard files mean "git merges
concurrent sessions' writes cleanly... with no conflict and no clobber." That claim is correct for
**file content** — two sessions' shards never collide on disk or in a merge — but it does not extend to
the **git index** (staging area), which is a single shared resource within one local checkout
(`C:/Users/brown/Git/engineering-journal`), used by every concurrent Claude Code session across every
project. The documented commit step (`claude/CLAUDE.md` → Stub file workflow, both "First session" and
"Subsequent sessions" step 7) was a bare `git add <this session's files>` followed by a bare
`git commit -m "..."` with no pathspec. **A bare `git commit` commits the entire staged index**, not just
the files just `git add`-ed — so if a concurrent session had already staged its own files via its own
`git add` before this session's `git commit` ran, those files were swept into this session's commit too.

**Incident:** engineering-journal commit `a876284` ("draft: 2026-06-30 dev-env #443 review complete"),
produced by a bare `git commit` in a dev-env session, also contains a concurrent lifting-logbook
session's pre-staged `sessions/lifting-logbook/2026-06-30_135019.stub.md` and
`sessions/lifting-logbook/2026-06-30_141746.manifest.jsonl`. Harmless in this instance — the shards are
disjoint, additive, and content-intact, and `/journal-compose` reads shards by glob regardless of which
commit they landed in — but it entangled two unrelated sessions' work into one commit, undermining the
per-session attribution the stub/shard model exists to provide.

**Fix:** every commit step in the Stub file workflow now passes an explicit pathspec to `git commit`:

```bash
git commit -m "draft: YYYY-MM-DD session N" -- \
  sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md \
  sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl \
  sessions/<project>/open-prs/
```

`git commit -- <pathspec>` commits only the working-tree content of the named paths (auto-staging them if
not already staged) and explicitly leaves any *other* already-staged changes in the index untouched for a
future commit — the correct, minimal fix. No architectural change (e.g., per-session worktrees for the
engineering-journal checkout) is warranted: the hazard is in the *commit invocation*, not the sharding
design, which remains structurally sound for what it claims (content-level disjointness).

**Status:** this is a refinement, not a reversal — ADR-056's Decision, Considered alternatives, and
Consequences stand unchanged; Status remains Accepted. Tracked in [dev-env#449](https://github.com/brownm09/dev-env/issues/449).

---

## Addendum (2026-07-01) — Extended to propose, nightly-research, and merge-stale-pr.sh

The first addendum (2026-06-30) scoped the explicit-pathspec fix to the engineering-journal Stub
file workflow in `claude/CLAUDE.md`, plus `claude/skills/journal-compose/SKILL.md` and
`claude/routines/biweekly-retro/SKILL.md` — the sites confirmed at the time to write the shared
`C:/Users/brown/Git/engineering-journal` checkout. A `/review` pass on that PR flagged two more
bare-`git commit` sites pending verification of their shared-checkout status, and a follow-up
repo-wide grep across `claude/scripts/*.py` and `*.sh` (not covered by the original audit, which
was scoped to `claude/skills/` and `claude/routines/`) surfaced two more.

**Investigated and fixed** (same hazard: a persistent, non-worktree-isolated checkout with a bare
`git commit` that would silently absorb anything else staged in that checkout's index):

- `claude/skills/propose/SKILL.md` Step 10 — commits to whatever repo `config.roadmap_file` /
  `config.github_repo` targets. Step 6 does `git checkout main && git pull && git checkout -b
  docs/propose-<slug>` directly in the current working directory; no worktree isolation is set up
  anywhere in the skill.
- `claude/routines/nightly-research/SKILL.md` Step 4 — commits to `C:/Users/brown/Git/research-notes`.
  This repo has exactly one automated writer across all of `claude/` (confirmed by grep: the
  `/research` skill's "shared source library" is a different path, `~/.claude/skills/sources.md`;
  `biweekly-retro` only routes issue-filing there, never writes) — materially lower concurrency risk
  than engineering-journal. Fixed anyway: the script already computes the exact paths it stages, so
  scoping the commit is free and matches the pattern established for the structurally identical
  engineering-journal case.
- `claude/scripts/merge-stale-pr.sh` Step 4 — operates directly on the shared engineering-journal
  checkout (`cd "$JOURNAL_REPO"`, no worktree). Was `git add -u` (repo-wide stage) followed by a bare
  commit — broader than the original bug, since even the `add` was untargeted, not just the `commit`.
- `claude/scripts/pr-merge-reminder.py` (3 sites) — does not itself commit, but printed reminder text
  instructing the *next* session to run a bare `git commit` for the same stub workflow this ADR
  already fixed in `claude/CLAUDE.md`. Left uncorrected, the hook's own guidance would keep
  reintroducing the hazard it exists to help prevent. Reminder text updated to match the pathspec
  convention.

**Investigated and confirmed exempt (no change):**

- `claude/scripts/reconcile-late-stubs.py:198` — a bare `git commit`, but it runs inside a freshly
  created, detached, single-purpose temporary worktree (`git worktree add --detach <temp_dir>
  origin/<target_branch>`, line ~174) that is removed in the `finally` block immediately after. A
  fresh worktree has its own index; nothing else can be staged there. Adding a pathspec would be
  redundant defensive code for a scenario the isolation already rules out — left as-is.

**Status:** this is a scope extension of the same fix, not a reversal — ADR-056's Decision and both
addenda's Fix stand unchanged. Tracked in [dev-env#459](https://github.com/brownm09/dev-env/issues/459).
