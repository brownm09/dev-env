#!/usr/bin/env python3
"""Tests for pre-merge-message-check.py pure helpers.

Exercises the command-detection predicate and queue-read helper offline (no
disk I/O for the detection tests, a tmp file for the read tests). The stdin
plumbing and exit-2 emission are not covered (pure-helper convention).
"""
import importlib.util
import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-merge-message-check.py")
# The script imports _winsubp and _hookio (siblings in scripts/); make them resolvable.
sys.path.insert(0, os.path.dirname(_SCRIPT))
spec = importlib.util.spec_from_file_location("pre_merge_message_check", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

is_pr_merge_command = mod.is_pr_merge_command


# ---------------------------------------------------------------------------
# is_pr_merge_command tests
# ---------------------------------------------------------------------------

def test_bare_merge():
    assert is_pr_merge_command("gh pr merge")

def test_merge_with_flags():
    assert is_pr_merge_command("gh pr merge --squash --delete-branch")

def test_merge_with_number():
    assert is_pr_merge_command("gh pr merge 42")

def test_merge_chained():
    assert is_pr_merge_command("git fetch origin && gh pr merge --squash")

def test_merge_after_semicolon():
    assert is_pr_merge_command("git status; gh pr merge")

def test_merge_cd_chained():
    assert is_pr_merge_command("cd C:/Users/brown/Git/dev-env && gh pr merge --squash")

def test_non_merge_gh_command():
    assert not is_pr_merge_command("gh pr create --title foo")

def test_unrelated_command():
    assert not is_pr_merge_command("git push origin main")

def test_merge_in_string_but_not_command():
    # "merge" appears inside a string argument, not as a gh subcommand
    assert not is_pr_merge_command("echo 'about to merge' && git push")

def test_merge_heredoc_body_not_matched():
    # dev-env#499: a "gh pr merge" mentioned only inside a heredoc body
    # (e.g. as prose in a commit message) must not count as a genuine
    # top-level invocation.
    command = 'git commit -m "$(cat <<\'EOF\'\nmentions gh pr merge in prose\nEOF\n)"'
    assert is_pr_merge_command(command) is False


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
