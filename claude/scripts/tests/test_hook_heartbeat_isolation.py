#!/usr/bin/env python3
"""Structural gate: no test may write into the production heartbeat ledger
(dev-env#1163).

A test that runs a real wired hook -- a subprocess of the real script, or an
in-process ``main()`` call -- reaches that hook's unconditional
``_hookutil.record_heartbeat()`` (ADR-106). Without ``HOOK_HEARTBEAT_DIR_OVERRIDE``
the fire lands in ``~/.claude/scratch/hook-heartbeat/``: it refreshes the hook's
liveness heartbeat (masking a hook that stopped firing in production) and, since
dev-env#848, mixes test fires into the per-hook timing data.

The gate classifies every test file in this directory and ``claude/hooks/tests/``:

  * **hook-driving** -- it names a wired hook script (a string literal equal to a
    basename from ``claude/settings.shared.json``) AND it executes something: a
    ``subprocess`` call, a ``.main(...)`` call, or ``runpy``. A bash gate counts
    when it names a wired script and invokes Python on it.
  * **isolated** -- it calls ``isolate_heartbeat()`` / ``child_env()`` from
    ``_heartbeat_isolation.py``, or uses the ``HOOK_HEARTBEAT_DIR_OVERRIDE`` literal
    in code (a docstring or comment mentioning it does not count).

Every hook-driving file must be isolated, except the named entries in
``EXEMPT``, each of which must still show the isolation it claims.

Calibration (measured 2026-10-30 against origin/main ec66eb1, before the fix, by
logging every ``record_heartbeat`` call that resolved to the real ledger, per test
file): 15 files wrote to the production ledger (13 Python, 2 bash). This detector
flagged all 15 (recall 15/15). It also flagged 10 files that drove real hooks but
happened to isolate by pointing HOME/USERPROFILE at a temp directory -- correct
under the rule above, which asks for the explicit override rather than relying on
a HOME redirect that a later case in the same file can forget (one of the 15,
``test_skill_file_size_advisory.py``, already mixed both). Files neither
hook-driving nor writing: no false positives. The embedded fixtures below pin the
known-bad (unisolated subprocess and in-process drivers) and known-good (isolated
driver, pure-function test) shapes so the classifier cannot drift to a vacuous pass.

Usage:
    py -3 claude/scripts/tests/test_hook_heartbeat_isolation.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hook_wiring  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
HOOKS_TESTS_DIR = _hook_wiring.REPO_ROOT / "claude" / "hooks" / "tests"
OVERRIDE = "HOOK_HEARTBEAT_DIR_OVERRIDE"
ISOLATION_CALLS = {"isolate_heartbeat", "child_env"}
SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
PY_LAUNCHERS = {"py", "pyw", "python", "python3", "pythonw"}

# Hook-driving files allowed to skip the override, with the isolation they use instead.
EXEMPT = {
    "test_hook_liveness_check.py": (
        "USERPROFILE",
        "isolates HOME/USERPROFILE to a temp dir; its end-to-end cases assert the hook's "
        "own heartbeat in that temp home's ledger, which the override would redirect",
    ),
}


def _docstring_ids(tree: ast.AST) -> set[int]:
    """ids of Constant nodes that are bare expression statements (docstrings)."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def code_strings(tree: ast.AST) -> set[str]:
    """String literals used in code, excluding docstrings."""
    skip = _docstring_ids(tree)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
    }


def _call_name(call: ast.Call) -> tuple[str | None, str | None]:
    """(receiver name, function name) for ``a.f(...)`` or ``f(...)``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        recv = func.value.id if isinstance(func.value, ast.Name) else None
        return recv, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _names_python(node: ast.AST) -> bool:
    """True when *node*'s subtree names a Python interpreter (``sys.executable``
    or a ``py``/``pyw``/``python`` literal)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "executable":
            return True
        if isinstance(sub, ast.Constant) and sub.value in PY_LAUNCHERS:
            return True
    return False


