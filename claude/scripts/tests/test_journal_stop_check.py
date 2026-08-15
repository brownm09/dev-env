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

The stub-push sentinel is scoped to the pushing session's own session_id
(dev-env#980, ADR-091 Amendment 2): a global sentinel previously let any
concurrent session's Stop consume it, producing both false-positive archive
instructions and missed reminders. `consume_stub_pushed_sentinel(session_id,
sentinel=...)` and the behavioral `_run_hook`/`_sentinel_path` fixtures below
were updated accordingly; see test_e2e_cross_session_sentinel_not_consumed and
test_e2e_no_session_id_never_blocks for the direct regression coverage.

Three layers, mirroring this repo's hook-test convention:

  * Pure / fixture-helper tests exercise the changed surface offline:
    - archive_reminder_message(): ASCII / cp1252-encodable (so the exit-2 stderr
      text cannot vanish under Claude Code's cp1252 hook-output pipe on Windows)
      and names the archive MCP tool + the list_sessions lookup.
    - parse_stop_hook_active(): True only when the payload sets the flag; False on
      false / missing / empty / malformed / non-dict stdin (never suppresses a
      genuine first Stop).
    - parse_session_id(): the payload's session_id, or "" on missing/empty/
      malformed/non-dict stdin (dev-env#980).
    - consume_stub_pushed_sentinel(session_id, sentinel=tmp): an explicit
      sentinel override behaves as before (present -> reminder + delete; second
      read -> None; absent -> None). Without an override, the path is derived
      from session_id; a falsy session_id with no override returns None without
      touching the filesystem.

  * A behavioral layer drives the real hook end-to-end over stdin via subprocess,
    with HOME/USERPROFILE pointed at a temp dir so the per-session sentinel
    resolves under the tmp scratch (isolated from the real ~/.claude/scratch):
    - flag present (own session_id) + stop_hook_active=false -> exit 2, reminder
      on stderr, EMPTY stdout (Claude Code shows a Stop hook's stderr on exit 2,
      not stdout), and the flag is consumed.
    - no flag -> exit 0 (advisory path; the git advisory calls fail closed against
      the nonexistent tmp journal repo, so output is empty).
    - no flag + a planted stale (uncomposed, pre-today) stub in the tmp journal repo
      -> exit 0 with the Checks 2-3 advisory delivered as a `{"systemMessage": ...}`
      JSON object on STDOUT and empty stderr (the re-pin of the corrected channel:
      systemMessage, not the former invisible plain-stdout print -- dev-env#740).
    - flag present (own session_id) + stop_hook_active=true -> exit 0 (loop guard:
      no re-block) and the flag is PRESERVED (not consumed without delivery).
    - flag present under a DIFFERENT session_id than the one in the payload ->
      exit 0 (no block) and that other session's flag is left untouched
      (dev-env#980's actual regression case).
    - flag present but the payload carries no session_id at all -> exit 0 (no
      block), regardless of what flags exist on disk.

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
import _hookutil  # noqa: E402  -- needs the sys.path.insert above

_spec = importlib.util.spec_from_file_location("journal_stop_check", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
jsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jsc)  # safe: main() is guarded by __main__

# stub-push-archive-reminder.py -- loaded too, only for the cross-file
# SENTINEL_PREFIX parity test below (dev-env#980 review finding: nothing
# previously caught the two literals drifting apart, which would silently
# and completely kill the archive-reminder mechanism this PR fixes).
_WRITER_SCRIPT = REPO_ROOT / "claude" / "scripts" / "stub-push-archive-reminder.py"
_writer_spec = importlib.util.spec_from_file_location("stub_push_archive_reminder", _WRITER_SCRIPT)
assert _writer_spec and _writer_spec.loader, f"cannot load module spec from {_WRITER_SCRIPT}"
spar = importlib.util.module_from_spec(_writer_spec)
_writer_spec.loader.exec_module(spar)  # safe: main() is guarded by __main__


def test_sentinel_prefix_matches_writer_module():
    assert jsc.SENTINEL_PREFIX == spar.SENTINEL_PREFIX, (
        f"journal-stop-check.py's SENTINEL_PREFIX ({jsc.SENTINEL_PREFIX!r}) must match "
        f"stub-push-archive-reminder.py's ({spar.SENTINEL_PREFIX!r}) or the reader can never "
        f"find a sentinel the writer wrote -- a silent, total failure of the whole mechanism "
        f"(dev-env#980 review finding)"
    )
    return "reader and writer SENTINEL_PREFIX literals match (guards against silent drift)"


# --- pure: _SAFE_SESSION_ID (dev-env#980 review finding: unsanitized session_id
# in a filesystem path this hook unlink()'s) -------------------------------------

def test_safe_session_id_accepts_uuid_shape():
    assert jsc._SAFE_SESSION_ID.match("a1b2c3d4-e5f6-4789-a012-3456789abcde")
    return "a real UUID-shaped session_id matches"


def test_safe_session_id_rejects_path_traversal():
    for bad in ("../../etc/passwd", "a/b", "a\\b", "a b", "a.b", ""):
        assert not jsc._SAFE_SESSION_ID.match(bad), f"{bad!r} must not match"
    return "path separators, whitespace, dots, and empty string are all rejected"


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


# --- pure: parse_session_id() (dev-env#980) -------------------------------------

def test_parse_session_id_present():
    assert jsc.parse_session_id(json.dumps({"session_id": "abc-123"})) == "abc-123"
    return "session_id present -> returned"


def test_parse_session_id_missing():
    assert jsc.parse_session_id(json.dumps({"stop_hook_active": False})) == ""
    return "session_id absent -> ''"


def test_parse_session_id_empty_and_malformed():
    assert jsc.parse_session_id("") == ""
    assert jsc.parse_session_id("not json{") == ""
    assert jsc.parse_session_id("[]") == ""  # non-dict JSON -> .get raises -> ""
    return "empty / malformed / non-dict stdin -> '' (never raises)"


# --- fixture: consume_stub_pushed_sentinel(session_id, sentinel=tmp, scratch=tmp) --
# The *sentinel* override path (explicit fixture injection) and the
# *session_id*-derives-a-path production path are exercised separately --
# passing session_id alongside an explicit sentinel override would be dead
# (the sentinel branch short-circuits before session_id is ever read), so
# the override tests below omit it (dev-env#980 review finding).

def test_consume_present_returns_and_deletes():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed-s1.flag"
        flag.write_text("1")
        msg = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        assert msg == jsc.archive_reminder_message()
        assert not flag.exists(), "sentinel must be consumed (deleted) on read"
    return "present flag (explicit sentinel override) -> reminder returned + flag deleted (one-shot)"


def test_consume_is_one_shot():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed-s1.flag"
        flag.write_text("1")
        first = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        second = jsc.consume_stub_pushed_sentinel(sentinel=flag)
        assert first is not None and second is None
    return "second read after consume -> None (block fires at most once)"


def test_consume_absent_returns_none():
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "stub-pushed-s1.flag"  # never created
        assert jsc.consume_stub_pushed_sentinel(sentinel=flag) is None
    return "absent flag -> None"


def test_consume_derives_path_from_session_id():
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        derived = _hookutil.sentinel_path(jsc.SENTINEL_PREFIX, "s1", scratch=scratch)
        derived.parent.mkdir(parents=True, exist_ok=True)
        derived.write_text("1")
        # No explicit sentinel override -- must derive the same path itself,
        # via the injectable *scratch* param (not a monkeypatch of
        # _hookutil.SCRATCH -- dev-env#980 review finding).
        msg = jsc.consume_stub_pushed_sentinel(session_id="s1", scratch=scratch)
        assert msg == jsc.archive_reminder_message()
        assert not derived.exists()
    return "no sentinel override -> path derived from session_id via _hookutil.sentinel_path(scratch=...)"


def test_consume_empty_session_id_no_override_returns_none_without_touching_disk():
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        # Plant a file at what an empty-session_id path WOULD resolve to, to
        # prove it is never even looked at.
        degenerate = scratch / f"{jsc.SENTINEL_PREFIX}.flag"
        degenerate.write_text("1")
        result = jsc.consume_stub_pushed_sentinel(session_id="", scratch=scratch)
        assert result is None
        assert degenerate.exists(), "a falsy session_id must never touch any file, degenerate or not"
    return "falsy session_id + no override -> None, filesystem untouched (dev-env#980 guard)"


def test_consume_unsafe_session_id_no_override_returns_none_without_touching_disk():
    with tempfile.TemporaryDirectory() as d:
        scratch = Path(d)
        result = jsc.consume_stub_pushed_sentinel(session_id="../escape", scratch=scratch)
        assert result is None
    return "session_id outside _SAFE_SESSION_ID + no override -> None (dev-env#980 review finding)"


# --- behavioral: real hook over stdin via subprocess (HOME-isolated sentinel) --

def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _sentinel_path(home, session_id):
    return Path(home) / ".claude" / "scratch" / f"stub-pushed-{session_id}.flag"


def _run_hook(home, *, flag_present, stop_hook_active, session_id="s1", flag_session_id=None,
              include_session_id=True):
    """Drive the real hook once. Returns (exit_code, stdout, stderr).

    *session_id* is the id sent in the Stop payload (the "current session").
    *flag_session_id* -- the id whose sentinel gets planted when flag_present
    -- defaults to *session_id* (same-session, the common case); pass a
    different value to simulate another session's still-armed sentinel
    (dev-env#980). *include_session_id=False* omits session_id from the
    payload entirely.
    """
    home = Path(home)
    if flag_present:
        sentinel = _sentinel_path(home, flag_session_id if flag_session_id is not None else session_id)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("1")
    payload_dict = {"stop_hook_active": stop_hook_active, "hook_event_name": "Stop"}
    if include_session_id:
        payload_dict["session_id"] = session_id
    payload = json.dumps(payload_dict)
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
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=False, session_id="s1")
        consumed = not _sentinel_path(home, "s1").exists()
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[journal-stop-hook]" in err and "ccd_session_mgmt__archive_session" in err, err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    assert consumed, "sentinel must be consumed on the blocking run"
    return "e2e own-session flag + not-continuation -> exit 2, reminder on stderr, empty stdout, flag consumed"


def test_e2e_no_flag_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=False, stop_hook_active=False)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no flag -> exit 0 (advisory path, fail-closed against the tmp journal repo)"


def test_e2e_cross_session_sentinel_not_consumed():
    # The dev-env#980 regression: session A's push armed a sentinel; a
    # DIFFERENT session's Stop (session B) must not consume it or block.
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(
            home, flag_present=True, stop_hook_active=False,
            session_id="session-B", flag_session_id="session-A",
        )
        a_flag_survives = _sentinel_path(home, "session-A").exists()
    assert rc == 0, f"expected exit 0 (no cross-session block), got {rc} (stderr={err!r})"
    assert "ccd_session_mgmt__archive_session" not in err, err
    assert a_flag_survives, "session A's sentinel must be untouched by session B's Stop"
    return "e2e session A's flag + session B's Stop -> exit 0, no block, A's flag left intact (dev-env#980)"


def test_e2e_no_session_id_never_blocks():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(
            home, flag_present=True, stop_hook_active=False,
            flag_session_id="session-A", include_session_id=False,
        )
        a_flag_survives = _sentinel_path(home, "session-A").exists()
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    assert a_flag_survives, "no session_id in the payload -> no flag may be touched"
    return "e2e payload with no session_id -> exit 0 regardless of what flags exist on disk"


def test_e2e_stop_hook_active_allows_and_preserves_flag():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(home, flag_present=True, stop_hook_active=True, session_id="s1")
        preserved = _sentinel_path(home, "s1").exists()
    assert rc == 0, f"expected exit 0 with stop_hook_active, got {rc} (stderr={err!r})"
    assert preserved, "flag must be preserved (not consumed) on a continuation so it can still deliver later"
    return "e2e own-session flag + stop_hook_active=true -> exit 0 (loop guard), flag preserved"


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
        ("SENTINEL_PREFIX matches writer module (dev-env#980)", test_sentinel_prefix_matches_writer_module),
        ("_SAFE_SESSION_ID accepts UUID shape (dev-env#980)", test_safe_session_id_accepts_uuid_shape),
        ("_SAFE_SESSION_ID rejects path traversal (dev-env#980)", test_safe_session_id_rejects_path_traversal),
        ("parse session_id present (dev-env#980)", test_parse_session_id_present),
        ("parse session_id missing (dev-env#980)", test_parse_session_id_missing),
        ("parse session_id empty/malformed (dev-env#980)", test_parse_session_id_empty_and_malformed),
        ("consume present returns + deletes", test_consume_present_returns_and_deletes),
        ("consume is one-shot", test_consume_is_one_shot),
        ("consume absent -> None", test_consume_absent_returns_none),
        ("consume derives path from session_id (dev-env#980)", test_consume_derives_path_from_session_id),
        ("consume empty session_id -> None, disk untouched (dev-env#980)", test_consume_empty_session_id_no_override_returns_none_without_touching_disk),
        ("consume unsafe session_id -> None, disk untouched (dev-env#980)", test_consume_unsafe_session_id_no_override_returns_none_without_touching_disk),
        ("e2e flag blocks on stderr + consumes", test_e2e_flag_blocks_on_stderr_and_consumes),
        ("e2e no flag allows", test_e2e_no_flag_allows),
        ("e2e cross-session sentinel not consumed (dev-env#980)", test_e2e_cross_session_sentinel_not_consumed),
        ("e2e no session_id never blocks (dev-env#980)", test_e2e_no_session_id_never_blocks),
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
