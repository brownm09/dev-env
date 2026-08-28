#!/usr/bin/env python3
"""Unit tests for worktree-npm-install.py's pre-install free-space gate and its
node_modules-truncation audit.

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

Truncation audit (dev-env#970, ADR-142)
---------------------------------------
The same split applies: `classify_package_dir()`, `truncation_verdict()` and
`is_staging_name()` are pure and tested exhaustively, while `scan_node_modules()`
is exercised against real temp-directory fixtures — the precedent is
test_reclaim_worktree_disk.py, which builds a whole fake worktree the same way.

Several thresholds here are calibration constants rather than round numbers, so the
cases that pin them cite the measured figure they came from (48 real node_modules
trees, 2026-08-27). A test that merely re-asserted `0.50 == 0.50` would not notice
the thing worth noticing: that the benign ceiling actually observed in the corpus
was 21.3%, and a future edit narrowing the margin toward it is the regression.

`_audit_existing_tree()` itself is not unit-tested — it shells out to `npm ci` and
writes sentinels under the real ~/.claude/scratch, the two things this suite's
convention keeps out. Its decision logic lives entirely in the pure helpers above,
and its live behaviour was verified end-to-end against known-good and known-bad
trees before merge (see ADR-142).

Usage:
    py -3 claude/scripts/tests/test_worktree_npm_install.py

Exit 0 = all pass.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
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
classify_package_dir = wni.classify_package_dir
truncation_verdict = wni.truncation_verdict
is_staging_name = wni.is_staging_name
scan_node_modules = wni.scan_node_modules

INSTALL_FLOOR = wni.INSTALL_FLOOR_GB  # 10.0
HARD_FLOOR = wni.HARD_FLOOR_GB        # 5.0
RATIO_FLOOR = wni.EMPTY_SHELL_RATIO_FLOOR  # 0.50

# The worst benign empty-shell ratio measured across the 48-tree calibration corpus
# (2026-08-27): cover-letter-runtime's reverent-kowalevski worktree, whose empties are
# all optional platform deps npm correctly skipped. RATIO_FLOOR must stay clear of it.
MEASURED_BENIGN_CEILING = 0.213


class _Tree:
    """A throwaway node_modules fixture. Used as a context manager."""

    def __init__(self):
        self._tmp = None
        self.root = None

    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="wni_nm_")
        self.root = Path(self._tmp) / "node_modules"
        self.root.mkdir(parents=True)
        return self

    def __exit__(self, *_exc):
        shutil.rmtree(self._tmp, ignore_errors=True)
        return False

    def package(self, name, *, package_json=True, contents=False):
        """Create a package dir: healthy, an empty shell, or a partial extraction."""
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        if package_json:
            (d / "package.json").write_text('{"name":"x"}', encoding="utf-8")
        if contents:
            (d / "lib").mkdir(exist_ok=True)
        return d

    def bare(self, name):
        """Create a non-package entry (npm bookkeeping, a stray file)."""
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        return d


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


def test_classify_covers_the_four_shapes() -> str:
    # A workspace link is classified before anything else is even looked at: it points
    # into the repo, so its contents say nothing about whether the install completed.
    assert classify_package_dir(True, False, True) == "link", "link wins over everything"
    assert classify_package_dir(True, True, False) == "link", "link wins over everything"
    assert classify_package_dir(False, True, False) == "ok", "has package.json -> ok"
    assert classify_package_dir(False, False, True) == "empty-shell", "nothing there"
    assert classify_package_dir(False, False, False) == "partial", "something, but no manifest"
    return "link / ok / empty-shell / partial all distinguished"


def test_partial_drives_repair() -> str:
    # One partial is enough — this is the signal with a measured 0/38 false-positive
    # rate, so it does not need corroboration from the ratio.
    assert truncation_verdict(300, 0, 1) == "repair", "a single partial must repair"
    assert truncation_verdict(300, 299, 1) == "repair", "partial outranks the ratio arm"
    return "partial >= 1 -> repair, regardless of the empty-shell ratio"


def test_measured_benign_ceiling_stays_ok() -> str:
    # The regression this pins is a future edit sliding RATIO_FLOOR down toward the
    # worst benign tree actually observed, not the arithmetic of the comparison.
    checked = 1000
    empties = int(MEASURED_BENIGN_CEILING * checked)
    assert truncation_verdict(checked, empties, 0) == "ok", (
        f"the measured benign ceiling ({MEASURED_BENIGN_CEILING:.1%}) must not fire; "
        f"RATIO_FLOOR is {RATIO_FLOOR:.2f}"
    )
    assert RATIO_FLOOR > MEASURED_BENIGN_CEILING, "floor must sit above the benign ceiling"
    margin = RATIO_FLOOR / MEASURED_BENIGN_CEILING
    return (
        f"{MEASURED_BENIGN_CEILING:.1%} empty (optional platform deps) -> ok; "
        f"floor {RATIO_FLOOR:.2f} keeps a {margin:.1f}x margin"
    )


def test_empty_shell_ratio_boundary_advises() -> str:
    # >= the floor, not >, matching install_decision()'s inclusive-boundary style.
    assert truncation_verdict(100, int(RATIO_FLOOR * 100), 0) == "advise", "at the floor -> advise"
    assert truncation_verdict(100, int(RATIO_FLOOR * 100) - 1, 0) == "ok", "just under -> ok"
    return f"empty ratio == {RATIO_FLOOR:.2f} -> advise; one package under -> ok"


def test_ratio_arm_never_repairs() -> str:
    # The whole point of the split: an uncalibrated signal diagnoses, it never runs a
    # destructive reinstall. A 100%-empty tree with no partials still only advises.
    assert truncation_verdict(500, 500, 0) == "advise", "100% empty must advise, not repair"
    return "100% empty shells with no partial -> advise (uncalibrated arm cannot repair)"


def test_empty_tree_advises_without_dividing_by_zero() -> str:
    assert truncation_verdict(0, 0, 0) == "advise", "no packages at all is suspicious"
    return "checked == 0 -> advise (and no ZeroDivisionError)"


def test_staging_names_are_recognized() -> str:
    # Observed live on 2026-08-27 mid-install: `.core-YZEumUMX`, `.schematics-cli-SI9Wl1c1`.
    assert is_staging_name(".core-YZEumUMX", False), "top-level staging suffix"
    assert is_staging_name(".anything-at-all", True), "any dot entry inside a scope"
    for benign in (".bin", ".cache", ".prisma", ".vite", ".vite-temp", ".package-lock.json"):
        assert not is_staging_name(benign, False), f"{benign} is npm bookkeeping, not staging"
    assert not is_staging_name("react", False), "a real package is never staging"
    return "npm `.<pkg>-XXXXXXXX` recognized; .bin/.cache/.vite-temp left alone"


def test_scan_healthy_tree_is_clean() -> str:
    with _Tree() as t:
        t.package("react")
        t.package("@babel/core")
        t.bare(".bin")
        (t.root / ".package-lock.json").write_text("{}", encoding="utf-8")
        checked, empty, partials, staging = scan_node_modules(t.root)
    assert (checked, empty, partials, staging) == (2, 0, [], 0), (
        f"healthy tree scanned as {(checked, empty, partials, staging)}"
    )
    assert truncation_verdict(checked, empty, len(partials)) == "ok", "healthy -> ok"
    return "2 packages (one scoped), dot-entries ignored, verdict ok"


def test_scan_separates_partial_from_empty_shell() -> str:
    with _Tree() as t:
        t.package("react")
        t.package("zod", package_json=False, contents=True)          # the #945 shape
        t.package("@esbuild/linux-x64", package_json=False)          # skipped platform dep
        checked, empty, partials, staging = scan_node_modules(t.root)
    assert checked == 3, f"expected 3 packages, got {checked}"
    assert partials == ["zod"], f"expected only zod partial, got {partials}"
    assert empty == 1, f"expected 1 empty shell, got {empty}"
    assert staging == 0, "no staging dirs in this fixture"
    return "zod (contents, no manifest) -> partial; @esbuild stub -> empty shell"


def test_scan_counts_staging_instead_of_partials() -> str:
    with _Tree() as t:
        t.package("react")
        t.bare("@babel")
        t.package("@babel/.core-plYEPuLa", package_json=False, contents=True)
        checked, empty, partials, staging = scan_node_modules(t.root)
    assert staging == 1, f"expected 1 staging dir, got {staging}"
    assert partials == [], f"a live extraction must not be reported as partial: {partials}"
    assert checked == 1, f"staging dirs are not packages; checked={checked}"
    return "`@babel/.core-plYEPuLa` counted as staging, never as a partial"


def test_scan_unreadable_tree_returns_none() -> str:
    # A measurement failure must be distinguishable from "measured, found nothing" —
    # otherwise an unreadable tree would advise via the checked == 0 arm.
    missing = Path(tempfile.gettempdir()) / "wni_definitely_not_here_970"
    assert scan_node_modules(missing) is None, "unreadable tree must return None, not (0,0,[],0)"
    return "unreadable node_modules -> None (fails open, never advises)"


def test_scan_skips_workspace_links() -> str:
    with _Tree() as t:
        t.package("react")
        target = Path(t._tmp) / "packages" / "core"
        target.mkdir(parents=True)
        # No package.json in the target: if the link were followed and counted it would
        # register as an empty shell, so a regression here is visible, not silent.
        try:
            os.symlink(target, t.root / "linked-pkg", target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as exc:
            checked, empty, partials, staging = scan_node_modules(t.root)
            assert (checked, empty, partials) == (1, 0, []), "base fixture must still scan clean"
            return (
                f"symlink creation unavailable on this platform ({type(exc).__name__}); "
                "link handling covered by test_classify_covers_the_four_shapes instead"
            )
        checked, empty, partials, staging = scan_node_modules(t.root)
    assert checked == 1, f"the workspace link must not be counted as a package: checked={checked}"
    assert empty == 0 and partials == [], f"link leaked into the counts: {empty}, {partials}"
    return "symlinked workspace package skipped entirely (not counted, not flagged)"


def main() -> int:
    tests = [
        ("ample space proceeds", test_ample_space_proceeds),
        ("install-floor boundary is proceed", test_install_floor_boundary_is_proceed),
        ("low space reclaims first", test_low_space_reclaims_first),
        ("recovered above hard floor proceeds", test_reclaim_recovered_proceeds),
        ("hard-floor boundary proceeds", test_hard_floor_boundary_proceeds),
        ("still low after reclaim aborts", test_still_low_after_reclaim_aborts),
        ("classify covers the four shapes", test_classify_covers_the_four_shapes),
        ("partial drives repair", test_partial_drives_repair),
        ("measured benign ceiling stays ok", test_measured_benign_ceiling_stays_ok),
        ("empty-shell ratio boundary advises", test_empty_shell_ratio_boundary_advises),
        ("ratio arm never repairs", test_ratio_arm_never_repairs),
        ("empty tree advises safely", test_empty_tree_advises_without_dividing_by_zero),
        ("staging names are recognized", test_staging_names_are_recognized),
        ("scan: healthy tree is clean", test_scan_healthy_tree_is_clean),
        ("scan: partial vs empty shell", test_scan_separates_partial_from_empty_shell),
        ("scan: staging not partial", test_scan_counts_staging_instead_of_partials),
        ("scan: unreadable tree -> None", test_scan_unreadable_tree_returns_none),
        ("scan: workspace links skipped", test_scan_skips_workspace_links),
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