def _launches_python(call: ast.Call, launcher_funcs: set[str]) -> bool:
    """True when a subprocess call runs Python, as opposed to git, npm, or a shell
    builtin: its arguments name an interpreter directly, or call a module-level
    helper that does (the ``_py_cmd()`` pattern)."""
    if _names_python(call):
        return True
    return any(
        isinstance(sub, ast.Call) and _call_name(sub)[1] in launcher_funcs
        for sub in ast.walk(call)
    )


def classify_python(source: str, wired: set[str]) -> tuple[bool, bool]:
    """Return (hook_driving, isolated) for a Python test file's source."""
    tree = ast.parse(source)
    strings = code_strings(tree)
    names_hook = any(s in wired for s in strings) or any(
        s.endswith(("/" + w, "\\" + w)) for s in strings for w in wired
    )
    launcher_funcs = {
        node.name for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _names_python(node)
    }
    executes = isolated = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        recv, name = _call_name(node)
        if recv == "subprocess" and name in SUBPROCESS_FUNCS and _launches_python(node, launcher_funcs):
            executes = True
        elif (name == "main" and recv not in (None, "unittest")) or recv == "runpy":
            # A bare main() is the test file's own runner; <module>.main() is a hook's.
            executes = True
        if name in ISOLATION_CALLS:
            isolated = True
    if OVERRIDE in strings:
        isolated = True
    return names_hook and executes, isolated


def _code_lines(source: str) -> list[str]:
    return [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]


def classify_bash(source: str, wired: set[str]) -> tuple[bool, bool]:
    """Return (hook_driving, isolated) for a bash gate's source."""
    lines = _code_lines(source)
    names_hook = any(w in ln for ln in lines for w in wired)
    runs_python = any(re.search(r"(py -3|python3?|\$PY|\$\{PY\})(\s|\"|$)", ln) for ln in lines)
    isolated = any(OVERRIDE in ln for ln in lines)
    return names_hook and runs_python, isolated


def wired_scripts() -> set[str]:
    return set(_hook_wiring.wired_scripts(_hook_wiring.load_settings()))


def test_files() -> list[Path]:
    files = sorted(TESTS_DIR.glob("test_*.py")) + sorted(TESTS_DIR.glob("*.sh"))
    files += sorted(HOOKS_TESTS_DIR.glob("*.sh"))
    return [f for f in files if f.name != Path(__file__).name]


def classify(path: Path, wired: set[str]) -> tuple[bool, bool]:
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return classify_python(source, wired)
    return classify_bash(source, wired)


# --- fixtures: known-bad and known-good shapes ---------------------------------

BAD_SUBPROCESS = '''
import subprocess, sys
HOOK = SCRIPTS / "{hook}"
def test_blocks():
    subprocess.run([sys.executable, str(HOOK)], input="{{}}", text=True)
'''

BAD_IN_PROCESS = '''
import importlib.util
spec = importlib.util.spec_from_file_location("h", SCRIPTS / "{hook}")
mod = importlib.util.module_from_spec(spec)
def test_main():
    mod.main()
'''

BAD_DOCSTRING_ONLY = '''
"""Mentions HOOK_HEARTBEAT_DIR_OVERRIDE in prose but never sets it."""
import subprocess, sys
def test_blocks():
    subprocess.run([sys.executable, "{hook}"])
'''

GOOD_ISOLATED = '''
import subprocess, sys
from _heartbeat_isolation import isolate_heartbeat
isolate_heartbeat()
def test_blocks():
    subprocess.run([sys.executable, str(SCRIPTS / "{hook}")])
'''

GOOD_CHILD_ENV = '''
import subprocess, sys
from _heartbeat_isolation import child_env
def test_blocks():
    subprocess.run([sys.executable, "{hook}"], env=child_env(HOME="x"))
'''

GOOD_PURE = '''
import importlib.util
spec = importlib.util.spec_from_file_location("h", SCRIPTS / "{hook}")
mod = importlib.util.module_from_spec(spec)
def test_classify():
    assert mod.classify("git status") == "read"
'''

BAD_BASH = 'HOOK="$SCRIPT_DIR/../{hook}"\necho "{{}}" | $PY "$HOOK"\n'
GOOD_BASH = (
    'export HOOK_HEARTBEAT_DIR_OVERRIDE="$(mktemp -d)"\n'
    'HOOK="$SCRIPT_DIR/../{hook}"\necho "{{}}" | $PY "$HOOK"\n'
)


