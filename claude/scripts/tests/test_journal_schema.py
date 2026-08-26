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
- ``malformed_tile_fields`` (dev-env#904, ADR-081 Amendment 2): the live corruption shape
  (``C:Users<U+0008>rownGitdev-env``, a backslash Windows path through a double-quoted
  ``node -e`` string literal), plus a wrong type, an empty/whitespace value, a relative
  path, and the deliberate non-flags — a valid backslash path, and a path that simply does
  not exist on this machine.
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
malformed_tile_fields = mod.malformed_tile_fields
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
        "stub": "sessions/dev-env/2026-07-22_140000.stub.md",
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
        "issue", "url", "title", "tldr", "prompt", "cwd", "spawned"
    )

def test_tile_stub_is_optional():
    # `stub` is provenance, not payload, and is genuinely not always knowable: the tiling
    # rule fires the moment a follow-up is identified, while the stub triggers are
    # PR-open / PR-merge / report-generation — so a session that tiles something in passing
    # writes no stub at all. Requiring it would force that session to invent a value.
    entry = _valid_tile_entry()
    del entry["stub"]
    assert missing_tile_fields(entry) == []

def test_tile_stub_present_is_project_qualified():
    # When present it must carry its project, unlike the open-PR shard's bare filename: a
    # tile shard is filed under its *target* project, so the spawning session's stub can
    # live under a different one and a bare filename would not resolve.
    assert _valid_tile_entry()["stub"].startswith("sessions/")

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
# malformed_tile_fields — `cwd` plausibility (dev-env#904, ADR-081 Amendment 2)
# ---------------------------------------------------------------------------

# The exact value found live in sessions/dev-env/tiles/{898,899,900}.json. Written as an
# explicit escape rather than pasted so the corruption survives this file being reformatted:
# `C:\Users\brown\Git\dev-env` through a double-quoted `node -e` string literal loses `\U`
# and `\G` and turns `\b` into U+0008.
_CORRUPT_CWD = "C:Users\brownGitdev-env"


def test_corrupt_cwd_fixture_is_the_real_shape():
    # Guards the fixture itself: if a future edit "helpfully" fixes the escape, every test
    # below would silently stop exercising the class it was written for.
    assert _CORRUPT_CWD == "C:Users" + chr(0x08) + "rownGitdev-env"
    assert "\\" not in _CORRUPT_CWD and "/" not in _CORRUPT_CWD


def test_tile_healthy_cwd_is_not_flagged():
    assert malformed_tile_fields(_valid_tile_entry()) == []


def test_tile_corrupt_cwd_flagged_as_control_character():
    problems = malformed_tile_fields(_valid_tile_entry(cwd=_CORRUPT_CWD))
    assert len(problems) == 1, problems
    # Names the codepoint and the cause, so the fix is readable off the message alone.
    assert "U+0008" in problems[0], problems
    assert "node -e" in problems[0], problems
    assert "forward slashes" in problems[0], problems


def test_tile_control_character_reported_alone_not_also_as_relative():
    # The corrupt value is *also* non-absolute, but restating one defect twice makes the
    # real diagnosis harder to read — the control character is the cause, so it wins.
    problems = malformed_tile_fields(_valid_tile_entry(cwd=_CORRUPT_CWD))
    assert not any("not an absolute path" in p for p in problems), problems


def test_tile_control_character_flagged_even_when_path_is_absolute():
    # A mangled path can keep a valid-looking drive root; the absolute-path check alone
    # would pass it.
    problems = malformed_tile_fields(_valid_tile_entry(cwd="C:/Users/brown/Git/de\bv-env"))
    assert len(problems) == 1, problems
    assert "U+0008" in problems[0], problems


def test_tile_relative_cwd_flagged():
    problems = malformed_tile_fields(_valid_tile_entry(cwd="Git/dev-env"))
    assert len(problems) == 1, problems
    assert "not an absolute path" in problems[0], problems


def test_tile_bare_word_cwd_flagged():
    # "A value with no separator at all is unambiguously corrupt" — the issue's own bar.
    problems = malformed_tile_fields(_valid_tile_entry(cwd="dev-env"))
    assert len(problems) == 1, problems
    assert "no path separator" in problems[0], problems


