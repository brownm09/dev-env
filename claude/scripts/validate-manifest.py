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

Pure-helper convention (matches the rest of `claude/scripts/`): the validation logic lives in
``missing_required_fields`` / ``find_entries_missing_fields`` / ``parse_manifest_text`` — all
pure (dict/str in, data out), unit-tested offline in ``tests/test_validate_manifest.py`` with no
subprocess, network, or disk. ``main()`` is the only impure surface (it reads the files named on
argv) and is not unit-tested.

Usage (paths may be shell globs — non-matching / absent paths are skipped, so an unmatched
glob that the shell passes through literally is harmless):

    py -3 validate-manifest.py <manifest-path> [<manifest-path> ...]

Both formats are handled by parsing line-by-line: an ADR-056 per-session shard is a single
JSON object (one line); a legacy per-day manifest is one JSON object per line.

Exit 0 — every entry has all required fields (or no manifest entries were found).
Exit 1 — at least one entry is missing a required field, or a line failed to parse.
"""
from __future__ import annotations

import json
import os
import sys

# The manifest schema's required fields, in canonical (schema) order. Kept in sync with
# docs/REFERENCE.md → "Manifest shard format". ``priorities`` is optional and not listed here.
REQUIRED_FIELDS = ("stub", "topic", "tokens", "prs_opened", "prs_closed")


def missing_required_fields(entry: object) -> list[str]:
    """Return the required fields absent from a single parsed manifest entry.

    Returned in canonical schema order so reports are stable. A non-dict entry (a JSON
    list/scalar that slipped in) is treated as missing *every* required field — it cannot
    satisfy the schema. "Missing" means the key is absent; a present-but-null value is out
    of scope (the issue is *omitted* fields, per #423).
    """
    if not isinstance(entry, dict):
        return list(REQUIRED_FIELDS)
    return [f for f in REQUIRED_FIELDS if f not in entry]


def find_entries_missing_fields(entries):
    """Pure contract: parsed manifest entries -> [(entry, [missing-fields]), ...].

    One tuple per entry that is missing at least one required field; entries with all five
    fields are omitted. The list preserves input order. This is the function the issue
    (#423) specifies — `journal-compose` uses ``main`` for source-aware reporting, but this
    is the stable, testable core.
    """
    result = []
    for entry in entries:
        missing = missing_required_fields(entry)
        if missing:
            result.append((entry, missing))
    return result


def parse_manifest_text(text: str):
    """Pure: manifest `.jsonl` text -> [(lineno, entry-or-None), ...].

    Each non-blank line is one JSON object (a shard is a single line; a legacy per-day
    manifest is one object per line). Blank/whitespace-only lines are skipped. A line that
    is not valid JSON, or that parses to a non-object (list/scalar), yields ``(lineno, None)``
    so ``main`` can report it as a parse error rather than silently dropping it.
    """
    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            results.append((lineno, None))
            continue
        results.append((lineno, obj if isinstance(obj, dict) else None))
    return results


def main(argv) -> int:
    paths = argv[1:]
    entry_count = 0
    parse_errors = []   # list[str] — "path:lineno"
    field_errors = []   # list[tuple[str, str, list[str]]] — (path:lineno, stub-label, missing)

    for path in paths:
        if not os.path.isfile(path):
            # Unmatched shell glob passed through literally, or an absent legacy file —
            # not a validation failure; nothing to check.
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            parse_errors.append(f"{path} (unreadable: {exc})")
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

    if not parse_errors and not field_errors:
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
    if field_errors:
        sys.stderr.write("Entries missing required field(s):\n")
        for src, stub, missing in field_errors:
            sys.stderr.write(f"  - {src}\n      stub: {stub}\n      missing: {', '.join(missing)}\n")
        sys.stderr.write("\n")
    if parse_errors:
        sys.stderr.write("Unparseable lines or unreadable files:\n")
        for src in parse_errors:
            sys.stderr.write(f"  - {src}\n")
        sys.stderr.write("\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
