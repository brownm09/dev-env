#!/usr/bin/env python3
"""Tests for session-mode-report.py.

Drives the report against a synthetic fixture log covering the cases that
matter: an interactive session that started in `plan`, an interactive session
that started in `bypassPermissions` (must be flagged), an automated
`<scheduled-task>` session in bypass (must NOT be flagged), a multi-prompt
session whose *earliest* entry determines the startup mode, a malformed line,
and a line with no session_id. Asserts classification, flagging, startup-mode
selection, and that bad lines are counted rather than crashing.

Usage:
    py -3 claude/scripts/tests/test_session_mode_report.py

Exit 0 = all pass. Mirrors the harness style of test_pyw_stdio.py.
"""

import json
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import importlib

smr = importlib.import_module("session-mode-report")


def _line(session_id, ts, mode, prompt_prefix, stage="additional_context_emitted", **extra):
    entry = {
        "stage": stage,
        "session_id": session_id,
        "prompt_prefix": prompt_prefix,
        "permission_mode": mode,
        "ts": ts,
    }
    entry.update(extra)
    return json.dumps(entry)


def _write_fixture():
    lines = [
        # Interactive session that started in plan.
        _line("sess-plan-001", "2026-06-05T16:14:57", "plan", "I need to study the tools"),
        # Interactive session started in bypass -> must be flagged.
        _line("sess-bypass-002", "2026-06-05T15:59:32", "bypassPermissions", "My CLAUDE.md is too large"),
        # Automated scheduled-task in bypass -> must NOT be flagged.
        _line("sess-auto-003", "2026-06-05T07:09:34", "bypassPermissions",
              "<scheduled-task name=\"daily\"", stage="automated_suppressed"),
        # Multi-prompt session: later entry in bypass, earlier (startup) in plan.
        _line("sess-multi-004", "2026-06-06T10:03:32", "bypassPermissions", "Proceed."),
        _line("sess-multi-004", "2026-06-06T09:30:00", "plan", "Start the next phase."),
        # A line with no session_id (e.g. json_parse_failed stage upstream).
        json.dumps({"stage": "json_parse_failed", "ts": "2026-06-06T11:00:00"}),
        # A malformed (non-JSON) line.
        "this is not json {{{",
        "",  # blank line, ignored
    ]
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8"
    )
    fd.write("\n".join(lines) + "\n")
    fd.close()
    return Path(fd.name)


def test_parse_counts_bad_lines() -> str:
    fixture = _write_fixture()
    try:
        sessions, stats = smr.parse_log(str(fixture))
    finally:
        fixture.unlink()
    if len(sessions) != 4:
        raise AssertionError("expected 4 sessions, got {}: {}".format(
            len(sessions), sorted(sessions)))
    if stats["malformed"] != 1:
        raise AssertionError("expected 1 malformed line, got {}".format(stats["malformed"]))
    if stats["no_session"] != 1:
        raise AssertionError("expected 1 no-session line, got {}".format(stats["no_session"]))
    return "4 sessions parsed; 1 malformed + 1 no-session line counted, not crashed"


def test_startup_mode_is_earliest_entry() -> str:
    fixture = _write_fixture()
    try:
        sessions, _ = smr.parse_log(str(fixture))
    finally:
        fixture.unlink()
    startup = sessions["sess-multi-004"]
    if startup["permission_mode"] != "plan":
        raise AssertionError(
            "multi-prompt startup mode should be the EARLIEST entry (plan), got {!r}".format(
                startup["permission_mode"]))
    if startup["ts"] != "2026-06-06T09:30:00":
        raise AssertionError("startup entry should be the earliest ts, got {!r}".format(startup["ts"]))
    return "startup mode resolves to the earliest-ts entry per session"


def test_classification_and_flagging() -> str:
    fixture = _write_fixture()
    try:
        sessions, _ = smr.parse_log(str(fixture))
    finally:
        fixture.unlink()
    rows = {r["session_id"]: r for r in smr.build_rows(sessions)}

    if rows["sess-plan-001"]["kind"] != "interactive" or rows["sess-plan-001"]["flagged"]:
        raise AssertionError("plan interactive session must be interactive and unflagged")
    if not rows["sess-bypass-002"]["flagged"]:
        raise AssertionError("interactive bypass session must be flagged")
    if rows["sess-bypass-002"]["kind"] != "interactive":
        raise AssertionError("bypass-002 should be interactive")
    if rows["sess-auto-003"]["kind"] != "automated":
        raise AssertionError("scheduled-task session must be classified automated")
    if rows["sess-auto-003"]["flagged"]:
        raise AssertionError("automated bypass session must NOT be flagged")
    return "interactive/automated classification and non-plan flagging correct"


def test_filters() -> str:
    fixture = _write_fixture()
    try:
        sessions, _ = smr.parse_log(str(fixture))
    finally:
        fixture.unlink()
    rows = smr.build_rows(sessions)

    non_plan = smr.filter_rows(rows, non_plan_only=True)
    if {r["session_id"] for r in non_plan} != {"sess-bypass-002"}:
        raise AssertionError("--non-plan-only should yield only the flagged interactive session, got {}".format(
            sorted(r["session_id"] for r in non_plan)))

    interactive = smr.filter_rows(rows, interactive_only=True)
    if any(r["kind"] == "automated" for r in interactive):
        raise AssertionError("--interactive-only must drop automated sessions")

    since = smr.filter_rows(rows, since="2026-06-06")
    if {r["session_id"] for r in since} != {"sess-multi-004"}:
        raise AssertionError("--since 2026-06-06 should yield only the 06-06 session, got {}".format(
            sorted(r["session_id"] for r in since)))
    return "--non-plan-only, --interactive-only, and --since filters work"


def test_main_runs_clean_on_fixture() -> str:
    fixture = _write_fixture()
    try:
        rc = smr.main(["--log", str(fixture)])
    finally:
        fixture.unlink()
    if rc != 0:
        raise AssertionError("main() should exit 0 on a readable fixture, got {}".format(rc))
    return "main() returns 0 against the fixture log"


def test_main_missing_log_returns_nonzero() -> str:
    rc = smr.main(["--log", "C:/nonexistent/definitely/not/here.log"])
    if rc == 0:
        raise AssertionError("main() should return non-zero when the log is missing")
    return "main() returns non-zero (and errors to stderr) on a missing log"


def main() -> int:
    tests = [
        ("parse counts malformed and no-session lines", test_parse_counts_bad_lines),
        ("startup mode is the earliest entry", test_startup_mode_is_earliest_entry),
        ("classification and flagging", test_classification_and_flagging),
        ("filters (since / interactive-only / non-plan-only)", test_filters),
        ("main runs clean on fixture", test_main_runs_clean_on_fixture),
        ("main returns non-zero on missing log", test_main_missing_log_returns_nonzero),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print("PASS: {}".format(name))
            print("      {}".format(detail))
        except AssertionError as e:
            failed += 1
            print("FAIL: {}".format(name))
            for line in str(e).splitlines():
                print("      {}".format(line))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR: {}: {}: {}".format(name, type(e).__name__, e))
    print()
    print("Tests: {} passed, 0 skipped, {} failed".format(len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
