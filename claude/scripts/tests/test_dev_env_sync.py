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

dev-env#797 (ADR-110): a fast-forward-pull failure that persists across prompts/sessions (a
dirty tracked file conflicting with an incoming commit — the #697/#795 recurrences) now
escalates to a distinct, louder advisory instead of the same-severity per-prompt warning.
This file additionally exercises the pure escalation helpers offline (`parse_blocking_files`,
`format_duration`, `record_failure`, `should_escalate`, `format_escalated_pull_failure_message`)
and the best-effort scratch-state I/O (`read/write/clear_failure_state`) against a
`tempfile.TemporaryDirectory()` (its only impure surface is the filesystem, matching
`test_hookutil.py`'s convention — no real `~/.claude/scratch`). `main()`'s orchestration (the
track-on-failure / clear-on-success wiring) is not covered here, consistent with the same
pure-helper convention above.

Usage:
    py -3 claude/scripts/tests/test_dev_env_sync.py

Exit 0 = all pass.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
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

import _hookutil  # noqa: E402  -- resolvable only after the sys.path.insert above

_plural = des._plural
_count_from = des._count_from
format_sync_note = des.format_sync_note
format_pull_failure_message = des.format_pull_failure_message
format_diverged_message = des.format_diverged_message
format_pulled_message = des.format_pulled_message

# Persistent ff-failure escalation helpers (dev-env#797).
parse_blocking_files = des.parse_blocking_files
format_duration = des.format_duration
record_failure = des.record_failure
should_escalate = des.should_escalate
format_escalated_pull_failure_message = des.format_escalated_pull_failure_message
build_failure_response = des.build_failure_response
failure_state_path = des.failure_state_path
read_failure_state = des.read_failure_state
write_failure_state = des.write_failure_state
clear_failure_state = des.clear_failure_state
FAILURE_STATE_PREFIX = des.FAILURE_STATE_PREFIX
ESCALATE_AFTER_CONSECUTIVE_FAILURES = des.ESCALATE_AFTER_CONSECUTIVE_FAILURES
ESCALATE_AFTER_HOURS = des.ESCALATE_AFTER_HOURS

LOCAL = "33b0036049a9ad6747e1b0d88688ee4fb86420e0"
REMOTE = "d249ba461e1c64aae45a31297e232a756bcdd2fc"

# git's two `--ff-only` abort shapes (a dirty tracked file, and an untracked-file conflict).
_LOCAL_CHANGES_STDERR = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    "\tclaude/skills/sources.md\n"
    "Please commit your changes or stash them before you merge.\n"
    "Aborting\n"
)
_UNTRACKED_STDERR = (
    "error: The following untracked working tree files would be overwritten by merge:\n"
    "\tbash.exe.stackdump\n"
    "Please move or remove them before you merge.\n"
    "Aborting\n"
)


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
    assert "has diverged" in msg, "a true fork (both ahead and behind > 0) must say 'diverged'"
    assert LOCAL[:8] in msg and REMOTE[:8] in msg, "both short SHAs must appear"
    return msg.splitlines()[0]


def test_format_diverged_message_ahead_only_is_not_labeled_diverged() -> str:
    # Review finding, PR #701: behind == 0 means local is merely ahead (e.g. a commit landed
    # directly on the canonical) -- not a true fork. Calling it "diverged" would be internally
    # contradictory ("0 commits behind ... has diverged").
    msg = format_diverged_message(LOCAL, REMOTE, behind=0, ahead=4)
    assert "is ahead of origin/main" in msg, "ahead-only state must use non-contradictory wording"
    assert "has diverged" not in msg, "must not claim divergence when behind == 0"
    assert "4 commits" in msg, "ahead count must still be shown"
    assert LOCAL[:8] in msg and REMOTE[:8] in msg, "both short SHAs must appear"
    return msg.splitlines()[0]


def test_format_pulled_message_matching_counts_has_no_mismatch_note() -> str:
    lines = ["abc1234 fix: something", "def5678 feat: something else"]
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=2, pulled_lines=lines)
    assert "Pulled 2 commits" in msg, "count must reflect the pulled lines"
    assert LOCAL[:8] in msg and REMOTE[:8] in msg, "both short SHAs must appear (review finding, PR #701)"
    assert "concurrent process" not in msg, "matching counts must not print the race-note"
    for line in lines:
        assert line in msg, f"each pulled commit line must be shown: {line}"
    return msg