def test_tile_unc_cwd_accepted_both_slash_forms():
    # Found by this PR's own /review. A UNC path is a *valid* absolute Windows path, so
    # flagging it would fire on a correct value — exactly what this checker's stated rule
    # forbids. `\\wsl$\...` is plausible on this Windows/WSL setup, and the backslash form
    # was the one being rejected (the forward-slash form passed only incidentally, via the
    # POSIX-absolute alternative).
    assert malformed_tile_fields(_valid_tile_entry(cwd=r"\\wsl$\Ubuntu\home\brown\repo")) == []
    assert malformed_tile_fields(_valid_tile_entry(cwd=r"\\server\share\repo")) == []
    assert malformed_tile_fields(_valid_tile_entry(cwd="//wsl$/Ubuntu/home/brown/repo")) == []


def test_tile_single_backslash_root_still_flagged():
    # The regression pin for the fix above: `\Users\brown\...` is *drive-relative*, not
    # absolute (it resolves against the current drive), so widening the pattern for UNC must
    # not also admit it.
    problems = malformed_tile_fields(_valid_tile_entry(cwd=r"\Users\brown\Git\dev-env"))
    assert len(problems) == 1, problems
    assert "not an absolute path" in problems[0], problems


def test_tile_bare_drive_letter_flagged():
    # "C:" names the current directory on drive C:, not the drive root — no separator, so it
    # cannot be a repo root.
    problems = malformed_tile_fields(_valid_tile_entry(cwd="C:"))
    assert len(problems) == 1, problems
    assert "not an absolute path" in problems[0], problems


def test_tile_surrounding_whitespace_flagged_both_sides():
    # Also from this PR's /review: the absolute-path regex is start-anchored, so leading
    # whitespace was reported with the misleading "not an absolute path" while *trailing*
    # whitespace passed entirely. Both are quoting artifacts, and Windows silently strips
    # trailing spaces from path components — so the two values compare unequal to the real
    # path while resolving to it.
    for value in ("  C:/Users/brown/Git/dev-env", "C:/Users/brown/Git/dev-env  "):
        problems = malformed_tile_fields(_valid_tile_entry(cwd=value))
        assert len(problems) == 1, (value, problems)
        assert "leading or trailing whitespace" in problems[0], (value, problems)


def test_tile_posix_absolute_cwd_accepted():
    # The journal is read on Windows today, but the schema is not Windows-only and a POSIX
    # root must not be reported as corrupt.
    assert malformed_tile_fields(_valid_tile_entry(cwd="/home/brown/git/dev-env")) == []


def test_tile_backslash_cwd_is_valid_and_not_flagged():
    # Deliberate non-flag: a correctly-escaped Windows path is a *correct* value. Only the
    # escaping layer it must survive is fragile, and that is a docs rule (REFERENCE.md ->
    # Tile shards), not a validation one. Flagging it would fire this advisory on healthy
    # shards every time one is merely named in a command.
    assert malformed_tile_fields(_valid_tile_entry(cwd=r"C:\Users\brown\Git\dev-env")) == []


def test_tile_nonexistent_but_well_formed_cwd_is_not_flagged():
    # Deliberate non-flag: shards are read on machines other than the one that wrote them,
    # and this module is import-only/offline. Plausibility is the bar, not existence.
    assert malformed_tile_fields(_valid_tile_entry(cwd="D:/no/such/directory/anywhere")) == []


def test_tile_non_string_cwd_flagged_by_type():
    problems = malformed_tile_fields(_valid_tile_entry(cwd=["C:/Users/brown/Git/dev-env"]))
    assert len(problems) == 1, problems
    assert "must be a string path, got list" in problems[0], problems


def test_tile_empty_and_whitespace_cwd_flagged():
    for value in ("", "   "):
        problems = malformed_tile_fields(_valid_tile_entry(cwd=value))
        assert len(problems) == 1, (value, problems)
        assert "empty" in problems[0], (value, problems)


def test_tile_absent_cwd_not_double_reported():
    # missing_tile_fields already reports it; this must stay silent so one omission does
    # not produce two problem lines (matches malformed_manifest_fields' contract).
    entry = _valid_tile_entry()
    del entry["cwd"]
    assert malformed_tile_fields(entry) == []
    assert missing_tile_fields(entry) == ["cwd"]


def test_tile_malformed_on_non_dict_is_empty():
    assert malformed_tile_fields(None) == []
    assert malformed_tile_fields(["not", "a", "dict"]) == []


def test_tile_malformed_messages_are_ascii():
    # These ride the hook's exit-2 stderr, which is cp1252-decoded on Windows.
    for cwd in (_CORRUPT_CWD, "dev-env", "", 17):
        for problem in malformed_tile_fields(_valid_tile_entry(cwd=cwd)):
            assert problem.isascii(), (cwd, problem)


