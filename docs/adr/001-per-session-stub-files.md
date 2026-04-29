# ADR 001 — Per-Session Stub Files for Journal Composition

**Date:** 2026-03-27  
**Status:** Accepted

---

## Context

The original engineering journal approach used a single mutable draft file per day (`YYYY-MM-DD-draft.md`). All sessions on a given day appended to that one file. This created two problems:

1. **Write contention:** Multiple parallel sessions (e.g., two PRs open simultaneously in separate worktrees) would overwrite each other's content on push.
2. **Unstructured composition:** `/journal-compose` had to parse an unstructured, potentially inconsistent draft to produce the canonical day document. Any mid-session edit or partial commit left the draft in an ambiguous state.

---

## Decision

Each session writes its own immutable stub file: `sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`, where `HHMMSS` is the UTC session start time. A companion `YYYY-MM-DD.manifest.jsonl` tracks session metadata (topic, tokens, PRs opened/closed) without reading individual stubs.

`/journal-compose` discovers all stubs for a day via the manifest (or a glob fallback), merges them in order, and produces the canonical 11-section document at day end.

---

## Consequences

- Parallel sessions never conflict — each writes to a unique filename.
- Stubs are append-only and immutable after commit; no partial-write ambiguity.
- Journal composition is always a dedicated, clean-slate operation (see ADR 002).
- The manifest lets `/journal-compose` reconstruct session order and token data without reading stub content upfront.
- Adding a session to an existing day requires only a new stub + manifest append — no rewrite of existing files.

---

## References

- Engineering journal: `sessions/meta/2026-04-05-workflow-and-journal-setup.md` (stub structure documentation)
- `dev-env` commit `1bd387f` — `feat: migrate journal workflow to per-session stub files`
- `claude/skills/journal-compose/SKILL.md` — composition logic