def test_format_pulled_message_singular_commit() -> str:
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=1, pulled_lines=["abc1234 fix: x"])
    assert "Pulled 1 commit " in msg or msg.count("Pulled 1 commit\n") or "Pulled 1 commit -" in msg, msg
    assert "Pulled 1 commits" not in msg, "1 must be singular"
    assert LOCAL[:8] in msg and REMOTE[:8] in msg, "both short SHAs must appear"
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
    # behind_count=5 (nonzero) is a REAL measurement, distinct from the fail-open-to-0 sentinel
    # covered by test_format_pulled_message_zero_behind_count_reports_unmeasured below.
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=5, pulled_lines=[])
    assert "Pulled 0 commits" in msg, "an empty post-pull range still reports the pulled count honestly"
    assert "measured 5 commits behind before pulling" in msg, "the pre-pull measurement must be surfaced"
    assert "concurrent process likely moved origin/main" in msg, "must name the likely cause"
    return msg


def test_format_pulled_message_zero_behind_count_reports_unmeasured() -> str:
    # Review finding, PR #701: at the call site, base == local and local != remote are already
    # established, so a REAL behind_count is always >= 1 -- behind_count == 0 can only mean the
    # pre-pull rev-list measurement itself failed (_count_from's fail-open sentinel). Even though
    # commits were genuinely pulled, this must NOT be misattributed to a concurrent process.
    msg = format_pulled_message(LOCAL, REMOTE, behind_count=0, pulled_lines=["abc1234 fix: x"])
    assert "Pulled 1 commit" in msg, "the pulled count must still be reported honestly"
    assert "could not be measured" in msg, "a zero behind_count must be labeled as unmeasured"
    assert "concurrent process" not in msg, "a measurement failure must not be misattributed as a race"
    return msg


def test_all_formatters_are_cp1252_encodable() -> str:
    # This PR's entire premise is fixing a cp1252 failure (the previous "warning sign" emoji,
    # silently discarded on stderr regardless of encoding). Pin that every formatter's output
    # survives Claude Code's cp1252 hook-output pipe on Windows, so a future non-cp1252 character
    # (emoji, smart quote, arrow) can't silently reintroduce this exact bug class -- review
    # finding, PR #701. The em dash (u2014) is non-ASCII but IS cp1252-safe (0x97), so this
    # asserts .encode("cp1252") succeeds rather than the stricter .isascii() some sibling tests
    # use (which would incorrectly fail on the em dash).
    messages = [
        format_sync_note(LOCAL, REMOTE, 21),
        format_pull_failure_message(LOCAL, REMOTE, 21, "error: conflict\nAborting"),
        format_diverged_message(LOCAL, REMOTE, behind=3, ahead=2),
        format_diverged_message(LOCAL, REMOTE, behind=0, ahead=4),
        format_pulled_message(LOCAL, REMOTE, behind_count=2, pulled_lines=["abc1234 fix: x"]),
        format_pulled_message(LOCAL, REMOTE, behind_count=0, pulled_lines=["abc1234 fix: x"]),
    ]
    for msg in messages:
        msg.encode("cp1252")  # raises UnicodeEncodeError if not cp1252-safe
    return f"{len(messages)} formatter outputs all cp1252-encodable"


# --- persistent ff-failure escalation (dev-env#797) ------------------------------


def test_parse_blocking_files_local_changes_variant() -> str:
    files = parse_blocking_files(_LOCAL_CHANGES_STDERR)
    assert files == ["claude/skills/sources.md"], files
    return "local-changes abort -> ['claude/skills/sources.md']"


def test_parse_blocking_files_untracked_variant() -> str:
    files = parse_blocking_files(_UNTRACKED_STDERR)
    assert files == ["bash.exe.stackdump"], files
    return "untracked-file abort -> ['bash.exe.stackdump']"


def test_parse_blocking_files_multiple_files() -> str:
    stderr = (
        "error: Your local changes to the following files would be overwritten by merge:\n"
        "\tclaude/skills/sources.md\n"
        "\tclaude/skills/journal-compose/SKILL.md\n"
        "Please commit your changes or stash them before you merge.\n"
        "Aborting\n"
    )
    files = parse_blocking_files(stderr)
    assert files == [
        "claude/skills/sources.md",
        "claude/skills/journal-compose/SKILL.md",
    ], files
    return "two tab-indented files both captured, in order"


