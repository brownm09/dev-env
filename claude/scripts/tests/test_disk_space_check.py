#!/usr/bin/env python3
"""Unit tests for disk-space-check.py's free-space classification.

`disk-space-check.py` is now registered under both UserPromptSubmit and
PreToolUse(Bash) (dev-env#592, ADR-087) — the same script, unmodified, reused
for both hook events since main() already only reads session_id/cwd from
stdin. This test exercises the pure `classify_free_space()` helper extracted
from main()'s inline if/elif so the threshold boundaries are pinned offline
(no disk, no network, no subprocess), matching the repo's fixture-only test
convention (test_worktree_npm_install.py). The disk_usage syscall, marker-file
I/O, and the detached reclaim spawn are intentionally not tested — they touch
real disk/process state and the repo avoids mocking those boundaries.

Usage:
    py -3 claude/scripts/tests/test_disk_space_check.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "disk-space-check.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("disk_space_check", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
dsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsc)  # safe: main() is guarded by __main__
classify_free_space = dsc.classify_free_space

WARN_GB = dsc.WARN_GB  # 20.0
ACT_GB = dsc.ACT_GB    # 10.0


def test_ample_space_is_ok() -> str:
    assert classify_free_space(50.0, WARN_GB, ACT_GB) == "ok", "50 GB free must be ok"
    return "50 GB free -> ok (no warning, no reclaim)"


def test_warn_boundary_is_ok() -> str:
    # Exactly WARN_GB free has not yet crossed into "warn" (boundary inclusive on the ok side).
    assert classify_free_space(WARN_GB, WARN_GB, ACT_GB) == "ok", "exactly WARN_GB free is still ok"
    assert classify_free_space(WARN_GB - 0.1, WARN_GB, ACT_GB) == "warn", "just under WARN_GB -> warn"
    return f"free == {WARN_GB:.0f} GB -> ok; {WARN_GB - 0.1:.1f} GB -> warn"


def test_mid_band_is_warn() -> str:
    assert classify_free_space(15.0, WARN_GB, ACT_GB) == "warn", "15 GB free (between thresholds) must be warn"
    return "15 GB free -> warn"


def test_act_boundary_is_warn() -> str:
    # Exactly ACT_GB free has not yet crossed into "act" (boundary inclusive on the warn side).
    assert classify_free_space(ACT_GB, WARN_GB, ACT_GB) == "warn", "exactly ACT_GB free is still warn, not act"
    assert classify_free_space(ACT_GB - 0.1, WARN_GB, ACT_GB) == "act", "just under ACT_GB -> act"
    return f"free == {ACT_GB:.0f} GB -> warn; {ACT_GB - 0.1:.1f} GB -> act"


def test_low_space_is_act() -> str:
    assert classify_free_space(2.0, WARN_GB, ACT_GB) == "act", "2 GB free must be act"
    return "2 GB free -> act (reclaim spawned)"


def test_zero_free_is_act() -> str:
    assert classify_free_space(0.0, WARN_GB, ACT_GB) == "act", "0 GB free must be act"
    return "0 GB free -> act"


def main() -> int:
    tests = [
        ("ample space is ok", test_ample_space_is_ok),
        ("warn boundary is ok, just under is warn", test_warn_boundary_is_ok),
        ("mid band is warn", test_mid_band_is_warn),
        ("act boundary is warn, just under is act", test_act_boundary_is_warn),
        ("low space is act", test_low_space_is_act),
        ("zero free is act", test_zero_free_is_act),
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
