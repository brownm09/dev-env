#!/usr/bin/env python3
"""End-to-end subprocess tests for session-mode-prompt.py's sentinel cleanup
wiring (dev-env#768).

Before this fix, the hook's own docstring documented the marker files at
scratch/session_mode_ack_<session_id>.txt as "orphaned but harmless" and
never swept them -- one of several non-cleaning sentinel writers found at the
2026-07-10 hook-reliability assessment. It now sweeps them via
_hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX, ext=".txt") on every
invocation (this hook only fires on UserPromptSubmit -- once per prompt, not
a hot per-tool-call path, so no gating is needed here, unlike
disk-space-check.py).

Drives the real hook over stdin via subprocess, with HOME/USERPROFILE
redirected to a disposable temp dir so no run ever touches the real
~/.claude/scratch/. A prompt starting with a lowercase-initial XML tag (e.g.
<scheduled-task>) is treated as automated and suppressed by the hook's
pre-existing logic -- avoided here by using plain human-looking prompt text.

Usage:
    py -3 claude/scripts/tests/test_session_mode_prompt.py

Exit 0 = all pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "session-mode-prompt.py"
SENTINEL_PREFIX = "session_mode_ack_"
MAX_AGE_DAYS = 30  # mirrors _hookutil.MAX_AGE_DAYS


def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _run_hook(payload, home: Path):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    return subprocess.run(
        _py_cmd() + [str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=30,
    )


def _scratch(home: Path) -> Path:
    return home / ".claude" / "scratch"


def test_creates_marker_for_new_session() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        proc = _run_hook({"session_id": "sess-new-1", "prompt": "help me fix a bug"}, home)
        assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stderr}"
        marker = _scratch(home) / f"{SENTINEL_PREFIX}sess-new-1.txt"
        assert marker.exists(), f"expected marker at {marker}"
        assert proc.stdout.strip(), "expected additionalContext JSON on stdout for a fresh session"
    return "first prompt of a new session writes session_mode_ack_<sid>.txt and emits context"


def test_second_prompt_same_session_passes_through_silently() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        first = _run_hook({"session_id": "sess-repeat", "prompt": "first prompt"}, home)
        assert first.returncode == 0
        marker = _scratch(home) / f"{SENTINEL_PREFIX}sess-repeat.txt"
        assert marker.exists()

        second = _run_hook({"session_id": "sess-repeat", "prompt": "second prompt"}, home)
        assert second.returncode == 0, f"second invocation must also exit 0: {second.stderr}"
        assert second.stdout.strip() == "", "an already-acked session must pass through with no output"
    return "a second prompt in an already-acked session emits no additional context"


def test_sweeps_stale_marker_keeps_fresh() -> str:
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        scratch = _scratch(home)
        scratch.mkdir(parents=True, exist_ok=True)

        stale = scratch / f"{SENTINEL_PREFIX}old-session.txt"
        fresh = scratch / f"{SENTINEL_PREFIX}recent-session.txt"
        stale.write_text("123.0")
        fresh.write_text("456.0")
        past = time.time() - (MAX_AGE_DAYS + 1) * 86400
        os.utime(stale, (past, past))

        proc = _run_hook({"session_id": "sess-brand-new", "prompt": "hello"}, home)
        assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}: {proc.stderr}"
        assert not stale.exists(), "a marker older than MAX_AGE_DAYS must be swept"
        assert fresh.exists(), "a fresh marker from another session must be kept"
    return "cleanup sweeps a >30-day-old .txt marker from an unrelated session while keeping a fresh one"


def test_automated_prompt_still_suppressed_and_no_marker_write_needed() -> str:
    # Pre-existing behavior (unchanged by this PR): automated XML-tagged
    # prompts are suppressed before the marker is even written. Confirms the
    # new cleanup call doesn't interfere with that early exit.
    with tempfile.TemporaryDirectory() as home_s:
        home = Path(home_s)
        proc = _run_hook(
            {"session_id": "sess-auto", "prompt": "<scheduled-task>run the thing</scheduled-task>"},
            home,
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == "", "automated prompts must not receive the reminder"
    return "automated (XML-tagged) prompts remain suppressed after adding the cleanup call"


def main() -> int:
    tests = [
        ("creates marker for a new session", test_creates_marker_for_new_session),
        ("second prompt, same session, passes through silently",
         test_second_prompt_same_session_passes_through_silently),
        ("sweeps stale marker, keeps fresh", test_sweeps_stale_marker_keeps_fresh),
        ("automated prompt still suppressed", test_automated_prompt_still_suppressed_and_no_marker_write_needed),
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
