#!/usr/bin/env python3
"""Unit tests for _hookutil.py — sentinel, transcript-locate, and reader helpers.

_hookutil.py is the shared utility module for the Stop / UserPromptSubmit hook
family: per-session sentinels + transcript-locate (extracted from near-verbatim
copies in posttooluse-inert-advisory.py, reconcile-open-prs.py, and
token-tracker.py — ADR-064), the transcript-record readers `load_records` /
`_parse_records` / `iter_bash_calls` / `_result_text` / `_content_items`
(extracted from posttooluse-inert-advisory.py and stop-tile-enumeration-gate.py —
ADR-090), the bounded tail reader `iter_records_reverse` / `_record_from_line`
(dev-env#679, ADR-090 Amendment 1), and the hook heartbeat writer
`record_heartbeat` (ADR-106) — called by every wired hook's `main()`, read by
`hook-liveness-check.py` (see test_hook_liveness_check.py for the reader side).

Exercises the pure helpers offline (tmp dirs, injected paths, hand-built records —
no real ~/.claude/scratch or ~/.claude/projects).  The live sentinel write path in
the consuming hooks' main() functions is not covered (pure-helper convention).
`test_iter_records_reverse_is_lazy_stops_early` is the one exception to
"no mocking" in this file: it monkeypatches `builtins.open` to count chunk reads,
matching the precedent already set in test_prune_merged_worktrees.py, because
proving the whole point of this function (it does NOT read the whole file) is
not otherwise observable from pure inputs/outputs alone.
`test_iter_records_reverse_long_single_line_is_not_quadratic` is the one
timing-based test in this file -- a regression guard (with several times' margin)
for a real O(line_length^2 / chunk_size) buffer-re-concatenation bug an
adversarial /review pass caught before merge; see that test's own comment for
the full rationale and the empirical numbers behind its bound.

Usage:
    py -3 claude/scripts/tests/test_hookutil.py

Exit 0 = all pass.
"""
import json
import os
import sys
import time
import tempfile
import unittest.mock
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


def test_cleanup_default_ext_is_flag() -> str:
    # Backward-compat pin: every pre-existing caller passes only `prefix`, so
    # the default `ext` must stay ".flag" (dev-env#768).
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        flag = scratch / f"{PREFIX}old.flag"
        txt = scratch / f"{PREFIX}old.txt"
        flag.write_text("")
        txt.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(flag, (past, past))
        os.utime(txt, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch)
        assert not flag.exists(), "stale .flag sentinel should have been removed by default"
        assert txt.exists(), ".txt file must be untouched when ext is not overridden"
    return "cleanup_stale_sentinels defaults to sweeping only *.flag (backward compatible)"


def test_cleanup_custom_ext() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        txt = scratch / f"{PREFIX}old.txt"
        flag = scratch / f"{PREFIX}old.flag"
        txt.write_text("")
        flag.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(txt, (past, past))
        os.utime(flag, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch, ext=".txt")
        assert not txt.exists(), "stale .txt sentinel should have been removed with ext='.txt'"
        assert flag.exists(), ".flag file must be untouched when ext='.txt' is requested"
    return "cleanup_stale_sentinels(ext='.txt') sweeps only the requested suffix"


def test_cleanup_rejects_empty_ext() -> str:
    # dev-env#768 review: ext="" would otherwise broaden the glob to
    # "{prefix}*" -- matching every extension under the prefix. Must be a
    # no-op instead of a wider-than-intended sweep.
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        flag = scratch / f"{PREFIX}old.flag"
        flag.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(flag, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch, ext="")
        assert flag.exists(), "ext='' must be rejected as a no-op, not broaden the glob"
    return "cleanup_stale_sentinels(ext='') is a no-op rather than matching every extension"


def test_cleanup_rejects_dotless_ext() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        flag = scratch / f"{PREFIX}old.flag"
        flag.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(flag, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch, ext="flag")
        assert flag.exists(), "ext='flag' (no leading dot) must be rejected as a no-op"
    return "cleanup_stale_sentinels(ext='flag') (dot-less) is a no-op, not a broadened match"


def test_cleanup_custom_max_age_days() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        f = scratch / f"{PREFIX}recent.flag"
        f.write_text("")
        past = time.time() - 5 * 86400
        os.utime(f, (past, past))
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch, max_age_days=10)
        assert f.exists(), "5-day-old file survives a 10-day threshold"
        _hookutil.cleanup_stale_sentinels(PREFIX, scratch=scratch, max_age_days=1)
        assert not f.exists(), "5-day-old file is removed under a 1-day threshold"
    return "cleanup_stale_sentinels(max_age_days=N) overrides the default MAX_AGE_DAYS retention"


