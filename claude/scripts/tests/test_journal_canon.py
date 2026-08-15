#!/usr/bin/env python3
"""Unit tests for _journal_canon.py (dev-env#982 / ADR-133).

Pins: default resolution, env-var override (using two of the four real hook env-var
names), the normalization scheme's case/separator/trailing-slash/dot-segment behavior,
its equivalence with the two legacy ad hoc schemes for every real-world (git-resolved)
input shape, and the one documented, provably-unreachable-in-production divergence on
empty input.

Usage:
    py -3 claude/scripts/tests/test_journal_canon.py

Exit 0 = all pass.
"""
import os
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _journal_canon  # noqa: E402

DEFAULT_JOURNAL_PATH = _journal_canon.DEFAULT_JOURNAL_PATH
resolve_journal_path = _journal_canon.resolve_journal_path
normalize_journal_path = _journal_canon.normalize_journal_path

UNSET_ENV_VAR = "_JOURNAL_CANON_TEST_UNSET_VAR_XYZ"


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


def test_normalize_journal_path_equivalence_with_legacy_schemes_for_real_world_inputs() -> str:
    # Reconstruct BOTH legacy schemes exactly as they appeared in the hooks that had one,
    # and pin agreement for every real-world (git-resolved-toplevel-shaped) input --
    # forward-slash, backslash, trailing-slash, mixed-case. dev-env#982 stress test.
    def legacy_replace_scheme(path: str) -> str:
        return (path or "").replace("\\", "/").rstrip("/").lower()

    def legacy_worktree_path_check_normalize(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    real_world_inputs = [
        "C:/Users/brown/Git/engineering-journal",
        r"C:\Users\brown\Git\engineering-journal",
        "C:/Users/brown/Git/engineering-journal/",
        "C:/Users/Brown/Git/Engineering-Journal",
        r"C:\Users\Brown\Git\Engineering-Journal\\",
    ]

    # Every input above must be treated as equivalent to every other input under EACH
    # scheme on its own -- i.e. no input here is distinguished from another by one scheme
    # but not the other.
    new_values = {normalize_journal_path(p) for p in real_world_inputs}
    old_replace_values = {legacy_replace_scheme(p) for p in real_world_inputs}
    assert len(new_values) == 1, f"new scheme disagrees within itself: {new_values!r}"
    assert len(old_replace_values) == 1, f"legacy .replace scheme disagrees within itself: {old_replace_values!r}"

    # The new shared scheme is byte-identical to worktree-path-check.py's own pre-existing
    # local _normalize() (same algorithm) for every one of these inputs.
    for path in real_world_inputs:
        new = normalize_journal_path(path)
        old_wpc = legacy_worktree_path_check_normalize(path)
        assert new == old_wpc, (
            f"new scheme must be byte-identical to worktree-path-check's own legacy "
            f"_normalize() (same algorithm) for {path!r}: new={new!r} old_wpc={old_wpc!r}"
        )
    return (
        "normalize_journal_path agrees with both legacy schemes' own internal "
        "equivalence classes for every real-world git-resolved-toplevel-shaped input; "
        "byte-identical to pre-tool-use-worktree-path-check.py's own legacy _normalize()"
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
            "normalize_journal_path: equivalence with both legacy schemes (real-world inputs)",
            test_normalize_journal_path_equivalence_with_legacy_schemes_for_real_world_inputs,
        ),
        (
            "normalize_journal_path: pinned empty/None-input divergence from legacy scheme",
            test_normalize_journal_path_pins_empty_input_divergence,
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
