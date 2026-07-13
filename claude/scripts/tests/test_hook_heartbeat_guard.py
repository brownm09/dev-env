#!/usr/bin/env python3
"""Structural gate: every wired hook calls `_hookutil.record_heartbeat(<own-name>)`
as the first statement of its own `main()` (ADR-106; dev-env#745, PR8 of #717).

ADR-106 introduced the heartbeat ledger via a one-off, uncommitted AST
transformation script that swept the call-site into all 41 then-wired hooks in
one PR. A `/review` finding on that PR pointed out the obvious risk: nothing
enforces the invariant going forward, so a 42nd hook added by hand (or a future
edit that reorders an existing hook's `main()`) could silently omit the
heartbeat call with no signal — exactly the "wrong result with no signal" shape
this whole initiative exists to close, reintroduced in its own tooling. This
gate closes that gap the same way `test_hook_safe_exit_guard.py` /
`test_hook_output_contract.py` close the analogous gaps for the safe-exit guard
and the output-contract channel: a structural AST check over every currently
wired script, run on every PR via the CI test suite.

Unlike those two gates, this one ships with an EMPTY allowlist from day one —
PR8 made every wired hook compliant in the same change that introduced the
gate, so there is no pre-existing debt to allowlist. A newly wired hook must be
compliant from its first commit; there is no "shrinking allowlist" migration
period for this particular invariant, since there is nothing to migrate.

Detection scope / documented limitations:
  - Only the exact shape `_hookutil.record_heartbeat("<literal>")` is
    recognized as compliant — a call via `import _hookutil as x; x.record_heartbeat(...)`
    aliasing, a `from _hookutil import record_heartbeat` unqualified call, or a
    dynamically-computed name argument are all treated as non-compliant. No
    wired hook uses any of these shapes today (verified by the passing
    repo-wide gate below); if one legitimately needs to, extend
    `_is_record_heartbeat_call` rather than allowlisting around it.
  - The literal argument must equal the script's own basename minus `.py`
    exactly (case-sensitive) — this is the same string every hook already
    passes by construction, so this is a tautology check on today's hooks, not
    a new constraint.
  - A docstring as `main()`'s first statement is skipped (the heartbeat call
    only needs to be the first *executable* statement), mirroring
    `test_hook_safe_exit_guard.py`'s treatment of the safe-exit guard search.

Usage:
    py -3 claude/scripts/tests/test_hook_heartbeat_guard.py

Exit 0 = all pass.
"""

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import _hook_wiring as wiring  # noqa: E402

# ---------------------------------------------------------------------------
# AST detection (pure -- operates on source text, testable with synthetic fixtures)
# ---------------------------------------------------------------------------


def _find_main(tree: ast.Module):
    """Return the top-level `def main(...):` FunctionDef node, or None."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


def _first_real_statement(main_func: ast.FunctionDef):
    """The first non-docstring statement of main()'s body, or None if main()
    is empty or contains only a docstring."""
    body = main_func.body
    if not body:
        return None
    first = body[0]
    is_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    if is_docstring:
        return body[1] if len(body) > 1 else None
    return first


def _is_record_heartbeat_call(stmt, expected_name: str) -> bool:
    """True if *stmt* is exactly `_hookutil.record_heartbeat("<expected_name>")`
    (extra keyword args, e.g. a test-only heartbeat_dir override, are fine --
    only the module, attribute, and first positional literal are checked)."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "record_heartbeat"):
        return False
    if not (isinstance(func.value, ast.Name) and func.value.id == "_hookutil"):
        return False
    if not call.args:
        return False
    arg0 = call.args[0]
    return isinstance(arg0, ast.Constant) and arg0.value == expected_name


def hook_name_from_script(script: str) -> str:
    return script[:-3] if script.endswith(".py") else script


def classify_heartbeat(source: str, expected_name: str, filename: str = "<string>") -> tuple[bool, str]:
    """Return (compliant, reason). reason is "" when compliant, else a short
    human-readable explanation of what's wrong."""
    tree = ast.parse(source, filename=filename)
    main_func = _find_main(tree)
    if main_func is None:
        return (False, "no top-level main() function")
    stmt = _first_real_statement(main_func)
    if stmt is None:
        return (False, "main() has no statements (besides a docstring)")
    if not _is_record_heartbeat_call(stmt, expected_name):
        return (
            False,
            f"main()'s first statement is not _hookutil.record_heartbeat({expected_name!r})",
        )
    return (True, "")


# ---------------------------------------------------------------------------
# Real-repo scan
# ---------------------------------------------------------------------------


def _scan_real_repo() -> dict[str, str]:
    """Return {script: reason} for every wired script that is NOT compliant.
    An empty dict means every wired hook passes."""
    settings = wiring.load_settings()
    scripts = wiring.wired_scripts(settings)
    problems: dict[str, str] = {}
    for script in scripts:
        path = wiring.SCRIPTS_DIR / script
        source = path.read_text(encoding="utf-8")
        expected_name = hook_name_from_script(script)
        compliant, reason = classify_heartbeat(source, expected_name, filename=script)
        if not compliant:
            problems[script] = reason
    return problems