def test_cleanup_empty_prefix_sweeps_all_matching_ext() -> str:
    # dev-env#802: hook-liveness-check.py sweeps orphaned heartbeat tmps via
    # cleanup_stale_sentinels("", scratch=HEARTBEAT_DIR, ext=".tmp") -> glob("*.tmp"). The empty
    # prefix is deliberate -- the per-hook <hook>.ts.<pid>.tmp orphans share no common prefix, and
    # in HEARTBEAT_DIR the only .tmp files ARE those orphans. Pin that an empty prefix sweeps stale
    # .tmp files while sparing a fresh (in-flight) .tmp and a live <hook>.ts ledger, so a future
    # empty-prefix rejection (by analogy with the empty-ext rejection above) is caught by this local
    # test, not only the full-suite CI e2e.
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        stale = scratch / "foo.ts.111.tmp"
        fresh = scratch / "bar.ts.222.tmp"
        ledger = scratch / "foo.ts"
        for p in (stale, fresh, ledger):
            p.write_text("")
        past = time.time() - (_hookutil.MAX_AGE_DAYS + 1) * 86400
        os.utime(stale, (past, past))
        _hookutil.cleanup_stale_sentinels("", scratch=scratch, ext=".tmp")
        assert not stale.exists(), "a stale .tmp orphan must be swept with an empty prefix"
        assert fresh.exists(), "a fresh (in-flight) .tmp must be kept"
        assert ledger.exists(), "a .ts ledger must never match the *.tmp glob"
    return "cleanup_stale_sentinels('', ext='.tmp') sweeps stale .tmp orphans, sparing fresh .tmp and .ts ledgers"


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


def test_find_transcript_unsafe_session_id_returns_none_without_globbing() -> str:
    # dev-env#1002 review finding: session_id reached the glob pattern with no
    # sanitization of its own -- a crafted id could escape the *projects* root
    # via "**/../...". Plant a file at the escape target to prove the unsafe id
    # is rejected before any glob runs, not merely that this particular pattern
    # happens not to match.
    with tempfile.TemporaryDirectory() as root:
        projects = Path(root) / "projects"
        projects.mkdir()
        escape_target = Path(root) / "escaped.jsonl"
        escape_target.write_text("")
        result = _hookutil.find_transcript("../escaped", projects=projects)
        assert result is None, f"expected None, got {result}"
    return "find_transcript rejects a session_id outside _SAFE_SESSION_ID (dev-env#1002 review finding)"


def test_find_transcript_empty_session_id_returns_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        result = _hookutil.find_transcript("", projects=Path(root))
        assert result is None, f"expected None, got {result}"
    return "find_transcript('') -> None without touching the filesystem"


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


def test_user_message_texts_extracts() -> str:
    # dev-env#710: bare-string content, list-of-text-items, and the various empties.
    assert _hookutil._user_message_texts({"type": "user", "message": {"content": "skip tiles"}}) == ["skip tiles"]
    rec = {"type": "user", "message": {"content": [
        {"type": "text", "text": "one"},
        {"type": "tool_result", "content": "ignored"},  # non-text item ignored
        {"type": "text", "text": "two"},
    ]}}
    assert _hookutil._user_message_texts(rec) == ["one", "two"]
    # non-user / non-dict rec / non-dict message / non-str-non-list content -> []
    assert _hookutil._user_message_texts({"type": "assistant", "message": {"content": "x"}}) == []
    assert _hookutil._user_message_texts("not-a-dict") == []
    assert _hookutil._user_message_texts({"type": "user", "message": "not-a-dict"}) == []
    assert _hookutil._user_message_texts({"type": "user", "message": {"content": 42}}) == []
    # a text item with missing / None "text" coerces to ""
    rec2 = {"type": "user", "message": {"content": [{"type": "text"}, {"type": "text", "text": None}]}}
    assert _hookutil._user_message_texts(rec2) == ["", ""]
    return "_user_message_texts: bare-string / text-items extraction; [] for non-user/non-dict/malformed; missing text -> ''"


