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
checkout's `hook-config.json` when the worktree-local copy is absent, so the
hook fires in worktree sessions too (it previously hit a silent `sys.exit(0)`).
That file is gitignored in dev-env's own convention -- not a universal one,
see post-tool-use.py's module docstring -- so this fallback is the case that
matters for a project following dev-env's convention; a project that tracks
the file in git (e.g. lifting-logbook) gets it from the cwd-local read on
`load_config`'s first line instead. `canonical_root_from_worktree` — the pure
path-derivation behind the fallback — is covered here, and `load_config`'s
canonical-worktree branch is exercised end-to-end against a hermetic temp dir.

dev-env#527 (ADR-076) adds a live `gh api graphql` fetch of `single_select`
field options, replacing the cached hook-config.json values on success and
falling back to them (labeled, so staleness is visible) on any failure. The
pure parse (`_parse_live_options`), the pure legacy-config normalization
shared between rendering and live-fetch target discovery
(`_resolve_required_fields`), the field-selection logic of
`fetch_live_required_field_options` (via an injected fake `fetch_fn`, so no
real subprocess runs), and `format_reminder`'s live/cached/unlabeled rendering
branches are all covered here. The real `gh api graphql` call inside
`fetch_live_field_options` is not — it shells out, matching this file's
existing `add_to_project` / `canonical_root_via_git` convention — and was
instead verified once by hand against dev-env's own live Impact field during
development of this fix.

These tests exercise the pure helpers offline (no network, no gh subprocess),
matching the repo's fixture-only test convention. `add_to_project` (the live gh
call) and the `subprocess.run` in `canonical_root_via_git` are not tested — they
shell out — but the pure resolver it delegates to, `_canonical_root_from_common_dir`,
is covered. Their UTF-8 output-decoding behavior (dev-env#503 — both calls used to
crash with UnicodeDecodeError on Windows) is covered separately by
`test_winsubp.py`'s tests of `_apply_windows_subprocess_defaults`, the shared
helper both calls now go through via the `_winsubp` import at the top of this file.

Usage:
    py -3 claude/scripts/tests/test_post_tool_use.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import subprocess
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
is_issue_create_command = post_tool_use.is_issue_create_command
is_pr_create_command = post_tool_use.is_pr_create_command
_parse_live_options = post_tool_use._parse_live_options
_resolve_required_fields = post_tool_use._resolve_required_fields
fetch_live_required_field_options = post_tool_use.fetch_live_required_field_options
format_reminder = post_tool_use.format_reminder

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


# ---------------------------------------------------------------------------
# is_issue_create_command / is_pr_create_command  (dev-env#499)
#
# Before this fix, main() detected these with an unanchored
# re.search(r"\bgh\s+issue\s+create\b", command) / re.search(r"\bgh\s+pr\s+create\b",
# command) over the WHOLE raw command string -- matching the substring anywhere,
# including inside a heredoc body, a quoted commit message, a grep pattern
# argument, or a --text field value. These two functions now share
# _hookio.scan_top_level with pr-merge-reminder.py's equivalent detection, so
# only a genuine top-level invocation counts.
# ---------------------------------------------------------------------------


def test_pr_create_simple_matches() -> str:
    assert is_pr_create_command("gh pr create --fill")
    return "bare gh pr create -> match"


def test_issue_create_simple_matches() -> str:
    assert is_issue_create_command('gh issue create --title "x" --body "y"')
    return "bare gh issue create -> match"


def test_pr_create_with_cd_prefix_matches() -> str:
    assert is_pr_create_command("cd /some/path && gh pr create --fill")
    return "cd ... && gh pr create -> match"


def test_pr_create_chained_with_merge_still_matches() -> str:
    # A genuine top-level create chained with a merge must still be detected --
    # the fix must not overcorrect into false negatives.
    assert is_pr_create_command("gh pr create --fill && gh pr merge --auto")
    return "top-level gh pr create chained with gh pr merge -> still matches"


def test_pr_create_not_matched_as_issue_create() -> str:
    assert not is_issue_create_command("gh pr create --fill")
    return "gh pr create -> not an issue-create match"


def test_issue_create_not_matched_as_pr_create() -> str:
    assert not is_pr_create_command("gh issue create --title x")
    return "gh issue create -> not a pr-create match"


