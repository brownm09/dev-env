#!/usr/bin/env python3
"""Tests for pre-tool-use-nested-agent-background-guard.py (dev-env#935, #1158).

Behavioral subprocess tests only (mirroring test_pre_tool_use_journal_compose_
force_guard.py's `_run_hook` pattern) -- this hook has no meaningful pure
classification function to test separately from main() (unlike the git-
command-parsing hooks): its whole decision is a few cheap field checks on an
already-parsed JSON payload plus one per-session sentinel, so there is nothing
worth extracting into a Layer-1 pure-function suite.

Pins the gate: BLOCK requires agent_id present (nested) AND run_in_background
omitted from tool_input AND evidence that this session's subagent schema
exposes the parameter (an earlier nested call in the same session carried it,
recorded as a sentinel). Without that evidence an omission allows: the harness
has shipped subagent Agent schemas with no run_in_background at all, where the
field can never arrive (dev-env#1158). Also pins the payload-shape fail-open
cases and the exact _hookout.emit_block channel contract (stdout empty,
non-empty stderr, exit 2).

Every run gets its own temporary HOME/USERPROFILE, so the sentinel (and the
heartbeat) land in a throwaway ~/.claude/scratch, never the real one.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-tool-use-nested-agent-background-guard.py")
MODULE_PATH = Path(_SCRIPT)
SENTINEL_PREFIX = "nested_agent_rib_seen_"
SESSION = "sess-1158"


def _run_hook(payload, home=None):
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    with tempfile.TemporaryDirectory() as tmp:
        root = home if home is not None else tmp
        env = dict(os.environ)
        env["HOME"] = str(root)
        env["USERPROFILE"] = str(root)  # Path.home() honors USERPROFILE on Windows
        return subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input=stdin_text,
            capture_output=True,
            text=True,
            env=env,
            timeout=90,  # generous headroom under CI resource contention (dev-env#994)
        )


def _sentinel(home):
    return Path(home) / ".claude" / "scratch" / f"{SENTINEL_PREFIX}{SESSION}.flag"


def _nested_payload(run_in_background=None, present=True, agent_id="agent_abc123",
                    agent_type="general-purpose", session_id=SESSION):
    tool_input = {"prompt": "do something"}
    if present:
        tool_input["run_in_background"] = run_in_background
    payload = {"tool_name": "Agent", "agent_id": agent_id, "agent_type": agent_type, "tool_input": tool_input}
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


# ---------------------------------------------------------------------------
# dev-env#1158: a subagent schema with no run_in_background must not be blocked
# ---------------------------------------------------------------------------

def test_nested_missing_field_without_schema_evidence_allows():
    # Reproduces the 2026-10-21 career-playbook block (PR #2271): a subagent
    # whose Agent tool has no run_in_background parameter can never send the
    # field, so before #1158 every one of its nested spawns was blocked.
    proc = _run_hook(_nested_payload(present=False))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""


def test_nested_missing_field_without_session_id_allows():
    # No session_id: no safe key for the schema evidence, so no judgment.
    proc = _run_hook(_nested_payload(present=False, session_id=None))
    assert proc.returncode == 0


def test_unsafe_session_id_allows_and_writes_nothing():
    with tempfile.TemporaryDirectory() as home:
        proc = _run_hook(_nested_payload(run_in_background=False, session_id="../escape"), home)
        assert proc.returncode == 0
        assert not list(Path(home).rglob(f"{SENTINEL_PREFIX}*"))


# ---------------------------------------------------------------------------
# The orphaning protection: nested AND omitted AND the schema offers the field
# ---------------------------------------------------------------------------

def test_explicit_field_records_schema_evidence():
    with tempfile.TemporaryDirectory() as home:
        proc = _run_hook(_nested_payload(run_in_background=False), home)
        assert proc.returncode == 0
        assert _sentinel(home).exists()


def test_nested_missing_field_after_schema_evidence_blocks():
    # Same session already proved its subagent schema exposes the parameter,
    # so an omission really is "forgot to choose" -- the #935 failure mode.
    with tempfile.TemporaryDirectory() as home:
        first = _run_hook(_nested_payload(run_in_background=False), home)
        assert first.returncode == 0
        proc = _run_hook(_nested_payload(present=False), home)
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "agent_id=agent_abc123" in proc.stderr
        assert "agent_type=general-purpose" in proc.stderr
        assert "run_in_background: false" in proc.stderr


def test_schema_evidence_is_per_session():
    with tempfile.TemporaryDirectory() as home:
        _run_hook(_nested_payload(run_in_background=True), home)
        proc = _run_hook(_nested_payload(present=False, session_id="other-session"), home)
        assert proc.returncode == 0


def test_missing_agent_type_still_blocks_with_placeholder():
    with tempfile.TemporaryDirectory() as home:
        _run_hook(_nested_payload(run_in_background=False), home)
        payload = {
            "session_id": SESSION,
            "tool_name": "Agent",
            "agent_id": "agent_xyz789",
            "tool_input": {"prompt": "x"},
        }
        proc = _run_hook(payload, home)
        assert proc.returncode == 2
        assert "agent_type=unknown" in proc.stderr


def test_nested_explicit_false_allows():
    proc = _run_hook(_nested_payload(run_in_background=False))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_nested_explicit_true_allows():
    # Deliberate parallel fan-out (e.g. "a reviewer subagent that dispatches
    # a verifier per finding") must not be punished -- only an OMITTED field
    # is the failure mode this hook targets.
    with tempfile.TemporaryDirectory() as home:
        _run_hook(_nested_payload(run_in_background=False), home)
        proc = _run_hook(_nested_payload(run_in_background=True), home)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_top_level_missing_run_in_background_allows():
    # No agent_id at all -- a top-level spawn from the main session. Out of
    # scope by design: backgrounding there is the normal, documented default.
    with tempfile.TemporaryDirectory() as home:
        _run_hook(_nested_payload(run_in_background=False), home)
        payload = {"session_id": SESSION, "tool_name": "Agent", "tool_input": {"prompt": "do something"}}
        proc = _run_hook(payload, home)
        assert proc.returncode == 0
        assert proc.stderr == ""


def test_top_level_explicit_agent_id_null_allows():
    payload = {"tool_name": "Agent", "agent_id": None, "tool_input": {"prompt": "x"}}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_empty_agent_id_string_allows():
    # Falsy-but-present agent_id treated the same as absent (`if not agent_id`).
    with tempfile.TemporaryDirectory() as home:
        _run_hook(_nested_payload(run_in_background=False), home)
        proc = _run_hook(_nested_payload(present=False, agent_id=""), home)
        assert proc.returncode == 0


# ---------------------------------------------------------------------------
# Payload-shape fail-open cases -- unrelated to the guard's own decision
# ---------------------------------------------------------------------------

def test_empty_stdin_allows():
    proc = _run_hook("")
    assert proc.returncode == 0


def test_malformed_json_allows():
    proc = _run_hook("{not json")
    assert proc.returncode == 0


def test_non_dict_json_allows():
    proc = _run_hook("[1, 2, 3]")
    assert proc.returncode == 0


def test_wrong_tool_name_allows():
    payload = {"tool_name": "Bash", "agent_id": "agent_abc123", "tool_input": {"command": "ls"}}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_tool_name_case_sensitive_no_match():
    # Claude Code reports "Agent" exactly; a lowercase/other-cased variant
    # must not be treated as a match (fails open, not a case-fold bug).
    payload = {"tool_name": "agent", "agent_id": "agent_abc123", "tool_input": {}}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_missing_tool_input_allows():
    # A well-formed Agent PreToolUse payload always carries tool_input as an
    # object; a payload where it's missing entirely is a shape anomaly this
    # hook does not understand -- fail open rather than guess.
    payload = {"session_id": SESSION, "tool_name": "Agent", "agent_id": "agent_abc123"}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_non_dict_tool_input_allows():
    payload = {"session_id": SESSION, "tool_name": "Agent", "agent_id": "agent_abc123", "tool_input": "not-a-dict"}
    proc = _run_hook(payload)
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
