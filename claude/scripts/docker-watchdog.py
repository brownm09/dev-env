#!/usr/bin/env python3
"""Keeps Docker Desktop startable and stops it when sessions are done. See
ADR-158 and dev-env#1278.

Purpose: scheduled-task payload that checks the hypervisor, repairs a stuck
Docker Desktop, and stops a session-started engine when sessions are done.

Task Scheduler runs this at sign-in and every 5 minutes (--tick), and on demand
to start Docker (--start). It has to run from there: only a process outside the
Claude app's tree can delete Docker's stale socket files.

  --tick    Monitor: once per boot, check the hypervisor and notify if it's off.
            Repair: stop a Docker stuck for WEDGE_SECONDS; delete stale sockets
            whenever no backend is running, so the next start can't hit them.
            Idle stop: stop a session-started engine per `_docker_lifecycle.decide`.
  --start   Repair, then `docker desktop start`.
  --status  Print the current state. Changes nothing, so it's safe from a session.

Registered by register-docker-watchdog.ps1. Actions go to watchdog.log in
`_docker_lifecycle.state_dir()`. Stopping Docker never calls `wsl --shutdown`:
WSL stops its VM by itself, and that command would also kill other distros.
"""
import contextlib
import ctypes
import json
import os
import sys
import time

import _docker_lifecycle as lifecycle

_TOAST_SCRIPT = r"""
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
$null = $text.Item(0).AppendChild($xml.CreateTextNode($env:DOCKER_WATCHDOG_TITLE))
$null = $text.Item(1).AppendChild($xml.CreateTextNode($env:DOCKER_WATCHDOG_BODY))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""


def notify(title: str, body: str, runner=lifecycle.run) -> None:
    os.environ["DOCKER_WATCHDOG_TITLE"] = title
    os.environ["DOCKER_WATCHDOG_BODY"] = body
    code, _ = runner(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_SCRIPT], timeout=30
    )
    lifecycle.log(f"notify rc={code}: {title}")


def boot_time(now: float) -> int:
    """Minute-rounded, so clock jitter doesn't read as a new boot."""
    uptime = ctypes.windll.kernel32.GetTickCount64
    uptime.restype = ctypes.c_ulonglong
    return int((now - uptime() / 1000) // 60) * 60


def check_hypervisor_once_per_boot(boot: int, runner=lifecycle.run, notifier=notify) -> None:
    path = os.path.join(lifecycle.state_dir(), "health.json")
    with contextlib.suppress(OSError, ValueError):
        with open(path, encoding="utf-8") as handle:
            if json.load(handle).get("boot") == boot:
                return
    present = lifecycle.hypervisor_present(runner)
    os.makedirs(lifecycle.state_dir(), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"boot": boot, "hypervisor": present}, handle)
    lifecycle.log(f"hypervisor present={present}")
    if not present:
        notifier("Docker and WSL can't start", lifecycle.HYPERVISOR_FIX, runner)


def _unhealthy_for(now: float, unhealthy: bool) -> float:
    path = os.path.join(lifecycle.state_dir(), "unhealthy-since")
    if not unhealthy:
        with contextlib.suppress(OSError):
            os.remove(path)
        return 0
    with contextlib.suppress(OSError, ValueError):
        with open(path, encoding="utf-8") as handle:
            return now - float(handle.read())
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(now))
    return 0


def repair_sockets(runner=lifecycle.run) -> None:
    if lifecycle.backend_running(runner):
        return
    removed, failed = lifecycle.clean_stale_sockets()
    for path in removed:
        lifecycle.log(f"removed stale socket {path}")
    for line in failed:
        lifecycle.log(f"could not remove stale socket {line}")


def force_stop(runner=lifecycle.run) -> bool:
    code, _ = runner(["docker", "desktop", "stop", "--force", "--timeout", "60"], timeout=90)
    lifecycle.log(f"force stop rc={code}")
    if code == 0:
        lifecycle.clear_ownership()
        _unhealthy_for(time.time(), False)
        repair_sockets(runner)
    return code == 0


def tick(runner=lifecycle.run, now=None) -> str:
    now = time.time() if now is None else now
    with lifecycle.lifecycle_lock() as held:
        if not held:
            return "locked"  # A start or stop is in progress.
        engine = lifecycle.engine_up(runner)
        backend = lifecycle.backend_running(runner)
        tray = lifecycle.tray_running(runner)
        action = lifecycle.decide(lifecycle.Snapshot(
            marker=lifecycle.has_marker(),
            engine=engine,
            backend=backend,
            tray=tray,
            lease_age=lifecycle.newest_lease_age(now),
            containers=lifecycle.container_count(runner) if engine else 0,
            unhealthy_for=_unhealthy_for(now, not engine and (backend or tray)),
        ))
        if action == lifecycle.STOP:
            code, _ = runner(["docker", "desktop", "stop", "--timeout", "60"], timeout=90)
            lifecycle.log(f"idle stop rc={code}")
            if code == 0:  # Otherwise the marker stays and the next tick retries.
                lifecycle.clear_ownership()
                repair_sockets(runner)
        elif action == lifecycle.FORCE_STOP:
            force_stop(runner)
        elif action == lifecycle.HOLD:
            lifecycle.log("holding: containers still running")
        elif action == lifecycle.TIDY:
            # A fresh lease can mean the hook just wrote the marker and the start
            # task hasn't taken the lock yet. Clearing it then would make a
            # session's engine look user-started, and it would never be stopped.
            age = lifecycle.newest_lease_age(now)
            if age is None or age > lifecycle.START_TIMEOUT_SECONDS:
                lifecycle.clear_ownership()
            repair_sockets(runner)
        return action


def start(runner=lifecycle.run) -> int:
    with lifecycle.lifecycle_lock(wait_seconds=120) as held:
        if not held:
            lifecycle.log("start: lock not available")
        if lifecycle.engine_up(runner) or lifecycle.backend_running(runner):
            return 0
        if lifecycle.tray_running(runner):
            force_stop(runner)
            lifecycle.write_marker()  # force_stop clears it; this start is a session's.
        repair_sockets(runner)
        code, _ = runner(
            ["docker", "desktop", "start", "--timeout", str(lifecycle.START_TIMEOUT_SECONDS)],
            timeout=lifecycle.START_TIMEOUT_SECONDS + 15,
        )
        lifecycle.log(f"start rc={code}")
        return code


def status(runner=lifecycle.run) -> int:
    now = time.time()
    age = lifecycle.newest_lease_age(now)
    print("hypervisor present:", lifecycle.hypervisor_present(runner))
    print("engine answering:  ", lifecycle.engine_up(runner))
    print("backend running:   ", lifecycle.backend_running(runner))
    print("tray running:      ", lifecycle.tray_running(runner))
    print("started by session:", lifecycle.has_marker())
    print("last docker command:", "none" if age is None else f"{int(age)} seconds ago")
    print("log:", os.path.join(lifecycle.state_dir(), "watchdog.log"))
    return 0


def main(argv: list) -> int:
    mode = argv[1] if len(argv) > 1 else "--status"
    if mode == "--tick":
        now = time.time()
        check_hypervisor_once_per_boot(boot_time(now))
        tick(now=now)
        return 0
    if mode == "--start":
        return start()
    if mode == "--status":
        return status()
    print("usage: docker-watchdog.py --tick | --start | --status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:  # noqa: BLE001 -- no console to report to, so it goes to the log
        lifecycle.log(f"crashed: {type(exc).__name__}: {exc}")
        sys.exit(1)
