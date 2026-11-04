#!/usr/bin/env python3
"""Static source scans behind the ADR-007 hook-invocation invariants.

These checks only read files, so unlike `test_pyw_stdio.py` (which spawns
real `pythonw.exe` children and needs an interactive console) they run under
`run-hook-tests.py` and therefore in the `hook-tests` CI workflow. They used
to live in `test_pyw_stdio.py`, which the runner skips as a whole, so CI never
ran them: `_auto_resume.py` merged without `import _winsubp` and stayed red on
`main` until a local run caught it (dev-env#1191, dev-env#1194).

  1. Every hook command in `claude/settings.shared.json` runs `pyw -3` through
     the hook launcher (ADR-148) and names scripts that exist in
     `claude/scripts/` and parse as Python.
  2. Every `claude/scripts/*.py` that uses subprocess imports `_winsubp`
     (ADR-007, 2026-06-01 follow-up).
  3. No `claude/scripts/*.py` spawns Python through the `py` / `py.exe`
     launcher (ADR-007 follow-up 2, dev-env#300).

Each scan is a function over a directory, so the fixture tests below run the
same function against a temporary directory holding a known-bad module and a
known-good one. Each real-directory scan also asserts that it examined at
least one file, so an empty glob fails instead of passing (ADR-144).

Usage:
    py -3 claude/scripts/tests/test_hook_static_scans.py

Exit 0 = all pass.
"""

import ast
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _settings_sync import iter_hook_entries  # noqa: E402 -- the one settings walker (dev-env#1169)

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS = REPO_ROOT / "claude" / "settings.shared.json"
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"

# `_WINSUBP_IMPORT_RE` matches a real import of the helper: `import _winsubp`
# (optionally with `as alias` or a trailing comment) or `from _winsubp import X`.
# Leading whitespace is allowed, so an indented import inside a function counts;
# a comment or docstring that mentions `_winsubp` does not.
_WINSUBP_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+_winsubp\b|from\s+_winsubp\b)",
    re.MULTILINE,
)
# `_SUBPROCESS_USE_RE` matches a subprocess use: `subprocess.run(...)` and the
# other spawn functions, and `from subprocess import ...`, so a module using
# that idiom is not silently skipped.
_SUBPROCESS_USE_RE = re.compile(
    r"\bsubprocess\.(?:run|Popen|check_output|check_call|call)\b"
    r"|^\s*from\s+subprocess\s+import\b",
    re.MULTILINE,
)

_SPAWN_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
_FORBIDDEN_LAUNCHERS = {"py", "py.exe"}


def _is_winsubp_module(name: str) -> bool:
    """True only for `_winsubp.py`, the one module that cannot import itself.

    Every other underscore-prefixed support module (`_hookio.py`,
    `_gh_project.py`, ...) is a normal scan target.
    """
    return name == "_winsubp.py"


def scan_winsubp_imports(directory: Path) -> tuple[int, list[str]]:
    """Return (subprocess-using files checked, names missing `import _winsubp`)."""
    checked = 0
    missing: list[str] = []
    for path in sorted(directory.glob("*.py")):
        if _is_winsubp_module(path.name):
            continue
        text = path.read_text(encoding="utf-8")
        if not _SUBPROCESS_USE_RE.search(text):
            continue
        checked += 1
        if not _WINSUBP_IMPORT_RE.search(text):
            missing.append(path.name)
    return checked, missing


