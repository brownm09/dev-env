#!/usr/bin/env python3
"""Structural gate: no test may write into the production heartbeat ledger
(dev-env#1163).

A test that runs a real wired hook reaches that hook's unconditional
``_hookutil.record_heartbeat()`` (ADR-106). Without ``HOOK_HEARTBEAT_DIR_OVERRIDE``
the fire lands in ``~/.claude/scratch/hook-heartbeat/``, refreshing the hook's
liveness heartbeat and mixing test fires into its timing data.

Every test file in this directory and ``claude/hooks/tests/`` that is
**hook-driving** (names a wired hook script and runs it: a Python-launching
subprocess, a ``<expr>.main()`` call, or ``runpy``; a bash gate that names a
wired script and invokes Python) must be **isolated**: it calls
``isolate_heartbeat()`` from ``_heartbeat_isolation.py`` as a module-level
statement, or *sets* the override (a subscript assignment, a dict-literal key,
a keyword argument; in bash an assignment or ``export``). Merely naming it -- in a
docstring, a comparison, a ``.pop`` or an ``unset`` -- does not count.

Classifier rules, known gaps, and the calibration against a probe of the real
ledger are in ``docs/TESTING.md`` item 106.

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
ISOLATION_CALLS = {"isolate_heartbeat"}
SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
PY_LAUNCHERS = {"py", "pyw", "python", "python3", "pythonw"}
# Receiver placeholder for a call on a non-Name expression: ``_load().main()``.
EXPR_RECEIVER = "<expr>"


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
    """(receiver, function name) for ``a.f(...)``, ``<expr>.f(...)`` or ``f(...)``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        recv = func.value.id if isinstance(func.value, ast.Name) else EXPR_RECEIVER
        return recv, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _names_python(node: ast.AST) -> bool:
    """True when *node*'s subtree names a Python interpreter: ``sys.executable``,
    a ``py``/``pyw``/``python`` literal, or a shell string starting with one
    (``"py -3 hook.py"``)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "executable":
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                and sub.value.split(" ", 1)[0] in PY_LAUNCHERS:
            return True
    return False


def _launcher_names(tree: ast.Module) -> set[str]:
    """Names, at any depth, that stand for a Python interpreter: a function or
    method whose body names one (``_py_cmd()``, ``self._py()``) or a variable
    assigned one (``PY = [sys.executable]``, ``cmd = [sys.executable, HOOK]``)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _names_python(node):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                and _names_python(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _launches_python(call: ast.Call, launchers: set[str]) -> bool:
    """True when a subprocess call runs Python, as opposed to git, npm, or a shell
    builtin: its arguments name an interpreter, or a launcher name or call."""
    if _names_python(call):
        return True
    for sub in ast.walk(call):
        if isinstance(sub, ast.Name) and sub.id in launchers:
            return True
        if isinstance(sub, ast.Call) and _call_name(sub)[1] in launchers:
            return True
    return False


def _subprocess_bindings(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(module aliases, bare function names) bound to subprocess:
    ``import subprocess as sp`` and ``from subprocess import run``."""
    modules, funcs = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            funcs.update(a.asname or a.name for a in node.names if a.name in SUBPROCESS_FUNCS)
    return modules, funcs


def _sets_override(tree: ast.Module) -> bool:
    """True when the override is *set*: assigned through a subscript
    (``env["X"] = ...``), used as a dict-literal key, or passed as a keyword
    (``dict(os.environ, X=...)``). A docstring, a comparison or a ``.pop`` does
    not count."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and t.slice.value == OVERRIDE:
                    return True
        elif isinstance(node, ast.Dict):
            if any(isinstance(k, ast.Constant) and k.value == OVERRIDE for k in node.keys):
                return True
        elif isinstance(node, ast.keyword) and node.arg == OVERRIDE:
            return True
    return False


def _calls_isolate_at_module_level(tree: ast.Module) -> bool:
    """True when ``isolate_heartbeat()`` is a top-level statement, so it runs at
    import, before any test can drive a hook."""
    return any(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and _call_name(node.value)[1] in ISOLATION_CALLS
        for node in tree.body
    )


def classify_python(source: str, wired: set[str]) -> tuple[bool, bool]:
    """Return (hook_driving, isolated) for a Python test file's source."""
    tree = ast.parse(source)
    strings = code_strings(tree)
    names_hook = any(w in s for s in strings for w in wired)
    launchers = _launcher_names(tree)
    sub_modules, sub_funcs = _subprocess_bindings(tree)
    executes = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        recv, name = _call_name(node)
        is_subprocess = (recv in sub_modules and name in SUBPROCESS_FUNCS) or (
            recv is None and name in sub_funcs
        )
        if is_subprocess and _launches_python(node, launchers):
            executes = True
        elif (name == "main" and recv not in (None, "unittest")) or recv == "runpy":
            # A bare main() is the test file's own runner; <module>.main() is a hook's.
            executes = True
    isolated = _calls_isolate_at_module_level(tree) or _sets_override(tree)
    return names_hook and executes, isolated


def _code_lines(source: str) -> list[str]:
    return [ln for ln in source.splitlines() if not ln.lstrip().startswith("#")]


_BASH_PYTHON = re.compile(r"(\bpyw? -3\b|\bpython[w3]?\b|\$\{?PY(THON)?\}?)", re.IGNORECASE)
_BASH_SETS_OVERRIDE = re.compile(rf"^\s*(export\s+)?{OVERRIDE}=\S")


def classify_bash(source: str, wired: set[str]) -> tuple[bool, bool]:
    """Return (hook_driving, isolated) for a bash gate's source. Isolated means
    the override is assigned (``X=...`` or ``export X=...``), not merely named."""
    lines = _code_lines(source)
    names_hook = any(w in ln for ln in lines for w in wired)
    runs_python = any(_BASH_PYTHON.search(ln) for ln in lines)
    isolated = any(_BASH_SETS_OVERRIDE.match(ln) for ln in lines)
    return names_hook and runs_python, isolated


def wired_scripts() -> set[str]:
    return set(_hook_wiring.wired_scripts(_hook_wiring.load_settings()))


def test_files() -> list[Path]:
    files = []
    for directory in (TESTS_DIR, HOOKS_TESTS_DIR):
        files += sorted(directory.glob("test_*.py")) + sorted(directory.glob("*.sh"))
    return [f for f in files if f.name != Path(__file__).name]


def classify(path: Path, wired: set[str]) -> tuple[bool, bool]:
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return classify_python(source, wired)
    return classify_bash(source, wired)


# --- fixtures: known-bad and known-good shapes ---------------------------------

BAD_PYTHON = {
    "subprocess": '''
import subprocess, sys
HOOK = SCRIPTS / "{hook}"
def test_blocks():
    subprocess.run([sys.executable, str(HOOK)], input="{{}}", text=True)
''',
    "in-process main()": '''
import importlib.util
spec = importlib.util.spec_from_file_location("h", SCRIPTS / "{hook}")
mod = importlib.util.module_from_spec(spec)
def test_main():
    mod.main()
''',
    "docstring-only mention": '''
"""Mentions HOOK_HEARTBEAT_DIR_OVERRIDE in prose but never sets it."""
import subprocess, sys
def test_blocks():
    subprocess.run([sys.executable, "{hook}"])
''',
    "module-level interpreter constant": '''
import subprocess, sys
PY = [sys.executable]
def test_blocks():
    subprocess.run(PY + ["{hook}"])
''',
    "from subprocess import run": '''
import sys
from subprocess import run
def test_blocks():
    run([sys.executable, "{hook}"])
''',
    "main() on a call result": '''
def _load():
    return load_module(SCRIPTS / "{hook}")
def test_main():
    _load().main()
''',
    "local command variable": '''
import subprocess, sys
def test_blocks():
    cmd = [sys.executable, str(SCRIPTS / "{hook}")]
    subprocess.run(cmd)
''',
    "aliased subprocess import": '''
import subprocess as sp, sys
def test_blocks():
    sp.run([sys.executable, "{hook}"])
''',
    "shell string": '''
import subprocess
def test_blocks():
    subprocess.run("py -3 claude/scripts/{hook}", shell=True)
''',
    "launcher method": '''
import subprocess, sys
class T:
    def _py(self):
        return [sys.executable]
    def test_blocks(self):
        subprocess.run(self._py() + ["{hook}"])
''',
    "isolate_heartbeat() inside a test only": '''
import subprocess, sys
from _heartbeat_isolation import isolate_heartbeat
def test_blocks():
    subprocess.run([sys.executable, "{hook}"])
def test_later():
    isolate_heartbeat()
''',
    "override name only removed": '''
import os, subprocess, sys
def test_blocks():
    env = {{k: v for k, v in os.environ.items() if k != "HOOK_HEARTBEAT_DIR_OVERRIDE"}}
    env.pop("HOOK_HEARTBEAT_DIR_OVERRIDE", None)
    subprocess.run([sys.executable, "{hook}"], env=env)
''',
}

BAD_BASH = {
    "unisolated": 'HOOK="$SCRIPT_DIR/../{hook}"\necho "{{}}" | $PY "$HOOK"\n',
    "override only unset": 'unset HOOK_HEARTBEAT_DIR_OVERRIDE\necho "{{}}" | "$PYTHON" "$SCRIPT_DIR/../{hook}"\n',
}

GOOD_ISOLATED = {
    "isolate_heartbeat() at module level": '''
import subprocess, sys
from _heartbeat_isolation import isolate_heartbeat
isolate_heartbeat()
def test_blocks():
    subprocess.run([sys.executable, str(SCRIPTS / "{hook}")])
''',
    "override set by subscript": '''
import os, subprocess, sys
def test_blocks(tmp):
    env = dict(os.environ)
    env["HOOK_HEARTBEAT_DIR_OVERRIDE"] = str(tmp)
    subprocess.run([sys.executable, "{hook}"], env=env)
''',
    "override passed as a keyword": '''
import os, subprocess, sys
def test_blocks(tmp):
    subprocess.run([sys.executable, "{hook}"], env=dict(os.environ, HOOK_HEARTBEAT_DIR_OVERRIDE=tmp))
''',
}

GOOD_PURE = '''
import importlib.util, subprocess, sys, unittest
spec = importlib.util.spec_from_file_location("h", SCRIPTS / "{hook}")
mod = importlib.util.module_from_spec(spec)
def test_classify():
    subprocess.run(["git", "init"])
    assert mod.classify("git status") == "read"
def main():
    return 0
if __name__ == "__main__":
    unittest.main()
    sys.exit(main())
'''

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
    for name, src in BAD_PYTHON.items():
        driving, isolated = classify_python(src.format(hook=hook), wired)
        assert driving and not isolated, f"known-bad {name} fixture not flagged: {(driving, isolated)}"
    for name, src in BAD_BASH.items():
        driving, isolated = classify_bash(src.format(hook=hook), wired)
        assert driving and not isolated, f"known-bad bash {name} fixture not flagged: {(driving, isolated)}"


def test_known_good_fixtures_pass() -> None:
    wired = wired_scripts()
    hook = _fixture_hook(wired)
    for name, src in GOOD_ISOLATED.items():
        driving, isolated = classify_python(src.format(hook=hook), wired)
        assert driving and isolated, f"known-good {name} fixture misclassified: {(driving, isolated)}"
    driving, _ = classify_python(GOOD_PURE.format(hook=hook), wired)
    assert not driving, (
        "a test that loads a hook, runs git and its own main()/unittest.main(), but never "
        "runs the hook, was classified hook-driving"
    )
    driving, isolated = classify_bash(GOOD_BASH.format(hook=hook), wired)
    assert driving and isolated, f"known-good bash fixture misclassified: {(driving, isolated)}"


def test_every_hook_driving_test_is_isolated() -> None:
    wired = wired_scripts()
    offenders = [
        path.name for path in test_files()
        if classify(path, wired) == (True, False)
    ]
    assert not offenders, (
        "these tests run a real hook without HOOK_HEARTBEAT_DIR_OVERRIDE, so they write "
        "into ~/.claude/scratch/hook-heartbeat/ -- call isolate_heartbeat() from "
        f"_heartbeat_isolation.py at module level: {offenders}"
    )


TESTS = [
    ("wired-script list is non-empty", test_wired_scripts_nonempty),
    ("test corpus and hook-driving set are non-empty", test_corpus_nonempty),
    ("known-bad fixtures are flagged", test_known_bad_fixtures_flagged),
    ("known-good fixtures pass", test_known_good_fixtures_pass),
    ("every hook-driving test isolates the heartbeat ledger", test_every_hook_driving_test_is_isolated),
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
