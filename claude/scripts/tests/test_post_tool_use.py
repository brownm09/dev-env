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

These tests exercise the pure helpers offline (no network, no gh subprocess),
matching the repo's fixture-only test convention. `add_to_project` (the live gh
call) is intentionally not tested.

Usage:
    py -3 claude/scripts/tests/test_post_tool_use.py

Exit 0 = all pass.
"""

import importlib.util
import sys
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

URL = "https://github.com/brownm09/dev-env/issues/377"
OTHER_REPO_URL = "https://github.com/someone/other-repo/issues/5"
REPO = "brownm09/dev-env"


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
