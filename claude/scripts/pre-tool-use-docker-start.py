#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- starts Docker Desktop when a session runs a
`docker` command, by way of a scheduled task. See ADR-158 and dev-env#1278.

Starting through Task Scheduler is the point. A backend started directly from a
session crashes on its next start, because a process in the Claude app's tree
can't delete Docker's old socket file (see `_docker_lifecycle`). So this hook
also blocks `docker desktop start`/`restart`, and launching Docker Desktop.exe,
from a session. Those commands are enumerated, and the block names the way out.

Everything else fails open: if Docker can't be started, the hook tells the user
and lets the command run, so Docker's own error reaches the session.

Stdin JSON shape (PreToolUse):
  {"tool_name": "Bash" or "PowerShell", "tool_input": {"command": ...}, "session_id": ...}
"""
import contextlib
import json
import os
import re
import shlex
import sys
import time

from _hookio import SHELL_TOOLS, load_hook_input, read_command, read_cwd, split_top_level
import _docker_lifecycle as lifecycle
import _hookout
import _hookutil

NEEDS_ENGINE = "needs-engine"
DIRECT_START = "direct-start"

POLL_SECONDS = 3
# The hook's own timeout in settings is 180 seconds.
START_WAIT_SECONDS = 165

# Cheap first pass, so most commands never reach the parser.
_MENTIONS_DOCKER = re.compile(r"docker", re.IGNORECASE)
_LEADING = re.compile(r"^\s*[&(]*\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")
_DOCKER_CLI = re.compile(r"^[\"']?(?:docker|docker-compose)(?:\.exe)?[\"']?(?:\s+(.*))?$", re.IGNORECASE)
_DESKTOP_EXE = re.compile(
    r"^(?:Start-Process\s+(?:-FilePath\s+)?)?[\"']?[^\"']*Docker Desktop\.exe", re.IGNORECASE
)
_LAUNCH_PREFIX = re.compile(r"^\s*(?:&\s*)?(?:Start-Process\b.*)?[\"']", re.IGNORECASE)

# First words that don't need the engine.
_NO_ENGINE_WORDS = {"context", "desktop", "help", "login", "logout"}
_NO_ENGINE_FLAGS = {"--version", "-v", "--help", "-h"}
_DIRECT_START_WORDS = {"start", "restart"}


def _words(rest: str) -> list:
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.split()


def classify_segment(segment: str):
    """Return NEEDS_ENGINE, DIRECT_START, or None for one top-level segment."""
    line = segment.split("\n", 1)[0]
    text = _LEADING.sub("", line, count=1).strip()
    if _LAUNCH_PREFIX.match(line) and _DESKTOP_EXE.match(text):
        return DIRECT_START
    match = _DOCKER_CLI.match(text)
    if not match:
        return None
    words = _words(match.group(1) or "")
    if not words or any(word in _NO_ENGINE_FLAGS for word in words):
        return None
    first = words[0].lower()
    if first == "desktop":
        return DIRECT_START if len(words) > 1 and words[1].lower() in _DIRECT_START_WORDS else None
    return None if first in _NO_ENGINE_WORDS else NEEDS_ENGINE


def project_test_commands(cwd: str):
    """(patterns, skip_marker) from the nearest .claude/hook-config.json above cwd.

    Some test commands reach Docker without naming it, like lifting-logbook's
    `npm test`, which starts Postgres through Testcontainers. A project lists
    those as `docker_test_commands` (regexes, searched against each top-level
    segment) and can name `docker_test_skip`, text that turns them off when the
    command contains it. A missed command costs one retry: the test reports
    Docker as unreachable, and the session's next `docker` command starts it.
    """
    path = os.path.abspath(cwd) if cwd else ""
    while path:
        config = os.path.join(path, ".claude", "hook-config.json")
        if os.path.isfile(config):
            with contextlib.suppress(OSError, ValueError):
                with open(config, encoding="utf-8") as handle:
                    data = json.load(handle)
                patterns = []
                for text in data.get("docker_test_commands") or []:
                    with contextlib.suppress(re.error, TypeError):
                        patterns.append(re.compile(text))
                return patterns, data.get("docker_test_skip") or ""
            return [], ""
        parent = os.path.dirname(path)
        path = "" if parent == path else parent
    return [], ""


def classify(command: str, cwd: str = ""):
    """A direct start outranks everything else in the same command."""
    patterns, skip = project_test_commands(cwd)
    if skip and skip in command:
        patterns = []
    if not patterns and not _MENTIONS_DOCKER.search(command):
        return None
    segments = split_top_level(command, split_pipe=True)
    found = {classify_segment(seg) for seg in segments}
    if DIRECT_START in found:
        return DIRECT_START
    if NEEDS_ENGINE in found:
        return NEEDS_ENGINE
    for segment in segments:
        text = _LEADING.sub("", segment.split("\n", 1)[0], count=1).strip()
        if any(pattern.search(text) for pattern in patterns):
            return NEEDS_ENGINE
    return None


def ensure_engine(session_id, runner=lifecycle.run, clock=time.monotonic, sleep=time.sleep):
    """Returns (started, problem). Both empty when the engine was already up."""
    lifecycle.touch_lease(session_id)
    if lifecycle.engine_up(runner):
        return False, ""
    if not lifecycle.backend_running(runner):
        if not lifecycle.hypervisor_present(runner):
            return False, lifecycle.HYPERVISOR_FIX
        # Written before the start so the watchdog can't read this as user-started.
        lifecycle.write_marker()
        code, _ = runner(["schtasks", "/Run", "/TN", lifecycle.START_TASK], timeout=15)
        if code != 0:
            lifecycle.clear_ownership()
            return False, (
                f"Couldn't run the {lifecycle.START_TASK} scheduled task. "
                f"Register it with: {lifecycle.REGISTER_HINT}"
            )
    deadline = clock() + START_WAIT_SECONDS
    while clock() < deadline:
        if lifecycle.engine_up(runner):
            return True, ""
        sleep(POLL_SECONDS)
    return False, (
        f"Docker didn't answer within {START_WAIT_SECONDS} seconds. "
        "Details: py -3 C:/Users/brown/.claude/scripts/docker-watchdog.py --status"
    )


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-docker-start")
    data = load_hook_input(SHELL_TOOLS)
    if data is None:
        sys.exit(0)
    kind = classify(read_command(data), read_cwd(data))
    if kind is None:
        sys.exit(0)
    if kind == DIRECT_START:
        _hookout.emit_block(
            "[docker-start] BLOCKED: don't start Docker Desktop from a session. A backend started "
            "from the Claude app's process tree can't delete its own socket file, so its next "
            "start crashes with 'initializing Inference manager ... The file cannot be accessed "
            "by the system' (ADR-158). Run the docker command you need instead: this hook starts "
            "Docker through Task Scheduler. To see why it isn't running: "
            "py -3 C:/Users/brown/.claude/scripts/docker-watchdog.py --status"
        )
    started, problem = ensure_engine(data.get("session_id"))
    if problem:
        _hookout.emit_advisory("PreToolUse", f"[docker-start] {problem}", audience="user")
    if started:
        _hookout.emit_advisory("PreToolUse", "[docker-start] Docker Desktop started.", audience="user")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- fail open: a hook bug never blocks a shell command
        sys.exit(0)