# --- dev-env#499 false-positive reproductions (both PR- and issue-create) ---
# Each repro embeds the literal example command `gh pr create --fill && gh pr
# merge --auto` (or the issue-create equivalent) the way the real dev-env#494
# fix session did -- inside text that is NOT a real invocation.

def test_pr_create_in_heredoc_commit_body_not_matched() -> str:
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        "fix(hooks): explain the gh pr create --fill && gh pr merge --auto example\n"
        "EOF\n"
        ')"'
    )
    assert not is_pr_create_command(cmd)
    return "gh pr create inside a heredoc commit body -> no match (dev-env#499)"


def test_issue_create_in_heredoc_commit_body_not_matched() -> str:
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        'fix(hooks): explain the gh issue create --title "x" example\n'
        "EOF\n"
        ')"'
    )
    assert not is_issue_create_command(cmd)
    return "gh issue create inside a heredoc commit body -> no match (dev-env#499)"


def test_pr_create_in_quoted_commit_message_not_matched() -> str:
    cmd = 'git commit -m "document gh pr create --fill && gh pr merge --auto behavior"'
    assert not is_pr_create_command(cmd)
    return "gh pr create inside a quoted commit message -> no match (dev-env#499 repro 1)"


def test_issue_create_in_quoted_commit_message_not_matched() -> str:
    cmd = 'git commit -m "document the gh issue create --title flag behavior"'
    assert not is_issue_create_command(cmd)
    return "gh issue create inside a quoted commit message -> no match"


def test_grep_pattern_argument_not_matched_for_either() -> str:
    # The actual dev-env#499 repro 2: a single grep whose PATTERN argument
    # names both literal strings, run against the hook's own source.
    cmd = (
        'grep -n "gh pr create\\|gh issue create\\|gh pr merge" '
        "claude/scripts/post-tool-use.py"
    )
    assert not is_pr_create_command(cmd)
    assert not is_issue_create_command(cmd)
    return "grep pattern argument naming both strings -> neither detector fires (dev-env#499 repro 2)"


def test_pr_create_in_project_item_edit_text_not_matched() -> str:
    cmd = (
        "gh project item-edit --project-id PVT_x --id ITEM --field-id FIELD "
        '--text "fixed the gh pr create --fill && gh pr merge --auto detection bug"'
    )
    assert not is_pr_create_command(cmd)
    return "gh pr create inside a --text field value -> no match (dev-env#499 repro 3)"


def test_issue_create_in_project_item_edit_text_not_matched() -> str:
    cmd = (
        "gh project item-edit --project-id PVT_x --id ITEM --field-id FIELD "
        '--text "fixed the gh issue create detection bug"'
    )
    assert not is_issue_create_command(cmd)
    return "gh issue create inside a --text field value -> no match"


def test_pr_create_in_pr_comment_body_not_matched() -> str:
    cmd = 'gh pr comment 500 --body "Example: gh pr create --fill && gh pr merge --auto"'
    assert not is_pr_create_command(cmd)
    return "gh pr create inside a PR comment body -> no match (dev-env#499 repro 4)"


# --- subshell / double-quote negatives (mirroring test_pr_merge_reminder.py) ---

def test_pr_create_in_subshell_not_matched() -> str:
    assert not is_pr_create_command("echo $(gh pr create --fill)")
    return "gh pr create inside $() subshell -> no match"


def test_issue_create_in_subshell_not_matched() -> str:
    assert not is_issue_create_command("echo $(gh issue create --title x)")
    return "gh issue create inside $() subshell -> no match"


def test_pr_create_in_double_quotes_not_matched() -> str:
    assert not is_pr_create_command('echo "gh pr create --fill"')
    return "gh pr create inside double quotes -> no match"


# ---------------------------------------------------------------------------
# main() end-to-end: the exit_code != 0 gate (lines immediately following the
# detection swap) must still short-circuit correctly. Both cases share cwd,
# command, and output -- differing only in exitCode -- so the outcome
# difference (exit 0 vs exit 2) isolates exactly the gate under test, without
# ever invoking a live `gh`/network call in either branch (the "no GitHub URL
# found" advisory path is reached, not add_to_project).
# ---------------------------------------------------------------------------

