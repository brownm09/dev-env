# ADR-072 — Shared `_repo_scan` Module for `find_git_repos()` Directory Discovery

**Date:** 2026-07-01
**Status:** Accepted
**Tags:** worktrees, github-project, scan-dir, dry, maintainability, shared-module

---

## Context

Three scripts in `claude/scripts/` each carried their own near-identical copy of
`find_git_repos(scan_dir)` — a helper that scans a directory for primary git repos (has a
`.git` *directory*; a worktree's `.git` is a file, so worktrees are excluded automatically)
to back each script's `--scan-dir` mode:

| Script | Catches | Return on scan failure | `os.scandir` handle |
|---|---|---|---|
| `prune-merged-worktrees.py` | `PermissionError` only | `[]` (same as zero-repos-found) | not closed |
| `reclaim-worktree-disk.py` | `PermissionError`, `FileNotFoundError` | `[]` (same as zero-repos-found) | not closed |
| `reconcile-project-board.py` (post-#465) | `PermissionError`, `FileNotFoundError` | `None` (distinct from `[]`) | closed via `with` |

`reconcile-project-board.py`'s `--scan-dir` mode ([ADR-070](070-reconcile-project-board-scan-dir.md))
was the third copy, and that ADR explicitly flagged the duplication rather than resolving it:

> `find_git_repos` now exists in three near-identical copies across `claude/scripts/`.
> Tracked as a candidate for a shared-module extraction, not done here.

Left alone, `prune-merged-worktrees.py`'s narrower `except PermissionError` is a latent bug:
`os.scandir()` on a nonexistent `--scan-dir` raises `FileNotFoundError`, which that script's
except clause does not catch — the exception propagates uncaught through `main()` and crashes
the process with a raw traceback instead of the graceful "no repos found" exit every other
scan-dir failure path produces.

This mirrors the extraction precedent already established for the hook families — `_hookio`
([ADR-050](050-shared-hookio-sibling-hook-fixes.md)), `_worktree_liveness`
([ADR-051](051-worktree-liveness-guard.md)), `_journal_shards`
([ADR-057](057-shared-journal-shard-reader.md)), and `_hookutil`
([ADR-064](064-shared-hookutil-sentinel-transcript-locate.md)) — applied here to the three
worktree/board maintenance scripts instead of the hook scripts. Issue
[#471](https://github.com/brownm09/dev-env/issues/471) tracked doing it.

## Decision

1. **Promote `find_git_repos` to `claude/scripts/_repo_scan.py`** — an import-only,
   side-effect-free module resolved via the same sibling-on-`sys.path` pattern as
   `_hookio` / `_worktree_liveness` / `_hookutil`. Signature:
   `find_git_repos(scan_dir: str) -> list[str] | None`.

2. **Backport `reconcile-project-board.py`'s more-complete behavior**, rather than keeping the
   more conservative version, per explicit recommendation in issue #471:
   - Catches both `PermissionError` and `FileNotFoundError`.
   - Returns `None` when `scan_dir` itself could not be scanned, distinct from `[]` (scanned
     fine, zero repos found) — callers that want to distinguish "scan failed" from "nothing
     here" now can.
   - Uses `with os.scandir(scan_dir) as it:` so the directory handle is always closed.

3. **All three scripts import `_repo_scan` and delegate.** Local `find_git_repos` definitions
   are removed from each script; `prune-merged-worktrees.py` and `reclaim-worktree-disk.py`
   need no other call-site changes — their existing `if not repos:` check already treats
   `None` and `[]` identically (prints "No git repos found under {scan_dir}", exits 0), so the
   richer return type is additive, not breaking. `reconcile-project-board.py`'s call site
   already branched on `None` vs `[]` explicitly (that was its own pre-existing logic) and is
   unaffected beyond the import swap.

4. **Offline, fixture-only test:** `tests/test_repo_scan.py` pins the helper using real
   `tempfile.TemporaryDirectory()` trees (no mocking) — mixed primary-repo/worktree/plain-dir/
   file discovery, case-insensitive sort order, a nonexistent `scan_dir` returning `None`, and
   an empty-but-readable `scan_dir` returning `[]` (proving the `None`-vs-`[]` distinction).
   `PermissionError` is not separately exercised (it shares the same `except` tuple as
   `FileNotFoundError`, which the nonexistent-dir case already drives through the same
   catch-and-return-`None` branch); reliably triggering a permission error portably, especially
   on Windows, would require mocking `os.scandir` for no additional coverage. The three
   consuming scripts' existing test suites (`test_prune_merged_worktrees.py`,
   `test_reclaim_worktree_disk.py`, `test_reconcile_project_board.py`) remain unchanged and
   green, confirming the extraction preserved call-site behavior.

## Considered alternatives

- **Leave the copies, re-sync on drift.** Rejected: the duplication already spans three files,
  had already drifted once (the `FileNotFoundError` gap), and would grow with any future
  script needing the same directory-scan shape.
- **Keep the more conservative (`[]`-only, `PermissionError`-only) behavior in the shared
  module, to minimize the diff.** Rejected per explicit direction in issue #471: the more
  complete behavior is strictly safer (no unhandled-exception crash path) and is additive at
  every existing call site — `prune-merged-worktrees.py` and `reclaim-worktree-disk.py`'s
  `if not repos:` guard already handles `None` the same way it handled the old `[]`, so there
  is no conservative option that is actually lower-risk here, only a strictly worse one.
- **Give the shared helper a `log_prefix` parameter so `reconcile-project-board.py` can keep
  its `[reconcile-board]`-tagged warning.** Rejected as unnecessary API surface: the warning
  text is a single `print(..., file=sys.stderr)` line, not machine-parsed by any routine (the
  routines key off the `RESULT:` stdout line), and `prune-merged-worktrees.py` /
  `reclaim-worktree-disk.py` never had a script-specific prefix on this message in the first
  place — losing `reconcile-project-board.py`'s prefix on this one line is a cosmetic
  consequence of unifying three scripts that disagreed on it, not a functional regression.

## Consequences

- Three scripts each lose 15–20 LOC of duplicated helper code.
- `_repo_scan` is the single source of truth for "primary git repos directly under a
  directory" in this repo; a future `--scan-dir`-style script imports it rather than
  re-deriving the pattern.
- **Behavior-preserving at every call site**, with one incidental fix:
  `prune-merged-worktrees.py --scan-dir <nonexistent path>` no longer crashes with an
  unhandled `FileNotFoundError` traceback — it now prints the same graceful
  `WARNING: cannot scan ...` / `No git repos found under ...` / exit 0 path the other two
  scripts already had.
- `reconcile-project-board.py`'s scan-failure warning loses its `[reconcile-board]` prefix
  (now reads identically to the other two scripts' plain `WARNING: cannot scan ...`) — a
  cosmetic, non-machine-parsed change (see Considered Alternatives).
- Continues the shared-helper line: `_winsubp` ([ADR-007](007-hook-command-invocation.md)) →
  `_hookio` (ADR-050) → `_worktree_liveness` (ADR-051) → `_worktree_topology` → `_journal_shards`
  (ADR-057) → `_hookutil` (ADR-064) → `_repo_scan`.

## References

- [ADR-070](070-reconcile-project-board-scan-dir.md) — deferred this extraction explicitly.
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md),
  [ADR-057](057-shared-journal-shard-reader.md),
  [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — the shared-module precedents this mirrors.
- Issue [#471](https://github.com/brownm09/dev-env/issues/471) — tracked the extraction.
- `claude/scripts/_repo_scan.py`; `claude/scripts/tests/test_repo_scan.py`.
