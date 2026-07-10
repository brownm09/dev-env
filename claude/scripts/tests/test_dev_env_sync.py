#!/usr/bin/env python3
"""Unit tests for dev-env-sync.py's pure message-formatting helpers.

dev-env#694: a dirty working-tree file (`claude/skills/sources.md`, left uncommitted by an
earlier `/research` session) silently blocked `git pull --ff-only` for 36+ hours and 21+
commits of drift because the failure warning was routed to stderr on an always-exit-0
`UserPromptSubmit` hook — a channel Claude Code does not add to context for that hook type
(confirmed live during the investigation: the hook fired every prompt, the pull failed every
time, and nothing ever appeared in the model's context). The fix moves every advisory to
stdout and enriches the fast-forward-related messages with local/remote short SHAs and a
commit-behind count, so a future occurrence is self-diagnosing without a manual `git log`/
`git fetch` comparison. This test exercises the pure formatters offline (no subprocess, no
network, no git) — the git/subprocess orchestration is intentionally not tested, matching
this repo's established convention for topology-diagnosing orchestration scripts (`##
Testing` items 22/26/30; PR #661's own note that dev-env-sync.py has "zero local pure logic"
prior to this change).

Usage:
    py -3 claude/scripts/tests/test_dev_env_sync.py

Exit 0 = all pass.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "dev-env-sync.py"

# The script imports _winsubp and _worktree_topology (siblings in scripts/); make them resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("dev_env_sync", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
des = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(des)  # safe: main() is guarded by __main__

_plural = des._plural
_count_from = des._count_from
format_sync_note = des.format_sync_note
format_pull_failure_message = des.format_pull_failure_message
format_diverged_message = des.format_diverged_message
format_pulled_message = des.format_pulled_message

LOCAL = "33b0036049a9ad6747e1b0d88688ee4fb86420e0"
REMOTE = "d249ba461e1c64aae45a31297e232a756bcdd2fc"


def _proc(returncode: int, stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr="")


def test_plural_singular_and_plural() -> str:
    assert _plural(1) == "", "1 must take no plural suffix"
    assert _plural(0) == "s", "0 takes the plural suffix"
    assert _plural(2) == "s", "2 takes the plural suffix"
    return "_plural(1)=='' ; _plural(0)==_plural(2)=='s'"


def test_count_from_parses_valid_digit_output() -> str:
    assert _count_from(_proc(0, "21\n")) == 21, "a clean rev-list --count result must parse"
    return "returncode 0, stdout '21\\n' -> 21"


def test_count_from_fails_open_on_nonzero_returncode() -> str:
    assert _count_from(_proc(1, "21\n")) == 0, "a failed rev-list call must not be trusted"
    return "returncode 1 -> 0 (fail-open; this is an advisory diagnostic, not load-bearing)"


def test_count_from_fails_open_on_non_digit_output() -> str:
    assert _count_from(_proc(0, "")) == 0, "empty stdout must not raise or misparse"
    assert _count_from(_proc(0, "fatal: bad range\n")) == 0, "non-digit stdout must not raise"
    return "empty or non-digit stdout -> 0, no exception"


def test_format_sync_note_shortens_shas_and_pluralizes() -> str:
    note = format_sync_note(LOCAL, REMOTE, 21)
    assert LOCAL[:8] in note and REMOTE[:8] in note, "both short SHAs must appear"
    assert LOCAL not in note.replace(LOCAL[:8], ""), "the full (long) SHA must not appear"
    assert "21 commits behind" in note, "plural count must read '21 commits'"
    singular = format_sync_note(LOCAL, REMOTE, 1)
    assert "1 commit behind" in singular and "1 commits" not in singular, "1 must be singular"
    return note


def test_format_pull_failure_message_includes_conflict_and_note() -> str:
    git_stderr = (
        "error: Your local changes to the following files would be overwritten by merge:\n"
        "\tclaude/skills/sources.md\n"
        "Please commit your changes or stash them before you merge.\n"
        "Aborting\n"
    )
    msg = format_pull_failure_message(LOCAL, REMOTE, 21, git_stderr)
    assert msg.startswith("[dev-env-sync] WARNING: fast-forward pull failed"), "must lead with the tag+WARNING"
    assert "21 commits behind" in msg, "must carry the pre-pull behind-count"
    assert "claude/skills/sources.md" in msg, "must preserve git's own named-file diagnostic"
    assert not msg.endswith("\n"), "trailing whitespace from git stderr must be stripped"
    return msg.splitlines()[0]


def test_format_diverged_message_shows_both_directions() -> str:
    msg = format_diverged_message(LOCAL, REMOTE, behind=3, ahead=2)
    assert "2 commits ahead" in msg and "3 commits behind" in msg, "must show both ahead and behind counts"
    assert LOCAL[:8] in msg and REMOTE[:8] in msg, "both short SHAs must appear"
    return msg.splitlines()[0]


def test_format_pulled_message_matching_counts_has_no_mismatch_note() -> str:
    lines = ["abc1234 fix: something", "def5678 feat: something else"]
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=2, pulled_lines=lines)
    assert "Pulled 2 commits" in msg, "count must reflect the pulled lines"
    assert "concurrent process" not in msg, "matching counts must not print the race-note"
    for line in lines:
        assert line in msg, f"each pulled commit line must be shown: {line}"
    return msg


def test_format_pulled_message_singular_commit() -> str:
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=1, pulled_lines=["abc1234 fix: x"])
    assert "Pulled 1 commit " in msg or msg.count("Pulled 1 commit\n") or "Pulled 1 commit -" in msg, msg
    assert "Pulled 1 commits" not in msg, "1 must be singular"
    return msg.splitlines()[0]


def test_format_pulled_message_truncates_with_trailer() -> str:
    lines = [f"sha{i:04d} commit {i}" for i in range(8)]
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=8, pulled_lines=lines)
    assert "sha0000" in msg and "sha0004" in msg, "first 5 lines must be shown"
    assert "sha0005" not in msg, "6th+ line must not be individually shown"
    assert "... and 3 more" in msg, "trailer must report the remaining count"
    return "8 pulled lines -> 5 shown + '... and 3 more'"


def test_format_pulled_message_mismatch_surfaces_race_note() -> str:
    # dev-env#694's central ambiguity: behind_count (measured pre-pull) disagreeing with the
    # actual post-pull log range means a concurrent process moved the ref mid-pull. A future
    # occurrence of the original "Pulled 0 commits" mystery must now explain itself here.
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=5, pulled_lines=[])
    assert "Pulled 0 commits" in msg, "an empty post-pull range still reports the pulled count honestly"
    assert "measured 5 commits behind before pulling" in msg, "the pre-pull measurement must be surfaced"
    assert "concurrent process likely moved origin/main" in msg, "must name the likely cause"
    return msg


def main() -> int:
    tests = [
        ("_plural singular and plural", test_plural_singular_and_plural),
        ("_count_from parses valid digit output", test_count_from_parses_valid_digit_output),
        ("_count_from fails open on nonzero returncode", test_count_from_fails_open_on_nonzero_returncode),
        ("_count_from fails open on non-digit output", test_count_from_fails_open_on_non_digit_output),
        ("format_sync_note shortens SHAs and pluralizes", test_format_sync_note_shortens_shas_and_pluralizes),
        ("format_pull_failure_message includes conflict + note", test_format_pull_failure_message_includes_conflict_and_note),
        ("format_diverged_message shows both directions", test_format_diverged_message_shows_both_directions),
        ("format_pulled_message: matching counts, no mismatch note", test_format_pulled_message_matching_counts_has_no_mismatch_note),
        ("format_pulled_message: singular commit", test_format_pulled_message_singular_commit),
        ("format_pulled_message: truncates with trailer", test_format_pulled_message_truncates_with_trailer),
        ("format_pulled_message: mismatch surfaces race note", test_format_pulled_message_mismatch_surfaces_race_note),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            for line in str(detail).splitlines():
                print(f"      {line}")
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
