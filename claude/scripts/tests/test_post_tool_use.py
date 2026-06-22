#!/usr/bin/env python3
"""Unit tests for post-tool-use.py command-output reading and URL extraction.

`post-tool-use.py` is a PostToolUse hook that fires after `gh issue create` /
`gh pr create` and adds the new item to the configured GitHub Project. Before
the fix in dev-env#377 it read `tool_response["output"]`, but Claude Code's Bash
hook payload exposes a command's output under `stdout`/`stderr` (no `output`
key). So `output` was always `""`, `extract_github_url` returned `None`, and —
because the dev-env config sets `repo` — the hook hit a silent `sys.exit(0)`.
The hook therefore never fired in any retained transcript; every project-add was
done by hand via the documented fallback.

The fix routes output through the pure `read_command_output()` helper (stdout +
stderr, with a legacy `output` fallback) and de-silences the no-URL path so a
successful create that yields no GitHub URL at all surfaces instead of vanishing.

A later fix (dev-env#378) makes `load_config` fall back to the canonical
checkout's gitignored `hook-config.json` when the worktree-local copy is absent,
so the hook fires in worktree sessions too (it previously hit a silent
`sys.exit(0)`). `canonical_root_from_worktree` — the pure path-derivation behind
that fallback — is covered here, and `load_config`'s canonical-worktree branch is
exercised end-to-end against a hermetic temp dir.

These tests exercise the pure helpers offline (no network, no gh subprocess),
matching the repo's fixture-only test convention. `add_to_project` (the live gh
call) and the `subprocess.run` in `canonical_root_via_git` are not tested — they
shell out — but the pure resolver it delegates to, `_canonical_root_from_common_dir`,
is covered.

Usage:
    py -3 claude/scripts/tests/test_post_tool_use.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-tool-use.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable when
# exec_module runs the module body.
sys.path.insert(0, str(SCRIPT.parent))

# The script filename is hyphenated, so import it by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_tool_use", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
post_tool_use = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(post_tool_use)  # safe: main() is guarded by __main__
read_command_output = post_tool_use.read_command_output
extract_github_url = post_tool_use.extract_github_url
canonical_root_from_worktree = post_tool_use.canonical_root_from_worktree
_canonical_root_from_common_dir = post_tool_use._canonical_root_from_common_dir
load_config = post_tool_use.load_config

URL = "https://github.com/brownm09/dev-env/issues/377"
OTHER_REPO_URL = "https://github.com/someone/other-repo/issues/5"
REPO = "brownm09/dev-env"

# dev-env#378 worktree fixtures: a Claude-managed worktree cwd and its canonical root.
WT_FWD = "C:/Users/brown/Git/dev-env/.claude/worktrees/sweet-mendel-8e98d1"
WT_BACK = r"C:\Users\brown\Git\dev-env\.claude\worktrees\sweet-mendel-8e98d1"
CANON = "C:/Users/brown/Git/dev-env"


def test_reads_stdout() -> str:
    # The real Bash payload shape: output lives under `stdout`, not `output`.
    payload = {"tool_response": {"stdout": URL, "stderr": "", "interrupted": False}}
    got = read_command_output(payload)
    assert got == URL, f"expected stdout URL, got {got!r}"
    return "stdout-shaped payload -> output is the URL (the #377 regression)"


def test_combines_stdout_and_stderr() -> str:
    # `gh pr merge` prints its success line to stderr; both must be captured.
    payload = {"tool_response": {"stdout": "done", "stderr": "warn"}}
    got = read_command_output(payload)
    assert got == "done\nwarn", f"expected stdout+stderr joined, got {got!r}"
    return "stdout + stderr are both captured"


def test_legacy_output_fallback() -> str:
    # If a build ever sends the legacy `output` field, still read it.
    payload = {"tool_response": {"output": URL}}
    got = read_command_output(payload)
    assert got == URL, f"expected legacy output fallback, got {got!r}"
    return "legacy `output` field still works (forward/backward compatible)"


def test_empty_and_malformed_payloads() -> str:
    assert read_command_output({}) == "", "missing tool_response -> ''"
    assert read_command_output({"tool_response": {}}) == "", "empty tool_response -> ''"
    assert read_command_output({"tool_response": None}) == "", "None tool_response -> ''"
    assert read_command_output({"tool_response": "x"}) == "", "non-dict tool_response -> ''"
    return "missing/empty/None/non-dict tool_response all yield '' (no crash)"


def test_old_output_read_would_have_been_empty() -> str:
    # Pin the root cause: the pre-fix read (`.get("output")`) on the real shape
    # is empty, which is exactly what silently broke the hook.
    real_shape = {"stdout": URL, "stderr": "", "interrupted": False, "isImage": False}
    assert real_shape.get("output", "") == "", "pre-fix read must be empty on real shape"
    assert read_command_output({"tool_response": real_shape}) == URL, "fixed read recovers URL"
    return "pre-fix `output` read was '' on the real payload; fixed read recovers the URL"


def test_extract_url_matches_configured_repo() -> str:
    assert extract_github_url(URL, REPO) == URL, "matching-repo URL should extract"
    return "extract_github_url returns the URL for the configured repo"


def test_extract_url_filters_other_repo() -> str:
    assert extract_github_url(OTHER_REPO_URL, REPO) is None, "different-repo URL must not match"
    return "repo filter rejects a URL for a different repo"


def test_extract_url_empty_output_is_none() -> str:
    assert extract_github_url("", REPO) is None, "empty output -> None"
    assert extract_github_url("no url here", REPO) is None, "no URL -> None"
    return "empty / URL-less output -> None"


def test_desilence_predicate() -> str:
    # The de-silenced branch stays silent only when SOME GitHub URL is present
    # (a legitimate different-repo create); it surfaces when there is none.
    different_repo = OTHER_REPO_URL
    no_url = "=== merge exit: 0 ==="
    assert extract_github_url(different_repo, None), "different-repo case has a URL -> stay silent"
    assert not extract_github_url(no_url, None), "no-URL case is empty -> surface advisory"
    return "different-repo output stays silent; no-URL output surfaces (de-silence behavior)"


# --- dev-env#378: canonical-checkout config fallback in worktree sessions ---


def test_canonical_root_forward_slash() -> str:
    assert canonical_root_from_worktree(WT_FWD) == "C:/Users/brown/Git/dev-env"
    return "forward-slash worktree cwd -> canonical root"


def test_canonical_root_backslash() -> str:
    got = canonical_root_from_worktree(WT_BACK)
    assert got == r"C:\Users\brown\Git\dev-env", f"got {got!r}"
    return "backslash worktree cwd -> canonical root (separator preserved)"


def test_canonical_root_subdir() -> str:
    assert canonical_root_from_worktree(WT_FWD + "/claude/scripts") == "C:/Users/brown/Git/dev-env"
    return "cwd in a subdir of a worktree -> canonical root"


def test_canonical_root_main_checkout_none() -> str:
    assert canonical_root_from_worktree(CANON) is None
    return "main checkout cwd (no .claude/worktrees/) -> None"


def test_canonical_root_sibling_none() -> str:
    # Sibling worktrees (dev-env-188) are not under .claude/worktrees/, so the
    # pure regex returns None; canonical_root_via_git resolves those via git.
    assert canonical_root_from_worktree("C:/Users/brown/Git/dev-env-188") is None
    return "sibling worktree (dev-env-188) -> None (git fallback handles it, not the regex)"


def test_canonical_root_posix() -> str:
    assert canonical_root_from_worktree("/home/me/Git/dev-env/.claude/worktrees/abc") == "/home/me/Git/dev-env"
    return "POSIX worktree path -> canonical root"


def test_canonical_root_empty_and_none() -> str:
    assert canonical_root_from_worktree("") is None
    assert canonical_root_from_worktree(None) is None
    return "empty / None cwd -> None (no crash)"


def test_load_config_falls_back_to_canonical() -> str:
    # The #378 fix end-to-end: a Claude-managed worktree cwd whose own
    # .claude/hook-config.json is absent resolves the canonical checkout's copy.
    # Hermetic and offline — the regex path resolves before any git fallback.
    with tempfile.TemporaryDirectory() as root:
        canon_cfg_dir = os.path.join(root, ".claude")
        os.makedirs(canon_cfg_dir)
        with open(os.path.join(canon_cfg_dir, "hook-config.json"), "w", encoding="utf-8") as f:
            json.dump({"project_number": "3", "project_owner": "brownm09"}, f)
        worktree = os.path.join(root, ".claude", "worktrees", "sweet-mendel-8e98d1")
        os.makedirs(worktree)  # worktree-local config deliberately absent
        cfg = load_config(worktree)
        assert cfg is not None, "worktree cwd must fall back to canonical config"
        assert cfg.get("project_number") == "3", f"got {cfg!r}"
    return "worktree-local config absent -> canonical checkout config used (the #378 fix)"


# --- pure resolver behind canonical_root_via_git (the sibling git fallback) ---


def test_common_dir_relative() -> str:
    # Main checkout: `git rev-parse --git-common-dir` returns ".git" relative to cwd.
    assert _canonical_root_from_common_dir("/repo", ".git") == os.path.normpath("/repo")
    return "relative '.git' -> cwd is the canonical root"


def test_common_dir_absolute_sibling() -> str:
    # Sibling worktree: --git-common-dir returns the canonical <root>/.git (absolute).
    got = _canonical_root_from_common_dir("/repo-188", "/repo/.git")
    assert got == os.path.normpath("/repo"), f"got {got!r}"
    return "absolute '<root>/.git' -> <root> (sibling worktree case)"


def test_common_dir_strips_whitespace() -> str:
    # Raw subprocess stdout carries a trailing newline.
    got = _canonical_root_from_common_dir("/repo-188", "/repo/.git\n")
    assert got == os.path.normpath("/repo"), f"got {got!r}"
    return "trailing newline in --git-common-dir output is stripped"


def test_common_dir_non_git_basename_none() -> str:
    assert _canonical_root_from_common_dir("/repo", "/repo/.bare") is None
    return "common dir not named '.git' -> None"


def test_common_dir_empty_none() -> str:
    assert _canonical_root_from_common_dir("/repo", "") is None
    assert _canonical_root_from_common_dir("/repo", "  \n") is None
    return "empty / whitespace common-dir output -> None"


def main() -> int:
    tests = [
        ("reads command output from stdout", test_reads_stdout),
        ("combines stdout and stderr", test_combines_stdout_and_stderr),
        ("legacy output field still works", test_legacy_output_fallback),
        ("empty/malformed payloads yield ''", test_empty_and_malformed_payloads),
        ("pre-fix output read was empty (#377 root cause)", test_old_output_read_would_have_been_empty),
        ("extract URL for configured repo", test_extract_url_matches_configured_repo),
        ("repo filter rejects other repo", test_extract_url_filters_other_repo),
        ("empty output extracts to None", test_extract_url_empty_output_is_none),
        ("de-silence predicate distinguishes the two misses", test_desilence_predicate),
        ("canonical root from forward-slash worktree", test_canonical_root_forward_slash),
        ("canonical root from backslash worktree", test_canonical_root_backslash),
        ("canonical root from worktree subdir", test_canonical_root_subdir),
        ("main checkout -> no canonical root", test_canonical_root_main_checkout_none),
        ("sibling worktree -> no canonical root (regex)", test_canonical_root_sibling_none),
        ("canonical root from POSIX worktree", test_canonical_root_posix),
        ("empty/None cwd -> no canonical root", test_canonical_root_empty_and_none),
        ("load_config falls back to canonical config (#378)", test_load_config_falls_back_to_canonical),
        ("git common-dir: relative .git -> cwd", test_common_dir_relative),
        ("git common-dir: absolute <root>/.git -> root", test_common_dir_absolute_sibling),
        ("git common-dir: stdout whitespace stripped", test_common_dir_strips_whitespace),
        ("git common-dir: non-.git basename -> None", test_common_dir_non_git_basename_none),
        ("git common-dir: empty output -> None", test_common_dir_empty_none),
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
