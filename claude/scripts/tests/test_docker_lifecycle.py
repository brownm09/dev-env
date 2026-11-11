#!/usr/bin/env python3
"""Tests for _docker_lifecycle.py, pre-tool-use-docker-start.py,
docker-watchdog.py and register-docker-watchdog.ps1 (dev-env#1278, ADR-158).

Required when changing `_docker_lifecycle.py`, `pre-tool-use-docker-start.py`,
`docker-watchdog.py` or `register-docker-watchdog.ps1`.
Run: py -3 claude/scripts/tests/test_docker_lifecycle.py

Nothing here touches real Docker: a FakeDocker answers every command, records
what it was asked to run, and charges each call its timeout on a fake clock.
`DOCKER_LIFECYCLE_DIR_OVERRIDE` keeps state in a temp directory and
`HOOK_HEARTBEAT_DIR_OVERRIDE` keeps the heartbeat out of the real ledger. It pins
which commands need the engine, the direct-start block (nested shells and
launchers included), starting only through the scheduled task within the hook's
timeout, every stop-decision state, checks that time out across many ticks,
ownership races, the process lock, the once-per-boot hypervisor check, deleting
only old AF_UNIX sockets, and the registration script's task settings.

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
EXE = "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe"
CLI = "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"


def _reset_state():
    shutil.rmtree(lifecycle.state_dir(), ignore_errors=True)


class FakeDocker:
    """Answers the commands the lifecycle code runs, records them, and advances
    `elapsed` by each call's timeout, the worst case for a real call."""

    def __init__(self, engine=False, backend=False, tray=False, containers=0,
                 hypervisor=True, stop_rc=0, task_rc=0, up_after_polls=None, failing=(),
                 backend_pid=4242, on=None):
        self.failing = failing  # Command prefixes that time out.
        self.engine = engine
        self.backend = backend
        self.tray = tray
        self.containers = containers
        self.hypervisor = hypervisor
        self.stop_rc = stop_rc
        self.task_rc = task_rc
        self.up_after_polls = up_after_polls
        self.backend_pid = backend_pid
        self.on = on or {}  # Command prefix -> callable run just before answering.
        self.calls = []
        self.elapsed = 0.0

    def ran(self, *words):
        return any(all(w in call for w in words) for call in self.calls)

    def clock(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        self.elapsed += timeout
        joined = " ".join(args).replace("docker --context desktop-linux ", "docker ")
        for prefix, action in self.on.items():
            if joined.startswith(prefix):
                action()
        if any(joined.startswith(prefix) for prefix in self.failing):
            return lifecycle.RUN_FAILED, ""
        if joined.startswith("docker version"):
            if self.up_after_polls is not None:
                self.up_after_polls -= 1
                if self.up_after_polls <= 0:
                    self.engine = self.backend = True
            return (0, "29.4.3\n") if self.engine else (1, "")
        if joined.startswith("docker ps"):
            return 0, "abc\n" * self.containers
        if joined.startswith("tasklist"):
            image = joined.split("IMAGENAME eq ")[1].split(" /FO")[0]
            if image == lifecycle.BACKEND_IMAGE:
                up, pid = self.backend, self.backend_pid
            else:
                up, pid = self.tray, 5151
            return 0, (f'"{image}","{pid}","Console","1","10,000 K"\n' if up
                       else "INFO: No tasks are running which match the specified criteria.\n")
        if args[0] == "powershell":
            return 0, "True\n" if self.hypervisor else "False\n"
        if args[0] == "schtasks":
            return self.task_rc, ""
        if joined.startswith("docker desktop stop"):
            if self.stop_rc == 0:
                self.engine = self.backend = self.tray = False
            return self.stop_rc, ""
        if joined.startswith("docker desktop start"):
            self.engine = self.backend = True
            return 0, ""
        raise AssertionError(f"unexpected command: {joined}")


def _eq(actual, expected, label):
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def _write_unhealthy_since(seconds_ago):
    os.makedirs(lifecycle.state_dir(), exist_ok=True)
    with open(os.path.join(lifecycle.state_dir(), "unhealthy-since"), "w", encoding="utf-8") as handle:
        handle.write(str(time.time() - seconds_ago))


def _lease_exists(name="s1"):
    return os.path.exists(os.path.join(lifecycle.state_dir(), "leases", name))


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
        # Flags after the subcommand belong to it, not to `docker`.
        "docker run -v data:/data postgres",
        "docker compose down -v",
        "docker run -h host alpine",
        "docker exec c node --version",
        "docker run --rm node:20 node -v",
        "docker --context desktop-linux ps",
        f'& "{CLI}" ps',
        "/c/Program\\ Files/Docker/Docker/resources/bin/docker.exe ps",
        'powershell -NoProfile -Command "docker ps"',
        "bash -lc 'docker compose up -d'",
    ):
        _eq(hook.classify(command), hook.NEEDS_ENGINE, command)


