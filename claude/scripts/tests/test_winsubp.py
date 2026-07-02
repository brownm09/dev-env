#!/usr/bin/env python3
"""Unit tests for _winsubp._apply_windows_subprocess_defaults.

`_winsubp.py` monkey-patches `subprocess.Popen.__init__` for two Windows-only
defaults: OR-merging `CREATE_NO_WINDOW` into `creationflags` (dev-env#297, the
console-flash fix) and defaulting `encoding="utf-8", errors="replace"` onto a
text-mode call that doesn't already specify an encoding (dev-env#503, the
`UnicodeDecodeError` fix). Both defaults are applied by one pure helper,
`_apply_windows_subprocess_defaults(kwargs)`, which mutates and returns the
kwargs dict the patched `Popen.__init__` is about to forward to the real one.

Before dev-env#503, `post-tool-use.py`'s `add_to_project()` crashed with
`UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d` reading `gh
project item-add`'s stdout, because `subprocess.run(..., text=True)` with no
`encoding=` decodes as `locale.getpreferredencoding(False)` (cp1252 on
Windows) rather than UTF-8. `canonical_root_via_git()` in the same file and
`confirm_merge_via_gh()` in `_hookio.py` had the identical gap. All three
(and every other subprocess-using script in claude/scripts/, since the
existing `test_pyw_stdio.py` guard already requires every one of them to
import `_winsubp`) are fixed by this one change to the shared patch, rather
than by editing each call site — see ADR-007's 2026-07-02 follow-up.

These tests exercise `_apply_windows_subprocess_defaults` directly, offline
(no subprocess spawn, no mock) — matching this repo's pure-helper test
convention (e.g. `_canonical_root_from_common_dir` behind the untested
`canonical_root_via_git` in test_post_tool_use.py). The monkey-patch
installation itself, and an end-to-end reproduction of the exact dev-env#503
crash byte, are covered separately by `test_pyw_stdio.py` (which spawns real
`pyw -3` processes); this file never spawns a process.

Usage:
    py -3 claude/scripts/tests/test_winsubp.py

Exit 0 = all pass.
"""

import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _winsubp  # noqa: E402

_apply_windows_subprocess_defaults = _winsubp._apply_windows_subprocess_defaults
_CREATE_NO_WINDOW = _winsubp._CREATE_NO_WINDOW


def test_create_no_window_constant() -> str:
    # Pins the documented Win32 flag value so a future edit can't silently
    # drift from https://learn.microsoft.com/windows/win32/procthread/process-creation-flags
    assert _CREATE_NO_WINDOW == 0x08000000, f"got {_CREATE_NO_WINDOW:#x}"
    return "_CREATE_NO_WINDOW matches the documented Win32 flag value"


def test_creationflags_default_when_absent() -> str:
    result = _apply_windows_subprocess_defaults({})
    assert result["creationflags"] == _CREATE_NO_WINDOW, f"got {result!r}"
    return "no creationflags supplied -> defaults to CREATE_NO_WINDOW alone"


def test_creationflags_or_merged_with_existing() -> str:
    result = _apply_windows_subprocess_defaults({"creationflags": 0x1})
    assert result["creationflags"] == (0x1 | _CREATE_NO_WINDOW), f"got {result!r}"
    return "existing creationflags is OR-merged, not overwritten (dev-env#297 behavior)"


def test_encoding_defaulted_when_text_true() -> str:
    result = _apply_windows_subprocess_defaults({"text": True})
    assert result["encoding"] == "utf-8", f"got {result!r}"
    assert result["errors"] == "replace", f"got {result!r}"
    assert result["text"] is True, "caller's text=True must be preserved"
    return "text=True with no encoding -> encoding='utf-8', errors='replace' defaulted"


def test_encoding_defaulted_when_universal_newlines_true() -> str:
    # universal_newlines is the pre-3.7 alias for text= -- Popen still accepts it.
    result = _apply_windows_subprocess_defaults({"universal_newlines": True})
    assert result["encoding"] == "utf-8", f"got {result!r}"
    assert result["errors"] == "replace", f"got {result!r}"
    return "universal_newlines=True (legacy alias) also gets the UTF-8 default"