def test_parse_blocking_files_no_tab_lines_returns_empty() -> str:
    # A different failure mode (no tab-indented file list) must not raise or invent paths.
    assert parse_blocking_files("fatal: some other git error\n") == []
    assert parse_blocking_files("") == []
    return "no tab-indented lines (or empty) -> []"


def test_format_duration_boundaries() -> str:
    assert format_duration(0) == "under a minute", "0s"
    assert format_duration(59) == "under a minute", "59s still under a minute"
    assert format_duration(60) == "1m", "exactly 60s -> 1m"
    assert format_duration(45 * 60) == "45m", "45m"
    assert format_duration(2 * 3600) == "2h", "exactly 2h, no trailing minutes"
    assert format_duration(2 * 3600 + 15 * 60) == "2h 15m", "2h 15m"
    assert format_duration(-500) == "under a minute", "negative clamps to 0 (no negative duration)"
    return "under-a-minute / Xm / Xh / Xh Ym / negative-clamped all correct"


def test_record_failure_from_none_starts_fresh() -> str:
    state = record_failure(None, now=1000.0)
    assert state["first_failure_at"] == 1000.0, state
    assert state["consecutive_count"] == 1, state
    assert state["last_failure_at"] == 1000.0, state
    return "prev=None -> count 1, first==last==now"


def test_record_failure_increments_and_preserves_first() -> str:
    prev = {"first_failure_at": 1000.0, "consecutive_count": 2, "last_failure_at": 1500.0}
    state = record_failure(prev, now=2000.0)
    assert state["first_failure_at"] == 1000.0, "original start time preserved"
    assert state["consecutive_count"] == 3, "count incremented"
    assert state["last_failure_at"] == 2000.0, "last advances to now"
    return "ongoing run: first preserved, count+1, last=now"


def test_record_failure_malformed_prev_starts_fresh() -> str:
    # Missing/invalid first_failure_at -> no trustworthy start time -> fresh run (count reset).
    for prev in ({"consecutive_count": 9}, {"first_failure_at": "oops", "consecutive_count": 9}, {}, {"first_failure_at": True}):
        state = record_failure(prev, now=3000.0)
        assert state["first_failure_at"] == 3000.0, (prev, state)
        assert state["consecutive_count"] == 1, (prev, state)
    return "malformed/missing first_failure_at (incl. bool) -> fresh run count 1"


def test_record_failure_valid_first_corrupt_count() -> str:
    # A trustworthy timestamp but a corrupt count: keep the timestamp (time arm still works),
    # conservatively restart the count.
    state = record_failure({"first_failure_at": 500.0, "consecutive_count": "x"}, now=900.0)
    assert state["first_failure_at"] == 500.0, "valid timestamp preserved"
    assert state["consecutive_count"] == 1, "corrupt count restarts at 1"
    return "valid first + corrupt count -> first kept, count restarts at 1"


def test_should_escalate_below_both_thresholds_false() -> str:
    state = {"first_failure_at": 1000.0, "consecutive_count": 1, "last_failure_at": 1000.0}
    assert should_escalate(state, now=1000.0) is False, "1 failure, 0s elapsed must not escalate"
    return "count 1 + 0s elapsed -> no escalation"


def test_should_escalate_count_boundary() -> str:
    # Isolate the count arm: first_failure_at == now so the time arm contributes 0s.
    at_thresh = {"first_failure_at": 1000.0, "consecutive_count": ESCALATE_AFTER_CONSECUTIVE_FAILURES}
    below = {"first_failure_at": 1000.0, "consecutive_count": ESCALATE_AFTER_CONSECUTIVE_FAILURES - 1}
    assert should_escalate(at_thresh, now=1000.0) is True, "count == threshold escalates"
    assert should_escalate(below, now=1000.0) is False, "count == threshold-1 does not"
    return f"count boundary at {ESCALATE_AFTER_CONSECUTIVE_FAILURES}: == True, -1 False"


def test_should_escalate_time_boundary() -> str:
    # Isolate the time arm: count 1 (below the count threshold) so only elapsed time can fire.
    secs = ESCALATE_AFTER_HOURS * 3600
    at_thresh = {"first_failure_at": 1000.0, "consecutive_count": 1}
    below = {"first_failure_at": 1000.0, "consecutive_count": 1}
    assert should_escalate(at_thresh, now=1000.0 + secs) is True, "elapsed == threshold escalates"
    assert should_escalate(below, now=1000.0 + secs - 1) is False, "just under threshold does not"
    return f"time boundary at {ESCALATE_AFTER_HOURS}h: == True, -1s False"


