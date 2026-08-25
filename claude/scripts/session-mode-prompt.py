#!/usr/bin/env python3
"""
session-mode-prompt.py

On the first user prompt of a new session, inject a one-time mode-confirmation
reminder into Claude's context so Claude surfaces the active permission mode
(plan / bypass / auto) in its first response. Addresses sessions spawned from
an active bypass-permissions session that may have inherited the wrong
defaultMode from settings.json.

In a bypass-permissions session the injected context also carries the
content-authoring carve-out (see `_BYPASS_CONTENT_NOTE` / `reminder_text`):
that mode's standing harness instruction to make file changes with sed,
heredocs, or short scripts contradicts claude/CLAUDE.md -> Authoring File
Content, and the harness prompt is not ours to edit -- so the tiebreaker is
delivered into the same context, in the same sessions. See ADR-138 and
dev-env#1041.

Output contract: stdout JSON `{"hookSpecificOutput": {"hookEventName":
"UserPromptSubmit", "additionalContext": "..."}}` + exit 0. The reminder
becomes added context for Claude alongside the user's prompt; the prompt is
NOT erased and the user does not need to re-submit. See ADR-027 amendment
2026-05-27 (issue #268) for why this hook is exit-0/additionalContext rather
than exit-2/stderr — its goal is a one-time advisory, not a true block.

Per-session marker file at scratch/session_mode_ack_<session_id>.txt records
that the reminder has been injected for this session; any subsequent prompt
in the same session passes through silently. Markers from other sessions are
not read, so a reminder in session A does not suppress one in session B.
Markers older than _hookutil.MAX_AGE_DAYS are swept via
_hookutil.cleanup_stale_sentinels on each invocation (dev-env#768 — previously
these were never cleaned up, one of several non-cleaning sentinel writers
found at the 2026-07-10 hook-reliability assessment).

Suppressed for automated sessions whose prompt begins with an XML tag (e.g.
<scheduled-task>, <ci-monitor-event>). These are machine-generated triggers
where no human is present to need the reminder.

Debug logging: every invocation appends one JSON line to
C:/Users/brown/.claude/scratch/session-mode-prompt.log so silent failures can
be diagnosed without re-instrumenting the hook. See dev-env#261.
"""

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import _hookutil

# Derived from Path.home() (identical real-world value to the literal string
# this replaced) rather than hardcoded, so HOME/USERPROFILE overrides in tests
# isolate the marker/log writes from ~/.claude/scratch -- dev-env#768 review:
# a hardcoded MARKER_DIR meant a subprocess test's HOME redirection silently
# failed to isolate the marker write, writing a real file to the actual
# machine's scratch directory.
_SCRATCH_DIR = str(Path.home() / ".claude" / "scratch")
MARKER_DIR = _SCRATCH_DIR
LOG_PATH = f"{_SCRATCH_DIR}/session-mode-prompt.log"
SENTINEL_PREFIX = "session_mode_ack_"

# Automated triggers use XML-tagged prompts; human prompts never start with <tag>.
# Matches lowercase-initial tags only — all current triggers use lowercase; update if that changes.
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")

# session_id is a UUID from Claude Code; sanitize defensively in case the contract changes.
_SAFE_SESSION_ID = re.compile(r"[^A-Za-z0-9_-]")

_REMINDER_TEXT = (
    "Session-mode reminder (first prompt of a new session): the active "
    "permission mode is plan / bypass / auto (Shift+Tab cycles modes; plan "
    "is the settings default). Only surface this in your response if the "
    "user is starting a substantive task where the mode affects what you "
    "will do — skip the preamble for trivial prompts (greetings, /clear, "
    "single-word inputs, prompts where the mode is obviously irrelevant)."
)

# Bypass-permissions sessions receive a standing harness instruction to prefer
# the Bash tool and "make file changes with sed, heredocs, or short scripts,
# rather than using the dedicated Read, Edit, or Write tools." For authoring
# file CONTENT that instruction is the direct cause of dev-env#1041's three
# failures, and it contradicts claude/CLAUDE.md -> Authoring File Content with
# no tiebreaker stated anywhere. The harness prompt is not ours to edit, so the
# next best thing is to deliver the carve-out into the same context, in the
# same sessions, at the same moment -- which is exactly what this hook already
# does. Verified live from this hook's own log (5824 entries): bypass is 79% of
# all sessions, so the contradiction is present in four sessions out of five.
# See ADR-138.
_BYPASS_CONTENT_NOTE = (
    " Bypass-mode note: the standing instruction to prefer Bash (sed, heredocs, "
    "short scripts) over the Read/Edit/Write tools governs shell WORK -- running "
    "commands, inspecting state, invoking tooling -- not authoring file CONTENT. "
    "For content, claude/CLAUDE.md -> Authoring File Content wins: Write/Edit "
    "unless the content is another program's redirected output, or a single-line "
    "literal with no apostrophe, backtick, or backslash "
    "(pre-tool-use-shell-content-write-guard.py enforces this; ADR-138)."
)

