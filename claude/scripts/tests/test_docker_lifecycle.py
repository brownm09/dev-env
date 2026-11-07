#!/usr/bin/env python3
"""Tests for _docker_lifecycle.py, pre-tool-use-docker-start.py and
docker-watchdog.py (dev-env#1278, ADR-158).

Required when changing `_docker_lifecycle.py`, `pre-tool-use-docker-start.py`,
`docker-watchdog.py` or `register-docker-watchdog.ps1`.
Run: py -3 claude/scripts/tests/test_docker_lifecycle.py

Nothing here touches real Docker: a FakeDocker answers every command and records
what it was asked to run. `DOCKER_LIFECYCLE_DIR_OVERRIDE` keeps state in a temp
directory and `HOOK_HEARTBEAT_DIR_OVERRIDE` keeps the heartbeat out of the real
ledger. It pins which commands need the engine, the direct-start block under both
shells, starting only through the scheduled task, every stop-decision state, wedge
timing across ticks, leases, the lock, the once-per-boot hypervisor check, and
deleting only AF_UNIX sockets.

Scope gap: the failure this exists for only reproduces on a real machine, inside the
Claude app's process tree, and CI has no Docker Desktop. When changing how Docker is
started or stopped, also check by hand: with the engine down, a session's `docker ps`
succeeds; `py -3 claude/scripts/docker-watchdog.py --status` shows it as started by a
session; after 15 to 20 minutes idle it has stopped and `wsl --list --running` lists
no `docker-desktop`; and a later start shows no Inference manager error.

Exit 0 = all pass.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

_TMP = tempfile.mkdtemp(prefix="docker_lifecycle_")
os.environ["DOCKER_LIFECYCLE_DIR_OVERRIDE"] = os.path.join(_TMP, "state")
os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = os.path.join(_TMP, "heartbeat")

import _docker_lifecycle as lifecycle  # noqa: E402


def _load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_")[:-3], SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load("pre-tool-use-docker-start.py")
watchdog = _load("docker-watchdog.py")


def _reset_state():
    shutil.rmtree(lifecycle.state_dir(), ignore_errors=True)


class FakeDocker:
    """Answers the commands the lifecycle code runs, and records them."""

    def __init__(self, engine=False, backend=False, tray=False, containers=0,
                 hypervisor=True, stop_rc=0, task_rc=0, up_after_polls=None):
        self.engine = engine
        self.backend = backend
        self.tray = tray
        self.containers = containers
        self.hypervisor = hypervisor
        self.stop_rc = stop_rc
        self.task_rc = task_rc
        self.up_after_polls = up_after_polls
        self.calls = []

    def ran(self, *words):
        return any(all(w in call for w in words) for call in self.calls)

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        joined = " ".join(args)
        if joined.startswith("docker version"):
            if self.up_after_polls is not None:
                self.up_after_polls -= 1
                if self.up_after_polls <= 0:
                    self.engine = True
            return (0, "29.4.3\n") if self.engine else (1, "")
        if joined.startswith("docker ps"):
            return 0, "abc\n" * self.containers
        if joined.startswith("tasklist"):
            image = joined.split("IMAGENAME eq ")[1].split(" /NH")[0]
            up = self.backend if image.startswith("com.docker.backend") else self.tray
            return 0, (image + " 1 Console\n") if up else "INFO: No tasks are running\n"
        if args[0] == "powershell":
            return 0, "True\n" if self.hypervisor else "False\n"
        if args[0] == "schtasks":
            return self.task_rc, ""
        if joined.startswith("docker desktop stop"):
            if self.stop_rc == 0:
                self.engine = self.backend = self.tray = False
            return self.stop_rc, ""
        if joined.startswith("docker desktop start"):
            self.engine = True
            return 0, ""
        raise AssertionError(f"unexpected command: {joined}")


def _eq(actual, expected, label):
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


# --- Command classification -------------------------------------------------


def test_commands_that_need_the_engine():
    for command in (
        "docker ps",
        "docker compose -f docker-compose.test.yml up -d",
        "docker-compose up",
        "docker.exe ps",
        "cd apps/api && docker compose up -d",
        "git status; docker ps",
        "$x = 1; if ($?) { docker ps }",
        "FOO=1 docker ps",
        "& docker ps",
        "docker info >/dev/null 2>&1 && echo up || echo down",
        "cd x && (docker info --format '{{.ServerVersion}}' || echo down)",
        "docker rm -f ll-revcheck >/dev/null && echo removed",
        "echo hi | docker login --help; docker images",
    ):
        _eq(hook.classify(command), hook.NEEDS_ENGINE, command)


def test_mentions_and_other_commands_start_nothing():
    for command in (
        "git status",
        "git grep docker",
        "grep -rn 'docker ps' .",
        'echo "docker ps"',
        "git commit -m 'docker compose up'",
        "gh issue view 1278 --json title",
        "cat <<'EOF'\ndocker ps\nEOF",
        "Test-Path \"C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe\"",
        "ls docker",
        "dockerfile-lint Dockerfile",
    ):
        _eq(hook.classify(command), None, command)


def test_subcommands_that_do_not_need_the_engine():
    for command in (
        "docker", "docker --version", "docker -v", "docker --help", "docker help",
        "docker context ls", "docker desktop status", "docker desktop stop --force",
        "docker desktop --help", "docker desktop logs",
        "docker desktop start --help", "docker compose --help", "docker ps -h",
    ):
        _eq(hook.classify(command), None, command)


def test_direct_starts_are_recognised():
    for command in (
        "docker desktop start",
        "docker desktop start --timeout 180",
        "docker desktop restart",
        "docker ps; docker desktop start",
        "& \"C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe\"",
        "Start-Process \"C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe\"",
    ):
        _eq(hook.classify(command), hook.DIRECT_START, command)


def test_project_test_commands_reach_docker_without_naming_it():
    repo = tempfile.mkdtemp(dir=_TMP)
    os.makedirs(os.path.join(repo, ".claude"))
    subdir = os.path.join(repo, "apps", "api")
    os.makedirs(subdir)
    with open(os.path.join(repo, ".claude", "hook-config.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "docker_test_commands": [r"^npm test\b(?!.*-w @x/web)", "(unbalanced"],
            "docker_test_skip": "SKIP_DB",
        }, handle)
    for cwd in (repo, subdir):
        _eq(hook.classify("npm test", cwd), hook.NEEDS_ENGINE, "named test command")
        _eq(hook.classify("FOO=1 npm test -w @x/api", cwd), hook.NEEDS_ENGINE, "env prefix")
        _eq(hook.classify("cd apps/api && npm test", cwd), hook.NEEDS_ENGINE, "after cd")
    _eq(hook.classify("npm test -w @x/web", repo), None, "workspace without Docker")
    _eq(hook.classify("SKIP_DB=1 npm test", repo), None, "skip marker")
    _eq(hook.classify("export SKIP_DB=1; npm test", repo), None, "skip marker earlier")
    _eq(hook.classify("git status", repo), None, "other command")
    _eq(hook.classify("SKIP_DB=1 docker ps", repo), hook.NEEDS_ENGINE, "a real docker command still counts")
    _eq(hook.classify("npm test", tempfile.mkdtemp(dir=_TMP)), None, "project without the key")


def test_broken_project_config_is_ignored():
    repo = tempfile.mkdtemp(dir=_TMP)
    os.makedirs(os.path.join(repo, ".claude"))
    with open(os.path.join(repo, ".claude", "hook-config.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    _eq(hook.classify("npm test", repo), None, "broken config")
    _eq(hook.classify("docker ps", repo), hook.NEEDS_ENGINE, "docker still detected")


def _run_hook(command, tool="Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command}, "session_id": "s1"}
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "pre-tool-use-docker-start.py")],
        input=json.dumps(payload).encode("utf-8"), capture_output=True, check=False,
    )


def test_hook_blocks_a_direct_start_with_a_way_out():
    for tool in ("Bash", "PowerShell"):
        proc = _run_hook("docker desktop start", tool)
        _eq(proc.returncode, 2, tool)
        message = proc.stderr.decode("utf-8")
        assert "ADR-158" in message and "--status" in message, message


def test_hook_ignores_an_unrelated_command():
    proc = _run_hook("git status")
    _eq((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""), "unrelated command")


def test_a_non_matching_command_runs_no_subprocess():
    data = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "session_id": "s1"}
    original_load, original_ensure = hook.load_hook_input, hook.ensure_engine
    hook.load_hook_input = lambda _tools: data

    def fail(*_args, **_kwargs):
        raise AssertionError("ensure_engine ran for a non-matching command")

    hook.ensure_engine = fail
    try:
        try:
            hook.main()
        except SystemExit as stop:
            _eq(stop.code, 0, "exit code")
    finally:
        hook.load_hook_input, hook.ensure_engine = original_load, original_ensure


# --- Starting ---------------------------------------------------------------


def _ensure(fake, polls=1000):
    ticks = iter(range(polls))
    return hook.ensure_engine("s1", runner=fake, clock=lambda: next(ticks), sleep=lambda _s: None)


def test_engine_already_up_is_left_alone_and_stays_user_owned():
    _reset_state()
    fake = FakeDocker(engine=True, backend=True)
    _eq(_ensure(fake), (False, ""), "result")
    assert not fake.ran("schtasks"), "must not start a running engine"
    assert not lifecycle.has_marker(), "a running engine with no marker is the user's"


def test_start_goes_through_the_scheduled_task_never_directly():
    _reset_state()
    fake = FakeDocker(up_after_polls=3)
    _eq(_ensure(fake), (True, ""), "result")
    assert fake.ran("schtasks", "/Run", lifecycle.START_TASK), fake.calls
    assert not fake.ran("docker", "desktop", "start"), "a session must never start Docker directly"
    assert lifecycle.has_marker(), "a session-started engine carries the marker"


def test_hypervisor_off_reports_the_fix_and_starts_nothing():
    _reset_state()
    fake = FakeDocker(hypervisor=False)
    started, problem = _ensure(fake)
    _eq(started, False, "started")
    assert "SVM" in problem, problem
    assert not fake.ran("schtasks"), "starting would only wedge Docker"
    assert not lifecycle.has_marker(), "nothing was started"


def test_missing_task_fails_open_and_clears_the_marker():
    _reset_state()
    fake = FakeDocker(task_rc=1)
    started, problem = _ensure(fake)
    _eq(started, False, "started")
    assert "register-docker-watchdog.ps1" in problem, problem
    assert not lifecycle.has_marker(), "the failed start left no marker"


def test_a_start_that_never_answers_reports_it():
    _reset_state()
    fake = FakeDocker()
    started, problem = _ensure(fake, polls=hook.START_WAIT_SECONDS + 5)
    _eq(started, False, "started")
    assert "--status" in problem, problem


def test_a_user_start_in_progress_is_not_claimed():
    _reset_state()
    fake = FakeDocker(backend=True, up_after_polls=3)
    _eq(_ensure(fake), (True, ""), "result")
    assert not fake.ran("schtasks"), "a backend is already starting"
    assert not lifecycle.has_marker(), "the user started this one"


# --- Decision table ---------------------------------------------------------


def _state(**changes):
    base = dict(marker=True, engine=True, backend=True, tray=True,
                lease_age=0.0, containers=0, unhealthy_for=0.0)
    base.update(changes)
    return lifecycle.Snapshot(**base)


def test_decide_every_state():
    idle = lifecycle.IDLE_SECONDS
    hold = lifecycle.CONTAINER_HOLD_SECONDS
    wedge = lifecycle.WEDGE_SECONDS
    for changes, expected, label in (
        ({"marker": False, "lease_age": None}, lifecycle.NOTHING, "user-started engine"),
        ({"marker": False, "lease_age": hold * 2}, lifecycle.NOTHING, "user-started, old lease"),
        ({"lease_age": idle - 1}, lifecycle.NOTHING, "recent docker command"),
        ({"lease_age": idle - 1, "containers": 1}, lifecycle.NOTHING, "recent, with containers"),
        ({"lease_age": idle}, lifecycle.STOP, "idle, no containers"),
        ({"lease_age": None}, lifecycle.STOP, "marker but no lease"),
        ({"lease_age": idle, "containers": 1}, lifecycle.HOLD, "idle, containers running"),
        ({"lease_age": hold - 1, "containers": 1}, lifecycle.HOLD, "just inside the hold"),
        ({"lease_age": hold, "containers": 1}, lifecycle.STOP, "hold expired"),
        ({"engine": False}, lifecycle.WAIT, "backend up, engine not yet"),
        ({"engine": False, "unhealthy_for": wedge - 1}, lifecycle.WAIT, "not stuck yet"),
        ({"engine": False, "unhealthy_for": wedge}, lifecycle.FORCE_STOP, "stuck starting"),
        ({"engine": False, "backend": False, "unhealthy_for": wedge}, lifecycle.FORCE_STOP, "orphan tray"),
        ({"engine": False, "marker": False, "unhealthy_for": wedge}, lifecycle.FORCE_STOP, "user start, stuck"),
        ({"engine": False, "backend": False, "tray": False}, lifecycle.TIDY, "all stopped"),
    ):
        _eq(lifecycle.decide(_state(**changes)), expected, label)


def test_two_sessions_hold_the_engine_until_the_newer_lease_ages_out():
    _reset_state()
    now = time.time()
    lifecycle.touch_lease("older", now - lifecycle.IDLE_SECONDS * 3)
    lifecycle.touch_lease("newer", now - 60)
    age = lifecycle.newest_lease_age(now)
    assert 59 <= age <= 61, age
    _eq(lifecycle.decide(_state(lease_age=age)), lifecycle.NOTHING, "newer lease holds it")
    lifecycle.touch_lease("newer", now - lifecycle.IDLE_SECONDS - 1)
    age = lifecycle.newest_lease_age(now)
    _eq(lifecycle.decide(_state(lease_age=age)), lifecycle.STOP, "both aged out")


def test_lease_names_cannot_escape_the_state_directory():
    _reset_state()
    lifecycle.touch_lease("../../evil")
    assert os.path.exists(os.path.join(lifecycle.state_dir(), "leases", "______evil")), "sanitized"


# --- Watchdog tick ----------------------------------------------------------


def _session_started(lease_age, now):
    lifecycle.write_marker()
    lifecycle.touch_lease("s1", now - lease_age)


def test_tick_stops_an_idle_session_engine_and_tidies():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True, tray=True)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.STOP, "action")
    assert fake.ran("docker", "desktop", "stop"), fake.calls
    assert not fake.ran("--force"), "a healthy engine gets a clean stop"
    assert not fake.ran("wsl"), "never touches WSL"
    assert not lifecycle.has_marker(), "ownership cleared"


def test_tick_failed_stop_keeps_the_marker_and_retries():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True, stop_rc=1)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.STOP, "action")
    assert lifecycle.has_marker(), "kept for the next tick"
    assert not fake.ran("--force"), "a failed idle stop never escalates"


def test_tick_never_stops_a_user_started_engine():
    _reset_state()
    fake = FakeDocker(engine=True, backend=True)
    _eq(watchdog.tick(runner=fake, now=time.time()), lifecycle.NOTHING, "action")
    assert not fake.ran("docker", "desktop", "stop"), fake.calls


def test_tick_holds_for_a_running_container():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True, containers=1)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.HOLD, "action")
    assert not fake.ran("docker", "desktop", "stop"), fake.calls
    _session_started(lifecycle.CONTAINER_HOLD_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True, containers=1)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.STOP, "after 2 hours")


def test_tick_times_a_wedge_across_ticks_then_force_stops():
    _reset_state()
    now = time.time()
    fake = FakeDocker(tray=True)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.WAIT, "first sighting")
    _eq(watchdog.tick(runner=fake, now=now + 300), lifecycle.WAIT, "five minutes on")
    assert not fake.ran("docker", "desktop", "stop"), "not yet"
    _eq(watchdog.tick(runner=fake, now=now + lifecycle.WEDGE_SECONDS), lifecycle.FORCE_STOP, "ten minutes on")
    assert fake.ran("docker", "desktop", "stop", "--force"), fake.calls


def test_tick_recovery_resets_the_wedge_clock():
    _reset_state()
    now = time.time()
    watchdog.tick(runner=FakeDocker(tray=True), now=now)
    watchdog.tick(runner=FakeDocker(engine=True, backend=True), now=now + 60)
    _eq(watchdog.tick(runner=FakeDocker(tray=True), now=now + lifecycle.WEDGE_SECONDS),
        lifecycle.WAIT, "a new sighting starts a new clock")


def test_tick_keeps_ownership_while_a_start_is_on_its_way():
    _reset_state()
    now = time.time()
    _session_started(5, now)
    _eq(watchdog.tick(runner=FakeDocker(), now=now), lifecycle.TIDY, "action")
    assert lifecycle.has_marker(), "the start task hasn't taken the lock yet"
    _session_started(lifecycle.START_TIMEOUT_SECONDS + 1, now)
    _eq(watchdog.tick(runner=FakeDocker(), now=now), lifecycle.TIDY, "action")
    assert not lifecycle.has_marker(), "that start never happened"


def test_tick_skips_when_a_start_holds_the_lock():
    _reset_state()
    with lifecycle.lifecycle_lock() as held:
        assert held
        fake = FakeDocker(tray=True)
        _eq(watchdog.tick(runner=fake, now=time.time()), "locked", "action")
        _eq(fake.calls, [], "calls")


def test_a_stale_lock_is_broken():
    _reset_state()
    os.makedirs(lifecycle.state_dir(), exist_ok=True)
    path = os.path.join(lifecycle.state_dir(), "lock")
    open(path, "w", encoding="utf-8").close()
    old = time.time() - lifecycle.LOCK_STALE_SECONDS - 5
    os.utime(path, (old, old))
    with lifecycle.lifecycle_lock() as held:
        assert held, "stale lock should be taken over"


# --- Watchdog start and hypervisor check ------------------------------------


def test_start_force_stops_an_orphan_tray_first_and_keeps_ownership():
    _reset_state()
    lifecycle.write_marker()
    fake = FakeDocker(tray=True)
    _eq(watchdog.start(runner=fake), 0, "rc")
    assert fake.ran("--force"), fake.calls
    assert fake.ran("docker", "desktop", "start"), fake.calls
    assert lifecycle.has_marker(), "still a session's start"


def test_start_does_nothing_when_up_or_starting():
    for fake in (FakeDocker(engine=True, backend=True), FakeDocker(backend=True)):
        _reset_state()
        _eq(watchdog.start(runner=fake), 0, "rc")
        assert not fake.ran("docker", "desktop", "start"), fake.calls


def test_hypervisor_notifies_once_per_boot():
    _reset_state()
    notes = []
    fake = FakeDocker(hypervisor=False)
    note = lambda title, body, runner: notes.append(title)  # noqa: E731
    watchdog.check_hypervisor_once_per_boot(1000, runner=fake, notifier=note)
    watchdog.check_hypervisor_once_per_boot(1000, runner=fake, notifier=note)
    _eq(len(notes), 1, "same boot")
    watchdog.check_hypervisor_once_per_boot(2000, runner=fake, notifier=note)
    _eq(len(notes), 2, "new boot")


def test_hypervisor_present_stays_quiet_and_unreadable_reads_as_present():
    _reset_state()
    notes = []
    watchdog.check_hypervisor_once_per_boot(
        1000, runner=FakeDocker(), notifier=lambda *a: notes.append(a))
    _eq(notes, [], "no notification")
    assert lifecycle.hypervisor_present(lambda *_a, **_k: (1, "")), "unreadable reads as present"


# --- Stale sockets ----------------------------------------------------------


def test_clean_removes_only_af_unix_sockets():
    root = tempfile.mkdtemp(dir=_TMP)
    for name in ("dockerInference", "ordinary.txt", "stuck.sock"):
        open(os.path.join(root, name), "w", encoding="utf-8").close()

    def fake_lstat(path):
        if path.endswith("stuck.sock"):
            raise OSError(1920, "The file cannot be accessed by the system")
        tag = lifecycle.IO_REPARSE_TAG_AF_UNIX if path.endswith("dockerInference") else 0
        return types.SimpleNamespace(st_reparse_tag=tag)

    removed, failed = lifecycle.clean_stale_sockets([root, os.path.join(root, "absent")], lstat=fake_lstat)
    _eq([os.path.basename(p) for p in removed], ["dockerInference"], "removed")
    _eq(len(failed), 1, "failed")
    assert "stuck.sock" in failed[0], failed
    assert os.path.exists(os.path.join(root, "ordinary.txt")), "other files stay"


def test_repair_leaves_sockets_alone_while_the_backend_runs():
    original = lifecycle.clean_stale_sockets

    def fail(*_args, **_kwargs):
        raise AssertionError("cleaned while the backend was running")

    lifecycle.clean_stale_sockets = fail
    try:
        watchdog.repair_sockets(runner=FakeDocker(backend=True))
    finally:
        lifecycle.clean_stale_sockets = original


def test_socket_dirs_include_the_redirected_view():
    local = tempfile.mkdtemp(dir=_TMP)
    cache = os.path.join(local, "Packages", "Claude_abc", "LocalCache", "Local")
    os.makedirs(cache)
    original = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = local
    try:
        dirs = lifecycle.socket_dirs()
    finally:
        os.environ["LOCALAPPDATA"] = original or ""
    assert os.path.join(local, "Docker", "run") in dirs, dirs
    assert os.path.join(cache, "docker-secrets-engine") in dirs, dirs


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert tests, "no tests collected"
    failed = 0
    start = time.time()
    for name, func in tests:
        try:
            func()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001 -- report and keep going
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed ({time.time() - start:.1f}s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
