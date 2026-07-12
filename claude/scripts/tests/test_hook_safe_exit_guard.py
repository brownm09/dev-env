#!/usr/bin/env python3
"""Structural gate: every wired hook has a top-level safe-exit `__main__` guard,
and that guard fails in the direction the hook declares (dev-env#720, ADR-103;
authoring rules 2 & 5, docs/REFERENCE.md).

Claude Code reads a hook's *exit code*, not its intent: for a PreToolUse hook,
exit 2 blocks the tool and anything else (0, or a traceback's default 1) lets it
through. So a crash in a hook's own code (import-time or in main()) that escapes
to a default exit 1 silently does the WRONG thing — an advisory that should fail
*open* (exit 0) instead blocks nothing but logs a traceback the user never sees,
and a fail-*closed* gate that should exit 2 instead silently disables the very
check it exists to enforce (the class dev-env#717/#718 closed). Authoring rule 2
prescribes the guard; rule 5 prescribes its direction.

This gate checks, for every script wired in claude/settings.json:
  1. it has a module-level `if __name__ == "__main__":` block containing a
     `try: ... except Exception|<bare>: ...` whose handler deterministically
     `sys.exit(N)`s (a literal, or a one-level call to a module-level helper that
     does — e.g. pre-auto-merge-checkpoint-gate's `_fail_closed`); and
  2. that handler's exit code N equals the direction the hook declares via
     `fail_direction()` (0 = fail open / advisory, 2 = fail closed / gate).

The fail direction is a *design* decision per hook, not derivable from the code, so
it is pinned here: `FAIL_CLOSED` is the hard-coded set of the two ADR-083/ADR-096
gates that fail closed (exit 2); `fail_direction()` returns 0 for every other wired
hook (fail open). Pinning it makes a future edit that flips a hook's direction — an
advisory that starts exiting 2, or a fail-closed gate downgraded to 0 — a hard
failure here (the `wrong_direction` bucket below), never allowlist-able. Two
narrower guards keep `FAIL_CLOSED` itself honest: `test_fail_direction_map_covers_exactly_wired_set`
fails on a `FAIL_CLOSED` entry naming a no-longer-wired script (stale), and any
guarded hook whose crash-exit contradicts its direction fails regardless of the
allowlist. A newly wired hook with no `FAIL_CLOSED` entry is NOT rejected — it
defaults to the fail-open expectation, which the guarded / wrong-direction checks
then verify.

Two-sided allowlist (the `test_no_crude_command_substring_checks.py` mechanism):
_UNGUARDED_ALLOWLIST holds the scripts that do NOT yet have a compliant guard
(bare `main()` / `sys.exit(main())` / no `__main__` block). Each is a real offense
the PR7 safe-exit sweep will fix by adding the guard; when it does, the entry
becomes stale and THIS test fails until it is removed. A newly wired unguarded
hook not in the allowlist also fails. A guarded hook must NOT be allowlisted.

Detection scope / documented limitations (per the plan's gotcha #6 — scope to
explicit shapes, document the rest):
  - The exit code is resolved from a literal `sys.exit(N)` / `exit(N)` in the
    handler, or one level into a module-level helper the handler calls. A handler
    that exits through two+ levels of indirection, `os._exit`, or a computed code
    is treated as "no deterministic exit" -> unguarded. None exist today.
  - Only the `except Exception` / bare-`except` handler is inspected; an
    `except SystemExit: raise` (the pass-through that preserves main()'s own
    deliberate exit 0/2 verdicts) is correctly ignored, not mistaken for the
    fail-direction handler.
  - The fail-CLOSED gates additionally need a module-level dependency-load guard
    (rule 5) that this structural test does NOT verify; that surface is pinned by
    each gate's own e2e suite (test_pre_auto_merge_checkpoint_gate.py item 52,
    test_pre_tool_use_journal_compose_force_guard.py item 55).

Usage:
    py -3 claude/scripts/tests/test_hook_safe_exit_guard.py

Exit 0 = all pass.
"""

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import _hook_wiring as wiring  # noqa: E402

