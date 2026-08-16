#!/usr/bin/env python3
"""Shared engineering-journal canonical-path resolution (dev-env#982).

Four hooks independently duplicated the "compare a resolved canonical path against the
engineering-journal path" pattern, each with its own module-level constant, env-var
override, and (in three of the four) its own copy of a normalize-for-comparison scheme:

  - `pre-tool-use-canonical-mutate-guard.py`'s `_REDIRECT_TARGET_ALLOWLIST` (frozenset) /
    `CANONICAL_MUTATE_GUARD_JOURNAL_PATH` -- normalizes both at construction and at
    comparison time in `_is_allowlisted_root()`.
  - `journal-canonical-guard.py`'s `JOURNAL_REPO` (`Path`) /
    `JOURNAL_CANONICAL_GUARD_REPO_PATH` -- no normalization at all; used only as a
    subprocess `cwd=`, `.is_dir()`, and interpolated into printed advisory text, never
    equality-compared.
  - `pre-tool-use-journal-draft-worktree-guard.py`'s `JOURNAL_REPO` (str) /
    `JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH` -- normalizes at construction;
    `_is_journal_canonical()` normalizes the candidate the same way at comparison time.
  - `pre-tool-use-worktree-path-check.py`'s `_JOURNAL_ROOT` (str) /
    `WORKTREE_PATH_CHECK_JOURNAL_PATH` -- normalized via that file's own general-purpose
    `_normalize()` helper, which is ALSO used for unrelated worktree-path comparisons in
    the same file (`worktree_norm`, `file_norm`, `_worktree_is_live`,
    `_resolve_worktree_scope`) and stays as a locally-named wrapper for those call sites --
    but its body now delegates to `normalize_journal_path()` below (dev-env#982 review),
    so there is exactly one algorithm, not a second copy.

`pre-tool-use-canonical-mutate-guard.py` also has its own long-standing, unrelated
`_normalize_path()` helper (worktree-liveness comparisons, `_is_live_worktree()` --
predates this extraction and is out of scope for it) that happens to implement the
identical `os.path.normcase(os.path.normpath(...))` algorithm. Its body now delegates to
`normalize_journal_path()` too (dev-env#982 review), for the same reason: two hand-written
copies of one algorithm drift silently, and this one backs a blocking guard's exemption
list just as directly as `_is_allowlisted_root()` does.

This module single-sources the two orthogonal halves of that pattern:

  - `resolve_journal_path(env_var, default=DEFAULT_JOURNAL_PATH)` -- env-override-or-
    default, UNNORMALIZED. `journal-canonical-guard.py` is the reason this exists as its
    own function: it needs the raw, human-readable path (proper case, its own separator
    style) for a subprocess `cwd=`, `Path.is_dir()`, and printed advisory text --
    normalizing it there would degrade message readability (backslash-and-lowercase a
    path a human reads) for a purely cosmetic reason, since Windows path APIs are already
    case/separator-insensitive. An explicitly-empty env-var override (`VAR=""`) is treated
    as "not set" and falls back to `default` rather than resolving to `""` -- this value
    backs a blocking security exemption list in two of the four consumers, so a degenerate
    override should not silently produce a degenerate (and, post-normalization, non-empty:
    `"." `) allowlist entry (dev-env#982 review).
  - `normalize_journal_path(path)` -- the one canonical normalization for EQUALITY
    COMPARISON only, `os.path.normcase(os.path.normpath(path or ""))`. Chosen over the
    `.replace("\\","/").rstrip("/").lower()` scheme two of the four hooks used
    historically because it also collapses `..`/`.` segments and repeated separators,
    which the manual scheme does not (verified: `"Git//engineering-journal"` and
    `"Git/foo/../engineering-journal"` are left uncollapsed by the manual scheme, which
    would falsely mismatch a git-resolved toplevel containing either shape -- never
    observed in practice since `git rev-parse --show-toplevel` never emits them, but a
    latent correctness gap the manual scheme carried). This is now the ONE implementation
    of the algorithm in the repo -- `pre-tool-use-worktree-path-check.py`'s `_normalize()`
    and `pre-tool-use-canonical-mutate-guard.py`'s `_normalize_path()` both delegate to it
    (dev-env#982 review) rather than carrying their own byte-identical copies.

    CAUTION for any future caller: the lexical `..`/`.` collapsing is only sound when
    `path` is already a git-resolved, filesystem-real toplevel (`git rev-parse
    --show-toplevel`'s output, which is what every current call site passes) -- it does
    NOT resolve through symlinks or junctions the way `os.path.realpath()` would. A raw,
    command-string-derived path containing `..` could lexically collapse into the journal
    allowlist entry without actually being that directory. Not exploitable today (traced:
    every caller passes an already git-resolved path), but do not repurpose this function
    for a raw, unresolved path without switching to `os.path.realpath()` first.

    The two schemes disagree only on empty-string input -- `"" -> ""` (old) vs. `"" -> "."`
    (new, since `os.path.normpath("")` is `"."`) -- and every existing call site across all
    four hooks provably never passes an empty string here: `root`/candidate values always
    come from `git rev-parse --show-toplevel`'s `strip() or None` contract, gated behind an
    `if root and ...` / `if root is None: continue` check before ever reaching the
    comparison, and no test overrides any of the four env vars to the empty string. See
    `test_journal_canon.py`'s `test_normalize_journal_path_pins_empty_input_divergence`.

Each hook keeps its OWN env-var name (backward-compat with its own existing test suite --
matches the ADR-073/`_worktree_canon.py` precedent of preserving each caller's own
contract rather than forcing convergence) and its own local constant name/shape
(`_REDIRECT_TARGET_ALLOWLIST` a frozenset, `JOURNAL_REPO` a `Path` in one hook and a str in
another, `_JOURNAL_ROOT` a str), built by calling the two functions above.

`DEFAULT_JOURNAL_PATH` converges `journal-canonical-guard.py`'s former
`Path.home() / "Git" / "engineering-journal"` default onto the same hardcoded literal the
other three hooks already used -- verified on the deployed machine to be a true no-op
(`Path.home() / "Git" / "engineering-journal" == Path("C:/Users/brown/Git/engineering-journal")`
is `True`; this is a single-user, machine-specific environment where this exact path is
already hardcoded pervasively elsewhere in this codebase).

Pure -- no I/O, no subprocess -- exercised offline in `tests/test_journal_canon.py`.
Imported the same way as `_hookio` / `_worktree_canon` / `_hookutil`: a sibling module in
`scripts/` resolved via `sys.path` (the `pyw -3` hook launcher puts the script's own
directory on `sys.path`; the test harness does the same with
`sys.path.insert(0, scripts_dir)`).

See ADR-133.
"""
from __future__ import annotations

