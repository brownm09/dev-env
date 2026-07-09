#!/usr/bin/env python3
"""Unit tests for idle-refresher.py's pure helpers.

`idle-refresher.py` is a UserPromptSubmit hook that injects a "give the user a
refresher" cue when the user returns after a long idle gap (dev-env#655,
ADR-095). This suite exercises the pure, offline helpers extracted for exactly
this purpose — ISO-timestamp parsing, the last-assistant idle anchor, gap
calculation, the threshold decision, config load + default, the automated-prompt
skip, and the ASCII/cp1252-safety of the injected text — matching the repo's
fixture-only test convention (test_disk_space_check.py). main()'s stdin plumbing
and the live transcript read are intentionally not covered (they touch real
stdin/disk and the repo avoids mocking those boundaries; the reads go through
the already-tested _hookutil.load_records / find_transcript).

Usage:
    py -3 claude/scripts/tests/test_idle_refresher.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "idle-refresher.py"

# The script imports _hookutil (a sibling in scripts/); make it resolvable
# before exec_module runs the module-level import.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("idle_refresher", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
ir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ir)  # safe: main() is guarded by __main__

DEFAULT_MINUTES = ir.DEFAULT_MINUTES  # 60


def test_parse_iso_z_and_offset_agree() -> str:
    z = ir.parse_iso_to_epoch("2026-07-09T17:53:30Z")
    off = ir.parse_iso_to_epoch("2026-07-09T17:53:30+00:00")
    assert z is not None and off is not None, "both forms must parse"
    assert z == off, f"Z and +00:00 must give the same epoch ({z} != {off})"
    # A one-hour-later stamp is exactly 3600s further along.
    later = ir.parse_iso_to_epoch("2026-07-09T18:53:30Z")
    assert later - z == 3600.0, f"one hour apart must be 3600s, got {later - z}"
    return "Z / +00:00 agree; +1h == +3600s"


def test_parse_iso_microseconds_and_naive() -> str:
    frac = ir.parse_iso_to_epoch("2026-07-09T17:53:30.670Z")
    assert frac is not None, "microsecond form must parse"
    # A naive (no-zone) stamp is treated as UTC, so it matches the Z form.
    naive = ir.parse_iso_to_epoch("2026-07-09T17:53:30")
    zed = ir.parse_iso_to_epoch("2026-07-09T17:53:30Z")
    assert naive == zed, f"naive stamp must be read as UTC ({naive} != {zed})"
    return "microseconds parse; naive stamp assumed UTC"


def test_parse_iso_bad_input_is_none() -> str:
    for bad in (None, "", "   ", "not-a-timestamp", 1720000000, "2026-13-40T99:99:99Z"):
        assert ir.parse_iso_to_epoch(bad) is None, f"{bad!r} must parse to None"
    return "None/empty/garbage/non-str -> None"


def test_last_activity_picks_last_assistant() -> str:
    records = [
        {"type": "user", "timestamp": "2026-07-09T10:00:00Z"},
        {"type": "assistant", "timestamp": "2026-07-09T10:01:00Z"},
        {"type": "user", "timestamp": "2026-07-09T10:02:00Z"},  # tool_result — must be ignored
        {"type": "assistant", "timestamp": "2026-07-09T10:03:00Z"},  # end of last turn
    ]
    got = ir.last_activity_epoch(records)
    expected = ir.parse_iso_to_epoch("2026-07-09T10:03:00Z")
    assert got == expected, f"must anchor on the LAST assistant record ({got} != {expected})"
    return "last assistant record's timestamp is the anchor (user records ignored)"


def test_last_activity_none_without_assistant() -> str:
    # First prompt of a session: only the user's prompt (and meta) exist, no assistant turn.
    records = [
        {"type": "user", "timestamp": "2026-07-09T10:00:00Z"},
        {"type": "queue-operation", "timestamp": "2026-07-09T10:00:01Z"},
    ]
    assert ir.last_activity_epoch(records) is None, "no assistant record -> None"
    assert ir.last_activity_epoch([]) is None, "empty transcript -> None"
    assert not ir.has_prior_assistant_turn(records), "no prior assistant turn"
    assert ir.has_prior_assistant_turn([{"type": "assistant", "timestamp": "x"}]), "assistant present"
    return "no assistant record -> None / has_prior_assistant_turn False"


def test_compute_gap_seconds() -> str:
    assert ir.compute_gap_seconds(1000.0, 400.0) == 600.0, "gap is now - last"
    assert ir.compute_gap_seconds(1000.0, None) is None, "unknown last -> None gap"
    return "gap = now - last; None last -> None"


def test_should_refresh_boundary() -> str:
    thresh = 60 * 60  # 3600s == 60 min
    assert ir.should_refresh(thresh, thresh) is False, "exactly at threshold must NOT fire (strict >)"
    assert ir.should_refresh(thresh + 1, thresh) is True, "just over threshold fires"
    assert ir.should_refresh(thresh - 1, thresh) is False, "just under threshold does not fire"
    assert ir.should_refresh(None, thresh) is False, "None gap never fires"
    return "boundary: ==thresh -> no; >thresh -> yes; <thresh -> no; None -> no"


def test_load_threshold_configured() -> str:
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w", encoding="utf-8") as f:
            json.dump({"idle_refresher_minutes": 30}, f)
        assert ir.load_threshold_minutes(d) == 30, "configured override must be honored"
    return "idle_refresher_minutes override honored (30)"


def test_load_threshold_defaults() -> str:
    # No config directory at all.
    with tempfile.TemporaryDirectory() as d:
        assert ir.load_threshold_minutes(d) == DEFAULT_MINUTES, "missing config -> default 60"
    # Config present but no key.
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w", encoding="utf-8") as f:
            json.dump({"turn_threshold": 12}, f)
        assert ir.load_threshold_minutes(d) == DEFAULT_MINUTES, "absent key -> default 60"
    # Malformed JSON.
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, ".claude"))
        with open(os.path.join(d, ".claude", "hook-config.json"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert ir.load_threshold_minutes(d) == DEFAULT_MINUTES, "malformed JSON -> default 60"
    return "missing / absent-key / malformed all -> default 60"


def test_is_automated_prompt() -> str:
    assert ir.is_automated_prompt("<scheduled-task>run</scheduled-task>") is True, "XML tag -> automated"
    assert ir.is_automated_prompt("   <ci-monitor-event>") is True, "leading whitespace still matches"
    assert ir.is_automated_prompt("Please continue the refactor") is False, "human prose -> not automated"
    assert ir.is_automated_prompt("") is False, "empty -> not automated"
    assert ir.is_automated_prompt("<UPPER>") is False, "uppercase tag not matched (lowercase-initial only)"
    return "XML-tagged (with leading ws) -> automated; prose/empty/UPPER -> not"


def test_humanize_gap() -> str:
    assert ir.humanize_gap(72 * 60) == "72 minutes", "under 120 min -> minutes"
    assert ir.humanize_gap(119 * 60) == "119 minutes", "119 min stays minutes"
    assert ir.humanize_gap(120 * 60) == "2 hours", "120 min -> hours"
    assert ir.humanize_gap(3 * 3600 + 5) == "3 hours", "3h+ -> hours (floored)"
    return "minutes under 120; hours at/after 120 min"


def test_refresher_context_is_cp1252_safe() -> str:
    # Claude Code pipes hook stdout as cp1252; a char outside it (an arrow, an
    # em-dash) makes the write raise and the cue vanish through the exit-0 guard.
    # Pin the injected text ASCII/cp1252-encodable so that can't regress.
    for gap in (61 * 60, 3 * 3600):
        msg = ir.build_refresher_context(gap)
        msg.encode("cp1252")  # raises UnicodeEncodeError on a non-cp1252 char
        assert msg.isascii(), f"cue must be ASCII, got non-ASCII: {msg!r}"
        assert "refresher" in msg.lower(), "cue must actually mention a refresher"
    return "injected cue is ASCII / cp1252-encodable (won't vanish under cp1252 stdout)"


def main() -> int:
    tests = [
        ("parse ISO: Z and offset agree", test_parse_iso_z_and_offset_agree),
        ("parse ISO: microseconds + naive-as-UTC", test_parse_iso_microseconds_and_naive),
        ("parse ISO: bad input -> None", test_parse_iso_bad_input_is_none),
        ("last_activity picks last assistant", test_last_activity_picks_last_assistant),
        ("last_activity None without assistant", test_last_activity_none_without_assistant),
        ("compute_gap_seconds", test_compute_gap_seconds),
        ("should_refresh boundary (strict >)", test_should_refresh_boundary),
        ("load_threshold configured override", test_load_threshold_configured),
        ("load_threshold defaults to 60", test_load_threshold_defaults),
        ("is_automated_prompt", test_is_automated_prompt),
        ("humanize_gap minutes/hours", test_humanize_gap),
        ("refresher cue is cp1252-safe", test_refresher_context_is_cp1252_safe),
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
