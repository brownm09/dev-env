#!/usr/bin/env python3
"""Verify `pyw -3` stdio behavior for Claude Code hook compatibility.

`pythonw.exe` (which `pyw -3` resolves to) is built against the Windows
subsystem rather than the console subsystem. The known wrinkle: when the
parent process does NOT supply stdio handles, `pythonw` leaves
`sys.stdin`, `sys.stdout`, and `sys.stderr` set to `None` instead of
wiring them to a console. Any hook script that touches stdio under those
conditions would crash with `AttributeError: 'NoneType' object has no
attribute 'read'` (or `write`, or `flush`).

Claude Code spawns hooks with stdio pipes (it has to — that's how it
delivers the hook event payload as JSON on stdin and reads the
`systemMessage` JSON back on stdout). So the question is empirical: does
`pyw -3` honor parent-supplied pipes the same way `py -3` does?

This test answers that question by running `pyw -3` under
`subprocess.Popen` with `stdin=PIPE, stdout=PIPE, stderr=PIPE` — exactly
the shape Claude Code uses — and asserting:

  1. `sys.stdin`, `sys.stdout`, `sys.stderr` are all non-None inside the
     child.
  2. Bytes written to the child's stdin are readable via `sys.stdin.read()`.
  3. Bytes the child writes to `sys.stdout` reach the parent intact.
  4. A synthetic Claude-Code-shaped hook (reads JSON on stdin, writes
     `systemMessage` JSON to stdout, exits 0) round-trips correctly.
  5. Every hook script referenced from `claude/settings.json` exists on
     disk under `claude/scripts/` and is syntactically valid Python —
     the test does not invoke real hooks (many have side effects:
     scratch writes, git fetches, project board mutations).
  6. A `subprocess.run(..., text=True)` call made after `import _winsubp`
     decodes a child's UTF-8 output as UTF-8, not the Windows cp1252
     default codepage — the dev-env#503 crash, reproduced end-to-end with
     the exact byte from its traceback and proven fixed.

If any check fails, the `pyw -3` swap in `claude/settings.json` is not
safe and should be reverted.

Usage:
    py -3 claude/scripts/tests/test_pyw_stdio.py

The test runs under `py -3` (a console-attached parent); the
subprocess invocations under test are `pyw -3`. Exit 0 = all pass.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS = REPO_ROOT / "claude" / "settings.json"
HOOKS_DIR = REPO_ROOT / "claude" / "scripts"


def _require_pyw() -> str:
    pyw = shutil.which("pyw")
    if not pyw:
        raise RuntimeError(
            "`pyw` is not on PATH. This test only runs on Windows with the "
            "python.org Python Launcher installed. ADR-007 covers the "
            "Windows-only scope of the hook invocation rule."
        )
    return pyw


def test_pyw_stdio_wired_when_parent_supplies_pipes() -> str:
    """Direct invariant: pyw -3 with subprocess.PIPE must NOT leave stdio None."""
    _require_pyw()
    program = (
        "import sys, json\n"
        "payload = sys.stdin.read()\n"
        "result = {\n"
        "    'stdin_is_none': sys.stdin is None,\n"
        "    'stdout_is_none': sys.stdout is None,\n"
        "    'stderr_is_none': sys.stderr is None,\n"
        "    'stdin_payload': payload,\n"
        "}\n"
        "sys.stdout.write(json.dumps(result))\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('stderr-channel-ok')\n"
    )
    input_payload = '{"hook_event_name":"UserPromptSubmit","prompt":"probe"}'
    proc = subprocess.run(
        ["pyw", "-3", "-c", program],
        input=input_payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"pyw -3 -c <program> exited {proc.returncode}; "
            f"stderr={proc.stderr!r}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"pyw stdout was not valid JSON (suggests stdio truncation): "
            f"stdout={proc.stdout!r}; error={e}"
        )
    if result["stdin_is_none"]:
        raise AssertionError("sys.stdin is None inside pyw -3 child")
    if result["stdout_is_none"]:
        raise AssertionError("sys.stdout is None inside pyw -3 child")
    if result["stderr_is_none"]:
        raise AssertionError("sys.stderr is None inside pyw -3 child")
    if result["stdin_payload"] != input_payload:
        raise AssertionError(
            f"stdin payload round-trip mismatch: sent {input_payload!r}, "
            f"received {result['stdin_payload']!r}"
        )
    if proc.stderr != "stderr-channel-ok":
        raise AssertionError(
            f"stderr channel not propagated: got {proc.stderr!r}"
        )
    return "stdin/stdout/stderr all wired; stdin payload round-trips; stderr channel propagates"


def test_pyw_runs_claude_code_shaped_hook_end_to_end() -> str:
    """End-to-end: a hook that mimics Claude Code's contract must work."""
    _require_pyw()
    # Synthetic hook: reads a JSON event from stdin, writes a Claude-shaped
    # JSON response to stdout (the `{"systemMessage": "..."}` pattern used
    # by real hooks like post-compact.py and dev-env-sync.py), exits 0.
    synthetic = (
        "import sys, json\n"
        "event = json.loads(sys.stdin.read())\n"
        "resp = {'systemMessage': 'echo:' + event.get('hook_event_name','')}\n"
        "sys.stdout.write(json.dumps(resp))\n"
        "sys.stdout.flush()\n"
        "sys.exit(0)\n"
    )
    event_payload = json.dumps(
        {
            "session_id": "test",
            "hook_event_name": "PostCompact",
            "transcript_path": "",
            "cwd": str(REPO_ROOT),
        }
    )
    proc = subprocess.run(
        ["pyw", "-3", "-c", synthetic],
        input=event_payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"synthetic hook exited {proc.returncode}; stderr={proc.stderr!r}"
        )
    resp = json.loads(proc.stdout)
    if resp.get("systemMessage") != "echo:PostCompact":
        raise AssertionError(
            f"synthetic hook response wrong: {resp!r}"
        )
    return "Claude-Code-shaped JSON-in/JSON-out hook contract works under pyw -3"


