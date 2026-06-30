#!/usr/bin/env python3
"""Unit tests for _hookio.read_command_output — the shared PostToolUse output read.

Claude Code's Bash hook payload exposes a command's output under
`tool_response.stdout` / `tool_response.stderr`, NOT `output`. `post-tool-use.py`
read the legacy `output` field and therefore silently never fired (dev-env #377 /
ADR-049); the same wrong read existed in four sibling hooks (#380). The fix is the
shared `read_command_output` helper in `claude/scripts/_hookio.py`, imported by all
five hooks. These tests pin its field precedence offline (no network, no gh).

Usage:
    py -3 claude/scripts/tests/test_hookio.py

Exit 0 = all pass.
"""

import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _hookio import (  # noqa: E402
    effective_merge_dir,
    merge_pr_number_from_output,
    output_has_merge_marker,
    read_command_output,
)

URL = "https://github.com/brownm09/dev-env/issues/377"


def test_reads_stdout() -> str:
    # The real Bash payload shape: output lives under `stdout`, not `output`.
    payload = {"tool_response": {"stdout": URL, "stderr": "", "interrupted": False}}
    assert read_command_output(payload) == URL, "stdout should be read"
    return "stdout-shaped payload -> stdout content (the #377 regression)"


def test_combines_stdout_and_stderr() -> str:
    # `gh pr merge` prints its success line to stderr; both must be captured.
    payload = {"tool_response": {"stdout": "done", "stderr": "warn"}}
    assert read_command_output(payload) == "done\nwarn", "stdout+stderr joined"
    return "stdout + stderr are both captured, newline-joined"


def test_stderr_only() -> str:
    # gh writes the merge success marker to stderr with no stdout.
    payload = {"tool_response": {"stdout": "", "stderr": "Squashed and merged pull request #380"}}
    assert read_command_output(payload) == "Squashed and merged pull request #380"
    return "stderr-only payload -> stderr content (the gh pr merge shape)"


def test_legacy_output_fallback() -> str:
    # If a build ever sends the legacy `output` field, still read it.
    payload = {"tool_response": {"output": URL}}
    assert read_command_output(payload) == URL, "legacy output fallback"
    return "legacy `output` field still works (forward/backward compatible)"


def test_stdout_preferred_over_legacy_output() -> str:
    payload = {"tool_response": {"stdout": "real", "output": "legacy"}}
    assert read_command_output(payload) == "real", "stdout wins over legacy output"
    return "stdout/stderr take precedence over the legacy output field"


def test_empty_and_malformed_payloads() -> str:
    assert read_command_output({}) == "", "missing tool_response -> ''"
    assert read_command_output({"tool_response": {}}) == "", "empty tool_response -> ''"
    assert read_command_output({"tool_response": None}) == "", "None tool_response -> ''"
    assert read_command_output({"tool_response": "x"}) == "", "non-dict tool_response -> ''"
    return "missing/empty/None/non-dict tool_response all yield '' (no crash)"


def test_old_output_read_would_have_been_empty() -> str:
    # Pin the root cause: the pre-fix read (`.get("output")`) on the real shape
    # is empty, which is exactly what silently broke the four sibling hooks.
    real_shape = {"stdout": URL, "stderr": "", "interrupted": False, "isImage": False}
    assert real_shape.get("output", "") == "", "pre-fix read must be empty on real shape"
    assert read_command_output({"tool_response": real_shape}) == URL, "fixed read recovers content"
    return "pre-fix `output` read was '' on the real payload; fixed read recovers it"


def test_merge_marker_detected() -> str:
    assert output_has_merge_marker("✓ Squashed and merged pull request #380 (T)")
    assert output_has_merge_marker("✓ Merged pull request #1")
    assert output_has_merge_marker("✓ Rebased and merged pull request brownm09/dev-env#7")
    return "real merge markers (incl. cross-repo owner/repo#N) -> True"


def test_merge_marker_excludes_auto_failure_and_stray() -> str:
    assert not output_has_merge_marker("✓ Pull request #380 will be automatically merged")
    assert not output_has_merge_marker("X Pull request #380 is not mergeable")
    # A stray verb phrase WITHOUT "pull request #N" must not count — this is the
    # chained-output false positive the line-anchored regex closes (#380 review).
    assert not output_has_merge_marker("note: it was 'Squashed and merged' last time")
    assert not output_has_merge_marker("")
    return "queued --auto / failure / stray phrase / empty -> False"


