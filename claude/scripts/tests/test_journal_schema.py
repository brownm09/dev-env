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
- ``TILE_REQUIRED_FIELDS`` order and ``missing_tile_fields`` (ADR-118): all-present, one
  missing, a missing ``prompt`` (the field without which a chip cannot be reconstructed),
  a stray ``task_id`` not masking a real omission, a missing subset in schema order,
  non-dict/empty entries, and — the copy-paste guard — that the tile and open-PR schemas
  are not interchangeable despite sharing ``url``/``stub`` and a numeric filename layout.
- ``decode_shard_bytes``: plain UTF-8, a UTF-8 BOM (text returned past the BOM, named
  problem), UTF-16 LE/BE BOMs, invalid UTF-8, and empty bytes.
- ``has_unresolved_open_pr`` (dev-env#651, ADR-091 Amendment 1): a `prs_opened` PR number
  absent from `prs_closed` is unresolved; a matching PR compared across int/str type
  mismatch, an already-resolved or never-opened entry, a partial multi-PR overlap, missing
  keys, a non-dict entry, and a non-list `prs_opened`/`prs_closed` value all return the
  documented conservative result.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _journal_schema as mod  # noqa: E402

REQUIRED_FIELDS = mod.REQUIRED_FIELDS
OPEN_PR_REQUIRED_FIELDS = mod.OPEN_PR_REQUIRED_FIELDS
TILE_REQUIRED_FIELDS = mod.TILE_REQUIRED_FIELDS
missing_required_fields = mod.missing_required_fields
missing_open_pr_fields = mod.missing_open_pr_fields
missing_tile_fields = mod.missing_tile_fields
malformed_manifest_fields = mod.malformed_manifest_fields
has_unresolved_open_pr = mod.has_unresolved_open_pr
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


def _valid_tile_entry(**overrides):
    base = {
        "issue": 868,
        "url": "https://github.com/brownm09/dev-env/issues/868",
        "title": "Tile persistence PR1: schema, store, write rule",
        "tldr": "Establish the on-disk tile-shard format and the rule that writes it.",
        "prompt": "Implement the tile-shard schema per dev-env#868. See #867 for full scope.",
        "cwd": "C:/Users/brown/Git/dev-env",
        "stub": "2026-07-22_140000.stub.md",
        "spawned": "2026-07-22",
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
# TILE_REQUIRED_FIELDS / missing_tile_fields (ADR-118)
# ---------------------------------------------------------------------------

def test_tile_field_order():
    assert TILE_REQUIRED_FIELDS == (
        "issue", "url", "title", "tldr", "prompt", "cwd", "stub", "spawned"
    )

def test_tile_all_fields_present():
    assert missing_tile_fields(_valid_tile_entry()) == []

def test_tile_missing_one_field():
    entry = _valid_tile_entry()
    del entry["url"]
    assert missing_tile_fields(entry) == ["url"]

def test_tile_missing_prompt_is_flagged():
    # The load-bearing field: without `prompt` the shard cannot reconstruct the chip, which
    # is the shard's entire reason to exist. A "looks populated" shard missing only this one
    # would otherwise pass a shallow eyeball check and fail silently at re-spawn time.
    entry = _valid_tile_entry()
    del entry["prompt"]
    assert missing_tile_fields(entry) == ["prompt"]

def test_tile_task_id_does_not_substitute_for_required_fields():
    # ADR-094's rejected "task_id record only" alternative: a chip ID is dead after restart.
    # Carrying one must not mask a missing required field.
    entry = _valid_tile_entry()
    del entry["cwd"]
    entry["task_id"] = "task_abc123"
    assert missing_tile_fields(entry) == ["cwd"]

def test_tile_missing_subset_in_schema_order():
    entry = _valid_tile_entry()
    del entry["title"]
    del entry["spawned"]
    assert missing_tile_fields(entry) == ["title", "spawned"]

def test_tile_non_dict_treated_as_missing_all():
    assert missing_tile_fields(None) == list(TILE_REQUIRED_FIELDS)

def test_tile_empty_dict_missing_all():
    assert missing_tile_fields({}) == list(TILE_REQUIRED_FIELDS)

def test_tile_and_open_pr_schemas_are_distinct():
    # Both are all-required single-object shards on the same numeric-filename layout, so a
    # copy-paste that validated a tile against the PR schema would "pass" on the shared `url`
    # /`stub` keys while silently ignoring `prompt`/`cwd`. Pin that they are not interchangeable.
    assert missing_tile_fields(_valid_open_pr_entry()) != []
    assert missing_open_pr_fields(_valid_tile_entry()) != []


# ---------------------------------------------------------------------------
# has_unresolved_open_pr (dev-env#651, ADR-091 Amendment 1)
# ---------------------------------------------------------------------------

def test_unresolved_pr_when_opened_not_closed():
    entry = _valid_manifest_entry(prs_opened=[635], prs_closed=[])
    assert has_unresolved_open_pr(entry) is True

def test_no_unresolved_pr_when_opened_and_closed_match():
    entry = _valid_manifest_entry(prs_opened=[633], prs_closed=[633])
    assert has_unresolved_open_pr(entry) is False

def test_no_unresolved_pr_when_opened_in_earlier_session_closed_here():
    entry = _valid_manifest_entry(prs_opened=[], prs_closed=[608])
    assert has_unresolved_open_pr(entry) is False

def test_no_unresolved_pr_when_nothing_opened():
    entry = _valid_manifest_entry(prs_opened=[], prs_closed=[])
    assert has_unresolved_open_pr(entry) is False

def test_unresolved_pr_string_int_type_mismatch_still_matches():
    entry = _valid_manifest_entry(prs_opened=[635], prs_closed=["635"])
    assert has_unresolved_open_pr(entry) is False

def test_unresolved_pr_missing_prs_closed_key():
    entry = _valid_manifest_entry(prs_opened=[635])
    del entry["prs_closed"]
    assert has_unresolved_open_pr(entry) is True

def test_unresolved_pr_missing_prs_opened_key():
    entry = _valid_manifest_entry(prs_closed=[608])
    del entry["prs_opened"]
    assert has_unresolved_open_pr(entry) is False

def test_unresolved_pr_non_dict_entry_returns_false():
    assert has_unresolved_open_pr(["not", "a", "dict"]) is False
    assert has_unresolved_open_pr(None) is False

def test_unresolved_pr_partial_overlap():
    entry = _valid_manifest_entry(prs_opened=[54, 55], prs_closed=[54])
    assert has_unresolved_open_pr(entry) is True

def test_unresolved_pr_non_list_prs_opened_conservative():
    entry = _valid_manifest_entry(prs_opened="635", prs_closed=[])
    assert has_unresolved_open_pr(entry) is True

def test_unresolved_pr_non_list_prs_closed_conservative():
    entry = _valid_manifest_entry(prs_opened=[635], prs_closed="635")
    assert has_unresolved_open_pr(entry) is True


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
# malformed_manifest_fields — tokens type/shape validation (dev-env #824)
# ---------------------------------------------------------------------------

def test_malformed_tokens_bare_int():
    entry = _valid_manifest_entry(tokens=0)
    result = malformed_manifest_fields(entry)
    assert len(result) == 1
    assert "int" in result[0]

def test_malformed_tokens_bare_string():
    entry = _valid_manifest_entry(tokens="input=0,output=0")
    result = malformed_manifest_fields(entry)
    assert len(result) == 1
    assert "str" in result[0]

def test_malformed_tokens_null():
    entry = _valid_manifest_entry(tokens=None)
    result = malformed_manifest_fields(entry)
    assert len(result) == 1
    assert "NoneType" in result[0]

def test_malformed_tokens_dict_missing_key():
    entry = _valid_manifest_entry(tokens={"input": 1, "output": 2})  # missing "cost"
    result = malformed_manifest_fields(entry)
    assert len(result) == 1
    assert "cost" in result[0]

def test_malformed_tokens_dict_non_numeric_value():
    entry = _valid_manifest_entry(tokens={"input": "many", "output": 200, "cost": 0.01})
    result = malformed_manifest_fields(entry)
    assert len(result) == 1
    assert "input" in result[0]

def test_malformed_tokens_valid_int_values():
    entry = _valid_manifest_entry(tokens={"input": 1000, "output": 200, "cost": 0})
    assert malformed_manifest_fields(entry) == []

def test_malformed_tokens_valid_float_cost():
    # Uses the fixture default: {"input": 1000, "output": 200, "cost": 0.01}
    entry = _valid_manifest_entry()
    assert malformed_manifest_fields(entry) == []

def test_malformed_tokens_absent_no_double_report():
    entry = _valid_manifest_entry()
    del entry["tokens"]
    assert malformed_manifest_fields(entry) == []

def test_malformed_tokens_non_dict_entry_no_double_report():
    assert malformed_manifest_fields(["not", "a", "dict"]) == []


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
