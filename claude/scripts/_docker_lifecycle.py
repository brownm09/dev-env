#!/usr/bin/env python3
"""Shared logic for starting Docker Desktop on demand and stopping it when
sessions are done. Used by pre-tool-use-docker-start.py and docker-watchdog.py.
See ADR-158 and dev-env#1278.

Purpose: Docker Desktop start/stop state (marker and leases, engine checks,
stale-socket repair, and the stop decision), shared by the Docker hook and watchdog.

Why Docker is never started from a session: a process in the Claude desktop
app's tree sees %LOCALAPPDATA% through the app's redirected view. In that view
an AF_UNIX socket file can be created but not opened or deleted (error 1920,
"The file cannot be accessed by the system"). Docker's backend deletes its old
socket at startup, so a backend started from a session crashes on the next
start. The same file deletes normally from a process Task Scheduler started.
Measured 2026-11-06 (ADR-158); the redirection mechanism itself isn't proven.

A check that fails reads as the state that destroys nothing (ADR-158): an engine
check that times out is "unknown" and the watchdog acts on nothing, a tasklist
that fails reads as running, and a container count that fails reads as one.

State lives in `_hookutil.SCRATCH/docker-lifecycle/`:
  started-by-session   Marker: a session started this engine, so it may be
                       stopped. Holds the backend's process IDs once known, so a
                       marker left from an earlier engine isn't honoured. Absent
                       means the user started it: never stopped.
  leases/<session>     One per session. Its mtime is that session's last
                       `docker` command.
  engine-seen          Its mtime is the last time the hook saw the engine answer.
  unhealthy-since      First time the watchdog saw a stuck Docker.
  health.json          Hypervisor result for the current boot.
  watchdog.log         What the watchdog did.
  lock                 Held across a start, a stop, or a tick.

Test-only override: DOCKER_LIFECYCLE_DIR_OVERRIDE replaces the state directory.
"""
import contextlib
import csv
import ctypes
import glob
import json
import msvcrt
import os
import re
import subprocess
import time
from typing import Callable, NamedTuple, Optional

import _winsubp  # noqa: F401 -- no console window for any child, UTF-8 decoding
import _hookutil

IDLE_SECONDS = 15 * 60  # The user's choice (2026-11-06), not derived from data.
CONTAINER_HOLD_SECONDS = 2 * 60 * 60  # Also the user's choice.
# A start's normal time isn't bounded by `--timeout 150`: the first start after SVM was
# turned on ran 7.4 minutes with no engine (2026-11-11), and a later one 35 seconds.
WEDGE_SECONDS = 20 * 60
START_TIMEOUT_SECONDS = 150
# A tray with no backend is the first seconds of a launch. One the watchdog has seen for
# this long isn't launching any more.
ORPHAN_TRAY_SECONDS = 60
ENGINE_SEEN_SECONDS = 60
SAME_BOOT_SECONDS = 120
LOG_MAX_BYTES = 1_000_000

START_TASK = "ClaudeDockerStart"
REGISTER_HINT = (
    "powershell -ExecutionPolicy Bypass -File "
    "C:/Users/brown/.claude/scripts/register-docker-watchdog.ps1"
)
# Pinned, so the engine check doesn't follow the user's current `docker context`.
DOCKER = ["docker", "--context", "desktop-linux"]
BACKEND_IMAGE = "com.docker.backend.exe"
TRAY_IMAGE = "Docker Desktop.exe"

IO_REPARSE_TAG_AF_UNIX = 0x80000023
DIR_OVERRIDE_ENV = "DOCKER_LIFECYCLE_DIR_OVERRIDE"
RUN_FAILED = -1  # Timed out or couldn't launch: the answer is unknown.

Runner = Callable[..., "tuple[int, str]"]


def state_dir() -> str:
    return os.environ.get(DIR_OVERRIDE_ENV) or os.path.join(_hookutil.SCRATCH, "docker-lifecycle")


def _path(*parts: str) -> str:
    return os.path.join(state_dir(), *parts)


def run(args: list, timeout: float = 30) -> "tuple[int, str]":
    """Run a command. A timeout or a failure to launch returns RUN_FAILED, so a
    caller can tell "no answer" from a real non-zero exit."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return RUN_FAILED, ""
    return proc.returncode, proc.stdout or ""


def _mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# --- Marker and leases ------------------------------------------------------


def has_marker() -> bool:
    return os.path.exists(_path("started-by-session"))


def write_marker(pids=()) -> None:
    """Write the marker. Binding process IDs to an existing marker keeps its time,
    which counts as a `docker` command in `newest_lease_age`."""
    path = _path("started-by-session")
    previous = _mtime(path) if pids else None
    os.makedirs(state_dir(), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"pids": sorted(pids)}, handle)
    if previous is not None:
        os.utime(path, (previous, previous))


def marker_pids() -> Optional[list]:
    """None with no marker; [] when it isn't bound to a backend yet."""
    try:
        with open(_path("started-by-session"), encoding="utf-8") as handle:
            pids = json.load(handle).get("pids")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, AttributeError):
        return []
    return [p for p in pids if isinstance(p, int)] if isinstance(pids, list) else []