def test_should_escalate_time_arm_independent_of_count() -> str:
    # The robustness property: a long-persisting failure escalates on time even if the count
    # was lost to a concurrent-write race (count stuck at 1).
    state = {"first_failure_at": 0.0, "consecutive_count": 1}
    assert should_escalate(state, now=ESCALATE_AFTER_HOURS * 3600 + 1) is True
    return "count 1 but > threshold hours elapsed -> escalates (time arm robust to lost count)"


def test_format_escalated_message_contains_key_facts() -> str:
    msg = format_escalated_pull_failure_message(
        LOCAL, REMOTE, behind=21, blocking_files=["claude/skills/sources.md"],
        consecutive_count=7, seconds_failing=41 * 3600, git_stderr=_LOCAL_CHANGES_STDERR,
    )
    assert "PERSISTENT FAILURE" in msg, "must lead with the distinct escalation tag"
    assert "7 consecutive failing pulls" in msg, "must name the consecutive failing-pull count"
    assert "41h" in msg, "must name how long it has been failing"
    assert "21 commit" in msg, "must name the commits-behind count"
    assert LOCAL[:8] in msg and REMOTE[:8] in msg, "must name local/remote short SHAs"
    assert "claude/skills/sources.md" in msg, "must name the blocking file path"
    assert "STALE" in msg, "must state the stale-tooling blast radius"
    assert "Aborting" in msg, "must still echo git's own diagnostic"
    return msg.splitlines()[0]


def test_format_escalated_message_singular_prompt() -> str:
    msg = format_escalated_pull_failure_message(
        LOCAL, REMOTE, behind=1, blocking_files=["a.txt"],
        consecutive_count=1, seconds_failing=7200, git_stderr="",
    )
    assert "1 consecutive failing pull " in msg and "1 consecutive failing pulls" not in msg, "must be singular"
    assert "1 commit behind" in msg and "1 commits" not in msg, "commit must be singular"
    return "consecutive_count=1 and behind=1 both render singular"


def test_format_escalated_message_no_blocking_files_degrades() -> str:
    msg = format_escalated_pull_failure_message(
        LOCAL, REMOTE, behind=3, blocking_files=[],
        consecutive_count=4, seconds_failing=3 * 3600, git_stderr="fatal: weird error\n",
    )
    assert "none named by git" in msg, "empty blocking list must degrade gracefully, not blank"
    assert "fatal: weird error" in msg, "git's diagnostic must still be echoed"
    return "empty blocking_files -> graceful placeholder, git diagnostic preserved"


def test_format_escalated_message_is_ascii() -> str:
    # Delivered on stdout (the model-visible UserPromptSubmit channel); must survive Claude
    # Code's cp1252 hook-output pipe on Windows. Written all-ASCII, so pin the stronger
    # .isascii() (matches the ASCII-only convention of the sibling advisory hooks) -- the
    # git_stderr echo is the only place non-ASCII could enter, and a real conflict path can
    # carry a non-ASCII filename, so include one and confirm the *formatter's own* structural
    # text stays ASCII-safe up to that echoed content.
    msg = format_escalated_pull_failure_message(
        LOCAL, REMOTE, behind=5, blocking_files=["claude/skills/sources.md"],
        consecutive_count=3, seconds_failing=2 * 3600 + 30 * 60, git_stderr=_LOCAL_CHANGES_STDERR,
    )
    assert msg.isascii(), "escalated message (ASCII inputs) must be pure ASCII for the cp1252 pipe"
    return "escalated message is .isascii() with ASCII inputs"


def test_failure_state_roundtrip_and_clear() -> str:
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        assert read_failure_state(scratch) is None, "no file yet -> None"
        state = {"first_failure_at": 1000.0, "consecutive_count": 2, "last_failure_at": 1500.0}
        write_failure_state(state, scratch)
        assert read_failure_state(scratch) == state, "written state round-trips"
        # Atomic write must leave no orphan tmp file behind.
        leftovers = [p.name for p in scratch.iterdir() if p.name.endswith(".tmp")]
        assert not leftovers, f"no .tmp orphan after atomic write, found: {leftovers}"
        clear_failure_state(scratch)
        assert read_failure_state(scratch) is None, "cleared -> None"
        clear_failure_state(scratch)  # clearing an already-absent file is a no-op
    return "write/read round-trip, no tmp orphan, clear deletes, double-clear is a no-op"