def _fixture_hook(wired: set[str]) -> str:
    hook = "pre-tool-use-canonical-mutate-guard.py"
    assert hook in wired, f"fixture hook {hook} is no longer wired; pick another"
    return hook


def test_wired_scripts_nonempty() -> None:
    wired = wired_scripts()
    assert len(wired) >= 20, f"only {len(wired)} wired scripts parsed -- a vacuous pass, not a clean one"


def test_corpus_nonempty() -> None:
    files = test_files()
    wired = wired_scripts()
    driving = [f for f in files if classify(f, wired)[0]]
    assert len(files) >= 50, f"only {len(files)} test files found"
    assert len(driving) >= 15, (
        f"only {len(driving)} hook-driving test files classified -- the detector has "
        "likely stopped recognizing them (a vacuous pass, not a clean one)"
    )


def test_known_bad_fixtures_flagged() -> None:
    wired = wired_scripts()
    hook = _fixture_hook(wired)
    for name, src in (("subprocess", BAD_SUBPROCESS), ("in-process", BAD_IN_PROCESS),
                      ("docstring-only", BAD_DOCSTRING_ONLY)):
        driving, isolated = classify_python(src.format(hook=hook), wired)
        assert driving and not isolated, f"known-bad {name} fixture not flagged: {(driving, isolated)}"
    driving, isolated = classify_bash(BAD_BASH.format(hook=hook), wired)
    assert driving and not isolated, f"known-bad bash fixture not flagged: {(driving, isolated)}"


def test_known_good_fixtures_pass() -> None:
    wired = wired_scripts()
    hook = _fixture_hook(wired)
    for name, src in (("isolate_heartbeat", GOOD_ISOLATED), ("child_env", GOOD_CHILD_ENV)):
        driving, isolated = classify_python(src.format(hook=hook), wired)
        assert driving and isolated, f"known-good {name} fixture misclassified: {(driving, isolated)}"
    driving, _ = classify_python(GOOD_PURE.format(hook=hook), wired)
    assert not driving, "a pure-function test that never runs main() was classified hook-driving"
    driving, isolated = classify_bash(GOOD_BASH.format(hook=hook), wired)
    assert driving and isolated, f"known-good bash fixture misclassified: {(driving, isolated)}"


def test_every_hook_driving_test_is_isolated() -> None:
    wired = wired_scripts()
    offenders = []
    for path in test_files():
        driving, isolated = classify(path, wired)
        if driving and not isolated and path.name not in EXEMPT:
            offenders.append(path.name)
    assert not offenders, (
        "these tests run a real hook without HOOK_HEARTBEAT_DIR_OVERRIDE, so they write "
        "into ~/.claude/scratch/hook-heartbeat/ -- call isolate_heartbeat() from "
        f"_heartbeat_isolation.py at module level: {offenders}"
    )


def test_exemptions_still_hold() -> None:
    wired = wired_scripts()
    for name, (marker, _reason) in EXEMPT.items():
        path = TESTS_DIR / name
        assert path.exists(), f"exempt file {name} no longer exists; drop its EXEMPT entry"
        assert classify(path, wired)[0], f"exempt file {name} no longer drives a hook; drop its entry"
        strings = code_strings(ast.parse(path.read_text(encoding="utf-8")))
        assert marker in strings, f"exempt file {name} no longer sets {marker}; its exemption is void"


TESTS = [
    ("wired-script list is non-empty", test_wired_scripts_nonempty),
    ("test corpus and hook-driving set are non-empty", test_corpus_nonempty),
    ("known-bad fixtures are flagged", test_known_bad_fixtures_flagged),
    ("known-good fixtures pass", test_known_good_fixtures_pass),
    ("every hook-driving test isolates the heartbeat ledger", test_every_hook_driving_test_is_isolated),
    ("every exemption still holds", test_exemptions_still_hold),
]


def main() -> int:
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS: {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}\n      {e}")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
