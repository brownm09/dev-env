#!/usr/bin/env python3
"""Unit tests for worktree-npm-install.py's pre-install free-space gate.

`worktree-npm-install.py` runs `npm ci`/`npm install` unattended in Claude-managed
worktrees. Before dev-env#364 it ran with no free-space check — an install into a
near-full C: drive could silently truncate node_modules (npm reports exit 0, yet a
native binary is partially extracted), surfacing hours later as misleading
downstream errors.

The fix extracts the gate decision into the pure `install_decision()` helper so the
threshold/laddering logic can be exercised offline (no disk, no network, no npm),
matching the repo's fixture-only test convention (test_usage_snapshot.py). The
synchronous reclamation ladder and the real install are intentionally not tested —
they shell out and the repo avoids subprocess mocks.

Usage:
    py -3 claude/scripts/tests/test_worktree_npm_install.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "worktree-npm-install.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("worktree_npm_install", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
wni = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wni)  # safe: main() is guarded by __main__
install_decision = wni.install_decision

INSTALL_FLOOR = wni.INSTALL_FLOOR_GB  # 10.0
HARD_FLOOR = wni.HARD_FLOOR_GB        # 5.0


def test_ample_space_proceeds() -> str:
    assert install_decision(50.0) == "proceed", "50 GB free must proceed"
    return "50 GB free -> proceed (today's behavior, no reclaim)"


def test_install_floor_boundary_is_proceed() -> str:
    # >= INSTALL_FLOOR proceeds (boundary inclusive); just under triggers reclaim.
    assert install_decision(INSTALL_FLOOR) == "proceed", "exactly the floor proceeds"
    assert install_decision(INSTALL_FLOOR - 0.1) == "reclaim-first", "just under -> reclaim"
    return f"free == {INSTALL_FLOOR:.0f} GB -> proceed; {INSTALL_FLOOR - 0.1:.1f} GB -> reclaim-first"


def test_low_space_reclaims_first() -> str:
    assert install_decision(8.0) == "reclaim-first", "8 GB free must reclaim before deciding"
    return "8 GB free, no reclaim figure yet -> reclaim-first"


def test_reclaim_recovered_proceeds() -> str:
    # Started low, ladder recovered to >= HARD_FLOOR -> proceed.
    assert install_decision(8.0, HARD_FLOOR + 1.0) == "proceed", "recovered above hard floor -> proceed"
    return f"8 GB -> reclaimed to {HARD_FLOOR + 1.0:.0f} GB -> proceed"


def test_hard_floor_boundary_proceeds() -> str:
    # Exactly the hard floor after reclaim is acceptable (>=, not >).
    assert install_decision(8.0, HARD_FLOOR) == "proceed", "exactly the hard floor proceeds"
    return f"reclaimed to exactly {HARD_FLOOR:.0f} GB -> proceed (boundary inclusive)"


def test_still_low_after_reclaim_aborts() -> str:
    # Ladder ran but space is still below the hard floor -> refuse the install.
    assert install_decision(8.0, HARD_FLOOR - 0.1) == "abort", "still below hard floor -> abort"
    return f"reclaimed to {HARD_FLOOR - 0.1:.1f} GB (< {HARD_FLOOR:.0f}) -> abort (refuse, do not truncate)"


def main() -> int:
    tests = [
        ("ample space proceeds", test_ample_space_proceeds),
        ("install-floor boundary is proceed", test_install_floor_boundary_is_proceed),
        ("low space reclaims first", test_low_space_reclaims_first),
        ("recovered above hard floor proceeds", test_reclaim_recovered_proceeds),
        ("hard-floor boundary proceeds", test_hard_floor_boundary_proceeds),
        ("still low after reclaim aborts", test_still_low_after_reclaim_aborts),
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
