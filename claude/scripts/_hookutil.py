#!/usr/bin/env python3
"""Shared utilities for the Stop / UserPromptSubmit hook family.

Three groups of helpers, each extracted from near-verbatim copies that existed
in the consuming scripts before this module:

- **Per-session sentinels** (`cleanup_stale_sentinels`, `sentinel_path`) — used by
  posttooluse-inert-advisory.py, stop-tile-enumeration-gate.py, and
  reconcile-open-prs.py to fire at most once per session (ADR-064).
- **Transcript locate** (`find_transcript`) — used by token-tracker.py and
  posttooluse-inert-advisory.py to find a session's JSONL when the Stop payload's
  path is absent/stale (ADR-064).
- **Transcript record readers** (`load_records`, `_parse_records`, `iter_bash_calls`,
  `_result_text`, `_content_items`) — used by posttooluse-inert-advisory.py and
  stop-tile-enumeration-gate.py to parse a transcript into records and pair Bash
  tool_use/tool_result calls. `iter_bash_calls` returns (command, output, cwd)
  3-tuples (the advisory scopes by cwd; the gate ignores it) (ADR-090).

Mirrors how _hookio.py was extracted for PostToolUse Bash hooks (ADR-050).
See ADR-064 and ADR-090 for rationale.

Imported the same way as _hookio: a sibling module in scripts/ that the
`pyw -3` hook launcher (which puts the script's own directory on sys.path)
and the test harness (sys.path.insert(0, scripts_dir)) both resolve.

Usage:
    import _hookutil

    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)
    path = _hookutil.sentinel_path(SENTINEL_PREFIX, session_id)
    tpath = _hookutil.find_transcript(session_id)
    records = _hookutil.load_records(tpath)
    for command, output, cwd in _hookutil.iter_bash_calls(records):
        ...
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"
PROJECTS = Path.home() / ".claude" / "projects"
MAX_AGE_DAYS = 30


def cleanup_stale_sentinels(prefix: str, scratch: Path | None = None) -> None:
    """Remove per-session sentinel files whose names start with *prefix* and whose
    mtime is older than MAX_AGE_DAYS.  Swallows all I/O errors — the cleanup is
    best-effort and must never block a hook.  *scratch* overrides SCRATCH (used by
    tests to isolate against the real ~/.claude/scratch directory)."""
    root = scratch if scratch is not None else SCRATCH
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        flags = list(root.glob(f"{prefix}*.flag"))
    except Exception:
        return
    # Guard each file independently — a single stat()/unlink() failure (race,
    # permission) must not abort cleanup of the remaining sentinels.
    for f in flags:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except Exception:
            continue


def sentinel_path(prefix: str, session_id: str, scratch: Path | None = None) -> Path:
    """Return ``scratch / f"{prefix}{session_id}.flag"``.

    *scratch* overrides SCRATCH (used by tests).  The existence of this file is the
    per-session "already ran" signal; callers create it with ``.write_text("")`` and
    check it with ``.exists()``."""
    root = scratch if scratch is not None else SCRATCH
    return root / f"{prefix}{session_id}.flag"


def find_transcript(session_id: str, projects: Path | None = None) -> Path | None:
    """Return the JSONL transcript for *session_id*, or ``None`` when not found.

    Searches all project directories under ``projects`` (defaults to
    ``~/.claude/projects/``).  *projects* is injectable for offline tests.
    """
    root = projects if projects is not None else PROJECTS
    # Session ids are unique, so the first match is the answer — next() short-circuits
    # instead of materializing every JSONL under ~/.claude/projects/.
    return next(root.glob(f"**/{session_id}.jsonl"), None)


# --- transcript record readers -------------------------------------------------
# Parse a session JSONL transcript into records and pair Bash tool_use/tool_result
# calls. Shared by posttooluse-inert-advisory.py and stop-tile-enumeration-gate.py,
# extracted from the near-verbatim copies that lived in each (ADR-090).


def _content_items(rec: dict) -> list:
    """The ``message.content`` list of a transcript record, or ``[]`` when the
    record, its ``message``, or the ``content`` is absent or not the expected
    type. The guards let the pairing/scan helpers stay safe on hand-built or
    malformed record lists without an outer try/except."""
    if not isinstance(rec, dict):
        return []
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    c = msg.get("content")
    return c if isinstance(c, list) else []


def _result_text(item: dict, record: dict) -> str:
    """Best-available text of a tool_result: the per-id content the model saw,
    falling back to the record's structured ``toolUseResult`` (stdout+stderr)."""
    c = item.get("content")
    if isinstance(c, str) and c.strip():
        return c
    if isinstance(c, list):
        joined = "\n".join(
            x.get("text", "")
            for x in c
            if isinstance(x, dict) and x.get("type") == "text"
        )
        if joined.strip():
            return joined
    tur = record.get("toolUseResult")
    if isinstance(tur, dict):
        parts = [p for p in (tur.get("stdout"), tur.get("stderr")) if p]
        if parts:
            return "\n".join(parts)
        out = tur.get("output")
        if out:
            return str(out)
    return ""


def iter_bash_calls(records: list) -> list:
    """Pair each Bash tool_use with its tool_result by ``tool_use_id``.

    Returns ``(command, output, cwd)`` tuples. Pairing by id (not adjacency)
    keeps parallel tool calls from mismatching; ``cwd`` is taken from the
    assistant record that issued the command. posttooluse-inert-advisory.py uses
    ``cwd`` for its dev-env-cwd scoping; stop-tile-enumeration-gate.py ignores it
    (via a thin 2-tuple adapter). ``isinstance`` guards (in ``_content_items`` and
    per-item) keep it safe on malformed or hand-built record lists.
    """
    commands: dict = {}
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        cwd = rec.get("cwd", "") or ""
        for item in _content_items(rec):
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "Bash"
            ):
                tid = item.get("id")
                if tid:
                    commands[tid] = ((item.get("input") or {}).get("command", ""), cwd)

    calls: list = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "user":
            continue
        for item in _content_items(rec):
            if isinstance(item, dict) and item.get("type") == "tool_result":
                tid = item.get("tool_use_id")
                if tid in commands:
                    command, cwd = commands[tid]
                    calls.append((command, _result_text(item, rec), cwd))
    return calls


def _parse_records(text: str) -> list:
    """Parse a transcript's JSONL *text* into records, keeping only JSON objects.

    A bare ``null`` / number / string / array line is dropped rather than kept as
    a non-dict record — otherwise a single such line could raise an
    ``AttributeError`` in a downstream helper and, caught only by a hook's outer
    guard, silently disable it. Callers that already hold the transcript text (to
    run a cheap pre-filter first, as stop-tile-enumeration-gate.py does) call this
    directly; ``load_records`` wraps it for the read-then-parse case.
    """
    records: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def load_records(transcript_path: Path) -> list:
    """Read a session JSONL transcript file and return its JSON-object records
    (via ``_parse_records`` — non-object lines are dropped)."""
    with open(transcript_path, encoding="utf-8") as f:
        return _parse_records(f.read())
