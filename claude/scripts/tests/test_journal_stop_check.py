#!/usr/bin/env python3
"""Unit + behavioral tests for journal-stop-check.py (ADR-091).

journal-stop-check.py is a Stop hook. Its Check 1 (stub-push sentinel) emits a
CLAUDE-facing archive instruction -- "call ccd_session_mgmt__archive_session ...
Then stop." -- which must reach Claude's context. For a Stop hook, exit-0 stdout
is NOT added to Claude's context (only UserPromptSubmit / UserPromptExpansion /
SessionStart are), so the reminder is now emitted on STDERR with exit 2 (blocking
the stop), mirroring stop-tile-enumeration-gate.py (ADR-088). Its Checks 2-3
(stale-draft / unmerged-branch advisories) stay NON-blocking but now ride the
_hookout systemMessage channel (exit 0) rather than plain stdout: a Stop hook's
exit-0 stdout is invisible (transcript-only), so the former print() surfaced
nothing (ADR-103, dev-env#740). They point at work for a later, dedicated session
and must not block.

Two layers, mirroring this repo's hook-test convention:

  * Pure / fixture-helper tests exercise the changed surface offline:
    - archive_reminder_message(): ASCII / cp1252-encodable (so the exit-2 stderr
      text cannot vanish under Claude Code's cp1252 hook-output pipe on Windows)
      and names the archive MCP tool + the list_sessions lookup.
    - parse_stop_hook_active(): True only when the payload sets the flag; False on
      false / missing / empty / malformed / non-dict stdin (never suppresses a
      genuine first Stop).
    - consume_stub_pushed_sentinel(sentinel=tmp): a present flag yields the
      reminder and is deleted (the consume-on-read one-shot guard); a second read
      yields None; an absent flag yields None.

  * A behavioral layer drives the real hook end-to-end over stdin via subprocess,
    with HOME/USERPROFILE pointed at a temp dir so SENTINEL resolves under the tmp
    scratch (isolated from the real ~/.claude/scratch):
    - flag present + stop_hook_active=false -> exit 2, reminder on stderr, EMPTY
      stdout (Claude Code shows a Stop hook's stderr on exit 2, not stdout), and
      the flag is consumed.
    - no flag -> exit 0 (advisory path; the git advisory calls fail closed against
      the nonexistent tmp journal repo, so output is empty).
    - no flag + a planted stale (uncomposed, pre-today) stub in the tmp journal repo
      -> exit 0 with the Checks 2-3 advisory delivered as a `{"systemMessage": ...}`
      JSON object on STDOUT and empty stderr (the re-pin of the corrected channel:
      systemMessage, not the former invisible plain-stdout print -- dev-env#740).
    - flag present + stop_hook_active=true -> exit 0 (loop guard: no re-block) and
      the flag is PRESERVED (not consumed without delivery).

main()'s advisory branches (stale drafts / unmerged branches / orphan cleanup) run
subprocess git against the journal repo and are not unit-tested here (pure-helper
convention) -- the end-to-end no-flag run exercises their fail-closed path.

Usage:
    py -3 claude/scripts/tests/test_journal_stop_check.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "journal-stop-check.py"

# The script imports _winsubp (sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("journal_stop_check", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
jsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jsc)  # safe: main() is guarded by __main__


# --- pure: archive_reminder_message() ------------------------------------------

def test_archive_message_is_cp1252_encodable():
    msg = jsc.archive_reminder_message()
    assert msg.isascii(), f"archive reminder must be ASCII, got: {msg!r}"
    msg.encode("cp1252")  # must not raise -- Claude Code pipes hook output as cp1252 on Windows
    return "archive reminder is ASCII / cp1252-safe (cannot vanish on the exit-2 stderr path)"


def test_archive_message_names_tool_and_lookup():
    msg = jsc.archive_reminder_message()
    assert "ccd_session_mgmt__archive_session" in msg, msg
    assert "list_sessions" in msg, msg
    return "archive reminder names the archive MCP tool + the list_sessions lookup"


# --- pure: parse_stop_hook_active() --------------------------------------------

def test_parse_stop_hook_active_true():
    assert jsc.parse_stop_hook_active(json.dumps({"stop_hook_active": True})) is True
    return "stop_hook_active:true -> True"


def test_parse_stop_hook_active_false():
    assert jsc.parse_stop_hook_active(json.dumps({"stop_hook_active": False})) is False
    return "stop_hook_active:false -> False"


def test_parse_stop_hook_active_missing():
    assert jsc.parse_stop_hook_active(json.dumps({"session_id": "x"})) is False
    return "flag absent -> False"


def test_parse_stop_hook_active_empty_and_malformed():
    assert jsc.parse_stop_hook_active("") is False
    assert jsc.parse_stop_hook_active("not json{") is False
    assert jsc.parse_stop_hook_active("[]") is False  # non-dict JSON -> .get raises -> False
    return "empty / malformed / non-dict stdin -> False (never suppresses a genuine first Stop)"


# --- fixture: consume_stub_pushed_sentinel(sentinel=tmp) -----------------------

def test_consume_present_returns_and_deletes():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed.flag"
        flag.write_text("1")
        msg = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        assert msg == jsc.archive_reminder_message()
        assert not flag.exists(), "sentinel must be consumed (deleted) on read"
    return "present flag -> reminder returned + flag deleted (one-shot)"


def test_consume_is_one_shot():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed.flag"
        flag.write_text("1")
        first = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        second = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        assert first is not None and second is None
    return "second read after consume -> None (block fires at most once)"


def test_consume_absent_returns_none():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed.flag"  # never created
        assert jsc.consume_stub_pushed_sentinel(sentinel=flag) is None
    return "absent flag -> None"


# --- behavioral: real hook over stdin via subprocess (HOME-isolated sentinel) --

def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _sentinel_path(home):
    return Path(home) / ".claude" / "scratch" / "stub-pushed.flag"


def _run_hook(home, *, flag_present, stop_hook_active):
    """Drive the real hook once. Returns (exit_code, stdout, stderr)."""
    home = Path(home)
    if flag_present:
        sentinel = _sentinel_path(home)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("1")
    payload = json.dumps({"stop_hook_active": stop_hook_active, "hook_event_name": "Stop"})
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=payload,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_e2e_flag_blocks_on_stderr_and_consumes():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False)
        consumed = not _sentinel_path(home).exists()
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[journal-stop-hook]" in err and "ccd_session_mgmt__archive_session" in err, err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    assert consumed, "sentinel must be consumed on the blocking run"
    return "e2e flag + not-continuation -> exit 2, reminder on stderr, empty stdout, flag consumed"


def test_e2e_no_flag_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=False, stop_hook_active=False)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no flag -> exit 0 (advisory path, fail-closed against the tmp journal repo)"


def test_e2e_stop_hook_active_allows_and_preserves_flag():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=True)
        preserved = _sentinel_path(home).exists()
    assert rc == 0, f"expected exit 0 with stop_hook_active, got {rc} (stderr={err!r})"
    assert preserved, "flag must be preserved (not consumed) on a continuation so it can still deliver later"
    return "e2e flag + stop_hook_active=true -> exit 0 (loop guard), flag preserved"


def test_e2e_stale_advisory_is_systemmessage():
    # The re-pin of the corrected Checks 2-3 channel (dev-env#740): plant an old,
    # uncomposed stub in the tmp journal repo (JOURNAL_REPO = <home>/Git/
    # engineering-journal) so stale_draft_artifacts() fires, then assert the
    # advisory arrives as a {"systemMessage": ...} JSON object on STDOUT (exit 0) --
    # NOT the former invisible plain-stdout print. The non-git tmp repo makes the
    # orphan-cleanup git status fail closed, so the planted stub stays "still stale".
    with tempfile.TemporaryDirectory() as home:
        stub = (
            Path(home) / "Git" / "engineering-journal" / "sessions" / "proj"
            / "2020-01-01_120000.stub.md"
        )
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("old uncomposed draft")
        rc, out, err = _run_hook(home, flag_present=False, stop_hook_active=False)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    assert err.strip() == "", f"stderr must be empty on the advisory path, got {err!r}"
    payload = json.loads(out)  # emit_advisory writes exactly one JSON object to stdout
    assert "systemMessage" in payload, f"advisory must be a systemMessage JSON, got {out!r}"
    assert "Stale draft artifact" in payload["systemMessage"], payload
    assert "2020-01-01" in payload["systemMessage"], payload
    return "e2e no flag + planted stale stub -> exit 0, Checks 2-3 delivered as a systemMessage JSON on stdout"


def main():
    tests = [
        ("archive message cp1252-encodable", test_archive_message_is_cp1252_encodable),
        ("archive message names tool + lookup", test_archive_message_names_tool_and_lookup),
        ("parse stop_hook_active true", test_parse_stop_hook_active_true),
        ("parse stop_hook_active false", test_parse_stop_hook_active_false),
        ("parse stop_hook_active missing", test_parse_stop_hook_active_missing),
        ("parse stop_hook_active empty/malformed", test_parse_stop_hook_active_empty_and_malformed),
        ("consume present returns + deletes", test_consume_present_returns_and_deletes),
        ("consume is one-shot", test_consume_is_one_shot),
        ("consume absent -> None", test_consume_absent_returns_none),
        ("e2e flag blocks on stderr + consumes", test_e2e_flag_blocks_on_stderr_and_consumes),
        ("e2e no flag allows", test_e2e_no_flag_allows),
        ("e2e stale advisory is a systemMessage", test_e2e_stale_advisory_is_systemmessage),
        ("e2e stop_hook_active allows + preserves flag", test_e2e_stop_hook_active_allows_and_preserves_flag),
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