def touch_lease(session_id: str, now: Optional[float] = None) -> None:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    path = _path("leases", safe)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        pass
    stamp = time.time() if now is None else now
    os.utime(path, (stamp, stamp))


def newest_lease_age(now: float) -> Optional[float]:
    """Seconds since any session's last `docker` command, or since a session's start
    task wrote the marker; None with neither."""
    paths = glob.glob(_path("leases", "*")) + [_path("started-by-session")]
    stamps = [stamp for stamp in map(_mtime, paths) if stamp is not None]
    return now - max(stamps) if stamps else None


def clear_marker() -> None:
    with contextlib.suppress(OSError):
        os.remove(_path("started-by-session"))


def clear_leases(before: Optional[float] = None) -> None:
    for path in glob.glob(_path("leases", "*")):
        stamp = _mtime(path)
        if stamp is not None and (before is None or stamp < before):
            with contextlib.suppress(OSError):
                os.remove(path)


def clear_ownership(before: Optional[float] = None) -> None:
    """Forget a session's engine. With `before`, a lease or marker written since
    then belongs to a newer command or start and stays."""
    clear_leases(before)
    stamp = _mtime(_path("started-by-session"))
    if stamp is not None and (before is None or stamp < before):
        clear_marker()


def mark_engine_seen() -> None:
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path("engine-seen"), "w", encoding="utf-8"):
        pass


def engine_seen_recently(now: float) -> bool:
    stamp = _mtime(_path("engine-seen"))
    return stamp is not None and 0 <= now - stamp < ENGINE_SEEN_SECONDS


# --- Engine and process checks ----------------------------------------------


def engine_up(runner: Runner = run) -> Optional[bool]:
    """True when the engine answers, False when it refuses, None when the check
    timed out. While Docker is starting, the same call fails with an HTTP 500, so
    `docker desktop status` isn't used here."""
    code, out = runner(DOCKER + ["version", "--format", "{{.Server.Version}}"], timeout=15)
    if code == RUN_FAILED:
        return None
    return code == 0 and bool(out.strip())


def _pids(image: str, runner: Runner) -> Optional[set]:
    """Process IDs running `image`; None when tasklist fails."""
    code, out = runner(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], timeout=15)
    if code != 0 or not out.strip():
        return None
    pids = set()
    for row in csv.reader(out.splitlines()):
        if len(row) >= 2 and row[0].lower() == image.lower() and row[1].isdigit():
            pids.add(int(row[1]))
    return pids


def _image_running(image: str, runner: Runner) -> bool:
    """A tasklist that fails or times out reads as running: "not running" leads to
    deleting sockets and clearing a session's ownership, which an unknown state must
    never do. Under memory pressure on 2026-11-11 every check timed out at once."""
    pids = _pids(image, runner)
    return pids is None or bool(pids)


def backend_running(runner: Runner = run) -> bool:
    return _image_running(BACKEND_IMAGE, runner)


def backend_pids(runner: Runner = run) -> Optional[set]:
    return _pids(BACKEND_IMAGE, runner)


def tray_running(runner: Runner = run) -> bool:
    return _image_running(TRAY_IMAGE, runner)


def container_count(runner: Runner = run) -> int:
    """A count that fails reads as one container, so an unknown holds the engine
    rather than stopping it under a running container."""
    code, out = runner(DOCKER + ["ps", "-q"], timeout=30)
    return len(out.split()) if code == 0 else 1


def owns_engine(runner: Runner = run) -> Optional[bool]:
    """Whether the marker belongs to the backend running now. None when the backend's
    process IDs can't be read. A marker naming other processes is from an engine
    that has since gone, so the running one is the user's: the marker is dropped."""
    bound = marker_pids()
    if bound is None:
        return False
    current = backend_pids(runner)
    if not current:
        return None
    if not bound:
        write_marker(current)  # First sighting of the engine this start launched.
        return True
    if set(bound) & current:
        return True
    log(f"marker named backend {sorted(bound)}, now {sorted(current)}: this engine is the user's")
    clear_marker()
    return False


def hypervisor_present(runner: Runner = run) -> Optional[bool]:
    """False means WSL 2 can't boot (Wsl/.../HCS_E_HYPERV_NOT_INSTALLED). None when
    the check didn't answer; callers treat that as present, so a broken check never
    blocks a start."""
    code, out = runner(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-CimInstance Win32_ComputerSystem).HypervisorPresent"],
        timeout=30,
    )
    answer = out.strip().lower()
    if code != 0 or answer not in ("true", "false"):
        return None
    return answer == "true"


HYPERVISOR_FIX = (
    "The Windows hypervisor isn't running, so WSL 2 and Docker can't start. The usual cause "
    "is virtualization being off in the BIOS (AMD: SVM Mode; ASUS: Advanced > CPU "
    "Configuration > SVM Mode > Enabled). Check the System event log for "
    "Microsoft-Windows-Hyper-V-Hypervisor event 42. Software can't change this setting."
)


