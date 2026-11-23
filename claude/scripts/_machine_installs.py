#!/usr/bin/env python3
"""Which machine-local installs this machine has, against claude/machine-installs.json.

Purpose: checks (and with --install, registers) the scheduled tasks the register-*.ps1
scripts create, for dev-env-doctor.py, setup.sh and hook-liveness-check.py's daily nudge.

dev-env#1397. The links setup.sh makes come from git, so every machine gets them. The scheduled
tasks the `claude/scripts/register-*.ps1` scripts create don't: each is a step someone has to
remember on each machine, and on LETHEON three of four had never been run. The manifest lists
every installer, and this module answers the question for one machine:

- `evaluate()` is the pure decision. Each install is `ok`, a `problem` (a task missing, disabled,
  or running a script other than the junctioned `~/.claude/scripts/<script>`, or its config file
  missing or unreadable), `skipped` (named in `skip_installs` in ~/.claude/machine.json, or its
  config file has `"enabled": false`, which is how a register script's `-Unregister` turns it
  off: both are deliberate) or `unknown` (a task couldn't be read, which is never reported as
  missing).
- `query_tasks()` reads every task in one PowerShell run through the Task Scheduler COM API,
  2 to 5 s on LETHEON, with one timeout below the hook's. Only "not found" (0x80070002) counts
  as missing.
- `py -3 _machine_installs.py --install` runs the register script of each install with a
  problem, then checks again. An `outside_app` install (one that writes HKCU, which a Claude
  desktop session can't make real) is never run here: its command is printed to run from a
  terminal you open yourself.
- `py -3 _machine_installs.py` alone prints the same report and changes nothing.

Fail direction: everything here only reads, except --install. A manifest that can't be read, is
empty, or lists no task raises `ManifestError`, so a caller can't mistake "nothing extracted" for
"nothing missing" (ADR-144).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

import _winsubp  # noqa: F401  -- UTF-8 decoding + no console flash for child processes (ADR-007)

CLAUDE_HOME = Path.home() / ".claude"
SCRIPTS_DIR = CLAUDE_HOME / "scripts"  # the junction every register script defaults to
MANIFEST = Path(__file__).resolve().parent.parent / "machine-installs.json"
MACHINE_JSON = CLAUDE_HOME / "machine.json"
NUDGE_STAMP = CLAUDE_HOME / "scratch" / "machine-installs-nudge.txt"

OK, PROBLEM, SKIPPED, UNKNOWN = "ok", "problem", "skipped", "unknown"
QUERY_TIMEOUT = 20.0  # the one Task Scheduler query; hook-liveness-check.py's timeout is 30 s
REGISTER_TIMEOUT = 180.0


class ManifestError(Exception):
    """The manifest couldn't be read, or holds nothing to check."""


class TaskInfo(NamedTuple):
    enabled: bool
    command: str
    arguments: str


class TaskState(NamedTuple):
    found: bool | None  # True: read; False: not registered; None: couldn't tell
    info: TaskInfo | None
    detail: str  # why found is False or None


class Finding(NamedTuple):
    status: str  # OK / PROBLEM / SKIPPED / UNKNOWN
    name: str
    detail: str
    fix: str  # the command that fixes a PROBLEM, else ""


# ------------------------------------------------------------------ manifest and machine.json


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"could not read {path}: {exc}") from exc
    installs = data.get("installs") if isinstance(data, dict) else None
    if not isinstance(installs, list) or not installs:
        raise ManifestError(f"{path} lists no installs")
    for entry in installs:
        tasks = entry.get("tasks") if isinstance(entry, dict) else None
        if not (isinstance(entry, dict) and entry.get("name") and entry.get("register")
                and isinstance(tasks, list) and tasks
                and all(isinstance(t, dict) and t.get("name") and t.get("script") for t in tasks)):
            raise ManifestError(f"{path} has an entry without a name, register script and tasks: {entry!r}")
    return installs


def load_skips(raw: bytes | None) -> set[str]:
    """`skip_installs` from ~/.claude/machine.json. Unreadable or absent means no skips: the
    doctor's routine-host line already reports a broken machine.json."""
    if raw is None:
        return set()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return set()
    skips = data.get("skip_installs") if isinstance(data, dict) else None
    return {s for s in skips if isinstance(s, str)} if isinstance(skips, list) else set()