def test_merge_pr_number_from_output() -> str:
    assert merge_pr_number_from_output("✓ Squashed and merged pull request #380 (T)") == 380
    assert merge_pr_number_from_output("✓ Rebased and merged pull request o/r#7") == 7
    assert merge_pr_number_from_output("no marker here") is None
    return "marker PR number extracted; None when absent"


# ---------------------------------------------------------------------------
# effective_merge_dir  (dev-env#446 / ADR-067)
# ---------------------------------------------------------------------------

def test_merge_dir_bare_merge_is_cwd() -> str:
    assert effective_merge_dir("gh pr merge --squash --delete-branch", "/session/cwd") == "/session/cwd"
    return "bare gh pr merge -> session cwd"


def test_merge_dir_cd_chain_redirects() -> str:
    out = effective_merge_dir("cd /Git/dev-env && gh pr merge --squash", "/Git/lifting-logbook")
    assert out == "/Git/dev-env", f"expected /Git/dev-env, got {out!r}"
    return "cd <repo> && gh pr merge -> that repo, not session cwd"


def test_merge_dir_cd_chain_multi_segment() -> str:
    # The merge is usually the tail of a longer chain.
    out = effective_merge_dir(
        "cd /Git/dev-env && git add . && gh pr merge --squash --delete-branch",
        "/Git/lifting-logbook",
    )
    assert out == "/Git/dev-env", f"got {out!r}"
    return "cd <repo> && ... && gh pr merge -> the repo dir"


def test_merge_dir_quoted_path() -> str:
    out = effective_merge_dir('cd "/Git/dir with spaces" && gh pr merge --squash', "/base")
    assert out == "/Git/dir with spaces", f"got {out!r}"
    return "quoted cd path -> unquoted target dir"


def test_merge_dir_relative_resolved_against_cwd() -> str:
    import os
    out = effective_merge_dir("cd sub/repo && gh pr merge --squash", "/base")
    assert os.path.isabs(out), f"relative target not resolved: {out!r}"
    assert os.path.basename(out) == "repo"
    assert out == os.path.normpath(os.path.join("/base", "sub/repo"))
    return "relative cd path -> normalized join under cwd"


def test_merge_dir_semicolon_chain() -> str:
    out = effective_merge_dir("cd /Git/dev-env ; gh pr merge --squash", "/base")
    assert out == "/Git/dev-env", f"got {out!r}"
    return "cd <repo> ; gh pr merge -> that repo (semicolon chain)"


def test_merge_dir_cd_after_merge_ignored() -> str:
    # A cd appearing only AFTER the merge does not govern it -> fall back to cwd.
    out = effective_merge_dir("gh pr merge --squash && cd /Git/elsewhere", "/base")
    assert out == "/base", f"cd after merge must not redirect: {out!r}"
    return "cd after the merge -> cwd (merge region excludes it)"


def main() -> int:
    tests = [
        ("reads command output from stdout", test_reads_stdout),
        ("combines stdout and stderr", test_combines_stdout_and_stderr),
        ("stderr-only (gh pr merge shape)", test_stderr_only),
        ("legacy output field still works", test_legacy_output_fallback),
        ("stdout preferred over legacy output", test_stdout_preferred_over_legacy_output),
        ("empty/malformed payloads yield ''", test_empty_and_malformed_payloads),
        ("pre-fix output read was empty (#377 root cause)", test_old_output_read_would_have_been_empty),
        ("merge marker detected (incl cross-repo)", test_merge_marker_detected),
        ("merge marker excludes auto/failure/stray", test_merge_marker_excludes_auto_failure_and_stray),
        ("merge PR number from output", test_merge_pr_number_from_output),
        ("merge dir: bare merge -> cwd", test_merge_dir_bare_merge_is_cwd),
        ("merge dir: cd <repo> && merge -> that repo", test_merge_dir_cd_chain_redirects),
        ("merge dir: cd <repo> && ... && merge -> repo dir", test_merge_dir_cd_chain_multi_segment),
        ("merge dir: quoted cd path", test_merge_dir_quoted_path),
        ("merge dir: relative path resolved vs cwd", test_merge_dir_relative_resolved_against_cwd),
        ("merge dir: semicolon chain", test_merge_dir_semicolon_chain),
        ("merge dir: cd after merge ignored", test_merge_dir_cd_after_merge_ignored),
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