def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _hook_config_cwd(tmp_root: str) -> str:
    cfg_dir = os.path.join(tmp_root, ".claude")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "hook-config.json"), "w", encoding="utf-8") as f:
        json.dump({"project_number": "999", "project_owner": "testowner"}, f)
    return tmp_root


def test_main_exit_code_nonzero_short_circuits_even_when_create_detected() -> str:
    with tempfile.TemporaryDirectory() as tmp_root:
        cwd = _hook_config_cwd(tmp_root)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --fill"},
            "tool_response": {"stdout": "done", "stderr": "", "exitCode": 1},
            "cwd": cwd,
        }
        result = _run_hook(payload)
        assert result.returncode == 0, (
            f"expected exit 0 (exit_code gate short-circuits), got {result.returncode}: "
            f"stderr={result.stderr!r}"
        )
        assert result.stderr == "", f"expected no stderr output, got {result.stderr!r}"
    return "genuine top-level create + exitCode!=0 -> exit 0, silent (gate unaffected by the fix)"


def test_main_exit_code_zero_proceeds_past_gate_to_no_url_advisory() -> str:
    # Control for the test above: same command/config/output, only exitCode
    # differs. This proves detection really fired True (the run reaches the
    # downstream "no GitHub URL found" branch, exit 2) rather than the exit-0
    # result above being coincidental (e.g. config missing regardless).
    with tempfile.TemporaryDirectory() as tmp_root:
        cwd = _hook_config_cwd(tmp_root)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --fill"},
            "tool_response": {"stdout": "done", "stderr": "", "exitCode": 0},
            "cwd": cwd,
        }
        result = _run_hook(payload)
        assert result.returncode == 2, (
            f"expected exit 2 (no GitHub URL found advisory), got {result.returncode}: "
            f"stderr={result.stderr!r}"
        )
        assert "no GitHub URL found" in result.stderr, f"got {result.stderr!r}"
    return "genuine top-level create + exitCode==0, no URL in output -> exit 2 advisory (detection fired)"


# ---------------------------------------------------------------------------
# dev-env#527 (ADR-076): live-fetch of single_select field options, with
# graceful fallback to the cached hook-config.json value on failure.
# ---------------------------------------------------------------------------


def test_parse_live_options_valid() -> str:
    raw = '{"data":{"node":{"options":[{"id":"08de2558","name":"High"},{"id":"6320e8a6","name":"Medium"}]}}}'
    assert _parse_live_options(raw) == {"High": "08de2558", "Medium": "6320e8a6"}
    return "well-formed gh api graphql response -> {name: id}"


def test_parse_live_options_empty_options_is_empty_dict_not_none() -> str:
    # A field with zero options (all deleted) is a real, distinct state from
    # a failed fetch -- must not collapse to the same None the caller uses
    # to mean "fetch failed, fall back to cache".
    raw = '{"data":{"node":{"options":[]}}}'
    assert _parse_live_options(raw) == {}
    return "field with zero live options -> {} (not None -- distinct from a failed fetch)"


def test_parse_live_options_wrong_node_type_is_none() -> str:
    # node(id: $id) resolving to a non-ProjectV2SingleSelectField (or a
    # deleted/inaccessible node) makes the inline fragment yield node: null.
    raw = '{"data":{"node":null}}'
    assert _parse_live_options(raw) is None
    return "node null (wrong type / deleted / inaccessible) -> None"


def test_parse_live_options_malformed_json_is_none() -> str:
    assert _parse_live_options("not json") is None
    assert _parse_live_options("") is None
    return "non-JSON / empty response -> None"


def test_parse_live_options_missing_keys_is_none() -> str:
    assert _parse_live_options('{"data":{}}') is None
    assert _parse_live_options('{}') is None
    return "well-formed JSON missing the expected data/node/options path -> None"


def test_resolve_required_fields_passthrough() -> str:
    config = {"required_fields": [{"name": "Impact", "field_id": "F1", "type": "single_select", "options": {}}]}
    assert _resolve_required_fields(config) == config["required_fields"]
    return "required_fields present -> returned unchanged"


