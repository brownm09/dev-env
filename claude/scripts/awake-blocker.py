"""Block Windows system sleep while Claude is processing.

Invoked from settings.json hooks:
  - UserPromptSubmit: start (idempotent — spawns watcher if not running, refreshes heartbeat)
  - Stop / Notification: stop (removes sentinel, watcher exits within 1s)

Mechanism: a detached background watcher process calls
SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) in a loop and exits
when the sentinel file is missing or its heartbeat is older than HEARTBEAT_MAX_AGE.
Exit clears the execution-state flag automatically.

Safe-exit guard: any exception in hook mode exits 0 so a bug never blocks a prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRATCH = Path("C:/Users/brown/.claude/scratch")
SENTINEL = SCRATCH / "awake.lock"
PID_FILE = SCRATCH / "awake.pid"
LOG_FILE = SCRATCH / "awake.log"

# Heartbeat must be refreshed at least this often. UserPromptSubmit refreshes it on every prompt;
# if the session crashes or is killed, the watcher self-terminates after this many seconds.
HEARTBEAT_MAX_AGE = 30 * 60  # 30 minutes
WATCHER_POLL_INTERVAL = 1.0   # seconds between sentinel checks
EXEC_STATE_REFRESH = 30.0     # seconds between SetThreadExecutionState calls

# SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _log(msg: str) -> None:
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} pid={os.getpid()} {msg}\n")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # Windows: use tasklist via subprocess (no extra deps)
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in out.stdout
    except Exception:
        return False


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    os.utime(path, None)


def start() -> None:
    """Ensure a watcher is running and refresh the heartbeat."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    _touch(SENTINEL)

    # Is a watcher already alive?
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                return
        except Exception:
            pass

    # Spawn detached watcher. DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP keeps it
    # alive past the hook's exit and disconnects it from the parent's console.
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        ["py", "-3", str(Path(__file__).resolve()), "--watcher"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    try:
        PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except Exception:
        pass
    _log(f"spawned watcher pid={proc.pid}")


def stop() -> None:
    """Remove the sentinel; the watcher will exit on its next poll."""
    try:
        SENTINEL.unlink(missing_ok=True)
    except Exception:
        pass


def watcher() -> None:
    """Background loop: hold the wake-lock while sentinel is fresh."""
    import ctypes
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint

    _log("watcher start")
    last_refresh = 0.0
    try:
        while True:
            if not SENTINEL.exists():
                _log("watcher exit: sentinel missing")
                break
            try:
                age = time.time() - SENTINEL.stat().st_mtime
            except FileNotFoundError:
                _log("watcher exit: sentinel removed")
                break
            if age > HEARTBEAT_MAX_AGE:
                _log(f"watcher exit: heartbeat stale ({age:.0f}s)")
                break

            now = time.time()
            if now - last_refresh >= EXEC_STATE_REFRESH:
                kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                last_refresh = now

            time.sleep(WATCHER_POLL_INTERVAL)
    finally:
        # Clear the flag explicitly (also auto-clears on process exit).
        try:
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        _log("watcher done")


def _hook_event() -> str:
    # Prefer stdin JSON (Claude Code's hook contract); fall back to env var.
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            ev = data.get("hook_event_name") or data.get("event") or ""
            if ev:
                return ev
    except Exception:
        pass
    return os.environ.get("CLAUDE_HOOK_EVENT_NAME", "")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--watcher":
        watcher()
        return 0

    try:
        ev = _hook_event()
        if ev == "UserPromptSubmit":
            start()
        elif ev in ("Stop", "Notification"):
            stop()
        # Unknown event: no-op.
    except Exception as exc:
        _log(f"hook error: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
