#!/usr/bin/env python3
"""Tests for hook-dispatch.py, the in-process hook dispatcher (dev-env#851 stage 1, ADR-151).

Two layers:
- ``combine()`` and ``exit_code()`` offline: how the handlers' exit codes and streams
  become the one output Claude Code reads.
- End to end, as a subprocess through the real launcher, with fake handler scripts in
  a temp directory: stdin reaches every handler, blocks and toasts combine, an uncaught
  error and a missing script fail open, a child process writing to the inherited stdout
  cannot corrupt the result, each handler gets exactly one duration line, and a handler
  past its budget is dropped without holding the result back.

The real PostToolUse handlers are not fired here: on a merge-shaped payload they act on
GitHub and the canonical. Their own suites cover them; the dispatcher only runs them.

Usage:
    py -3 claude/scripts/tests/test_hook_dispatch.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _heartbeat_isolation import ENV_VAR, isolate_heartbeat  # noqa: E402

isolate_heartbeat()
HEARTBEAT_DIR = os.environ[ENV_VAR]
DISPATCH = SCRIPTS / "hook-dispatch.py"
LAUNCHER = SCRIPTS / "_hook_launch.py"

_spec = importlib.util.spec_from_file_location("hook_dispatch", DISPATCH)
hd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hd)

PAYLOAD = json.dumps({"session_id": "t", "tool_name": "Bash", "tool_input": {"command": "git status"}})

HANDLERS = {
    "echo-tool.py": (
        "import json, sys\n"
        "data = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'systemMessage': 'saw ' + data['tool_name']}))\n"
    ),
    "blocker.py": "import sys\nsys.stderr.write('[blocker] stop and look\\n')\nsys.exit(2)\n",
    "crasher.py": "raise RuntimeError('escaped its guard')\n",
    "child-writer.py": (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', \"print('child noise on the real stdout')\"])\n"
    ),
    "beats.py": (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import _hookutil\n"
        "def main():\n"
        "    _hookutil.record_heartbeat('beats')\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
}


class _Run:
    def __init__(self, name, code, stdout="", stderr="", done=True):
        self.name, self.code, self.stdout, self.stderr, self.done = name, code, stdout, stderr, done


def _fire(handler_paths, budgets=None):
    """Run the dispatcher through the launcher; with *budgets*, via a wrapper that sets them."""
    env = dict(os.environ)
    if budgets is None:
        args = [sys.executable, str(LAUNCHER), str(DISPATCH)] + [str(p) for p in handler_paths]
    else:
        wrapper = (
            "import importlib.util, sys\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            f"spec = importlib.util.spec_from_file_location('hook_dispatch', {str(DISPATCH)!r})\n"
            "hd = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(hd)\n"
            f"hd.BUDGET_SECONDS.update({budgets!r})\n"
            f"sys.argv = [{str(DISPATCH)!r}] + {[str(p) for p in handler_paths]!r}\n"
            "hd.main()\n"
        )
        args = [sys.executable, "-c", wrapper]
    started = time.monotonic()
    proc = subprocess.run(args, input=PAYLOAD, capture_output=True, text=True, env=env, timeout=120)
    return proc, time.monotonic() - started


def _handlers(tmp, names):
    paths = []
    for name in names:
        path = Path(tmp) / name
        path.write_text(HANDLERS[name], encoding="utf-8")
        paths.append(path)
    return paths


def test_exit_code_mapping() -> str:
    assert hd.exit_code(None) == 0 and hd.exit_code(2) == 2 and hd.exit_code("message") == 1
    return "sys.exit(None) is 0, an int is itself, a message string is 1"


def test_combine_blocks_only() -> str:
    out, err, code = hd.combine([_Run("a", 2, stderr="[a] one\n"), _Run("b", 2, stderr="[b] two")], [])
    assert (out, code) == ("", 2), (out, code)
    assert err == "[a] one\n\n[b] two\n", repr(err)
    return "exit-2 stderr texts join on stderr with exit 2, as each would have been"


def test_combine_messages_only() -> str:
    runs = [_Run("a", 0, stdout=json.dumps({"systemMessage": "pulled"})), _Run("b", 0, stdout="")]
    out, err, code = hd.combine(runs, ["[hook-dispatch] WARNING: missing x.py"])
    assert (err, code) == ("", 0)
    assert json.loads(out) == {"systemMessage": "[hook-dispatch] WARNING: missing x.py\n\npulled"}, out
    return "systemMessage JSON and dispatch warnings join into one systemMessage, exit 0"


def test_combine_both_uses_decision_block() -> str:
    runs = [_Run("a", 2, stderr="[a] tile now"), _Run("b", 0, stdout=json.dumps({"systemMessage": "pulled"}))]
    out, err, code = hd.combine(runs, [])
    assert (err, code) == ("", 0), (err, code)
    body = json.loads(out)
    assert body == {"decision": "block", "reason": "[a] tile now", "systemMessage": "pulled"}, body
    return "a block plus a toast becomes decision:block with reason and systemMessage, so neither is lost"


def test_combine_ignores_exit_1_and_dropped() -> str:
    runs = [_Run("a", 1, stderr="Traceback ..."), _Run("b", 2, stderr="[b] late", done=False)]
    assert hd.combine(runs, []) == ("", "", 0)
    return "an exit-1 handler and one dropped at its budget contribute nothing"


def test_combine_sanitizes_non_ascii() -> str:
    out, err, code = hd.combine([_Run("a", 2, stderr="[a] one — two")], [])
    assert code == 2 and err.isascii(), repr(err)
    out, err, code = hd.combine([_Run("a", 0, stdout=json.dumps({"systemMessage": "café"}))], [])
    assert out.isascii() and json.loads(out)["systemMessage"] == "café", out
    return "stderr is ASCII-sanitized and JSON is ASCII-escaped, both lossless for the reader"


def test_end_to_end_combines_and_fails_open() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        paths = _handlers(tmp, ["echo-tool.py", "blocker.py", "crasher.py", "child-writer.py"])
        paths.append(Path(tmp) / "not-there.py")
        proc, _ = _fire(paths)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    body = json.loads(proc.stdout)  # one JSON object: the child's noise did not reach stdout
    assert body["decision"] == "block" and body["reason"] == "[blocker] stop and look", body
    assert "saw Bash" in body["systemMessage"], body
    assert "not-there.py" in body["systemMessage"], body
    assert proc.stderr == "", repr(proc.stderr)
    return "stdin reached each handler, block and toast combined, crash and missing script failed open, child output contained"


def test_end_to_end_one_duration_line_per_handler() -> str:
    hb = Path(HEARTBEAT_DIR)
    before = (hb / "beats.dur.jsonl").read_text(encoding="utf-8").count("\n") if (hb / "beats.dur.jsonl").exists() else 0
    with tempfile.TemporaryDirectory() as tmp:
        proc, _ = _fire(_handlers(tmp, ["beats.py"]))
    assert proc.returncode == 0 and proc.stdout == "", (proc.returncode, proc.stdout, proc.stderr)
    assert (hb / "beats.ts").exists(), "the handler's own heartbeat must still be written"
    after = (hb / "beats.dur.jsonl").read_text(encoding="utf-8").count("\n")
    assert after - before == 1, f"expected one duration line, got {after - before}"
    return "the handler's heartbeat is written and exactly one duration line recorded (no exit-time double)"


def test_end_to_end_budget_drops_slow_handler() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        slow = Path(tmp) / "slow.py"
        slow.write_text(
            "import sys, time\ntime.sleep(20)\nsys.stderr.write('[slow] too late\\n')\nsys.exit(2)\n",
            encoding="utf-8",
        )
        paths = _handlers(tmp, ["echo-tool.py"]) + [slow]
        proc, elapsed = _fire(paths, budgets={"slow": 1})
    assert elapsed < 15, f"dispatcher waited {elapsed:.1f}s on a handler with a 1s budget"
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert json.loads(proc.stdout) == {"systemMessage": "saw Bash"}, proc.stdout
    return "a handler past its budget is dropped, the rest still report, and the process doesn't wait for it"


def main() -> int:
    tests = [
        ("exit_code mapping", test_exit_code_mapping),
        ("combine: blocks only", test_combine_blocks_only),
        ("combine: messages only", test_combine_messages_only),
        ("combine: block plus toast", test_combine_both_uses_decision_block),
        ("combine: exit 1 and dropped ignored", test_combine_ignores_exit_1_and_dropped),
        ("combine: ASCII-safe output", test_combine_sanitizes_non_ascii),
        ("end to end: combine and fail open", test_end_to_end_combines_and_fails_open),
        ("end to end: one duration line per handler", test_end_to_end_one_duration_line_per_handler),
        ("end to end: budget drops a slow handler", test_end_to_end_budget_drops_slow_handler),
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
