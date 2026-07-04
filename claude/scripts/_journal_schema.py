#!/usr/bin/env python3
"""Shared schema/validation helpers for engineering-journal manifest and open-PR shards
(dev-env #423, #556; ADR-081).

Extracted from ``validate-manifest.py`` (which re-imports these names so its existing
callers and tests are unaffected) so a second consumer — the write-time
``journal-shard-write-advisory.py`` PostToolUse hook — can validate the same schema
without duplicating the logic. This mirrors the repo's established extraction
convention: shared logic lives in an underscore module that both the original script
and a new hook import directly (see ``_journal_shards.py`` / ADR-057, ``_worktree_canon.py``
/ ADR-073). No production script ever ``importlib``s a hyphenated sibling.

Two schemas are covered:

  - Manifest shards (``sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl``):
    ``REQUIRED_FIELDS`` — kept in sync with docs/REFERENCE.md -> "Manifest shard format".
  - Open-PR tracking shards (``sessions/<project>/open-prs/<N>.json``):
    ``OPEN_PR_REQUIRED_FIELDS`` — kept in sync with docs/REFERENCE.md -> "Open-PR tracking
    shards". Unlike the manifest schema, every field is required (no optional fields
    documented for this shard kind).

This module is import-only — no ``main()``, no subprocess, no ``_winsubp`` — so every
helper unit-tests offline (``tests/test_journal_schema.py``).
"""
from __future__ import annotations

import codecs
import json

# The manifest schema's required fields, in canonical (schema) order. Kept in sync with
# docs/REFERENCE.md → "Manifest shard format". ``priorities`` is optional and not listed here.
REQUIRED_FIELDS = ("stub", "topic", "tokens", "prs_opened", "prs_closed")

# The open-PR tracking shard schema, in canonical (schema) order. Kept in sync with
# docs/REFERENCE.md → "Open-PR tracking shards". No optional fields are documented there.
OPEN_PR_REQUIRED_FIELDS = ("pr", "url", "topic", "stub", "opened")


def missing_required_fields(entry: object, fields: tuple[str, ...] = REQUIRED_FIELDS) -> list[str]:
    """Return the fields in ``fields`` absent from a single parsed shard entry.

    Returned in the given order so reports are stable. A non-dict entry (a JSON
    list/scalar that slipped in) is treated as missing *every* field — it cannot
    satisfy the schema. "Missing" means the key is absent; a present-but-null value is
    out of scope (the issue is *omitted* fields, per #423).

    ``fields`` defaults to the manifest schema (``REQUIRED_FIELDS``) so every existing
    caller (``validate-manifest.py``, its tests) is unaffected by this parameter's
    addition. Pass ``OPEN_PR_REQUIRED_FIELDS`` (or use ``missing_open_pr_fields`` below)
    to validate an open-PR shard instead.
    """
    if not isinstance(entry, dict):
        return list(fields)
    return [f for f in fields if f not in entry]


def missing_open_pr_fields(entry: object) -> list[str]:
    """``missing_required_fields`` specialized to the open-PR shard schema."""
    return missing_required_fields(entry, OPEN_PR_REQUIRED_FIELDS)


def find_entries_missing_fields(entries):
    """Pure contract: parsed manifest entries -> [(entry, [missing-fields]), ...].

    One tuple per entry that is missing at least one required field; entries with all
    required fields are omitted. The list preserves input order. This is the function
    the issue (#423) specifies — `journal-compose` uses ``main`` for source-aware
    reporting, but this is the stable, testable core.
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
    so callers can report it as a parse error rather than silently dropping it.
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


def decode_shard_bytes(raw: bytes) -> tuple[str | None, str | None]:
    """Decode shard file bytes, naming a BOM problem instead of letting it surface as an
    opaque JSON parse failure on line 1.

    Returns ``(text, problem)``: on a recognized BOM, ``text`` is the content **past** the
    BOM (decoded with the matching codec) so field validation still proceeds, and
    ``problem`` names the encoding (e.g. ``"UTF-8 BOM"``). On a plain UTF-8 file (the
    documented shard encoding), ``(text, None)``. On bytes that are neither BOM-prefixed
    nor valid UTF-8, ``(None, "not valid UTF-8")`` — nothing further can be validated.
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace"), "UTF-8 BOM"
    if raw.startswith(codecs.BOM_UTF16_LE):
        return raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le", errors="replace"), "UTF-16 LE BOM"
    if raw.startswith(codecs.BOM_UTF16_BE):
        return raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be", errors="replace"), "UTF-16 BE BOM"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "not valid UTF-8"