def test_mentions_and_other_commands_start_nothing():
    for command in (
        "git status",
        "git grep docker",
        "grep -rn 'docker ps' .",
        'echo "docker ps"',
        "git commit -m 'docker compose up'",
        'git commit -m "powershell -Command docker desktop start"',
        "gh issue view 1278 --json title",
        "cat <<'EOF'\ndocker ps\nEOF",
        f'Test-Path "{EXE}"',
        f'grep -n "Docker Desktop.exe" notes.md',
        f'Get-Item "{EXE}"',
        'echo "bash -c docker ps"',
        "ls docker",
        "ls /usr/bin/docker",
        "dockerfile-lint Dockerfile",
        'powershell -NoProfile -Command "Get-Date"',
    ):
        _eq(hook.classify(command), None, command)


def test_subcommands_that_do_not_need_the_engine():
    for command in (
        "docker", "docker --version", "docker -v", "docker --help", "docker help",
        "docker context ls", "docker desktop status", "docker desktop stop --force",
        "docker desktop --help", "docker desktop logs",
        "docker desktop start --help", "docker compose --help", "docker ps -h",
        "docker --context default --version",
    ):
        _eq(hook.classify(command), None, command)


def test_direct_starts_are_recognised():
    for command in (
        "docker desktop start",
        "docker desktop start --timeout 180",
        "docker desktop restart",
        "docker ps; docker desktop start",
        "docker --context default desktop start",
        f'"{CLI}" desktop start',
        f'& "{EXE}"',
        f'Start-Process "{EXE}"',
        f'Start-Process -WindowStyle Hidden "{EXE}"',
        f'start "{EXE}"',
        f'saps "{EXE}"',
        f'cmd //c start "" "{EXE}"',
        "/c/Program\\ Files/Docker/Docker/Docker\\ Desktop.exe &",
        '"/c/Program Files/Docker/Docker/Docker Desktop.exe" &',
        'powershell -NoProfile -Command "docker desktop start"',
        "pwsh -c 'docker desktop restart'",
        'bash -c "docker desktop start"',
        'powershell -Command "docker ps; docker desktop start"',
    ):
        _eq(hook.classify(command), hook.DIRECT_START, command)


def _repo_with_config(config):
    repo = tempfile.mkdtemp(dir=_TMP)
    os.makedirs(os.path.join(repo, ".claude"))
    with open(os.path.join(repo, ".claude", "hook-config.json"), "w", encoding="utf-8") as handle:
        if isinstance(config, str):
            handle.write(config)
        else:
            json.dump(config, handle)
    return repo


def test_project_test_commands_reach_docker_without_naming_it():
    repo = _repo_with_config({
        "docker_test_commands": [r"^npm test\b(?!.*-w @x/web)", "(unbalanced"],
        "docker_test_skip": "SKIP_DB",
    })
    subdir = os.path.join(repo, "apps", "api")
    os.makedirs(subdir)
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
    repo = _repo_with_config("{not json")
    _eq(hook.classify("npm test", repo), None, "broken config")
    _eq(hook.classify("docker ps", repo), hook.NEEDS_ENGINE, "docker still detected")


def test_project_config_of_the_wrong_type_is_ignored():
    # A string would be iterated a character at a time, and "^" matches every command.
    repo = _repo_with_config({"docker_test_commands": "^npm test", "docker_test_skip": ["SKIP_DB"]})
    _eq(hook.classify("git status", repo), None, "string pattern list")
    _eq(hook.classify("npm test", repo), None, "string pattern list, its own command")
    _eq(hook.classify("docker desktop start", repo), hook.DIRECT_START, "the block survives a bad skip")
    for config in ("[1, 2]", '"text"', '{"docker_test_commands": [3, "^npm test"]}'):
        repo = _repo_with_config(config)
        expected = hook.NEEDS_ENGINE if "npm test" in config else None
        _eq(hook.classify("npm test", repo), expected, config)