def test_resolve_required_fields_legacy_epic_fallback() -> str:
    config = {
        "epic_field_id": "F_EPIC",
        "epic_options": {"Backend": "opt1"},
        "milestones": ["v0.1"],
    }
    resolved = _resolve_required_fields(config)
    assert resolved == [
        {"name": "Epic", "field_id": "F_EPIC", "type": "single_select", "options": {"Backend": "opt1"}},
        {"name": "Milestone", "type": "milestone", "options_list": ["v0.1"]},
    ], resolved
    return "legacy epic_field_id/milestones config -> normalized required_fields (ADR-023 fallback)"


def test_resolve_required_fields_empty_config_is_empty_list() -> str:
    assert _resolve_required_fields({}) == []
    return "config with none of the known keys -> []"


def test_fetch_live_required_field_options_filters_and_keys_by_field_id() -> str:
    required_fields = [
        {"name": "Impact", "field_id": "F1", "type": "single_select"},
        {"name": "Why", "field_id": "F2", "type": "text"},
        {"name": "Milestone", "type": "milestone"},
        {"name": "NoId", "type": "single_select"},  # no field_id -- must be skipped
    ]
    calls = []

    def fake_fetch(field_id):
        calls.append(field_id)
        return {"fake": field_id}

    result = fetch_live_required_field_options(required_fields, fetch_fn=fake_fetch)
    assert calls == ["F1"], f"expected only the single_select field with a field_id queried, got {calls!r}"
    assert result == {"F1": {"fake": "F1"}}, result
    return "only single_select fields with a field_id are queried; text/milestone/no-id fields are skipped"


def test_fetch_live_required_field_options_records_failure_as_none() -> str:
    result = fetch_live_required_field_options(
        [{"name": "Impact", "field_id": "F1", "type": "single_select"}],
        fetch_fn=lambda field_id: None,
    )
    assert result == {"F1": None}, result
    return "a failed live fetch is recorded as None, not omitted"


_BASE_CONFIG = {
    "project_node_id": "PVT_1",
    "required_fields": [
        {"name": "Epic", "field_id": "F_EPIC", "type": "single_select", "options": {"Backend": "cached-id"}},
    ],
}


def test_format_reminder_no_live_options_matches_original_behavior() -> str:
    # Default call (no live_options) must render identically to the
    # pre-dev-env#527 output: cached options, no freshness label.
    out = format_reminder("Issue", "https://github.com/x/y/issues/1", "ITEM1", _BASE_CONFIG)
    assert "Epic options:" in out, out
    assert "Backend: cached-id" in out, out
    assert "(live)" not in out and "cached — live fetch failed" not in out
    return "no live_options arg -> byte-identical to pre-live-fetch rendering (cached, unlabeled)"


def test_format_reminder_uses_live_options_when_available() -> str:
    out = format_reminder(
        "Issue", "https://github.com/x/y/issues/1", "ITEM1", _BASE_CONFIG,
        live_options={"F_EPIC": {"Backend": "fresh-live-id"}},
    )
    assert "Epic options (live):" in out, out
    assert "Backend: fresh-live-id" in out, out
    assert "cached-id" not in out, out
    return "successful live fetch -> live data used and labeled '(live)', cached value not shown"


def test_format_reminder_falls_back_to_cached_on_live_failure() -> str:
    out = format_reminder(
        "Issue", "https://github.com/x/y/issues/1", "ITEM1", _BASE_CONFIG,
        live_options={"F_EPIC": None},
    )
    assert "Epic options (cached — live fetch failed; may be stale):" in out, out
    assert "Backend: cached-id" in out, out
    return "failed live fetch -> cached data used, labeled so staleness is visible (the dev-env#527 fix)"


def test_format_reminder_required_fields_param_used_directly_over_config() -> str:
    # main() resolves required_fields once and threads it into both the
    # live-fetch call and format_reminder, so the two never diverge
    # (dev-env#527 review). Prove the wiring is live, not dead code: pass a
    # required_fields list that names a DIFFERENT field than _BASE_CONFIG
    # would independently resolve, and confirm the passed-in list wins.
    override_fields = [
        {"name": "Override", "field_id": "F_OTHER", "type": "single_select", "options": {"X": "override-id"}},
    ]
    out = format_reminder(
        "Issue", "https://github.com/x/y/issues/1", "ITEM1", _BASE_CONFIG,
        required_fields=override_fields,
    )
    assert "Set Override:" in out, out
    assert "X: override-id" in out, out
    assert "Epic" not in out, out
    return "required_fields param, when provided, is rendered directly instead of re-deriving from config"