def _collect_hook_scripts() -> list[str]:
    """Pull every distinct script path referenced from settings.json hook commands."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})
    scripts: set[str] = set()
    for event_entries in hooks.values():
        for entry in event_entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                parts = cmd.split()
                # Expected shape: "pyw -3 C:/.../scripts/<name>.py"
                if len(parts) >= 3 and parts[2].endswith(".py"):
                    scripts.add(parts[2])
    return sorted(scripts)


def test_all_settings_hooks_use_pyw_and_resolve_to_repo() -> str:
    """Every hook command must invoke `pyw -3` and point at a real script in this repo."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})
    bad_launcher: list[str] = []
    missing: list[str] = []
    syntax_errors: list[str] = []
    total = 0
    for event_entries in hooks.values():
        for entry in event_entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                parts = cmd.split()
                if len(parts) < 3 or not parts[2].endswith(".py"):
                    continue
                total += 1
                launcher = parts[0]
                if launcher != "pyw":
                    bad_launcher.append(cmd)
                    continue
                local = HOOKS_DIR / Path(parts[2]).name
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
    if missing:
        problems.append(f"missing scripts: {missing}")
    if syntax_errors:
        problems.append("syntax errors:\n  " + "\n  ".join(syntax_errors))
    if problems:
        raise AssertionError("\n".join(problems))
    return f"{total} hook commands all use `pyw -3` and resolve to syntactically valid scripts in {HOOKS_DIR.name}/"


