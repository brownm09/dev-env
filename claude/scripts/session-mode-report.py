#!/usr/bin/env python3
"""session-mode-report.py — summarize per-session startup permission mode.

`session-mode-prompt.py` (a UserPromptSubmit hook) appends one JSON line per
prompt to ``scratch/session-mode-prompt.log``, each carrying the live
``permission_mode`` for that prompt. This utility turns that raw append-only
log into a readable, per-session report: for every ``session_id`` it finds the
*first* prompt (the session's startup) and reports the mode the session started
in.

Why this exists: ``defaultMode: "plan"`` in ``~/.claude/settings.json`` governs
fresh local sessions, but Desktop/web app sessions and spawn-task / sub-sessions
are launched by the platform in ``bypassPermissions`` so they can run
autonomously — that startup flag overrides ``defaultMode`` by design. The result
is that some sessions silently start off-plan. This report makes that visible.

Sessions are classified as **automated** (startup ``stage`` is
``automated_suppressed`` or the prompt begins with an XML tag like
``<scheduled-task>`` / ``<task-notification>``) or **interactive**. Automated
sessions starting in bypass is expected and is *not* flagged. Interactive
sessions that start in any mode other than ``plan`` ARE flagged (``!``).

Read-only. The report is the product and goes to stdout; diagnostics go to
stderr; exit 0 on success, non-zero on a usage/IO error. stdlib only (spawns no
subprocess, so no ``_winsubp`` import is needed).

Usage:
    py -3 claude/scripts/session-mode-report.py [options]

Options:
    --since YYYY-MM-DD   Only sessions that started on/after this date.
    --interactive-only   Exclude automated (machine-triggered) sessions.
    --non-plan-only      Show only flagged sessions (interactive, not plan).
    --log PATH           Override the log path (defaults to the scratch log).

See ADR-027 (session-mode-prompt hook conventions) for background.
"""

import argparse
import json
import re
import sys

DEFAULT_LOG = "C:/Users/brown/.claude/scratch/session-mode-prompt.log"

# Automated triggers use XML-tagged prompts; human prompts never start with <tag>.
# Mirrors the detection in session-mode-prompt.py.
_AUTOMATED_PREFIX = re.compile(r"^\s*<[a-z]")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_automated(record):
    """A session is automated if its startup entry was suppressed as such or
    its first prompt begins with an XML trigger tag."""
    if record.get("stage") == "automated_suppressed":
        return True
    return bool(_AUTOMATED_PREFIX.match(record.get("prompt_prefix", "") or ""))


def parse_log(path):
    """Read the JSONL log and return (sessions, stats).

    ``sessions`` maps session_id -> the earliest (startup) entry for that
    session. ``stats`` counts lines that could not be attributed to a session.
    Timestamps are ``%Y-%m-%dT%H:%M:%S`` strings, which sort chronologically as
    plain strings, so the lexicographic minimum is the startup entry.
    """
    sessions = {}
    stats = {"malformed": 0, "no_session": 0, "fallback_marker": 0}

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, TypeError):
                stats["malformed"] += 1
                continue

            session_id = entry.get("session_id") or ""
            if not session_id:
                # json_parse_failed / stdin_read_failed stages, or a session_id
                # the hook could not capture. Count, don't attribute.
                stats["no_session"] += 1
                if entry.get("fallback_marker"):
                    stats["fallback_marker"] += 1
                continue

            ts = entry.get("ts", "")
            prior = sessions.get(session_id)
            if prior is None or ts < prior.get("ts", ""):
                sessions[session_id] = entry

    return sessions, stats


def build_rows(sessions):
    """Turn the startup-entry map into sorted, classified rows."""
    rows = []
    for session_id, entry in sessions.items():
        kind = "automated" if _is_automated(entry) else "interactive"
        mode = entry.get("permission_mode") or "(unknown)"
        rows.append(
            {
                "ts": entry.get("ts", ""),
                "session_id": session_id,
                "mode": mode,
                "kind": kind,
                "prompt_prefix": (entry.get("prompt_prefix") or "").replace("\n", " "),
                "flagged": kind == "interactive" and mode != "plan",
            }
        )
    rows.sort(key=lambda r: r["ts"])
    return rows


