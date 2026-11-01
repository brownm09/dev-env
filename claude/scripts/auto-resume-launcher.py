#!/usr/bin/env python3
"""
Open interrupted, opted-in desktop sessions after the Claude app starts
(dev-env#1182, ADR-152).

When the app restarts after a crash or a reboot, it starts a session's CLI process only
when that session is opened. A session that was cut off mid-turn therefore just sits
there. Its ``SessionStart`` hook, ``session-start-auto-resume.py``, never fires, so
nothing continues it. This script closes that gap. It runs from the
"ClaudeAutoResumeLauncher" scheduled task, which ``register-auto-resume.ps1`` registers
to run at sign-in and then every few minutes. Each run:

1. Finds the app's main ``Claude.exe`` process and its start time. If the app is not
   running, or this app instance was already handled, the run exits right away. That
   makes the frequent trigger cheap.
2. Waits until the app has been up for ``--settle`` seconds (default 90), so a slow
   start is not scanned half-loaded. A later run picks it up if this one is too early.
3. Collects the opted-in sessions whose transcript ended mid-turn *before* this app
   instance started, inside ``maxAgeHours``, with wakes left, and with no CLI process
   already running for them. A running session is never opened, so it is never sent a
   second turn.
4. Opens each one with ``claude://code/continue?session=local_<id>``. That is the app's
   OS entry-point deep link, and it starts the session's CLI. The hook then decides
   whether to wake the model and makes the final checks.
5. Confirms each open started a CLI process, logs the outcome to
   ``Documents\\LOGS\\claude-auto-resume_<date>.jsonl``, and records the app start time
   so this instance is not handled twice.

This script never sends a message itself. The hook does that, after its own opt-in and
settle checks, so the two can't both send to the same session. ``--dry-run`` lists what
it would open without opening anything or recording state.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _auto_resume as ar  # noqa: E402

SOURCE = "launcher"
STATE_PATH = ar.STATE_DIR / "launcher-state.json"
OPEN_GAP_SECONDS = 10
VERIFY_SECONDS = 45
_RESUME_ARG = re.compile(r"--(?:resume|session-id)[= ]\"?([0-9a-fA-F-]{36})")

_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" | ForEach-Object { "
    "[pscustomobject]@{ path = $_.ExecutablePath; cmd = $_.CommandLine; "
    "created = $_.CreationDate.ToUniversalTime().ToString('o') } } | ConvertTo-Json -Compress"
)


def list_processes() -> list[dict]:
    """Every process named claude.exe (case-insensitive: the app and its CLI children)."""
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS],
        capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = r.stdout.decode("utf-8", "replace").strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def _is_cli(p: dict) -> bool:
    return "claude-code" in (p.get("path") or "").lower().replace("\\", "/")


def app_start(procs: list[dict]) -> float | None:
    """Start time of the app's main process: not a CLI child, no ``--type=`` switch."""
    starts = [ar.parse_ts(p.get("created")) for p in procs
              if not _is_cli(p) and "--type=" not in (p.get("cmd") or "")]
    starts = [s for s in starts if s is not None]
    return min(starts) if starts else None


def running_cli_ids(procs: list[dict]) -> set[str]:
    ids = set()
    for p in procs:
        if _is_cli(p):
            ids.update(m.lower() for m in _RESUME_ARG.findall(p.get("cmd") or ""))
    return ids


def candidates(cfg: dict, sessions: list[dict], started: float, running: set[str],
               now: float, projects: Path | None = None) -> list[dict]:
    out = []
    for s in sessions:
        cli = s.get("cliSessionId") or ""
        if not str(s.get("sessionId") or "").startswith("local_") or not ar.opted_in(cfg, s):
            continue
        path = ar.find_transcript(cli, projects)
        if path is None:
            continue
        v = ar.classify(ar.read_tail(path))
        ts = ar.parse_ts(v["timestamp"])
        if v["state"] != "interrupted" or ts is None or ts >= started:
            continue
        if now - ts > float(cfg["maxAgeHours"]) * 3600 or cli.lower() in running:
            continue
        if not ar.attempts_left(cli, cfg, now=now):
            continue
        out.append({"sessionId": s["sessionId"], "cliSessionId": cli, "title": s.get("title"),
                    "detail": v["detail"], "lastMessageAt": v["timestamp"]})
    return out


def _load_state() -> dict:
    try:
        d = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="list, open nothing, record nothing")
    ap.add_argument("--force", action="store_true", help="rescan even if this app start was handled")
    ap.add_argument("--settle", type=float, default=90.0, help="seconds the app must be up first")
    args = ap.parse_args(argv)

    cfg = ar.load_config()
    if cfg.get("enabled") is not True:
        if args.dry_run:
            print(f"Disabled: {ar.CONFIG_PATH} is missing or not enabled.")
        return 0
    procs = list_processes()
    started = app_start(procs)
    if started is None:
        if args.dry_run:
            print("The Claude app is not running.")
        return 0
    started_iso = _dt.datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds")
    state = _load_state()
    if state.get("handledAppStart") == started_iso and not args.force:
        return 0
    now = time.time()
    if now - started < args.settle:
        return 0

    found = candidates(cfg, ar.load_app_sessions(), started, running_cli_ids(procs), now)
    ar.log(SOURCE, "scan", appStartedAt=started_iso, candidates=len(found), dryRun=args.dry_run)
    for c in found:
        line = f"{c['sessionId']}  {c['title']!r}  stopped at {c['lastMessageAt']} during {c['detail']}"
        if args.dry_run:
            print("would open: " + line)
            continue
        os.startfile(f"claude://code/continue?session={c['sessionId']}")
        ar.log(SOURCE, "open", **c)
        time.sleep(OPEN_GAP_SECONDS)
    if args.dry_run:
        print(f"{len(found)} session(s) would be opened (app started {started_iso}).")
        return 0

    if found:
        time.sleep(VERIFY_SECONDS)
        running = running_cli_ids(list_processes())
        for c in found:
            ok = c["cliSessionId"].lower() in running
            ar.log(SOURCE, "started" if ok else "not_started", sessionId=c["sessionId"],
                   cliSessionId=c["cliSessionId"],
                   **({} if ok else {"hint": "the deep link started no CLI process; the app "
                                              "may have its Code deep links turned off"}))
    state["handledAppStart"] = started_iso
    _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # never leave a scheduled task's error unrecorded
        ar.log(SOURCE, "error", error=f"{type(e).__name__}: {e}")
        sys.exit(1)
