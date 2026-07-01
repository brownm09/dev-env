#!/usr/bin/env python3
"""Unit tests for usage-snapshot.py token classification and merge detection.

`usage-snapshot.py` is a PostToolUse hook that fires after `gh pr merge` and
emits a usage snapshot. Before the fix in dev-env#355, an already-expired OAuth
token made the hook `sys.exit(0)` silently — the feature went dead with no
signal, even though an "expires within 1 hour" warning sat one branch below.

The fix extracts the expiry decision into the pure `classify_token()` helper so
it can be exercised offline (no network, no credentials file), matching the
repo's fixture-only test convention. These tests pin the four states and assert
the previously-silent `expired` path now yields a user-facing advisory.

dev-env#474 fixed a second, separate silent-drop path: the hook used to gate on
`tool_response.exitCode != 0`, which discards the snapshot on every worktree
merge (a worktree merge exits non-zero on local branch cleanup even though the
remote merge succeeded — issue #275), the exact defect ADR-049/ADR-050 fixed in
the sibling `post-pr-merge-*` hooks. The fix replaces that gate with the pure
`merge_confirmed(command, output)` predicate, gated on gh's output success
marker instead of the exit code. These tests pin that a worktree-merge payload
(marker present, exit non-zero) now confirms, while an unconfirmed merge (no
marker) and a non-merge command do not.

The live network call (`fetch_usage`) is intentionally not tested — the repo
avoids urllib mocks, consistent with the other script tests.

Usage:
    py -3 claude/scripts/tests/test_usage_snapshot.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "usage-snapshot.py"

# The script now imports _winsubp (a sibling in scripts/); make it resolvable
# when exec_module runs the module body.
sys.path.insert(0, str(SCRIPT.parent))

# The script filename is hyphenated, so import it by path rather than `import`.
_spec = importlib.util.spec_from_file_location("usage_snapshot", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
usage_snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage_snapshot)  # safe: main() is guarded by __main__
classify_token = usage_snapshot.classify_token
snapshot_action = usage_snapshot.snapshot_action
merge_confirmed = usage_snapshot.merge_confirmed

NOW_MS = 1_700_000_000_000  # fixed synthetic "now"; real time never consulted
HOUR_MS = 3_600_000
DAY_MS = 86_400_000


def test_no_expiry_proceeds_silently() -> str:
    state, advisory = classify_token(0, NOW_MS)
    assert state == "no_expiry", f"expected no_expiry, got {state}"
    assert advisory == "", f"expected empty advisory, got {advisory!r}"
    return "expires_at_ms=0 -> no_expiry, no advisory (preserves prior behavior)"


def test_valid_token_proceeds_silently() -> str:
    state, advisory = classify_token(NOW_MS + 5 * HOUR_MS, NOW_MS)
    assert state == "ok", f"expected ok, got {state}"
    assert advisory == "", f"expected empty advisory, got {advisory!r}"
    return "5h remaining -> ok, no advisory"


def test_expiring_within_hour_warns() -> str:
    state, advisory = classify_token(NOW_MS + 30 * 60_000, NOW_MS)
    assert state == "expiring", f"expected expiring, got {state}"
    assert "within 1 hour" in advisory, f"advisory missing warning text: {advisory!r}"
    return "30m remaining -> expiring, within-1h advisory"


def test_expired_token_now_visible() -> str:
    state, advisory = classify_token(NOW_MS - 8 * DAY_MS, NOW_MS)
    assert state == "expired", f"expected expired, got {state}"
    assert "expired" in advisory, f"advisory missing 'expired': {advisory!r}"
    assert "8.0 days ago" in advisory, f"advisory missing days-ago figure: {advisory!r}"
    return "expired 8 days ago -> expired, advisory names the cause (was silent before #355)"


def test_exact_expiry_boundary_counts_as_expired() -> str:
    # expires_at_ms == now_ms must classify as expired (<=, not <).
    state, _ = classify_token(NOW_MS, NOW_MS)
    assert state == "expired", f"boundary should be expired, got {state}"
    return "expires_at_ms == now_ms -> expired (boundary inclusive)"


def test_expired_state_triggers_refresh() -> str:
    assert snapshot_action("expired") == "refresh", "expired must trigger an on-demand refresh"
    return "expired -> refresh (on-demand CLI refresh before giving up)"


def test_expiring_state_now_fetches() -> str:
    # Behavior change (dev-env#361): an expiring-but-valid token is used, not skipped.
    assert snapshot_action("expiring") == "fetch", "expiring is still valid -> fetch, not block"
    return "expiring -> fetch (valid token no longer discarded)"


def test_valid_states_fetch() -> str:
    for st in ("ok", "no_expiry"):
        assert snapshot_action(st) == "fetch", f"{st} should fetch, got {snapshot_action(st)}"
    return "ok / no_expiry -> fetch"


def test_merge_confirmed_true_for_worktree_merge_despite_nonzero_exit() -> str:
    # Issue #275: a worktree merge exits non-zero on local branch cleanup
    # ("'main' is already checked out") even though the remote merge
    # succeeded. gh prints the success marker before that cleanup tail runs.
    command = "gh pr merge --squash --delete-branch"
    output = (
        "Squashed and merged pull request #466 (fix: correct scheduled-tasks/routines "
        "junction topology in docs)\n"
        "error: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    )
    assert merge_confirmed(command, output) is True
    return "worktree-merge output (marker present, exit non-zero) -> confirmed"


def test_merge_confirmed_false_without_marker() -> str:
    # A queued --auto exits 0 but has not actually merged yet.
    command = "gh pr merge --auto --squash --delete-branch"
    output = "Pull request #466 will be automatically merged when checks pass"
    assert merge_confirmed(command, output) is False
    return "queued --auto output (no marker yet) -> not confirmed"


def test_merge_confirmed_false_for_non_merge_command() -> str:
    command = "git push origin main"
    output = "Squashed and merged pull request #466 (unrelated coincidental text)"
    assert merge_confirmed(command, output) is False
    return "non-merge command -> not confirmed even if output text coincidentally matches"


def main() -> int:
    tests = [
        ("no-expiry token proceeds silently", test_no_expiry_proceeds_silently),
        ("valid token proceeds silently", test_valid_token_proceeds_silently),
        ("token expiring within 1h warns", test_expiring_within_hour_warns),
        ("expired token now yields advisory", test_expired_token_now_visible),
        ("exact-expiry boundary is expired", test_exact_expiry_boundary_counts_as_expired),
        ("expired state triggers on-demand refresh", test_expired_state_triggers_refresh),
        ("expiring state now fetches (not skipped)", test_expiring_state_now_fetches),
        ("ok / no_expiry states fetch", test_valid_states_fetch),
        (
            "worktree-merge output confirms despite non-zero exit",
            test_merge_confirmed_true_for_worktree_merge_despite_nonzero_exit,
        ),
        ("unconfirmed merge (no marker) is not confirmed", test_merge_confirmed_false_without_marker),
        ("non-merge command is not confirmed", test_merge_confirmed_false_for_non_merge_command),
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