def test_read_failure_state_malformed_and_non_dict_return_none() -> str:
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        failure_state_path(scratch).write_text("{not json", encoding="utf-8")
        assert read_failure_state(scratch) is None, "malformed JSON -> None"
        failure_state_path(scratch).write_text("[1, 2, 3]", encoding="utf-8")
        assert read_failure_state(scratch) is None, "non-dict JSON -> None"
    return "malformed JSON and non-dict JSON both -> None (fresh run, no crash)"


# --- PR #800 /review fixes -------------------------------------------------------


def test_format_escalated_message_behind_zero_unmeasured() -> str:
    # Review finding A2: behind == 0 on the escalation path can only be _count_from's fail-open
    # sentinel (a real count is always >= 1 there). Must not render "0 commits behind ... STALE"
    # -- the self-contradiction PR #701 fixed in the sibling formatters.
    msg = format_escalated_pull_failure_message(
        LOCAL, REMOTE, behind=0, blocking_files=["a.txt"],
        consecutive_count=3, seconds_failing=2 * 3600, git_stderr="",
    )
    assert "unmeasured number of commits" in msg, "behind==0 must read as unmeasured"
    assert "0 commit" not in msg, "must not render a literal '0 commits behind'"
    assert "STALE" in msg, "the stale-tooling warning must still be present"
    return "behind==0 -> 'unmeasured number of commits', never '0 commits behind'"


def test_format_escalated_message_sanitizes_git_stderr() -> str:
    # Review finding A4: git's stderr can carry non-cp1252 bytes (a non-Western LC_MESSAGES
    # locale, or U+FFFD from errors="replace"); echoing raw would raise UnicodeEncodeError at
    # print() and lose the advisory. ascii_sanitize must render the whole message ASCII/cp1252-safe.
    dirty_stderr = "error: Ваши изменения:\n\tclaude/skills/sources.md\nАварийный выход\n"
    assert not dirty_stderr.isascii(), "fixture must contain non-ASCII to exercise the sanitize"
    msg = format_escalated_pull_failure_message(
        LOCAL, REMOTE, behind=5, blocking_files=["claude/skills/sources.md"],
        consecutive_count=3, seconds_failing=2 * 3600, git_stderr=dirty_stderr,
    )
    assert msg.isascii(), "a non-ASCII git stderr must be sanitized so the whole message is ASCII"
    msg.encode("cp1252")  # must not raise
    return "non-ASCII git stderr sanitized -> whole escalated message .isascii()/cp1252-safe"


def test_format_pull_failure_message_sanitizes_git_stderr() -> str:
    # Same A4 fix applied to the pre-existing one-off formatter for consistency (both echo
    # git_stderr). This message carries a pre-existing em dash (U+2014) in its structural text
    # that is cp1252-safe but not ASCII, so the meaningful assertion is cp1252-encodability (the
    # actual failure mode: a raw non-cp1252 git stderr raises UnicodeEncodeError on the pipe),
    # not the stricter .isascii(). Without the sanitize, the Cyrillic below would make
    # .encode("cp1252") raise; with it, the Cyrillic becomes '?' and only the safe em dash remains.
    dirty_stderr = "error: Ваши:\n\tclaude/skills/sources.md\nAborting\n"
    msg = format_pull_failure_message(LOCAL, REMOTE, 5, dirty_stderr)
    msg.encode("cp1252")  # must not raise -- the whole point of the A4 sanitize
    assert "Ваши" not in msg, "non-cp1252 prose must be reduced away, not passed through"
    assert "claude/skills/sources.md" in msg, "an ASCII blocking-file path must be preserved"
    return "one-off formatter sanitizes git stderr (cp1252-safe); ASCII paths preserved"


def test_read_failure_state_non_utf8_returns_none() -> str:
    # Review finding A3/B4: a non-UTF-8 state file raises UnicodeDecodeError (a ValueError),
    # which must be caught (not escape and defeat the feature until a pull succeeds). Mirrors
    # the malformed/non-dict cases -> None (fresh run).
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        failure_state_path(scratch).write_bytes(b"\xff\xfe\x00 not valid utf-8 \x9d")
        assert read_failure_state(scratch) is None, "non-UTF-8 bytes -> None, no exception"
    return "non-UTF-8 state file -> None (UnicodeDecodeError caught)"