def read_skips(path: Path = MACHINE_JSON) -> set[str]:
    try:
        return load_skips(path.read_bytes())
    except OSError:
        return load_skips(None)


# ------------------------------------------------------------------ tasks


# HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND), which ITaskFolder::GetTask raises for a task that
# isn't registered. It is the only error read as "missing": unlike schtasks' message it isn't
# localized, and any other error (access denied, a stopped service) means the task's state is
# unknown.
TASK_NOT_FOUND = -2147024894  # 0x80070002


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def query_script(names: Iterable[str]) -> str:
    """PowerShell that reads each named task through the Task Scheduler COM API and prints one
    JSON array. Non-ASCII is escaped to \\uXXXX, so the output is ASCII whatever the console
    code page. schtasks can't be used for this: it writes its XML in the OEM code page, where on
    2026-11-22 an em dash in a task's path came back as "-", and its full listing took over 30 s."""
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$svc = New-Object -ComObject Schedule.Service; $svc.Connect(); "
        "$folder = $svc.GetFolder('\\'); "
        f"$rows = @(foreach ($n in @({', '.join(_ps_quote(n) for n in names)})) {{ "
        "try { $t = $folder.GetTask($n); "
        "$exec = @($t.Definition.Actions | Where-Object { $_.Type -eq 0 })[0]; "
        "[pscustomobject]@{ name = $n; found = $true; enabled = [bool]$t.Enabled; "
        "command = [string]$exec.Path; arguments = [string]$exec.Arguments } } "
        "catch { $e = $_.Exception; while ($e.InnerException) { $e = $e.InnerException }; "
        "[pscustomobject]@{ name = $n; found = $false; hresult = $e.HResult; error = $e.Message } } }); "
        "$json = ConvertTo-Json -InputObject $rows -Compress; "
        "[regex]::Replace($json, '[^\\x00-\\x7F]', { param($m) '\\u{0:x4}' -f [int][char]$m.Value })"
    )


def _text(data: bytes | str | None) -> str:
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data or ""


def parse_query_output(names: list[str], stdout: str) -> dict[str, TaskState]:
    """Each name's TaskState from `query_script`'s JSON. A name the output doesn't answer for is
    unknown, never missing."""
    def unknown(why: str) -> dict[str, TaskState]:
        return {n: TaskState(None, None, why) for n in names}

    try:
        rows = json.loads(stdout)
    except ValueError:
        return unknown("the Task Scheduler query returned no readable result")
    if not isinstance(rows, list):
        return unknown("the Task Scheduler query returned no readable result")
    states: dict[str, TaskState] = {}
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if name not in names:
            continue
        if row.get("found") is True:
            if row.get("command"):
                info = TaskInfo(bool(row.get("enabled")), row["command"], row.get("arguments") or "")
                states[name] = TaskState(True, info, "")
            else:
                states[name] = TaskState(None, None, "it has no program to run")
        elif row.get("hresult") == TASK_NOT_FOUND:
            states[name] = TaskState(False, None, "not registered")
        else:
            hresult = row.get("hresult")
            code = f" (0x{hresult & 0xFFFFFFFF:08X})" if isinstance(hresult, int) else ""
            states[name] = TaskState(None, None, f"{(row.get('error') or 'an error').strip()}{code}")
    for name in names:
        states.setdefault(name, TaskState(None, None, "the Task Scheduler query did not answer for it"))
    return states


