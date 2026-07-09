#!/usr/bin/env python3
"""Tests for pre-tool-use-journal-compose-force-guard.py (dev-env#631, ADR-096).

Two layers, mirroring this hook family's established split
(test_canonical_mutate_guard.py, test_pre_merge_findings_gate.py):

Layer 1 -- pure command-classification tests, offline, no subprocess: pins
`segment_targets_today_compose` / `command_targets_today_compose` against
every real journal-compose SKILL.md command shape (worktree add, -C-scoped
commit/push, the compose/YYYY-MM-DD recovery branch) and the key regression
this hook exists to avoid: a commit message merely MENTIONING
"draft/<today>" or "compose-<today>" as prose (this repo's own commits
legitimately discuss this pattern) must never false-trigger.

Layer 2 -- behavioral subprocess tests driving the real hook over stdin
(mirroring test_canonical_mutate_guard.py's `_run_hook` pattern), with
JOURNAL_COMPOSE_FORCE_MARKER_DIR redirected at a disposable temp dir so no
run ever touches the real scratch directory or depends on which day it
executes (today-dated commands are built from `datetime.date.today()` at
test-run time).
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-tool-use-journal-compose-force-guard.py")
_SCRIPTS_DIR = os.path.dirname(_SCRIPT)
sys.path.insert(0, _SCRIPTS_DIR)
spec = importlib.util.spec_from_file_location("pre_tool_use_journal_compose_force_guard", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

segment_targets_today_compose = mod.segment_targets_today_compose
command_targets_today_compose = mod.command_targets_today_compose

import _journal_compose_force as jcf  # noqa: E402

MODULE_PATH = Path(_SCRIPT)

TODAY = "2026-07-09"
YESTERDAY = "2026-07-08"


# ---------------------------------------------------------------------------
# Layer 1: pure command-classification tests
# ---------------------------------------------------------------------------

def test_worktree_add_via_C_value_matches():
    cmd = (
        'git -C C:/Users/brown/Git/engineering-journal worktree add --detach '
        'C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 '
        'refs/remotes/origin/draft/2026-07-09'
    )
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_worktree_add_ref_only_matches():
    cmd = 'git worktree add --detach /tmp/wt refs/remotes/origin/draft/2026-07-09'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_worktree_add_non_today_date_no_match():
    cmd = (
        'git -C C:/Users/brown/Git/engineering-journal worktree add --detach '
        'C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-06-01 '
        'refs/remotes/origin/draft/2026-06-01'
    )
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_via_C_value_matches():
    cmd = (
        'git -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 '
        'commit -m "[docs] Add 2026-07-09 journal: some-slug"'
    )
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_commit_message_mentioning_draft_today_as_prose_no_match():
    # This repo's own commits legitimately discuss "draft/YYYY-MM-DD" as
    # prose -- the exact false-positive class this hook must not trigger on.
    cmd = 'git commit -m "Documented the draft/2026-07-09 today-guard fix"'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_message_mentioning_compose_today_as_prose_no_match():
    cmd = 'git commit -m "Explains the compose-2026-07-09 worktree naming"'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_long_message_flag_value_excluded():
    cmd = 'git commit --message "mentions draft/2026-07-09 in prose"'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_message_eq_form_excluded():
    cmd = 'git commit --message=draft/2026-07-09-in-prose'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_push_refspec_matches():
    cmd = (
        'git -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 '
        'push origin HEAD:refs/heads/draft/2026-07-09'
    )
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_push_recovery_branch_slash_form_matches_via_C():
    # Step 10.5's recovery push: compose/YYYY-MM-DD (slash) as the positional
    # ref, but -C still carries the hyphenated compose-YYYY-MM-DD worktree path.
    cmd = (
        'git -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 '
        'push -u origin compose/2026-07-09'
    )
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_push_no_date_reference_no_match():
    # -C's value here (the bare $EJ path) carries no compose-/draft- dated
    # substring at all -- unlike the worktree path used elsewhere in this
    # suite, which legitimately does and must match (see
    # test_push_refspec_matches).
    cmd = 'git -C C:/Users/brown/Git/engineering-journal push'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_readonly_status_not_gated():
    cmd = 'git -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 status'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_readonly_diff_not_gated():
    cmd = 'git -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 diff'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_branch_delete_not_gated():
    # Step 11 cleanup -- explicitly out of scope per the issue ("worktree-
    # creation/commit/push commands"), unlike worktree remove/prune.
    cmd = 'git branch -D compose/2026-07-09'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_worktree_remove_matches_loosely():
    # Harmless over-inclusion by design: by the time cleanup runs, the
    # marker (if force=true) already exists from Step 0.6.
    cmd = 'git -C C:/Users/brown/Git/engineering-journal worktree remove --force C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_heredoc_body_mention_not_matched():
    cmd = 'git commit -F - <<\'EOF\'\nmentions compose-2026-07-09 in a body\nEOF\n'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_recovery_branch_suffix_extracts_base_date():
    cmd = 'git worktree add --detach /tmp/wt refs/remotes/origin/draft/2026-07-09-recovery'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_non_git_command_no_match():
    cmd = 'npm install'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_env_prefixed_git_command_still_classified():
    cmd = 'FOO=bar git -C /path/compose-2026-07-09 commit -m "msg"'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_git_dir_eq_form_matches():
    cmd = 'git --git-dir=C:/repo/compose-2026-07-09/.git push origin draft/2026-07-09'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_command_targets_today_compose_chained_second_segment():
    cmd = 'git fetch origin && git -C /path/compose-2026-07-09 commit -m "msg"'
    assert command_targets_today_compose(cmd, TODAY) is True

def test_command_targets_today_compose_no_segment_matches():
    cmd = 'git fetch origin && git status'
    assert command_targets_today_compose(cmd, TODAY) is False


# ---------------------------------------------------------------------------
# Layer 1 regressions from PR #671 review: `-c <value>` verb-detection
# bypass, glued/combined message-flag false positives, and the performance
# pre-filter.
# ---------------------------------------------------------------------------

def test_dash_c_prefix_does_not_swallow_verb():
    # Review finding: `-c core.hooksPath=x` was treated as an unrecognized
    # flag, so its VALUE token was then mistaken for the verb -- a real
    # same-day compose commit silently escaped the gate entirely.
    cmd = (
        'git -c core.hooksPath=x -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-2026-07-09 '
        'commit -m "msg"'
    )
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_dash_c_with_gpgsign_value_does_not_swallow_verb():
    cmd = 'git -c commit.gpgsign=false commit -m "msg" -C /path/compose-2026-07-09'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_dash_c_as_last_token_does_not_crash():
    # No value follows -c at all -- must not raise, and correctly finds no verb.
    cmd = 'git -c'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_combined_short_flag_am_message_no_match():
    # git commit -am "..." -- the standard "-a -m combined" idiom. Review
    # finding: fell through to scan_tokens uninspected since neither the
    # exact-match nor the `--message=`-prefix check recognized `-am`.
    cmd = 'git commit -am "Documented the draft/2026-07-09 today-guard fix"'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_combined_short_flag_am_real_match_still_works():
    cmd = 'git -C /path/compose-2026-07-09 commit -am "unrelated message"'
    assert segment_targets_today_compose(cmd, TODAY) is True

def test_commit_glued_m_message_no_match():
    # git commit -m"..." (no space) -- glued short-option value.
    cmd = 'git commit -m"docs: explain draft/2026-07-09 guard"'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_glued_capital_f_message_no_match():
    cmd = 'git commit -Fdraft/2026-07-09.txt'
    assert segment_targets_today_compose(cmd, TODAY) is False

def test_commit_glued_ma_form_is_message_value_a():
    # git commit -ma -- per git/getopt semantics, once 'm' appears in a
    # short cluster everything after it (glued or not) is its value, so
    # this means message="a", NOT "-a -m combined". No date in the glued
    # value, so no match either way -- this pins the parse doesn't crash
    # or misclassify the verb.
    cmd = 'git -C /path/compose-2026-07-09 commit -ma'
    assert segment_targets_today_compose(cmd, TODAY) is True  # -C value still carries the date

def test_command_targets_today_compose_short_circuits_when_date_absent():
    # Performance pre-filter: a command with no literal today-date substring
    # anywhere must return False without needing a real git/date match --
    # exercised here via a long, unrelated command that would be expensive
    # to fully parse if the pre-filter were missing.
    cmd = 'git -C /path/to/some/repo commit -m "a totally unrelated message with no date at all"'
    assert command_targets_today_compose(cmd, TODAY) is False


# ---------------------------------------------------------------------------
# Layer 2: end-to-end subprocess tests
# ---------------------------------------------------------------------------

def _run_hook(payload, marker_dir=None):
    env = dict(os.environ)
    if marker_dir is not None:
        env[jcf.MARKER_DIR_ENV] = marker_dir
    if isinstance(payload, str):
        stdin_text = payload
    else:
        stdin_text = json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _today_worktree_add_command(today):
    return (
        f'git -C C:/Users/brown/Git/engineering-journal worktree add --detach '
        f'C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-{today} '
        f'refs/remotes/origin/draft/{today}'
    )


def test_e2e_no_marker_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        today = datetime.date.today().isoformat()
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(today)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 2
        assert proc.stdout == ""
        reason = json.loads(proc.stderr)["reason"]
        assert "BLOCKED" in reason
        assert today in reason

def test_e2e_fresh_true_marker_allows():
    with tempfile.TemporaryDirectory() as tmp:
        today_dt = datetime.datetime.now()
        today = today_dt.date().isoformat()
        marker = jcf.build_marker(True, "--force", today_dt)
        jcf.write_marker(os.path.join(tmp, f"journal-compose-force-{today}.json"), marker)
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(today)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 0
        assert proc.stderr == ""

def test_e2e_force_false_marker_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        today_dt = datetime.datetime.now()
        today = today_dt.date().isoformat()
        marker = jcf.build_marker(False, "", today_dt)
        jcf.write_marker(os.path.join(tmp, f"journal-compose-force-{today}.json"), marker)
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(today)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 2

def test_e2e_stale_true_marker_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        today_dt = datetime.datetime.now()
        today = today_dt.date().isoformat()
        stale_resolved_at = today_dt - datetime.timedelta(seconds=jcf.MAX_MARKER_AGE_SECONDS + 60)
        marker = jcf.build_marker(True, "--force", stale_resolved_at)
        jcf.write_marker(os.path.join(tmp, f"journal-compose-force-{today}.json"), marker)
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(today)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 2

def test_e2e_corrupt_marker_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        today = datetime.date.today().isoformat()
        with open(os.path.join(tmp, f"journal-compose-force-{today}.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(today)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 2

def test_e2e_tz_aware_marker_fails_closed_not_uncaught_crash():
    # Review finding on PR #671: a tz-aware `resolved_at` (a completely
    # ordinary ISO-8601 shape) previously raised an uncaught TypeError in
    # is_marker_fresh, exiting 1 (not 2) -- a non-blocking hook error that
    # let the command PROCEED, inverting the fail-closed guarantee. Must now
    # block cleanly like any other corrupt marker.
    with tempfile.TemporaryDirectory() as tmp:
        today = datetime.date.today().isoformat()
        with open(os.path.join(tmp, f"journal-compose-force-{today}.json"), "w", encoding="utf-8") as f:
            json.dump({"force": True, "raw_arguments": "--force", "resolved_at": f"{today}T00:00:00+00:00"}, f)
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(today)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "Traceback" not in proc.stderr

def test_e2e_dash_c_prefix_still_blocked_with_no_marker():
    # Review finding on PR #671: a `-c <value>` git-level flag previously
    # let the verb-detection logic mistake the config value for the verb,
    # bypassing the gate entirely (exit 0, no marker check performed at
    # all). Must now be gated exactly like the equivalent command without
    # `-c`.
    with tempfile.TemporaryDirectory() as tmp:
        today = datetime.date.today().isoformat()
        cmd = (
            f'git -c core.hooksPath=x -C C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-{today} '
            f'commit -m "msg"'
        )
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 2

def test_e2e_non_today_date_allows_regardless_of_marker():
    with tempfile.TemporaryDirectory() as tmp:
        # No marker written at all for YESTERDAY -- must still allow, since
        # this hook is entirely out of scope for a past-day compose.
        payload = {"tool_name": "Bash", "tool_input": {"command": _today_worktree_add_command(YESTERDAY)}, "cwd": "C:/x"}
        proc = _run_hook(payload, marker_dir=tmp)
        assert proc.returncode == 0

def test_e2e_non_bash_tool_allows():
    payload = {"tool_name": "Write", "tool_input": {"file_path": "x.md"}}
    proc = _run_hook(payload)
    assert proc.returncode == 0

def test_e2e_malformed_json_allows():
    proc = _run_hook("{not json")
    assert proc.returncode == 0

def test_e2e_empty_stdin_allows():
    proc = _run_hook("")
    assert proc.returncode == 0

def test_e2e_missing_command_allows():
    payload = {"tool_name": "Bash", "tool_input": {}}
    proc = _run_hook(payload)
    assert proc.returncode == 0

def test_e2e_non_dict_json_allows():
    proc = _run_hook("[1, 2, 3]")
    assert proc.returncode == 0


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
