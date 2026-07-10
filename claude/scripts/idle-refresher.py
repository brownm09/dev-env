#!/usr/bin/env python3
"""idle-refresher.py

UserPromptSubmit hook: when the user returns to a session after a long idle
gap, inject a cue telling Claude to open its reply with a brief refresher
(what we were working on, current state, pending to-dos/tiles) before
addressing the new prompt. This is the code half of the "Open with a refresher
after a long idle gap" rule in the global CLAUDE.md "Session Summaries & Tile
Tracking" section — Claude cannot observe elapsed idle time on its own, so a
hook has to measure the wall-clock gap and inject the cue. See
docs/adr/095-session-boundary-summaries-and-idle-refresher.md and dev-env#655.

Output contract: stdout JSON `{"hookSpecificOutput": {"hookEventName":
"UserPromptSubmit", "additionalContext": "..."}}` + exit 0 — the same
additionalContext injection session-mode-prompt.py uses (ADR-027). The prompt
is NOT erased and the user does not need to re-submit; the cue is added context
Claude sees before it answers.

Measuring the gap — anchor on the last ASSISTANT record's timestamp, not the
last record of any type. The user's just-submitted prompt (and a preceding
queue-operation record) are appended to the transcript around submit time, so
"the last record" would be ~now and the gap would always be ~0. The last
assistant record is the end of Claude's previous turn — the true idle anchor —
and its absence cleanly means "first prompt / no prior turn", which is exactly
when the refresher should be skipped. A *resumed* session (--continue/--resume)
after a long break DOES have prior assistant records, so it fires correctly,
which is a primary intended case.

Skips (all exit 0, silent):
  - automated/XML-prefixed prompts (scheduled tasks, CI monitors) — no human
    returning to orient;
  - the first prompt of a session (no prior assistant turn);
  - gap at or below the threshold (default 60 min; per-project override
    `idle_refresher_minutes` in `.claude/hook-config.json`).

Fail-open: any error exits 0 and emits nothing — a refresher cue is a nicety
and must never block or disrupt the user's prompt. The injected text is kept
ASCII-only so it survives Claude Code's cp1252-encoded hook-stdout pipe on
Windows (the vanishing-output failure class posttooluse-inert-advisory.py
guards against). Stateless by design: the gap is self-limiting (once the user
is active, inter-prompt gaps fall below the threshold), so no per-session
marker is needed to avoid re-firing.

Reading the transcript: this hook only ever needs the single last assistant
record, so it sources one via _hookutil.iter_records_reverse (a bounded tail
read, chunked from the end of the file) instead of _hookutil.load_records (a
full parse) -- a multi-MB transcript costs a couple of chunk reads here, not
a full parse on every prompt submit (dev-env#679, ADR-090 Amendment 1).
last_activity_epoch takes an already most-recent-first iterable for exactly
this reason -- see its docstring.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time

import _hookutil

DEFAULT_MINUTES = 60
CONFIG_FILE = ".claude/hook-config.json"

# Automated triggers use XML-tagged prompts; human prompts never start with <tag>.
# Mirrors session-mode-prompt.py's _AUTOMATED_PREFIX (lowercase-initial tags only).
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")


def parse_iso_to_epoch(ts):
    """Parse a transcript record's ISO-8601 timestamp into epoch seconds (UTC).

    Transcript timestamps look like "2026-07-09T17:53:30.670Z". `fromisoformat`
    does not accept a bare trailing "Z" before Python 3.11, so normalise it to
    "+00:00" first; a timestamp with no zone is assumed UTC (all real records
    carry the Z). Returns None on missing/blank/unparseable input.
    """
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def last_activity_epoch(records_reverse):
    """Epoch of the last assistant record with a parseable timestamp, or None.

    *records_reverse* must yield records most-recent-first — a materialized
    list built via ``reversed(records)``, or (the live path) a lazy generator
    like ``_hookutil.iter_records_reverse`` that reads the transcript file
    from the end in bounded chunks. Consuming *records_reverse* lazily (a
    plain ``for`` loop, no eager ``reversed()`` call in here) is what lets a
    bounded generator short-circuit as soon as a match is found instead of
    first reading a session's whole transcript into memory. A record with an
    unparseable/missing timestamp is skipped in favor of an earlier one. None
    means no assistant record carried a parseable timestamp (typically: the
    first prompt of a fresh session, where no assistant turn exists yet).
    """
    for rec in records_reverse:
        if isinstance(rec, dict) and rec.get("type") == "assistant":
            ep = parse_iso_to_epoch(rec.get("timestamp"))
            if ep is not None:
                return ep
    return None


def compute_gap_seconds(now, last_activity):
    """Seconds elapsed since the last activity, or None when unknown."""
    if last_activity is None:
        return None
    return now - last_activity


def load_threshold_minutes(cwd):
    """Idle threshold in minutes from the project's hook-config.json, else the
    default. Mirrors turn-count-hook.py's load_prompt_threshold: any read/parse
    problem falls back to DEFAULT_MINUTES rather than raising. A configured
    non-positive value also falls back to the default -- otherwise
    should_refresh's strict gap > threshold check would fire on nearly every
    prompt, turning an occasional orientation cue into per-turn noise."""
    path = os.path.join(cwd or "", CONFIG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        minutes = int(config.get("idle_refresher_minutes", DEFAULT_MINUTES))
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError):
        return DEFAULT_MINUTES
    return minutes if minutes > 0 else DEFAULT_MINUTES


def is_automated_prompt(prompt):
    """True for machine-generated (XML-tagged) prompts — no human returning."""
    return bool(_AUTOMATED_PREFIX.match(prompt or ""))


def should_refresh(gap_seconds, threshold_seconds):
    """Fire only when the gap strictly exceeds the threshold ("extended idle")."""
    return gap_seconds is not None and gap_seconds > threshold_seconds


def humanize_gap(gap_seconds):
    """Coarse ASCII duration for the cue ("72 minutes" / "3 hours")."""
    minutes = int(gap_seconds // 60)
    if minutes < 120:
        return f"{minutes} minutes"
    return f"{minutes // 60} hours"


def build_refresher_context(gap_seconds):
    """The additionalContext cue. ASCII-only (hyphens, "about" — never an
    em-dash or a math sign) so it survives the cp1252 hook-stdout pipe."""
    span = humanize_gap(gap_seconds)
    return (
        f"[idle-refresher] The user is returning to this session after about "
        f"{span} away. Before addressing their new message, open your reply with "
        "a short refresher so they can re-orient: what we were working on, the "
        "current state (what is done and what is still in flight), and any "
        "pending to-dos or spawned tiles. Keep it to a few sentences - an "
        "orientation, not a full report - then address their request. (Session "
        "Summaries & Tile Tracking rule; ADR-095.)"
    )


def _last_activity_epoch_from_path(path):
    """last_activity_epoch, sourced from a bounded tail read of *path* instead
    of a fully materialized record list -- None on any failure (fail-open),
    matching the old _read_records contract this replaces (dev-env#679)."""
    if not path:
        return None
    try:
        return last_activity_epoch(_hookutil.iter_records_reverse(path))
    except Exception:
        return None


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict):
            sys.exit(0)
    except Exception:
        sys.exit(0)

    try:
        prompt = data.get("prompt", "") or ""
        # Automated sessions (scheduled tasks, CI monitors, etc.) — no human to orient.
        if is_automated_prompt(prompt):
            sys.exit(0)

        path = data.get("transcript_path", "") or ""
        if not path:
            found = _hookutil.find_transcript(data.get("session_id", "") or "")
            path = str(found) if found else ""

        last = _last_activity_epoch_from_path(path)
        if last is None:
            # No assistant record with a parseable timestamp: either the first
            # prompt of a fresh session (no assistant turn yet) or a corrupted
            # transcript. Either way there is nothing reliable to compare
            # against -> skip. (The transcript file's mtime is NOT a usable
            # substitute here: the user's just-submitted prompt is written to
            # the transcript around submit time -- confirmed against a real
            # transcript -- so by hook-fire time mtime is already ~now,
            # collapsing the gap to ~0 in exactly the case this branch exists
            # to handle.)
            sys.exit(0)

        gap = compute_gap_seconds(time.time(), last)
        threshold_seconds = load_threshold_minutes(data.get("cwd", "") or os.getcwd()) * 60
        if not should_refresh(gap, threshold_seconds):
            sys.exit(0)

        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": build_refresher_context(gap),
            }
        }
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
    except Exception:
        # Fail open: a refresher cue is a nicety; never block or disrupt the prompt.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
