# ADR 073 — Shared `_worktree_canon` and `_gh_project` Modules for the Project-Board Hook Pair

**Date:** 2026-07-01
**Status:** Accepted
**Tags:** hooks, post-tool-use, github-project, worktree, reconciliation, shared-module, maintainability, dry

---

## Context

`reconcile-project-board.py` (merged in PR #451, ADR-068 — the nightly/on-demand backstop
that adds orphaned issues to a GitHub Project board) duplicated two pieces of logic that
already existed in `post-tool-use.py` (the PostToolUse hook that adds newly-created
issues/PRs to the same board) instead of sharing them:

1. **Worktree canonicalization** — an identical `_WORKTREE_RE` regex, each wrapped by a
   function with a *different* no-match contract. `post-tool-use.py`'s
   `canonical_root_from_worktree(cwd) -> str | None` returns `None` on no match; its
   caller, `load_config()`, uses that to decide whether to fall through to a *different*,
   non-duplicated sibling-worktree git fallback (`canonical_root_via_git`). reconcile-
   project-board.py's `canonical_repo_root(path) -> str` returns `path` unchanged on no
   match; its caller, `default_repo_root()`, always holds a real path derived from
   `__file__` and does `os.path.join(repo_root, CONFIG_FILE)` on the result — `None`
   there would raise `TypeError`.
2. **`add_to_project()`** — two independent `gh project item-add` subprocess wrappers with
   different signatures, return shapes, timeouts, and encoding handling.

Filed as [issue #454](https://github.com/brownm09/dev-env/issues/454) as a maintainability
finding rather than fixed inline in #451 (touching a second, already-shipped hook was out
of scope there). Continues this repo's shared-module line: `_hookio` (ADR-050) →
`_worktree_liveness` (ADR-051) → `_journal_shards` (ADR-057) → `_worktree_topology`
(ADR-058) → `_hookutil` (ADR-064) → `_repo_scan` (ADR-072, merged concurrently with this
work — also extracted for `reconcile-project-board.py`, among others). Every one of those
six modules is pure (no `_winsubp`, no `subprocess`) — confirmed directly, not assumed,
including `_worktree_liveness.py`'s own docstring ("import-only... no subprocess") and
`_repo_scan.py`'s `find_git_repos` (filesystem `os.scandir` only).

