# ADR-133 — Shared `_journal_canon.py` Module for the Engineering-Journal Canonical-Path Pattern

**Date:** 2026-08-15
**Status:** Accepted
**Closes:** [dev-env#982](https://github.com/brownm09/dev-env/issues/982)
**Tags:** hooks, journal, canonical, shared-module, maintainability, dry, pre-tool-use, user-prompt-submit
**Related:** [ADR-071](071-canonical-checkout-mutate-guard-hook.md), [ADR-073](073-shared-worktree-canon-gh-project-modules.md), [ADR-093](093-journal-canonical-hijack-guard.md), [ADR-105](105-draft-branch-worktree-squat-guard.md), [ADR-024](024-worktree-path-guard-hook.md)

---

## Context

Four dev-env hooks each independently defined the "compare a resolved canonical path
against the engineering-journal path" pattern — a module-level constant, an env-var
override, and (in three of the four) a normalize-for-comparison scheme — with real
divergence in every dimension:

| Hook | Constant | Env var | Default | Normalization |
|---|---|---|---|---|
| `pre-tool-use-canonical-mutate-guard.py` | `_REDIRECT_TARGET_ALLOWLIST` (frozenset) | `CANONICAL_MUTATE_GUARD_JOURNAL_PATH` | literal `"C:/Users/brown/Git/engineering-journal"` | `.replace("\\","/").rstrip("/").lower()` |
| `journal-canonical-guard.py` | `JOURNAL_REPO` (`Path`) | `JOURNAL_CANONICAL_GUARD_REPO_PATH` | `Path.home()/"Git"/"engineering-journal"` | none — never compared, only used as subprocess `cwd=`/`.is_dir()`/printed text |
| `pre-tool-use-journal-draft-worktree-guard.py` | `JOURNAL_REPO` (str) | `JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH` | literal `"C:/Users/brown/Git/engineering-journal"` | `.replace("\\","/").rstrip("/").lower()` |
| `pre-tool-use-worktree-path-check.py` | `_JOURNAL_ROOT` (str) | `WORKTREE_PATH_CHECK_JOURNAL_PATH` | literal `"C:/Users/brown/Git/engineering-journal"` | file-local `_normalize()` = `os.path.normcase(os.path.normpath(...))` |

The fourth consumer (`pre-tool-use-worktree-path-check.py`'s own carve-out) was added in
[PR #981](https://github.com/brownm09/dev-env/pull/981), closing dev-env#750 reopened. Per
[ADR-105](105-draft-branch-worktree-squat-guard.md)'s "Judgment calls" section, which cites
"this codebase's own stated convention of tolerating duplication through two consumers
before extracting" for an analogous case, four independent consumers is past that
threshold. dev-env#982 was filed at the point the fourth consumer landed, as the trigger
for this extraction — not a report of anything currently broken.

Every consumer's own docstring already cross-references the others ("mirrors
pre-tool-use-canonical-mutate-guard.py's..."), confirming this was always meant to be one
concept, never unified.

Continues this repo's shared-module line: `_hookio` (ADR-050) → `_worktree_liveness`
(ADR-051) → `_journal_shards` (ADR-057) → `_worktree_topology` (ADR-058) → `_hookutil`
(ADR-064) → `_repo_scan` (ADR-072) → `_worktree_canon`/`_gh_project` (ADR-073) → ... →
`_journal_canon` (this ADR).

## Decision

1. **New `claude/scripts/_journal_canon.py`**, pure, modeled directly on
   `_worktree_canon.py`'s house style (long rationale docstring citing all four consumers
   and why each function exists, `from __future__ import annotations`, per-function
   docstrings naming consumers, no I/O). Exposes two composable primitives rather than one
   combined function:

   - `resolve_journal_path(env_var, default=DEFAULT_JOURNAL_PATH) -> str` — env-override-
     or-default, **unnormalized**. `journal-canonical-guard.py` is the reason this exists
     on its own: it interpolates `JOURNAL_REPO` into printed advisory text
     (`git -C {JOURNAL_REPO} checkout main`) and uses it as a literal subprocess `cwd=`/
     `Path.is_dir()` — normalizing it there would lowercase/backslash-ify a path a human
     reads, for zero behavioral benefit (Windows path APIs are already case/separator-
     insensitive).
   - `normalize_journal_path(path) -> str` — the one canonical EQUALITY-COMPARISON
     normalization, `os.path.normcase(os.path.normpath(path or ""))`. Consumed at both
     construction time (building a hook's own constant) and comparison time (normalizing
     the candidate) by the two hooks that do direct equality comparison
     (`pre-tool-use-canonical-mutate-guard.py`, `pre-tool-use-journal-draft-worktree-guard.py`);
     consumed only at construction time by `pre-tool-use-worktree-path-check.py`, whose
     own comparison-time normalization stays its pre-existing, byte-identical local
     `_normalize()` — that helper also serves unrelated non-journal comparisons in the
     same file (`worktree_norm`, `file_norm`, `_worktree_is_live`,
     `_resolve_worktree_scope`) and is out of scope for this extraction.

2. **Normalization choice: `os.path.normcase(os.path.normpath(path or ""))`, not the
   `.replace("\\","/").rstrip("/").lower()` scheme two hooks used.** Chosen over the
   manual scheme because it also collapses `.`/`..` segments and repeated separators,
   which the manual scheme silently leaves uncollapsed — verified directly:
   `"Git//engineering-journal"` and `"Git/foo/../engineering-journal"` are NOT collapsed
   by the manual scheme, which would falsely mismatch a git-resolved toplevel containing
   either shape. Never observed in practice, since `git rev-parse --show-toplevel` never
   emits such segments, but a latent correctness gap the manual scheme carried. The new
   scheme is byte-identical to `pre-tool-use-worktree-path-check.py`'s own pre-existing
   local `_normalize()` — confirmed empirically (both schemes agree on every real-world,
   git-resolved-toplevel-shaped input: forward-slash, backslash, trailing-slash,
   mixed-case). The **only** divergence found is empty-string input — `"" -> ""` under the
   manual scheme vs. `"" -> "."` under the new one (`os.path.normpath("")` is `"."`) —
   traced every call site across all four hooks and confirmed it is unreachable in
   practice: `root`/candidate values always come from `git rev-parse --show-toplevel`'s
   `strip() or None` contract, gated behind an `if root and ...` /
   `if root is None: continue` check before ever reaching the comparison, and no existing
   test overrides any of the four env vars to the empty string. Documented explicitly in
   the module docstring and pinned in `test_journal_canon.py`'s
   `test_normalize_journal_path_pins_empty_input_divergence`, matching `_worktree_canon.py`'s
   own precedent of naming understood-but-unreachable divergences rather than leaving them
   silent.

3. **Default harmonization: `journal-canonical-guard.py`'s default moves from
   `Path.home()/"Git"/"engineering-journal"` to the shared `DEFAULT_JOURNAL_PATH`
   literal** (the same string the other three hooks already hardcoded). Verified a true
   no-op on the deployed machine: `Path.home()/"Git"/"engineering-journal" ==
   Path("C:/Users/brown/Git/engineering-journal")` evaluates `True`. No test in
   `test_journal_canonical_guard.py` exercises the true default — every test overrides the
   env var to a disposable temp dir — so there is nothing to break even in principle. This
   is a single-user, machine-specific environment where this exact literal is already
   hardcoded pervasively elsewhere in this codebase.

4. **Each hook keeps its own env-var name and local constant name/shape** —
   `_REDIRECT_TARGET_ALLOWLIST` stays a frozenset, `JOURNAL_REPO` stays a `Path` in one
   hook and a plain str in another, `_JOURNAL_ROOT` stays a str — built by calling the two
   shared functions. Explicit backward-compatibility decision, matching
   [ADR-073](073-shared-worktree-canon-gh-project-modules.md)/`_worktree_canon.py`'s
   precedent of preserving each caller's own contract (there: divergent no-match return
   shapes) rather than forcing convergence onto one name/shape.

5. `pre-tool-use-worktree-path-check.py`'s general-purpose `_normalize()` helper is
   **explicitly not touched or extracted** — only its `_JOURNAL_ROOT` construction site
   changes to call `_journal_canon.resolve_journal_path()`; the helper's other four call
   sites in that file, and the comparison in `main()`, are untouched.

6. All four existing hook test suites (`test_canonical_mutate_guard.py`,
   `test_journal_canonical_guard.py`, `test_journal_draft_worktree_guard.py`,
   `test_worktree_path_check.py`) required **zero edits** — confirmed by running each
   after the refactor, not just by tracing. Every journal-carveout test in these files
   either asserts only a boolean end-to-end outcome against fixtures that normalize
   identically under both schemes, or overrides the env var to a disposable temp dir and
   never exercises the default/normalization directly.

## Considered alternatives

- **Converge all four hooks onto one shared env-var name.** Rejected — breaks each hook's
  existing test-injectable-override contract for no benefit; matches ADR-073's identical
  rejection of forcing one no-match contract onto both its callers.
- **Fold into `_worktree_canon.py`.** Rejected — unrelated domain (comparing a resolved
  path against one fixed engineering-journal path vs. matching an arbitrary path against a
  worktree-directory-shape regex), no shared audience name between the two concerns.
  Mirrors ADR-073's own "not bundled into one module" reasoning for not merging
  `_worktree_canon.py` and `_gh_project.py`.
- **Special-case empty input to match the old scheme's `"" -> ""` behavior exactly.**
  Rejected as unneeded complexity for an input no real call site produces — documented as
  a known, pinned divergence instead of silently masked.
- **Leave the duplication, re-sync on drift.** Rejected, same rationale as every prior
  extraction in this line — duplication already spans four files, past this codebase's own
  stated two-consumer tolerance threshold.

## Consequences

- Each of the four hooks loses ~5–10 LOC of duplicated constant/normalization logic,
  offset by one new import line each.
- One new pure, non-invoked library module (`_journal_canon.py`) — no `docs/REFERENCE.md`
  Utilities-table row, matching the established convention for `_worktree_canon.py`/
  `_gh_project.py`/`_repo_scan.py` (a prose paragraph near the consumer hooks' entries
  instead).
- One new test file (`test_journal_canon.py`, 11 cases) pinning the normalization choice,
  its equivalence with both legacy schemes on real-world inputs, and the documented
  empty-input divergence.
- Zero edits to any of the four existing hook test files — verified by running all four
  suites unchanged after the refactor (87 + 6 + 27 + 16 tests, all passing).
- One intentional, verified-no-op behavior convergence:
  `journal-canonical-guard.py`'s default source.
- `docs/adr/INDEX.md`, `claude/scripts/README.md`, `claude/scripts/tests/README.md`,
  `docs/REFERENCE.md`, `CLAUDE.md`'s `## Testing` index, and `docs/TESTING.md` all gain a
  reference to this module/test in the same PR (doc-reconciliation checkpoint).

## References

- [dev-env#982](https://github.com/brownm09/dev-env/issues/982) — the filed issue.
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — `pre-tool-use-canonical-mutate-guard.py`'s
  `_REDIRECT_TARGET_ALLOWLIST` carve-out this extraction touches.
- [ADR-105](105-draft-branch-worktree-squat-guard.md) — `pre-tool-use-journal-draft-worktree-guard.py`'s
  origin, and the source of the "tolerating duplication through two consumers" convention
  this issue acts on.
- [ADR-024](024-worktree-path-guard-hook.md) — `pre-tool-use-worktree-path-check.py`'s
  origin and its 2026-08-15 addendum adding the fourth consumer (dev-env#750 reopened,
  PR #981).
- [ADR-093](093-journal-canonical-hijack-guard.md) — `journal-canonical-guard.py`'s origin.
- [ADR-073](073-shared-worktree-canon-gh-project-modules.md) — `_worktree_canon.py`/
  `_gh_project.py`, the directly-applicable precedent this extraction's structure and
  "keep each caller's own contract" decision follow.
- `claude/scripts/_journal_canon.py`, `claude/scripts/tests/test_journal_canon.py` — the
  new module and its tests.