def test_winsubp_patches_subprocess_under_pyw() -> str:
    """`import _winsubp` under pyw -3 must install the Popen patch and let subprocess still work."""
    _require_pyw()
    scripts_dir = (REPO_ROOT / "claude" / "scripts").as_posix()
    program = (
        f"import sys; sys.path.insert(0, {scripts_dir!r})\n"
        "import _winsubp  # noqa: F401\n"
        "import subprocess, json\n"
        "patched = getattr(subprocess, '_winsubp_patched', False)\n"
        "proc = subprocess.run(['cmd','/c','echo','probe'], capture_output=True, text=True)\n"
        "sys.stdout.write(json.dumps({'patched': patched, 'echo_rc': proc.returncode, 'echo_out': proc.stdout.strip()}))\n"
    )
    proc = subprocess.run(
        ["pyw", "-3", "-c", program],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise AssertionError(f"pyw child exited {proc.returncode}; stderr={proc.stderr!r}")
    result = json.loads(proc.stdout)
    if not result["patched"]:
        raise AssertionError("_winsubp did not set subprocess._winsubp_patched=True under pyw")
    if result["echo_rc"] != 0 or result["echo_out"] != "probe":
        raise AssertionError(
            f"patched subprocess.run broke the echo round-trip: rc={result['echo_rc']}, "
            f"out={result['echo_out']!r}"
        )
    return "subprocess patched (CREATE_NO_WINDOW applied) and subprocess.run still functions"


def test_winsubp_decodes_utf8_not_cp1252_under_pyw() -> str:
    """The exact dev-env#503 crash, reproduced end-to-end and proven fixed.

    Before the fix, a hook whose `subprocess.run(..., text=True)` captured a
    child's stdout containing byte 0x9d (unmapped in cp1252, the Windows
    default text-mode codepage) crashed with `UnicodeDecodeError: 'charmap'
    codec can't decode byte 0x9d` — verbatim the traceback in dev-env#503,
    which crashed post-tool-use.py's `gh project item-add` read.

    That crash happens inside `subprocess.run`'s internal stdout-reader
    THREAD (confirmed empirically while writing this test), so it does not
    raise as a catchable exception in the caller: the default
    unhandled-thread-exception hook prints it to stderr and the read is
    simply lost — `proc.stdout` ends up `None` rather than raising. So the
    observable, testable effect of the bug is "the captured output silently
    disappears," not "a UnicodeDecodeError propagates to my try/except."

    This spawns a grandchild that writes that exact byte and asserts the
    patched `_winsubp` default (`encoding="utf-8", errors="replace"`)
    decodes it to U+FFFD — a normal, non-None result — instead. The JSON
    status crossing back to this (unpatched) test-runner process is
    ASCII-only (`json.dumps` escapes non-ASCII by default), so it never
    itself depends on the fix being tested.
    """
    _require_pyw()
    scripts_dir = (REPO_ROOT / "claude" / "scripts").as_posix()
    grandchild = "import sys; sys.stdout.buffer.write(bytes([0x9d]))"
    program = (
        f"import sys; sys.path.insert(0, {scripts_dir!r})\n"
        "import _winsubp  # noqa: F401\n"
        "import subprocess, json\n"
        f"proc = subprocess.run([sys.executable, '-c', {grandchild!r}], capture_output=True, text=True)\n"
        "sys.stdout.write(json.dumps({'decoded': proc.stdout}))\n"
    )
    # encoding/errors set explicitly (not left to default) even though this
    # outer capture only ever carries ASCII-safe content in practice (json.dumps
    # defaults to ensure_ascii=True, and Python tracebacks are ASCII) -- a test
    # that proves UTF-8-vs-cp1252 decoding matters should not itself rely on an
    # unspecified default for its own capture.
    proc = subprocess.run(
        ["pyw", "-3", "-c", program],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if proc.returncode != 0:
        raise AssertionError(f"pyw child exited {proc.returncode}; stderr={proc.stderr!r}")
    if "UnicodeDecodeError" in proc.stderr or "Exception in thread" in proc.stderr:
        raise AssertionError(
            "the dev-env#503 crash is NOT fixed -- the reader thread still raised:\n"
            f"{proc.stderr}"
        )
    result = json.loads(proc.stdout)
    if result["decoded"] != chr(0xFFFD):
        raise AssertionError(
            "byte 0x9d was not decoded as U+FFFD (got "
            f"{result['decoded']!r}) -- the dev-env#503 crash is NOT fixed. Pre-fix, "
            "this reader-thread UnicodeDecodeError does not raise in the caller; it "
            "prints to stderr and leaves stdout as None (see docstring above)."
        )
    return "byte 0x9d (dev-env#503's exact crash byte) decodes to U+FFFD instead of being silently lost"


# Regexes used by the static-scan guard below. Defined at module scope so
# the negative-control assertion at the bottom of this test exercises the
# exact same patterns the real scan uses.
#
# `_WINSUBP_IMPORT_RE` matches a real top-level import of the helper —
# either `import _winsubp` (optionally with `as alias` or `# comment`) or
# `from _winsubp import X`. Multiline + leading-whitespace tolerant so an
# indented re-import inside a function still counts; comments and
# docstring mentions of the bare string `_winsubp` do not.
_WINSUBP_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+_winsubp\b|from\s+_winsubp\b)",
    re.MULTILINE,
)
# `_SUBPROCESS_USE_RE` matches an actual subprocess use-site. Covers both
# `subprocess.run(...)` / `subprocess.Popen(...)` etc. AND
# `from subprocess import run, Popen` (or any subset) so that a hook
# using the `from subprocess import` idiom is not silently skipped.
_SUBPROCESS_USE_RE = re.compile(
    r"\bsubprocess\.(?:run|Popen|check_output|check_call|call)\b"
    r"|^\s*from\s+subprocess\s+import\b",
    re.MULTILINE,
)


