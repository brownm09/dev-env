#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- starts Docker Desktop when a session runs a
`docker` command, by way of a scheduled task. See ADR-158 and dev-env#1278.

Purpose: PreToolUse (Bash, PowerShell) hook that starts Docker Desktop through a
scheduled task when a session runs `docker` or a project-listed test command.

Starting through Task Scheduler is the point. A backend started directly from a
session crashes on its next start, because a process in the Claude app's tree
can't delete Docker's old socket file (see `_docker_lifecycle`). So this hook
also blocks `docker desktop start`/`restart`, and launching Docker Desktop.exe,
from a session, including inside a `powershell -Command`, `bash -c` or
`cmd /c` string. The block names the way out.

Everything else fails open: if Docker can't be started, the hook tells the user
and lets the command run, so Docker's own error reaches the session.

This runs on every shell call, so a command that doesn't match imports nothing
that starts processes: `_docker_lifecycle` loads only after a match.

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
import _hookout
import _hookutil

NEEDS_ENGINE = "needs-engine"
DIRECT_START = "direct-start"

POLL_SECONDS = 3
# Every check and the wait share this budget. The last engine probe can take 15 s
# more, which still ends inside the hook's 180-second timeout in settings.
START_BUDGET_SECONDS = 150
# Once the watchdog has seen Docker stuck for longer than a normal start, waiting the
# whole budget on every command only delays the error.
SHORT_WAIT_SECONDS = 30

# Cheap first pass, so most commands never reach the parser.
_MENTIONS_DOCKER = re.compile(r"docker", re.IGNORECASE)
_LEADING = re.compile(r"^\s*[&(]*\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")
# `docker`, or a path ending in it: quoted, unquoted, or with escaped spaces.
_DOCKER_CLI = re.compile(
    r"""^(?:"[^"]*[\\/]|'[^']*[\\/]|(?:\\\s|[^\s"'])*[\\/])?"""
    r"""(?:docker|docker-compose)(?:\.exe)?["']?(?:\s+(.*))?$""",
    re.IGNORECASE | re.DOTALL,
)
_DESKTOP_EXE = re.compile(r"Docker(?:\\?\s|%20)Desktop\.exe", re.IGNORECASE)
_FIRST_WORD = re.compile(r""""[^"]*"|'[^']*'|(?:\\\s|\S)+""")
# A shell given a command string: powershell -Command "...", bash -c "...", cmd /c ...
_NESTED_SHELL = re.compile(
    r"""^(?:[^\s"']*[\\/])?(?:powershell|pwsh|bash|sh|cmd)(?:\.exe)?\s+"""
    r"""(?:.*?\s)?(?:-\w*c|-command|/c|//c)\s+(.+)$""",
    re.IGNORECASE | re.DOTALL,
)
# Command words that launch the program they're given.
_LAUNCHERS = {"start-process", "start", "saps", "cmd", "cmd.exe", "explorer", "explorer.exe",
              "invoke-item", "ii"}

# Global options that take a value, so the word after them isn't the subcommand.
_GLOBAL_WITH_VALUE = {"--config", "-c", "--context", "-H", "--host", "-l", "--log-level",
                      "--tlscacert", "--tlscert", "--tlskey"}
_VERSION_OR_HELP = {"--version", "-v", "--help", "-h"}
# Subcommands that don't need the engine.
_NO_ENGINE_WORDS = {"context", "desktop", "help", "login", "logout"}
_DIRECT_START_WORDS = {"start", "restart"}
_MAX_NESTING = 3


def _words(rest: str) -> list:
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.split()


def _subcommand(words: list):
    """(subcommand, the words after it), skipping global options. (None, []) for
    `docker --version` or `docker --help`, which don't need the engine."""
    i = 0
    while i < len(words) and words[i].startswith("-"):
        flag = words[i].split("=", 1)[0]
        if flag in _VERSION_OR_HELP:
            return None, []
        i += 2 if flag in _GLOBAL_WITH_VALUE and "=" not in words[i] else 1
    return (words[i].lower(), words[i + 1:]) if i < len(words) else (None, [])


def _asks_for_help(sub: str, rest: list) -> bool:
    """`--help`/`-h` among the options right after the subcommand. For `run` and
    `create`, `-h` is the container's hostname."""
    for word in rest:
        if not word.startswith("-"):
            return False
        if word == "--help" or (word == "-h" and sub not in ("run", "create")):
            return True
    return False


def _classify_docker(rest: str):
    sub, words = _subcommand(_words(rest))
    if sub is None:
        return None
    if sub == "desktop":
        starts = bool(words) and words[0].lower() in _DIRECT_START_WORDS
        return DIRECT_START if starts and not {"--help", "-h"} & set(words[1:]) else None
    if sub in _NO_ENGINE_WORDS or _asks_for_help(sub, words):
        return None
    return NEEDS_ENGINE


