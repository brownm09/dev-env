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

A third consumer, ``stub-push-archive-reminder.py``, reads ``prs_opened``/``prs_closed`` via
``has_unresolved_open_pr()`` to gate the post-stub-push archive reminder on a same-session PR
still being open (dev-env#651, ADR-091 Amendment 1).

Three schemas are covered:

  - Manifest shards (``sessions/<project>/YYYY-MM-DD_HHMMSS.manifest.jsonl``):
    ``REQUIRED_FIELDS`` — kept in sync with docs/REFERENCE.md -> "Manifest shard format".
  - Open-PR tracking shards (``sessions/<project>/open-prs/<N>.json``):
    ``OPEN_PR_REQUIRED_FIELDS`` — kept in sync with docs/REFERENCE.md -> "Open-PR tracking
    shards". Unlike the manifest schema, every field is required (no optional fields
    documented for this shard kind).
  - Tile shards (``sessions/<project>/tiles/<issue-number>.json``, ADR-118):
    ``TILE_REQUIRED_FIELDS`` — kept in sync with docs/REFERENCE.md -> "Tile shards". Also
    all-required: a tile shard exists to reconstruct a lost ``spawn_task`` chip, and a
    partial one cannot do that, so there is no field it is meaningful to omit.

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

# The tile shard schema, in canonical (schema) order. Kept in sync with docs/REFERENCE.md →
# "Tile shards" (ADR-118). `title`/`tldr`/`prompt`/`cwd` are the four spawn_task arguments —
# together they are what makes an exact re-spawn possible, which is the whole point of the
# shard. `url` carries owner/repo so a bare-numeric filename still resolves cross-repo (the
# same trick the open-PR shard uses). `task_id` is deliberately absent: chip IDs do not
# survive an app restart (ADR-094), so storing one would persist a value that is dead
# exactly when the shard is needed.
#
# `stub` is OPTIONAL and deliberately absent from this tuple (the manifest schema's
# `priorities` is the same shape). Unlike an open-PR shard — always written by a session
# that also writes a stub — a tile can be spawned by a session that writes no stub at all:
# the tiling rule fires "the moment you identify" a follow-up, while the stub triggers are
# PR-open / PR-merge / report-generation. Requiring `stub` would force such a session to
# invent a value. When present it must be **project-qualified**
# (`sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`, the manifest convention) rather than the
# open-PR shard's bare filename: a tile shard is filed under its *target* project, so the
# spawning session's stub may live under a different one and a bare filename would not
# resolve.
TILE_REQUIRED_FIELDS = ("issue", "url", "title", "tldr", "prompt", "cwd", "spawned")

# Sub-keys required inside the `tokens` dict value (dev-env #824).
TOKENS_REQUIRED_KEYS = ("input", "output", "cost")


def missing_required_fields(entry: object, fields: tuple[str, ...] = REQUIRED_FIELDS) -> list[str]:
    """Return the fields in ``fields`` absent from a single parsed shard entry.

    Returned in the given order so reports are stable. A non-dict entry (a JSON
    list/scalar that slipped in) is treated as missing *every* field — it cannot
    satisfy the schema. "Missing" means the key is absent; a present-but-null value is
    out of scope (the issue is *omitted* fields, per #423).

    ``fields`` defaults to the manifest schema (``REQUIRED_FIELDS``) so every existing
    caller (``validate-manifest.py``, its tests) is unaffected by this parameter's
    addition. Pass ``OPEN_PR_REQUIRED_FIELDS`` (or use ``missing_open_pr_fields`` below)
    to validate an open-PR shard instead, or ``TILE_REQUIRED_FIELDS`` /
    ``missing_tile_fields`` for a tile shard.
    """
    if not isinstance(entry, dict):
        return list(fields)
    return [f for f in fields if f not in entry]


def missing_open_pr_fields(entry: object) -> list[str]:
    """``missing_required_fields`` specialized to the open-PR shard schema."""
    return missing_required_fields(entry, OPEN_PR_REQUIRED_FIELDS)


def missing_tile_fields(entry: object) -> list[str]:
    """``missing_required_fields`` specialized to the tile shard schema (ADR-118)."""
    return missing_required_fields(entry, TILE_REQUIRED_FIELDS)


def malformed_manifest_fields(entry: object) -> list[str]:
    """Return descriptions of present-but-malformed fields in a manifest entry.

    Currently validates only ``tokens``: it must be a dict with keys ``input``,
    ``output``, ``cost``, each numeric (int or float). A bare scalar, ``None``,
    or any other wrong type is flagged. Returns ``[]`` for non-dict entries (already
    caught by ``missing_required_fields``) and when ``tokens`` is absent (also caught
    there) — no double-reporting. (dev-env #824)
    """
    if not isinstance(entry, dict):
        return []
    if "tokens" not in entry:
        return []
    t = entry["tokens"]
    if not isinstance(t, dict):
        return [f"tokens: must be a dict with keys input/output/cost, got {type(t).__name__}"]
    problems = []
    missing_keys = [k for k in TOKENS_REQUIRED_KEYS if k not in t]
    if missing_keys:
        problems.append(f"tokens dict missing keys: {', '.join(missing_keys)}")
    bad_type_keys = [k for k in TOKENS_REQUIRED_KEYS if k in t and not isinstance(t[k], (int, float))]
    if bad_type_keys:
        problems.append(f"tokens keys not numeric: {', '.join(bad_type_keys)}")
    return problems


def has_unresolved_open_pr(entry: object) -> bool:
    """True if entry's ``prs_opened`` includes a PR number not also in ``prs_closed``.

    Compared as strings so an int- or str-typed PR number in either list still matches.
    A non-dict entry returns False (nothing to flag — a caller needing "can't confirm
    resolved" semantics on unreadable/unparseable input applies that at the file-read
    layer, e.g. ``stub-push-archive-reminder.py``'s ``head_commit_has_unresolved_pr``). A
    present ``prs_opened``/``prs_closed`` value that isn't a list (valid JSON, wrong field
    shape — e.g. a bare string PR number instead of a one-element list) returns True
    rather than silently misparsing (iterating a string's characters) or raising:
    malformed, so it cannot be confirmed resolved.
    """
    if not isinstance(entry, dict):
        return False
    opened = entry.get("prs_opened")
    closed = entry.get("prs_closed")
    if opened is not None and not isinstance(opened, list):
        return True
    if closed is not None and not isinstance(closed, list):
        return True
    opened_set = {str(n) for n in (opened or [])}
    closed_set = {str(n) for n in (closed or [])}
    return bool(opened_set - closed_set)


def find_entries_missing_fields(entries):
    """Pure contract: parsed manifest entries -> [(entry, [missing-fields]), ...].

    One tuple per entry that is missing at least one required field; entries with all
    required fields are omitted. The list preserves input order. This is the function
    the issue (#423) specifies — `journal-compose` uses ``main`` for source-aware
    reporting, but this is the stable, testable core.

    Manifest-schema only (fixed at the ``REQUIRED_FIELDS`` default) — open-PR shards are
    single-object files validated directly via ``missing_open_pr_fields``, not as a list.
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
