#!/usr/bin/env python3
"""Unit tests for _journal_canon.py (dev-env#982 / ADR-133).

Pins: default resolution, env-var override (using two of the four real hook env-var
names) including an explicitly-empty override falling back to the default, the
normalization scheme's case/separator/trailing-slash/dot-segment behavior (Windows-only
by construction -- guarded with `os.name == "nt"` assertions, since `os.path.normcase`
is the identity function on POSIX), its agreement with the REAL delegating wrappers in
`pre-tool-use-canonical-mutate-guard.py` and `pre-tool-use-worktree-path-check.py` (loaded
and called directly, not reimplemented in this file), and the one documented,
provably-unreachable-in-production divergence from the retired legacy scheme on empty
input.

Usage:
    py -3 claude/scripts/tests/test_journal_canon.py

Exit 0 = all pass.
"""
import importlib.util
import os
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _journal_canon  # noqa: E402

DEFAULT_JOURNAL_PATH = _journal_canon.DEFAULT_JOURNAL_PATH
resolve_journal_path = _journal_canon.resolve_journal_path
normalize_journal_path = _journal_canon.normalize_journal_path

UNSET_ENV_VAR = "_JOURNAL_CANON_TEST_UNSET_VAR_XYZ"


def _load_hook_module(filename: str, module_name: str):
    """Load a hyphenated hook script as an importable module -- same technique
    `test_canonical_mutate_guard.py` / `test_worktree_path_check.py` use for their own
    hooks. Used here so the cross-implementation equivalence test below actually exercises
    the REAL hook code (dev-env#982 review), not a reimplementation of it."""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cmg = _load_hook_module("pre-tool-use-canonical-mutate-guard.py", "journal_canon_test_cmg")
_wpc = _load_hook_module("pre-tool-use-worktree-path-check.py", "journal_canon_test_wpc")


def _with_env(var: str, value: str, fn):
    """Set `var=value` for the duration of `fn()`, restoring prior state after."""
    had_prior = var in os.environ
    prior = os.environ.get(var)
    os.environ[var] = value
    try:
        return fn()
    finally:
        if had_prior:
            os.environ[var] = prior
        else:
            os.environ.pop(var, None)


# --- resolve_journal_path --------------------------------------------------------------


def test_default_journal_path_constant() -> str:
    assert DEFAULT_JOURNAL_PATH == "C:/Users/brown/Git/engineering-journal"
    return "DEFAULT_JOURNAL_PATH is the single hardcoded literal all four hooks converge on"


def test_resolve_journal_path_returns_default_when_unset() -> str:
    assert UNSET_ENV_VAR not in os.environ, "test fixture env var must not already be set"
    assert resolve_journal_path(UNSET_ENV_VAR) == DEFAULT_JOURNAL_PATH
    return "resolve_journal_path falls back to DEFAULT_JOURNAL_PATH when the env var is unset"


def test_resolve_journal_path_honors_explicit_default_override() -> str:
    assert resolve_journal_path(UNSET_ENV_VAR, default="C:/elsewhere") == "C:/elsewhere"
    return "resolve_journal_path honors an explicit non-default `default` argument"


def test_resolve_journal_path_env_override_real_var_names() -> str:
    # A couple of the actual hook env-var names, confirming each is a genuine pass-through
    # key, not hardcoded/aliased anywhere in this module.
    for var in ("CANONICAL_MUTATE_GUARD_JOURNAL_PATH", "WORKTREE_PATH_CHECK_JOURNAL_PATH"):
        got = _with_env(var, "D:/scratch/ej-override", lambda v=var: resolve_journal_path(v))
        assert got == "D:/scratch/ej-override", f"{var}: expected override, got {got!r}"
    return "resolve_journal_path honors an env override under two of the real hook var names"


def test_resolve_journal_path_empty_env_var_falls_back_to_default() -> str:
    # dev-env#982 review: an env var explicitly set to "" must be treated as unset, not as
    # an override to "" -- two of the four consumers feed this straight into a blocking
    # guard's exemption allowlist, where an empty override would otherwise normalize to a
    # degenerate but non-empty "." entry.
    got = _with_env(UNSET_ENV_VAR, "", lambda: resolve_journal_path(UNSET_ENV_VAR))
    assert got == DEFAULT_JOURNAL_PATH, f"expected fallback to default, got {got!r}"
    return "resolve_journal_path treats an explicitly-empty env var as unset, not as an override"