def test_encoding_defaulted_when_errors_set_alone() -> str:
    # errors= alone (no text=, no universal_newlines=) also puts real Popen
    # into text mode -- confirmed empirically against subprocess.Popen itself.
    # A caller that only sets errors= must still get encoding defaulted, or
    # they'd fall through to CPython's own cp1252 default.
    result = _apply_windows_subprocess_defaults({"errors": "replace"})
    assert result["encoding"] == "utf-8", f"got {result!r}"
    assert result["errors"] == "replace", f"got {result!r}"
    return "errors= alone (no text=) still gets encoding='utf-8' defaulted"


def test_no_encoding_default_without_text_mode() -> str:
    # Binary-mode calls (no text/universal_newlines/encoding at all) must be
    # left untouched -- injecting encoding/errors would break a caller reading
    # raw bytes.
    result = _apply_windows_subprocess_defaults({})
    assert "encoding" not in result, f"got {result!r}"
    assert "errors" not in result, f"got {result!r}"
    return "no text/universal_newlines requested -> no encoding/errors injected"


def test_no_encoding_default_when_text_false() -> str:
    result = _apply_windows_subprocess_defaults({"text": False})
    assert "encoding" not in result, f"got {result!r}"
    assert "errors" not in result, f"got {result!r}"
    return "text=False (explicit binary mode) -> no encoding/errors injected"


def test_explicit_encoding_respected() -> str:
    # A caller that already chose an encoding is never second-guessed --
    # neither encoding NOR errors is touched in that case.
    result = _apply_windows_subprocess_defaults({"text": True, "encoding": "cp1252"})
    assert result["encoding"] == "cp1252", f"got {result!r}"
    assert "errors" not in result, f"got {result!r}"
    return "explicit encoding= is never overridden, and errors= is left alone too"


def test_explicit_errors_only_still_gets_encoding_defaulted() -> str:
    # A caller who set errors= but not encoding= gets encoding defaulted to
    # utf-8, but their own errors= choice is preserved via setdefault.
    result = _apply_windows_subprocess_defaults({"text": True, "errors": "strict"})
    assert result["encoding"] == "utf-8", f"got {result!r}"
    assert result["errors"] == "strict", f"got {result!r}"
    return "explicit errors= alone still gets encoding='utf-8', but errors= is preserved"


def test_combined_creationflags_and_encoding() -> str:
    result = _apply_windows_subprocess_defaults({"text": True, "creationflags": 0x1})
    assert result["creationflags"] == (0x1 | _CREATE_NO_WINDOW), f"got {result!r}"
    assert result["encoding"] == "utf-8", f"got {result!r}"
    return "both defaults apply together in a single call"


def test_mutates_and_returns_same_dict() -> str:
    # _patched_popen_init calls _apply_windows_subprocess_defaults(kwargs)
    # WITHOUT reassigning the result -- it relies on in-place mutation. If
    # this function ever stopped mutating in place (e.g. returned a new dict
    # via {**kwargs, ...}), the patch would silently stop working.
    kwargs = {"text": True}
    result = _apply_windows_subprocess_defaults(kwargs)
    assert result is kwargs, "must mutate and return the same dict object"
    return "mutates the passed-in dict in place (the patched init relies on this)"


def main() -> int:
    tests = [
        ("CREATE_NO_WINDOW constant matches Win32 docs", test_create_no_window_constant),
        ("creationflags defaults when absent", test_creationflags_default_when_absent),
        ("creationflags OR-merged with existing", test_creationflags_or_merged_with_existing),
        ("encoding defaulted for text=True", test_encoding_defaulted_when_text_true),
        ("encoding defaulted for universal_newlines=True", test_encoding_defaulted_when_universal_newlines_true),
        ("encoding defaulted for errors= set alone", test_encoding_defaulted_when_errors_set_alone),
        ("no encoding default without text mode", test_no_encoding_default_without_text_mode),
        ("no encoding default when text=False", test_no_encoding_default_when_text_false),
        ("explicit encoding= respected", test_explicit_encoding_respected),
        ("explicit errors= (with text=True) still gets encoding defaulted", test_explicit_errors_only_still_gets_encoding_defaulted),
        ("creationflags + encoding combine", test_combined_creationflags_and_encoding),
        ("mutates and returns the same dict", test_mutates_and_returns_same_dict),
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