def filter_rows(rows, since=None, interactive_only=False, non_plan_only=False):
    out = []
    for r in rows:
        if since and r["ts"][:10] < since:
            continue
        if interactive_only and r["kind"] != "interactive":
            continue
        if non_plan_only and not r["flagged"]:
            continue
        out.append(r)
    return out


def render(rows, stats, total_sessions):
    """Render the report to a list of lines (stdout)."""
    lines = []
    header = "{:1} {:19}  {:8}  {:18}  {:11}  {}".format(
        "", "started", "session", "mode", "kind", "prompt"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        prompt = r["prompt_prefix"]
        if len(prompt) > 48:
            prompt = prompt[:45] + "..."
        lines.append(
            "{:1} {:19}  {:8}  {:18}  {:11}  {}".format(
                "!" if r["flagged"] else " ",
                r["ts"],
                r["session_id"][:8],
                r["mode"],
                r["kind"],
                prompt,
            )
        )

    # Summary computed over the *displayed* rows.
    by_mode = {}
    interactive = 0
    interactive_plan = 0
    automated = 0
    flagged = 0
    for r in rows:
        by_mode[r["mode"]] = by_mode.get(r["mode"], 0) + 1
        if r["kind"] == "interactive":
            interactive += 1
            if r["mode"] == "plan":
                interactive_plan += 1
        else:
            automated += 1
        if r["flagged"]:
            flagged += 1

    lines.append("")
    lines.append("Summary")
    lines.append("  sessions shown: {} (of {} total in log)".format(len(rows), total_sessions))
    if by_mode:
        modes = ", ".join("{}={}".format(m, by_mode[m]) for m in sorted(by_mode))
        lines.append("  by startup mode: {}".format(modes))
    lines.append("  interactive: {} (plan={}, off-plan={})".format(
        interactive, interactive_plan, interactive - interactive_plan))
    lines.append("  automated (expected bypass): {}".format(automated))
    lines.append("  ! interactive sessions that started outside plan: {}".format(flagged))

    skipped_bits = []
    if stats["malformed"]:
        skipped_bits.append("malformed lines={}".format(stats["malformed"]))
    if stats["no_session"]:
        note = "no session_id={}".format(stats["no_session"])
        if stats["fallback_marker"]:
            note += " (fallback_marker={})".format(stats["fallback_marker"])
        skipped_bits.append(note)
    if skipped_bits:
        lines.append("  skipped: {}".format("; ".join(skipped_bits)))

    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Summarize per-session startup permission mode from the "
        "session-mode-prompt hook log."
    )
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="only sessions started on/after this date")
    parser.add_argument("--interactive-only", action="store_true",
                        help="exclude automated (machine-triggered) sessions")
    parser.add_argument("--non-plan-only", action="store_true",
                        help="show only flagged sessions (interactive, not plan)")
    parser.add_argument("--log", default=DEFAULT_LOG,
                        help="path to the session-mode-prompt log (default: scratch log)")
    args = parser.parse_args(argv)

    if args.since and not _DATE_RE.match(args.since):
        print("error: --since must be YYYY-MM-DD, got {!r}".format(args.since), file=sys.stderr)
        return 2

    try:
        sessions, stats = parse_log(args.log)
    except FileNotFoundError:
        print("error: log not found: {}".format(args.log), file=sys.stderr)
        print("       (no sessions have been recorded yet, or the path is wrong)", file=sys.stderr)
        return 1
    except OSError as e:
        print("error: could not read log {}: {}".format(args.log, e), file=sys.stderr)
        return 1

    rows = build_rows(sessions)
    rows = filter_rows(
        rows,
        since=args.since,
        interactive_only=args.interactive_only,
        non_plan_only=args.non_plan_only,
    )

    for line in render(rows, stats, total_sessions=len(sessions)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
