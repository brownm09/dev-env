#!/usr/bin/env python3
"""Unit tests for _hookutil.py — sentinel, transcript-locate, and reader helpers.

_hookutil.py is the shared utility module for the Stop / UserPromptSubmit hook
family: per-session sentinels + transcript-locate (extracted from near-verbatim
copies in posttooluse-inert-advisory.py, reconcile-open-prs.py, and
token-tracker.py — ADR-064), and the transcript-record readers `load_records` /
`_parse_records` / `iter_bash_calls` / `_result_text` / `_content_items`
(extracted from posttooluse-inert-advisory.py and stop-tile-enumeration-gate.py —
ADR-090).

Exercises the pure helpers offline (tmp dirs, injected paths, hand-built records —
no real ~/.claude/scratch or ~/.claude/projects).  The live sentinel write path in
the consuming hooks' main() functions is not covered (pure-helper convention).

Usage:
    py -3 claude/scripts/tests/test_hookutil.py

Exit 0 = all pass.
"""
import os
import sys
import time
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _hookutil

PREFIX = "test-hookutil-"


def test_sentinel_path_returns_correct_path() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = _hookutil.sentinel_path(PREFIX, "abc123", scratch=Path(root))
        assert p.name == f"{PREFIX}abc123.flag", f"unexpected name {p.name!r}"
        assert p.parent == Path(root), f"unexpected parent {p.parent}"
    return "sentinel_path returns scratch / f'{prefix}{session_id}.flag'"


def test_sentinel_path_default_scratch() -> str:
    p = _hookutil.sentinel_path(PREFIX, "abc123")
    assert p.parent == _hookutil.SCRATCH, f"default parent should be SCRATCH, got {p.parent}"
    assert p.name == f"{PREFIX}abc123.flag"
    return "default scratch is SCRATCH (~/.claude/scratch)"


def test_cleanup_removes_stale_keeps_fresh() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        old = scratch / f"{PREFIX}old.flag"
        fresh = scratch / f"{PREFIX}fresh.flag"
        old.write_text("")
        fresh.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(old, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch)
        assert not old.exists(), "stale sentinel should have been removed"
        assert fresh.exists(), "fresh sentinel should be kept"
    return "sentinels older than MAX_AGE_DAYS are removed; fresh ones are kept"


def test_cleanup_ignores_different_prefix() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        other = scratch / "other-prefix-old.flag"
        other.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(other, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch)
        assert other.exists(), "file with a different prefix must not be removed"
    return "cleanup only removes files matching the given prefix"


def test_cleanup_no_crash_on_missing_dir() -> str:
    with tempfile.TemporaryDirectory() as root:
        nonexistent = Path(root) / "no-such-dir"
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=nonexistent)
    return "cleanup_stale_sentinels does not raise when scratch dir is absent"


def test_find_transcript_found() -> str:
    with tempfile.TemporaryDirectory() as root:
        projects = Path(root)
        proj_dir = projects / "C--Users-brown-Git-repo"
        proj_dir.mkdir(parents=True)
        jsonl = proj_dir / "abc123.jsonl"
        jsonl.write_text("")
        result = _hookutil.find_transcript("abc123", projects=projects)
        assert result == jsonl, f"expected {jsonl}, got {result}"
    return "find_transcript returns the matching path"


def test_find_transcript_not_found() -> str:
    with tempfile.TemporaryDirectory() as root:
        result = _hookutil.find_transcript("nonexistent", projects=Path(root))
        assert result is None, f"expected None, got {result}"
    return "find_transcript returns None when no matching jsonl"


def test_find_transcript_nested() -> str:
    # Transcript two levels below the projects root (e.g. a subagent JSONL under
    # a project dir) — the `**` glob must still find it.
    with tempfile.TemporaryDirectory() as root:
        projects = Path(root)
        nested = projects / "proj" / "subagents"
        nested.mkdir(parents=True)
        jsonl = nested / "sid42.jsonl"
        jsonl.write_text("")
        result = _hookutil.find_transcript("sid42", projects=projects)
        assert result == jsonl, f"expected {jsonl}, got {result}"
    return "find_transcript finds jsonl nested in a project subdirectory"


# --- transcript-record readers (ADR-090) --------------------------------------

def test_content_items_returns_list() -> str:
    rec = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    assert _hookutil._content_items(rec) == [{"type": "text", "text": "hi"}]
    return "_content_items returns message.content when it is a list"


def test_content_items_guards() -> str:
    assert _hookutil._content_items("not-a-dict") == []
    assert _hookutil._content_items({"type": "assistant"}) == []            # no message
    assert _hookutil._content_items({"message": "not-a-dict"}) == []        # message not a dict
    assert _hookutil._content_items({"message": {"content": "str"}}) == []  # content not a list
    return "_content_items returns [] for a non-dict rec / message / non-list content"


def test_result_text_string_content() -> str:
    assert _hookutil._result_text({"content": "the URL"}, {}) == "the URL"
    return "_result_text returns string content verbatim"