# ---------------------------------------------------------------------------
# Declared fail direction per wired hook: 0 = fail open (advisory), 2 = fail
# closed (gate). Only the two gates ADR-083 / ADR-096 name as fail-closed exit 2
# on their own crash; every other wired hook -- including the *blocking* gates
# that deliberately exit 2 to block but fail OPEN on an unexpected crash
# (canonical-mutate-guard, findings-gate, numbering-check, worktree-path-check,
# the Stop gates) -- fails open. See docs/REFERENCE.md authoring rule 5.
# ---------------------------------------------------------------------------
FAIL_CLOSED = {
    "pre-auto-merge-checkpoint-gate.py",
    "pre-tool-use-journal-compose-force-guard.py",
}


def fail_direction(script: str) -> int:
    return 2 if script in FAIL_CLOSED else 0


# Scripts that do NOT yet carry a compliant safe-exit `__main__` guard (bare
# `main()` / `sys.exit(main())` / no `__main__` block). Two-sided: a stale entry
# (script since guarded) fails the gate. PR7 (dev-env#743) guarded the last 14
# offenders (awake-blocker, idle-refresher, multi-worktree-alert,
# pre-commit-branch-check, pre-merge-branch-check, pre-merge-findings-gate,
# pre-merge-message-check, pre-merge-numbering-check, pre-pr-create-check,
# pre-tool-use-canonical-mutate-guard, pre-tool-use-worktree-path-check,
# session-mode-prompt, token-tracker, turn-count-hook), so this allowlist is now
# empty; any new entry the gate reports is a genuine regression to guard, not
# something to re-add here.
_UNGUARDED_ALLOWLIST: set[str] = set()


# ---------------------------------------------------------------------------
# AST detection (pure -- operates on source text, testable with synthetic fixtures)
# ---------------------------------------------------------------------------

def _find_main_block(tree: ast.Module):
    """Return the module-level `if __name__ == "__main__":` If node, or None."""
    for node in tree.body:
        if isinstance(node, ast.If):
            t = node.test
            if (
                isinstance(t, ast.Compare)
                and isinstance(t.left, ast.Name)
                and t.left.id == "__name__"
                and len(t.comparators) == 1
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value == "__main__"
            ):
                return node
    return None


def _exception_handler(try_node: ast.Try) -> ast.ExceptHandler | None:
    """The handler catching `Exception` / `BaseException` / bare `except:` -- the
    fail-direction handler. An `except SystemExit` handler is deliberately skipped
    (it only re-raises main()'s own verdicts)."""
    for h in try_node.handlers:
        if h.type is None:  # bare except:
            return h
        if isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"):
            return h
    return None


def _exit_code_in(stmts, tree: ast.Module, _depth: int = 0) -> int | None:
    """First literal `sys.exit(N)` / `exit(N)` / `raise SystemExit(N)` reachable in
    *stmts*, resolving one level into a module-level helper the code calls. Returns
    N or None. (`SystemExit` is accepted to match the sibling `_literal_exit` in
    test_hook_output_contract.py, so both AST gates agree on what counts as an exit
    -- dev-env#726 review.)"""
    # Direct sys.exit(N) / exit(N) / SystemExit(N) anywhere in these statements
    # (a bare `raise SystemExit(N)`'s call node is reached by the same walk).
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                f = node.func
                is_exit = (isinstance(f, ast.Attribute) and f.attr == "exit") or (
                    isinstance(f, ast.Name) and f.id in ("exit", "SystemExit")
                )
                if (
                    is_exit
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, int)
                ):
                    return node.args[0].value
    # One-level helper resolution: a call to a module-level function def.
    if _depth == 0:
        for stmt in stmts:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    helper = _module_func(tree, node.func.id)
                    if helper is not None:
                        code = _exit_code_in(helper.body, tree, _depth=1)
                        if code is not None:
                            return code
    return None


