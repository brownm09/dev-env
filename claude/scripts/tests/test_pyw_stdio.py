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

If any check fails, the `pyw -3` swap in `claude/settings.json` is not
safe and should be reverted.

Usage:
    py -3 claude/scripts/tests/test_pyw_stdio.py

The test runs under `py -3` (a console-attached parent); the
subprocess invocations under test are `pyw -3`. Exit 0 = all pass.
"""

import ast
import json
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


def test_every_subprocess_using_hook_imports_winsubp() -> str:
    """Every hook script in claude/scripts/ that uses subprocess must import _winsubp."""
    hooks_dir = REPO_ROOT / "claude" / "scripts"
    missing: list[str] = []
    checked = 0
    for path in sorted(hooks_dir.glob("*.py")):
        # Skip the helper itself and any underscore-prefixed support modules.
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess" not in text:
            continue
        # The grep used to build the patch list looked for subprocess.{run,Popen,...}
        # — anything matching that pattern must also import _winsubp.
        uses_subprocess = any(
            kw in text
            for kw in (
                "subprocess.run", "subprocess.Popen", "subprocess.check_output",
                "subprocess.check_call", "subprocess.call",
            )
        )
        if not uses_subprocess:
            continue
        checked += 1
        if "_winsubp" not in text:
            missing.append(path.name)
    if missing:
        raise AssertionError(
            f"{len(missing)} hook(s) use subprocess but do not import _winsubp: "
            + ", ".join(missing)
        )
    return f"all {checked} subprocess-using hook scripts import _winsubp"


def main() -> int:
    tests = [
        ("pyw -3 stdio wired when parent supplies pipes", test_pyw_stdio_wired_when_parent_supplies_pipes),
        ("pyw -3 runs Claude-Code-shaped hook end-to-end", test_pyw_runs_claude_code_shaped_hook_end_to_end),
        ("all settings.json hooks use pyw -3 and resolve", test_all_settings_hooks_use_pyw_and_resolve_to_repo),
        ("_winsubp patches subprocess under pyw -3", test_winsubp_patches_subprocess_under_pyw),
        ("every subprocess-using hook imports _winsubp", test_every_subprocess_using_hook_imports_winsubp),
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