# ---------------------------------------------------------------------------
# Detector self-tests (synthetic fixtures -- no disk I/O)
# ---------------------------------------------------------------------------


def test_compliant_first_statement() -> str:
    src = (
        "import _hookutil\n"
        'def main():\n    _hookutil.record_heartbeat("foo-hook")\n    other()\n'
    )
    assert classify_heartbeat(src, "foo-hook") == (True, ""), classify_heartbeat(src, "foo-hook")
    return "record_heartbeat as the literal first statement -> compliant"


def test_compliant_after_docstring() -> str:
    src = (
        "import _hookutil\n"
        'def main():\n    """A docstring."""\n    _hookutil.record_heartbeat("foo-hook")\n'
    )
    assert classify_heartbeat(src, "foo-hook") == (True, ""), classify_heartbeat(src, "foo-hook")
    return "record_heartbeat as the first statement AFTER a docstring -> compliant"


def test_missing_no_main() -> str:
    src = "import _hookutil\ndef not_main():\n    pass\n"
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant and "no top-level main()" in reason, (compliant, reason)
    return "no main() function -> non-compliant, reason names the gap"


def test_missing_empty_main() -> str:
    # `pass` IS a real (non-docstring) statement -- just not a heartbeat call.
    src = "import _hookutil\ndef main():\n    pass\n"
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant and "not _hookutil.record_heartbeat" in reason, (compliant, reason)
    return "main() with only `pass` (not a heartbeat call) -> non-compliant"


def test_missing_docstring_only_main() -> str:
    src = 'import _hookutil\ndef main():\n    """Just a docstring, no body."""\n'
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant and "no statements" in reason, (compliant, reason)
    return "main() with only a docstring, no real statements -> non-compliant"


def test_missing_call_not_first() -> str:
    src = (
        "import _hookutil\n"
        'def main():\n    setup()\n    _hookutil.record_heartbeat("foo-hook")\n'
    )
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant and "not _hookutil.record_heartbeat" in reason, (compliant, reason)
    return "record_heartbeat present but NOT the first statement -> non-compliant"


def test_missing_wrong_name() -> str:
    src = 'import _hookutil\ndef main():\n    _hookutil.record_heartbeat("some-other-hook")\n'
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant, (compliant, reason)
    return "record_heartbeat called with a DIFFERENT hook's name -> non-compliant"


def test_missing_dynamic_name() -> str:
    src = "import _hookutil\nNAME = 'foo-hook'\ndef main():\n    _hookutil.record_heartbeat(NAME)\n"
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant, (compliant, reason)
    return "record_heartbeat called with a non-literal (variable) argument -> non-compliant (scope limitation, documented)"


def test_missing_unqualified_import() -> str:
    src = (
        "from _hookutil import record_heartbeat\n"
        'def main():\n    record_heartbeat("foo-hook")\n'
    )
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant, (compliant, reason)
    return "unqualified `from _hookutil import record_heartbeat` call shape -> non-compliant (scope limitation, documented)"


def test_missing_other_call_first() -> str:
    src = 'import _hookutil\ndef main():\n    print("hi")\n'
    compliant, reason = classify_heartbeat(src, "foo-hook")
    assert not compliant, (compliant, reason)
    return "some other call entirely as the first statement -> non-compliant"


# ---------------------------------------------------------------------------
# Repo-wide gate
# ---------------------------------------------------------------------------


def test_repo_wide_heartbeat_guard_gate() -> str:
    """The regression gate: every currently wired hook must call
    _hookutil.record_heartbeat(<own name>) as the first statement of main().
    No allowlist -- PR8 made every wired hook compliant in the same change
    that introduced this gate (see module docstring)."""
    problems = _scan_real_repo()
    if problems:
        lines = ["Wired hooks not calling _hookutil.record_heartbeat(<own name>) as main()'s first statement:"]
        for script, reason in sorted(problems.items()):
            lines.append(f"  {script}: {reason}")
        lines.append(
            "  Fix: add `_hookutil.record_heartbeat(\"<script-basename-minus-.py>\")` "
            "as the literal first statement of main() (see ADR-106)."
        )
        raise AssertionError("\n".join(lines))
    settings = wiring.load_settings()
    total = len(wiring.wired_scripts(settings))
    return f"{total} wired hooks all call _hookutil.record_heartbeat(<own name>) as main()'s first statement"


def main() -> int:
    tests = [
        ("compliant: first statement", test_compliant_first_statement),
        ("compliant: first statement after docstring", test_compliant_after_docstring),
        ("non-compliant: no main()", test_missing_no_main),
        ("non-compliant: empty main() (only pass)", test_missing_empty_main),
        ("non-compliant: docstring-only main()", test_missing_docstring_only_main),
        ("non-compliant: call present but not first", test_missing_call_not_first),
        ("non-compliant: wrong hook name", test_missing_wrong_name),
        ("non-compliant: dynamic (non-literal) name", test_missing_dynamic_name),
        ("non-compliant: unqualified import shape", test_missing_unqualified_import),
        ("non-compliant: unrelated first call", test_missing_other_call_first),
        ("repo-wide heartbeat guard gate", test_repo_wide_heartbeat_guard_gate),
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