def find_py_launcher_spawns(tree: ast.AST) -> list[tuple[int, str]]:
    """Return (lineno, argv[0]) for each `subprocess.<spawn>([...])` whose argv
    is a list/tuple literal starting with the bare string `py` or `py.exe`.

    String commands (`shell=True` with `"py -3 ..."`) are out of scope.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in _SPAWN_FUNCS
        ):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, (ast.List, ast.Tuple)) or not first.elts:
            continue
        head = first.elts[0]
        if (
            isinstance(head, ast.Constant)
            and isinstance(head.value, str)
            and head.value.lower() in _FORBIDDEN_LAUNCHERS
        ):
            hits.append((node.lineno, head.value))
    return hits


def scan_py_launcher_spawns(directory: Path) -> tuple[int, list[str]]:
    """Return (files parsed, offenders as `name:line -- argv[0]=...`).

    A file that does not parse is an offender too: skipping it would leave the
    rule unchecked for that file while the count still looked healthy.
    """
    scanned = 0
    offenders: list[str] = []
    for path in sorted(directory.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            offenders.append(f"{path.name}:{e.lineno} -- does not parse, so it cannot be scanned")
            continue
        scanned += 1
        for lineno, head in find_py_launcher_spawns(tree):
            offenders.append(f"{path.name}:{lineno} -- argv[0]={head!r}")
    return scanned, offenders


# ---------------------------------------------------------------------------
# Real-repo scans
# ---------------------------------------------------------------------------

def test_all_settings_hooks_use_pyw_and_resolve_to_repo() -> str:
    """Every hook command must invoke `pyw -3` through the hook launcher (ADR-148)
    and point at real, syntactically valid scripts in this repo."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})
    bad_launcher: list[str] = []
    no_hook_launch: list[str] = []
    missing: list[str] = []
    syntax_errors: list[str] = []
    total = 0
    for _event, _group, hook in iter_hook_entries(hooks):
        cmd = hook.get("command", "")
        parts = cmd.split()
        if len(parts) < 3 or not parts[-1].endswith(".py"):
            continue
        total += 1
        if parts[0] != "pyw":
            bad_launcher.append(cmd)
            continue
        # One script, or the dispatcher plus one or more handlers (ADR-151).
        dispatched = len(parts) > 4 and Path(parts[3]).name == "hook-dispatch.py"
        if Path(parts[2]).name != "hook-launch.py" or (len(parts) != 4 and not dispatched):
            no_hook_launch.append(cmd)
            continue
        for script in parts[3:]:
            local = SCRIPTS_DIR / Path(script).name
            if not local.exists():
                missing.append(local.name)
                continue
            try:
                ast.parse(local.read_text(encoding="utf-8"), filename=str(local))
            except SyntaxError as e:
                syntax_errors.append(f"{local.name}: {e}")
    problems: list[str] = []
    if bad_launcher:
        problems.append(f"non-pyw launchers: {bad_launcher}")
    if no_hook_launch:
        problems.append(f"not run through hook-launch.py (ADR-148): {no_hook_launch}")
    if missing:
        problems.append(f"missing scripts: {missing}")
    if syntax_errors:
        problems.append("syntax errors:\n  " + "\n  ".join(syntax_errors))
    if problems:
        raise AssertionError("\n".join(problems))
    if total == 0:
        raise AssertionError("no hook commands parsed from the shared settings -- a vacuous pass")
    return (
        f"{total} hook commands all use `pyw -3 <launcher>` and resolve to syntactically "
        f"valid scripts in {SCRIPTS_DIR.name}/"
    )


def test_every_subprocess_using_script_imports_winsubp() -> str:
    """Every claude/scripts/*.py that uses subprocess must import _winsubp."""
    checked, missing = scan_winsubp_imports(SCRIPTS_DIR)
    if missing:
        raise AssertionError(
            f"{len(missing)} script(s) use subprocess but do not import _winsubp: "
            + ", ".join(missing)
        )
    if checked == 0:
        raise AssertionError(
            f"no subprocess-using scripts found in {SCRIPTS_DIR} -- a vacuous pass, not a clean one"
        )
    return f"all {checked} subprocess-using scripts import _winsubp"


def test_no_script_spawns_python_via_py_launcher() -> str:
    """No claude/scripts/*.py may spawn Python via the `py` / `py.exe` launcher.

    `py.exe` is a console-subsystem program that spawns `python.exe` without
    passing `CREATE_NO_WINDOW` on, so Windows gives the grandchild a fresh
    console (the flash ADR-007 follow-up 2 fixed). Use `sys.executable`.
    """
    scanned, offenders = scan_py_launcher_spawns(SCRIPTS_DIR)
    if offenders:
        raise AssertionError(
            "Script(s) spawn Python via the `py` launcher (re-introduces the "
            "launcher-chain console flash; see ADR-007 follow-up 2):\n  "
            + "\n  ".join(offenders)
        )
    if scanned == 0:
        raise AssertionError(
            f"no scripts parsed in {SCRIPTS_DIR} -- a vacuous pass, not a clean one"
        )
    return f"scanned {scanned} script(s); none spawn Python via the `py` launcher"


# ---------------------------------------------------------------------------
# Fixtures: known-bad and known-good modules through the same scan functions
# ---------------------------------------------------------------------------