def test_build_failure_response_fresh_is_plain() -> str:
    # Review finding B3: the escalate-vs-plain decision extracted into a pure helper.
    state, msg = build_failure_response(
        None, now=1000.0, local=LOCAL, remote=REMOTE, behind_count=2, git_stderr="err\n"
    )
    assert state["consecutive_count"] == 1, "first failure records count 1"
    assert state["first_failure_at"] == 1000.0, "first failure stamps the clock"
    assert "PERSISTENT FAILURE" not in msg, "a first failure must use the plain one-off message"
    assert "fast-forward pull failed" in msg, "plain message text expected"
    return "first failure -> count 1, plain (non-escalated) message"


def test_build_failure_response_escalates_on_count() -> str:
    prev = {
        "first_failure_at": 1000.0,
        "consecutive_count": ESCALATE_AFTER_CONSECUTIVE_FAILURES - 1,
        "last_failure_at": 1000.0,
    }
    state, msg = build_failure_response(
        prev, now=1000.0, local=LOCAL, remote=REMOTE, behind_count=5, git_stderr="err\n"
    )
    assert state["consecutive_count"] == ESCALATE_AFTER_CONSECUTIVE_FAILURES, "count incremented to threshold"
    assert "PERSISTENT FAILURE" in msg, "reaching the count threshold escalates"
    assert LOCAL[:8] in msg and "5 commit" in msg, "escalated message carries the behind/SHA context"
    return "count reaches threshold -> escalated message with context"


def test_build_failure_response_escalates_on_time() -> str:
    # count stays low; only the elapsed-time arm can fire (robustness property).
    prev = {"first_failure_at": 0.0, "consecutive_count": 0}
    state, msg = build_failure_response(
        prev, now=ESCALATE_AFTER_HOURS * 3600 + 1, local=LOCAL, remote=REMOTE, behind_count=3, git_stderr="err\n"
    )
    assert state["consecutive_count"] == 1, "count is 1 (below the count threshold)"
    assert "PERSISTENT FAILURE" in msg, "the time arm escalates even at count 1"
    return "time threshold exceeded at count 1 -> escalated message"


def test_write_failure_state_per_pid_tmp_isolation() -> str:
    # Review finding B5: the atomic write uses a per-PID tmp name so two racing writers never
    # clobber each other's tmp. Mirrors test_journal_compose_force.py's monkeypatched-getpid
    # precedent. des and this test share the one `os` module object, so patching os.getpid is
    # seen by des.write_failure_state's os.getpid() call.
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        real_getpid = os.getpid
        try:
            os.getpid = lambda: 11111
            write_failure_state({"first_failure_at": 1.0, "consecutive_count": 1, "last_failure_at": 1.0}, scratch)
            os.getpid = lambda: 22222
            write_failure_state({"first_failure_at": 2.0, "consecutive_count": 2, "last_failure_at": 2.0}, scratch)
        finally:
            os.getpid = real_getpid
        assert read_failure_state(scratch) == {
            "first_failure_at": 2.0,
            "consecutive_count": 2,
            "last_failure_at": 2.0,
        }, "last writer wins the target"
        orphans = sorted(p.name for p in scratch.iterdir() if p.name.endswith(".tmp"))
        assert not orphans, f"per-PID tmps must be consumed by os.replace, found: {orphans}"
    return "two PIDs write via distinct tmp names, both consumed, last write wins"


