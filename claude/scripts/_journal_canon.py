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
    the same file and stays local (not extracted here) -- only the `_JOURNAL_ROOT`
    constant's construction moves.

This module single-sources the two orthogonal halves of that pattern:

  - `resolve_journal_path(env_var, default=DEFAULT_JOURNAL_PATH)` -- env-override-or-
    default, UNNORMALIZED. `journal-canonical-guard.py` is the reason this exists as its
    own function: it needs the raw, human-readable path (proper case, its own separator
    style) for a subprocess `cwd=`, `Path.is_dir()`, and printed advisory text --
    normalizing it there would degrade message readability (backslash-and-lowercase a
    path a human reads) for a purely cosmetic reason, since Windows path APIs are already
    case/separator-insensitive.
  - `normalize_journal_path(path)` -- the one canonical normalization for EQUALITY
    COMPARISON only, `os.path.normcase(os.path.normpath(path or ""))`. Chosen over the
    `.replace("\\","/").rstrip("/").lower()` scheme two of the four hooks used
    historically because it also collapses `..`/`.` segments and repeated separators,
    which the manual scheme does not (verified: `"Git//engineering-journal"` and
    `"Git/foo/../engineering-journal"` are left uncollapsed by the manual scheme, which
    would falsely mismatch a git-resolved toplevel containing either shape -- never
    observed in practice since `git rev-parse --show-toplevel` never emits them, but a
    latent correctness gap the manual scheme carried). Identical, byte-for-byte, to
    `pre-tool-use-worktree-path-check.py`'s own pre-existing local `_normalize()` -- that
    file's construction-site call is swapped for this shared one, but its OTHER,
    non-journal uses of its local helper are untouched. The two schemes disagree only on
    empty-string input -- `"" -> ""` (old) vs. `"" -> "."` (new, since
    `os.path.normpath("")` is `"."`) -- and every existing call site across all four hooks
    provably never passes an empty string here: `root`/candidate values always come from
    `git rev-parse --show-toplevel`'s `strip() or None` contract, gated behind an
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
    """
    return os.environ.get(env_var, default)


def normalize_journal_path(path: str) -> str:
    """The one canonical normalization for EQUALITY COMPARISON purposes only:
    `os.path.normcase(os.path.normpath(path or ""))`.

    Consumed at BOTH construction time (building a hook's own module-level constant from
    `resolve_journal_path()`'s output) and comparison time (normalizing the candidate root
    being checked against it) by `pre-tool-use-canonical-mutate-guard.py`'s
    `_is_allowlisted_root()` and `pre-tool-use-journal-draft-worktree-guard.py`'s
    `_is_journal_canonical()`. `pre-tool-use-worktree-path-check.py` uses this only at
    construction time for its `_JOURNAL_ROOT` constant -- its comparison-time
    normalization stays its own pre-existing, byte-identical local `_normalize()` helper,
    since that helper also serves unrelated non-journal comparisons in the same file and
    is out of scope for this extraction.

    NEVER called with an empty/None `path` by any of the four hooks' actual call sites
    today (see module docstring) -- the `path or ""` guard exists so this degrades safely
    (to `"."`, matching `os.path.normpath("")`) rather than raising, should that ever
    change.
    """
    return os.path.normcase(os.path.normpath(path or ""))
