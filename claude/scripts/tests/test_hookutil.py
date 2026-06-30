#!/usr/bin/env python3
"""Unit tests for _hookutil.py — per-session sentinel + transcript-locate helpers.

_hookutil.py is the shared utility module for the Stop / UserPromptSubmit hook
family, extracted from near-verbatim copies in posttooluse-inert-advisory.py,
reconcile-open-prs.py, and token-tracker.py.  See ADR-064.

Exercises the pure helpers offline (tmp dirs, injected paths — no real
~/.claude/scratch or ~/.claude/projects).  The live sentinel write path in the
consuming hooks' main() functions is not covered (pure-helper convention).

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
    with tempfile.TemporaryDirectory() as root:
        projects = Path(root)
        nested = projects / "proj" / "subagents"
        nested.mkdir(parents=True)
        jsonl = projects / "proj" / "sid42.jsonl"
        jsonl.write_text("")
        result = _hookutil.find_transcript("sid42", projects=projects)
        assert result == jsonl, f"expected {jsonl}, got {result}"
    return "find_transcript finds jsonl nested in a project subdirectory"


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
