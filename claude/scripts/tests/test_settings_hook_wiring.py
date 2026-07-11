#!/usr/bin/env python3
"""Settings-wiring lint: every claude/settings.json hook entry points at a real
script and carries an explicit timeout at or above its per-hook budget
(dev-env#720, ADR-103; authoring rules 1 & 4, docs/REFERENCE.md).

A hook entry that references a script not on the branch blocks the matched tool
call silently (authoring rule 1: `settings.json` + script land in the same commit).
A missing / too-tight `timeout` is the flip side of the invisibility this
initiative fixes: Claude Code's default hook timeout kills a slow hook mid-run,
so a hook doing real subprocess work (git/gh) under a tight bound is silently
truncated (gotcha #5). This gate enforces, for EVERY (event, matcher, hook) entry:

  1. the command resolves to a `<name>.py` that exists in claude/scripts/;
  2. an explicit integer `timeout` (seconds) >= the script's budget floor:
       usage-snapshot.py           -> 90  (does ~45s of internal subprocess work)
       a hook importing _winsubp    -> 30  (spawns git/gh subprocesses)
       pure-Python (no _winsubp)    -> 10
     `_winsubp` import is the objective subprocess signal (authoring rule 4: any
     subprocess-using hook must import it). Budget is a FLOOR (>=), not an equality:
     a hook may declare a longer timeout, never a shorter one.

The `pyw -3` invocation invariant (authoring rule 3) is deliberately NOT re-checked
here -- `test_pyw_stdio.py`'s `test_all_settings_hooks_use_pyw_and_resolve_to_repo`
already gates it statically (and exercises the real `pyw` stdio behavior). The
resolution check below overlaps that test's resolution half by design: resolving a
script is the precondition for computing its `_winsubp`-based budget, and the plan
names resolution as a wiring-lint requirement.

The lint iterates every entry generically, so adding a new event/matcher group
(e.g. PR9's PowerShell PreToolUse mirror of the Bash group) is covered with no
change here beyond any new script's budget classification.

Usage:
    py -3 claude/scripts/tests/test_settings_hook_wiring.py

Exit 0 = all pass.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import _hook_wiring as wiring  # noqa: E402

# Per-hook timeout budget (seconds). See the module docstring.
_PURE_PYTHON_BUDGET = 10
_SUBPROCESS_BUDGET = 30
_USAGE_SNAPSHOT_BUDGET = 90
_USAGE_SNAPSHOT = "usage-snapshot.py"

# Line-anchored `import _winsubp` / `from _winsubp import ...` detector (matches the
# form in test_pyw_stdio.py). Anchored to line-start (after leading whitespace) so a
# commented-out `# import _winsubp` is not matched, and it catches the `from _winsubp
# import X` form a raw `"import _winsubp" in text` substring would miss (dev-env#726
# review).
_WINSUBP_IMPORT_RE = re.compile(r"^\s*(?:import\s+_winsubp\b|from\s+_winsubp\b)", re.MULTILINE)


def _imports_winsubp(script: str) -> bool:
    """True if *script* imports _winsubp (the subprocess-hook marker, rule 4)."""
    path = wiring.SCRIPTS_DIR / script
    if not path.exists():
        return False
    return bool(_WINSUBP_IMPORT_RE.search(path.read_text(encoding="utf-8")))


def min_timeout(script: str) -> int:
    """The timeout floor (seconds) a wired entry for *script* must declare."""
    if script == _USAGE_SNAPSHOT:
        return _USAGE_SNAPSHOT_BUDGET
    if _imports_winsubp(script):
        return _SUBPROCESS_BUDGET
    return _PURE_PYTHON_BUDGET


# ---------------------------------------------------------------------------
# self-tests
# ---------------------------------------------------------------------------

def test_script_from_command_extracts_basename() -> str:
    cmd = "pyw -3 C:/Users/brown/.claude/scripts/pre-commit-branch-check.py"
    assert wiring.script_from_command(cmd) == "pre-commit-branch-check.py"
    return "pyw -3 <path>/foo.py -> foo.py"


def test_script_from_command_none_when_not_py() -> str:
    assert wiring.script_from_command("echo hello") is None
    return "a command not ending in .py -> None"


def test_min_timeout_usage_snapshot_is_90() -> str:
    assert min_timeout(_USAGE_SNAPSHOT) == 90
    return "usage-snapshot.py budget floor is 90s"


def test_min_timeout_subprocess_is_30() -> str:
    # post-tool-use.py imports _winsubp (git/gh subprocess hook).
    assert min_timeout("post-tool-use.py") == 30, min_timeout("post-tool-use.py")
    return "a _winsubp-importing hook (post-tool-use.py) budget floor is 30s"


def test_min_timeout_pure_python_is_10() -> str:
    # turn-count-hook.py does no subprocess work (no _winsubp import).
    assert min_timeout("turn-count-hook.py") == 10, min_timeout("turn-count-hook.py")
    return "a pure-Python hook (turn-count-hook.py) budget floor is 10s"


def test_winsubp_import_regex() -> str:
    assert _WINSUBP_IMPORT_RE.search("import _winsubp")
    assert _WINSUBP_IMPORT_RE.search("import _winsubp  # noqa: F401")
    assert _WINSUBP_IMPORT_RE.search("from _winsubp import apply")  # the form a substring check missed
    assert _WINSUBP_IMPORT_RE.search("    import _winsubp")         # indented
    assert not _WINSUBP_IMPORT_RE.search("# import _winsubp")       # commented out
    assert not _WINSUBP_IMPORT_RE.search("import _winsubp_other")   # word boundary
    return "anchored regex matches import/from _winsubp, ignores commented lines + _winsubp_other"


# ---------------------------------------------------------------------------
# repo-wide gates
# ---------------------------------------------------------------------------

def test_every_command_resolves_to_existing_script() -> str:
    settings = wiring.load_settings()
    entries = wiring.hook_entries(settings)
    bad = []
    for e in entries:
        if e.script is None:
            bad.append(f"{e.event}/{e.matcher}: command resolves to no .py -> {e.command!r}")
        elif not (wiring.SCRIPTS_DIR / e.script).exists():
            bad.append(f"{e.event}/{e.matcher}: {e.script} not found in claude/scripts/")
    assert not bad, "Hook commands not resolving to an existing script:\n  " + "\n  ".join(bad)
    return f"all {len(entries)} hook entries resolve to an existing claude/scripts/*.py"


def test_every_entry_has_timeout_at_or_above_budget() -> str:
    settings = wiring.load_settings()
    entries = wiring.hook_entries(settings)
    bad = []
    for e in entries:
        if e.script is None:
            continue  # already reported by the resolution gate
        floor = min_timeout(e.script)
        t = e.timeout
        if not isinstance(t, int) or isinstance(t, bool):
            bad.append(f"{e.event}/{e.matcher}:{e.script}: timeout is {t!r}, expected an int >= {floor}")
        elif t < floor:
            bad.append(f"{e.event}/{e.matcher}:{e.script}: timeout {t}s < budget floor {floor}s")
    assert not bad, "Hook entries with a missing / too-tight timeout:\n  " + "\n  ".join(bad)
    return f"all {len(entries)} hook entries declare an explicit timeout >= their budget floor"


def main() -> int:
    tests = [
        ("script_from_command extracts basename", test_script_from_command_extracts_basename),
        ("script_from_command None when not .py", test_script_from_command_none_when_not_py),
        ("min_timeout usage-snapshot = 90", test_min_timeout_usage_snapshot_is_90),
        ("min_timeout subprocess = 30", test_min_timeout_subprocess_is_30),
        ("min_timeout pure-Python = 10", test_min_timeout_pure_python_is_10),
        ("_winsubp import regex (anchored)", test_winsubp_import_regex),
        ("every command resolves to an existing script", test_every_command_resolves_to_existing_script),
        ("every entry timeout >= budget", test_every_entry_has_timeout_at_or_above_budget),
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