def test_winsubp_scan_flags_fixture_missing_import() -> str:
    """The scan must report a subprocess-using module with no `import _winsubp`
    and pass one that has it."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "bad_hook.py").write_text(
            "import subprocess\n"
            "# _winsubp is mentioned here but never imported\n"
            "subprocess.run(['git', 'status'])\n",
            encoding="utf-8",
        )
        (d / "bad_from_import.py").write_text(
            "from subprocess import run\nrun(['git', 'status'])\n",
            encoding="utf-8",
        )
        (d / "good_hook.py").write_text(
            "import _winsubp  # noqa: F401\nimport subprocess\nsubprocess.run(['git', 'status'])\n",
            encoding="utf-8",
        )
        (d / "no_subprocess.py").write_text("print('hi')\n", encoding="utf-8")
        (d / "_winsubp.py").write_text(
            "import subprocess\nsubprocess.Popen\nsubprocess.run(['x'])\n",
            encoding="utf-8",
        )
        checked, missing = scan_winsubp_imports(d)
    if sorted(missing) != ["bad_from_import.py", "bad_hook.py"]:
        raise AssertionError(
            f"expected exactly the two known-bad fixtures to be reported, got {missing}"
        )
    if checked != 3:
        raise AssertionError(
            f"expected 3 subprocess-using fixtures checked (the two bad, the good; "
            f"_winsubp.py exempt, no_subprocess.py skipped), got {checked}"
        )
    for name in ("_hookio.py", "_gh_project.py", "_hookutil.py"):
        if _is_winsubp_module(name):
            raise AssertionError(f"_is_winsubp_module wrongly exempted {name!r}")
    return "known-bad fixtures reported, known-good passes, only _winsubp.py exempt"


def test_py_launcher_scan_flags_fixture_offender() -> str:
    """The scan must report `subprocess.Popen(['py', ...])` and pass `sys.executable`."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "bad_spawn.py").write_text(
            "import subprocess\nsubprocess.Popen(['py', '-3', 'x.py'])\n",
            encoding="utf-8",
        )
        (d / "bad_spawn_exe.py").write_text(
            "import subprocess\nsubprocess.run(('PY.EXE', 'x.py'))\n",
            encoding="utf-8",
        )
        (d / "good_spawn.py").write_text(
            "import subprocess, sys\nsubprocess.run([sys.executable, 'x.py'])\n",
            encoding="utf-8",
        )
        (d / "unparseable.py").write_text("def broken(:\n", encoding="utf-8")
        scanned, offenders = scan_py_launcher_spawns(d)
    names = sorted(o.split(":", 1)[0] for o in offenders)
    if names != ["bad_spawn.py", "bad_spawn_exe.py", "unparseable.py"]:
        raise AssertionError(
            f"expected the two known-bad spawns and the unparseable file, got {offenders}"
        )
    if scanned != 3:
        raise AssertionError(f"expected 3 fixture files parsed, got {scanned}")
    return "known-bad `py` / `PY.EXE` spawns and an unparseable file reported, sys.executable passes"


def test_import_regex_ignores_comments_and_docstrings() -> str:
    """The import regex must not count a comment or docstring mention."""
    if _WINSUBP_IMPORT_RE.search("# _winsubp is great\n"):
        raise AssertionError("import regex matched a comment mentioning _winsubp")
    if _WINSUBP_IMPORT_RE.search('"""mentions _winsubp in a docstring"""\n'):
        raise AssertionError("import regex matched a docstring mentioning _winsubp")
    if not _WINSUBP_IMPORT_RE.search("    import _winsubp as w\n"):
        raise AssertionError("import regex missed an indented aliased import")
    return "comments and docstrings do not count as imports; an indented import does"


def main() -> int:
    tests = [
        ("all settings hooks use pyw -3 and resolve", test_all_settings_hooks_use_pyw_and_resolve_to_repo),
        ("every subprocess-using script imports _winsubp", test_every_subprocess_using_script_imports_winsubp),
        ("no script spawns Python via the `py` launcher", test_no_script_spawns_python_via_py_launcher),
        ("_winsubp scan flags a fixture missing the import", test_winsubp_scan_flags_fixture_missing_import),
        ("py-launcher scan flags a fixture offender", test_py_launcher_scan_flags_fixture_offender),
        ("import regex ignores comments and docstrings", test_import_regex_ignores_comments_and_docstrings),
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