def test_tmp_orphan_swept_by_tmp_cleanup() -> str:
    # Review finding B1: a rare os.replace failure can leave an orphaned
    # dev_env_sync_ff_failure.json.<pid>.tmp that the .json cleanup glob cannot match; the hook
    # adds a second sweep with ext=".tmp". Confirm that sweep (the exact prefix+ext the hook
    # uses) reaps a stale orphan while sparing a fresh (concurrent in-flight) one.
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        orphan = scratch / f"{FAILURE_STATE_PREFIX}.json.99999.tmp"
        orphan.write_text("{}", encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(orphan, (old, old))
        fresh = scratch / f"{FAILURE_STATE_PREFIX}.json.88888.tmp"
        fresh.write_text("{}", encoding="utf-8")
        _hookutil.cleanup_stale_sentinels(FAILURE_STATE_PREFIX, scratch=scratch, ext=".tmp")
        assert not orphan.exists(), "a >30-day-old .tmp orphan must be swept"
        assert fresh.exists(), "a fresh .tmp (concurrent in-flight write) must NOT be swept"
    return "stale .tmp orphan swept, fresh in-flight .tmp preserved"


def main() -> int:
    tests = [
        ("_plural singular and plural", test_plural_singular_and_plural),
        ("_count_from parses valid digit output", test_count_from_parses_valid_digit_output),
        ("_count_from fails open on nonzero returncode", test_count_from_fails_open_on_nonzero_returncode),
        ("_count_from fails open on non-digit output", test_count_from_fails_open_on_non_digit_output),
        ("format_sync_note shortens SHAs and pluralizes", test_format_sync_note_shortens_shas_and_pluralizes),
        ("format_pull_failure_message includes conflict + note", test_format_pull_failure_message_includes_conflict_and_note),
        ("format_diverged_message shows both directions", test_format_diverged_message_shows_both_directions),
        ("format_diverged_message: ahead-only is not labeled diverged", test_format_diverged_message_ahead_only_is_not_labeled_diverged),
        ("format_pulled_message: matching counts, no mismatch note", test_format_pulled_message_matching_counts_has_no_mismatch_note),
        ("format_pulled_message: singular commit", test_format_pulled_message_singular_commit),
        ("format_pulled_message: truncates with trailer", test_format_pulled_message_truncates_with_trailer),
        ("format_pulled_message: mismatch surfaces race note", test_format_pulled_message_mismatch_surfaces_race_note),
        ("format_pulled_message: zero behind_count reports unmeasured", test_format_pulled_message_zero_behind_count_reports_unmeasured),
        ("all formatters are cp1252-encodable", test_all_formatters_are_cp1252_encodable),
        ("parse_blocking_files: local-changes variant", test_parse_blocking_files_local_changes_variant),
        ("parse_blocking_files: untracked variant", test_parse_blocking_files_untracked_variant),
        ("parse_blocking_files: multiple files", test_parse_blocking_files_multiple_files),
        ("parse_blocking_files: no tab lines -> []", test_parse_blocking_files_no_tab_lines_returns_empty),
        ("format_duration: boundaries", test_format_duration_boundaries),
        ("record_failure: from None starts fresh", test_record_failure_from_none_starts_fresh),
        ("record_failure: increments and preserves first", test_record_failure_increments_and_preserves_first),
        ("record_failure: malformed prev starts fresh", test_record_failure_malformed_prev_starts_fresh),
        ("record_failure: valid first + corrupt count", test_record_failure_valid_first_corrupt_count),
        ("should_escalate: below both thresholds -> False", test_should_escalate_below_both_thresholds_false),
        ("should_escalate: count boundary", test_should_escalate_count_boundary),
        ("should_escalate: time boundary", test_should_escalate_time_boundary),
        ("should_escalate: time arm independent of count", test_should_escalate_time_arm_independent_of_count),
        ("format_escalated_message: contains key facts", test_format_escalated_message_contains_key_facts),
        ("format_escalated_message: singular prompt/commit", test_format_escalated_message_singular_prompt),
        ("format_escalated_message: no blocking files degrades", test_format_escalated_message_no_blocking_files_degrades),
        ("format_escalated_message: is ASCII", test_format_escalated_message_is_ascii),
        ("failure-state: round-trip and clear", test_failure_state_roundtrip_and_clear),
        ("failure-state: malformed/non-dict -> None", test_read_failure_state_malformed_and_non_dict_return_none),
        ("escalated message: behind==0 unmeasured (A2)", test_format_escalated_message_behind_zero_unmeasured),
        ("escalated message: sanitizes git stderr (A4)", test_format_escalated_message_sanitizes_git_stderr),
        ("one-off message: sanitizes git stderr (A4)", test_format_pull_failure_message_sanitizes_git_stderr),
        ("failure-state: non-UTF-8 -> None (A3/B4)", test_read_failure_state_non_utf8_returns_none),
        ("build_failure_response: fresh is plain (B3)", test_build_failure_response_fresh_is_plain),
        ("build_failure_response: escalates on count (B3)", test_build_failure_response_escalates_on_count),
        ("build_failure_response: escalates on time (B3)", test_build_failure_response_escalates_on_time),
        ("write_failure_state: per-PID tmp isolation (B5)", test_write_failure_state_per_pid_tmp_isolation),
        (".tmp orphan swept by .tmp cleanup (B1)", test_tmp_orphan_swept_by_tmp_cleanup),
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
