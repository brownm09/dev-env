#!/usr/bin/env python3
"""Unit tests for post-pr-merge-reclaim.py's merge-detection predicate.

`post-pr-merge-reclaim.py` is a PostToolUse hook that, after a successful
`gh pr merge`, spawns reclaim-worktree-disk.py to strip regenerable node_modules
from now-idle worktrees (dev-env#364). The detection of "was this a successful
merge?" is extracted into the pure `is_successful_merge()` predicate so it can be
exercised offline (no subprocess spawn, no git/gh), matching the repo's
fixture-only test convention.

The detached spawn (`_spawn_reclaim`) is intentionally not tested — it shells out
and the repo avoids subprocess mocks.

Usage:
    py -3 claude/scripts/tests/test_post_pr_merge_reclaim.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-pr-merge-reclaim.py"

# The script imports _winsubp (a sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_pr_merge_reclaim", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
ppmr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ppmr)  # safe: main() is guarded by __main__
is_successful_merge = ppmr.is_successful_merge


def test_clean_merge_exit_zero() -> str:
    assert is_successful_merge("gh pr merge 364 --squash --delete-branch", 0, "")
    return "gh pr merge + exit 0 -> reclaim"


def test_worktree_merge_nonzero_but_marker() -> str:
    # From a worktree gh exits non-zero on local-checkout cleanup; stdout marker
    # confirms the remote merge succeeded (issue #275 behavior, mirrored here).
    assert is_successful_merge(
        "gh pr merge 364 --squash --delete-branch", 1,
        "Squashed and merged pull request #364",
    )
    return "gh pr merge + exit 1 + 'Squashed and merged' marker -> reclaim"


def test_non_merge_command_ignored() -> str:
    assert not is_successful_merge("gh pr create --fill", 0, "")
    assert not is_successful_merge("npm test", 0, "")
    return "non-merge commands -> no-op"


def test_failed_merge_no_marker_ignored() -> str:
    # A genuine merge failure (non-zero, no success marker) must not reclaim.
    assert not is_successful_merge(
        "gh pr merge 364 --squash", 1, "X Pull request #364 is not mergeable",
    )
    return "gh pr merge failed (exit 1, no success marker) -> no-op"


def main() -> int:
    tests = [
        ("clean merge (exit 0) reclaims", test_clean_merge_exit_zero),
        ("worktree merge (exit 1 + marker) reclaims", test_worktree_merge_nonzero_but_marker),
        ("non-merge command ignored", test_non_merge_command_ignored),
        ("failed merge with no marker ignored", test_failed_merge_no_marker_ignored),
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