def _run_hook(command, tool="Bash", python_args=()):
    payload = {"tool_name": tool, "tool_input": {"command": command}, "session_id": "s1"}
    return subprocess.run(
        [sys.executable, *python_args, str(SCRIPTS / "pre-tool-use-docker-start.py")],
        input=json.dumps(payload).encode("utf-8"), capture_output=True, check=False,
    )


def test_hook_blocks_a_direct_start_with_a_way_out():
    for tool, command in (("Bash", "docker desktop start"), ("PowerShell", "docker desktop start"),
                          ("Bash", 'powershell -Command "docker desktop start"'),
                          ("PowerShell", f'Start-Process "{EXE}"')):
        proc = _run_hook(command, tool)
        _eq(proc.returncode, 2, f"{tool}: {command}")
        message = proc.stderr.decode("utf-8")
        assert "ADR-158" in message and "--status" in message, message


def test_hook_ignores_an_unrelated_command():
    proc = _run_hook("git status")
    _eq((proc.returncode, proc.stdout, proc.stderr), (0, b"", b""), "unrelated command")


def test_a_non_matching_command_runs_no_subprocess():
    # Run the real hook in a fresh interpreter and list every module it imported:
    # with no subprocess module loaded, nothing on this path could start a process.
    for command in ("git status", "git grep docker", "npm test"):
        proc = _run_hook(command, python_args=("-X", "importtime"))
        _eq((proc.returncode, proc.stdout), (0, b""), command)
        imported = {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.decode("utf-8").splitlines()
                    if line.startswith("import time:")}
        assert "_hookio" in imported, "the import trace is empty, so it proves nothing"
        for module in ("subprocess", "_winsubp", "_docker_lifecycle"):
            assert module not in imported, f"{command}: imported {module}"


# --- Starting ---------------------------------------------------------------


def _ensure(fake):
    return hook.ensure_engine("s1", runner=fake, clock=fake.clock, sleep=fake.sleep)


def test_engine_already_up_is_left_alone_and_stays_user_owned():
    _reset_state()
    fake = FakeDocker(engine=True, backend=True)
    _eq(_ensure(fake), (False, ""), "result")
    assert not fake.ran("schtasks"), "must not start a running engine"
    assert not lifecycle.has_marker(), "a running engine with no marker is the user's"
    assert _lease_exists(), "the command still counts as this session's use"


def test_an_engine_seen_a_moment_ago_is_not_probed_again():
    _reset_state()
    _ensure(FakeDocker(engine=True, backend=True))
    fake = FakeDocker(engine=True, backend=True)
    _eq(_ensure(fake), (False, ""), "result")
    _eq(fake.calls, [], "calls")
    stamp = time.time() - lifecycle.ENGINE_SEEN_SECONDS - 1
    os.utime(os.path.join(lifecycle.state_dir(), "engine-seen"), (stamp, stamp))
    _ensure(fake)
    assert fake.ran("docker", "version"), "probes again once the stamp is old"


def test_start_goes_through_the_scheduled_task_never_directly():
    _reset_state()
    fake = FakeDocker(up_after_polls=3)
    _eq(_ensure(fake), (True, ""), "result")
    assert fake.ran("schtasks", "/Run", lifecycle.START_TASK), fake.calls
    assert not fake.ran("docker", "desktop", "start"), "a session must never start Docker directly"
    assert not lifecycle.has_marker(), "the start task writes the marker, under the lock"


def test_a_start_that_cleared_the_lease_gets_it_back():
    # The start task's orphan-tray path force-stops first, which clears every lease.
    _reset_state()
    fake = FakeDocker(up_after_polls=3, on={"schtasks": lambda: lifecycle.clear_leases()})
    _eq(_ensure(fake), (True, ""), "result")
    assert _lease_exists(), "with no lease, the next tick would stop the engine"