def test_is_synthetic_user_flags() -> str:
    # dev-env#710: isMeta / isCompactSummary -> synthetic; a plain user record ->
    # not; the non-dict guard returns False (does NOT raise), so a caller may front
    # this check ahead of _user_message_texts's own dict guard on a malformed list.
    assert _hookutil._is_synthetic_user({"type": "user", "isMeta": True}) is True
    assert _hookutil._is_synthetic_user({"type": "user", "isCompactSummary": True}) is True
    assert _hookutil._is_synthetic_user({"type": "user", "message": {"content": "hi"}}) is False
    assert _hookutil._is_synthetic_user({"isMeta": False, "isCompactSummary": False}) is False
    assert _hookutil._is_synthetic_user("not-a-dict") is False
    assert _hookutil._is_synthetic_user(None) is False
    return "_is_synthetic_user: True for isMeta/isCompactSummary, False for a plain user record and (without raising) a non-dict"


def test_first_user_prompt_text_finds_opening_prompt() -> str:
    # dev-env#1044: the composition of the two helpers above. The FIRST genuine
    # user prompt is what the unchained-merge trigger's scope test reads.
    records = [
        {"type": "user", "message": {"content": "Implement #1044"}},
        {"type": "user", "message": {"content": "a later prompt"}},
    ]
    assert _hookutil.first_user_prompt_text(records) == "Implement #1044"
    # Multiple text items in ONE record join with newlines -- they render as a
    # single prompt, so they must read back as one.
    joined = [{"type": "user", "message": {"content": [
        {"type": "text", "text": "one"}, {"type": "text", "text": "two"}]}}]
    assert _hookutil.first_user_prompt_text(joined) == "one\ntwo"
    return "first_user_prompt_text: returns the first genuine prompt; joins one record's text items"


