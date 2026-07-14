#!/usr/bin/env python3
"""Tests for pre-merge-message-check.py.

Exercises the command-detection predicate and queue-read helper offline (no
disk I/O for the detection tests, a tmp file for the read tests), plus an
end-to-end subprocess layer proving the real exit-2 blocking behavior (added
dev-env#762 review: this hook DOES block -- `Exit 2 -- block the merge` per
its own docstring -- so it must not be left without PowerShell tool_name
coverage the way the genuinely-advisory sibling hooks are; a prior version of
this PR's ADR-071 Amendment 4 mischaracterized it as "pure advisory").
"""
import importlib.util
import json
import os
import subprocess
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
# End-to-end main() via subprocess (dev-env#762 review)
#
# This hook genuinely blocks (exit 2) -- unlike the sibling advisory hooks
# (pre-merge-branch-check.py, pre-bash-drift-check.py) that have an
# established, deliberate "no main() coverage" convention, this one's
# blocking behavior must be proven end-to-end for both tool_name values.
# MERGE_QUEUE_FILE_PATH redirects the hook's queue file at a disposable temp
# file so this never touches the developer's real merge-queue.md.
# ---------------------------------------------------------------------------

def _run_hook(payload, queue_path=None):
    env = dict(os.environ)
    if queue_path is not None:
        env["MERGE_QUEUE_FILE_PATH"] = queue_path
    return subprocess.run(
        [sys.executable, _SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_e2e_blocks_via_bash_tool_name_when_queue_has_content():
    with tempfile.TemporaryDirectory() as tmp:
        queue = os.path.join(tmp, "merge-queue.md")
        with open(queue, "w", encoding="utf-8") as f:
            f.write("please check the flaky test first")
        payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge --squash"}, "cwd": "."}
        proc = _run_hook(payload, queue_path=queue)
        assert proc.returncode == 2, f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}"
        assert "flaky test" in proc.stderr


def test_e2e_blocks_via_powershell_tool_name_when_queue_has_content():
    # dev-env#620/dev-env#762: PowerShell must block identically to Bash --
    # this is the one genuinely-blocking gate that was missing this proof.
    with tempfile.TemporaryDirectory() as tmp:
        queue = os.path.join(tmp, "merge-queue.md")
        with open(queue, "w", encoding="utf-8") as f:
            f.write("please check the flaky test first")
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "gh pr merge --squash"}, "cwd": "."}
        proc = _run_hook(payload, queue_path=queue)
        assert proc.returncode == 2, f"expected exit 2 for tool_name=PowerShell, got {proc.returncode}. stderr={proc.stderr!r}"
        assert "flaky test" in proc.stderr


def test_e2e_allows_via_powershell_tool_name_when_queue_empty():
    with tempfile.TemporaryDirectory() as tmp:
        queue = os.path.join(tmp, "merge-queue.md")
        with open(queue, "w", encoding="utf-8") as f:
            f.write("")
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "gh pr merge --squash"}, "cwd": "."}
        proc = _run_hook(payload, queue_path=queue)
        assert proc.returncode == 0, f"expected exit 0 (empty queue), got {proc.returncode}. stderr={proc.stderr!r}"


def test_e2e_noop_on_unrelated_tool_name():
    with tempfile.TemporaryDirectory() as tmp:
        queue = os.path.join(tmp, "merge-queue.md")
        with open(queue, "w", encoding="utf-8") as f:
            f.write("please check the flaky test first")
        payload = {"tool_name": "Write", "tool_input": {"file_path": "x.md"}, "cwd": "."}
        proc = _run_hook(payload, queue_path=queue)
        assert proc.returncode == 0, f"expected exit 0 (unrelated tool), got {proc.returncode}"


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
