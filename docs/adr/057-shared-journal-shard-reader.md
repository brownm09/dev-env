# ADR-057 — Shared `_journal_shards` Reader for open-PR Tracking

**Date:** 2026-06-22
**Status:** Accepted
**Tags:** journal, open-prs, hooks, post-compact, reconcile, dry, maintainability, sharding

---

## Context

[ADR-056](056-per-session-sharding-journal-companion-files.md) reshaped open-PR tracking from a
single shared `sessions/<project>/open-prs.jsonl` into per-PR shards
`sessions/<project>/open-prs/<N>.json`, with the legacy single file still read during a
back-compatible transition. Two hooks consume that tracking:

- **`reconcile-open-prs.py`** (a `UserPromptSubmit` hook) walks the shards once per session and
  `unlink`s those whose PRs are now MERGED/CLOSED — it needs the **file paths**.
- **`post-compact.py`** (a `PostCompact` hook) reads the shards on a manual `/compact` to remind
  Claude to `/review` an open PR — it needs the **parsed entries**, deduped by PR number.

ADR-056 landed the reader logic independently in each hook: glob `open-prs/*.json`, sort
numerically by PR number, tolerate malformed JSON, then fold in the legacy file. Two copies of
transition-period logic drift — and already did once: the shard **sort key** differed between the
two hooks (lexical vs. numeric) until it was reconciled during the
[#394](https://github.com/brownm09/dev-env/pull/394) review. The maintainability finding (B1) from
that review tracked the extraction as [#395](https://github.com/brownm09/dev-env/issues/395).

This mirrors a precedent already set twice in this repo: a constraint shared by multiple hooks is
promoted to one importable sibling module — `_hookio`
([ADR-050](050-shared-hookio-sibling-hook-fixes.md)) for the PostToolUse output read,
`_worktree_liveness` ([ADR-051](051-worktree-liveness-guard.md)) for the session-liveness guard.

## Decision

1. **Promote the shard/legacy read to `claude/scripts/_journal_shards.py`** — an import-only,
   side-effect-free module (no `_winsubp`, no subprocess, no `main()`), resolved via the same
   sibling-module-on-`sys.path` pattern as `_hookio` / `_worktree_liveness`. It exposes:
   - `shard_pr_number(path) -> int | None` — a PR shard is identified **by its numeric filename**;
   - `iter_pr_shards(shard_dir) -> list[(Path, dict)]` — numeric-named `*.json`, **numerically**
     sorted, JSON-parsed, returning `(path, entry)` so one caller can `unlink` and the other can
     read from the *same* enumeration; a missing/non-directory path yields `[]`;
   - `read_legacy_entries(path) -> list[dict]` — one JSON object per line; a missing file yields
     `[]`.

2. **Both hooks import it as the single source of truth.** `reconcile-open-prs.py`'s
   `reconcile_shard_dir` loops `iter_pr_shards` (and keeps its `state_fn` seam, per-shard `unlink`,
   and empty-dir cleanup); `post-compact.py`'s `read_open_pr_entries` consumes both readers and keeps
   its own dedup-by-PR (the dedup is consumer policy, not part of the read).

3. **Hardening folded into the shared parse.** `iter_pr_shards` and `read_legacy_entries` skip a
   parsed **non-object** value (a JSON list/scalar), in addition to unparseable JSON. Previously a
   non-object shard reached `entry.get(...)` and raised, which each hook only swallowed in an outer
   `try/except` that discarded *all* of a project's open-PR context. Skipping non-dicts at the read
   makes one malformed shard cost only itself.

4. **One place to retire the legacy format.** When the back-compat window closes — after the
   [engineering-journal#128](https://github.com/brownm09/engineering-journal/issues/128) data
   migration drains the legacy files — the legacy branch (`read_legacy_entries` and its call sites)
   is deleted from `_journal_shards` and the two hooks in one coordinated change, not hunted across
   divergent copies.

5. **Offline, fixture-only test:** `tests/test_journal_shards.py` pins the enumeration, numeric sort,
   the skip rules (non-numeric name / unparseable / non-object), returned-path identity, the
   content-agnostic return of a pr-less dict, and the legacy reader's blank/malformed/non-dict/
   missing-file handling. The two hooks' existing tests (`test_reconcile_open_prs.py`,
   `test_post_compact.py`) are unchanged and still green, pinning that the extraction preserved
   behaviour.

## Considered alternatives

- **Leave the two copies and re-sync on drift.** Rejected: the copies already drifted once (the sort
  key), and the cost recurs every time either hook's reader is touched. One module removes the drift
  surface entirely.
- **Have `iter_pr_shards` return only entries (no paths), and let reconcile re-derive the path from
  the PR number.** Rejected: re-deriving `shard_dir / f"{pr}.json"` duplicates the filename contract
  the reader already owns and re-opens the door to drift. Returning `(path, entry)` lets both callers
  consume the one enumeration directly.
- **A new ADR vs. an addendum to ADR-056.** Chosen a dedicated ADR to match the
  one-shared-module-one-ADR precedent (`_hookio` → ADR-050, `_worktree_liveness` → ADR-051) that the
  README/REFERENCE helper-module paragraphs link to.

## Consequences

- The shard sort/parse rule lives in one module; the two hooks can no longer drift apart on it.
- `_journal_shards` is the canonical open-PR reader for this repo's journal tooling; a future reader
  imports it rather than re-deriving the enumeration.
- A malformed (non-object) tracking record now costs only its own shard/line, not a project's whole
  open-PR context.
- **Behaviour-precision on malformed input (intentional, safe-direction).** For *well-formed* inputs
  this is a pure refactor — no file format, schema, or on-disk change; rollback is a plain revert. For
  *malformed* inputs the shared readers are strictly more graceful than the originals, in three ways
  recorded here so they read as deliberate rather than accidental drift:
  - `post-compact.py` now also ignores **non-numeric-named** files in `open-prs/` (e.g. `index.json`),
    matching `reconcile-open-prs.py` (which already filtered) and the `open-prs/<N>.json` contract — the
    old post-compact reader had no name filter and could surface such a file in the `/review` reminder.
  - `iter_pr_shards` / `read_legacy_entries` tolerate `OSError` — a mid-read TOCTOU failure now degrades
    to "skip that shard/line", where the originals propagated (reconcile skipped the whole project,
    post-compact dropped all open-PR context).
  - a **non-object legacy line** in `open-prs.jsonl` is now dropped on the next rewrite instead of
    *freezing* all cleanup of that file: the old `reconcile_file` crashed on it and the swallowed
    exception meant the file was never rewritten (so stale merged entries also survived). The file is
    system-written (`json.dumps(dict)` per line) and draining toward retirement, so a corrupt line
    carries no tracking data. Pinned by a new `test_reconcile_open_prs.py` case + `test_journal_shards.py`.
- Continues the shared-helper line `_winsubp` ([ADR-007](007-hook-command-invocation.md)) → `_hookio`
  (ADR-050) → `_worktree_liveness` (ADR-051) → `_journal_shards`.

## References

- [ADR-056](056-per-session-sharding-journal-companion-files.md) — the per-PR sharding this reads.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md), [ADR-051](051-worktree-liveness-guard.md) — the
  shared-sibling-module precedent this mirrors.
- [ADR-018](018-reconcile-open-prs-hook.md) — the `reconcile-open-prs.py` hook.
- Issue [#395](https://github.com/brownm09/dev-env/issues/395); surfaced as the B1 maintainability
  finding in the [#394](https://github.com/brownm09/dev-env/pull/394) review.
- [engineering-journal#128](https://github.com/brownm09/engineering-journal/issues/128) — the legacy
  data migration this consolidation pairs with.
- `claude/scripts/_journal_shards.py`; `claude/scripts/tests/test_journal_shards.py`.