def test_first_user_prompt_text_skips_synthetic_and_toolresult() -> str:
    # A compact summary restating an earlier request is not a fresh opening
    # prompt (the same reason skip_override filters it), and a user record
    # carrying only tool_result items contributes no text -- so a transcript
    # that opens with either must still resolve to the first REAL prompt, not
    # to "" (which the caller treats as an unreadable scope test).
    records = [
        {"type": "user", "isCompactSummary": True,
         "message": {"content": "earlier: implement #999"}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking"}]}},
        {"type": "user", "message": {"content": "fix the flaky test"}},
    ]
    assert _hookutil.first_user_prompt_text(records) == "fix the flaky test"
    # isMeta caveat blocks are skipped for the same reason.
    assert _hookutil.first_user_prompt_text(
        [{"type": "user", "isMeta": True, "message": {"content": "<local-command-stdout>"}}]) == ""
    return "first_user_prompt_text: skips synthetic records and tool_result-only user records"


def test_first_user_prompt_text_skips_command_wrapper() -> str:
    # Review of PR #1053: a <command-name>...</command-name> slash-command
    # wrapper is a genuine (non-isMeta) "type":"user" record with real string
    # content, so _is_synthetic_user alone doesn't filter it -- its keywords
    # are command machinery, not what a human or routine typed. Without this
    # filter, a /review-launched session's scope (e.g. trigger 5's chain-bearing
    # test) would be decided by whatever the wrapper text itself contains.
    records = [
        {"type": "user", "message": {"content": "<command-name>/review</command-name> "
                                                 "https://github.com/brownm09/dev-env/pull/1053"}},
        {"type": "user", "message": {"content": "fix the flaky worktree test"}},
    ]
    assert _hookutil.first_user_prompt_text(records) == "fix the flaky worktree test"
    # A record mixing one wrapper text item with one genuine item keeps only
    # the genuine one, rather than dropping the whole record.
    mixed = [{"type": "user", "message": {"content": [
        {"type": "text", "text": "<command-name>/review</command-name>"},
        {"type": "text", "text": "real prompt text"},
    ]}}]
    assert _hookutil.first_user_prompt_text(mixed) == "real prompt text"
    # A session whose ONLY user record is the wrapper resolves to "" (the caller
    # treats this as an unreadable/out-of-scope opening prompt).
    assert _hookutil.first_user_prompt_text(
        [{"type": "user", "message": {"content": "<command-name>/review</command-name>"}}]
    ) == ""
    return "first_user_prompt_text: skips a <command-name> wrapper, keeping any genuine text"


def test_first_user_prompt_text_blank_and_malformed() -> str:
    # A whitespace-only prompt is not a prompt -- keep walking. And a malformed
    # record list must yield "" rather than raise: this feeds a Stop hook whose
    # every input can be hand-built or truncated.
    assert _hookutil.first_user_prompt_text([]) == ""
    assert _hookutil.first_user_prompt_text([
        {"type": "user", "message": {"content": "   \n  "}},
        {"type": "user", "message": {"content": "real prompt"}},
    ]) == "real prompt"
    assert _hookutil.first_user_prompt_text(
        ["not-a-dict", {"type": "user", "message": "not-a-dict"}, {"type": "user"}]) == ""
    return "first_user_prompt_text: skips blank prompts; '' (no raise) on empty/malformed records"


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


def test_result_text_output_fallback_coerced() -> str:
    # No stdout/stderr -> fall back to toolUseResult.output, str()-coercing a non-str value.
    item = {"content": ""}
    rec = {"toolUseResult": {"output": 123}}
    assert _hookutil._result_text(item, rec) == "123"
    return "_result_text falls back to toolUseResult.output, coercing a non-str via str()"


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


# --- bounded tail reader (dev-env#679, ADR-090 Amendment 1) --------------------

def test_record_from_line_valid_dict() -> str:
    assert _hookutil._record_from_line(b'{"a": 1}') == {"a": 1}
    assert _hookutil._record_from_line(b'  {"a": 1}  ') == {"a": 1}, "surrounding whitespace stripped"
    return "_record_from_line parses a valid JSON-object line (with whitespace stripped)"


def test_record_from_line_skips_blank_malformed_non_object() -> str:
    for raw in (b"", b"   ", b"not json", b"42", b'"str"', b"[1,2]", b"null"):
        assert _hookutil._record_from_line(raw) is None, f"{raw!r} must yield None"
    return "_record_from_line -> None for blank/malformed/non-object lines"


def _write_jsonl(path: Path, records: list, trailing_newline: bool = True) -> None:
    text = "\n".join(json.dumps(r) for r in records)
    if trailing_newline:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def test_iter_records_reverse_basic_order() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        records = [{"n": 1}, {"n": 2}, {"n": 3}]
        _write_jsonl(p, records)
        got = list(_hookutil.iter_records_reverse(p))
        assert got == [{"n": 3}, {"n": 2}, {"n": 1}], f"expected reverse order, got {got}"
    return "iter_records_reverse yields records most-recent-first"


def test_iter_records_reverse_matches_load_records_across_chunk_sizes() -> str:
    # Property check: for many different chunk_size values -- including ones far
    # smaller than a single line, forcing lines to be reassembled across several
    # chunk reads -- the result must equal reversed(load_records(path)). This is
    # the correctness guarantee the whole chunked-backward-read algorithm rests on.
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        records = [{"type": "user" if i % 2 else "assistant", "n": i, "text": "x" * (i % 7)}
                   for i in range(60)]
        _write_jsonl(p, records)
        expected = list(reversed(_hookutil.load_records(p)))
        for chunk_size in (1, 2, 3, 5, 8, 16, 37, 64, 4096):
            got = list(_hookutil.iter_records_reverse(p, chunk_size=chunk_size))
            assert got == expected, f"chunk_size={chunk_size} mismatch: {got[:3]}... != {expected[:3]}..."
    return "iter_records_reverse matches reversed(load_records(...)) across 9 chunk sizes (1..4096)"


def test_iter_records_reverse_no_trailing_newline() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        _write_jsonl(p, [{"n": 1}, {"n": 2}], trailing_newline=False)
        got = list(_hookutil.iter_records_reverse(p, chunk_size=3))
        assert got == [{"n": 2}, {"n": 1}], f"got {got}"
    return "iter_records_reverse handles a file with no trailing newline"


def test_iter_records_reverse_blank_and_malformed_lines_skipped() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        p.write_text('{"a": 1}\n\nnot json\n42\n"str"\n[1,2]\nnull\n{"b": 2}\n', encoding="utf-8")
        got = list(_hookutil.iter_records_reverse(p, chunk_size=4))
        assert got == [{"b": 2}, {"a": 1}], f"got {got}"
    return "iter_records_reverse skips blank/malformed/non-object lines (mirrors _parse_records)"


def test_iter_records_reverse_utf8_multibyte_across_small_chunks() -> str:
    # chunk_size smaller than most multi-byte characters' encoded length all but
    # guarantees a chunk boundary lands inside one; the byte-level split on b"\n"
    # (never inside a UTF-8 continuation/lead byte) must still decode correctly.
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        records = [
            {"text": "café naïve"},           # 2-byte accented chars
            {"text": "日本語"},               # 3-byte CJK
            {"text": "\U0001f389 party \U0001f680"},      # 4-byte emoji
        ]
        _write_jsonl(p, records)
        expected = list(reversed(records))
        for chunk_size in (1, 2, 3, 4, 5, 7):
            got = list(_hookutil.iter_records_reverse(p, chunk_size=chunk_size))
            assert got == expected, f"chunk_size={chunk_size}: got {got}"
    return "multi-byte UTF-8 characters decode correctly even when chunk boundaries split them"


def test_iter_records_reverse_empty_file() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        assert list(_hookutil.iter_records_reverse(p)) == []
    return "iter_records_reverse yields nothing for an empty file"


def test_iter_records_reverse_missing_file_raises() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "nonexistent.jsonl"
        try:
            list(_hookutil.iter_records_reverse(p))
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError:
            pass
    return "iter_records_reverse raises FileNotFoundError for a missing path (matches load_records)"


def test_iter_records_reverse_nonpositive_chunk_size_raises() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "t.jsonl"
        _write_jsonl(p, [{"n": 1}])
        for bad in (0, -1, -100):
            try:
                list(_hookutil.iter_records_reverse(p, chunk_size=bad))
                raise AssertionError(f"expected ValueError for chunk_size={bad}")
            except ValueError:
                pass
    return "iter_records_reverse raises ValueError for a non-positive chunk_size (would never advance)"


def test_iter_records_reverse_is_lazy_stops_early() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "big.jsonl"
        records = [{"type": "user", "n": i} for i in range(4000)]
        records.append({"type": "assistant", "marker": "TAIL"})
        _write_jsonl(p, records)

        read_calls = []
        real_open = open

        def counting_open(path, mode="r", *args, **kwargs):
            f = real_open(path, mode, *args, **kwargs)
            if Path(path) != p or "b" not in mode:
                return f
            orig_read = f.read

            def counted_read(*a, **kw):
                read_calls.append(1)
                return orig_read(*a, **kw)

            f.read = counted_read
            return f

        with unittest.mock.patch("builtins.open", counting_open):
            gen = _hookutil.iter_records_reverse(p, chunk_size=128)
            first = next(gen)
            gen.close()

        assert first == {"type": "assistant", "marker": "TAIL"}, f"expected the tail record first, got {first}"
        assert len(read_calls) >= 1, (
            "the open()/.read() patch never intercepted a call -- this test would vacuously "
            "pass with 0 reads if iter_records_reverse stopped calling the builtin open() it "
            "patches (e.g. a future refactor to Path.read_bytes()); a real chunk read must occur"
        )
        assert len(read_calls) <= 2, (
            f"expected the match in the first chunk (or two), got {len(read_calls)} reads "
            f"-- a count this low proves the ~4000 leading lines were never touched"
        )
    return f"iter_records_reverse found the tail record in {len(read_calls)} chunk read(s), not a full parse"


def test_iter_records_reverse_long_single_line_is_not_quadratic() -> str:
    # Regression guard for a real bug caught by an adversarial /review pass: the
    # first implementation re-concatenated a growing buffer (`chunk + tail`) on
    # every chunk read, costing O(line_length^2 / chunk_size) for a single line
    # spanning many chunks. That shape matters specifically here -- the record
    # right before whatever this generator is scanning for is often the
    # transcript's newest entry (e.g. the user's just-submitted prompt on the
    # UserPromptSubmit path idle-refresher.py drives), and a large paste puts
    # its size directly under the user's control. A small chunk_size below
    # forces ~15600 chunk reads across one ~2MB line; the fixed (list-of-
    # fragments, join-once) implementation finishes in well under a second.
    # The reviewing subagent independently measured the pre-fix quadratic
    # version at 573ms-23s+ for comparably- and more-aggressively-shaped
    # inputs, so the 5s bound below has several times' margin against a
    # reintroduced O(n^2) while leaving ample room for a slow CI machine on
    # the passing (linear) side.
    with tempfile.TemporaryDirectory() as root:
        p = Path(root) / "long_line.jsonl"
        big_record = {"type": "user", "text": "x" * 2_000_000}
        p.write_text(json.dumps(big_record) + "\n", encoding="utf-8")

        start = time.time()
        got = list(_hookutil.iter_records_reverse(p, chunk_size=128))
        elapsed = time.time() - start

        assert got == [big_record], "the long line must still parse correctly, not just quickly"
        assert elapsed < 5.0, (
            f"took {elapsed:.2f}s for a ~2MB single line across ~15600 chunk reads -- "
            f"this smells like the O(n^2) re-concatenation bug this test guards against"
        )
    return f"a ~2MB single line across ~15600 small chunks parses correctly in {elapsed:.2f}s (not quadratic)"


# --- hook heartbeat (ADR-106) --------------------------------------------------

def test_record_heartbeat_writes_parseable_recent_timestamp() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb_dir = Path(root) / "hook-heartbeat"
        before = time.time()
        _hookutil.record_heartbeat("my-hook", heartbeat_dir=hb_dir)
        after = time.time()
        target = hb_dir / "my-hook.ts"
        assert target.exists(), "heartbeat file was not created"
        value = float(target.read_text(encoding="utf-8").strip())
        assert before - 1 <= value <= after + 1, f"timestamp {value} not within [{before}, {after}]"
    return "record_heartbeat writes a parseable, current Unix timestamp"


def test_record_heartbeat_creates_dir_if_absent() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb_dir = Path(root) / "does" / "not" / "exist" / "yet"
        assert not hb_dir.exists()
        _hookutil.record_heartbeat("foo", heartbeat_dir=hb_dir)
        assert (hb_dir / "foo.ts").exists()
    return "record_heartbeat creates the heartbeat directory (and parents) if absent"


def test_record_heartbeat_overwrites_on_second_call() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb_dir = Path(root)
        _hookutil.record_heartbeat("foo", heartbeat_dir=hb_dir)
        first = float((hb_dir / "foo.ts").read_text(encoding="utf-8").strip())
        time.sleep(0.01)  # force a detectably later timestamp regardless of clock resolution
        _hookutil.record_heartbeat("foo", heartbeat_dir=hb_dir)
        second = float((hb_dir / "foo.ts").read_text(encoding="utf-8").strip())
        assert second >= first, f"second call's timestamp {second} should be >= first {first}"
    return "a second record_heartbeat call overwrites with an updated timestamp"


def test_record_heartbeat_leaves_no_tmp_file() -> str:
    with tempfile.TemporaryDirectory() as root:
        hb_dir = Path(root)
        _hookutil.record_heartbeat("foo", heartbeat_dir=hb_dir)
        leftovers = [p for p in hb_dir.iterdir() if p.name != "foo.ts"]
        assert leftovers == [], f"unexpected leftover files: {leftovers}"
    return "record_heartbeat's tmp file is removed by the atomic os.replace (no .tmp leftovers)"


def test_record_heartbeat_swallows_errors_when_dir_uncreatable() -> str:
    with tempfile.TemporaryDirectory() as root:
        # A plain file occupying the path where record_heartbeat needs a directory --
        # mkdir(parents=True, exist_ok=True) raises FileExistsError/NotADirectoryError.
        blocker = Path(root) / "blocker"
        blocker.write_text("")
        hb_dir = blocker / "hook-heartbeat"
        _hookutil.record_heartbeat("foo", heartbeat_dir=hb_dir)  # must not raise
    return "record_heartbeat swallows errors when the heartbeat directory can't be created"


def test_record_heartbeat_default_dir_is_scratch_subdir() -> str:
    assert _hookutil.HEARTBEAT_DIR == _hookutil.SCRATCH / "hook-heartbeat", (
        f"HEARTBEAT_DIR should be SCRATCH/hook-heartbeat, got {_hookutil.HEARTBEAT_DIR}"
    )
    return "HEARTBEAT_DIR (the default heartbeat_dir) is SCRATCH / 'hook-heartbeat'"


def test_record_heartbeat_env_override_takes_precedence_over_default() -> str:
    """dev-env#1031/#1033: HOOK_HEARTBEAT_DIR_OVERRIDE must redirect a
    record_heartbeat() call that passes NO explicit heartbeat_dir (the shape
    every real wired hook uses: `_hookutil.record_heartbeat("<name>")`) --
    this is the whole point of the override, so it must be pinned against
    that exact call shape, not just the explicit-parameter one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        override_dir = Path(tmp) / "override-heartbeat"
        real_env = os.environ.get("HOOK_HEARTBEAT_DIR_OVERRIDE")
        os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = str(override_dir)
        try:
            _hookutil.record_heartbeat("env-override-test")
        finally:
            if real_env is None:
                del os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"]
            else:
                os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = real_env
        assert (override_dir / "env-override-test.ts").exists(), (
            "record_heartbeat() with no explicit heartbeat_dir must honor "
            "HOOK_HEARTBEAT_DIR_OVERRIDE, not fall through to the real HEARTBEAT_DIR"
        )
        assert not (_hookutil.HEARTBEAT_DIR / "env-override-test.ts").exists(), (
            "the real ~/.claude/scratch/hook-heartbeat/ must NOT be touched when the override is set"
        )
    return "HOOK_HEARTBEAT_DIR_OVERRIDE redirects a no-explicit-heartbeat_dir call away from the real HEARTBEAT_DIR"


def test_record_heartbeat_env_override_takes_precedence_over_explicit_param() -> str:
    """The env override wins even when a caller ALSO passes an explicit
    heartbeat_dir -- test code that already sets the env var for a whole
    test run must not be silently bypassed by a caller that also passes a
    (now-stale) explicit directory.
    """
    with tempfile.TemporaryDirectory() as tmp:
        override_dir = Path(tmp) / "override-wins"
        explicit_dir = Path(tmp) / "explicit-loses"
        real_env = os.environ.get("HOOK_HEARTBEAT_DIR_OVERRIDE")
        os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = str(override_dir)
        try:
            _hookutil.record_heartbeat("precedence-test", heartbeat_dir=explicit_dir)
        finally:
            if real_env is None:
                del os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"]
            else:
                os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = real_env
        assert (override_dir / "precedence-test.ts").exists(), "env override should win"
        assert not explicit_dir.exists(), "explicit heartbeat_dir must not be used when the env override is set"
    return "HOOK_HEARTBEAT_DIR_OVERRIDE takes precedence even over an explicit heartbeat_dir argument"


def test_record_heartbeat_empty_env_override_falls_through_to_default() -> str:
    """An explicitly-empty-string env var (as opposed to unset) must not be
    treated as a directory path -- falls through to heartbeat_dir/HEARTBEAT_DIR
    exactly as if the variable were unset.
    """
    with tempfile.TemporaryDirectory() as tmp:
        hb_dir = Path(tmp) / "hb"
        real_env = os.environ.get("HOOK_HEARTBEAT_DIR_OVERRIDE")
        os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = ""
        try:
            _hookutil.record_heartbeat("empty-override-test", heartbeat_dir=hb_dir)
        finally:
            if real_env is None:
                del os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"]
            else:
                os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = real_env
        assert (hb_dir / "empty-override-test.ts").exists(), "empty override string must fall through to heartbeat_dir"
    return "an empty-string HOOK_HEARTBEAT_DIR_OVERRIDE falls through to heartbeat_dir, not treated as a real path"


def main() -> int:
    tests = [
        ("sentinel_path: correct path with override", test_sentinel_path_returns_correct_path),
        ("sentinel_path: default SCRATCH parent", test_sentinel_path_default_scratch),
        ("cleanup: removes stale, keeps fresh", test_cleanup_removes_stale_keeps_fresh),
        ("cleanup: ignores different prefix", test_cleanup_ignores_different_prefix),
        ("cleanup: no crash on missing dir", test_cleanup_no_crash_on_missing_dir),
        ("cleanup: default ext is .flag (backward compatible)", test_cleanup_default_ext_is_flag),
        ("cleanup: custom ext sweeps only that suffix", test_cleanup_custom_ext),
        ("cleanup: rejects empty ext (no-op, not broadened glob)", test_cleanup_rejects_empty_ext),
        ("cleanup: rejects dot-less ext (no-op)", test_cleanup_rejects_dotless_ext),
        ("cleanup: custom max_age_days overrides default", test_cleanup_custom_max_age_days),
        ("cleanup: empty prefix sweeps all matching ext (heartbeat .tmp)", test_cleanup_empty_prefix_sweeps_all_matching_ext),
        ("find_transcript: found", test_find_transcript_found),
        ("find_transcript: not found -> None", test_find_transcript_not_found),
        ("find_transcript: nested dir", test_find_transcript_nested),
        ("find_transcript: unsafe session_id rejected (dev-env#1002)", test_find_transcript_unsafe_session_id_returns_none_without_globbing),
        ("find_transcript: empty session_id -> None (dev-env#1002)", test_find_transcript_empty_session_id_returns_none),
        ("_content_items: returns list", test_content_items_returns_list),
        ("_content_items: guards -> []", test_content_items_guards),
        ("_user_message_texts: extraction + guards (dev-env#710)", test_user_message_texts_extracts),
        ("_is_synthetic_user: flags + non-dict guard (dev-env#710)", test_is_synthetic_user_flags),
        ("first_user_prompt_text: opening prompt (dev-env#1044)",
         test_first_user_prompt_text_finds_opening_prompt),
        ("first_user_prompt_text: skips synthetic/tool_result (dev-env#1044)",
         test_first_user_prompt_text_skips_synthetic_and_toolresult),
        ("first_user_prompt_text: skips <command-name> wrapper (review of PR #1053)",
         test_first_user_prompt_text_skips_command_wrapper),
        ("first_user_prompt_text: blank + malformed (dev-env#1044)",
         test_first_user_prompt_text_blank_and_malformed),
        ("_result_text: string content", test_result_text_string_content),
        ("_result_text: list content", test_result_text_list_content),
        ("_result_text: toolUseResult fallback", test_result_text_tooluseresult_fallback),
        ("_result_text: empty -> ''", test_result_text_empty),
        ("_result_text: output fallback coerced", test_result_text_output_fallback_coerced),
        ("iter_bash_calls: pairs by id (+cwd)", test_iter_bash_calls_pairs_by_id_with_cwd),
        ("iter_bash_calls: parallel not mismatched", test_iter_bash_calls_parallel_not_mismatched),
        ("iter_bash_calls: default cwd ''", test_iter_bash_calls_default_cwd_empty),
        ("iter_bash_calls: unmatched/malformed -> []", test_iter_bash_calls_unmatched_and_malformed),
        ("_parse_records: filters non-objects", test_parse_records_filters_non_objects),
        ("load_records: reads object records", test_load_records_reads_object_records),
        ("_record_from_line: valid dict", test_record_from_line_valid_dict),
        ("_record_from_line: skips blank/malformed/non-object", test_record_from_line_skips_blank_malformed_non_object),
        ("iter_records_reverse: basic order", test_iter_records_reverse_basic_order),
        ("iter_records_reverse: matches load_records across chunk sizes", test_iter_records_reverse_matches_load_records_across_chunk_sizes),
        ("iter_records_reverse: no trailing newline", test_iter_records_reverse_no_trailing_newline),
        ("iter_records_reverse: blank/malformed lines skipped", test_iter_records_reverse_blank_and_malformed_lines_skipped),
        ("iter_records_reverse: UTF-8 multibyte across small chunks", test_iter_records_reverse_utf8_multibyte_across_small_chunks),
        ("iter_records_reverse: empty file -> []", test_iter_records_reverse_empty_file),
        ("iter_records_reverse: missing file -> FileNotFoundError", test_iter_records_reverse_missing_file_raises),
        ("iter_records_reverse: non-positive chunk_size -> ValueError", test_iter_records_reverse_nonpositive_chunk_size_raises),
        ("iter_records_reverse: lazy, stops early", test_iter_records_reverse_is_lazy_stops_early),
        ("iter_records_reverse: long single line is not quadratic", test_iter_records_reverse_long_single_line_is_not_quadratic),
        ("record_heartbeat: writes parseable recent timestamp", test_record_heartbeat_writes_parseable_recent_timestamp),
        ("record_heartbeat: creates dir if absent", test_record_heartbeat_creates_dir_if_absent),
        ("record_heartbeat: overwrites on second call", test_record_heartbeat_overwrites_on_second_call),
        ("record_heartbeat: no leftover tmp file", test_record_heartbeat_leaves_no_tmp_file),
        ("record_heartbeat: swallows errors when dir uncreatable", test_record_heartbeat_swallows_errors_when_dir_uncreatable),
        ("record_heartbeat: default dir is SCRATCH/hook-heartbeat", test_record_heartbeat_default_dir_is_scratch_subdir),
        ("record_heartbeat: HOOK_HEARTBEAT_DIR_OVERRIDE beats the default (dev-env#1031/#1033)", test_record_heartbeat_env_override_takes_precedence_over_default),
        ("record_heartbeat: HOOK_HEARTBEAT_DIR_OVERRIDE beats an explicit heartbeat_dir too", test_record_heartbeat_env_override_takes_precedence_over_explicit_param),
        ("record_heartbeat: empty-string override falls through to heartbeat_dir", test_record_heartbeat_empty_env_override_falls_through_to_default),
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
