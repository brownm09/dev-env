#!/usr/bin/env python3
"""Unit tests for _gh_project.py — the shared `gh project item-add` wrapper and its
best-effort item-ID cache (dev-env#1057, ADR-141).

Exercises the pure/best-effort helpers offline (tmp dirs, injected `cache_path`
overrides — no real ~/.claude/scratch). `add_to_project`'s own `subprocess.run` call
is a live `gh` network boundary and is NOT unit-tested, matching this repo's
no-subprocess-mock convention (see the module's own docstring) — but its cache-write
side effect (`_cache_new_item`) is fully testable in isolation, since it sits
entirely on the Python side of that boundary and never touches `gh` itself. That
gives full coverage of the actual new logic without mocking subprocess.

Usage:
    py -3 claude/scripts/tests/test_gh_project.py

Exit 0 = all pass.
"""
import os
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _gh_project


# --- _parse_issue_url -----------------------------------------------------------

def test_parse_issue_url_matches_issues_url() -> str:
    result = _gh_project._parse_issue_url("https://github.com/brownm09/dev-env/issues/1057")
    assert result == ("brownm09/dev-env", 1057), f"unexpected {result!r}"
    return "_parse_issue_url matches an /issues/<N> URL into ('owner/repo', N)"


def test_parse_issue_url_matches_pull_url() -> str:
    result = _gh_project._parse_issue_url("https://github.com/brownm09/dev-env/pull/1053")
    assert result == ("brownm09/dev-env", 1053), f"unexpected {result!r}"
    return "_parse_issue_url matches a /pull/<N> URL into ('owner/repo', N)"


def test_parse_issue_url_tolerates_trailing_slash() -> str:
    result = _gh_project._parse_issue_url("https://github.com/brownm09/dev-env/issues/1057/")
    assert result == ("brownm09/dev-env", 1057), f"unexpected {result!r}"
    return "_parse_issue_url tolerates an optional trailing slash"


def test_parse_issue_url_none_on_non_github_url() -> str:
    result = _gh_project._parse_issue_url("https://example.com/brownm09/dev-env/issues/1057")
    assert result is None, f"expected None, got {result!r}"
    return "_parse_issue_url returns None for a non-github.com URL"


def test_parse_issue_url_none_on_malformed_path() -> str:
    result = _gh_project._parse_issue_url("https://github.com/brownm09/dev-env")
    assert result is None, f"expected None, got {result!r}"
    return "_parse_issue_url returns None when the issues/pull path segment is missing"


def test_parse_issue_url_none_on_non_numeric_trailer() -> str:
    result = _gh_project._parse_issue_url("https://github.com/brownm09/dev-env/issues/abc")
    assert result is None, f"expected None, got {result!r}"
    return "_parse_issue_url returns None when the trailing segment isn't numeric"


# --- read_item_cache --------------------------------------------------------------

def test_read_item_cache_missing_file_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "does-not-exist.json"
        assert _gh_project.read_item_cache(cache_path) == {}
    return "read_item_cache returns {} when the cache file doesn't exist"


def test_read_item_cache_corrupt_json_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "corrupt.json"
        cache_path.write_text("{not valid json", encoding="utf-8")
        assert _gh_project.read_item_cache(cache_path) == {}
    return "read_item_cache returns {} (never raises) on corrupt JSON"


def test_read_item_cache_non_object_json_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "list.json"
        cache_path.write_text("[1, 2, 3]", encoding="utf-8")
        assert _gh_project.read_item_cache(cache_path) == {}
    return "read_item_cache returns {} when the JSON value isn't an object"


def test_read_item_cache_valid_file_returns_parsed_dict() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "cache.json"
        cache_path.write_text('{"brownm09/dev-env#1057": "PVTI_abc"}', encoding="utf-8")
        assert _gh_project.read_item_cache(cache_path) == {"brownm09/dev-env#1057": "PVTI_abc"}
    return "read_item_cache returns the parsed dict for a well-formed cache file"


# --- write_item_cache_entry / lookup_cached_item_id round-trip -------------------

def test_write_then_lookup_round_trips() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "cache.json"
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", cache_path)
        assert _gh_project.lookup_cached_item_id("brownm09/dev-env", 1057, cache_path) == "PVTI_abc"
    return "write_item_cache_entry + lookup_cached_item_id round-trip a single entry"


