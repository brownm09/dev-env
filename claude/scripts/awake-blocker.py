"""Block Windows system sleep while Claude is processing.

Invoked from settings.json hooks:
  - UserPromptSubmit: start (idempotent — spawns watcher if not running, refreshes heartbeat)
  - Stop / Notification: stop (removes sentinel, watcher exits within 1s)

Mechanism: a detached background watcher process calls
SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) in a loop and exits
when the sentinel file is missing or its heartbeat is older than HEARTBEAT_MAX_AGE.
Exit clears the execution-state flag automatically.

Platform: Windows only. On non-Windows the hook is a silent no-op (no log line,
no Popen, no error).

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
LOG_FILE_ROTATED = SCRATCH / "awake.log.1"

# Heartbeat must be refreshed at least this often. UserPromptSubmit refreshes it on every prompt;
# if the session crashes or is killed, the watcher self-terminates after this many seconds.
HEARTBEAT_MAX_AGE = 30 * 60  # 30 minutes
WATCHER_POLL_INTERVAL = 1.0   # seconds between sentinel checks
EXEC_STATE_REFRESH = 30.0     # seconds between SetThreadExecutionState calls

# Log rotation: when LOG_FILE exceeds this many bytes, rotate to LOG_FILE_ROTATED.
LOG_MAX_BYTES = 256 * 1024  # 256 KiB

# Watcher process image name — used together with PID to defeat PID-reuse false positives.
WATCHER_IMAGE = "py.exe"

# SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

# Notification subtypes for which we should release the wake-lock. Other subtypes
# (status surfaces, transient banners) leave the lock held so the machine does not
# sleep mid-turn. If Claude Code's payload omits the subtype field entirely, fall
# back to releasing (preserves the original ADR-033 behavior).
NOTIFICATION_IDLE_SUBTYPES = {
    "permission",          # waiting on user to approve a tool call
    "permission_request",
    "waiting_for_input",
    "idle",
}


def _is_windows() -> bool:
    return sys.platform == "win32"


def _rotate_log_if_large() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            # Best-effort single-generation rotation; ignore replace errors.
            try:
                if LOG_FILE_ROTATED.exists():
                    LOG_FILE_ROTATED.unlink()
            except Exception:
                pass
            try:
                LOG_FILE.replace(LOG_FILE_ROTATED)
            except Exception:
                pass
    except Exception:
        pass


def _log(msg: str) -> None:
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_large()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} pid={os.getpid()} {msg}\n")
    except Exception:
        pass


def _pid_is_watcher(pid: int) -> bool:
    """Return True only if a process with this PID exists AND its image matches WATCHER_IMAGE.

    Filtering on image name defeats PID-reuse false positives: when Windows recycles
    a dead watcher's PID for an unrelated process, the PID-only check would
    incorrectly report "watcher alive" and start() would skip spawning, silently
    dropping sleep protection. tasklist's combined PID + IMAGENAME filter resolves it.
    """
    if pid <= 0:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FI", f"IMAGENAME eq {WATCHER_IMAGE}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        stdout = out.stdout or ""
        # When no process matches, tasklist prints "INFO: No tasks are running ...".
        # On a match, the row contains both the image name and the PID.
        if "No tasks" in stdout:
            return False
        return WATCHER_IMAGE.lower() in stdout.lower() and str(pid) in stdout
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
            if _pid_is_watcher(pid):
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


def _read_hook_payload() -> tuple[str, dict]:
    """Return (event_name, full_payload_dict). Empty event on parse failure."""
    try:
        raw = sys.stdin.read()
        if raw:
            data = json.loads(raw)
            ev = data.get("hook_event_name") or data.get("event") or ""
            return ev, data if isinstance(data, dict) else {}
    except Exception:
        pass
    return os.environ.get("CLAUDE_HOOK_EVENT_NAME", ""), {}


def _notification_should_release(payload: dict) -> bool:
    """Inspect Notification payload to decide whether to release the wake-lock.

    Claude Code passes additional notification-specific fields alongside
    hook_event_name. The exact schema varies; we look at the most plausible field
    names. If none are present, fall back to releasing (the original ADR-033
    decision — release on every Notification).
    """
    for field in ("notification_type", "type", "subtype", "kind"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value.lower() in NOTIFICATION_IDLE_SUBTYPES
    # No subtype information available — preserve original behavior.
    return True


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--watcher":
        # Watcher process: only run on Windows (ctypes.windll is Windows-only).
        if not _is_windows():
            return 0
        watcher()
        return 0

    # Hook entry-point. Short-circuit on non-Windows — the wake-lock mechanism
    # (SetThreadExecutionState, tasklist, DETACHED_PROCESS) is Windows-only, so
    # there is nothing useful to do elsewhere. Silent no-op (no log line, no Popen).
    if not _is_windows():
        return 0

    try:
        ev, payload = _read_hook_payload()
        if ev == "UserPromptSubmit":
            start()
        elif ev == "Stop":
            stop()
        elif ev == "Notification":
            if _notification_should_release(payload):
                stop()
        # Unknown event: no-op.
    except Exception as exc:
        _log(f"hook error: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