def test_hypervisor_off_reports_the_fix_and_starts_nothing():
    for backend in (False, True):  # A leftover backend must not hide the advice.
        _reset_state()
        fake = FakeDocker(hypervisor=False, backend=backend)
        started, problem = _ensure(fake)
        _eq(started, False, "started")
        assert "SVM" in problem, problem
        assert not fake.ran("schtasks"), "starting would only wedge Docker"


def test_the_watchdog_s_hypervisor_answer_is_reused():
    _reset_state()
    boot = lifecycle.boot_time(time.time())
    os.makedirs(lifecycle.state_dir(), exist_ok=True)
    with open(os.path.join(lifecycle.state_dir(), "health.json"), "w", encoding="utf-8") as handle:
        json.dump({"boot": boot, "hypervisor": False}, handle)
    fake = FakeDocker(hypervisor=True)
    started, problem = _ensure(fake)
    assert "SVM" in problem, problem
    assert not fake.ran("powershell"), "the cached answer for this boot is enough"


def test_a_failed_task_run_clears_nobody_s_ownership():
    _reset_state()
    lifecycle.write_marker()
    lifecycle.touch_lease("other")
    fake = FakeDocker(task_rc=1)
    started, problem = _ensure(fake)
    _eq(started, False, "started")
    assert "register-docker-watchdog.ps1" in problem, problem
    assert lifecycle.has_marker() and _lease_exists("other"), "another session's ownership survives"


def test_a_task_run_that_times_out_still_waits():
    _reset_state()
    fake = FakeDocker(up_after_polls=3, failing=("schtasks",))
    _eq(_ensure(fake), (True, ""), "the task may have launched")


def test_a_start_that_never_answers_ends_inside_the_hook_timeout():
    _reset_state()
    fake = FakeDocker()
    started, problem = _ensure(fake)
    _eq(started, False, "started")
    assert "--status" in problem, problem
    assert fake.elapsed <= 165, f"took {fake.elapsed}s of the hook's 180 with every call at its timeout"


def test_a_stuck_docker_gets_a_short_wait():
    _reset_state()
    _write_unhealthy_since(hook.START_BUDGET_SECONDS + 60)
    fake = FakeDocker(backend=True)
    started, problem = _ensure(fake)
    _eq(started, False, "started")
    assert fake.elapsed <= 15 + 30 + 15 + hook.SHORT_WAIT_SECONDS + 15, fake.elapsed


def test_a_user_start_in_progress_is_not_claimed():
    _reset_state()
    fake = FakeDocker(backend=True, up_after_polls=3)
    _eq(_ensure(fake), (True, ""), "result")
    assert not lifecycle.has_marker(), "the user started this one"
    _reset_state()
    fake = FakeDocker(backend=True)
    _eq(watchdog.start(runner=fake), 0, "the start task sees it starting")
    assert not fake.ran("docker", "desktop", "start") and not lifecycle.has_marker(), fake.calls


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


def test_the_marker_s_own_time_counts_when_no_lease_is_left():
    _reset_state()
    now = time.time()
    lifecycle.write_marker()
    age = lifecycle.newest_lease_age(now + 60)
    assert 59 <= age <= 61, age


def test_lease_names_cannot_escape_the_state_directory():
    _reset_state()
    lifecycle.touch_lease("../../evil")
    assert os.path.exists(os.path.join(lifecycle.state_dir(), "leases", "______evil")), "sanitized"


# --- Watchdog tick ----------------------------------------------------------


def _session_started(lease_age, now, pids=(4242,)):
    lifecycle.write_marker(pids)
    stamp = now - lease_age
    os.utime(os.path.join(lifecycle.state_dir(), "started-by-session"), (stamp, stamp))
    lifecycle.touch_lease("s1", stamp)


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


def test_a_marker_from_an_earlier_engine_is_not_honoured():
    # The user quit a session's Docker and started it again by hand before a tick.
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now, pids=(111,))
    fake = FakeDocker(engine=True, backend=True, backend_pid=4242)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.NOTHING, "action")
    assert not fake.ran("docker", "desktop", "stop"), fake.calls
    assert not lifecycle.has_marker(), "the stale marker is dropped"