def test_lookup_miss_returns_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "cache.json"
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", cache_path)
        assert _gh_project.lookup_cached_item_id("brownm09/dev-env", 9999, cache_path) is None
    return "lookup_cached_item_id returns None for a number not in the cache"


def test_second_write_preserves_first_entry() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "cache.json"
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_first", cache_path)
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1060, "PVTI_second", cache_path)
        assert _gh_project.lookup_cached_item_id("brownm09/dev-env", 1057, cache_path) == "PVTI_first"
        assert _gh_project.lookup_cached_item_id("brownm09/dev-env", 1060, cache_path) == "PVTI_second"
    return "write_item_cache_entry is a read-modify-write -- a second key doesn't clobber the first"


def test_write_same_key_overwrites_value() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "cache.json"
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_old", cache_path)
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_new", cache_path)
        assert _gh_project.lookup_cached_item_id("brownm09/dev-env", 1057, cache_path) == "PVTI_new"
    return "writing the same repo#number key again overwrites the stored item ID"


def test_write_item_cache_entry_creates_dir_if_absent() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "does" / "not" / "exist" / "yet" / "cache.json"
        assert not cache_path.parent.exists()
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", cache_path)
        assert cache_path.exists()
    return "write_item_cache_entry creates the cache directory (and parents) if absent"


def test_write_item_cache_entry_leaves_no_tmp_file() -> str:
    with tempfile.TemporaryDirectory() as root:
        cache_path = Path(root) / "cache.json"
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", cache_path)
        leftovers = [p for p in Path(root).iterdir() if p.name != "cache.json"]
        assert leftovers == [], f"unexpected leftover files: {leftovers}"
    return "write_item_cache_entry's tmp file is removed by the atomic os.replace (no .tmp leftovers)"


def test_write_item_cache_entry_swallows_errors_when_dir_uncreatable() -> str:
    with tempfile.TemporaryDirectory() as root:
        # A plain file occupying the path where write_item_cache_entry needs a
        # directory -- mkdir(parents=True, exist_ok=True) raises
        # FileExistsError/NotADirectoryError. Same trick as
        # test_hookutil.test_record_heartbeat_swallows_errors_when_dir_uncreatable.
        blocker = Path(root) / "blocker"
        blocker.write_text("")
        cache_path = blocker / "subdir" / "cache.json"
        _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", cache_path)  # must not raise
    return "write_item_cache_entry swallows errors when the cache directory can't be created"


def test_read_item_cache_returns_empty_by_default_with_no_real_scratch_write() -> str:
    # Sanity check that the *default* CACHE_PATH constant is never touched by the
    # explicit-cache_path-parameter tests above -- every other test in this file
    # passes an explicit tmp-dir cache_path and must never fall through to it.
    assert _gh_project.CACHE_PATH == Path("C:/Users/brown/.claude/scratch/project-item-cache.json")
    return "CACHE_PATH is the documented default path (explicit cache_path always overrides it in tests above)"


# --- PROJECT_ITEM_CACHE_PATH_OVERRIDE precedence ----------------------------------

def test_env_override_takes_precedence_over_explicit_param() -> str:
    """The env override wins even when a caller ALSO passes an explicit
    cache_path -- mirrors test_hookutil's
    test_record_heartbeat_env_override_takes_precedence_over_explicit_param."""
    with tempfile.TemporaryDirectory() as tmp:
        override_path = Path(tmp) / "override.json"
        explicit_path = Path(tmp) / "explicit.json"
        real_env = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
        os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = str(override_path)
        try:
            _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", explicit_path)
        finally:
            if real_env is None:
                del os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"]
            else:
                os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = real_env
        assert override_path.exists(), "env override should win"
        assert not explicit_path.exists(), "explicit cache_path must not be used when the env override is set"
    return "PROJECT_ITEM_CACHE_PATH_OVERRIDE takes precedence even over an explicit cache_path argument"