# The harness reports bypass mode as "bypassPermissions"; matched
# case-insensitively on the "bypass" stem so a contract rename to a sibling
# spelling still lands the note rather than silently dropping it.
_BYPASS_MODE_STEM = "bypass"


def reminder_text(permission_mode):
    """The context to inject. Bypass-mode sessions additionally get the
    content-authoring carve-out; every other mode gets the base reminder
    unchanged (no contradiction to resolve there)."""
    if isinstance(permission_mode, str) and _BYPASS_MODE_STEM in permission_mode.lower():
        return _REMINDER_TEXT + _BYPASS_CONTENT_NOTE
    return _REMINDER_TEXT


def _marker_path(session_id, event=None):
    """Return the per-session marker path. Falls back to 'unknown' when session_id is missing.

    When the fallback fires, set event["fallback_marker"]=True so a future Claude Code
    contract change that drops session_id is visible in scratch/session-mode-prompt.log
    rather than silently re-introducing cross-session contamination via session_mode_ack_unknown.txt.
    """
    safe = _SAFE_SESSION_ID.sub("", session_id or "")
    if not safe:
        safe = "unknown"
        if event is not None:
            event["fallback_marker"] = True
    return f"{MARKER_DIR}/session_mode_ack_{safe}.txt"


def _log(event):
    """Append one JSON line to the log. Never raise."""
    try:
        event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def main():
    _hookutil.record_heartbeat("session-mode-prompt")
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX, ext=".txt")
    event = {"stage": "start"}

    try:
        raw = sys.stdin.read()
    except Exception as e:
        event["stage"] = "stdin_read_failed"
        event["error"] = repr(e)
        _log(event)
        sys.exit(0)

    try:
        data = json.loads(raw)
    except Exception as e:
        event["stage"] = "json_parse_failed"
        event["error"] = repr(e)
        event["raw_len"] = len(raw)
        _log(event)
        sys.exit(0)

    now = time.time()
    session_id = data.get("session_id", "")
    prompt = data.get("prompt", "")
    event["session_id"] = session_id
    event["prompt_prefix"] = prompt[:80]
    event["permission_mode"] = data.get("permission_mode", "")

    marker_path = _marker_path(session_id, event)
    event["marker_path"] = marker_path
    marker_exists = os.path.exists(marker_path)
    event["marker_exists"] = marker_exists

    # If the reminder was already injected for THIS session, pass through silently.
    if marker_exists:
        event["stage"] = "session_acked_passthrough"
        event["exit"] = 0
        _log(event)
        sys.exit(0)

    # Automated sessions (scheduled tasks, CI monitors, etc.) — no human to remind.
    if _AUTOMATED_PREFIX.match(prompt):
        event["stage"] = "automated_suppressed"
        event["exit"] = 0
        _log(event)
        sys.exit(0)

    # Write marker BEFORE emitting so any retry on the same session passes through.
    try:
        # Explicit mkdir rather than relying on record_heartbeat's own
        # mkdir(parents=True) (called earlier in main()) as an implicit,
        # unrelated side effect that happens to create this same directory
        # (dev-env#768 review) -- this write is self-sufficient regardless
        # of what else main() does before it.
        os.makedirs(MARKER_DIR, exist_ok=True)
        with open(marker_path, "w") as f:
            f.write(str(now))
        event["marker_written"] = True
    except Exception as e:
        # Route to log only; stderr on exit-0 UserPromptSubmit is surfaced to the user UI,
        # which would expose a hook-internal disk error in the chat. The log captures it.
        event["marker_write_error"] = repr(e)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": reminder_text(data.get("permission_mode")),
        }
    }

    # Marker is written BEFORE this emit (above) so a transient retry on the same
    # session_id passes through silently rather than double-emitting. The tradeoff:
    # if the emit below raises (e.g., broken pipe), the reminder is silently lost for
    # this session — observable via the log, but the user gets no preamble. Accepted:
    # double-emission would be worse than a missed one-time advisory.
    try:
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
        event["stage"] = "additional_context_emitted"
        event["exit"] = 0
        _log(event)
    except Exception as e:
        # Same rationale as marker_write_error: log-only, no user-facing stderr.
        event["stage"] = "additional_context_emit_failed"
        event["error"] = repr(e)
        event["traceback"] = traceback.format_exc()
        event["exit"] = 0
        _log(event)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