def _module_func(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def classify_guard(source: str, filename: str = "<string>") -> tuple[str, int | None]:
    """Return (shape, exit_code). shape is "guarded" (a safe-exit try/except whose
    Exception handler deterministically exits) or "unguarded"; exit_code is the
    resolved handler exit code when guarded, else None.

    Scans EVERY top-level `try` in the `__main__` block, not just the first: a hook
    may open with a setup `try/except` whose handler does not exit (e.g.
    `except Exception: cfg = None`) before the real `try: main() except: sys.exit(...)`
    guard. Returns the first try whose Exception/bare handler yields a deterministic
    exit (dev-env#726 review)."""
    tree = ast.parse(source, filename=filename)
    main_if = _find_main_block(tree)
    if main_if is None:
        return ("unguarded", None)
    for stmt in main_if.body:
        if isinstance(stmt, ast.Try):
            handler = _exception_handler(stmt)
            if handler is not None:
                code = _exit_code_in(handler.body, tree)
                if code is not None:
                    return ("guarded", code)
    return ("unguarded", None)


# ---------------------------------------------------------------------------
# Real-repo scan
# ---------------------------------------------------------------------------

def _scan_real_repo():
    """Return (guarded, unguarded, wrong_direction):
      guarded[script] = exit_code (compliant guard, correct direction)
      unguarded = set of scripts with no compliant guard
      wrong_direction[script] = (found_code, expected_code)  -- HARD violations
    """
    settings = wiring.load_settings()
    scripts = wiring.wired_scripts(settings)
    guarded, unguarded, wrong = {}, set(), {}
    for script in scripts:
        path = wiring.SCRIPTS_DIR / script
        source = path.read_text(encoding="utf-8")
        shape, code = classify_guard(source, filename=script)
        if shape == "unguarded":
            unguarded.add(script)
            continue
        expected = fail_direction(script)
        if code != expected:
            wrong[script] = (code, expected)
        else:
            guarded[script] = code
    return guarded, unguarded, wrong


# ---------------------------------------------------------------------------
# Detector self-tests (synthetic fixtures -- no disk I/O)
# ---------------------------------------------------------------------------

def test_guarded_exit0() -> str:
    src = (
        "def main():\n    pass\n"
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n    except Exception:\n        sys.exit(0)\n"
    )
    assert classify_guard(src) == ("guarded", 0), classify_guard(src)
    return "try/except Exception: sys.exit(0) -> guarded, code 0"


def test_guarded_exit2_literal() -> str:
    src = (
        "def main():\n    pass\n"
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n"
        "    except SystemExit:\n        raise\n"
        "    except Exception:\n        sys.stderr.write('x')\n        sys.exit(2)\n"
    )
    assert classify_guard(src) == ("guarded", 2), classify_guard(src)
    return "except SystemExit:raise + except Exception: ...sys.exit(2) -> guarded, code 2 (SystemExit handler ignored)"


def test_guarded_exit2_via_helper() -> str:
    # pre-auto-merge-checkpoint-gate's shape: handler calls a module-level helper
    # that owns the sys.exit(2).
    src = (
        "def _fail_closed(msg):\n    sys.stderr.write(msg)\n    sys.exit(2)\n"
        "def main():\n    pass\n"
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n"
        "    except SystemExit:\n        raise\n"
        "    except Exception as exc:\n        _fail_closed(f'boom {exc}')\n"
    )
    assert classify_guard(src) == ("guarded", 2), classify_guard(src)
    return "handler calls a module-level helper that sys.exit(2)s -> resolved to code 2 (one-level)"


def test_unguarded_bare_main() -> str:
    src = 'def main():\n    pass\nif __name__ == "__main__":\n    main()\n'
    assert classify_guard(src) == ("unguarded", None), classify_guard(src)
    return "bare `main()` in __main__ -> unguarded"


def test_unguarded_sys_exit_main() -> str:
    src = 'def main():\n    return 0\nif __name__ == "__main__":\n    sys.exit(main())\n'
    assert classify_guard(src) == ("unguarded", None), classify_guard(src)
    return "`sys.exit(main())` (crash -> default exit 1, not fail-open 0) -> unguarded"


def test_unguarded_no_main_block() -> str:
    src = "def main():\n    pass\nmain()\n"
    assert classify_guard(src) == ("unguarded", None), classify_guard(src)
    return "no `if __name__ == \"__main__\"` block (session-mode-prompt shape) -> unguarded"


def test_handler_without_exit_is_unguarded() -> str:
    # A try/except that swallows the error but never exits: control falls off the
    # end -> exit 0 by luck, but not a *declared* guard. Treat as unguarded so it
    # is not mistaken for a compliant fail-open guard.
    src = (
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n    except Exception:\n        pass\n"
    )
    assert classify_guard(src) == ("unguarded", None), classify_guard(src)
    return "except handler with no sys.exit -> unguarded (not a declared guard)"


def test_wrong_direction_is_detected_as_guarded_with_that_code() -> str:
    # classify_guard reports the code faithfully; the repo gate compares it to
    # the declared direction. An advisory that wrongly exits 2 is "guarded, 2".
    src = (
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n    except Exception:\n        sys.exit(2)\n"
    )
    assert classify_guard(src) == ("guarded", 2), classify_guard(src)
    return "handler exiting 2 -> ('guarded', 2) reported faithfully for the direction comparison"


def test_guarded_scans_past_leading_setup_try() -> str:
    # A leading setup try/except whose handler does NOT exit, then the real guard.
    # The first-try-only detector would misclassify this as unguarded (dev-env#726).
    src = (
        'if __name__ == "__main__":\n'
        "    try:\n        cfg = load()\n    except Exception:\n        cfg = None\n"
        "    try:\n        main()\n    except Exception:\n        sys.exit(0)\n"
    )
    assert classify_guard(src) == ("guarded", 0), classify_guard(src)
    return "leading non-exiting setup try + real guard -> guarded (scans all tries, not just the first)"


def test_guarded_raise_systemexit() -> str:
    src = (
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n    except Exception:\n        raise SystemExit(2)\n"
    )
    assert classify_guard(src) == ("guarded", 2), classify_guard(src)
    return "handler `raise SystemExit(2)` -> guarded, code 2 (matches sibling _literal_exit)"


def test_guarded_except_baseexception() -> str:
    src = (
        'if __name__ == "__main__":\n'
        "    try:\n        main()\n    except BaseException:\n        sys.exit(0)\n"
    )
    assert classify_guard(src) == ("guarded", 0), classify_guard(src)
    return "`except BaseException:` recognized as the fail-direction handler"


def test_non_main_dunder_block_is_not_a_guard() -> str:
    # `if __name__ == "__not_main__":` must not be mistaken for the entrypoint guard.
    src = (
        'if __name__ == "__not_main__":\n'
        "    try:\n        main()\n    except Exception:\n        sys.exit(0)\n"
    )
    assert classify_guard(src) == ("unguarded", None), classify_guard(src)
    return 'a non-"__main__" `if __name__ == ...` block is not treated as the guard'


# ---------------------------------------------------------------------------
# Repo-wide gates
# ---------------------------------------------------------------------------

def test_fail_direction_map_covers_exactly_wired_set() -> str:
    """FAIL_CLOSED must reference only wired scripts (no stale entries). Every
    wired script gets a direction via fail_direction(); this pins that the
    fail-closed set itself hasn't gone stale."""
    settings = wiring.load_settings()
    wired = set(wiring.wired_scripts(settings))
    stale_closed = FAIL_CLOSED - wired
    assert not stale_closed, f"FAIL_CLOSED names non-wired scripts (remove): {sorted(stale_closed)}"
    return f"FAIL_CLOSED = {sorted(FAIL_CLOSED)}; all wired; {len(wired)} scripts each have a direction"


def test_all_wired_commands_parse_to_a_script() -> str:
    """Fail if any wired command doesn't resolve to a `<name>.py`. This gate derives
    its scan set from wired_scripts(), which silently drops a None-script entry, so a
    command that stops parsing (e.g. a trailing arg after the .py) would vanish from
    coverage with a false all-clear -- assert it here rather than trusting the wiring
    lint's separate run to catch it (dev-env#726 review)."""
    settings = wiring.load_settings()
    unparsed = wiring.unparsed_commands(settings)
    assert not unparsed, "Wired commands not resolving to a .py script:\n  " + "\n  ".join(
        f"{e.event}/{e.matcher}: {e.command!r}" for e in unparsed
    )
    return "all wired commands resolve to a .py script (none silently dropped from this scan)"


def test_repo_wide_safe_exit_guard_gate() -> str:
    """The regression gate. Fails on (a) an unguarded wired hook not in
    _UNGUARDED_ALLOWLIST, (b) a stale allowlist entry (script now guarded), or
    (c) a guarded hook whose exit code contradicts FAIL_DIRECTION (never
    allowlist-able -- a real contract violation)."""
    guarded, unguarded, wrong = _scan_real_repo()

    unexpected_unguarded = unguarded - _UNGUARDED_ALLOWLIST
    stale_allowlist = _UNGUARDED_ALLOWLIST - unguarded

    if unexpected_unguarded or stale_allowlist or wrong:
        lines = []
        if unexpected_unguarded:
            lines.append("Wired hooks with no safe-exit __main__ guard (add one, or allowlist):")
            for s in sorted(unexpected_unguarded):
                lines.append(f"  {s}  (should fail {fail_direction(s)} on crash)")
            lines.append(
                "  Fix: add `try: main() except Exception: sys.exit(<dir>)` "
                "(fail-closed gates: `except SystemExit: raise` + exit 2), per authoring rule 5."
            )
        if stale_allowlist:
            lines.append("Stale _UNGUARDED_ALLOWLIST entries (now guarded -- remove them):")
            for s in sorted(stale_allowlist):
                lines.append(f"  {s!r}")
        if wrong:
            lines.append("Guarded hooks whose crash-exit contradicts FAIL_DIRECTION (fix the guard):")
            for s, (found, expected) in sorted(wrong.items()):
                direction = "fail-closed" if expected == 2 else "fail-open"
                lines.append(f"  {s}: guard exits {found}, expected {expected} ({direction})")
        raise AssertionError("\n".join(lines))

    return (
        f"{len(guarded)} guarded (correct direction), "
        f"{len(unguarded)} unguarded (all allowlisted), 0 wrong-direction"
    )


def main() -> int:
    tests = [
        ("guarded exit 0", test_guarded_exit0),
        ("guarded exit 2 (literal, SystemExit handler ignored)", test_guarded_exit2_literal),
        ("guarded exit 2 (via one-level helper)", test_guarded_exit2_via_helper),
        ("unguarded: bare main()", test_unguarded_bare_main),
        ("unguarded: sys.exit(main())", test_unguarded_sys_exit_main),
        ("unguarded: no __main__ block", test_unguarded_no_main_block),
        ("unguarded: handler without sys.exit", test_handler_without_exit_is_unguarded),
        ("wrong-direction reported faithfully", test_wrong_direction_is_detected_as_guarded_with_that_code),
        ("guarded: scans past a leading setup try", test_guarded_scans_past_leading_setup_try),
        ("guarded: raise SystemExit(2)", test_guarded_raise_systemexit),
        ("guarded: except BaseException", test_guarded_except_baseexception),
        ('non-"__main__" dunder block is not a guard', test_non_main_dunder_block_is_not_a_guard),
        ("FAIL_CLOSED map covers only wired scripts", test_fail_direction_map_covers_exactly_wired_set),
        ("all wired commands parse to a script", test_all_wired_commands_parse_to_a_script),
        ("repo-wide safe-exit guard gate", test_repo_wide_safe_exit_guard_gate),
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
