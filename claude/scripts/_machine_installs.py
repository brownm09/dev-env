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
  missing or turned off), `skipped` (named in `skip_installs` in ~/.claude/machine.json, for a
  difference that is deliberate) or `unknown` (a task couldn't be read, which is never reported
  as missing).
- `query_task()` reads one task with `schtasks /Query /TN <name> /XML`, about 0.2 s each.
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

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

import _winsubp  # noqa: F401  -- UTF-8 decoding + no console flash for child processes (ADR-007)

CLAUDE_HOME = Path.home() / ".claude"
SCRIPTS_DIR = CLAUDE_HOME / "scripts"  # the junction every register script defaults to
MANIFEST = Path(__file__).resolve().parent.parent / "machine-installs.json"
MACHINE_JSON = CLAUDE_HOME / "machine.json"
NUDGE_STAMP = CLAUDE_HOME / "scratch" / "machine-installs-nudge.txt"

OK, PROBLEM, SKIPPED, UNKNOWN = "ok", "problem", "skipped", "unknown"
QUERY_TIMEOUT = 10.0
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


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_task_xml(text: str) -> TaskInfo | None:
    """The first Exec action and the Enabled setting from `schtasks /XML` output. None when the
    text isn't task XML or has no Exec action."""
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    sections = {_local(child.tag): child for child in root}
    enabled = True  # Task Scheduler omits <Settings><Enabled> when it is true
    for child in sections.get("Settings", []):
        if _local(child.tag) == "Enabled":
            enabled = (child.text or "").strip().lower() != "false"
    for action in sections.get("Actions", []):
        if _local(action.tag) == "Exec":
            fields = {_local(c.tag): (c.text or "").strip() for c in action}
            if fields.get("Command"):
                return TaskInfo(enabled, fields["Command"], fields.get("Arguments", ""))
    return None


def query_task(name: str, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> TaskState:
    try:
        proc = run(["schtasks", "/Query", "/TN", name, "/XML"], capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=QUERY_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return TaskState(None, None, f"schtasks could not run: {type(exc).__name__}")
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else proc.stdout or ""
    err = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else proc.stderr or ""
    if proc.returncode != 0:
        # schtasks exits 1 both for a missing task and for one it may not read; its message is
        # localized, so it is passed on rather than matched.
        first = next((line.strip() for line in err.splitlines() if line.strip()), f"exit {proc.returncode}")
        return TaskState(False, None, f"not registered ({first})")
    info = parse_task_xml(out)
    if info is None:
        return TaskState(None, None, "schtasks returned no readable action")
    return TaskState(True, info, "")


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
        config = entry.get("config")
        if config:
            cstate = configs.get(config, "unreadable")
            if cstate == "missing":
                problems.append(f"~/.claude/{config} is missing")
            elif cstate == "off":
                problems.append(f'~/.claude/{config} has "enabled": false')
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
    manifest: Path = MANIFEST,
    query: Callable[[str], TaskState] = query_task,
    claude_home: Path = CLAUDE_HOME,
) -> list[Finding]:
    """Read this machine's state and evaluate it. Raises ManifestError."""
    installs = load_manifest(manifest)
    skips = read_skips(claude_home / "machine.json")
    wanted = [e for e in installs if e["name"] not in skips]
    tasks = {t["name"]: query(t["name"]) for e in wanted for t in e["tasks"]}
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
    # No ~/.claude/scripts means this home has no dev-env install to check (a test's temp home).
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


def install(findings: list[Finding], installs: list[dict], scripts_dir: Path) -> bool:
    """Run the register script of each install with a problem, except outside_app ones.
    True when every one it ran exited 0."""
    by_name = {e["name"]: e for e in installs}
    ok = True
    for f in findings:
        if f.status != PROBLEM:
            continue
        entry = by_name[f.name]
        if entry.get("outside_app"):
            print(f"Not run here: {f.name} writes HKCU, which a Claude desktop session can't make real. Run it yourself:")
            print(f"  {register_command(entry, SCRIPTS_DIR)}")
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
    parser = argparse.ArgumentParser(description="Check this machine's dev-env scheduled tasks against machine-installs.json.")
    parser.add_argument("--install", action="store_true", help="run the register script of each install with a problem")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("Scheduled tasks are Windows-only; nothing to check here.")
        return 0
    try:
        installs = load_manifest()
        findings = check()
        if not args.install:
            _print(findings)
            return 1 if any(f.status == PROBLEM for f in findings) else 0
        if not any(f.status == PROBLEM for f in findings):
            print("Every machine-local install is in place.")
            _print(findings)
            return 0
        ran_ok = install(findings, installs, Path(__file__).resolve().parent)
        print()
        print("After registering:")
        after = check()
        _print(after)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    outside = {e["name"] for e in installs if e.get("outside_app")}
    leftover = [f for f in after if f.status == PROBLEM and f.name not in outside]
    return 0 if ran_ok and not leftover else 1


if __name__ == "__main__":
    sys.exit(main())
