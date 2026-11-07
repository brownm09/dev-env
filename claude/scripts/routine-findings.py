#!/usr/bin/env python3
"""routine-findings.py: keep a routine's finding when its push can't be delivered (dev-env#1308).

A scheduled session has no Remote Control transport, so PushNotification usually
answers "Mobile push not sent (Remote Control inactive)." and the finding reaches
nobody. The source and conditions of that message are on dev-env#1308. See ADR-160.

Two modes, one file, because they share the location and format:

  py -3 ~/.claude/scripts/routine-findings.py save --routine NAME --message TEXT --push-result TEXT

    Run by a routine after each push, with the tool's result text. A result in
    DELIVERED_RESULTS saves nothing. Anything else is saved, including an empty
    result (the tool failed or wasn't available) and any wording this script doesn't
    know, so a changed message can only cause an extra finding, never a lost one.
    Exits 1 if the finding could not be saved.

  (no arguments) UserPromptSubmit hook

    Claims pending findings by moving them from pending/ to shown/, then injects them
    as additionalContext on stdout, which is the stream this event forwards on exit 0
    (ADR-027). The move is what makes it once only: a finding that's been moved can't
    be shown again, and if two sessions race, the rename succeeds for only one of them.
    Automated (XML-prefixed) prompts are skipped, so a scheduled run can't consume
    what a person is meant to see. That's why this isn't a SessionStart hook: that
    event has no prompt to tell the two apart. Fails open.

Findings live in ~/.claude/routine-findings/{pending,shown}/, one JSON file each,
outside the journal and machine-local. shown/ is kept as the record of what was
shown. ROUTINE_FINDINGS_DIR overrides the location for tests.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

import _hookutil

# The two results the PushNotification tool returns when it hands a push to the
# transport (Claude Code 2.1.317). It gets no receipt from the phone, so this is as
# close to "delivered" as a routine can observe.
DELIVERED_RESULTS = {
    "Mobile push requested.",
    "Terminal notification sent. Mobile push requested.",
}

# Heuristic limits that keep one injection well under a screenful and clear of
# Claude Code's hook-output cap. Anything over MAX_SHOWN stays pending for the next
# prompt; a message over MAX_MESSAGE_CHARS is cut in the cue, and shown/ has it whole.
MAX_SHOWN = 20
MAX_MESSAGE_CHARS = 300

# Mirrors idle-refresher.py and session-mode-prompt.py: scheduled tasks and CI
# monitors send XML-tagged prompts, and people's prompts don't start that way.
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")


def findings_dir() -> Path:
    override = os.environ.get("ROUTINE_FINDINGS_DIR")
    return Path(override) if override else Path.home() / ".claude" / "routine-findings"


def is_delivered(push_result: str) -> bool:
    return (push_result or "").strip() in DELIVERED_RESULTS


def save_finding(routine: str, message: str, push_result: str, base: Path, now=None) -> Path:
    """Write one finding atomically to pending/ and return its path."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    pending = base / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", routine) or "routine"
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    path = pending / f"{stamp}-{slug}-{os.getpid()}.json"
    # A .tmp name first, because the hook reads only *.json and must never see half a file.
    tmp = path.with_suffix(".tmp")
    record = {
        "routine": routine,
        "message": message,
        "push_result": push_result,
        "saved_at": now.isoformat(timespec="seconds"),
    }
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def claim_pending(base: Path, limit: int = MAX_SHOWN) -> tuple[list[Path], int]:
    """Move up to *limit* pending findings to shown/, oldest first.

    Returns (claimed paths in shown/, how many stayed pending). A rename that fails
    means another session claimed that file first, so it's skipped.
    """
    pending = base / "pending"
    try:
        names = sorted(p.name for p in pending.glob("*.json"))
    except OSError:
        return [], 0
    if not names:
        return [], 0
    shown = base / "shown"
    shown.mkdir(parents=True, exist_ok=True)
    claimed = []
    for name in names[:limit]:
        target = shown / name
        try:
            os.rename(pending / name, target)
        except OSError:
            continue
        claimed.append(target)
    return claimed, max(0, len(names) - limit)


def describe(path: Path) -> str:
    """One line for the cue. An unreadable file is still reported, by its path."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        routine = record["routine"]
        message = str(record["message"])
        saved_at = record["saved_at"]
    except (OSError, ValueError, KeyError, TypeError):
        return f"- (unreadable finding: {path})"
    if len(message) > MAX_MESSAGE_CHARS:
        message = f"{message[:MAX_MESSAGE_CHARS]}... (full text: {path})"
    return f"- {routine} at {saved_at}: {message}"


def build_context(claimed: list[Path], remaining: int, base: Path) -> str:
    lines = "\n".join(describe(p) for p in claimed)
    more = f" {remaining} more will follow on the next prompt." if remaining else ""
    return (
        f"[routine-findings] {len(claimed)} finding(s) from scheduled routines could not "
        "be delivered by push. Open your reply by showing them to the user, each with its "
        "routine and time, then address their message. They won't be shown again. If another "
        f"session started this one, repeat them in your report too.{more} Copies are in "
        f"{base / 'shown'}.\n{lines}"
    )


def cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Save a routine's finding if its push wasn't delivered.")
    sub = parser.add_subparsers(dest="command", required=True)
    save = sub.add_parser("save")
    save.add_argument("--routine", required=True)
    save.add_argument("--message", required=True)
    save.add_argument(
        "--push-result",
        required=True,
        help="The PushNotification tool's result text; empty if the tool failed or wasn't available.",
    )
    args = parser.parse_args(argv)
    if is_delivered(args.push_result):
        print("routine-findings: push handed off; nothing saved")
        return 0
    try:
        path = save_finding(args.routine, args.message, args.push_result, findings_dir())
    except OSError as exc:
        print(f"routine-findings: could not save the finding: {exc}", file=sys.stderr)
        return 1
    print(f"routine-findings: push not delivered; saved {path}")
    return 0


def main():
    _hookutil.record_heartbeat("routine-findings")
    try:
        data = json.loads(sys.stdin.read() or "{}")
        prompt = data.get("prompt", "") if isinstance(data, dict) else ""
        if _AUTOMATED_PREFIX.match(prompt or ""):
            sys.exit(0)
        base = findings_dir()
        claimed, remaining = claim_pending(base)
        if not claimed:
            sys.exit(0)
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": build_context(claimed, remaining, base),
            }
        }
        # json.dumps escapes non-ASCII, so a finding survives the cp1252 hook-stdout pipe.
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
    except Exception:
        # Fail open: this must never block a prompt. A claimed finding stays in shown/.
        pass
    sys.exit(0)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            sys.exit(cli(sys.argv[1:]))
        main()
    except Exception:
        sys.exit(0)