def test_every_subprocess_using_hook_imports_winsubp() -> str:
    """Every hook script in claude/scripts/ that uses subprocess must import _winsubp.

    The check uses real regexes (not substring matches) so neither a
    comment/docstring mention of `_winsubp` nor a `from subprocess import …`
    idiom can bypass the guard.
    """
    hooks_dir = REPO_ROOT / "claude" / "scripts"
    missing: list[str] = []
    checked = 0
    for path in sorted(hooks_dir.glob("*.py")):
        # Skip the helper itself and any underscore-prefixed support modules.
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        if not _SUBPROCESS_USE_RE.search(text):
            continue
        checked += 1
        if not _WINSUBP_IMPORT_RE.search(text):
            missing.append(path.name)
    if missing:
        raise AssertionError(
            f"{len(missing)} hook(s) use subprocess but do not import _winsubp: "
            + ", ".join(missing)
        )

    # Negative controls — make sure the regexes don't false-positive on
    # comments/docstrings or false-negative on the `from subprocess import`
    # idiom. If these fail, the guard above is no longer load-bearing.
    if _WINSUBP_IMPORT_RE.search("# _winsubp is great\n"):
        raise AssertionError("import regex false-positive on a comment mentioning _winsubp")
    if _WINSUBP_IMPORT_RE.search('"""mentions _winsubp in a docstring"""\n'):
        raise AssertionError("import regex false-positive on a docstring mentioning _winsubp")
    if not _SUBPROCESS_USE_RE.search("from subprocess import run\nrun(['x'])\n"):
        raise AssertionError("subprocess-use regex missed the `from subprocess import` idiom")

    return f"all {checked} subprocess-using hook scripts import _winsubp"


def test_no_hook_spawns_python_via_py_launcher() -> str:
    """No hook in claude/scripts/ may spawn Python via the `py` / `py.exe` launcher.

    Naming `py` as the first element of a `subprocess.Popen` / `subprocess.run`
    argv re-introduces the launcher-chain console flash that ADR-007 follow-up 2
    fixed (dev-env#300): `py.exe` is a console-subsystem program that spawns
    `python.exe` without propagating `CREATE_NO_WINDOW`, so Windows allocates a
    fresh console for the grandchild. `_winsubp` only suppresses the immediate
    child's console, not the grandchild's. Hooks that spawn Python must use
    `sys.executable` (or another already-windowless launcher).

    This check is AST-based: it walks every `subprocess.Popen(...)` /
    `subprocess.run(...)` / `subprocess.check_*(...)` / `subprocess.call(...)`
    call in `claude/scripts/*.py` and fails if any of them passes a list/tuple
    literal whose first element is the bare string `"py"` or `"py.exe"`.
    String-command spawns (e.g., `shell=True` invocations of `"py -3 ..."`)
    are out of scope — those flash for other reasons and would be caught by
    review separately.
    """
    hooks_dir = REPO_ROOT / "claude" / "scripts"
    SPAWN_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
    FORBIDDEN = {"py", "py.exe"}
    offenders: list[str] = []
    scanned = 0
    for path in sorted(hooks_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match `subprocess.<fn>(...)`.
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in SPAWN_FUNCS
            ):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, (ast.List, ast.Tuple)) or not first.elts:
                continue
            head = first.elts[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                if head.value.lower() in FORBIDDEN:
                    offenders.append(
                        f"{path.name}:{node.lineno} — argv[0]={head.value!r}"
                    )
    if offenders:
        raise AssertionError(
            "Hook(s) spawn Python via the `py` launcher (re-introduces "
            "launcher-chain console flash; see ADR-007 follow-up 2):\n  "
            + "\n  ".join(offenders)
        )

    # Negative control — a synthetic offending snippet must trip the AST walk.
    # If this stops detecting, the guard above is no longer load-bearing.
    bad = "import subprocess\nsubprocess.Popen(['py', '-3', 'x.py'])\n"
    bad_tree = ast.parse(bad)
    detected = False
    for node in ast.walk(bad_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in SPAWN_FUNCS
            and node.args
            and isinstance(node.args[0], (ast.List, ast.Tuple))
            and node.args[0].elts
            and isinstance(node.args[0].elts[0], ast.Constant)
            and node.args[0].elts[0].value in FORBIDDEN
        ):
            detected = True
    if not detected:
        raise AssertionError("AST walker failed to detect a synthetic `py` offender")

    return f"scanned {scanned} hook script(s); none spawn Python via the `py` launcher"


def main() -> int:
    tests = [
        ("pyw -3 stdio wired when parent supplies pipes", test_pyw_stdio_wired_when_parent_supplies_pipes),
        ("pyw -3 runs Claude-Code-shaped hook end-to-end", test_pyw_runs_claude_code_shaped_hook_end_to_end),
        ("all settings.json hooks use pyw -3 and resolve", test_all_settings_hooks_use_pyw_and_resolve_to_repo),
        ("_winsubp patches subprocess under pyw -3", test_winsubp_patches_subprocess_under_pyw),
        ("_winsubp decodes UTF-8 not cp1252 under pyw -3", test_winsubp_decodes_utf8_not_cp1252_under_pyw),
        ("every subprocess-using hook imports _winsubp", test_every_subprocess_using_hook_imports_winsubp),
        ("no hook spawns Python via the `py` launcher", test_no_hook_spawns_python_via_py_launcher),
    ]
    failed = 0
    passed_names: list[str] = []
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
            passed_names.append(name)
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
