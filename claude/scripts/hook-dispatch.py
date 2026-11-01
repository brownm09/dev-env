#!/usr/bin/env python3
"""Run several hook scripts in one process instead of one process each (dev-env#851).

Wired in place of a group of hooks that share an event and matcher:

    pyw -3 C:/Users/brown/.claude/hook-launch.py C:/Users/brown/.claude/scripts/hook-dispatch.py \\
        C:/Users/brown/.claude/scripts/a.py C:/Users/brown/.claude/scripts/b.py ...

On this machine every process start costs 0.5-4.3 s under Defender and Acronis, and
the 10 PostToolUse(Bash) hooks spent 4.3 s starting for 0.3 s of work per Bash call
(the dev-env#849 baseline on #851). This runs them all in the one process the launcher
already started.

Each handler keeps what it had as its own process:

- **Its code path.** It is executed as ``__main__`` from its own file, so its
  ``if __name__ == "__main__":`` block runs, including its own crash guard. It reads
  the same stdin payload. Its module globals are its own.
- **Concurrency and its time budget.** Claude Code runs an event's hooks in parallel,
  so each handler runs in its own thread. Its output counts only if it finished within
  the timeout it had as a hook command (``BUDGET_SECONDS``), counted from the dispatch
  start, as Claude Code's own timeout counted from its spawn. A handler that missed its
  budget is dropped with a warning, as a killed hook got a timeout notice, and if one
  is still running the process ends with ``os._exit`` rather than wait for it.
- **Its streams.** ``sys.stdin``/``stdout``/``stderr`` are routed per thread to the
  handler's own buffers, and stay routed until the process exits, so a late handler
  never writes into the result. A child process inherits the real standard handles, as
  it did when the handler was its own hook.
- **Its heartbeat and duration.** Its own ``record_heartbeat`` call still writes its
  liveness file. Its exit-time duration record does not arm, because ``__main__`` in
  ``sys.modules`` stays this dispatcher; this records each kept handler's run time
  instead (ADR-106 Amendment 2). That run time now spans compiling the handler and its
  imports too, with no separate startup sample.
- **Its fail direction.** Every handler wired through this today is advisory and fails
  open, and so does the dispatcher: a missing handler file is skipped with the
  launcher's own throttled warning (ADR-148), and anything unexpected here exits 0.

The handlers' results are combined into the one output Claude Code reads:

- only blocks (exit 2 with stderr, or exit-0 JSON ``decision: "block"``): the texts
  joined, on stderr, exit 2;
- only ``systemMessage``s and warnings: one joined ``systemMessage``, exit 0;
- both: a PostToolUse ``{"decision": "block", "reason": ..., "systemMessage": ...}``,
  exit 0, because exit 2 would drop the user's messages. Per the Claude Code hooks
  reference, ``decision: "block"`` prompts Claude with ``reason``, as exit-2 stderr does;
- neither: nothing, exit 0.

A handler's exit 1 (an error its own guard missed) and any JSON key this cannot carry
through become warnings instead of vanishing. See ADR-151.
"""

import builtins
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import _hook_launch
import _hookout
import _hookutil

DISPATCH_NAME = "hook-dispatch"

# The timeout each handler had as its own hook command. Unlisted handlers get the
# default. The wiring lint checks each is at least that handler's floor, and that the
# dispatcher's own settings timeout clears the largest by a margin for process start.
DEFAULT_BUDGET_SECONDS = 30
BUDGET_SECONDS = {
    "usage-snapshot": 90,
    "post-merge-tile-checkpoint": 10,
    "journal-shard-write-advisory": 10,
}

# The JSON keys a handler's exit-0 output may carry; anything else is reported, not lost silently.
_CARRIED_KEYS = {"systemMessage", "decision", "reason"}


class _Routed:
    """Stands in for sys.stdin/stdout/stderr, sending each thread to its own stream."""

    def __init__(self, default):
        self._default = default
        self._local = threading.local()

    def bind(self, stream) -> None:
        self._local.stream = stream

    def __getattr__(self, name):
        return getattr(getattr(self._local, "stream", self._default), name)

    def __iter__(self):
        return iter(getattr(self._local, "stream", self._default))


class HandlerRun:
    """One handler's outcome; ``kept`` is set once, when the dispatcher stops waiting."""

    def __init__(self, path: Path):
        self.path = path
        self.name = path.stem
        self.budget = budget_for(self.name)
        self.code = 0
        self.stdout = ""
        self.stderr = ""
        self.run_ms = 0
        self.ended = None
        self.kept = False


def budget_for(name: str) -> int:
    return BUDGET_SECONDS.get(name, DEFAULT_BUDGET_SECONDS)


