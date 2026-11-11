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
  --start   Repair, then `docker desktop start`, and mark the engine as a session's.
  --status  Print the current state. Changes nothing, so it's safe from a session.

Registered by register-docker-watchdog.ps1. Actions go to watchdog.log in
`_docker_lifecycle.state_dir()`. Stopping Docker never calls `wsl --shutdown`:
WSL stops its VM by itself, and that command would also kill other distros.
"""
import json
import os
import sys
import time

import _docker_lifecycle as lifecycle

# A tick holds the lock for its checks and at most one stop.
START_LOCK_WAIT_SECONDS = 120

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


def check_hypervisor_once_per_boot(boot: float, runner=lifecycle.run, notifier=notify) -> None:
    if lifecycle.cached_hypervisor(boot) is not None:
        return
    present = lifecycle.hypervisor_present(runner)
    if present is None:
        # Not cached, so the next tick asks again rather than going quiet all boot.
        lifecycle.log("hypervisor check didn't answer; retrying next tick")
        return
    os.makedirs(lifecycle.state_dir(), exist_ok=True)
    with open(os.path.join(lifecycle.state_dir(), "health.json"), "w", encoding="utf-8") as handle:
        json.dump({"boot": boot, "hypervisor": present}, handle)
    lifecycle.log(f"hypervisor present={present}")
    if not present:
        notifier("Docker and WSL can't start", lifecycle.HYPERVISOR_FIX, runner)


def repair_sockets(runner=lifecycle.run) -> None:
    confirmed = time.time()  # Before the check, so a backend launched during it keeps its sockets.
    if lifecycle.backend_running(runner):
        return
    removed, failed = lifecycle.clean_stale_sockets(older_than=confirmed)
    for path in removed:
        lifecycle.log(f"removed stale socket {path}")
    for line in failed:
        lifecycle.log(f"could not remove stale socket {line}")


def force_stop(runner=lifecycle.run) -> None:
    decided = time.time()
    code, _ = runner(["docker", "desktop", "stop", "--force", "--timeout", "60"], timeout=90)
    lifecycle.log(f"force stop rc={code}")
    if code == 0:
        lifecycle.clear_ownership(before=decided)
        lifecycle.unhealthy_for(time.time(), False)
        repair_sockets(runner)


def tick(runner=lifecycle.run, now=None) -> str:
    now = time.time() if now is None else now
    began = time.time()
    with lifecycle.lifecycle_lock() as held:
        if not held:
            return "locked"  # A start or stop is in progress.
        engine = lifecycle.engine_up(runner)
        if engine is None:
            # Under memory pressure every check can time out. Acting on a guess could
            # stop a working engine or forget a session's, so this tick does nothing.
            lifecycle.log("skipped: the engine check didn't answer")
            return lifecycle.UNKNOWN
        backend = lifecycle.backend_running(runner)
        tray = lifecycle.tray_running(runner)
        marker = lifecycle.owns_engine(runner) is True if engine else lifecycle.has_marker()
        action = lifecycle.decide(lifecycle.Snapshot(
            marker=marker,
            engine=engine,
            backend=backend,
            tray=tray,
            lease_age=lifecycle.newest_lease_age(now),
            containers=lifecycle.container_count(runner) if engine else 0,
            unhealthy_for=lifecycle.unhealthy_for(now, not engine and (backend or tray)),
        ))
        if action == lifecycle.STOP:
            # The checks above can take a minute under load; a session may have run
            # `docker` since the snapshot.
            age = lifecycle.newest_lease_age(now + time.time() - began)
            if age is not None and age < lifecycle.IDLE_SECONDS:
                lifecycle.log("idle stop skipped: a session ran docker during the checks")
                return lifecycle.NOTHING
            decided = time.time()
            code, _ = runner(["docker", "desktop", "stop", "--timeout", "60"], timeout=90)
            lifecycle.log(f"idle stop rc={code}")
            if code == 0:  # Otherwise the marker stays and the next tick retries.
                lifecycle.clear_ownership(before=decided)
                repair_sockets(runner)
        elif action == lifecycle.FORCE_STOP:
            force_stop(runner)
        elif action == lifecycle.HOLD:
            lifecycle.log("holding: containers still running")
        elif action == lifecycle.TIDY:
            # Starts write the marker under this lock, so none is on its way. A recent
            # lease can be a hook whose start task hasn't run yet; it stays.
            if lifecycle.has_marker():
                lifecycle.log("tidy: engine, backend and tray all down; cleared session ownership")
            lifecycle.clear_marker()
            lifecycle.clear_leases(before=now - lifecycle.IDLE_SECONDS)
            repair_sockets(runner)
        return action


def start(runner=lifecycle.run) -> int:
    with lifecycle.lifecycle_lock(wait_seconds=START_LOCK_WAIT_SECONDS) as held:
        if not held:
            lifecycle.log(f"start: skipped, the lock was held for {START_LOCK_WAIT_SECONDS} seconds")
            return 1
        if lifecycle.engine_up(runner) or lifecycle.backend_running(runner):
            return 0  # Up, or starting: the user's or another session's.
        if lifecycle.tray_running(runner):
            if lifecycle.unhealthy_age(time.time()) < lifecycle.ORPHAN_TRAY_SECONDS:
                lifecycle.log("start: Docker Desktop is already launching")
                return 0
            force_stop(runner)  # A tray with no backend; `docker desktop start` would do nothing.
        repair_sockets(runner)
        # Under the lock, so a tick can't clear it while this start runs.
        lifecycle.write_marker()
        code, _ = runner(
            ["docker", "desktop", "start", "--timeout", str(lifecycle.START_TIMEOUT_SECONDS)],
            timeout=lifecycle.START_TIMEOUT_SECONDS + 15,
        )
        lifecycle.log(f"start rc={code}")
        if code == 0:
            pids = lifecycle.backend_pids(runner)
            if pids:
                lifecycle.write_marker(pids)
        return code


def status(runner=lifecycle.run) -> int:
    now = time.time()
    age = lifecycle.newest_lease_age(now)
    present = lifecycle.hypervisor_present(runner)
    print("hypervisor present:", "unknown" if present is None else present)
    engine = lifecycle.engine_up(runner)
    print("engine answering:  ", "unknown" if engine is None else engine)
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
        try:
            check_hypervisor_once_per_boot(lifecycle.boot_time(now))
        except Exception as exc:  # noqa: BLE001 -- the monitor must never cost the tick its stop or repair
            lifecycle.log(f"hypervisor check failed: {type(exc).__name__}: {exc}")
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
