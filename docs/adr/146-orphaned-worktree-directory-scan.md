# ADR-146: Orphaned-Worktree-Directory Scan-and-Remove Pass in `prune-merged-worktrees.py`

**Date:** 2026-09-29
**Status:** Accepted
**Tags:** worktrees, prune, disk, windows, silent-failure, git-worktree-remove, shared-module, ADR-051, ADR-058, ADR-105

---

## Context

`prune-merged-worktrees.py` removes a `claude/*` worktree once its branch is merged, via `git
worktree remove <path>` (no `--force`, only after the script's own `is_merged()`/`is_dirty()`
checks already confirmed it is clean and merged). On Windows, that call can fail part-way through
— `error: failed to delete '<path>': Permission denied`, `Filename too long` (a deep
`node_modules` tree past Windows's MAX_PATH), or `Directory not empty` (a partial delete leaving a
locked file behind). [dev-env#1104](https://github.com/brownm09/dev-env/issues/1104)'s original
framing assumed a failed removal "lingers" — the directory and its git registration stay in place,
so the same worktree shows up as a skip on every subsequent scheduled run.

A live 2026-09-28 manual disk-space cleanup of lifting-logbook corrected that assumption. After
`git worktree remove` fails, the worktree's git registration is **already gone**: `git worktree
list` no longer mentions it at all, and the `.git` link inside the directory has already been
removed. Git tears down its own bookkeeping (the `.git` link, the `git worktree list` entry) before
attempting the actual recursive directory delete — so a failure at that last step leaves a
directory with **zero trace in git's own view**, not a lingering, re-checkable skip. It does not
resurface on the next run; it drops off git's radar entirely and becomes a fully silent orphan.

Every check in `prune-merged-worktrees.py` and `_worktree_topology.py` — `is_merged()`,
`is_dirty()`, the liveness guard, the main/draft-branch squatter detection — starts from `git
worktree list`'s own output. None of them can ever see a directory git itself no longer lists, by
construction.

**Scale.** lifting-logbook alone had accumulated 28 such orphaned directories (dated 2026-07-05
through 2026-08-31 — nearly 3 months), totaling ~11.2 GB, none visible in `git worktree list` and
none appearing in any scheduled-run skip report in that entire window. Total `.claude/worktrees`
usage there was 21 GB; manual cleanup freed 16 GB (21 GB -> 5 GB, the remainder protected by the
active-session/locked-credential guards). The failure is also a *family*, not one error string:
"Permission denied," "Filename too long," and "Directory not empty" are three distinct Windows
delete-failure shapes with the identical downstream consequence.

---

## Decision

1. **`_worktree_topology.py` gains a fifth pure helper**, alongside the four concerns its module
   docstring already documents: `find_orphaned_worktree_dirs(disk_dirs, worktrees)`. Given the
   caller's own on-disk directory listing and a parsed `git worktree list --porcelain` result, it
   set-differences the two (via the same `_norm()` path normalization every other comparison in the
   module already uses) and returns the directories git does not know about at all. Pure — no
   filesystem access, no subprocess — matching every other helper in this module.

2. **`prune-merged-worktrees.py` gains the filesystem-touching half**: `worktrees_root()` (the
   `<primary>/.claude/worktrees/` convention `reclaim-worktree-disk.py`'s
   `is_claude_managed_worktree()` already documents), `list_worktree_subdirs()` (a real
   `os.scandir()`, fail-safe to `[]` on any `OSError`), `has_git_link()` (`path/.git`
   existence — the actual safety gate, see Judgment calls), and `find_and_remove_orphaned_worktrees()`,
   which combines them: for each orphan candidate, skip (never touch) a live Claude session (the
   same ADR-051 liveness window the rest of the script already uses) or a directory that still has a
   `.git` link; otherwise delete it via a plain `shutil.rmtree()` — there is no git command to run,
   since git has nothing left registered to unwind. `--dry-run` reports without deleting. An
   `OSError` from `shutil.rmtree` (the same lock/long-path failure persisting) is caught per-orphan
   and reported as still-skipped, without aborting the rest of the scan.

3. **Called unconditionally at the end of `prune_one()`**, once per repo, regardless of whether that
   same run's own removal loop hit a failure. This is the actual fix for the silent-accumulation
   bug: it also catches every *past* run's orphans, not just a fresh one from the current run, and
   it will keep re-attempting — via the routine's own normal scheduled cadence — until whatever
   transient Windows-side lock finally clears, rather than needing a single in-process retry to get
   the timing right.

---

## Judgment calls

**Placed in `prune-merged-worktrees.py`, not `reclaim-worktree-disk.py`** (the issue named either as
acceptable). `prune-merged-worktrees.py` already owns "call `git worktree remove` and interpret its
result" — the orphan scan is a direct extension of that same responsibility, finishing what
`worktree remove` started but couldn't complete. `reclaim-worktree-disk.py`'s whole contract is
stripping a regenerable *subdirectory* (`node_modules`, `.turbo`) out of an otherwise-intact,
still-registered worktree; it never deletes a whole worktree directory, and giving it that power
here would be a scope mismatch with everything else it does.

**The `.git`-absence check is the real safety gate — `git worktree list` absence alone is not
enough to delete.** A directory absent from `git worktree list` but that *still* has a `.git`
link inside is left completely untouched and flagged for manual review, never deleted. This
defends against a directory this script has no business guessing about: a narrow `git worktree
add` mid-creation race, or literally anything else a human placed under `.claude/worktrees/`. Only
the *combination* — unregistered AND no `.git` — is the dev-env#1104 orphan shape; either signal
alone is not conclusive on its own, but empirically (per the 2026-09-28 finding) they always
co-occur for a genuinely orphaned directory, so requiring both costs nothing in the common case
and gains a real backstop against a false deletion in an edge case.

**No in-process sleep-based retry at the point of the original failure.** The unconditional scan
pass already re-attempts on every future scheduled run — a natural backoff with zero added latency
to any single run. A fixed in-process retry would either succeed trivially (if the lock had
already cleared in milliseconds) or add latency for no benefit (if the lock-holder — an editor, an
antivirus scan, a lingering process — needs longer than that). The scan pass gets the same eventual
result without having to guess a sleep duration.

**No byte-count/GB-freed reporting.** `reclaim-worktree-disk.py` reports bytes reclaimed
(`dir_size_bytes`/`_fmt_gb`); `prune-merged-worktrees.py` never has, reporting only paths and
counts. This change matches the file it's actually extending rather than importing a different
sibling's convention for a nicety the issue itself didn't ask for.

---

## Consequences

- **Testing.** `test_worktree_topology.py` (dev-env `## Testing` item 22) gains four cases for
  `find_orphaned_worktree_dirs()`: none-registered-are-orphans, an unregistered dir is found, empty
  `disk_dirs`, and path normalization (a redundant `.` segment still matches the registered
  spelling). `test_prune_merged_worktrees.py` (item 26) gains real-`tempfile.TemporaryDirectory()`
  coverage (matching this file's own established filesystem-test convention) for
  `list_worktree_subdirs()`, `has_git_link()`, and `find_and_remove_orphaned_worktrees()`'s four
  paths — a safe orphan is actually deleted from disk, a directory that still has `.git` is left
  completely untouched, a live-session candidate is left untouched, and an `OSError` from one
  orphan's `shutil.rmtree` is caught and skipped without aborting a second orphan in the same call
  — plus one end-to-end test driving `prune_one()` itself (not just the helper in isolation)
  against a real tmp directory standing in for the primary worktree, proving the wiring folds
  orphan results into the returned `(pruned, skipped)` counts. All new tests initially had a
  scoping bug caught during authoring: several `Path.exists()` assertions sat outside the
  `tempfile.TemporaryDirectory()` `with` block, so Python's own cleanup had already deleted the
  tree before the assertion ran, masking what the implementation actually did — every filesystem
  assertion now runs inside the block. As additional diligence beyond item 22's own text (which
  names `test_worktree_topology.py` itself but not these four by number), the four scripts
  `_worktree_topology.py`'s own module docstring documents as consumers of its shared helpers
  (`test_canonical_mutate_guard.py`, `test_journal_canonical_guard.py`,
  `test_journal_draft_worktree_guard.py`, `test_worktree_path_check.py`) were also re-run and
  pass unchanged, confirming the purely-additive change regressed none of them. The full
  `run-hook-tests.py` suite passes unchanged too.
- **Observability.** One print line per orphan found/removed/skipped, matching the existing
  per-worktree print convention elsewhere in the same loop; the final `Done — pruned N, skipped N`
  line folds orphan counts into the same totals `prune_one()` already returns and reports.
- **Security.** N/A — local filesystem cleanup only, no secrets/PII/auth surface.
- **Resilience / failure modes.** An `OSError` from one orphan's `rmtree` is caught per-orphan,
  never aborts the scan. A missing `.claude/worktrees/` directory (the common no-worktrees-yet
  case) short-circuits to a no-op. An empty `primary` (the repo's `worktrees` list was itself
  empty) short-circuits to `([], [])` rather than resolving a bogus cwd-relative path.
- **Performance.** One `os.scandir()` call per repo per run — cheap, bounded by however many
  worktree-shaped directories exist. No additional git or `gh` subprocess calls: this is pure
  filesystem comparison against the `worktrees` list `prune_one()` already fetched for its own
  main loop.
- **Data integrity.** Never destructive against anything git still registers — the `.git`-absence
  gate is what makes this safe (see Judgment calls). Nothing here touches a branch, a commit, or
  any git ref; it is a plain recursive filesystem delete of a directory git has already fully
  disowned.
- **Maintainability.** `_worktree_topology.py`'s module docstring gains its documented "a fifth
  concern" paragraph, matching the precedent ADR-093 and ADR-105 set when each of them added a new
  concern to the same module.

---

## Alternatives rejected

- **`git worktree remove --force` fallback.** Rejected. `--force` only skips git's own
  uncommitted-changes safety check — and `prune_one()`'s existing `is_dirty()` guard already
  confirms clean *before* `git worktree remove` is ever called, so by the time removal is
  attempted, the one thing `--force` exempts is already satisfied. It does nothing for an
  OS-level lock, a Windows MAX_PATH overflow, or a "directory not empty" partial-delete leftover —
  the three failure classes the issue's own 2026-09-28 comment identified. Adding it would be a
  no-op for this bug's observable behavior, while still incurring the ADR-144 gate-calibration
  burden a "changes what the script will silently do" flag would owe.
- **In-process sleep-based retry-with-backoff at the point of the original failure.** Rejected in
  favor of the unconditional scan-pass's cross-run backoff (see Judgment calls) — a fixed
  in-process retry cannot know how long the actual lock-holder will keep the handle, and a script
  meant to run unattended on a schedule should not block on that guess.
- **Placing the scan in `reclaim-worktree-disk.py` instead.** Rejected — scope mismatch (see
  Judgment calls); that script's whole contract is stripping a regenerable subdirectory *from* an
  otherwise-live worktree, never deleting an entire, no-longer-registered worktree directory.
- **Enabling Windows's global Long Paths policy (`HKLM\...\FileSystem\LongPathsEnabled`) as the fix
  for the "Filename too long" variant.** Rejected — that is a systemwide, elevation-gated OS
  setting change, out of scope for a config-repo automation script to silently flip on the user's
  machine, and the `## Code Quality -> Back up before you mutate` convention would require a
  reversible backup/restore path this one-line automation fix does not warrant. The orphan scan's
  cross-run retry already resolves the same failure once whatever transient lock or partial state
  clears, with no machine-wide policy change needed.

---

## References

- [dev-env#1104](https://github.com/brownm09/dev-env/issues/1104) — the issue this ADR implements,
  including the 2026-09-28 comment that corrected the original "recurring skip" framing to "fully
  silent orphan" and supplied the scale evidence (28 dirs / ~11.2 GB in lifting-logbook)
- `claude/scripts/_worktree_topology.py` — `find_orphaned_worktree_dirs` (new)
- `claude/scripts/prune-merged-worktrees.py` — `worktrees_root`, `list_worktree_subdirs`,
  `has_git_link`, `find_and_remove_orphaned_worktrees` (new); `prune_one()` (extended)
- `claude/scripts/tests/test_worktree_topology.py`,
  `claude/scripts/tests/test_prune_merged_worktrees.py` — new coverage
- [ADR-051](051-worktree-liveness-guard.md) — the transcript-mtime liveness guard this reuses
  unchanged
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — the original
  topology-diagnosis-plus-non-destructive-correction precedent in this same module/script pair
- [ADR-105](105-draft-branch-worktree-squat-guard.md) — the closest structural analog: a new pure
  detection helper added to `_worktree_topology.py` and wired into the same `prune_one()` loop
- [ADR-144](144-gate-calibration-pass-3-dimension.md) — cited in Alternatives rejected for why a
  `--force` behavior change was avoided rather than added without a demonstrated need