def exit_code(code) -> int:
    """The process exit code Python would give ``sys.exit(code)``; a message string is 1."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def run_handler(run: HandlerRun, payload: str, routes) -> None:
    """Execute *run*'s script as ``__main__`` on this thread, capturing its streams."""
    stdin_route, stdout_route, stderr_route = routes
    out, err = io.StringIO(), io.StringIO()
    stdin_route.bind(io.StringIO(payload))
    stdout_route.bind(out)
    stderr_route.bind(err)
    start = time.monotonic()
    code = 0
    try:
        source = run.path.read_bytes()
        module = type(sys)("__main__")  # not registered in sys.modules, see the module docstring
        module.__dict__.update(
            __file__=str(run.path), __builtins__=builtins, __spec__=None,
            __loader__=None, __package__=None, __cached__=None,
        )
        exec(compile(source, str(run.path), "exec"), module.__dict__)  # noqa: S102 -- running the hook IS the job
    except SystemExit as exc:
        code = exit_code(exc.code)
    except BaseException as exc:  # noqa: BLE001 -- an escaped error is this handler's exit 1, never the dispatcher's
        code = 1
        err.write(f"{type(exc).__name__}: {exc}\n")
    run.code, run.stdout, run.stderr = code, out.getvalue(), err.getvalue()
    run.run_ms = int((time.monotonic() - start) * 1000)
    run.ended = time.monotonic()


def handler_paths(argv: list, own: Path) -> tuple:
    """The handler files to run, and warnings for the ones skipped.

    Skips this dispatcher and the launcher (a misconfigured command must not recurse)
    and repeats (a handler must not run twice at once). A missing file gets the
    launcher's own warning, throttled the same way."""
    paths, warnings, seen = [], [], set()
    for arg in argv:
        path = Path(arg)
        key = path.name.lower()
        if key in seen or key in (own.name.lower(), "hook-launch.py", "_hook_launch.py"):
            continue
        seen.add(key)
        if path.is_file():
            paths.append(path)
        elif _hook_launch.should_announce(path.name):
            warnings.append(_hook_launch.missing_message(str(path)))
    return paths, warnings


def read_output(run: HandlerRun) -> tuple:
    """``(block_text, system_message, warning)`` from a kept handler's own output."""
    if run.code == 2:
        return run.stderr.strip() or None, None, None
    if run.code != 0:
        last = run.stderr.strip().splitlines()[-1:] or [f"exit {run.code}"]
        return None, None, f"[hook-dispatch] {run.name} failed (exit {run.code}): {last[0]}"
    try:
        data = json.loads(run.stdout)
    except ValueError:
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    block = data.get("reason") if data.get("decision") == "block" else None
    message = data.get("systemMessage")
    dropped = sorted(set(data) - _CARRIED_KEYS)
    warning = f"[hook-dispatch] {run.name} output keys not carried: {', '.join(dropped)}" if dropped else None
    return (
        block if isinstance(block, str) and block else None,
        message if isinstance(message, str) and message else None,
        warning,
    )


def combine(runs: list, warnings: list) -> tuple:
    """Return ``(stdout_text, stderr_text, exit_code)`` for the kept *runs*."""
    blocks, messages = [], list(warnings)
    for run in runs:
        if not run.kept:
            messages.append(
                f"[hook-dispatch] {run.name} did not finish within its {run.budget}s budget; "
                "its output was dropped."
            )
            continue
        block, message, warning = read_output(run)
        blocks += [block] if block else []
        messages += [m for m in (message, warning) if m]
    reason = "\n\n".join(blocks)
    shown = "\n\n".join(messages)
    if blocks and messages:
        body = {"decision": "block", "reason": _hookout.ascii_sanitize(reason), "systemMessage": shown}
        return json.dumps(body, ensure_ascii=True) + "\n", "", 0
    if blocks:
        return "", _hookout.ascii_sanitize(reason) + "\n", 2
    if messages:
        return json.dumps({"systemMessage": shown}, ensure_ascii=True) + "\n", "", 0
    return "", "", 0


def main() -> None:
    _hookutil.record_heartbeat("hook-dispatch")
    payload = sys.stdin.read()
    paths, warnings = handler_paths(sys.argv[1:], Path(__file__))
    runs = [HandlerRun(path) for path in paths]

    real_out, real_err = sys.__stdout__, sys.__stderr__
    routes = (_Routed(sys.stdin), _Routed(sys.stdout), _Routed(sys.stderr))
    sys.stdin, sys.stdout, sys.stderr = routes  # left in place: a late handler must not write into the result
    start = time.monotonic()
    threads = []
    for run in runs:
        thread = threading.Thread(target=run_handler, args=(run, payload, routes), daemon=True)
        thread.start()
        threads.append(thread)
    for run, thread in zip(runs, threads):
        thread.join(max(0.0, start + run.budget - time.monotonic()))
    for run in runs:  # one decision per handler, read once
        run.kept = run.ended is not None and run.ended - start <= run.budget

    for run in runs:
        if run.kept:
            _hookutil.record_duration(run.name, run.run_ms)
    out_text, err_text, code = combine(runs, warnings)
    if out_text:
        real_out.write(out_text)
        real_out.flush()
    if err_text:
        real_err.write(err_text)
        real_err.flush()
    if any(thread.is_alive() for thread in threads):
        # A handler outlived its budget: don't wait on its thread. os._exit skips the
        # dispatcher's exit-time duration record, so record it here.
        _hookutil.record_duration(DISPATCH_NAME, int((time.monotonic() - start) * 1000))
        os._exit(code)
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