def test_format_reminder_field_not_in_live_options_uses_cached_unlabeled() -> str:
    # live_options was attempted this run but doesn't mention this field_id
    # (e.g. a text/milestone field, or a field skipped for having no
    # field_id) -- must render like the "not attempted at all" case, not
    # like a failure.
    out = format_reminder(
        "Issue", "https://github.com/x/y/issues/1", "ITEM1", _BASE_CONFIG,
        live_options={},
    )
    assert "Epic options:" in out, out
    assert "(live)" not in out and "cached — live fetch failed" not in out
    return "field_id absent from live_options -> cached data, unlabeled (not mistaken for a failure)"


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
        ("pr-create: bare match", test_pr_create_simple_matches),
        ("issue-create: bare match", test_issue_create_simple_matches),
        ("pr-create: cd prefix match", test_pr_create_with_cd_prefix_matches),
        ("pr-create: chained with merge still matches", test_pr_create_chained_with_merge_still_matches),
        ("pr-create not matched as issue-create", test_pr_create_not_matched_as_issue_create),
        ("issue-create not matched as pr-create", test_issue_create_not_matched_as_pr_create),
        ("pr-create in heredoc commit body -> no match (dev-env#499)", test_pr_create_in_heredoc_commit_body_not_matched),
        ("issue-create in heredoc commit body -> no match (dev-env#499)", test_issue_create_in_heredoc_commit_body_not_matched),
        ("pr-create in quoted commit message -> no match (dev-env#499)", test_pr_create_in_quoted_commit_message_not_matched),
        ("issue-create in quoted commit message -> no match", test_issue_create_in_quoted_commit_message_not_matched),
        ("grep pattern argument -> neither fires (dev-env#499)", test_grep_pattern_argument_not_matched_for_either),
        ("pr-create in --text field value -> no match (dev-env#499)", test_pr_create_in_project_item_edit_text_not_matched),
        ("issue-create in --text field value -> no match", test_issue_create_in_project_item_edit_text_not_matched),
        ("pr-create in PR comment body -> no match (dev-env#499)", test_pr_create_in_pr_comment_body_not_matched),
        ("pr-create in $() subshell -> no match", test_pr_create_in_subshell_not_matched),
        ("issue-create in $() subshell -> no match", test_issue_create_in_subshell_not_matched),
        ("pr-create in double quotes -> no match", test_pr_create_in_double_quotes_not_matched),
        ("main(): exitCode!=0 short-circuits even when create detected", test_main_exit_code_nonzero_short_circuits_even_when_create_detected),
        ("main(): exitCode==0 proceeds to no-URL advisory", test_main_exit_code_zero_proceeds_past_gate_to_no_url_advisory),
        ("parse live options: valid response", test_parse_live_options_valid),
        ("parse live options: empty options -> {} not None", test_parse_live_options_empty_options_is_empty_dict_not_none),
        ("parse live options: node null -> None", test_parse_live_options_wrong_node_type_is_none),
        ("parse live options: malformed JSON -> None", test_parse_live_options_malformed_json_is_none),
        ("parse live options: missing keys -> None", test_parse_live_options_missing_keys_is_none),
        ("resolve required fields: passthrough", test_resolve_required_fields_passthrough),
        ("resolve required fields: legacy epic/milestone fallback", test_resolve_required_fields_legacy_epic_fallback),
        ("resolve required fields: empty config -> []", test_resolve_required_fields_empty_config_is_empty_list),
        ("fetch live required fields: filters to single_select with field_id", test_fetch_live_required_field_options_filters_and_keys_by_field_id),
        ("fetch live required fields: failure recorded as None", test_fetch_live_required_field_options_records_failure_as_none),
        ("format_reminder: no live_options -> original behavior", test_format_reminder_no_live_options_matches_original_behavior),
        ("format_reminder: live options used and labeled", test_format_reminder_uses_live_options_when_available),
        ("format_reminder: failed live fetch falls back to cached, labeled", test_format_reminder_falls_back_to_cached_on_live_failure),
        ("format_reminder: required_fields param used directly over config", test_format_reminder_required_fields_param_used_directly_over_config),
        ("format_reminder: field absent from live_options -> cached, unlabeled", test_format_reminder_field_not_in_live_options_uses_cached_unlabeled),
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
