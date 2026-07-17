#!/usr/bin/env python3
"""Pre-compose validator for engineering-journal manifest shards (dev-env #423).

`/journal-compose` reads each session's manifest shard
(`sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl`, schema in
docs/REFERENCE.md → Engineering Journal Internals) to drive composition. The schema
requires five fields: ``stub``, ``topic``, ``tokens``, ``prs_opened``, ``prs_closed``.

A stub written against an *older* schema silently omits a newly-added field, and the gap
only surfaces mid-`/journal-compose` — where a subagent trips over it and a human hand-patches
the manifest (observed 2026-06-13 career-playbook ×5; 2026-06-17 lifting-logbook line 3). This
is a silent-skip class: a check that should fire up front instead fires late.

This script converts that late, silent violation into an immediate, visible one. It is wired
into the `journal-compose` skill as a Step-0 gate that runs **before any subagent spawns**:
it lists every manifest entry missing a required field (and every unparseable line) and exits
non-zero so composition aborts up front.

Pure-helper convention (matches the rest of `claude/scripts/`): the validation logic — now
shared with the write-time `journal-shard-write-advisory.py` PostToolUse hook (dev-env #556,
ADR-081) — lives in `_journal_schema.py`'s ``missing_required_fields`` /
``find_entries_missing_fields`` / ``parse_manifest_text`` / ``decode_shard_bytes``, all pure
(dict/str/bytes in, data out), unit-tested offline in ``tests/test_journal_schema.py`` with no
subprocess, network, or disk. ``main()`` is the only impure surface here (it reads the files
named on argv) and is not unit-tested.

Usage (paths may be shell globs — non-matching / absent paths are skipped, so an unmatched
glob that the shell passes through literally is harmless):

    py -3 validate-manifest.py <manifest-path> [<manifest-path> ...]

Both formats are handled by parsing line-by-line: an ADR-056 per-session shard is a single
JSON object (one line); a legacy per-day manifest is one JSON object per line.

Exit 0 — every entry has all required fields (or no manifest entries were found).
Exit 1 — at least one entry is missing a required field, a line failed to parse, or a file
had an encoding problem (e.g. a UTF-8 BOM).
"""
from __future__ import annotations

import os
import sys

from _journal_schema import (
    REQUIRED_FIELDS,
    decode_shard_bytes,
    find_entries_missing_fields,
    malformed_manifest_fields,
    missing_required_fields,
    parse_manifest_text,
)


def main(argv) -> int:
    paths = argv[1:]
    entry_count = 0
    parse_errors = []     # list[str] — "path:lineno" or "path (unreadable: ...)"
    field_errors = []     # list[tuple[str, str, list[str]]] — (path:lineno, stub-label, missing)
    type_errors = []      # list[tuple[str, str, list[str]]] — (path:lineno, stub-label, problems)
    encoding_errors = []  # list[str] — "path: <problem>" (e.g. a named BOM)

    for path in paths:
        if not os.path.isfile(path):
            # Unmatched shell glob passed through literally, or an absent legacy file —
            # not a validation failure; nothing to check.
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as exc:
            parse_errors.append(f"{path} (unreadable: {exc})")
            continue
        text, problem = decode_shard_bytes(raw)
        if problem:
            encoding_errors.append(f"{path}: {problem}")
        if text is None:
            # Not valid UTF-8 even past a BOM check — nothing left to parse.
            continue
        for lineno, entry in parse_manifest_text(text):
            src = f"{path}:{lineno}"
            if entry is None:
                parse_errors.append(src)
                continue
            entry_count += 1
            missing = missing_required_fields(entry)
            if missing:
                stub = entry.get("stub", "<no stub field>")
                field_errors.append((src, stub, missing))
            type_problems = malformed_manifest_fields(entry)
            if type_problems:
                stub = entry.get("stub", "<no stub field>")
                type_errors.append((src, stub, type_problems))

    if not parse_errors and not field_errors and not type_errors and not encoding_errors:
        noun = "entry" if entry_count == 1 else "entries"
        print(
            f"[validate-manifest] OK - {entry_count} manifest {noun} valid; "
            "all required fields present."
        )
        return 0

    sys.stderr.write(
        "[validate-manifest] FAIL - manifest shard(s) violate the required-field schema "
        f"({', '.join(REQUIRED_FIELDS)}).\n"
        "Fix each entry below before composing - this gate exists so the gap surfaces now,\n"
        "up front, instead of mid-compose where it is hand-patched.\n\n"
    )
    if encoding_errors:
        sys.stderr.write("Encoding problems:\n")
        for src in encoding_errors:
            sys.stderr.write(f"  - {src}\n")
        sys.stderr.write("\n")
    if field_errors:
        sys.stderr.write("Entries missing required field(s):\n")
        for src, stub, missing in field_errors:
            sys.stderr.write(f"  - {src}\n      stub: {stub}\n      missing: {', '.join(missing)}\n")
        sys.stderr.write("\n")
    if type_errors:
        sys.stderr.write("Entries with malformed field values:\n")
        for src, stub, problems in type_errors:
            sys.stderr.write(f"  - {src}\n      stub: {stub}\n      problems: {'; '.join(problems)}\n")
        sys.stderr.write("\n")
    if parse_errors:
        sys.stderr.write("Unparseable lines or unreadable files:\n")
        for src in parse_errors:
            sys.stderr.write(f"  - {src}\n")
        sys.stderr.write("\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