def test_tile_long_corrupt_cwd_echo_is_bounded():
    # One pathological shard must not flood stderr with its own contents.
    problems = malformed_tile_fields(_valid_tile_entry(cwd="x" * 5000))
    assert len(problems) == 1, problems
    assert len(problems[0]) < 400, len(problems[0])
    assert "..." in problems[0], problems


# ---------------------------------------------------------------------------
# malformed_tile_fields — `stub` / `task_id` (dev-env#907, ADR-081 Amendment 3)
# ---------------------------------------------------------------------------

def test_tile_qualified_stub_not_flagged():
    # The fixture's own stub is already project-qualified — belt-and-suspenders alongside
    # test_tile_healthy_cwd_is_not_flagged, which also asserts on the fixture as a whole.
    assert malformed_tile_fields(_valid_tile_entry()) == []


def test_tile_bare_filename_stub_flagged():
    # The exact live shape found in dev-env#907 and reconfirmed by this session's own sweep,
    # e.g. career-playbook/tiles/1009.json: "2026-07-28_174500.stub.md", no project at all.
    problems = malformed_tile_fields(_valid_tile_entry(stub="2026-07-28_174500.stub.md"))
    assert len(problems) == 1, problems
    assert "not project-qualified" in problems[0], problems
    assert "sessions/" in problems[0], problems


def test_tile_project_prefixed_but_unqualified_stub_flagged():
    # Also live: a project name up front but missing the "sessions/" root, e.g.
    # career-playbook/tiles/1047.json: "career-playbook/2026-08-03_012340.stub.md". Having
    # *a* prefix doesn't make it qualified — only "sessions/<project>/..." does.
    problems = malformed_tile_fields(
        _valid_tile_entry(stub="career-playbook/2026-08-03_012340.stub.md")
    )
    assert len(problems) == 1, problems
    assert "not project-qualified" in problems[0], problems


def test_tile_absent_stub_not_flagged():
    # stub is optional (test_tile_stub_is_optional pins this for missing_tile_fields);
    # malformed_tile_fields must agree there is nothing to flag when it's simply not there.
    entry = _valid_tile_entry()
    del entry["stub"]
    assert malformed_tile_fields(entry) == []


def test_tile_non_string_stub_flagged_by_type():
    problems = malformed_tile_fields(_valid_tile_entry(stub=["sessions/dev-env/x.stub.md"]))
    assert len(problems) == 1, problems
    assert "must be a string path, got list" in problems[0], problems


def test_tile_long_bad_stub_echo_is_bounded():
    problems = malformed_tile_fields(_valid_tile_entry(stub="x" * 5000))
    assert len(problems) == 1, problems
    assert len(problems[0]) < 400, len(problems[0])
    assert "..." in problems[0], problems


def test_tile_present_task_id_flagged():
    # ADR-118: "deliberately not stored" — a chip ID is dead after an app restart. The exact
    # live shape from dev-env#907: career-playbook/tiles/849.json carried "task_cdc4d05c".
    problems = malformed_tile_fields(_valid_tile_entry(task_id="task_cdc4d05c"))
    assert len(problems) == 1, problems
    assert "task_id" in problems[0], problems
    assert "deliberately not stored" in problems[0], problems


def test_tile_absent_task_id_not_flagged():
    assert malformed_tile_fields(_valid_tile_entry()) == []
    assert "task_id" not in _valid_tile_entry()


def test_tile_stub_and_task_id_problems_accumulate_with_cwd():
    # The architectural change this PR makes to malformed_tile_fields: unlike a single-field
    # check (malformed_manifest_fields' `tokens`), cwd/stub/task_id are three independent
    # fields and a shard can have more than one wrong at once — all three must be reported,
    # not just the first found. dev-env#907's own motivating shard (849.json) actually had
    # exactly two of these three problems simultaneously (task_id + bad stub).
    entry = _valid_tile_entry(
        cwd="Git/dev-env", stub="2026-07-23_021500.stub.md", task_id="task_cdc4d05c"
    )
    problems = malformed_tile_fields(entry)
    assert len(problems) == 3, problems
    assert any(p.startswith("cwd:") for p in problems), problems
    assert any(p.startswith("stub:") for p in problems), problems
    assert any(p.startswith("task_id:") for p in problems), problems


def test_tile_stub_and_task_id_messages_are_ascii():
    # Same rationale as test_tile_malformed_messages_are_ascii: these ride the hook's exit-2
    # stderr, which is cp1252-decoded on Windows.
    entry = _valid_tile_entry(stub="not/project/qualified.stub.md", task_id="task_abc123")
    for problem in malformed_tile_fields(entry):
        assert problem.isascii(), problem


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