import os

# The literal all four hooks hardcoded independently before this extraction. Single-sourced
# here so no hook needs its own copy of the string; also the converged default for
# journal-canonical-guard.py, whose former default was
# `Path.home() / "Git" / "engineering-journal"` (see module docstring for why this is a
# verified no-op, not a speculative harmonization).
DEFAULT_JOURNAL_PATH = "C:/Users/brown/Git/engineering-journal"


def resolve_journal_path(env_var: str, default: str = DEFAULT_JOURNAL_PATH) -> str:
    """Env-override-or-default engineering-journal path, UNNORMALIZED -- raw,
    human-readable casing/separators preserved exactly as the environment or `default`
    provided them.

    This is the shape a consumer needs when using the value as an actual filesystem path:
    `journal-canonical-guard.py`'s `JOURNAL_REPO` wraps this directly in `Path(...)` for a
    subprocess `cwd=`, `.is_dir()`, and interpolation into printed advisory text -- none of
    which want a lowercased/backslash-ified value. Consumers that instead need this for an
    EQUALITY COMPARISON (`pre-tool-use-canonical-mutate-guard.py`,
    `pre-tool-use-journal-draft-worktree-guard.py`, `pre-tool-use-worktree-path-check.py`)
    pass this straight into `normalize_journal_path()`.

    An env var explicitly set to the empty string is treated the same as unset (falls back
    to `default`), not as an override to `""` -- `os.environ.get(env_var) or default`,
    not `os.environ.get(env_var, default)`. Two of the four consumers feed this straight
    into a blocking guard's exemption allowlist; a degenerate empty override should not
    silently produce a degenerate allowlist entry (dev-env#982 review).
    """
    return os.environ.get(env_var) or default


def normalize_journal_path(path: str | None) -> str:
    """The one canonical normalization for EQUALITY COMPARISON purposes only:
    `os.path.normcase(os.path.normpath(path or ""))`.

    Consumed at BOTH construction time (building a hook's own module-level constant from
    `resolve_journal_path()`'s output) and comparison time (normalizing the candidate root
    being checked against it) by `pre-tool-use-canonical-mutate-guard.py`'s
    `_is_allowlisted_root()` and `pre-tool-use-journal-draft-worktree-guard.py`'s
    `_is_journal_canonical()`. `pre-tool-use-worktree-path-check.py` also delegates its
    local `_normalize()` to this function at both construction and comparison time --
    `_normalize()` stays as a locally-named wrapper (not inlined away) because it is also
    called from four other, non-journal call sites in that file (`worktree_norm`,
    `file_norm`, `_worktree_is_live`, `_resolve_worktree_scope`), which are out of scope for
    this extraction. `pre-tool-use-canonical-mutate-guard.py`'s own unrelated
    `_normalize_path()` (worktree-liveness comparisons) delegates the same way, for the
    same reason its call sites keep their own local name.

    NEVER called with an empty/None `path` by any of the four hooks' actual call sites
    today (see module docstring) -- the `path or ""` guard exists so this degrades safely
    (to `"."`, matching `os.path.normpath("")`) rather than raising, should that ever
    change.
    """
    return os.path.normcase(os.path.normpath(path or ""))
