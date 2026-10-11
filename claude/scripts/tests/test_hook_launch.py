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
import os
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


def run(args, stdin_text="", interpreter=None, state_dir=None):
    """Run the launcher with `args`; return (returncode, stdout, stderr) as text.

    The warning throttle's state always goes to a temp directory (a fresh one unless
    `state_dir` is given), so no case touches the real ~/.claude/scratch and no case's
    throttle state leaks into another's."""
    cmd = (interpreter or PY) + [str(LAUNCHER)] + [str(a) for a in args]
    env = dict(os.environ)
    fresh = None
    if state_dir is None:
        fresh = tempfile.mkdtemp()
        state_dir = fresh
    env["HOOK_LAUNCH_STATE_DIR"] = str(state_dir)
    try:
        proc = subprocess.run(
            cmd, input=stdin_text.encode("utf-8"), capture_output=True, timeout=60, env=env
        )
    finally:
        if fresh:
            shutil.rmtree(fresh, ignore_errors=True)
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
    assert "checkout main;" in msg and " pull" in msg, "message must name the recovery commands"
    # The recovery terminal may be Windows PowerShell 5.1: no `&&`, and no `~` (neither
    # git nor PowerShell expands it for a native program).
    assert "&&" not in msg and "git -C ~" not in msg, f"recovery must be PowerShell-safe: {msg}"
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
        assert os.path.isabs(got["file"]), f"__file__ must be absolute, as in a direct run: {got['file']!r}"
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


def test_launcher_overhead_report() -> str:
    """Informational only -- never fails. Timing on a shared CI runner is too noisy to
    gate on (a single stall breaks any bound), and a bound loose enough to be stable
    also let a runpy-based launcher through. The cost guarantee is structural instead:
    test_launcher_avoids_costly_imports below. Runs are interleaved and the median is
    reported, so the printed numbers are at least comparable."""
    direct, launched = [], []
    with Fixture() as fx:
        script = fx.script("noop.py", "pass\n")
        for _ in range(5):
            t0 = time.perf_counter()
            subprocess.run(PY + [str(script)], capture_output=True, timeout=60)
            direct.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            run([script])
            launched.append(time.perf_counter() - t0)
    med = lambda xs: sorted(xs)[len(xs) // 2]  # noqa: E731
    return (
        f"informational: direct {med(direct) * 1000:.0f} ms, via launcher "
        f"{med(launched) * 1000:.0f} ms (median of 5, interleaved)"
    )


# Imports and calls that would make the launcher slow (a second interpreter, runpy's
# pkgutil) or that it must not need at module level (json only on the missing path).
_FORBIDDEN_MODULES = {"subprocess", "runpy", "multiprocessing", "pkgutil", "importlib"}
_FORBIDDEN_OS_CALL_PREFIXES = ("exec", "spawn", "system", "popen", "startfile")


def test_launcher_avoids_costly_imports() -> str:
    """The structural cost guarantee: no second interpreter, no runpy, and only the
    already-loaded `builtins`/`os`/`sys` at module level (json/time are imported on
    the missing-script path alone). Measured: a runpy draft added about 44 ms per hook,
    the compile + exec form about 2 ms."""
    # Known-bad references first, so the scan is proven able to fail (ADR-144).
    for snippet in ("import runpy\n", "import subprocess\n", "import os\nos.execv('x', [])\n", "import json\n"):
        bad_found, top = _cost_violations(snippet)
        assert bad_found or not set(top) <= {"builtins", "os", "sys"}, f"known-bad not caught: {snippet!r}"
    bad, top_level = _cost_violations(LAUNCHER.read_text(encoding="utf-8"))
    assert not bad, f"the launcher must not spawn processes or use runpy/importlib: {bad}"
    assert set(top_level) <= {"builtins", "os", "sys"}, (
        f"module-level imports must stay builtins/os/sys (already loaded); found {top_level}"
    )
    return f"module-level imports {top_level}; no subprocess/runpy/exec/spawn (4 known-bad caught)"


def _cost_violations(source: str):
    """(forbidden imports/calls anywhere, module-level import names) for `source`."""
    tree = ast.parse(source)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in _FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in _FORBIDDEN_MODULES:
            bad.append(node.module)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr.startswith(_FORBIDDEN_OS_CALL_PREFIXES)
        ):
            bad.append(f"os.{node.attr}")
    top_level = sorted(
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    ) + sorted(node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom))
    return bad, top_level


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


def test_missing_warning_is_throttled() -> str:
    """A stale tree can leave a dozen hooks missing, each firing on every tool call.
    A non-gate script is announced once per THROTTLE_SECONDS; a gate every time, so its
    NOT-enforcing warning cannot be buried."""
    state = Path(tempfile.mkdtemp())
    try:
        with Fixture() as fx:
            code1, out1, _ = run([fx.root / "plain-hook.py"], state_dir=state)
            code2, out2, _ = run([fx.root / "plain-hook.py"], state_dir=state)
            code3, out3, _ = run([fx.root / "other-hook.py"], state_dir=state)
            gate = sorted(_hook_launch.GATE_SCRIPTS)[0]
            g1 = run([fx.root / gate], state_dir=state)
            g2 = run([fx.root / gate], state_dir=state)
        assert (code1, code2, code3) == (0, 0, 0), "every missing-script run exits 0"
        _system_message(out1)
        assert out2.strip() == "", f"second announcement within the window must be silent: {out2!r}"
        _system_message(out3)  # a different script has its own window
        assert g1[0] == g2[0] == 0
        assert "NOT enforcing" in _system_message(g1[1]) and "NOT enforcing" in _system_message(g2[1]), (
            "a missing gate is announced on every run"
        )
        assert (state / "plain-hook.py.ts").is_file(), "the throttle state lands in the override dir"
    finally:
        shutil.rmtree(state, ignore_errors=True)
    return "repeat silent within the window, other scripts independent, gates always announced"


def test_throttle_fails_open() -> str:
    """A state dir that cannot be created (a file sits at its path) must still announce
    every time -- a silenced warning is the worse failure."""
    with Fixture() as fx:
        blocker = fx.root / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        outs = [run([fx.root / "plain-hook.py"], state_dir=blocker / "sub") for _ in range(2)]
    for code, out, _ in outs:
        assert code == 0
        _system_message(out)
    return "unwritable state dir -> announced on every run, still exit 0"


def test_recovery_names_canonical_repo() -> str:
    """The recovery command names the checkout the scripts dir resolves to, as an
    absolute forward-slash path (pasteable into Git Bash or PowerShell)."""
    with Fixture() as fx:
        scripts = fx.root / "claude" / "scripts"
        scripts.mkdir(parents=True)
        repo = _hook_launch.canonical_repo(str(scripts / "gone.py"))
        expect = os.path.realpath(str(fx.root)).replace("\\", "/")
        assert repo.lower() == expect.lower(), f"{repo!r} != {expect!r}"
        other = _hook_launch.canonical_repo(str(fx.root / "elsewhere" / "gone.py"))
        assert other.replace("\\", "/").endswith("/Git/dev-env") and "~" not in other, other
    return "repo = realpath(scripts dir)/../..; fallback is the expanded ~/Git/dev-env"


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
        ("launcher overhead (informational)", test_launcher_overhead_report),
        ("launcher avoids costly imports (structural)", test_launcher_avoids_costly_imports),
        ("missing-script warning is throttled; gates never", test_missing_warning_is_throttled),
        ("throttle fails open on unusable state dir", test_throttle_fails_open),
        ("recovery names the canonical repo, absolute", test_recovery_names_canonical_repo),
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