def test_result_text_list_content() -> str:
    item = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert _hookutil._result_text(item, {}) == "a\nb"
    return "_result_text joins the text parts of list content"


def test_result_text_tooluseresult_fallback() -> str:
    # Empty per-id content -> fall back to the record's structured stdout+stderr.
    item = {"content": ""}
    rec = {"toolUseResult": {"stdout": "out", "stderr": "warn"}}
    assert _hookutil._result_text(item, rec) == "out\nwarn"
    return "_result_text falls back to toolUseResult stdout+stderr on empty content"


def test_result_text_empty() -> str:
    assert _hookutil._result_text({"content": ""}, {}) == ""
    assert _hookutil._result_text({}, {}) == ""
    return "_result_text returns '' when there is no content and no toolUseResult"


def test_iter_bash_calls_pairs_by_id_with_cwd() -> str:
    records = [
        {"type": "assistant", "cwd": "/repo", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "echo hi"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "hi"}]}},
    ]
    assert _hookutil.iter_bash_calls(records) == [("echo hi", "hi", "/repo")]
    return "iter_bash_calls returns (command, output, cwd) paired by tool_use_id"


def test_iter_bash_calls_parallel_not_mismatched() -> str:
    # Two parallel Bash calls; results arrive out of order. Pairing is by id.
    records = [
        {"type": "assistant", "cwd": "/w", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "a", "input": {"command": "CMD_A"}},
            {"type": "tool_use", "name": "Bash", "id": "b", "input": {"command": "CMD_B"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "OUT_B"}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "OUT_A"}]}},
    ]
    calls = _hookutil.iter_bash_calls(records)
    assert ("CMD_A", "OUT_A", "/w") in calls, f"got {calls!r}"
    assert ("CMD_B", "OUT_B", "/w") in calls, f"got {calls!r}"
    return "iter_bash_calls pairs by id, so out-of-order parallel results don't cross"


def test_iter_bash_calls_default_cwd_empty() -> str:
    records = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "t1", "input": {"command": "x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "y"}]}},
    ]
    assert _hookutil.iter_bash_calls(records) == [("x", "y", "")]
    return "iter_bash_calls yields cwd='' when the assistant record has no cwd"


def test_iter_bash_calls_unmatched_and_malformed() -> str:
    # Non-dict records, a non-dict message, a non-Bash tool_use, and an orphan
    # tool_result (no matching tool_use) are all ignored without raising.
    records = [
        None, 123, "str", [],
        {"type": "assistant", "message": ["not-a-dict"]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "id": "r1", "input": {"file_path": "x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "orphan", "content": "z"}]}},
    ]
    assert _hookutil.iter_bash_calls(records) == []
    return "iter_bash_calls: non-dict/non-Bash/orphan records -> [] without raising"


def test_parse_records_filters_non_objects() -> str:
    text = '{"a": 1}\n\n  \nnot json\n123\n"str"\n[1,2]\nnull\n{"b": 2}\n'
    assert _hookutil._parse_records(text) == [{"a": 1}, {"b": 2}]
    return "_parse_records keeps only JSON objects (drops blank/malformed/non-object lines)"


def test_load_records_reads_object_records() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        p.write_text('{"type": "assistant"}\n42\n{"type": "user"}\n', encoding="utf-8")
        assert _hookutil.load_records(p) == [{"type": "assistant"}, {"type": "user"}]
    return "load_records reads a JSONL file and returns its object records (42 dropped)"


def main() -> int:
    tests = [
        ("sentinel_path: correct path with override", test_sentinel_path_returns_correct_path),
        ("sentinel_path: default SCRATCH parent", test_sentinel_path_default_scratch),
        ("cleanup: removes stale, keeps fresh", test_cleanup_removes_stale_keeps_fresh),
        ("cleanup: ignores different prefix", test_cleanup_ignores_different_prefix),
        ("cleanup: no crash on missing dir", test_cleanup_no_crash_on_missing_dir),
        ("find_transcript: found", test_find_transcript_found),
        ("find_transcript: not found -> None", test_find_transcript_not_found),
        ("find_transcript: nested dir", test_find_transcript_nested),
        ("_content_items: returns list", test_content_items_returns_list),
        ("_content_items: guards -> []", test_content_items_guards),
        ("_result_text: string content", test_result_text_string_content),
        ("_result_text: list content", test_result_text_list_content),
        ("_result_text: toolUseResult fallback", test_result_text_tooluseresult_fallback),
        ("_result_text: empty -> ''", test_result_text_empty),
        ("iter_bash_calls: pairs by id (+cwd)", test_iter_bash_calls_pairs_by_id_with_cwd),
        ("iter_bash_calls: parallel not mismatched", test_iter_bash_calls_parallel_not_mismatched),
        ("iter_bash_calls: default cwd ''", test_iter_bash_calls_default_cwd_empty),
        ("iter_bash_calls: unmatched/malformed -> []", test_iter_bash_calls_unmatched_and_malformed),
        ("_parse_records: filters non-objects", test_parse_records_filters_non_objects),
        ("load_records: reads object records", test_load_records_reads_object_records),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
