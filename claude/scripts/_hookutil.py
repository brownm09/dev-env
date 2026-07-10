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
- **Bounded tail reader** (`iter_records_reverse`) — used by idle-refresher.py,
  which only needs the last assistant record's timestamp and would otherwise pay
  a full parse of a potentially multi-MB transcript on every prompt submit. Reads
  the file from the end in bounded chunks instead of the whole thing; a caller
  that stops consuming the generator early never touches the unread remainder
  (dev-env#679, ADR-090 Amendment 1).

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

    # Only need a small piece of tail state? Avoid the full parse:
    for rec in _hookutil.iter_records_reverse(tpath):
        if rec.get("type") == "assistant":
            ...  # first match is the most recent -- stop here if that's all you need
            break
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"
PROJECTS = Path.home() / ".claude" / "projects"
MAX_AGE_DAYS = 30
DEFAULT_REVERSE_CHUNK_SIZE = 65536  # 64 KiB


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


def _record_from_line(raw: bytes):
    """Decode+parse one raw JSONL line (bytes, no trailing newline) into a
    dict record, or ``None`` for a blank/malformed/non-object line. Shared by
    ``iter_records_reverse``'s chunk loop; ``errors="replace"`` on decode
    means a corrupted byte sequence degrades to a JSON parse failure (caught
    below) rather than raising, matching ``_parse_records``'s skip-and-continue
    contract."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        rec = json.loads(text)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def iter_records_reverse(transcript_path: Path, chunk_size: int = DEFAULT_REVERSE_CHUNK_SIZE):
    """Yield JSON-object records from *transcript_path* most-recent-first,
    reading the file from the end in bounded ``chunk_size`` chunks instead of
    parsing it whole (contrast ``load_records``, which always reads and parses
    every line -- dev-env#679).

    A caller that only needs a small piece of tail state -- e.g.
    idle-refresher.py's last-assistant-record timestamp -- can stop consuming
    the generator (``break``, or a bare ``next()``) as soon as it finds a
    match; the unread, earlier portion of the file is never touched. Chunk
    boundaries are found on raw bytes before any UTF-8 decoding, so a
    multi-byte character split across two chunk reads is never corrupted (the
    ASCII ``\\n`` byte cannot appear inside a UTF-8 continuation or lead byte).
    Blank and malformed lines are skipped and a non-object JSON value (``42``,
    ``"s"``, ``null``, ``[1]``) is dropped, mirroring ``_parse_records``'s
    contract -- see ``_record_from_line``.

    Raises the same exceptions ``open()`` would (``FileNotFoundError`` /
    ``OSError``) on a missing/unreadable path, and ``ValueError`` for a
    non-positive ``chunk_size`` (which would never advance the read
    position). Callers that want fail-open behavior should catch around
    consumption, same as ``load_records``'s contract.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")

    with open(transcript_path, "rb") as f:
        f.seek(0, 2)  # SEEK_END
        pos = f.tell()
        tail = b""  # a leading line fragment that continues into bytes not yet read
        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + tail
            lines = buf.split(b"\n")
            if pos > 0:
                # lines[0] continues before this chunk -- hold it, don't yield yet.
                tail = lines[0]
                complete = lines[1:]
            else:
                # Start of file: lines[0] is now bounded on both ends.
                tail = b""
                complete = lines
            for raw in reversed(complete):
                rec = _record_from_line(raw)
                if rec is not None:
                    yield rec