This PR was planned against an earlier `reconcile-project-board.py` (single-repo only, no
`--scan-dir`). By the time the branch was cut, `--scan-dir` (dev-env#462, ADR-070) and
`_repo_scan.py` (ADR-072) had both merged, moving `add_to_project`'s call site into a new
`_reconcile_repo()` helper and consuming ADR numbers 070–072. The two functions extracted
here are byte-identical in the post-`--scan-dir` file to what was originally read; only
their surrounding context shifted. This is a recurring hazard in this repo — multiple
concurrent sessions land PRs against the same files — and is why this ADR re-verified
`origin/main` and `docs/adr/INDEX.md` immediately before writing, rather than trusting
numbers/line references gathered during planning.

## Decision

1. **New `claude/scripts/_worktree_canon.py`** — pure, holds `_WORKTREE_RE` once and
   exposes both contracts as thin wrappers over one match:
   `canonical_repo_root(path) = canonical_root_from_worktree(path) or (path or "")`.
   Not folded into `_worktree_topology.py` despite the similar subject matter: that
   module already has an unrelated `canonical_worktree(worktrees)` — the first entry of
   a *parsed* `git worktree list --porcelain` result, a different signature and meaning
   entirely — so reusing the "canonical" name there for a completely different
   path-regex operation would be a readability trap. `_worktree_topology.py` is also
   policy-free at the *branch-topology* level (parses `git worktree list`); this module
   is pure at the *path-string* level (a regex match, no git calls at all). The
   `_worktree_canon.py` name itself was offered directly by issue #454 as one of two
   acceptable options for this piece.

2. **New `claude/scripts/_gh_project.py`** — the line's first genuinely *impure* shared
   module (it wraps a live `subprocess.run` call to `gh`). Reconciles the two
   `add_to_project`s onto the superset shape:
   - Returns `(item_id, stderr_or_exception_str)` — reconcile-project-board.py's shape.
     `post-tool-use.py`'s caller discards the second element:
     `item_id, _ = add_to_project(...)`.
   - `encoding="utf-8"` always (reconcile-project-board.py's original behavior, now
     shared) — a **deliberate behavior change** for `post-tool-use.py`'s call site: it
     previously used no explicit encoding (OS default locale — cp1252 on Windows),
     which could raise `UnicodeDecodeError` on a non-ASCII issue title, silently
     swallowed by its old bare `except Exception: return None`. `gh` emits UTF-8 JSON,
     so this is a correctness fix, not a risky change.
   - `timeout` is keyword-only, defaulting to 20 (`post-tool-use.py`'s original —
     tighter, a live interactive-session hook). `reconcile-project-board.py`'s call
     site passes `timeout=30` explicitly to preserve its own original, looser
     (unattended-batch) timeout exactly.
   - Imports `_winsubp` itself, even though the entry-point script already will have —
     the patch is a one-time, idempotent mutation of `subprocess.Popen.__init__`
     guarded on the `subprocess` module object itself (not per-importing-module), so a
     second import is a true no-op. Self-contained per `_winsubp.py`'s own instruction
     to place the import "near the top of any hook script that spawns subprocesses."
   - Not unit-tested: the `subprocess.run` call is a live `gh` network boundary,
     matching this repo's no-subprocess-mock convention — neither original
     `add_to_project` was tested either.

3. **Not bundled into one module.** Issue #454 only offered the fold-into-
   `_worktree_topology.py`-or-new-module choice for the canon piece; it never proposed
   merging canon resolution with the `gh` wrapper. The two concerns (path regex vs. a
   `gh` subprocess wrapper) are unrelated domains that happen to share two callers —
   closer to ADR-064's own *rejected* "merge into `_hookio`" alternative (different
   hook family, would blur the boundary) than to its *accepted* sentinel+transcript-
   locate bundle (which shared one real audience name, "Stop/UserPromptSubmit hook
   family"). Neither `_worktree_canon` nor `_gh_project` has a comparable shared
   audience name — each stands alone, one clearly-named concern per module, matching
   the majority precedent (`_hookio`, `_worktree_topology`, `_journal_shards`,
   `_repo_scan` are each single-concern; only `_hookutil` bundles, for its own
   stated reason).

4. `post-tool-use.py` and `reconcile-project-board.py` both import and delegate; local
   definitions removed. `post-tool-use.py`'s `_canonical_root_from_common_dir` /
   `canonical_root_via_git` (the sibling-worktree git fallback) are untouched — not
   duplicated anywhere, out of scope to move.

5. New `test_worktree_canon.py` pins the shared regex match AND the two functions'
   divergent no-match contracts side by side — the reconciliation itself, not just each
   function's happy path. Existing `test_post_tool_use.py` / `test_reconcile_project_board.py`
   require no edits: `from X import Y` binds the same function object into the
   `exec_module`-loaded module's namespace, so `post_tool_use.canonical_root_from_worktree`
   / `mod.canonical_repo_root` resolve identically whether the function is defined
   locally or imported — verified by running both files unchanged after the extraction
   (56 tests total across the three files, all green).

## Considered alternatives

- **Fold canon into `_worktree_topology.py`.** Rejected — naming collision
  (`canonical_worktree` already means something unrelated there) and domain mismatch
  (branch topology vs. path-string regex).
- **One bundled module for both concerns.** Rejected — see Decision point 3.
- **Leave the duplication, re-sync on drift.** Rejected, same rationale as every prior
  extraction in this line: duplication already spans two files and would grow with the
  next project-board script.
- **Pick one no-match contract and change the other caller to match.** Rejected — both
  callers' current behavior is correct for their own use; forcing one contract onto the
  other caller would either lose the None-signals-"try the git fallback next" behavior
  in `post-tool-use.py`, or introduce a `TypeError` risk in `reconcile-project-board.py`.

## Consequences

- `post-tool-use.py`: -19 LOC (regex + wrapper function + `add_to_project`). `reconcile-
  project-board.py`: -27 LOC (regex + wrapper function + `add_to_project`), offset by
  three new import lines and an expanded docstring.
- Two new modules become the shared source of truth for these two concerns; both
  consumed by exactly two files today, structured to generalize if a third
  project-board script appears (mirroring how `_repo_scan.py` just generalized from two
  consumers to three).
- Intentional, narrow behavior change: `post-tool-use.py`'s `add_to_project` call now
  forces UTF-8 decoding of `gh`'s stdout/stderr instead of the OS default locale.
- `_gh_project.py` is this line's first impure shared module — six pure predecessors
  (`_hookio`, `_worktree_liveness`, `_journal_shards`, `_worktree_topology`, `_hookutil`,
  `_repo_scan`) all confirmed pure at the time of writing.
- `docs/REFERENCE.md` gets a new paragraph (mirroring `_repo_scan.py`'s) noting both
  modules as non-invoked library modules, and the `post-tool-use.py` PostToolUse-hooks
  row is updated to cite them.

## References

- [Issue #454](https://github.com/brownm09/dev-env/issues/454), [PR #451](https://github.com/brownm09/dev-env/pull/451).
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md), [ADR-057](057-shared-journal-shard-reader.md),
  [ADR-058](058-worktree-squatting-main-detection-correction.md), [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md),
  [ADR-068](068-reconcile-project-board-orphan-issues.md), [ADR-070](070-reconcile-project-board-scan-dir.md),
  [ADR-072](072-shared-repo-scan-module.md) — the shared-module and reconcile-project-board precedents this builds on.
- `claude/scripts/_worktree_canon.py`, `claude/scripts/_gh_project.py`,
  `claude/scripts/tests/test_worktree_canon.py`.