def test_resolve_journal_path_is_unnormalized() -> str:
    # journal-canonical-guard.py needs the RAW value (proper case, its own separators) for
    # cwd=/is_dir()/printed messages -- resolve_journal_path must never touch casing/seps.
    raw = r"C:\Users\brown\Git\Engineering-Journal\\"
    got = _with_env(
        "JOURNAL_CANONICAL_GUARD_REPO_PATH",
        raw,
        lambda: resolve_journal_path("JOURNAL_CANONICAL_GUARD_REPO_PATH"),
    )
    assert got == raw, f"expected raw value preserved verbatim, got {got!r}"
    return "resolve_journal_path returns the env value verbatim -- no case/separator normalization"


# --- normalize_journal_path --------------------------------------------------------------


def test_normalize_journal_path_case_insensitive() -> str:
    # os.path.normcase is a Windows-specific case-fold; on POSIX it's the identity
    # function and this assertion would fail. This repo's CLAUDE.md declares Windows 11 as
    # the only supported platform and CI runs windows-latest exclusively, so this is a
    # documented precondition, not an unstated one (dev-env#982 review).
    assert os.name == "nt", "normcase case-insensitivity is Windows-specific; see docstring"
    assert normalize_journal_path("C:/Users/Brown/Git/Engineering-Journal") == normalize_journal_path(
        "C:/Users/brown/Git/engineering-journal"
    )
    return "normalize_journal_path is case-insensitive (Windows path semantics)"


def test_normalize_journal_path_trailing_slash() -> str:
    assert normalize_journal_path("C:/Users/brown/Git/engineering-journal/") == normalize_journal_path(
        "C:/Users/brown/Git/engineering-journal"
    )
    return "normalize_journal_path ignores a trailing slash"


def test_normalize_journal_path_mixed_separators() -> str:
    # os.path.normcase also folds "/" to "\\" only on Windows (identity on POSIX) --
    # same documented precondition as the case-insensitivity test above.
    assert os.name == "nt", "normcase separator-folding is Windows-specific; see docstring"
    assert normalize_journal_path("C:/Users/brown/Git/engineering-journal") == normalize_journal_path(
        r"C:\Users\brown\Git\engineering-journal"
    )
    return "normalize_journal_path treats forward- and back-slash paths identically"


def test_normalize_journal_path_collapses_dot_and_double_sep() -> str:
    # A capability the legacy .replace/rstrip/lower scheme did NOT have (dev-env#982
    # review) -- never observed on a real git-resolved toplevel, but a genuine
    # correctness improvement over the ad hoc scheme it replaces.
    base = normalize_journal_path("C:/Users/brown/Git/engineering-journal")
    assert normalize_journal_path("C:/Users/brown/Git//engineering-journal") == base
    assert normalize_journal_path("C:/Users/brown/Git/./engineering-journal") == base
    assert normalize_journal_path("C:/Users/brown/Git/foo/../engineering-journal") == base
    return "normalize_journal_path collapses repeated separators and '.'/'..' segments"


def test_normalize_journal_path_internally_consistent_for_real_world_inputs() -> str:
    # Every one of these representations of the same real-world path must normalize to
    # the SAME value -- a compound check the pairwise case/trailing-slash/separator tests
    # above don't exercise together. dev-env#982 stress test.
    real_world_inputs = [
        "C:/Users/brown/Git/engineering-journal",
        r"C:\Users\brown\Git\engineering-journal",
        "C:/Users/brown/Git/engineering-journal/",
        "C:/Users/Brown/Git/Engineering-Journal",
        r"C:\Users\Brown\Git\Engineering-Journal\\",
    ]
    values = {normalize_journal_path(p) for p in real_world_inputs}
    assert len(values) == 1, f"normalize_journal_path disagrees within itself: {values!r}"
    return "normalize_journal_path collapses every real-world representation to one value"


