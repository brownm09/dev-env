#!/usr/bin/env python3
"""Unit tests for session-start-sync.py's pure decision/formatting helpers.

dev-env#966 (ADR-130): generalizes dev-env-sync.py's fetch -> compare -> `pull --ff-only`
mechanic (hardcoded to one repo/branch) to any repo a session starts in, resolved dynamically
from cwd. This file exercises every pure decision and formatting helper offline (no
subprocess, no network, no git) plus the two small file-I/O helpers against a
`tempfile.TemporaryDirectory()` -- `main()`'s own subprocess orchestration is intentionally
not covered here, matching this repo's established convention for topology-diagnosing
orchestration scripts (`dev-env-sync.py` / `pre-bash-drift-check.py`'s own test files, `##
Testing` items 22/26/56/59). `_resolve_path` is also not directly tested -- a thin,
filesystem-state-dependent wrapper around `Path.resolve()`, mirroring `_worktree_topology`'s
own private `_norm` (which that module's test suite likewise exercises only via its callers,
not in isolation).

Usage:
    py -3 claude/scripts/tests/test_session_start_sync.py

Exit 0 = all pass.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "session-start-sync.py"

# The script imports _winsubp, _hookout, _hookutil, _worktree_liveness, and
# _worktree_topology (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename -- import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("session_start_sync", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
sss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sss)  # safe: main() is guarded by __main__

from _worktree_topology import DETACHED  # noqa: E402 -- resolvable only after sys.path.insert

_plural = sss._plural
_count_from = sss._count_from
resolve_default_branch = sss.resolve_default_branch
is_canonical_checkout = sss.is_canonical_checkout
resolve_compare_ref = sss.resolve_compare_ref
can_autofix = sss.can_autofix
classify_block_reason = sss.classify_block_reason
format_stale_warning = sss.format_stale_warning
format_autofix_success = sss.format_autofix_success
format_autofix_failure = sss.format_autofix_failure
load_disable_flag = sss.load_disable_flag
_read_hook_config_json = sss._read_hook_config_json

LOCAL = "aaaaaaaa1111111111111111111111111111111a"
REMOTE = "bbbbbbbb2222222222222222222222222222222b"

CANONICAL_WORKTREES = [
    {"path": "C:/Users/brown/Git/career-playbook", "branch": "main"},
    {"path": "C:/Users/brown/Git/career-playbook/.claude/worktrees/foo", "branch": "claude/foo"},
]

# The "eligible for auto-fix" baseline kwargs -- individual tests flip exactly one field.
_ELIGIBLE = dict(
    is_canonical=True,
    branch="main",
    default_branch="main",
    ahead_count=0,
    tree_clean=True,
    concurrent_session=False,
)


def _proc(returncode: int, stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr="")


# --- resolve_default_branch ---------------------------------------------------------------


def test_resolve_default_branch_success() -> str:
    assert resolve_default_branch(0, "main\n") == "main"
    return "returncode 0, stdout 'main\\n' -> 'main'"


def test_resolve_default_branch_failure_falls_back_to_main() -> str:
    assert resolve_default_branch(1, "") == "main", "unset origin/HEAD must fall back to main"
    return "returncode 1 (origin/HEAD unset) -> 'main' fallback"


def test_resolve_default_branch_empty_stdout_falls_back_to_main() -> str:
    assert resolve_default_branch(0, "   \n") == "main", "blank stdout must not yield a blank branch"
    return "returncode 0, blank stdout -> 'main' fallback"


def test_resolve_default_branch_non_main_default() -> str:
    assert resolve_default_branch(0, "trunk\n") == "trunk", "a non-main default must pass through unchanged"
    return "returncode 0, stdout 'trunk\\n' -> 'trunk' (not hardcoded to main)"


# --- is_canonical_checkout -----------------------------------------------------------------


def test_is_canonical_checkout_entry_zero_matches() -> str:
    got = is_canonical_checkout(CANONICAL_WORKTREES[0]["path"], CANONICAL_WORKTREES)
    assert got is True
    return "repo_root matching worktree-list entry 0 -> True"


def test_is_canonical_checkout_linked_worktree_does_not_match() -> str:
    got = is_canonical_checkout(CANONICAL_WORKTREES[1]["path"], CANONICAL_WORKTREES)
    assert got is False
    return "repo_root matching a linked (non-entry-0) worktree -> False"


def test_is_canonical_checkout_empty_worktrees_returns_false() -> str:
    got = is_canonical_checkout("C:/Users/brown/Git/career-playbook", [])
    assert got is False, "an undeterminable worktree list must not be trusted as canonical"
    return "empty worktree list (git worktree list failed) -> False (fail-safe, no autofix)"


def test_is_canonical_checkout_unrelated_path_returns_false() -> str:
    got = is_canonical_checkout("C:/Users/brown/Git/some-other-repo", CANONICAL_WORKTREES)
    assert got is False
    return "repo_root matching no known worktree entry -> False"


# --- resolve_compare_ref --------------------------------------------------------------------


def test_resolve_compare_ref_uses_upstream_when_present() -> str:
    got = resolve_compare_ref("feature-x", "origin/feature-x", "main")
    assert got == "origin/feature-x"
    return "tracked branch -> its own upstream, not origin/<default>"


def test_resolve_compare_ref_falls_back_without_upstream() -> str:
    got = resolve_compare_ref(DETACHED, None, "main")
    assert got == "origin/main"
    return "detached / no-upstream -> f'origin/{default_branch}' fallback"


def test_resolve_compare_ref_fallback_uses_actual_default_branch() -> str:
    got = resolve_compare_ref("local-only", None, "trunk")
    assert got == "origin/trunk", "the fallback must use the resolved default, not a hardcoded 'main'"
    return "no-upstream fallback respects a non-main default branch"


# --- can_autofix -----------------------------------------------------------------------------


def test_can_autofix_all_conditions_true() -> str:
    assert can_autofix(**_ELIGIBLE) is True
    return "canonical + on-default + ff-safe + clean + no-concurrent-session -> True"


def test_can_autofix_false_when_not_canonical() -> str:
    kwargs = {**_ELIGIBLE, "is_canonical": False}
    assert can_autofix(**kwargs) is False
    return "is_canonical=False -> False (never auto-mutate a linked worktree)"


def test_can_autofix_false_when_off_default_branch() -> str:
    kwargs = {**_ELIGIBLE, "branch": "feature-x"}
    assert can_autofix(**kwargs) is False
    return "branch != default_branch -> False (covers detached HEAD too)"


def test_can_autofix_false_when_ahead() -> str:
    kwargs = {**_ELIGIBLE, "ahead_count": 1}
    assert can_autofix(**kwargs) is False
    return "ahead_count > 0 -> False (not a true fast-forward)"


def test_can_autofix_false_when_dirty() -> str:
    kwargs = {**_ELIGIBLE, "tree_clean": False}
    assert can_autofix(**kwargs) is False
    return "tree_clean=False -> False (nothing uncommitted may be clobbered)"


def test_can_autofix_false_when_concurrent_session() -> str:
    kwargs = {**_ELIGIBLE, "concurrent_session": True}
    assert can_autofix(**kwargs) is False
    return "concurrent_session=True -> False (never mutate a checkout another session may use)"


# --- classify_block_reason (fixed precedence) -------------------------------------------------


def test_classify_block_reason_not_canonical_precedence() -> str:
    # Not-canonical is first in precedence even when every other condition also fails.
    got = classify_block_reason(
        is_canonical=False, branch="feature-x", default_branch="main",
        ahead_count=1, tree_clean=False, concurrent_session=True,
    )
    assert "linked worktree" in got, got
    return "not-canonical wins precedence over every other simultaneous failure"


def test_classify_block_reason_detached_head() -> str:
    got = classify_block_reason(
        is_canonical=True, branch=DETACHED, default_branch="main",
        ahead_count=0, tree_clean=True, concurrent_session=False,
    )
    assert "detached" in got.lower(), got
    return "DETACHED branch names 'HEAD is detached' specifically, not a generic off-branch message"


def test_classify_block_reason_off_branch_not_detached() -> str:
    got = classify_block_reason(
        is_canonical=True, branch="feature-x", default_branch="main",
        ahead_count=0, tree_clean=True, concurrent_session=False,
    )
    assert "feature-x" in got and "main" in got, got
    return "off-default-branch (not detached) names both the actual and default branch"


def test_classify_block_reason_diverged() -> str:
    got = classify_block_reason(
        is_canonical=True, branch="main", default_branch="main",
        ahead_count=3, tree_clean=True, concurrent_session=False,
    )
    assert "3 commit" in got and "diverged" in got, got
    return "ahead_count > 0 (on default branch) -> diverged reason, names the count"


def test_classify_block_reason_dirty_tree() -> str:
    got = classify_block_reason(
        is_canonical=True, branch="main", default_branch="main",
        ahead_count=0, tree_clean=False, concurrent_session=False,
    )
    assert "uncommitted" in got, got
    return "tree_clean=False (nothing else wrong) -> dirty-tree reason"


def test_classify_block_reason_concurrent_session() -> str:
    got = classify_block_reason(
        is_canonical=True, branch="main", default_branch="main",
        ahead_count=0, tree_clean=True, concurrent_session=True,
    )
    assert "another session" in got, got
    return "only concurrent_session=True wrong -> concurrent-session reason"


def test_classify_block_reason_precedence_multi_failure_diverged_before_dirty() -> str:
    # Both ahead_count>0 AND tree not clean -- diverged must win (fixed precedence).
    got = classify_block_reason(
        is_canonical=True, branch="main", default_branch="main",
        ahead_count=2, tree_clean=False, concurrent_session=False,
    )
    assert "diverged" in got and "uncommitted" not in got, got
    return "diverged beats dirty-tree when both apply (documented precedence order)"


# --- formatters ------------------------------------------------------------------------------


def test_format_stale_warning_shape() -> str:
    msg = format_stale_warning(
        "career-playbook", DETACHED, "origin/main", LOCAL, REMOTE, 20, 0,
        "HEAD is detached (not on 'main')",
    )
    assert "[session-start-sync]" in msg
    assert "career-playbook" in msg
    assert "20 commits behind" in msg
    assert LOCAL[:8] in msg and REMOTE[:8] in msg
    assert "detached" in msg
    return "names repo, branch, behind-count (plural), truncated SHAs, and the block reason"


def test_format_stale_warning_singular_commit() -> str:
    msg = format_stale_warning("x", "main", "origin/main", LOCAL, REMOTE, 1, 0, "dirty tree")
    assert "1 commit behind" in msg and "1 commits" not in msg
    return "behind_count=1 -> singular 'commit', not 'commits'"


def test_format_stale_warning_with_divergence_clause() -> str:
    msg = format_stale_warning("x", "main", "origin/main", LOCAL, REMOTE, 5, 2, "diverged")
    assert "2 commits ahead of it" in msg
    return "ahead_count > 0 adds the divergence clause naming the ahead-count"


def test_format_stale_warning_no_divergence_clause_when_not_ahead() -> str:
    msg = format_stale_warning("x", "main", "origin/main", LOCAL, REMOTE, 5, 0, "dirty tree")
    assert "ahead of it" not in msg
    return "ahead_count == 0 omits the divergence clause entirely"


def test_format_autofix_success_shape() -> str:
    msg = format_autofix_success("dev-env", "main", LOCAL, REMOTE, 4)
    assert "[session-start-sync]" in msg
    assert "Fast-forwarded dev-env main" in msg
    assert LOCAL[:8] in msg and REMOTE[:8] in msg
    assert "4 commits" in msg
    return "names repo, default branch, truncated SHAs, and pluralized commit count"


def test_format_autofix_success_singular_commit() -> str:
    msg = format_autofix_success("dev-env", "main", LOCAL, REMOTE, 1)
    assert "1 commit)" in msg and "1 commits)" not in msg
    return "behind_count=1 -> singular 'commit'"


def test_format_autofix_failure_ascii_sanitizes_stderr() -> str:
    # An em dash in git's stderr must not survive raw (cp1252-wire-safety, matching
    # dev-env-sync.py's own use of _hookout.ascii_sanitize on echoed git stderr).
    stderr = "error: local changes \u2014 would be overwritten"
    msg = format_autofix_failure("dev-env", "main", stderr)
    assert "\u2014" not in msg, "em dash must be ascii_sanitize'd before inclusion"
    assert "local changes - would be overwritten" in msg
    assert msg.isascii()
    return "echoed git stderr is ascii_sanitize'd (em dash -> '-'); result is guaranteed ASCII"


# --- load_disable_flag -------------------------------------------------------------------------


def test_load_disable_flag_missing_key_false() -> str:
    assert load_disable_flag({}) is False
    return "missing key -> False (enabled by default)"


def test_load_disable_flag_explicit_true() -> str:
    assert load_disable_flag({"session_start_sync_disabled": True}) is True
    return "explicit true -> True (disabled)"


def test_load_disable_flag_explicit_false() -> str:
    assert load_disable_flag({"session_start_sync_disabled": False}) is False
    return "explicit false -> False (enabled)"


def test_load_disable_flag_non_bool_value_false() -> str:
    assert load_disable_flag({"session_start_sync_disabled": "true"}) is False, (
        "a stringly-typed value must not be trusted as an affirmative disable"
    )
    return "non-bool value (string 'true') -> False (defensive; only literal True disables)"


def test_load_disable_flag_non_dict_input_false() -> str:
    assert load_disable_flag(None) is False
    assert load_disable_flag([]) is False
    return "non-dict input (None, list) -> False, never raises"


# --- _count_from / _plural (shared convention with dev-env-sync.py's own test) -----------------


def test_count_from_parses_valid_digit_output() -> str:
    assert _count_from(_proc(0, "7\n")) == 7
    return "returncode 0, stdout '7\\n' -> 7"


def test_count_from_fails_open_on_nonzero_returncode() -> str:
    assert _count_from(_proc(1, "7\n")) == 0
    return "returncode 1 -> 0 (fail-open; advisory diagnostic, not load-bearing)"


def test_count_from_fails_open_on_non_digit_output() -> str:
    assert _count_from(_proc(0, "")) == 0
    return "empty stdout -> 0, no raise"


def test_plural_singular_and_plural() -> str:
    assert _plural(1) == ""
    assert _plural(0) == "s"
    assert _plural(2) == "s"
    return "_plural(1)=='' ; _plural(0)==_plural(2)=='s'"


# --- _read_hook_config_json (I/O helper, tested against a tempdir) -----------------------------


def test_read_hook_config_reads_valid_json() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        claude_dir = repo_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hook-config.json").write_text(
            json.dumps({"session_start_sync_disabled": True}), encoding="utf-8"
        )
        got = _read_hook_config_json(str(repo_root))
        assert got == {"session_start_sync_disabled": True}
    return "valid .claude/hook-config.json round-trips through json.loads"


def test_read_hook_config_missing_file_returns_empty_dict() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        got = _read_hook_config_json(tmp)
        assert got == {}
    return "no .claude/hook-config.json present -> {} (never raises)"


def test_read_hook_config_malformed_json_returns_empty_dict() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        claude_dir = repo_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hook-config.json").write_text("{not valid json", encoding="utf-8")
        got = _read_hook_config_json(str(repo_root))
        assert got == {}
    return "malformed JSON -> {} (never raises; hook must not crash on a bad config file)"


def test_read_hook_config_non_dict_json_returns_empty_dict() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo_root = Path(tmp)
        claude_dir = repo_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "hook-config.json").write_text("[1, 2, 3]", encoding="utf-8")
        got = _read_hook_config_json(str(repo_root))
        assert got == {}
    return "valid JSON that isn't an object (a bare array) -> {}, not the array itself"


def main() -> int:
    tests = [
        ("resolve_default_branch: success", test_resolve_default_branch_success),
        ("resolve_default_branch: failure -> main", test_resolve_default_branch_failure_falls_back_to_main),
        ("resolve_default_branch: blank stdout -> main", test_resolve_default_branch_empty_stdout_falls_back_to_main),
        ("resolve_default_branch: non-main default", test_resolve_default_branch_non_main_default),
        ("is_canonical_checkout: entry 0 matches", test_is_canonical_checkout_entry_zero_matches),
        ("is_canonical_checkout: linked worktree", test_is_canonical_checkout_linked_worktree_does_not_match),
        ("is_canonical_checkout: empty worktrees", test_is_canonical_checkout_empty_worktrees_returns_false),
        ("is_canonical_checkout: unrelated path", test_is_canonical_checkout_unrelated_path_returns_false),
        ("resolve_compare_ref: tracked upstream", test_resolve_compare_ref_uses_upstream_when_present),
        ("resolve_compare_ref: no-upstream fallback", test_resolve_compare_ref_falls_back_without_upstream),
        ("resolve_compare_ref: fallback respects default", test_resolve_compare_ref_fallback_uses_actual_default_branch),
        ("can_autofix: all true", test_can_autofix_all_conditions_true),
        ("can_autofix: not canonical", test_can_autofix_false_when_not_canonical),
        ("can_autofix: off default branch", test_can_autofix_false_when_off_default_branch),
        ("can_autofix: ahead", test_can_autofix_false_when_ahead),
        ("can_autofix: dirty", test_can_autofix_false_when_dirty),
        ("can_autofix: concurrent session", test_can_autofix_false_when_concurrent_session),
        ("classify_block_reason: not-canonical precedence", test_classify_block_reason_not_canonical_precedence),
        ("classify_block_reason: detached HEAD", test_classify_block_reason_detached_head),
        ("classify_block_reason: off-branch, not detached", test_classify_block_reason_off_branch_not_detached),
        ("classify_block_reason: diverged", test_classify_block_reason_diverged),
        ("classify_block_reason: dirty tree", test_classify_block_reason_dirty_tree),
        ("classify_block_reason: concurrent session", test_classify_block_reason_concurrent_session),
        ("classify_block_reason: diverged beats dirty (precedence)", test_classify_block_reason_precedence_multi_failure_diverged_before_dirty),
        ("format_stale_warning: shape", test_format_stale_warning_shape),
        ("format_stale_warning: singular commit", test_format_stale_warning_singular_commit),
        ("format_stale_warning: divergence clause", test_format_stale_warning_with_divergence_clause),
        ("format_stale_warning: no divergence clause", test_format_stale_warning_no_divergence_clause_when_not_ahead),
        ("format_autofix_success: shape", test_format_autofix_success_shape),
        ("format_autofix_success: singular commit", test_format_autofix_success_singular_commit),
        ("format_autofix_failure: ascii_sanitize", test_format_autofix_failure_ascii_sanitizes_stderr),
        ("load_disable_flag: missing key", test_load_disable_flag_missing_key_false),
        ("load_disable_flag: explicit true", test_load_disable_flag_explicit_true),
        ("load_disable_flag: explicit false", test_load_disable_flag_explicit_false),
        ("load_disable_flag: non-bool value", test_load_disable_flag_non_bool_value_false),
        ("load_disable_flag: non-dict input", test_load_disable_flag_non_dict_input_false),
        ("_count_from: valid digits", test_count_from_parses_valid_digit_output),
        ("_count_from: nonzero returncode", test_count_from_fails_open_on_nonzero_returncode),
        ("_count_from: non-digit output", test_count_from_fails_open_on_non_digit_output),
        ("_plural: singular/plural", test_plural_singular_and_plural),
        ("_read_hook_config_json: valid file", test_read_hook_config_reads_valid_json),
        ("_read_hook_config_json: missing file", test_read_hook_config_missing_file_returns_empty_dict),
        ("_read_hook_config_json: malformed JSON", test_read_hook_config_malformed_json_returns_empty_dict),
        ("_read_hook_config_json: non-dict JSON", test_read_hook_config_non_dict_json_returns_empty_dict),
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