def boot_time(now: float) -> float:
    uptime = ctypes.windll.kernel32.GetTickCount64
    uptime.restype = ctypes.c_ulonglong
    return now - uptime() / 1000


def same_boot(a: float, b: float) -> bool:
    """Clock adjustments move the computed boot time a little between ticks."""
    return abs(a - b) <= SAME_BOOT_SECONDS


def read_health() -> dict:
    with contextlib.suppress(OSError, ValueError):
        with open(_path("health.json"), encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return data
    return {}


def cached_hypervisor(boot: float) -> Optional[bool]:
    """The watchdog's answer for this boot, or None if it hasn't checked yet."""
    data = read_health()
    stored = data.get("boot")
    if isinstance(stored, (int, float)) and same_boot(stored, boot) and isinstance(data.get("hypervisor"), bool):
        return data["hypervisor"]
    return None


# --- Unhealthy clock --------------------------------------------------------


def unhealthy_age(now: float) -> float:
    """Seconds since the watchdog first saw Docker stuck; 0 if it isn't."""
    with contextlib.suppress(OSError, ValueError):
        with open(_path("unhealthy-since"), encoding="utf-8") as handle:
            return now - float(handle.read())
    return 0


def unhealthy_for(now: float, unhealthy: bool) -> float:
    """Start, read or reset the unhealthy clock."""
    path = _path("unhealthy-since")
    if not unhealthy:
        with contextlib.suppress(OSError):
            os.remove(path)
        return 0
    if os.path.exists(path):
        return unhealthy_age(now)
    os.makedirs(state_dir(), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(now))
    return 0


# --- Stale socket repair ----------------------------------------------------


def socket_dirs() -> list:
    """Directories Docker keeps AF_UNIX sockets in, in the real location and in
    the view a session-started Docker wrote through."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    dirs = [os.path.join(local, "Docker", "run"), os.path.join(local, "docker-secrets-engine")]
    for cache in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Local")):
        dirs += [os.path.join(cache, "Docker", "run"), os.path.join(cache, "docker-secrets-engine")]
    return dirs


def clean_stale_sockets(older_than: float, dirs: Optional[list] = None, lstat=os.lstat) -> "tuple[list, list]":
    """Delete AF_UNIX socket files older than `older_than`, and nothing else. The
    caller confirmed at that time that no backend was running; a newer socket
    belongs to a backend launched since. Returns (removed, failed)."""
    removed, failed = [], []
    for directory in socket_dirs() if dirs is None else dirs:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            path = os.path.join(directory, name)
            try:
                info = lstat(path)
                if getattr(info, "st_reparse_tag", 0) != IO_REPARSE_TAG_AF_UNIX:
                    continue
                if info.st_mtime >= older_than:
                    continue
                os.unlink(path)
                removed.append(path)
            except OSError as exc:
                failed.append(f"{path}: {exc}")
    return removed, failed


# --- Decision ---------------------------------------------------------------


class Snapshot(NamedTuple):
    marker: bool
    engine: bool
    backend: bool
    tray: bool
    lease_age: Optional[float]
    containers: int
    unhealthy_for: float


NOTHING = "nothing"
HOLD = "hold"
STOP = "stop"
FORCE_STOP = "force-stop"
WAIT = "wait-unhealthy"
TIDY = "tidy"
UNKNOWN = "unknown"


def decide(state: Snapshot) -> str:
    """What one watchdog tick should do. Pure, so every state is testable. The
    tick never calls this when the engine check didn't answer."""
    if state.engine:
        if not state.marker:
            return NOTHING  # The user started it.
        if state.lease_age is not None and state.lease_age < IDLE_SECONDS:
            return NOTHING
        if state.containers and state.lease_age is not None and state.lease_age < CONTAINER_HOLD_SECONDS:
            return HOLD
        return STOP
    if state.backend or state.tray:
        # A backend with no engine, or a tray with no backend. Both can be a
        # normal start for a moment; neither recovers once it has lasted.
        return FORCE_STOP if state.unhealthy_for >= WEDGE_SECONDS else WAIT
    return TIDY


# --- Lock and log -----------------------------------------------------------


@contextlib.contextmanager
def lifecycle_lock(wait_seconds: float = 0):
    """Yield True when the lock was taken. A byte-range lock on an open handle:
    Windows releases it when the process exits, however it exits, so a crash,
    a reboot or Task Scheduler's time limit can't leave a stale lock behind."""
    os.makedirs(state_dir(), exist_ok=True)
    handle = os.open(_path("lock"), os.O_CREAT | os.O_RDWR)
    deadline = time.monotonic() + wait_seconds
    held = False
    try:
        while True:
            try:
                msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(1)
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        os.close(handle)


def log(message: str) -> None:
    """Best effort: a logging failure never changes what the watchdog does."""
    with contextlib.suppress(OSError):
        os.makedirs(state_dir(), exist_ok=True)
        path = _path("watchdog.log")
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