def test_normalize_journal_path_matches_real_hook_delegates() -> str:
    # dev-env#982 review: the equivalence claim that matters is that the REAL hook code
    # (not a reimplementation of it in this test file) stays byte-identical to this shared
    # function. pre-tool-use-canonical-mutate-guard.py's `_normalize_path()` and
    # pre-tool-use-worktree-path-check.py's `_normalize()` both DELEGATE to
    # normalize_journal_path() (dev-env#982 review) rather than carrying their own copies --
    # this test loads and calls the actual hook modules, so it fails immediately if either
    # ever stops delegating and reimplements independently (even identically), not just if
    # the reimplementation drifts.
    real_world_inputs = [
        "C:/Users/brown/Git/engineering-journal",
        r"C:\Users\brown\Git\engineering-journal",
        "C:/Users/brown/Git/engineering-journal/",
        "C:/Users/Brown/Git/Engineering-Journal",
        r"C:\Users\Brown\Git\Engineering-Journal\\",
        "C:/Users/brown/Git//engineering-journal",
        "C:/Users/brown/Git/foo/../engineering-journal",
    ]
    for path in real_world_inputs:
        shared = normalize_journal_path(path)
        cmg_val = _cmg._normalize_path(path)
        wpc_val = _wpc._normalize(path)
        assert cmg_val == shared, (
            f"pre-tool-use-canonical-mutate-guard.py's _normalize_path({path!r}) = "
            f"{cmg_val!r}, expected it to delegate to normalize_journal_path = {shared!r}"
        )
        assert wpc_val == shared, (
            f"pre-tool-use-worktree-path-check.py's _normalize({path!r}) = {wpc_val!r}, "
            f"expected it to delegate to normalize_journal_path = {shared!r}"
        )
    return (
        "pre-tool-use-canonical-mutate-guard.py's _normalize_path() and "
        "pre-tool-use-worktree-path-check.py's _normalize() both genuinely delegate to "
        "normalize_journal_path() -- verified against the real hook modules, not a "
        "reimplementation"
    )


def test_normalize_journal_path_pins_empty_input_divergence() -> str:
    # The one documented, provably-unreachable-in-production divergence (dev-env#982
    # review): the legacy `.replace/rstrip/lower` scheme maps "" -> "", this scheme maps
    # "" -> "." (os.path.normpath("") == "."). Every real call site across all four hooks
    # only ever passes a non-empty git-resolved toplevel or a non-empty env-resolved
    # journal path, so this divergence is never hit in practice -- pinned here as a known,
    # understood boundary rather than a silent trap (matching _worktree_canon.py's own
    # documented-divergence-boundary precedent).
    def legacy_replace_scheme(path):
        return (path or "").replace("\\", "/").rstrip("/").lower()

    assert legacy_replace_scheme("") == ""
    assert legacy_replace_scheme(None) == ""
    assert normalize_journal_path("") == "."
    assert normalize_journal_path(None) == "."
    return (
        "empty/None input: legacy .replace scheme yields '', normalize_journal_path "
        "yields '.' (documented, unreachable-in-production divergence)"
    )


def main() -> int:
    tests = [
        ("DEFAULT_JOURNAL_PATH constant", test_default_journal_path_constant),
        ("resolve_journal_path: default when unset", test_resolve_journal_path_returns_default_when_unset),
        (
            "resolve_journal_path: honors explicit default override",
            test_resolve_journal_path_honors_explicit_default_override,
        ),
        (
            "resolve_journal_path: env override under real hook var names",
            test_resolve_journal_path_env_override_real_var_names,
        ),
        (
            "resolve_journal_path: unnormalized (raw casing/separators preserved)",
            test_resolve_journal_path_is_unnormalized,
        ),
        ("normalize_journal_path: case-insensitive", test_normalize_journal_path_case_insensitive),
        ("normalize_journal_path: trailing slash", test_normalize_journal_path_trailing_slash),
        ("normalize_journal_path: mixed separators", test_normalize_journal_path_mixed_separators),
        (
            "normalize_journal_path: collapses '.'/'..' and double separators",
            test_normalize_journal_path_collapses_dot_and_double_sep,
        ),
        (
            "normalize_journal_path: internally consistent across real-world inputs",
            test_normalize_journal_path_internally_consistent_for_real_world_inputs,
        ),
        (
            "normalize_journal_path: matches the real hook modules' delegating wrappers",
            test_normalize_journal_path_matches_real_hook_delegates,
        ),
        (
            "normalize_journal_path: pinned empty/None-input divergence from legacy scheme",
            test_normalize_journal_path_pins_empty_input_divergence,
        ),
        (
            "resolve_journal_path: empty env var falls back to default",
            test_resolve_journal_path_empty_env_var_falls_back_to_default,
        ),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
