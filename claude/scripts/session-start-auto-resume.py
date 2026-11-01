#!/usr/bin/env python3
"""
SessionStart (``resume``) hook: continue a turn that a reboot or app crash cut off
(dev-env#1182, ADR-152).

When the Claude desktop app is killed, or Windows restarts, every session that was
mid-turn stays stopped. The app reopens them but does not continue the turn, so someone
had to type "continue" into each one. This hook is wired with ``asyncRewake: true``: it
runs in the background, and exiting 2 wakes the model with this hook's stderr as the
message, which starts a turn with no user input. That was verified against Claude Code
2.1.309 in the desktop's stream-json mode.

The hook fires whenever a session's CLI process starts with ``--resume``: when the user
opens or messages a session, or when ``auto-resume-launcher.py`` opens it after an app
restart. It wakes the model only when all of these hold:

1. ``~/.claude/auto-resume.json`` is enabled and opts this session in
   (``_auto_resume.opted_in``).
2. The transcript's last turn is ``interrupted`` (``_auto_resume.classify``). A finished
   turn, one paused on AskUserQuestion or ExitPlanMode, one the user stopped, or one
   ending on an API error is left alone.
3. The interruption is less than ``maxAgeHours`` old, and this session has fewer than
   ``maxAttempts`` wakes inside that window.
4. After waiting ``settleSeconds``, the last transcript message is unchanged. If the user
   typed something, or the app delivered a queued message, that turn is already running,
   and waking the model would send a second one.

Every decision on an interrupted transcript is logged to
``Documents\\LOGS\\claude-auto-resume_<date>.jsonl``. A clean transcript, which is the
usual case, is not logged. The hook fails open: any error exits 0 and wakes nothing.
"""
from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _auto_resume as ar  # noqa: E402
import _hookinput  # noqa: E402
import _hookutil  # noqa: E402

SOURCE = "hook"


def build_message(verdict: dict, boot: float | None) -> str:
    when = verdict.get("timestamp") or "an unknown time"
    ts = ar.parse_ts(verdict.get("timestamp"))
    if ts is not None:
        when = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    boot_txt = (_dt.datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M")
                if boot else "the last boot")
    return (
        f"[auto-resume] Your previous turn was cut off at {when}, during {verdict['detail']}, "
        "most likely by a Windows restart or a forced close of the Claude app. Nobody has "
        "typed anything since. Check what the interruption left behind first: a command "
        "that may have half-run, a partly written file, or a stale git `index.lock` "
        f"(remove one only if it is older than this boot, {boot_txt}, and no git process "
        "is running). Then carry on with the task from where it stopped. If the task was "
        "already finished, say so briefly and stop."
    )


def decide(payload: dict, cfg: dict, sleep=time.sleep, now=time.time) -> tuple[str, dict]:
    """Return ``(decision, info)``; decision is "wake" or a reason to skip."""
    sid = str(payload.get("session_id") or "")
    path = Path(str(payload.get("transcript_path") or "")) if payload.get("transcript_path") \
        else ar.find_transcript(sid)
    if payload.get("source") != "resume" or not sid or path is None:
        return "not_a_resume", {}
    if cfg.get("enabled") is not True:
        return "disabled", {}
    verdict = ar.classify(ar.read_tail(path))
    info = {"cliSessionId": sid, "state": verdict["state"], "detail": verdict["detail"],
            "lastMessageAt": verdict["timestamp"]}
    if verdict["state"] != "interrupted":
        return "not_interrupted", info
    meta = ar.meta_for(sid)
    info.update(sessionId=meta.get("sessionId"), title=meta.get("title"))
    if not ar.opted_in(cfg, {**meta, "cwd": meta.get("cwd") or payload.get("cwd")}):
        return "not_opted_in", info
    ts = ar.parse_ts(verdict["timestamp"])
    if ts is None or now() - ts > float(cfg["maxAgeHours"]) * 3600:
        return "too_old", info
    if not ar.attempts_left(sid, cfg, now=now()):
        return "attempts_exhausted", info
    sleep(float(cfg["settleSeconds"]))
    if ar.classify(ar.read_tail(path))["uuid"] != verdict["uuid"]:
        return "activity_during_settle", info
    info["message"] = build_message(verdict, ar.boot_time())
    return "wake", info


def main() -> int:
    _hookutil.record_heartbeat("session-start-auto-resume")
    payload = _hookinput.load_hook_input()
    if payload is None:
        return 0
    cfg = ar.load_config()
    decision, info = decide(payload, cfg)
    if decision in ("not_a_resume", "disabled", "not_interrupted"):
        return 0
    message = info.pop("message", None)
    if decision != "wake":
        ar.log(SOURCE, "skip", reason=decision, **info)
        return 0
    ar.record_attempt(info["cliSessionId"], cfg)
    ar.log(SOURCE, "wake", **info)
    sys.stderr.write(message)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
