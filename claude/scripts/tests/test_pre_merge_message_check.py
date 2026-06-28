#!/usr/bin/env python3
"""Tests for pre-merge-message-check.py pure helpers.

Exercises the command-detection regex and queue-read helper offline (no disk I/O
for the regex tests, a tmp file for the read tests). The stdin plumbing and exit-2
emission are not covered (pure-helper convention).
"""
import importlib.util
import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-merge-message-check.py")
# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, os.path.dirname(_SCRIPT))
spec = importlib.util.spec_from_file_location("pre_merge_message_check", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

_RE = mod._GH_PR_MERGE_RE


def _check_regex(cmd: str) -> bool:
    return bool(_RE.search(cmd))


# ---------------------------------------------------------------------------
# Regex tests
# ---------------------------------------------------------------------------

def test_bare_merge():
    assert _check_regex("gh pr merge")

def test_merge_with_flags():
    assert _check_regex("gh pr merge --squash --delete-branch")

def test_merge_with_number():
    assert _check_regex("gh pr merge 42")

def test_merge_chained():
    assert _check_regex("git fetch origin && gh pr merge --squash")

def test_non_merge_gh_command():
    assert not _check_regex("gh pr create --title foo")

def test_unrelated_command():
    assert not _check_regex("git push origin main")

def test_merge_in_string_but_not_command():
    # "merge" appears in a comment but not as a gh subcommand
    assert not _check_regex("echo 'about to merge' && git push")


# ---------------------------------------------------------------------------
# Queue-read helper tests (use tmp file, monkey-patch _QUEUE_FILE)
# ---------------------------------------------------------------------------

def _with_queue(content, fn):
    """Write content to a temp file, patch _QUEUE_FILE, call fn(), restore."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    original = mod._QUEUE_FILE
    mod._QUEUE_FILE = path
    try:
        return fn()
    finally:
        mod._QUEUE_FILE = original
        os.unlink(path)


def test_queue_empty_string():
    assert _with_queue("", mod._read_queue) == ""

def test_queue_whitespace_only():
    result = _with_queue("   \n\t\n", mod._read_queue)
    assert result.strip() == ""

def test_queue_has_content():
    result = _with_queue("Please fix the flaky test before merging.", mod._read_queue)
    assert "flaky test" in result

def test_queue_multiline():
    msg = "Line one.\nLine two.\nLine three."
    result = _with_queue(msg, mod._read_queue)
    assert result == msg

def test_queue_missing_file():
    """Non-existent path → _read_queue returns ''."""
    original = mod._QUEUE_FILE
    mod._QUEUE_FILE = "C:/nonexistent/path/merge-queue.md"
    try:
        assert mod._read_queue() == ""
    finally:
        mod._QUEUE_FILE = original


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
