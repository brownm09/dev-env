#!/usr/bin/env python3
"""Non-vacuity gate for the shared settings parser, tests/_hook_wiring.py (dev-env#1081).

Required when changing claude/scripts/tests/_hook_wiring.py's wired_scripts() or
wired_script_events(), or the scan-set functions of test_hook_output_contract.py or
test_hook_heartbeat_guard.py.
Run: py -3 claude/scripts/tests/test_hook_wiring_nonempty.py

Three malformed `hooks` blocks each drive hook_entries() to zero entries. Before
dev-env#1081, wired_scripts() then returned [] and items 61 and 68 passed against no
hooks at all. This pins that wired_scripts(), wired_script_events() and both gates'
scan-set functions raise on each shape, that hook_entries() still returns [] (the
doctor reports that case itself), and that the real settings still parse.
"""
from __future__ import annotations

import copy
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import _hook_wiring as wiring  # noqa: E402
import test_hook_heartbeat_guard as heartbeat_gate  # noqa: E402  (item 68)
import test_hook_output_contract as contract_gate  # noqa: E402  (item 61)


def _malformed() -> dict[str, dict]:
    real = wiring.load_settings()
    renamed = copy.deepcopy(real)
    renamed["hookConfigs"] = renamed.pop("hooks")
    as_list = copy.deepcopy(real)
    as_list["hooks"] = []
    groups_as_dict = copy.deepcopy(real)
    groups_as_dict["hooks"] = {
        event: {str(i): g for i, g in enumerate(groups)}
        for event, groups in groups_as_dict["hooks"].items()
    }
    return {
        "renamed-hooks-key": renamed,
        "hooks-as-list": as_list,
        "groups-as-dict": groups_as_dict,
    }


CALLS = {
    "_hook_wiring.wired_scripts": wiring.wired_scripts,
    "_hook_wiring.wired_script_events": wiring.wired_script_events,
    "item 61 _scan_output_contract": contract_gate._scan_output_contract,
    "item 61 _scan_nonascii": contract_gate._scan_nonascii,
    "item 68 _scan_real_repo": heartbeat_gate._scan_real_repo,
}


def main() -> int:
    failed = 0
    total = 0
    for shape, settings in _malformed().items():
        total += 1
        entries = wiring.hook_entries(settings)
        if entries:
            failed += 1
            print(f"FAIL: {shape}: fixture is not malformed, hook_entries() gave {len(entries)}")
        else:
            print(f"PASS: {shape}: hook_entries() returns [] (doctor reports its own empty case)")
        for name, fn in CALLS.items():
            total += 1
            try:
                result = fn(settings)
            except ValueError as e:
                msg = str(e)
                if "hooks" in msg and "claude/settings.shared.json" in msg:
                    print(f"PASS: {shape}: {name} raises")
                else:
                    failed += 1
                    print(f"FAIL: {shape}: {name} raised without naming hooks and the file: {msg}")
            else:
                failed += 1
                print(f"FAIL: {shape}: {name} returned {result!r} instead of raising")
    total += 1
    n = len(wiring.wired_scripts(wiring.load_settings()))
    if n:
        print(f"PASS: real claude/settings.shared.json yields {n} wired scripts")
    else:
        failed += 1
        print("FAIL: real claude/settings.shared.json yields no wired scripts")
    print()
    print(f"Tests: {total - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
