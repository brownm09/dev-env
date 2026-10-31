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
  so each handler runs in its own thread, and its output counts only if it finishes
  within the timeout it had as a hook command (``BUDGET_SECONDS``), measured from the
  dispatch start, as Claude Code's own timeout would. A handler still running then is
  dropped, as a killed hook would be, and the process ends with ``os._exit`` so its
  thread does not hold the result back.
- **Its streams.** ``sys.stdin``/``stdout``/``stderr`` are routed per thread to the
  handler's own buffers. File descriptors 1 and 2 point at the null device while
  handlers run, so a child process writing to an inherited stdout cannot corrupt the
  one result this process writes.
- **Its heartbeat and duration.** Its own ``record_heartbeat`` call still writes its
  liveness file. Its exit-time duration record does not arm, because ``__main__`` in
  ``sys.modules`` stays this dispatcher; this records each handler's run time instead
  (ADR-106 Amendment 2), and never for one dropped at its budget.
- **Its fail direction.** Every handler wired through this today is advisory and fails
  open, and so does the dispatcher: a missing handler file is skipped with a warning
  (as the launcher does, ADR-148), and anything unexpected here exits 0.

The handlers' results are combined into the one output Claude Code reads:

- only exit-2 stderr: those texts joined, on stderr, exit 2 (as each would have been);
- only ``systemMessage`` JSON: the messages joined, as one ``systemMessage``, exit 0;
- both: a PostToolUse ``{"decision": "block", "reason": ..., "systemMessage": ...}``,
  exit 0, because exit 2 would drop the user's messages. Per the Claude Code hooks
  reference, ``decision: "block"`` prompts Claude with ``reason``, as exit-2 stderr does;
- neither: nothing, exit 0.

Exit 1 (an uncaught error the handler's own guard missed) is ignored, as Claude Code
shows a non-blocking error only in the transcript view. See ADR-151.
"""

import builtins
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import _hookout
import _hookutil

# The timeout each handler had as its own hook command. Unlisted handlers get the
# default. The dispatcher's own timeout in settings must exceed the largest of these.
DEFAULT_BUDGET_SECONDS = 30
BUDGET_SECONDS = {
    "usage-snapshot": 90,
    "post-merge-tile-checkpoint": 10,
    "journal-shard-write-advisory": 10,
}


class _Routed:
    """Stands in for sys.stdin/stdout/stderr, sending each thread to its own stream."""

    def __init__(self, default):
        self._default = default
        self._local = threading.local()

    def bind(self, stream) -> None:
        self._local.stream = stream

    def _target(self):
        return getattr(self._local, "stream", self._default)

    def write(self, text):
        return self._target().write(text)

    def read(self, *args):
        return self._target().read(*args)

    def flush(self):
        return self._target().flush()

    def __getattr__(self, name):
        return getattr(self._target(), name)


class HandlerRun:
    """One handler's outcome; ``done`` stays False if it outlived its budget."""

    def __init__(self, path: Path):
        self.path = path
        self.name = path.stem
        self.budget = BUDGET_SECONDS.get(self.name, DEFAULT_BUDGET_SECONDS)
        self.code = 0
        self.stdout = ""
        self.stderr = ""
        self.run_ms = 0
        self.done = False


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
    except BaseException:  # noqa: BLE001 -- an escaped error is this handler's exit 1, never the dispatcher's
        code = 1
    run.run_ms = int((time.monotonic() - start) * 1000)
    run.code, run.stdout, run.stderr = code, out.getvalue(), err.getvalue()
    run.done = True


def system_message(stdout: str) -> str | None:
    """The ``systemMessage`` a handler printed as JSON on stdout, or None."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    message = data.get("systemMessage") if isinstance(data, dict) else None
    return message if isinstance(message, str) and message else None


def combine(runs: list, warnings: list) -> tuple:
    """Return ``(stdout_text, stderr_text, exit_code)`` for the finished *runs*."""
    blocks = [r.stderr.strip() for r in runs if r.done and r.code == 2 and r.stderr.strip()]
    messages = list(warnings)
    for r in runs:
        if r.done and r.code == 0:
            message = system_message(r.stdout)
            if message:
                messages.append(message)
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


def _silence_fds():
    """Point fds 1 and 2 at the null device; return the saved originals, or None."""
    try:
        saved = (os.dup(1), os.dup(2))
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.close(null)
        return saved
    except OSError:
        return None


def main() -> None:
    _hookutil.record_heartbeat("hook-dispatch")
    payload = sys.stdin.read()
    runs, warnings = [], []
    for arg in sys.argv[1:]:
        path = Path(arg)
        if path.is_file():
            runs.append(HandlerRun(path))
        else:
            warnings.append(f"[hook-dispatch] WARNING: hook script not found, skipped: {path.name} (ADR-148)")

    saved = _silence_fds()
    routes = (_Routed(sys.stdin), _Routed(sys.stdout), _Routed(sys.stderr))
    sys.stdin, sys.stdout, sys.stderr = routes
    start = time.monotonic()
    threads = []
    for run in runs:
        thread = threading.Thread(target=run_handler, args=(run, payload, routes), daemon=True)
        thread.start()
        threads.append(thread)
    for run, thread in zip(runs, threads):
        thread.join(max(0.0, start + run.budget - time.monotonic()))
    sys.stdin, sys.stdout, sys.stderr = sys.__stdin__, sys.__stdout__, sys.__stderr__

    for run in runs:
        if run.done:
            _hookutil.record_duration(run.name, run.run_ms)
    out_text, err_text, code = combine(runs, warnings)
    out_fd, err_fd = saved if saved else (1, 2)
    if out_text:
        os.write(out_fd, out_text.encode("ascii", "replace"))
    if err_text:
        os.write(err_fd, err_text.encode("ascii", "replace"))
    if not all(run.done for run in runs):
        os._exit(code)  # a handler outlived its budget; don't wait on its thread
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