def test_an_unbound_marker_binds_to_the_first_engine_it_sees():
    _reset_state()
    now = time.time()
    _session_started(60, now, pids=())
    watchdog.tick(runner=FakeDocker(engine=True, backend=True, backend_pid=4242), now=now)
    _eq(lifecycle.marker_pids(), [4242], "bound")
    age = lifecycle.newest_lease_age(now)
    assert age >= 59, f"binding mustn't reset the marker's time: {age}"


def test_unreadable_backend_ids_stop_nothing():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True, failing=("tasklist",))
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.NOTHING, "action")
    assert not fake.ran("docker", "desktop", "stop"), fake.calls
    assert lifecycle.has_marker(), "kept: the next tick can read them"


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


def test_a_docker_command_during_the_checks_cancels_the_stop():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True, on={"docker ps": lambda: lifecycle.touch_lease("s2")})
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.NOTHING, "action")
    assert not fake.ran("docker", "desktop", "stop"), fake.calls


def test_a_lease_written_during_the_stop_survives_it():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 1, now)
    fake = FakeDocker(engine=True, backend=True,
                      on={"docker desktop stop": lambda: lifecycle.touch_lease("s2", time.time() + 1)})
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.STOP, "action")
    assert _lease_exists("s2") and not _lease_exists("s1"), "only the leases it decided on go"


def test_tick_times_a_wedge_across_ticks_then_force_stops():
    _reset_state()
    now = time.time()
    fake = FakeDocker(tray=True)
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.WAIT, "first sighting")
    _eq(watchdog.tick(runner=fake, now=now + 300), lifecycle.WAIT, "five minutes on")
    assert not fake.ran("docker", "desktop", "stop"), "not yet"
    _eq(watchdog.tick(runner=fake, now=now + lifecycle.WEDGE_SECONDS), lifecycle.FORCE_STOP, "wedge limit on")
    assert fake.ran("docker", "desktop", "stop", "--force"), fake.calls


def test_tick_recovery_resets_the_wedge_clock():
    _reset_state()
    now = time.time()
    watchdog.tick(runner=FakeDocker(tray=True), now=now)
    watchdog.tick(runner=FakeDocker(engine=True, backend=True), now=now + 60)
    _eq(watchdog.tick(runner=FakeDocker(tray=True), now=now + lifecycle.WEDGE_SECONDS),
        lifecycle.WAIT, "a new sighting starts a new clock")


def test_tidy_drops_the_marker_and_keeps_a_fresh_lease():
    _reset_state()
    now = time.time()
    lifecycle.write_marker()
    lifecycle.touch_lease("s1", now - 5)  # A hook whose start task hasn't run yet.
    lifecycle.touch_lease("old", now - lifecycle.IDLE_SECONDS - 5)
    _eq(watchdog.tick(runner=FakeDocker(), now=now), lifecycle.TIDY, "action")
    assert not lifecycle.has_marker(), "no start is under way: starts hold the lock"
    assert _lease_exists("s1") and not _lease_exists("old"), "fresh lease kept, old one gone"


def test_tick_destroys_nothing_when_its_checks_time_out():
    # 2026-11-11 00:45: under memory pressure every check timed out, so a running,
    # session-started Docker read as stopped. The tick cleared its marker, so it was
    # never stopped, and deleted the live backend's sockets.
    _reset_state()
    now = time.time()
    _session_started(13 * 60, now)
    original = lifecycle.clean_stale_sockets

    def fail(*_args, **_kwargs):
        raise AssertionError("cleaned sockets on checks that timed out")

    lifecycle.clean_stale_sockets = fail
    try:
        fake = FakeDocker(engine=True, backend=True, tray=True,
                          failing=("docker version", "tasklist", "docker ps"))
        action = watchdog.tick(runner=fake, now=now)
    finally:
        lifecycle.clean_stale_sockets = original
    _eq(action, lifecycle.UNKNOWN, "action")
    assert lifecycle.has_marker(), "the session's ownership was cleared"


