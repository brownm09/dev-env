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

dev-env#557: `main()` adds a second guard — `if is_merge_help_only(command):
sys.exit(0)`, right after the existing `if not scan_top_level(command,
_check_merge_stmt): sys.exit(0)` line, before computing `exit_code` — so a
`gh pr merge --help` command never reaches the live `gh pr view` fallback
that would otherwise misattribute an unrelated already-merged PR to the
harmless `--help` invocation. The guard fires before the credentials/config
reads further down in `main()`, so this fix needs no fixture beyond the
command/output shapes already used elsewhere in this file. `is_merge_help_only`
itself is exhaustively tested in `test_hookio.py`; the composition test below
pins that `merge_confirmed` (the predicate the guard sits behind) returns
False for exactly the `--help` shape `is_merge_help_only` returns True for.

PR5 (dev-env#736) routed all four emissions through `_hookout.emit_block`
(exit-2 stderr, `ascii_sanitize` backstop + exit-code-safe `finally`) and made the
snapshot's static content ASCII (`status_label` returns OVER/NEAR/OK tokens; the
target line uses `<=` not U+2264). The tests below pin `status_label` and the
`format_snapshot` static template as `.isascii()`, and — the real end-to-end
guarantee — that `ascii_sanitize(format_snapshot(<non-ASCII action>))` is
`.isascii()`, since the action column interpolates arbitrary Unicode and wire-safety
lives in `emit_block`, not `format_snapshot` (the #670 pattern). All were previously
unexercised.

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
status_label = usage_snapshot.status_label
format_snapshot = usage_snapshot.format_snapshot

# is_merge_help_only lives in _hookio (a sibling); SCRIPT.parent already on
# sys.path via the insert above.
from _hookio import is_merge_help_only  # noqa: E402
# ascii_sanitize is emit_block's wire-safety backstop — the real guarantee that a
# snapshot interpolating a non-ASCII exchange action can't crash the stderr write.
from _hookout import ascii_sanitize  # noqa: E402

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


# ---------------------------------------------------------------------------
# is_merge_help_only composition (dev-env#557)
# ---------------------------------------------------------------------------

def test_help_command_not_merge_confirmed_and_is_help_only() -> str:
    command = "gh pr merge --help"
    output = "FLAGS\n      --admin   Use administrator privileges to merge a pull request"
    assert merge_confirmed(command, output) is False, "no success marker -> not merge_confirmed"
    assert is_merge_help_only(command), "gh pr merge --help -> is_merge_help_only True"
    return "gh pr merge --help: merge_confirmed False, is_merge_help_only True -> guard fires (dev-env#557)"


def test_unresolved_real_merge_is_not_help_only() -> str:
    # A genuine merge with no marker (e.g. dev-env#489's lost-marker shape) and
    # a non-zero exit must NOT be classified as help-only -- the live gh-pr-view
    # fallback must still be attempted for this shape, unchanged.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    assert merge_confirmed(command, output) is False
    assert not is_merge_help_only(command), "bare merge, no --help -> guard must not suppress it"
    return "unresolved real merge (no marker, non-help) -> is_merge_help_only False (fallback unaffected)"


def test_status_label_bands_are_ascii() -> str:
    # PR5 (dev-env#736): this returned emoji outside cp1252; on the raw stderr channel
    # the print raised, flipping exit 2 -> 0 and silently dropping the whole snapshot.
    # It now returns ASCII tokens.
    over = status_label(120, 100, 5)
    near = status_label(97, 100, 5)
    under = status_label(50, 100, 5)
    none = status_label(0, 0, 5)
    for s in (over, near, under, none):
        assert s.isascii(), f"non-ASCII status token: {s!r}"
    assert (over, near, under, none) == ("OVER cap", "NEAR cap", "OK under cap", ""), \
        (over, near, under, none)
    return "status_label bands -> ASCII tokens (OVER/NEAR/OK), .isascii() (dev-env#736)"


def test_format_snapshot_static_template_is_ascii() -> str:
    # Pins that the STATIC template carries no literal emoji / U+2264 (the PR5
    # ASCII-ification of status_label + the target line). This does NOT prove the
    # whole snapshot is ASCII — the action column interpolates arbitrary Unicode;
    # end-to-end wire-safety is emit_block's ascii_sanitize backstop, pinned below.
    config = {"alert_approaching_margin": 5}
    over = format_snapshot(
        {
            "seven_day": {"utilization": 99, "resets_at": "2026-07-14T00:00:00Z"},
            "five_hour": {"utilization": 40},
            "extra_usage": {"is_enabled": True, "used_credits": 1.5, "monthly_limit": 50, "utilization": 3.0},
        },
        config,
        [{"action": "Bash", "input": 1000, "cache_write": 50, "output": 200, "total": 1250}],
    )
    under = format_snapshot(
        {"seven_day": {"utilization": 10}, "five_hour": {"utilization": 5}}, config, [],
    )
    assert over.isascii(), f"non-ASCII in snapshot template: {over!r}"
    assert under.isascii(), f"non-ASCII in snapshot template: {under!r}"
    # the target line now uses ASCII "<=" instead of U+2264
    assert "<=" in under and "≤" not in under, under
    return "format_snapshot static template is .isascii() (emoji + U+2264 removed) (dev-env#736)"


def test_snapshot_wire_safe_even_with_non_ascii_action() -> str:
    # The REAL crash guard (the #670 pattern): even when an exchange action carries
    # arbitrary Unicode (describe_content() of the assistant's text, truncated), the
    # bytes emit_block actually writes are ascii_sanitize'd, so the stderr write can't
    # crash under cp1252. format_snapshot ALONE is not .isascii() here — the guarantee
    # lives in emit_block, not format_snapshot.
    config = {"alert_approaching_margin": 5}
    snap = format_snapshot(
        {"seven_day": {"utilization": 50}, "five_hour": {"utilization": 5}},
        config,
        [{"action": "fix the café 😀 bug", "input": 1000, "cache_write": 50, "output": 200, "total": 1250}],
    )
    assert not snap.isascii(), "fixture must carry non-ASCII so the backstop is what's under test"
    assert ascii_sanitize(snap).isascii(), "emit_block's ascii_sanitize must guarantee wire-safe bytes"
    return "non-ASCII action -> format_snapshot not ASCII, but ascii_sanitize(snapshot) is (emit_block backstop)"


def main() -> int:
    tests = [
        ("no-expiry token proceeds silently", test_no_expiry_proceeds_silently),
        ("status_label bands are ASCII (dev-env#736)", test_status_label_bands_are_ascii),
        ("format_snapshot static template is ASCII (dev-env#736)", test_format_snapshot_static_template_is_ascii),
        ("snapshot wire-safe via ascii_sanitize despite non-ASCII action (dev-env#736)", test_snapshot_wire_safe_even_with_non_ascii_action),
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
        ("gh pr merge --help: guard fires (dev-env#557)", test_help_command_not_merge_confirmed_and_is_help_only),
        ("unresolved real merge: guard does not suppress fallback", test_unresolved_real_merge_is_not_help_only),
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