def query_tasks(names: Iterable[str], run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> dict[str, TaskState]:
    """Every named task's state from one PowerShell run, so the whole check has one timeout."""
    names = list(dict.fromkeys(names))
    if not names:
        return {}
    try:
        proc = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", query_script(names)],
                   capture_output=True, stdin=subprocess.DEVNULL, timeout=QUERY_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {n: TaskState(None, None, f"the Task Scheduler query failed: {type(exc).__name__}") for n in names}
    if proc.returncode != 0:
        err = _text(proc.stderr)
        first = next((line.strip() for line in err.splitlines() if line.strip()), f"exit {proc.returncode}")
        return {n: TaskState(None, None, f"the Task Scheduler query failed: {first}") for n in names}
    return parse_query_output(names, _text(proc.stdout))


_PY_TOKEN = re.compile(r'"([^"]+?\.py)"|(\S+?\.py)\b', re.IGNORECASE)


def script_paths(arguments: str) -> list[str]:
    return [quoted or bare for quoted, bare in _PY_TOKEN.findall(arguments or "")]


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Equal after following links: the junctioned ~/.claude/scripts path and the dev-env
    checkout it points into are the same script; a worktree's copy is not."""
    def norm(p: str | Path) -> str:
        return os.path.normcase(os.path.normpath(os.path.realpath(str(p))))

    return norm(a) == norm(b)


# ------------------------------------------------------------------ the decision


def register_command(entry: dict, scripts_dir: Path = SCRIPTS_DIR) -> str:
    return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{scripts_dir / entry["register"]}"'


def config_state(path: Path) -> str:
    """'ok', 'missing', 'off' (its "enabled" is false) or 'unreadable'."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "unreadable"
    if isinstance(data, dict) and data.get("enabled") is False:
        return "off"
    return "ok"


def evaluate(
    installs: Iterable[dict],
    tasks: dict[str, TaskState],
    skips: set[str],
    configs: dict[str, str],
    scripts_dir: Path = SCRIPTS_DIR,
) -> list[Finding]:
    """One Finding per install. `tasks` maps task name to its state, `configs` maps a config
    file name (relative to ~/.claude) to `config_state()`."""
    findings: list[Finding] = []
    for entry in installs:
        name = entry["name"]
        if name in skips:
            findings.append(Finding(SKIPPED, name, "skipped on this machine by skip_installs in ~/.claude/machine.json", ""))
            continue
        config = entry.get("config")
        if config and configs.get(config) == "off":
            # How the register script's -Unregister leaves it: a deliberate opt-out, which
            # --install must not undo by re-running the script.
            findings.append(Finding(
                SKIPPED, name,
                f'turned off: ~/.claude/{config} has "enabled": false. To turn it back on: {register_command(entry, scripts_dir)}',
                "",
            ))
            continue
        problems: list[str] = []
        unknown: list[str] = []
        for task in entry["tasks"]:
            state = tasks.get(task["name"], TaskState(None, None, "not queried"))
            expected = scripts_dir / task["script"]
            if state.found is None:
                unknown.append(f"{task['name']}: {state.detail}")
            elif not state.found:
                problems.append(f"{task['name']} is {state.detail}")
            else:
                runs = script_paths(state.info.arguments)
                if not any(_same_file(p, expected) for p in runs):
                    problems.append(
                        f"{task['name']} runs {', '.join(runs) or repr(state.info.arguments)}, not {expected}"
                    )
                elif not state.info.enabled:
                    problems.append(f"{task['name']} is disabled")
        if config:
            cstate = configs.get(config, "unreadable")
            if cstate == "missing":
                problems.append(f"~/.claude/{config} is missing")
            elif cstate == "unreadable":
                problems.append(f"~/.claude/{config} is not readable JSON")
        fix = register_command(entry, scripts_dir)
        if entry.get("outside_app"):
            fix += "  (from a terminal you open yourself, not a Claude desktop session: it writes HKCU)"
        if problems:
            findings.append(Finding(PROBLEM, name, "; ".join(problems + unknown), fix))
        elif unknown:
            findings.append(Finding(UNKNOWN, name, "could not read " + "; ".join(unknown), ""))
        else:
            runs = "; ".join(f"{t['name']} runs {scripts_dir / t['script']}" for t in entry["tasks"])
            findings.append(Finding(OK, name, runs, ""))
    return findings


def check(
    installs: list[dict] | None = None,
    query: Callable[[list[str]], dict[str, TaskState]] = query_tasks,
    claude_home: Path = CLAUDE_HOME,
) -> list[Finding]:
    """Read this machine's state and evaluate it. `installs` defaults to the manifest, which
    raises ManifestError when it can't be read."""
    installs = load_manifest() if installs is None else installs
    skips = read_skips(claude_home / "machine.json")
    wanted = [e for e in installs if e["name"] not in skips]
    tasks = query([t["name"] for e in wanted for t in e["tasks"]])
    configs = {e["config"]: config_state(claude_home / e["config"]) for e in wanted if e.get("config")}
    return evaluate(installs, tasks, skips, configs, claude_home / "scripts")


# ------------------------------------------------------------------ the daily nudge


def nudge_due(stamp: Path, today: str) -> bool:
    try:
        return stamp.read_text(encoding="utf-8").strip() != today
    except OSError:
        return True


def mark_nudged(stamp: Path, today: str) -> None:
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(today, encoding="utf-8")
    except OSError:
        pass


def format_nudge(findings: list[Finding]) -> str:
    """The advisory for the problems among `findings`, or "" when there are none."""
    problems = [f for f in findings if f.status == PROBLEM]
    if not problems:
        return ""
    lines = [f"[machine-installs] {len(problems)} machine-local install(s) are missing or wrong on this machine (dev-env#1397):"]
    for f in problems:
        lines.append(f"  - {f.name}: {f.detail}")
        lines.append(f"    fix: {f.fix}")
    lines.append(
        "If a difference is deliberate, add the name to \"skip_installs\" in ~/.claude/machine.json. "
        "Full report: py -3 ~/.claude/scripts/dev-env-doctor.py"
    )
    return "\n".join(lines)


def daily_nudge(
    stamp: Path = NUDGE_STAMP,
    today: str | None = None,
    run_check: Callable[[], list[Finding]] = check,
    scripts_dir: Path = SCRIPTS_DIR,
) -> str:
    """The advisory text, at most once per calendar day per machine. "" when not due, when
    nothing is wrong, or when the check itself fails (the doctor reports that one)."""
    today = today or datetime.date.today().isoformat()
    # No ~/.claude/scripts means setup.sh never linked this home, so it has no installs to check.
    if os.name != "nt" or not scripts_dir.exists() or not nudge_due(stamp, today):
        return ""
    mark_nudged(stamp, today)
    try:
        return format_nudge(run_check())
    except Exception:  # noqa: BLE001 -- an advisory never breaks the prompt
        return ""


# ------------------------------------------------------------------ CLI


def _print(findings: list[Finding]) -> None:
    for f in findings:
        print(f"{f.status:<8} {f.name}: {f.detail}")
        if f.fix:
            print(f"         fix: {f.fix}")


def install(findings: list[Finding], installs: list[dict], scripts_dir: Path = SCRIPTS_DIR) -> bool:
    """Run the register script of each install with a problem, except outside_app ones, from
    the junctioned ~/.claude/scripts, the same path each fix line names. True when every one it
    ran exited 0."""
    by_name = {e["name"]: e for e in installs}
    ok = True
    for f in findings:
        if f.status != PROBLEM:
            continue
        entry = by_name[f.name]
        if entry.get("outside_app"):
            print(f"Not run here: {f.name} writes HKCU, which a Claude desktop session can't make real. Run it yourself:")
            print(f"  {register_command(entry, scripts_dir)}")
            continue
        script = scripts_dir / entry["register"]
        print(f"Registering {f.name}: {script}")
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=REGISTER_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"  failed: {type(exc).__name__}")
            ok = False
            continue
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.strip():
                print(f"  {line}")
        if proc.returncode != 0:
            print(f"  failed: exit {proc.returncode}")
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    import argparse  # here, not at the top: the hook that imports this module never parses arguments

    parser = argparse.ArgumentParser(description="Check this machine's dev-env scheduled tasks against machine-installs.json.")
    parser.add_argument("--install", action="store_true", help="run the register script of each install with a problem")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("Scheduled tasks are Windows-only; nothing to check here.")
        return 0
    try:
        installs = load_manifest()
        findings = check(installs)
        if not args.install:
            _print(findings)
            return 1 if any(f.status == PROBLEM for f in findings) else 0
        if not any(f.status == PROBLEM for f in findings):
            print("Every machine-local install is in place.")
            _print(findings)
            return 0
        ran_ok = install(findings, installs)
        print()
        print("After registering:")
        after = check(installs)
        _print(after)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    outside = {e["name"] for e in installs if e.get("outside_app")}
    leftover = [f for f in after if f.status == PROBLEM and f.name not in outside]
    return 0 if ran_ok and not leftover else 1


if __name__ == "__main__":
    sys.exit(main())