def test_checks_that_keep_timing_out_never_force_stop():
    # The same pressure lasting past the wedge limit must not read as a wedge.
    _reset_state()
    now = time.time()
    fake = FakeDocker(engine=True, backend=True, tray=True, containers=1,
                      failing=("docker version", "tasklist"))
    for minute in range(0, lifecycle.WEDGE_SECONDS // 60 + 15, 5):
        _eq(watchdog.tick(runner=fake, now=now + minute * 60), lifecycle.UNKNOWN, f"minute {minute}")
    assert not fake.ran("docker", "desktop", "stop"), fake.calls
    assert not os.path.exists(os.path.join(lifecycle.state_dir(), "unhealthy-since")), "clock started"


def test_tick_holds_when_docker_ps_times_out():
    _reset_state()
    now = time.time()
    _session_started(lifecycle.IDLE_SECONDS + 60, now)
    fake = FakeDocker(engine=True, backend=True, tray=True, containers=1, failing=("docker ps",))
    _eq(watchdog.tick(runner=fake, now=now), lifecycle.HOLD, "a failed count isn't zero containers")
    assert not fake.ran("docker", "desktop", "stop"), "stopped with containers unknown"


def test_tick_skips_when_a_start_holds_the_lock():
    _reset_state()
    with lifecycle.lifecycle_lock() as held:
        assert held
        fake = FakeDocker(tray=True)
        _eq(watchdog.tick(runner=fake, now=time.time()), "locked", "action")
        _eq(fake.calls, [], "calls")


_HOLDER = """
import os, sys, time
sys.path.insert(0, {scripts!r})
import _docker_lifecycle as lifecycle
with lifecycle.lifecycle_lock() as held:
    print("held" if held else "busy", flush=True)
    time.sleep(60)
"""


def test_a_lock_dies_with_its_process():
    # A crash, a reboot or Task Scheduler's time limit can't leave it held.
    _reset_state()
    holder = subprocess.Popen([sys.executable, "-c", _HOLDER.format(scripts=str(SCRIPTS))],
                              stdout=subprocess.PIPE, text=True)
    try:
        _eq(holder.stdout.readline().strip(), "held", "the other process took it")
        with lifecycle.lifecycle_lock() as held:
            assert not held, "two processes held the lock at once"
        holder.kill()
        holder.wait(timeout=30)
        with lifecycle.lifecycle_lock() as held:
            assert held, "a killed holder's lock wasn't released"
    finally:
        holder.kill()
        holder.stdout.close()


# --- Watchdog start and hypervisor check ------------------------------------


def test_start_force_stops_an_orphan_tray_and_marks_the_new_engine():
    _reset_state()
    _write_unhealthy_since(lifecycle.ORPHAN_TRAY_SECONDS + 5)
    fake = FakeDocker(tray=True, backend_pid=777)
    _eq(watchdog.start(runner=fake), 0, "rc")
    assert fake.ran("--force"), fake.calls
    assert fake.ran("docker", "desktop", "start"), fake.calls
    _eq(lifecycle.marker_pids(), [777], "marked as this start's engine")


def test_start_leaves_a_tray_that_is_just_launching():
    _reset_state()
    fake = FakeDocker(tray=True)
    _eq(watchdog.start(runner=fake), 0, "rc")
    assert not fake.ran("docker", "desktop"), "a user's launch in its first seconds"
    assert not lifecycle.has_marker(), "not claimed"


def test_start_does_nothing_when_up_or_starting():
    for fake in (FakeDocker(engine=True, backend=True), FakeDocker(backend=True)):
        _reset_state()
        _eq(watchdog.start(runner=fake), 0, "rc")
        assert not fake.ran("docker", "desktop", "start"), fake.calls
        assert not lifecycle.has_marker(), "not claimed"


def test_start_without_the_lock_does_nothing():
    _reset_state()
    original = watchdog.START_LOCK_WAIT_SECONDS
    watchdog.START_LOCK_WAIT_SECONDS = 0
    try:
        with lifecycle.lifecycle_lock():
            fake = FakeDocker()
            _eq(watchdog.start(runner=fake), 1, "rc")
    finally:
        watchdog.START_LOCK_WAIT_SECONDS = original
    _eq(fake.calls, [], "calls")


def test_hypervisor_notifies_once_per_boot():
    _reset_state()
    notes = []
    fake = FakeDocker(hypervisor=False)
    note = lambda title, body, runner: notes.append(title)  # noqa: E731
    watchdog.check_hypervisor_once_per_boot(1000, runner=fake, notifier=note)
    watchdog.check_hypervisor_once_per_boot(1000, runner=fake, notifier=note)
    watchdog.check_hypervisor_once_per_boot(1000 + 61, runner=fake, notifier=note)
    _eq(len(notes), 1, "same boot, clock adjusted across a minute")
    watchdog.check_hypervisor_once_per_boot(5000, runner=fake, notifier=note)
    _eq(len(notes), 2, "new boot")


def test_hypervisor_present_stays_quiet_and_unknown_is_asked_again():
    _reset_state()
    notes = []
    watchdog.check_hypervisor_once_per_boot(
        1000, runner=FakeDocker(), notifier=lambda *a: notes.append(a))
    _eq(notes, [], "no notification")
    _reset_state()
    unknown = FakeDocker(failing=("powershell",))
    watchdog.check_hypervisor_once_per_boot(1000, runner=unknown, notifier=lambda *a: notes.append(a))
    _eq(lifecycle.cached_hypervisor(1000), None, "an unknown isn't cached for the boot")
    fake = FakeDocker(hypervisor=False)
    watchdog.check_hypervisor_once_per_boot(1000, runner=fake, notifier=lambda *a: notes.append(a))
    _eq(len(notes), 1, "asked again on the next tick")
    _eq(lifecycle.hypervisor_present(lambda *_a, **_k: (1, "")), None, "unreadable")


def test_a_failing_hypervisor_step_still_runs_the_tick():
    ran = []
    originals = watchdog.check_hypervisor_once_per_boot, watchdog.tick

    def broken(_boot):
        raise OSError("disk full")

    watchdog.check_hypervisor_once_per_boot = broken
    watchdog.tick = lambda **_k: ran.append(True)
    try:
        _eq(watchdog.main(["docker-watchdog.py", "--tick"]), 0, "rc")
    finally:
        watchdog.check_hypervisor_once_per_boot, watchdog.tick = originals
    _eq(ran, [True], "tick ran")


# --- Stale sockets ----------------------------------------------------------


def test_clean_removes_only_old_af_unix_sockets():
    root = tempfile.mkdtemp(dir=_TMP)
    for name in ("dockerInference", "ordinary.txt", "stuck.sock", "fresh.sock"):
        open(os.path.join(root, name), "w", encoding="utf-8").close()
    confirmed = time.time()

    def fake_lstat(path):
        if path.endswith("stuck.sock"):
            raise OSError(1920, "The file cannot be accessed by the system")
        tag = 0 if path.endswith("ordinary.txt") else lifecycle.IO_REPARSE_TAG_AF_UNIX
        made = confirmed + 1 if path.endswith("fresh.sock") else confirmed - 3600
        return types.SimpleNamespace(st_reparse_tag=tag, st_mtime=made)

    removed, failed = lifecycle.clean_stale_sockets(confirmed, [root, os.path.join(root, "absent")],
                                                    lstat=fake_lstat)
    _eq([os.path.basename(p) for p in removed], ["dockerInference"], "removed")
    _eq(len(failed), 1, "failed")
    assert "stuck.sock" in failed[0], failed
    assert os.path.exists(os.path.join(root, "ordinary.txt")), "other files stay"
    assert os.path.exists(os.path.join(root, "fresh.sock")), "a backend launched since owns it"


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


# --- Registration script ----------------------------------------------------


def test_register_script_parses_and_keeps_its_task_settings():
    path = SCRIPTS / "register-docker-watchdog.ps1"
    parse = (
        "$e = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{path}', [ref]$null, [ref]$e); $e.Count"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", parse],
                          capture_output=True, text=True, check=False, timeout=120)
    _eq((proc.returncode, proc.stdout.strip()), (0, "0"), f"parse errors: {proc.stderr}")
    text = path.read_text(encoding="utf-8")
    for needle in ("-RunLevel Limited", "-LogonType Interactive", "-Hidden", "-MultipleInstances IgnoreNew",
                   "-AtLogOn", "-RepetitionInterval (New-TimeSpan -Minutes 5)", "pyw.exe", "--tick",
                   "--start", "Export-ScheduledTask"):
        assert needle in text, f"missing {needle}"
    for banned in ("-Verb RunAs", "-NoExit", "Start-Process"):
        assert banned not in text, f"ADR-041: {banned}"


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
