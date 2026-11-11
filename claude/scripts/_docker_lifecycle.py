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

State lives in `_hookutil.SCRATCH/docker-lifecycle/`:
  started-by-session   Marker: a session started this engine, so it may be
                       stopped. Absent means the user started it: never stopped.
  leases/<session>     One per session. Its mtime is that session's last
                       `docker` command.
  unhealthy-since      First time the watchdog saw a stuck Docker.
  health.json          Hypervisor result for the current boot.
  watchdog.log         What the watchdog did.
  lock                 Held across a start or a stop.

Test-only override: DOCKER_LIFECYCLE_DIR_OVERRIDE replaces the state directory.
"""
import contextlib
import glob
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
LOCK_STALE_SECONDS = 10 * 60
LOG_MAX_BYTES = 1_000_000

START_TASK = "ClaudeDockerStart"
WATCHDOG_TASK = "ClaudeDockerWatchdog"
REGISTER_HINT = (
    "powershell -ExecutionPolicy Bypass -File "
    "C:/Users/brown/.claude/scripts/register-docker-watchdog.ps1"
)

IO_REPARSE_TAG_AF_UNIX = 0x80000023
DIR_OVERRIDE_ENV = "DOCKER_LIFECYCLE_DIR_OVERRIDE"

Runner = Callable[..., "tuple[int, str]"]


def state_dir() -> str:
    return os.environ.get(DIR_OVERRIDE_ENV) or os.path.join(_hookutil.SCRATCH, "docker-lifecycle")


def _path(*parts: str) -> str:
    return os.path.join(state_dir(), *parts)


def run(args: list, timeout: float = 30) -> "tuple[int, str]":
    """Run a command; any failure to run it reads as a non-zero exit."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout or ""


# --- Marker and leases ------------------------------------------------------


def has_marker() -> bool:
    return os.path.exists(_path("started-by-session"))


def write_marker() -> None:
    os.makedirs(state_dir(), exist_ok=True)
    with open(_path("started-by-session"), "w", encoding="utf-8") as handle:
        handle.write(str(time.time()))


def touch_lease(session_id: str, now: Optional[float] = None) -> None:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
    path = _path("leases", safe)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8"):
        pass
    stamp = time.time() if now is None else now
    os.utime(path, (stamp, stamp))


def newest_lease_age(now: float) -> Optional[float]:
    """Seconds since any session's last `docker` command; None with no leases."""
    stamps = []
    for path in glob.glob(_path("leases", "*")):
        with contextlib.suppress(OSError):
            stamps.append(os.path.getmtime(path))
    return now - max(stamps) if stamps else None


def clear_ownership() -> None:
    for path in glob.glob(_path("leases", "*")) + [_path("started-by-session")]:
        with contextlib.suppress(OSError):
            os.remove(path)


# --- Engine and process checks ----------------------------------------------


def engine_up(runner: Runner = run) -> bool:
    """True only when the engine answers. While Docker is starting, the same
    call fails with an HTTP 500, so `docker desktop status` isn't used here."""
    code, out = runner(["docker", "version", "--format", "{{.Server.Version}}"], timeout=15)
    return code == 0 and bool(out.strip())


def _image_running(image: str, runner: Runner) -> bool:
    _, out = runner(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], timeout=15)
    return image.lower() in out.lower()


def backend_running(runner: Runner = run) -> bool:
    return _image_running("com.docker.backend.exe", runner)


def tray_running(runner: Runner = run) -> bool:
    return _image_running("Docker Desktop.exe", runner)


def container_count(runner: Runner = run) -> int:
    code, out = runner(["docker", "ps", "-q"], timeout=30)
    return len(out.split()) if code == 0 else 0


def hypervisor_present(runner: Runner = run) -> bool:
    """False means WSL 2 can't boot (Wsl/.../HCS_E_HYPERV_NOT_INSTALLED). An
    unreadable answer reads as present, so a broken check never stops Docker."""
    _, out = runner(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-CimInstance Win32_ComputerSystem).HypervisorPresent"],
        timeout=30,
    )
    return out.strip().lower() != "false"


HYPERVISOR_FIX = (
    "The Windows hypervisor isn't running, so WSL 2 and Docker can't start. The usual cause "
    "is virtualization being off in the BIOS (AMD: SVM Mode; ASUS: Advanced > CPU "
    "Configuration > SVM Mode > Enabled). Check the System event log for "
    "Microsoft-Windows-Hyper-V-Hypervisor event 42. Software can't change this setting."
)


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


def clean_stale_sockets(dirs: Optional[list] = None, lstat=os.lstat, unlink=os.unlink) -> "tuple[list, list]":
    """Delete AF_UNIX socket files, and nothing else. The caller must have
    confirmed the backend isn't running. Returns (removed, failed)."""
    removed, failed = [], []
    for directory in socket_dirs() if dirs is None else dirs:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            path = os.path.join(directory, name)
            try:
                if getattr(lstat(path), "st_reparse_tag", 0) != IO_REPARSE_TAG_AF_UNIX:
                    continue
                unlink(path)
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


def decide(state: Snapshot) -> str:
    """What one watchdog tick should do. Pure, so every state is testable."""
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
def lifecycle_lock(wait_seconds: float = 0, clock=time.time, sleep=time.sleep):
    """Yield True when the lock was taken. An atomic create; a lock older than
    LOCK_STALE_SECONDS belonged to a process that died."""
    os.makedirs(state_dir(), exist_ok=True)
    path = _path("lock")
    deadline = clock() + wait_seconds
    held = False
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if clock() - os.path.getmtime(path) > LOCK_STALE_SECONDS:
                    os.remove(path)
                    continue
            if clock() >= deadline:
                break
            sleep(1)
    try:
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                os.remove(path)


def log(message: str) -> None:
    """Best effort: a logging failure never changes what the watchdog does."""
    with contextlib.suppress(OSError):
        os.makedirs(state_dir(), exist_ok=True)
        path = _path("watchdog.log")
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
