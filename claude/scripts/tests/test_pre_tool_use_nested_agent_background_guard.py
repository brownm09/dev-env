#!/usr/bin/env python3
"""Tests for pre-tool-use-nested-agent-background-guard.py (dev-env#935).

Behavioral subprocess tests only (mirroring test_pre_tool_use_journal_compose_
force_guard.py's `_run_hook` pattern) -- this hook has no meaningful pure
classification function to test separately from main() (unlike the git-
command-parsing hooks): its whole decision is three cheap field checks on an
already-parsed JSON payload, so there is nothing worth extracting into a
Layer-1 pure-function suite.

Pins the exact three-condition gate: BLOCK requires agent_id present (nested)
AND run_in_background omitted entirely from tool_input; any one condition
failing must allow. Also pins the payload-shape fail-open cases (empty
stdin, malformed JSON, non-dict JSON, wrong tool_name, missing/non-dict
tool_input) and the exact _hookout.emit_block channel contract (stdout=None,
non-empty stderr, exit 2).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-tool-use-nested-agent-background-guard.py")
MODULE_PATH = Path(_SCRIPT)


def _run_hook(payload):
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=90,  # generous headroom under CI resource contention (dev-env#994)
    )


def _nested_payload(run_in_background=None, present=True, agent_id="agent_abc123", agent_type="general-purpose"):
    tool_input = {"prompt": "do something"}
    if present:
        tool_input["run_in_background"] = run_in_background
    return {"tool_name": "Agent", "agent_id": agent_id, "agent_type": agent_type, "tool_input": tool_input}


# ---------------------------------------------------------------------------
# The core gate: nested (agent_id present) AND run_in_background omitted
# ---------------------------------------------------------------------------

def test_nested_missing_run_in_background_blocks():
    proc = _run_hook(_nested_payload(present=False))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "agent_id=agent_abc123" in proc.stderr
    assert "agent_type=general-purpose" in proc.stderr
    assert "run_in_background: false" in proc.stderr


def test_nested_explicit_false_allows():
    proc = _run_hook(_nested_payload(run_in_background=False))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_nested_explicit_true_allows():
    # Deliberate parallel fan-out (e.g. "a reviewer subagent that dispatches
    # a verifier per finding") must not be punished -- only an OMITTED field
    # is the failure mode this hook targets.
    proc = _run_hook(_nested_payload(run_in_background=True))
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_top_level_missing_run_in_background_allows():
    # No agent_id at all -- a top-level spawn from the main session. Out of
    # scope by design: backgrounding there is the normal, documented default.
    payload = {"tool_name": "Agent", "tool_input": {"prompt": "do something"}}
    proc = _run_hook(payload)
    assert proc.returncode == 0
    assert proc.stderr == ""


def test_top_level_explicit_agent_id_null_allows():
    payload = {"tool_name": "Agent", "agent_id": None, "tool_input": {"prompt": "x"}}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_empty_agent_id_string_allows():
    # Falsy-but-present agent_id treated the same as absent (`if not agent_id`).
    proc = _run_hook(_nested_payload(present=False, agent_id=""))
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
    payload = {"tool_name": "Agent", "agent_id": "agent_abc123"}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_non_dict_tool_input_allows():
    payload = {"tool_name": "Agent", "agent_id": "agent_abc123", "tool_input": "not-a-dict"}
    proc = _run_hook(payload)
    assert proc.returncode == 0


def test_missing_agent_type_still_blocks_with_placeholder():
    payload = {
        "tool_name": "Agent",
        "agent_id": "agent_xyz789",
        "tool_input": {"prompt": "x"},
    }
    proc = _run_hook(payload)
    assert proc.returncode == 2
    assert "agent_type=unknown" in proc.stderr


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
