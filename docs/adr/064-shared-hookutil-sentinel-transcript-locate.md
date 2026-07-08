# ADR-064 — Shared `_hookutil` Module for Per-Session Sentinel and Transcript-Locate Helpers

**Date:** 2026-06-30
**Status:** Accepted
**Tags:** hooks, stop-hook, UserPromptSubmit, sentinel, transcript, dry, maintainability, shared-module

---

## Context

Three hook scripts in the Stop / UserPromptSubmit family independently duplicated two patterns:

**Pattern 1 — per-session sentinel files.** To avoid re-running expensive work on every Stop
event (Stop fires at each turn-end), hooks write a flag file
`~/.claude/scratch/{PREFIX}{session_id}.flag` on first run and skip all subsequent fires in
the same session. Each script reproduced: a module-level `SCRATCH` path, a `MAX_AGE_DAYS`
constant, a `cleanup_stale_sentinels()` function (deleting old flags), and a `sentinel_path()`
function (returning the path for a given session-id).

**Pattern 2 — transcript locate.** Two hooks needed the session JSONL path when the Stop
payload's `transcript_path` was absent or stale. Each independently implemented:
`PROJECTS.glob(f"**/{session_id}.jsonl")`.

| Script | Has sentinel? | Has transcript-locate? |
|---|---|---|
| `posttooluse-inert-advisory.py` (Stop) | yes | yes |
| `reconcile-open-prs.py` (UserPromptSubmit) | yes | no |
| `token-tracker.py` (Stop) | no | yes |

All three carried their own `SCRATCH` / `PROJECTS` path constants. No two copies had drifted
yet, but the surface existed: three places to update if `~/.claude/` changes structure, three
places to remember when writing a new Stop hook.

This mirrors the extraction precedent already established twice for the PostToolUse hook
family — `_hookio` ([ADR-050](050-shared-hookio-sibling-hook-fixes.md)) for the Bash-hook
output read, and the worktree-maintenance scripts' `_worktree_liveness`
([ADR-051](051-worktree-liveness-guard.md)) — and once for the journal open-PR hooks —
`_journal_shards` ([ADR-057](057-shared-journal-shard-reader.md)).

## Decision

1. **Promote the shared helpers to `claude/scripts/_hookutil.py`** — an import-only, side-effect-
   free module resolved via the same sibling-on-`sys.path` pattern as `_hookio` / `_worktree_liveness`.
   It exposes:
   - `SCRATCH`, `PROJECTS`, `MAX_AGE_DAYS` — the canonical path constants;
   - `cleanup_stale_sentinels(prefix, scratch=None)` — removes flags older than `MAX_AGE_DAYS`
     matching the given prefix; swallows all I/O errors (best-effort);
   - `sentinel_path(prefix, session_id, scratch=None) -> Path` — returns the flag path for a
     given hook family and session;
   - `find_transcript(session_id, projects=None) -> Path | None` — globs `**/{session_id}.jsonl`
     under `~/.claude/projects/`, returning the first match or `None`.

   The `scratch` and `projects` parameters are injectable (defaulting to the module constants)
   to support offline testing without touching the real `~/.claude/` tree.

2. **All three scripts import `_hookutil` and delegate.** Local `SCRATCH`, `PROJECTS`, `MAX_AGE_DAYS`
   constants, and local `cleanup_stale_sentinels()` / `sentinel_path()` / `find_transcript()`
   functions are removed from each script.

3. **Offline, fixture-only test:** `tests/test_hookutil.py` pins all three helpers using injected
   tmp directories — sentinel-path correctness, default-scratch parent, stale-cleanup (removes old,
   keeps fresh, ignores different-prefix, no-crash on missing dir), and transcript-locate
   (found, not-found, nested-project-dir). The three consuming hooks' existing tests remain
   unchanged and green, confirming the extraction preserved behaviour.

## Considered alternatives

- **Leave the copies, re-sync on drift.** Rejected: the duplication already spans three files and
  would grow whenever a new Stop / UserPromptSubmit hook needs a sentinel. One module removes the
  drift surface.
- **Merge into `_hookio`.** Rejected: `_hookio` is scoped to PostToolUse Bash hooks (reads the
  `tool_response` payload). Mixing Stop/UserPromptSubmit utilities into it would blur the
  hook-family boundary and confuse readers who import it only for `read_command_output`.

## Consequences

- Three scripts each lose ~15–20 LOC of duplicated helper code.
- `_hookutil` is the single source of truth for per-session sentinel + transcript-locate in this
  repo; new Stop / UserPromptSubmit hooks import it rather than re-deriving the patterns.
  ([ADR-090](090-shared-transcript-readers-hookutil.md) later extended it to also own the
  transcript-record readers `load_records` / `_parse_records` / `iter_bash_calls` / `_result_text` /
  `_content_items`, shared by `posttooluse-inert-advisory.py` and `stop-tile-enumeration-gate.py`.)
- **No behaviour change.** The extraction is a pure refactor — sentinel semantics, flag-file
  location, and transcript-glob expression are identical to each script's own copy.
- Continues the shared-helper line: `_winsubp`
  ([ADR-007](007-hook-command-invocation.md)) → `_hookio` (ADR-050) → `_worktree_liveness`
  (ADR-051) → `_journal_shards` (ADR-057) → `_hookutil`.

## References

- [ADR-050](050-shared-hookio-sibling-hook-fixes.md), [ADR-051](051-worktree-liveness-guard.md),
  [ADR-057](057-shared-journal-shard-reader.md) — the shared-module precedents this mirrors.
- [ADR-055](055-reliable-event-inert-posttooluse-advisory.md) — `posttooluse-inert-advisory.py`,
  one of the three migrated scripts.
- [ADR-018](018-reconcile-open-prs-hook.md) — `reconcile-open-prs.py`, one of the three migrated scripts.
- Issue [#393](https://github.com/brownm09/dev-env/issues/393) — tracked the extraction.
- `claude/scripts/_hookutil.py`; `claude/scripts/tests/test_hookutil.py`.
