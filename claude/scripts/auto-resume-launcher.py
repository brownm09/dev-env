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

1. Finds the app's main process
   (``...\\WindowsApps\\Claude_*\\app\\Claude.exe`` with no ``--type=`` switch) and its
   start time. If the app is not running, or this app instance is already finished
   with, the run exits right away. That makes the frequent trigger cheap.
2. Waits until the app has been up for ``SETTLE_SECONDS``, so a slow start is not
   scanned half-loaded. A later run picks it up if this one is too early.
3. Collects the opted-in sessions whose transcript, *before* this app instance started,
   ended mid-turn, or ended waiting on a background task that died with the old
   process (dev-env#1185). Each must be inside ``maxAgeHours``, have wakes left for this
   app instance, and have no CLI process already running. A running session is never
   opened, so it is never sent a second turn.
4. Opens each one with ``claude://code/continue?session=local_<id>``. That is the app's
   OS entry-point deep link, and it starts the session's CLI. The hook then decides
   whether to wake the model and makes the final checks. A session that has already
   been opened ``MAX_OPENS`` times for this app instance is not opened again.
5. Confirms each open started a CLI, and logs the outcome to
   ``Documents\\LOGS\\claude-auto-resume_<date>.jsonl``. The app instance is marked
   finished only when nothing is left to open, so a session the app was too busy to
   open is tried again on the next run. A run that fails is retried the same way, up to
   ``MAX_ERRORS`` times.

This script never sends a message itself. The hook does that, after its own opt-in and
settle checks, so the two can't both send to the same session. ``--dry-run`` lists what
it would open without opening anything or recording state. To make it reconsider an app
instance it has already finished with, delete
``~/.claude/scratch/auto-resume/launcher-state.json``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _auto_resume as ar  # noqa: E402

SOURCE = "launcher"
STATE_PATH = ar.STATE_DIR / "launcher-state.json"
SETTLE_SECONDS = 90
OPEN_GAP_SECONDS = 10
VERIFY_SECONDS = 45
MAX_OPENS = 3
MAX_ERRORS = 3


def candidates(cfg: dict, sessions: list[dict], started: float, running: set[str],
               now: float, projects: Path | None = None) -> list[dict]:
    out = []
    for s in sessions:
        cli = s.get("cliSessionId") or ""
        if not ar.opted_in(cfg, s) or cli.lower() in running:
            continue
        path = ar.find_transcript(cli, projects)
        if path is None:
            continue
        tail = ar.read_tail(path)
        v = ar.classify(tail, restarted_at=started)
        ts = ar.parse_ts(v["timestamp"])
        if v["state"] not in ar.RESUMABLE or ts is None or ts >= started:
            continue
        if now - ts > float(cfg["maxAgeHours"]) * 3600 \
                or not ar.attempts_left(cli, cfg, since=started, now=now) \
                or ar.consecutive_wakes(tail) >= ar.MAX_CONSECUTIVE_WAKES:
            continue
        out.append({"sessionId": s["sessionId"], "cliSessionId": cli, "title": s.get("title"),
                    "detail": v["detail"], "lastMessageAt": v["timestamp"]})
    return out


def plan_opens(found: list[dict], state: dict) -> tuple[list[dict], bool]:
    """Pick the candidates still under MAX_OPENS for this instance; True = instance done."""
    opens = state.get("opens", {})
    todo = [c for c in found if opens.get(c["sessionId"], 0) < MAX_OPENS]
    return todo, not todo


def run(args, state: dict, started: float, started_iso: str, procs: list[dict]) -> bool:
    """One pass over this app instance. Returns True when the instance is finished."""
    cfg = ar.load_config()
    if cfg.get("enabled") is not True:
        if cfg.get("invalid"):
            ar.log(SOURCE, "config_invalid", reason=cfg["invalid"])
        if args.dry_run:
            print(cfg.get("invalid") or f"Disabled: {ar.CONFIG_PATH} is missing or not enabled.")
        return True
    found = candidates(cfg, ar.load_app_sessions(), started, ar.running_cli_ids(procs), time.time())
    todo, done = plan_opens(found, state)
    if args.dry_run:
        for c in todo:
            print(f"would open: {c['sessionId']}  {c['title']!r}  stopped at "
                  f"{c['lastMessageAt']} during {c['detail']}")
        print(f"{len(todo)} session(s) would be opened (app started {started_iso}).")
        return done
    ar.log(SOURCE, "scan", appStartedAt=started_iso, candidates=len(found), opening=len(todo))
    if done:
        if found:
            ar.log(SOURCE, "gave_up", sessions=[c["sessionId"] for c in found], maxOpens=MAX_OPENS)
        return True
    opens = state.setdefault("opens", {})
    for c in todo:
        opens[c["sessionId"]] = opens.get(c["sessionId"], 0) + 1
        try:
            os.startfile(f"claude://code/continue?session={c['sessionId']}")
            ar.log(SOURCE, "open", attempt=opens[c["sessionId"]], **c)
        except OSError as e:
            ar.log(SOURCE, "open_failed", error=f"{type(e).__name__}: {e}", **c)
        time.sleep(OPEN_GAP_SECONDS)
    time.sleep(VERIFY_SECONDS)
    running = ar.running_cli_ids(ar.list_processes())
    for c in todo:
        ok = c["cliSessionId"].lower() in running
        ar.log(SOURCE, "started" if ok else "not_started", sessionId=c["sessionId"],
               cliSessionId=c["cliSessionId"],
               **({} if ok else {"note": f"will try again next run, up to {MAX_OPENS} opens"}))
    return False  # the next run rescans: started sessions drop out, the rest retry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="list, open nothing, record nothing")
    args = ap.parse_args(argv)

    procs = ar.list_processes()
    started = ar.app_start(procs)
    if started is None:
        if args.dry_run:
            print("The Claude app is not running.")
        return 0
    started_iso = _dt.datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds")
    state = ar.read_json(STATE_PATH)
    if state.get("appStart") != started_iso:
        state = {"appStart": started_iso}
    if state.get("done") or time.time() - started < SETTLE_SECONDS:
        return 0
    try:
        state["done"] = run(args, state, started, started_iso, procs)
    except Exception as e:
        state["errors"] = state.get("errors", 0) + 1
        state["done"] = state["errors"] >= MAX_ERRORS
        ar.log(SOURCE, "error", error=f"{type(e).__name__}: {e}", errors=state["errors"],
               givingUp=state["done"])
    if not args.dry_run:
        ar.write_json_atomic(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # the process query failed before any state existed
        # Logged at most hourly: a query that always fails would otherwise log every run.
        err_path = ar.STATE_DIR / "launcher-error.json"
        if time.time() - ar.read_json(err_path).get("at", 0) > 3600:
            ar.log(SOURCE, "error", error=f"{type(e).__name__}: {e}")
            ar.write_json_atomic(err_path, {"at": time.time()})
        sys.exit(1)
