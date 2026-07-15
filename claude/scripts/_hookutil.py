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
- **Hook heartbeat** (`record_heartbeat`) — called by every wired hook's `main()`
  as its first statement, so a hook that is registered in settings.json but has
  silently stopped firing (the #377 / #355 failure class -- post-tool-use.py dead
  for months, usage-snapshot.py for 8 days, discovered only by accident) leaves a
  detectable trace. `hook-liveness-check.py` reads the ledger this writes and warns
  when a wired hook has gone quiet longer than its expected cadence (ADR-106).

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

    # First statement of main() -- unconditional, regardless of what the rest
    # of the hook does or whether it raises:
    def main() -> None:
        _hookutil.record_heartbeat("my-hook-name")
        ...
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"
PROJECTS = Path.home() / ".claude" / "projects"
HEARTBEAT_DIR = SCRATCH / "hook-heartbeat"
MAX_AGE_DAYS = 30
DEFAULT_REVERSE_CHUNK_SIZE = 65536  # 64 KiB


def cleanup_stale_sentinels(prefix: str, scratch: Path | None = None, ext: str = ".flag") -> None:
    """Remove per-session sentinel files whose names start with *prefix* and whose
    mtime is older than MAX_AGE_DAYS.  Swallows all I/O errors — the cleanup is
    best-effort and must never block a hook.  *scratch* overrides SCRATCH (used by
    tests to isolate against the real ~/.claude/scratch directory).  *ext* overrides
    the default ``.flag`` suffix (e.g. ``.txt``) for callers whose sentinel files
    predate the ``.flag`` convention — every existing caller passes only *prefix*,
    so this stays backward compatible (dev-env#768)."""
    root = scratch if scratch is not None else SCRATCH
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        flags = list(root.glob(f"{prefix}*{ext}"))
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


def record_heartbeat(hook_name: str, heartbeat_dir: Path | None = None) -> None:
    """Record that *hook_name* just fired, for `hook-liveness-check.py` to notice
    when a wired hook has gone quiet (ADR-106).

    Writes ``heartbeat_dir / f"{hook_name}.ts"`` (default
    ``~/.claude/scratch/hook-heartbeat/``) containing the current Unix
    timestamp, via a per-process tmp file + ``os.replace`` atomic swap — no
    locks, no subprocess, and no risk of a concurrent session's write
    corrupting this one's (each writer's tmp file has a distinct name; the
    rename is atomic on both POSIX and Windows). *hook_name* is always a
    literal the caller controls (its own script basename minus ``.py``), not
    untrusted input, so no sanitization is needed — same trust model as
    ``sentinel_path``'s *session_id*.

    Best-effort: swallows all I/O errors, matching every other sentinel
    writer in this module. A heartbeat write must never be the reason a hook
    fails — call this as the unconditional first statement of ``main()`` so
    it still records even if the rest of the hook body raises.
    """
    root = heartbeat_dir if heartbeat_dir is not None else HEARTBEAT_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{hook_name}.ts"
        tmp = root / f"{hook_name}.ts.{os.getpid()}.tmp"
        tmp.write_text(str(time.time()), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        pass


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
    ``iter_records_reverse``'s chunk loop. Note this is *more* lenient than
    ``load_records``'s strict ``open(..., encoding="utf-8")`` (which raises
    ``UnicodeDecodeError`` on malformed bytes): ``errors="replace"`` here
    degrades a corrupted byte sequence to a JSON parse failure -- caught
    below and skipped, like any other malformed line -- rather than raising.
    Real transcripts are always well-formed UTF-8, so this only matters for a
    corrupted file, where "skip the bad line" is arguably the more useful
    behavior for a best-effort tail read anyway."""
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

    A single line's bytes accumulate in a list (``pending``) across chunk
    reads and are joined *once* when a terminator is finally found, rather
    than re-concatenating a growing buffer on every chunk (``buf = chunk +
    tail``) -- the latter costs O(line_length^2 / chunk_size) for a line that
    spans many chunks, which matters here specifically: the record right
    before whatever this generator is scanning for is often the transcript's
    newest entry (e.g. the user's just-submitted prompt on the
    UserPromptSubmit path), and its size is exactly the thing a large paste
    puts under the caller's control. This function is O(file bytes actually
    read) end to end, matching ``chunk_size``-bounded reads, not O(bytes^2)
    on any single line.

    Raises the same exceptions ``open()`` would (``FileNotFoundError`` /
    ``OSError``) on a missing/unreadable path, and ``ValueError`` for a
    non-positive ``chunk_size`` (which would never advance the read
    position) -- raised on the first ``next()``/iteration, like any generator
    function's body, not at the ``iter_records_reverse(...)`` call itself.
    Callers that want fail-open behavior should catch around consumption,
    same as ``load_records``'s contract.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")

    with open(transcript_path, "rb") as f:
        f.seek(0, 2)  # SEEK_END
        pos = f.tell()
        # Fragments of a not-yet-terminated line, most-recently-read first;
        # joined via b"".join(reversed(pending)) only once a terminator is
        # found (see the docstring for why this avoids quadratic copying).
        pending: list = []
        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            nl_pos = chunk.rfind(b"\n")
            if nl_pos == -1:
                # No terminator anywhere in this chunk -- it's all part of the
                # still-growing pending fragment; keep reading earlier bytes.
                pending.append(chunk)
                continue
            # chunk[nl_pos + 1:] completes the pending fragment (empty when
            # this terminator is the file's very last byte).
            pending.append(chunk[nl_pos + 1:])
            rec = _record_from_line(b"".join(reversed(pending)))
            if rec is not None:
                yield rec
            pending = []
            # Everything before that terminator may hold further complete
            # lines plus a new leading fragment -- same split-based handling
            # as before, just scoped to this chunk instead of the whole file.
            rest = chunk[:nl_pos]
            lines = rest.split(b"\n")
            if pos > 0:
                pending = [lines[0]]
                complete = lines[1:]
            else:
                complete = lines
            for raw in reversed(complete):
                rec = _record_from_line(raw)
                if rec is not None:
                    yield rec
        if pending:
            # The file's first line never found its own (preceding)
            # terminator -- it's complete because it's bounded by BOF.
            rec = _record_from_line(b"".join(reversed(pending)))
            if rec is not None:
                yield rec
