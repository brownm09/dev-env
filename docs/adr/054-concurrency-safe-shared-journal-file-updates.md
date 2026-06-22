# ADR-054 — Concurrency-Safe Updates to Shared Journal Files (Manifest + open-prs.jsonl)

**Date:** 2026-06-22
**Status:** Superseded by [ADR-056](056-per-session-sharding-journal-companion-files.md)
**Tags:** journal, stubs, manifest, open-prs, concurrency, data-loss, workflow, global-rule

---

> **Superseded by [ADR-056](056-per-session-sharding-journal-companion-files.md) (2026-06-22).** This ADR
> made shared-file updates safe by *documented discipline* (pull-first + surgical single-entry edit). ADR-056
> implements the structural fix recorded below under *Considered alternatives* — per-session/per-PR sharding,
> so no session ever writes a file another session also writes — making the hazard impossible rather than
> disallowed. The surgical-edit discipline this ADR introduced is retired; consult ADR-056 for the current
> mechanism. This record is retained for the incident history and the reasoning that led to the structural fix.

---

## Context

[ADR-001](001-per-session-stub-files.md) replaced the single mutable per-day draft file with
per-session immutable stub files (`YYYY-MM-DD_HHMMSS.stub.md`) precisely to eliminate write contention
between parallel sessions. Each session writes a uniquely-named stub; no session ever rewrites
another's.

That isolation was applied to the stubs but **not** to their two companion files:

- `sessions/<project>/YYYY-MM-DD.manifest.jsonl` — one JSON line per session (topic, tokens, PRs).
- `sessions/<project>/open-prs.jsonl` — one JSON line per still-open PR, carried across sessions/days.

Both are single per-day files that *every* session appends to and later updates. Two operations *edit
existing content* rather than append:

1. Setting `prs_closed:[N]` on a session's manifest line after a same-session merge.
2. Removing a merged PR's line from `open-prs.jsonl`.

When either is done as a **whole-file rewrite** — `cat > file`, or regenerating the file from the
session's own in-memory view of what it should contain — it overwrites any line a concurrent session
added that the rewriting session did not know about.

### Incident (2026-06-22)

Two sessions shared one engineering-journal clone on `draft/2026-06-22`. Session B had appended its
manifest entry (issue #381 / PR #387). Session A then ran a post-merge update that regenerated the whole
manifest via `cat >` from A's stale view, which did not include B's line — destroying B's entry. It
was recovered with `git checkout HEAD -- <manifest>` followed by surgically string-replacing only A's
line, but nothing had warned: the failure is silent, and the journal is the durable record, so an
uncaught clobber permanently loses session history.

The hazard is the combination of (a) a shared file, (b) a stale or in-memory copy, and (c) a
whole-file rewrite whose content comes from that copy rather than from the file on disk.

## Decision

Shared-file updates (manifest `prs_closed`; `open-prs.jsonl` removal) must be **concurrency-safe**:

1. **Pull first.** `git pull` the draft branch before the update, so any concurrent session's
   committed lines are present locally (covers the multi-clone case).
2. **Surgically edit only this session's entry.** Derive the new file content from the *current
   on-disk file* — read it, mutate the single line matched by stub filename (manifest) or PR number
   (`open-prs.jsonl`), and write it back. This preserves every other line verbatim, including a
   concurrent session's just-appended line on the same shared clone (the single-clone case, where
   reading the on-disk file is the load-bearing step).
3. **Never whole-file-rewrite from memory.** No `cat >` / `echo`-rebuild of the file from the
   session's own assumed contents. Appending the session's *own* new line (`>>`) remains fine; only
   rewrites that do not read the current file are prohibited.

The behavioral rule lives in the global `claude/CLAUDE.md` (Engineering Journal → Stub file workflow →
Shared-file exception; and Git Workflow → the "Write a stub on PR merge" bullet). The mechanical
surgical-update helpers — a manifest line-mutating `node -e` snippet and the existing `open-prs.jsonl`
line-dropping snippet — live in `docs/REFERENCE.md` → Engineering Journal Internals, both now prefaced
with the pull-first precondition.

## Considered alternatives

- **Restructure the manifest / `open-prs.jsonl` into append-only or per-session shards** (one file per
  session, merged at compose time), extending the ADR-001 stub model so no session ever touches
  another's data. This is the structurally stronger fix — it makes the hazard *impossible* rather than
  *disallowed* — but it is a larger change: it touches the write paths in `claude/CLAUDE.md`, the
  `reconcile-open-prs.py` hook, and the `/journal-compose` skill's discovery and merge logic, and it
  changes the on-disk layout that several hooks and the compose skill read. Deferred as a follow-up;
  the doc rule closes the active hazard immediately at low cost and risk.
- **A hook that blocks whole-file rewrites of these paths.** Hard to target precisely (the dangerous
  property is "content came from memory," which is not observable from the write itself) and easy to
  false-positive on the legitimate surgical helper, which also writes the whole file. Not pursued; the
  documented helper plus the pull-first rule is the practical control.

## Consequences

- Post-merge manifest/open-prs updates no longer silently clobber concurrent sessions' entries when
  the rule is followed.
- The `open-prs.jsonl` removal helper was already read-on-disk-and-filter (safe); it gains only the
  explicit pull-first precondition. The manifest now has an equivalent surgical-update helper, closing
  the gap that made `cat >` the path of least resistance for the `prs_closed` update.
- The protection is a disciplined convention plus a documented helper, not a mechanical gate — the
  stronger structural fix (sharding) is recorded above as deferred.
- This is the manifest/open-prs counterpart to ADR-001: ADR-001 isolated the stubs; ADR-054 makes the
  remaining shared files safe to update concurrently.

## References

- [ADR-001](001-per-session-stub-files.md) — Per-session stub files (the isolation this extends).
- `claude/CLAUDE.md` → Engineering Journal → Stub file workflow (Shared-file exception) and Git
  Workflow → "Write a stub on PR merge".
- `docs/REFERENCE.md` → Engineering Journal Internals (manifest + open-prs surgical-update helpers).
- Incident: engineering-journal `draft/2026-06-22`, dev-env manifest entries #386 / #387.
