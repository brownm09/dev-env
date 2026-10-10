#!/usr/bin/env python3
"""Tests for claude/scripts/_hook_launch.py (dev-env#1146, ADR-148).

The launcher sits in front of every wired hook:

    pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/<name>.py

What this pins, in order of how badly a regression would hurt:

  1. **A missing script fails OPEN.** Exit 0 plus one parseable, pure-ASCII
     `{"systemMessage": ...}` line naming the script -- never exit 2, which Claude Code
     reads as a block on every matching tool call, machine-wide. The two fail-closed
     gates fail open too (a user decision recorded in ADR-148), with louder text.
  2. **A present script is untouched.** Its exit code (0, 2, a traceback's 1), stdin,
     stdout, stderr, `__name__ == "__main__"`, `__file__`, `sys.argv` and sibling
     imports through `sys.path[0]` all behave exactly as under `pyw -3 <script>`, so
     every hook keeps its own declared fail direction (ADR-103 authoring rule 5).
  3. **Stdlib only.** The installed copy lives outside the junctioned tree precisely so
     it works when that tree is stale or broken; importing a dev-env module would
     reintroduce the dependency it exists to remove.
  4. **The gate list matches** test_hook_safe_exit_guard.FAIL_CLOSED.

Each case runs the launcher as a real subprocess against throwaway fixture scripts in a
temp directory (`py -3`, plus one real `pyw -3` run when pyw is on PATH).

Run: py -3 claude/scripts/tests/test_hook_launch.py
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
LAUNCHER = SCRIPTS_DIR / "_hook_launch.py"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

import _hook_launch  # noqa: E402

PY = [sys.executable]


def run(args, stdin_text="", interpreter=None):
    """Run the launcher with `args`; return (returncode, stdout, stderr) as text."""
    cmd = (interpreter or PY) + [str(LAUNCHER)] + [str(a) for a in args]
    proc = subprocess.run(
        cmd, input=stdin_text.encode("utf-8"), capture_output=True, timeout=60
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


class Fixture:
    """A temp directory holding fixture hook scripts (and a sibling module)."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp())
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)

    def script(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text(body, encoding="utf-8")
        return path


# --- 1. missing script fails open ---------------------------------------------------


def _system_message(stdout: str) -> str:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line, got {lines!r}"
    payload = json.loads(lines[0])
    assert set(payload) == {"systemMessage"}, f"unexpected keys: {sorted(payload)}"
    msg = payload["systemMessage"]
    msg.encode("ascii")  # raises if not pure ASCII
    lines[0].encode("ascii")
    return msg


def test_missing_script_exits_zero_with_system_message() -> str:
    with Fixture() as fx:
        missing = fx.root / "does-not-exist.py"
        code, out, err = run([missing])
    assert code == 0, f"missing script must exit 0 (fail open), got {code}; stderr={err!r}"
    msg = _system_message(out)
    assert "does-not-exist.py" in msg, msg
    assert "fail-open" in msg and "dev-env#1146" in msg, msg
    assert "git -C ~/Git/dev-env" in msg, "message must name the recovery command"
    assert "fail-closed gate" not in msg, "an ordinary hook must not get the gate warning"
    return "exit 0, one ASCII systemMessage naming the script and the recovery"


def test_missing_gate_fails_open_with_loud_warning() -> str:
    for gate in sorted(_hook_launch.GATE_SCRIPTS):
        with Fixture() as fx:
            code, out, _ = run([fx.root / gate])
        assert code == 0, f"missing gate {gate} must still exit 0 (ADR-148), got {code}"
        msg = _system_message(out)
        assert "fail-closed gate" in msg and "NOT enforcing" in msg, msg
    return f"both gates ({len(_hook_launch.GATE_SCRIPTS)}) fail open with the NOT-enforcing warning"


def test_no_argument_exits_zero() -> str:
    code, out, _ = run([])
    assert code == 0, f"no script argument must exit 0, got {code}"
    msg = _system_message(out)
    assert "<none>" in msg, msg
    return "no argument -> exit 0 with a systemMessage"


def test_directory_is_treated_as_missing() -> str:
    with Fixture() as fx:
        code, out, _ = run([fx.root])
    assert code == 0, f"a directory is not a runnable script; expected exit 0, got {code}"
    _system_message(out)
    return "a directory path -> fail open, not a Python 'can't open' exit 2"


def test_known_bad_direct_invocation_blocks() -> str:
    """Known-bad reference: without the launcher, Python's own exit for a missing script
    is 2 -- the blocking verdict this whole change exists to avoid. If this ever stops
    being 2, the premise of ADR-148 has changed and the ADR needs revisiting."""
    with Fixture() as fx:
        proc = subprocess.run(PY + [str(fx.root / "nope.py")], capture_output=True, timeout=60)
    assert proc.returncode == 2, f"expected python's own missing-script exit 2, got {proc.returncode}"
    return "bare `python <missing>.py` exits 2 (the lockout the launcher prevents)"


# --- 2. a present script is passed through untouched --------------------------------


def test_exit_codes_pass_through() -> str:
    seen = []
    with Fixture() as fx:
        for want, body in (
            (0, "import sys\nsys.exit(0)\n"),
            (2, "import sys\nsys.stderr.write('blocked\\n')\nsys.exit(2)\n"),
            (0, "x = 1\n"),  # falls off the end
            (1, "raise RuntimeError('boom')\n"),  # traceback -> 1
            (7, "import sys\nsys.exit(7)\n"),
        ):
            script = fx.script(f"exit_{want}_{len(seen)}.py", body)
            code, _, err = run([script])
            assert code == want, f"{script.name}: expected exit {want}, got {code}; stderr={err!r}"
            seen.append(want)
        _, _, err = run([fx.root / "exit_2_1.py"])
        assert "blocked" in err, "a hook's stderr (its block reason) must pass through"
        _, _, err = run([fx.root / "exit_1_3.py"])
        assert "RuntimeError: boom" in err, "a crash's traceback must reach stderr unchanged"
    return f"exit codes {seen} and stderr pass through unchanged"


def test_stdin_stdout_and_identity() -> str:
    body = (
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "print(json.dumps({'echo': data['x'], 'name': __name__, 'file': __file__,\n"
        "                  'argv': sys.argv, 'path0': sys.path[0]}))\n"
    )
    with Fixture() as fx:
        script = fx.script("echo.py", body)
        code, out, err = run([script, "extra1", "extra2"], stdin_text=json.dumps({"x": "hello"}))
        assert code == 0, f"exit {code}; stderr={err!r}"
        got = json.loads(out)
        assert got["echo"] == "hello", got
        assert got["name"] == "__main__", f"__name__ must be __main__, got {got['name']!r}"
        assert Path(got["file"]).resolve() == script.resolve(), got["file"]
        assert Path(got["argv"][0]).resolve() == script.resolve(), got["argv"]
        assert got["argv"][1:] == ["extra1", "extra2"], got["argv"]
        assert Path(got["path0"]).resolve() == fx.root.resolve(), (
            f"sys.path[0] must be the script's directory, got {got['path0']!r}"
        )
    return "stdin->stdout round-trips; __name__, __file__, argv and sys.path[0] match a direct run"


def test_sibling_import_resolves() -> str:
    with Fixture() as fx:
        fx.script("_sibling_helper.py", "VALUE = 'from-sibling'\n")
        script = fx.script("uses_sibling.py", "import _sibling_helper\nprint(_sibling_helper.VALUE)\n")
        code, out, err = run([script])
    assert code == 0 and out.strip() == "from-sibling", f"exit {code}, out={out!r}, err={err!r}"
    return "a sibling `import _helper` resolves through sys.path[0]"


def test_main_module_and_encoding() -> str:
    """sys.modules['__main__'] is the hook's own module while it runs, `__spec__` is None
    as for a direct script run, and a UTF-8 BOM plus non-ASCII source compile the way
    the interpreter would compile them."""
    body = (
        "import sys\n"
        "s = 'été'\n"
        "m = sys.modules['__main__']\n"
        "print(m.__file__ == __file__, __spec__ is None, len(s))\n"
    )
    with Fixture() as fx:
        script = fx.root / "bom.py"
        script.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        code, out, err = run([script])
    assert code == 0, f"exit {code}; stderr={err!r}"
    assert out.split() == ["True", "True", "3"], f"got {out!r}"
    return "__main__ is the hook's module, __spec__ is None, BOM + UTF-8 source compile"


def test_launcher_overhead_is_small() -> str:
    """Informational: the launcher runs the hook in-process (no second interpreter).
    The bound is generous -- it guards against an accidental subprocess re-launch,
    which would roughly double the cost, not against timing noise."""
    with Fixture() as fx:
        script = fx.script("noop.py", "pass\n")
        t0 = time.perf_counter()
        for _ in range(5):
            subprocess.run(PY + [str(script)], capture_output=True, timeout=60)
        direct = (time.perf_counter() - t0) / 5
        t0 = time.perf_counter()
        for _ in range(5):
            run([script])
        launched = (time.perf_counter() - t0) / 5
    # Measured 2026-10-10: direct 55 ms, launched 57 ms. A runpy-based launcher measured
    # 99 ms (it pulls in pkgutil, and json eagerly) -- the bound sits between the two.
    assert launched < direct * 1.5 + 0.03, (
        f"launcher run {launched * 1000:.0f} ms vs direct {direct * 1000:.0f} ms -- "
        "is it spawning a second interpreter?"
    )
    return f"direct {direct * 1000:.0f} ms, via launcher {launched * 1000:.0f} ms (avg of 5)"


def test_real_pyw_round_trip() -> str:
    pyw = shutil.which("pyw")
    if not pyw:
        return "SKIPPED (case only): pyw not on PATH -- the py -3 cases above still ran"
    with Fixture() as fx:
        script = fx.script("pyw_echo.py", "import sys\nsys.stdout.write(sys.stdin.read())\nsys.exit(2)\n")
        code, out, _ = run([script], stdin_text="ping", interpreter=[pyw, "-3"])
        assert code == 2 and out == "ping", f"pyw -3 via launcher: exit {code}, out={out!r}"
        code, out, _ = run([fx.root / "gone.py"], interpreter=[pyw, "-3"])
        assert code == 0, f"pyw -3 missing script via launcher must exit 0, got {code}"
        _system_message(out)
    return "pyw -3 <launcher> passes stdio + exit 2 through, and fails open on a missing script"


# --- 3. stdlib only, and 4. gate list parity -----------------------------------------


def test_launcher_imports_only_stdlib() -> str:
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    stdlib = getattr(sys, "stdlib_module_names", None)
    assert names, "no imports found -- the AST scan is broken (a vacuous pass)"
    non_stdlib = sorted(n for n in names if (stdlib is not None and n not in stdlib) or n.startswith("_hook"))
    local = sorted(n for n in names if (SCRIPTS_DIR / f"{n}.py").exists())
    assert not non_stdlib and not local, (
        f"the launcher must import only the standard library; found {non_stdlib or local}"
    )
    return f"imports only stdlib: {sorted(names)}"


def test_gate_scripts_match_fail_closed_set() -> str:
    import test_hook_safe_exit_guard as guard  # tests/ is on sys.path (see the top of this file)

    assert set(_hook_launch.GATE_SCRIPTS) == set(guard.FAIL_CLOSED), (
        f"_hook_launch.GATE_SCRIPTS {sorted(_hook_launch.GATE_SCRIPTS)} != "
        f"test_hook_safe_exit_guard.FAIL_CLOSED {sorted(guard.FAIL_CLOSED)}"
    )
    return f"GATE_SCRIPTS == FAIL_CLOSED ({sorted(guard.FAIL_CLOSED)})"


def main() -> int:
    tests = [
        ("missing script -> exit 0 + systemMessage", test_missing_script_exits_zero_with_system_message),
        ("missing gate -> still fails open, loud warning", test_missing_gate_fails_open_with_loud_warning),
        ("no argument -> exit 0", test_no_argument_exits_zero),
        ("directory -> treated as missing", test_directory_is_treated_as_missing),
        ("known-bad: bare python on a missing file exits 2", test_known_bad_direct_invocation_blocks),
        ("exit codes + stderr pass through", test_exit_codes_pass_through),
        ("stdin/stdout + __name__/__file__/argv/path0", test_stdin_stdout_and_identity),
        ("sibling import resolves", test_sibling_import_resolves),
        ("__main__ module, __spec__, BOM + UTF-8 source", test_main_module_and_encoding),
        ("launcher overhead is small (in-process)", test_launcher_overhead_is_small),
        ("real pyw -3 round trip", test_real_pyw_round_trip),
        ("launcher imports only stdlib", test_launcher_imports_only_stdlib),
        ("GATE_SCRIPTS == FAIL_CLOSED", test_gate_scripts_match_fail_closed_set),
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
