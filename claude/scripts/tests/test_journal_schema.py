#!/usr/bin/env python3
"""Tests for _journal_schema.py — shared shard schema/validation helpers (dev-env #556,
ADR-081).

Exercises ``missing_required_fields`` / ``missing_open_pr_fields`` /
``find_entries_missing_fields`` / ``parse_manifest_text`` / ``decode_shard_bytes`` offline
(no disk, no network, no subprocess). This module has no ``main()`` — every export is
pure and covered here.

Cases pinned:
- Moved-function parity (matches the coverage ``test_validate_manifest.py`` already pins
  for the pre-extraction versions of these four): all-fields-present, a missing subset in
  canonical schema order, and a non-dict entry treated as missing every field.
- ``OPEN_PR_REQUIRED_FIELDS`` order and ``missing_open_pr_fields``: all-present, one
  missing, the ``summary``-instead-of-``topic`` shape from the 2026-07-02 meta-shard
  incident, and a non-dict entry.
- ``decode_shard_bytes``: plain UTF-8, a UTF-8 BOM (text returned past the BOM, named
  problem), UTF-16 LE/BE BOMs, invalid UTF-8, and empty bytes.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _journal_schema as mod  # noqa: E402

REQUIRED_FIELDS = mod.REQUIRED_FIELDS
OPEN_PR_REQUIRED_FIELDS = mod.OPEN_PR_REQUIRED_FIELDS
missing_required_fields = mod.missing_required_fields
missing_open_pr_fields = mod.missing_open_pr_fields
find_entries_missing_fields = mod.find_entries_missing_fields
parse_manifest_text = mod.parse_manifest_text
decode_shard_bytes = mod.decode_shard_bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_manifest_entry(**overrides):
    base = {
        "stub": "sessions/dev-env/2026-06-30_120000.stub.md",
        "topic": "schema module",
        "tokens": {"input": 1000, "output": 200, "cost": 0.01},
        "prs_opened": [],
        "prs_closed": [],
    }
    base.update(overrides)
    return base


def _valid_open_pr_entry(**overrides):
    base = {
        "pr": 556,
        "url": "https://github.com/brownm09/dev-env/pull/556",
        "topic": "journal shard write advisory",
        "stub": "2026-07-03_170000.stub.md",
        "opened": "2026-07-03",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# REQUIRED_FIELDS / missing_required_fields — moved-function parity
# ---------------------------------------------------------------------------

def test_manifest_all_fields_present():
    assert missing_required_fields(_valid_manifest_entry()) == []

def test_manifest_missing_subset_in_schema_order():
    entry = _valid_manifest_entry()
    del entry["topic"]
    del entry["tokens"]
    assert missing_required_fields(entry) == ["topic", "tokens"]

def test_manifest_non_dict_treated_as_missing_all():
    assert missing_required_fields(["not", "a", "dict"]) == list(REQUIRED_FIELDS)

def test_manifest_missing_all_on_empty_dict():
    assert missing_required_fields({}) == list(REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# OPEN_PR_REQUIRED_FIELDS / missing_open_pr_fields
# ---------------------------------------------------------------------------

def test_open_pr_field_order():
    assert OPEN_PR_REQUIRED_FIELDS == ("pr", "url", "topic", "stub", "opened")

def test_open_pr_all_fields_present():
    assert missing_open_pr_fields(_valid_open_pr_entry()) == []

def test_open_pr_missing_one_field():
    entry = _valid_open_pr_entry()
    del entry["url"]
    assert missing_open_pr_fields(entry) == ["url"]

def test_open_pr_summary_instead_of_topic_incident_shape():
    # The 2026-07-02 meta-shard incident: a "summary" key was written where "topic" was
    # required — missing_open_pr_fields must flag "topic" as absent (summary doesn't count).
    entry = _valid_open_pr_entry()
    del entry["topic"]
    entry["summary"] = "session summary text"
    assert missing_open_pr_fields(entry) == ["topic"]

def test_open_pr_non_dict_treated_as_missing_all():
    assert missing_open_pr_fields(None) == list(OPEN_PR_REQUIRED_FIELDS)

def test_open_pr_empty_dict_missing_all():
    assert missing_open_pr_fields({}) == list(OPEN_PR_REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# find_entries_missing_fields / parse_manifest_text — smoke (full coverage stays in
# test_validate_manifest.py; these confirm the re-export/extraction didn't change behavior)
# ---------------------------------------------------------------------------

def test_find_entries_missing_fields_smoke():
    bad = _valid_manifest_entry()
    del bad["stub"]
    good = _valid_manifest_entry()
    result = find_entries_missing_fields([bad, good])
    assert len(result) == 1
    assert result[0][0] is bad
    assert result[0][1] == ["stub"]

def test_parse_manifest_text_smoke():
    import json
    entry = _valid_manifest_entry()
    assert parse_manifest_text(json.dumps(entry)) == [(1, entry)]


# ---------------------------------------------------------------------------
# decode_shard_bytes
# ---------------------------------------------------------------------------

def test_decode_plain_utf8():
    raw = '{"a":1}'.encode("utf-8")
    text, problem = decode_shard_bytes(raw)
    assert text == '{"a":1}'
    assert problem is None

def test_decode_utf8_bom():
    raw = b"\xef\xbb\xbf" + '{"a":1}'.encode("utf-8")
    text, problem = decode_shard_bytes(raw)
    assert text == '{"a":1}'
    assert problem == "UTF-8 BOM"

def test_decode_utf16_le_bom():
    raw = '{"a":1}'.encode("utf-16-le")
    raw = b"\xff\xfe" + raw
    text, problem = decode_shard_bytes(raw)
    assert text == '{"a":1}'
    assert problem == "UTF-16 LE BOM"

def test_decode_utf16_be_bom():
    raw = b"\xfe\xff" + '{"a":1}'.encode("utf-16-be")
    text, problem = decode_shard_bytes(raw)
    assert text == '{"a":1}'
    assert problem == "UTF-16 BE BOM"

def test_decode_invalid_utf8():
    # Lone continuation bytes (0x80-0xBF with no leading byte) are invalid UTF-8 and,
    # unlike the fixture this replaced, don't collide with any of the three recognized
    # BOM prefixes (0xEF 0xBB 0xBF / 0xFF 0xFE / 0xFE 0xFF).
    raw = b"\x80\x81\x82not valid utf-8"
    text, problem = decode_shard_bytes(raw)
    assert text is None
    assert problem == "not valid UTF-8"

def test_decode_empty_bytes():
    text, problem = decode_shard_bytes(b"")
    assert text == ""
    assert problem is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
