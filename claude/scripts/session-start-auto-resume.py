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

1. The session is a desktop session that ``~/.claude/auto-resume.json`` opts in
   (``_auto_resume.opted_in``; terminal and headless resumes have no app metadata and
   never match).
2. The transcript's last turn is ``interrupted``, or it finished but is
   ``waiting_on_background``: a background task it started never reported back, and
   with the old process gone it never will (``_auto_resume.classify``, dev-env#1185). A
   finished turn, one paused on AskUserQuestion or ExitPlanMode, one the user stopped,
   one ending on an API error, and a local slash command such as ``/compact`` are left
   alone. The "stopped" task-notification a resumed CLI writes for a dead background
   task, and the one turn it sets off, are set aside first. They are the CLI's own
   doing, and that turn often ends with nothing done.
3. The interrupted message is older than this hook's own start. A prompt the user just
   sent, which is what started this CLI, is never mistaken for an interrupted one.
4. It is less than ``maxAgeHours`` old, and this session has had fewer than
   ``maxAttempts`` wakes since the current Claude app instance started. The cap resets
   with every app restart or boot, and still stops a session that crashes on every
   reopen.
5. After waiting ``settleSeconds``, no turn has started in the transcript
   (``_auto_resume.turn_started``). Any new user entry counts (a prompt, or a queued or
   cross-session message), and so does any other non-bookkeeping line, unless the turn
   is the CLI's own answer to a stop notice. A turn already running would get a second
   message.

Every decision on an interrupted transcript is logged to
``Documents\\LOGS\\claude-auto-resume_<date>.jsonl``, and so is an invalid config or
an error. A clean transcript, which is the usual case, is not logged. The hook fails
open: an error exits 0 and wakes nothing.
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
# The interrupted message must be at least this much older than the hook's start.
START_MARGIN_SECONDS = 5


def build_message(verdict: dict, boot: float) -> str:
    when = verdict.get("timestamp") or "an unknown time"
    ts = ar.parse_ts(verdict.get("timestamp"))
    if ts is not None:
        when = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    boot_txt = _dt.datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M")
    lock = ("a stale git `index.lock` (remove one only if it is older than this boot, "
            f"{boot_txt}, and no git process is running)")
    if verdict["state"] == "waiting_on_background":
        return (
            f"[auto-resume] At {when} you were waiting on {verdict['detail']}. Since then "
            "Windows restarted or the Claude app was force-closed, so that task died and "
            "its result will never arrive. Nobody has typed anything since. Check its output "
            f"file for partial results and look for {lock}, then rerun the task and carry on. "
            "If it no longer matters, say so briefly and stop."
        )
    return (
        f"[auto-resume] Your previous turn was cut off at {when}, during {verdict['detail']}, "
        "most likely by a Windows restart or a forced close of the Claude app. Nobody has "
        "typed anything since. Check what the interruption left behind first: a command "
        f"that may have half-run, a partly written file, or {lock}. Then carry on with the "
        "task from where it stopped. If the task was already finished, say so briefly and stop."
    )


def decide(payload: dict, cfg: dict, sleep=time.sleep, now=time.time,
           instance_start=ar.instance_start) -> tuple[str, dict]:
    """Return ``(decision, info)``; decision is "wake" or a reason to skip."""
    started = now()
    sid = str(payload.get("session_id") or "")
    path = Path(str(payload["transcript_path"])) if payload.get("transcript_path") \
        else ar.find_transcript(sid)
    if payload.get("source") != "resume" or not sid or path is None:
        return "not_a_resume", {}
    if cfg.get("enabled") is not True:
        return ("config_invalid", {"cliSessionId": sid, "reason": cfg["invalid"]}) \
            if cfg.get("invalid") else ("disabled", {})
    offset = ar.file_size(path)
    verdict = ar.classify(ar.read_tail(path), restarted_at=started)
    info = {"cliSessionId": sid, "state": verdict["state"], "detail": verdict["detail"],
            "lastMessageAt": verdict["timestamp"]}
    if verdict["state"] not in ar.RESUMABLE:
        return "not_interrupted", info
    ts = ar.parse_ts(verdict["timestamp"])
    if ts is None or ts > started - START_MARGIN_SECONDS:
        return "not_interrupted", info  # this CLI started for a message that just arrived
    meta = ar.meta_for(sid)
    info.update(sessionId=meta.get("sessionId"), title=meta.get("title"))
    if not ar.opted_in(cfg, meta):
        return "not_opted_in", info
    if started - ts > float(cfg["maxAgeHours"]) * 3600:
        return "too_old", info
    if not ar.attempts_left(sid, cfg, since=instance_start(), now=started):
        return "attempts_exhausted", info
    sleep(float(cfg["settleSeconds"]))
    if ar.turn_started(ar.read_since(path, offset), verdict["stopNotice"]):
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
    try:
        ar.record_attempt(info["cliSessionId"], cfg)
    except OSError as e:
        # The cap is a backstop; losing one count is better than dropping this wake.
        info["attemptNotRecorded"] = f"{type(e).__name__}: {e}"
    ar.log(SOURCE, "wake", **info)
    sys.stderr.write(message)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        ar.log(SOURCE, "error", error=f"{type(e).__name__}: {e}")
        sys.exit(0)
