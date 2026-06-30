#!/usr/bin/env python3
"""Tests for validate-manifest.py pure helpers.

Exercises ``missing_required_fields``, ``find_entries_missing_fields``, and
``parse_manifest_text`` offline (no disk, no network, no subprocess). The ``main()``
entry point is not covered (pure-helper convention; matches all other test_*.py in this
directory). See dev-env issue #423.

Cases pinned:
- All five required fields present -> no missing fields reported.
- One or more required fields absent -> the absent fields are returned in schema order.
- A non-dict entry (list or scalar) is treated as missing every required field.
- An empty dict is missing every required field.
- ``find_entries_missing_fields`` returns only entries with at least one missing field,
  preserving input order, skipping fully-valid entries.
- ``parse_manifest_text``:
  - Blank / whitespace-only lines are skipped.
  - A valid JSON object yields (lineno, dict).
  - An invalid JSON line yields (lineno, None).
  - A JSON non-object (list, scalar) yields (lineno, None).
  - ADR-056 single-object shard (one line) is parsed as one entry.
  - Legacy per-day manifest (multiple JSON objects, one per line) is parsed as multiple entries.
"""
import importlib.util
import os
import sys

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "validate-manifest.py")
spec = importlib.util.spec_from_file_location("validate_manifest", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

missing_required_fields = mod.missing_required_fields
find_entries_missing_fields = mod.find_entries_missing_fields
parse_manifest_text = mod.parse_manifest_text
REQUIRED_FIELDS = mod.REQUIRED_FIELDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_entry(**overrides):
    """Return a fully-valid manifest entry, optionally with overrides."""
    base = {
        "stub": "sessions/dev-env/2026-06-30_120000.stub.md",
        "topic": "manifest validator",
        "tokens": {"input": 1000, "output": 200, "cost": 0.01},
        "prs_opened": [],
        "prs_closed": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# missing_required_fields
# ---------------------------------------------------------------------------

def test_all_fields_present():
    assert missing_required_fields(_valid_entry()) == []

def test_all_fields_plus_optional_priorities():
    entry = _valid_entry()
    entry["priorities"] = [{"label": "Ship it", "ref": "dev-env#1"}]
    assert missing_required_fields(entry) == []

def test_missing_topic():
    entry = _valid_entry()
    del entry["topic"]
    assert missing_required_fields(entry) == ["topic"]

def test_missing_tokens():
    entry = _valid_entry()
    del entry["tokens"]
    assert missing_required_fields(entry) == ["tokens"]

def test_missing_prs_opened():
    entry = _valid_entry()
    del entry["prs_opened"]
    assert missing_required_fields(entry) == ["prs_opened"]

def test_missing_prs_closed():
    entry = _valid_entry()
    del entry["prs_closed"]
    assert missing_required_fields(entry) == ["prs_closed"]

def test_missing_stub():
    entry = _valid_entry()
    del entry["stub"]
    assert missing_required_fields(entry) == ["stub"]

def test_missing_multiple_fields():
    # An older schema entry missing both topic and tokens (the 2026-06-13 career-playbook case).
    entry = _valid_entry()
    del entry["topic"]
    del entry["tokens"]
    result = missing_required_fields(entry)
    assert result == ["topic", "tokens"]  # canonical schema order

def test_missing_all_five():
    assert missing_required_fields({}) == list(REQUIRED_FIELDS)

def test_non_dict_list():
    # A JSON list that snuck in is treated as missing every field.
    result = missing_required_fields(["stub", "topic"])
    assert result == list(REQUIRED_FIELDS)

def test_non_dict_scalar_string():
    result = missing_required_fields("bare string")
    assert result == list(REQUIRED_FIELDS)

def test_non_dict_scalar_int():
    result = missing_required_fields(42)
    assert result == list(REQUIRED_FIELDS)

def test_non_dict_none():
    result = missing_required_fields(None)
    assert result == list(REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# find_entries_missing_fields
# ---------------------------------------------------------------------------

def test_find_empty_list():
    assert find_entries_missing_fields([]) == []

def test_find_all_valid():
    entries = [_valid_entry(), _valid_entry(stub="other.md")]
    assert find_entries_missing_fields(entries) == []

def test_find_one_bad_one_good():
    bad = _valid_entry()
    del bad["prs_closed"]
    good = _valid_entry()
    result = find_entries_missing_fields([bad, good])
    assert len(result) == 1
    assert result[0][0] is bad
    assert result[0][1] == ["prs_closed"]

def test_find_preserves_order():
    e1 = _valid_entry()
    del e1["stub"]
    e2 = _valid_entry()  # valid — skipped
    e3 = _valid_entry()
    del e3["topic"]
    del e3["tokens"]
    result = find_entries_missing_fields([e1, e2, e3])
    assert len(result) == 2
    assert result[0][0] is e1
    assert result[1][0] is e3

def test_find_non_dict_entry():
    entries = [["not", "a", "dict"], _valid_entry()]
    result = find_entries_missing_fields(entries)
    assert len(result) == 1
    assert result[0][1] == list(REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# parse_manifest_text
# ---------------------------------------------------------------------------

def test_parse_empty_string():
    assert parse_manifest_text("") == []

def test_parse_blank_lines_skipped():
    text = "\n\n   \n\t\n"
    assert parse_manifest_text(text) == []

def test_parse_single_object_shard():
    # ADR-056 per-session shard: exactly one JSON object on one line.
    import json
    entry = _valid_entry()
    text = json.dumps(entry)
    result = parse_manifest_text(text)
    assert result == [(1, entry)]

def test_parse_legacy_multiline():
    # Legacy per-day manifest: one JSON object per line.
    import json
    e1 = _valid_entry(stub="s1.stub.md")
    e2 = _valid_entry(stub="s2.stub.md")
    text = json.dumps(e1) + "\n" + json.dumps(e2) + "\n"
    result = parse_manifest_text(text)
    assert result == [(1, e1), (2, e2)]

def test_parse_invalid_json_line():
    result = parse_manifest_text("this is not json\n")
    assert result == [(1, None)]

def test_parse_json_list_yields_none():
    result = parse_manifest_text('["a","b"]\n')
    assert result == [(1, None)]

def test_parse_json_scalar_yields_none():
    result = parse_manifest_text("42\n")
    assert result == [(1, None)]

def test_parse_mixed_valid_invalid():
    import json
    e1 = _valid_entry()
    text = json.dumps(e1) + "\nbad json line\n" + json.dumps(_valid_entry(stub="s2.md"))
    result = parse_manifest_text(text)
    assert result[0] == (1, e1)
    assert result[1] == (2, None)   # parse error -> None
    assert result[2][1] is not None  # line 3 valid

def test_parse_lineno_tracks_blanks():
    # Blank lines are skipped but lineno still counts them, so the reported lineno reflects
    # position in the actual file (useful for humans patching the entry).
    import json
    e1 = _valid_entry()
    text = "\n\n" + json.dumps(e1)  # entry is on line 3
    result = parse_manifest_text(text)
    assert len(result) == 1
    assert result[0][0] == 3

def test_parse_trailing_whitespace_on_line():
    # A line with trailing spaces still parses correctly.
    import json
    e1 = _valid_entry()
    text = json.dumps(e1) + "   "
    result = parse_manifest_text(text)
    assert result == [(1, e1)]


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