def test_empty_env_override_falls_through_to_explicit_param() -> str:
    """An explicitly-empty-string env var (as opposed to unset) must not be treated
    as a path -- falls through to the explicit cache_path exactly as if unset."""
    with tempfile.TemporaryDirectory() as tmp:
        explicit_path = Path(tmp) / "explicit.json"
        real_env = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
        os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = ""
        try:
            _gh_project.write_item_cache_entry("brownm09/dev-env", 1057, "PVTI_abc", explicit_path)
        finally:
            if real_env is None:
                del os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"]
            else:
                os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = real_env
        assert explicit_path.exists(), "empty override string must fall through to the explicit cache_path"
    return "an empty-string PROJECT_ITEM_CACHE_PATH_OVERRIDE falls through to cache_path, not treated as a real path"


# --- _cache_new_item (the add_to_project success-path integration point) ---------

def test_cache_new_item_writes_entry_for_valid_url() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        real_env = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
        os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = str(cache_path)
        try:
            _gh_project._cache_new_item("https://github.com/brownm09/dev-env/issues/1057", "PVTI_xyz")
        finally:
            if real_env is None:
                del os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"]
            else:
                os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = real_env
        assert _gh_project.read_item_cache(cache_path) == {"brownm09/dev-env#1057": "PVTI_xyz"}
    return "_cache_new_item parses a valid issue URL and writes the (repo#number -> id) entry"


def test_cache_new_item_no_op_on_unparseable_url() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "cache.json"
        real_env = os.environ.get("PROJECT_ITEM_CACHE_PATH_OVERRIDE")
        os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = str(cache_path)
        try:
            _gh_project._cache_new_item("not-a-url", "PVTI_xyz")  # must not raise
        finally:
            if real_env is None:
                del os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"]
            else:
                os.environ["PROJECT_ITEM_CACHE_PATH_OVERRIDE"] = real_env
        assert not cache_path.exists(), "no cache file should be created for an unparseable URL"
    return "_cache_new_item is a silent no-op (never raises, writes nothing) when the URL doesn't parse"


def main() -> int:
    tests = [
        ("_parse_issue_url: matches /issues/<N>", test_parse_issue_url_matches_issues_url),
        ("_parse_issue_url: matches /pull/<N>", test_parse_issue_url_matches_pull_url),
        ("_parse_issue_url: tolerates trailing slash", test_parse_issue_url_tolerates_trailing_slash),
        ("_parse_issue_url: None on non-github URL", test_parse_issue_url_none_on_non_github_url),
        ("_parse_issue_url: None on malformed path", test_parse_issue_url_none_on_malformed_path),
        ("_parse_issue_url: None on non-numeric trailer", test_parse_issue_url_none_on_non_numeric_trailer),
        ("read_item_cache: missing file -> {}", test_read_item_cache_missing_file_returns_empty),
        ("read_item_cache: corrupt JSON -> {}", test_read_item_cache_corrupt_json_returns_empty),
        ("read_item_cache: non-object JSON -> {}", test_read_item_cache_non_object_json_returns_empty),
        ("read_item_cache: valid file -> parsed dict", test_read_item_cache_valid_file_returns_parsed_dict),
        ("write+lookup: round-trips a single entry", test_write_then_lookup_round_trips),
        ("lookup: miss returns None", test_lookup_miss_returns_none),
        ("write: second key preserves the first", test_second_write_preserves_first_entry),
        ("write: same key overwrites value", test_write_same_key_overwrites_value),
        ("write: creates dir if absent", test_write_item_cache_entry_creates_dir_if_absent),
        ("write: no leftover tmp file", test_write_item_cache_entry_leaves_no_tmp_file),
        ("write: swallows errors when dir uncreatable", test_write_item_cache_entry_swallows_errors_when_dir_uncreatable),
        ("CACHE_PATH: documented default", test_read_item_cache_returns_empty_by_default_with_no_real_scratch_write),
        ("env override: beats explicit cache_path param", test_env_override_takes_precedence_over_explicit_param),
        ("env override: empty string falls through", test_empty_env_override_falls_through_to_explicit_param),
        ("_cache_new_item: writes entry for a valid URL", test_cache_new_item_writes_entry_for_valid_url),
        ("_cache_new_item: no-op on an unparseable URL", test_cache_new_item_no_op_on_unparseable_url),
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
            print(f"      {e}")
    total = len(tests)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