def _launches_docker_desktop(text: str) -> bool:
    match = _FIRST_WORD.match(text)
    if not match or not _DESKTOP_EXE.search(text):
        return False
    first = match.group(0).strip("\"'")
    return first.lower() in _LAUNCHERS or bool(_DESKTOP_EXE.search(first))


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def classify_segment(segment: str, depth: int = 0):
    """Return NEEDS_ENGINE, DIRECT_START, or None for one top-level segment."""
    line = segment.split("\n", 1)[0]
    text = _LEADING.sub("", line, count=1).strip()
    nested = _NESTED_SHELL.match(text)
    if nested and depth < _MAX_NESTING:
        inner = classify_command(_unquote(nested.group(1)), depth + 1)
        if inner:
            return inner
    if _launches_docker_desktop(text):
        return DIRECT_START
    match = _DOCKER_CLI.match(text)
    return _classify_docker(match.group(1) or "") if match else None


def classify_command(command: str, depth: int = 0):
    """A direct start outranks a command that needs the engine."""
    found = {classify_segment(seg, depth) for seg in split_top_level(command, split_pipe=True)}
    if DIRECT_START in found:
        return DIRECT_START
    return NEEDS_ENGINE if NEEDS_ENGINE in found else None


def project_test_commands(cwd: str):
    """(patterns, skip_marker) from the nearest .claude/hook-config.json above cwd.

    Some test commands reach Docker without naming it, like lifting-logbook's
    `npm test`, which starts Postgres through Testcontainers. A project lists
    those as `docker_test_commands` (a list of regexes, searched against each
    top-level segment) and can name `docker_test_skip`, a string that turns them
    off when the command contains it. A value of another type is ignored. A
    missed command costs one retry: the test reports Docker as unreachable, and
    the session's next `docker` command starts it.
    """
    path = os.path.abspath(cwd) if cwd else ""
    while path:
        config = os.path.join(path, ".claude", "hook-config.json")
        if os.path.isfile(config):
            data = {}
            with contextlib.suppress(OSError, ValueError):
                with open(config, encoding="utf-8") as handle:
                    data = json.load(handle)
            if not isinstance(data, dict):
                return [], ""
            texts = data.get("docker_test_commands")
            skip = data.get("docker_test_skip")
            patterns = []
            for text in texts if isinstance(texts, list) else []:
                if isinstance(text, str):
                    with contextlib.suppress(re.error):
                        patterns.append(re.compile(text))
            return patterns, skip if isinstance(skip, str) else ""
        parent = os.path.dirname(path)
        path = "" if parent == path else parent
    return [], ""


def classify(command: str, cwd: str = ""):
    """Direct starts are found before project config is read, so a broken config
    can't turn the block off."""
    if _MENTIONS_DOCKER.search(command):
        kind = classify_command(command)
        if kind:
            return kind
    patterns, skip = project_test_commands(cwd)
    if not patterns or (skip and skip in command):
        return None
    for segment in split_top_level(command, split_pipe=True):
        text = _LEADING.sub("", segment.split("\n", 1)[0], count=1).strip()
        if any(pattern.search(text) for pattern in patterns):
            return NEEDS_ENGINE
    return None


def ensure_engine(session_id, runner=None, clock=time.monotonic, sleep=time.sleep):
    """Returns (started, problem). Both empty when the engine was already up."""
    import _docker_lifecycle as lifecycle  # Only after a match: it loads subprocess.

    runner = runner or lifecycle.run
    deadline = clock() + START_BUDGET_SECONDS
    lifecycle.touch_lease(session_id)
    if lifecycle.engine_seen_recently(time.time()):
        return False, ""
    if lifecycle.engine_up(runner):
        lifecycle.mark_engine_seen()
        return False, ""
    present = lifecycle.cached_hypervisor(lifecycle.boot_time(time.time()))
    if present is None:
        present = lifecycle.hypervisor_present(runner)
    if present is False:
        return False, lifecycle.HYPERVISOR_FIX
    # The task decides under its lock whether to start: an engine that is already
    # starting, the user's or another session's, is left alone and isn't claimed.
    code, _ = runner(["schtasks", "/Run", "/TN", lifecycle.START_TASK], timeout=15)
    if code not in (0, lifecycle.RUN_FAILED):
        return False, (
            f"Couldn't run the {lifecycle.START_TASK} scheduled task. "
            f"Register it with: {lifecycle.REGISTER_HINT}"
        )
    if lifecycle.unhealthy_age(time.time()) > START_BUDGET_SECONDS:
        deadline = min(deadline, clock() + SHORT_WAIT_SECONDS)
    while clock() < deadline:
        if lifecycle.engine_up(runner):
            # A start that cleared an orphan tray also cleared this session's lease.
            lifecycle.touch_lease(session_id)
            lifecycle.mark_engine_seen()
            return True, ""
        sleep(POLL_SECONDS)
    return False, (
        "Docker didn't answer in time. A first start after an update can take several "
        "minutes, so it may still be starting. Details: "
        "py -3 C:/Users/brown/.claude/scripts/docker-watchdog.py --status"
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
