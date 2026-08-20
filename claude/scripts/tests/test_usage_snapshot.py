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

dev-env#775: `find_session_jsonl`'s worktree-cwd resolution step used to strip a
hardcoded `"/.claude/worktrees/"` marker instead of reusing
`_worktree_canon.canonical_root_from_worktree` (the resolver post-tool-use.py already
uses), so a cwd under the sibling `<repo>-worktrees/<name>` convention (dev-env#760)
skipped straight to the full directory scan instead of the direct canonical-retry
step. The tests below use a real `tempfile.TemporaryDirectory` (matching
test_post_tool_use.py's `test_load_config_falls_back_to_canonical` fixture style, since
`find_session_jsonl` does real filesystem I/O) to pin that both the nested and sibling
conventions now resolve via the shared resolver, with `PROJECTS_ROOT` monkeypatched for
the duration of each test.

dev-env#819: the `if not token:` branch (creds file present and valid JSON, but
the oauth substructure doesn't yield a usable accessToken — distinct from a
missing/unparseable *file*, which `if not creds:` already discards silently
above) used to skip straight to the advisory with no refresh attempt, unlike
the adjacent expired-token branch. The retry-and-recheck sequence duplicated
by both branches (call refresh_token_now(), reload creds, re-extract the
token) is now factored into `attempt_token_refresh()`, with its three I/O
dependencies (refresh_fn/load_fn/get_fn) injectable so the sequence is
testable offline — mirroring this repo's existing pattern for testing an
I/O-wrapping decision via an injected fake (post-tool-use.py's
`fetch_live_required_field_options()`). Live verification of the CLI's own
refresh mechanism was attempted but confounded by an unrelated, pre-existing
dead OAuth session on the test machine (every `-p`/non-TTY invocation failed
identically against the *unmodified* credentials file too — see dev-env#825);
the tests below therefore pin the function's own retry/fallback contract
rather than an end-to-end live refresh.

dev-env#915: the machine has since migrated from the npm CLI to the MSIX Claude
desktop app, which keeps OAuth in the OS keychain and never writes a readable
`.credentials.json` — so the orphan file is blanked (accessToken ""), a CLI
subprocess reports `loggedIn:false`, and both the file read and
`keep-token-warm.ps1`'s refresh are permanently futile (setup-token is 403 at the
usage endpoint, ADR-043). `main()`'s branch A now probes `claude auth status`
(`cli_auth_status` -> `resolve_claude_exe` + pure `parse_auth_status`) and, on the
`out` signature, skips the doomed refresh and emits an accurate advisory naming
dev-env#915 (ADR-124). The tests below pin the blank-string token entry into branch
A, the pure `parse_auth_status` classification (only an explicit boolean False is a
dead-end), and — via injected `exe_fn`/`run_fn`, matching the `attempt_token_refresh`
fake-injection style — that `cli_auth_status` degrades to None (no spawn) off MSIX
and on subprocess error, and classifies "out" on the desktop-app signature.

dev-env#474 (follow-up): live reproductions after the original fix landed (PR
#954 on 2026-08-07, PR #988 on 2026-08-16) both saw no snapshot appear, and
neither investigation could tell which branch of the merge-confirmation
fallback actually ran. `resolve_merge()` now makes every branch of that
decision (marker matched, REST marker matched, not a merge shape, `--help`
guard, no-confirm-needed, live `gh pr view` confirmed/unconfirmed) an explicit,
traced return value instead of an implicit `sys.exit(0)` call site. The tests
below pin each `reason` branch, using an injected `confirm_fn` for the two
live-`gh pr view` outcomes (mirroring `attempt_token_refresh`'s fake-injection
pattern) so the network call itself is never exercised. `_log_merge_trace` is
exercised end-to-end against real temp files (mirroring `find_session_jsonl`'s
tempfile style below) to pin its append-only, never-raise, and max_lines-cap
contract (the cap was added post-review: an uncapped trace grows forever,
unlike session-mode-prompt.py's own uncapped `_log` this otherwise mirrors) —
it is otherwise a thin, intentionally-lightly-tested I/O wrapper around the
pure `resolve_merge` result.

The live network call (`fetch_usage`) is intentionally not tested — the repo
avoids urllib mocks, consistent with the other script tests.

dev-env#1028 (2026-08-20, career-playbook PR #1356): a third "no snapshot"
occurrence, this time with the trace mechanism above already in place — and
the trace log had ZERO entries, not merely an unhelpful one. Investigation
confirmed `resolve_merge()`'s own classification is correct for the exact
command/stderr text involved (the URL-argument command form, never exercised
by any fixture above, which all use the bare `--squash --delete-branch`
form) — the new `test_resolve_merge_worktree_holding_branch_url_argument_*`
case below pins that directly, the same way as the existing
`gh_view_confirmed`/`gh_view_unconfirmed` cases. The actual gap was one layer
up: `main()`'s own `command`/`exit_code` extraction crashed (silently, caught
only by the outermost safe-exit guard) on a present-but-non-dict
`tool_input`/`tool_response`, before `resolve_merge()` was ever called — see
`test_hookio.py`'s new `read_command`/`read_exit_code` coverage for the fix
itself. The tests below add what no test in this file had before: genuine
end-to-end coverage of `main()`'s own dispatch (stdin -> JSON parse ->
extraction -> `resolve_merge()` -> trace write), for the three scenarios that
don't require a live `gh pr view` call (matching this file's own
no-subprocess-mock convention above): the `--help` shape with a malformed
`tool_response` (the regression pin for the concrete bug, reached through
real `main()` dispatch rather than a hand-built `resolve_merge()` call), and
two cases pinning the new `classify_error` defense-in-depth fallback
(triggers only for a plausibly-merge-shaped command; never fires — and never
spams the trace — for an ordinary non-merge command).

Usage:
    py -3 claude/scripts/tests/test_usage_snapshot.py

Exit 0 = all pass.
"""

import importlib.util
import io
import sys
import tempfile
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
attempt_token_refresh = usage_snapshot.attempt_token_refresh
get_access_token = usage_snapshot.get_access_token
parse_auth_status = usage_snapshot.parse_auth_status
cli_auth_status = usage_snapshot.cli_auth_status
merge_confirmed = usage_snapshot.merge_confirmed
resolve_merge = usage_snapshot.resolve_merge
_log_merge_trace = usage_snapshot._log_merge_trace
_plausibly_merge_shaped = usage_snapshot._plausibly_merge_shaped
status_label = usage_snapshot.status_label
format_snapshot = usage_snapshot.format_snapshot
find_session_jsonl = usage_snapshot.find_session_jsonl
encode_cwd = usage_snapshot.encode_cwd

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


# ---------------------------------------------------------------------------
# attempt_token_refresh (dev-env#819)
# ---------------------------------------------------------------------------

def test_attempt_token_refresh_recovers_a_missing_token() -> str:
    # Mirrors the dev-env#819 scenario: creds exists but token extraction failed
    # (e.g. the oauth substructure is broken). refresh_fn succeeds and the
    # reloaded creds now yield a usable token.
    calls = {"refresh": 0, "load": 0, "get": 0}
    new_creds = {"claudeAiOauth": {"accessToken": "fresh-token", "expiresAt": NOW_MS + 5 * HOUR_MS}}

    def refresh_fn():
        calls["refresh"] += 1
        return True

    def load_fn():
        calls["load"] += 1
        return new_creds

    def get_fn(creds):
        calls["get"] += 1
        return get_access_token(creds)

    token, expires_at_ms, creds = attempt_token_refresh(
        {"claudeAiOauth": {}}, None, 0, refresh_fn=refresh_fn, load_fn=load_fn, get_fn=get_fn
    )
    assert token == "fresh-token", f"expected recovered token, got {token!r}"
    assert expires_at_ms == NOW_MS + 5 * HOUR_MS, expires_at_ms
    assert creds == new_creds, creds
    assert calls == {"refresh": 1, "load": 1, "get": 1}, calls
    return "missing token + successful refresh -> token recovered from reloaded creds (dev-env#819)"


def test_attempt_token_refresh_refresh_succeeds_but_still_broken() -> str:
    # The refresh CLI call ran to completion (refresh_fn -> True) but the
    # reloaded file still has no usable token -- e.g. a dead refresh token
    # (confirmed live: keep-token-warm.ps1 and a direct `claude -p ok` both
    # exit non-zero when the underlying session itself is unrefreshable, even
    # against an unmodified credentials file -- dev-env#825). Must not
    # fabricate a token just because the refresh call itself "succeeded".
    still_broken_creds = {"claudeAiOauth": {}}  # no accessToken key

    token, expires_at_ms, creds = attempt_token_refresh(
        {"claudeAiOauth": {}}, None, 0,
        refresh_fn=lambda: True, load_fn=lambda: still_broken_creds, get_fn=get_access_token,
    )
    assert token is None, f"expected no token when refresh doesn't fix it, got {token!r}"
    assert expires_at_ms == 0, expires_at_ms
    assert creds == still_broken_creds, "creds should still reflect the reload attempt"
    return "refresh ran but token still unusable -> stays None, doesn't fabricate one (dev-env#825 case)"


def test_attempt_token_refresh_refresh_fails_leaves_everything_unchanged() -> str:
    # refresh_fn itself reports failure (subprocess error/timeout) -> nothing
    # to re-read; load_fn/get_fn must not even be called.
    calls = {"load": 0, "get": 0}
    original_creds = {"sentinel": True}

    def load_fn():
        calls["load"] += 1
        return {"claudeAiOauth": {"accessToken": "should-never-be-seen", "expiresAt": 0}}

    def get_fn(creds):
        calls["get"] += 1
        return "should-never-be-called", 0

    token, expires_at_ms, creds = attempt_token_refresh(
        original_creds, "original-token", 12345,
        refresh_fn=lambda: False, load_fn=load_fn, get_fn=get_fn,
    )
    assert token == "original-token", f"refresh failure must leave token untouched, got {token!r}"
    assert expires_at_ms == 12345, expires_at_ms
    assert creds is original_creds, "creds object must be returned unchanged, not a copy"
    assert calls == {"load": 0, "get": 0}, f"load/get must not run when refresh_fn fails: {calls}"
    return "refresh_fn() False -> inputs returned unchanged, no reload attempted"


def test_attempt_token_refresh_reload_none_falls_back_to_original_creds() -> str:
    # refresh_fn succeeds but load_fn returns None (file briefly unreadable,
    # e.g. mid-write) -> creds falls back to the caller's original value
    # rather than being discarded (matches load_credentials()'s own None
    # return on a read/parse failure).
    original_creds = {"claudeAiOauth": {"accessToken": "stale-token", "expiresAt": 999}}

    token, expires_at_ms, creds = attempt_token_refresh(
        original_creds, "stale-token", 999,
        refresh_fn=lambda: True, load_fn=lambda: None, get_fn=get_access_token,
    )
    assert creds == original_creds, "a None reload must fall back to the original creds, not discard them"
    assert token == "stale-token", f"re-extracted from the fallback creds, got {token!r}"
    return "refresh succeeds but reload yields None -> falls back to original creds (no data loss)"


def test_missing_token_refresh_recovery_then_classifies_ok() -> str:
    # End-to-end composition of the actual dev-env#819 happy path using the
    # real (non-injected) classify_token: a missing token is recovered by
    # refresh, and the freshly-obtained token then classifies as ok (not
    # expired) -- so main() would proceed to fetch, not block.
    recovered_creds = {"claudeAiOauth": {"accessToken": "recovered", "expiresAt": NOW_MS + 5 * HOUR_MS}}
    token, expires_at_ms, _creds = attempt_token_refresh(
        {"claudeAiOauth": {}}, None, 0,
        refresh_fn=lambda: True, load_fn=lambda: recovered_creds, get_fn=get_access_token,
    )
    assert token == "recovered", token
    state, advisory = classify_token(expires_at_ms, NOW_MS)
    assert state == "ok", f"expected ok after recovery, got {state}"
    assert advisory == "", advisory
    return "recovered token classifies as ok -> main() would proceed to fetch, not block (dev-env#819 happy path)"


def test_get_access_token_blank_string_token() -> str:
    # dev-env#915: the MSIX desktop app leaves a well-formed but *blanked* creds
    # file (accessToken == "", expiresAt == 0). get_access_token returns ("", 0) --
    # NOT None (empty string is not a KeyError) -- and "" is falsy, so main()'s
    # `if not token:` branch A fires (the same branch #819 added the refresh to).
    # Prior tests only used None / missing-key fixtures; pin the blank-string case.
    token, expires_at_ms = get_access_token(
        {"claudeAiOauth": {"accessToken": "", "expiresAt": 0}}
    )
    assert token == "", f"expected empty string, got {token!r}"
    assert expires_at_ms == 0, expires_at_ms
    assert not token, "blank accessToken must be falsy so branch A fires"
    return "blanked creds (accessToken='') -> ('', 0), falsy -> branch A (dev-env#915)"


def test_parse_auth_status_classifies_states() -> str:
    # dev-env#915: `claude auth status --json` classification. loggedIn:false as a
    # subprocess is the MSIX desktop-app dead-end signature ("out"). Malformed,
    # field-less, or non-boolean output must NOT be treated as a dead-end (None) --
    # only an explicit boolean False skips the snapshot.
    cases = [
        ('{"loggedIn": false, "authMethod": "none"}', "out"),
        ('{"loggedIn": true}', "in"),
        ("{}", None),                     # no loggedIn field -> unknown
        ("not json at all", None),        # unparseable
        ("[1, 2, 3]", None),              # valid JSON but not an object
        ('{"loggedIn": "false"}', None),  # present but non-boolean -> unknown
        ("", None),                       # empty stdout
    ]
    for stdout, expected in cases:
        got = parse_auth_status(stdout)
        assert got == expected, f"parse_auth_status({stdout!r}) -> {got!r}, expected {expected!r}"
    return "parse_auth_status: false->out, true->in, malformed/field-less/non-bool->None (dev-env#915)"


def test_cli_auth_status_no_exe_returns_none_without_spawning() -> str:
    # dev-env#915: on a non-MSIX install (npm CLI) resolve_claude_exe() finds no
    # packaged .exe -> cli_auth_status returns None WITHOUT spawning a subprocess,
    # so the caller falls through to the legacy refresh path unchanged (and pays no
    # subprocess cost on installs the dead-end can't apply to).
    def run_fn(*a, **k):
        raise AssertionError("subprocess must not run when no exe resolves")

    got = cli_auth_status(exe_fn=lambda: None, run_fn=run_fn)
    assert got is None, f"expected None, got {got!r}"
    return "no packaged exe -> None, no subprocess spawned (npm-install degradation, dev-env#915)"


def test_cli_auth_status_out_when_subprocess_reports_logged_out() -> str:
    # dev-env#915: with a resolvable exe and an injected runner returning the
    # desktop-app signature, cli_auth_status classifies "out" -> main() skips the
    # ~35s refresh and emits the accurate advisory.
    class FakeProc:
        stdout = '{"loggedIn": false, "authMethod": "none"}'

    got = cli_auth_status(exe_fn=lambda: "C:/fake/claude.exe", run_fn=lambda *a, **k: FakeProc())
    assert got == "out", f"expected out, got {got!r}"
    return "resolvable exe + loggedIn:false subprocess -> 'out' (dev-env#915)"


def test_cli_auth_status_none_on_subprocess_error() -> str:
    # A subprocess failure/timeout must degrade to None (legacy behavior), never
    # crash the hook -- the probe is best-effort like refresh_token_now.
    def boom(*a, **k):
        raise TimeoutError("simulated timeout")

    got = cli_auth_status(exe_fn=lambda: "C:/fake/claude.exe", run_fn=boom)
    assert got is None, f"expected None on subprocess error, got {got!r}"
    return "subprocess error -> None (degrades to legacy, dev-env#915)"


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


def test_merge_confirmed_true_for_rest_merge_fallback() -> str:
    # dev-env#986: the two-step REST merge fallback (used e.g. during a
    # GitHub GraphQL rate-limit outage) bypasses `gh pr merge` entirely.
    command = "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash"
    output = '{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}'
    assert merge_confirmed(command, output) is True
    return "REST merge fallback + \"merged\":true -> confirmed (dev-env#986)"


def test_merge_confirmed_false_for_rest_merge_without_marker() -> str:
    command = "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash"
    output = '{"message":"Merge already in progress"}'
    assert merge_confirmed(command, output) is False
    return "REST merge call without \"merged\":true -> not confirmed"


def test_merge_confirmed_false_for_rest_path_in_quoted_string() -> str:
    command = 'echo "gh api -X PUT repos/o/r/pulls/1/merge"'
    output = '{"merged":true}'
    assert merge_confirmed(command, output) is False
    return "REST merge path text inside a quoted string -> not a top-level invocation"


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


# ---------------------------------------------------------------------------
# resolve_merge / _log_merge_trace (dev-env#474 follow-up)
# ---------------------------------------------------------------------------

def test_resolve_merge_marker_reason() -> str:
    command = "gh pr merge --squash --delete-branch"
    output = (
        "Squashed and merged pull request #466 (fix: correct scheduled-tasks/routines "
        "junction topology in docs)\n"
        "error: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    )
    result = resolve_merge(command, output, exit_code=1, cwd="C:/repo")
    assert result == {"is_merge_shaped": True, "confirmed": True, "reason": "marker"}, result
    return "worktree-merge marker present -> confirmed, reason='marker'"


def test_resolve_merge_rest_marker_reason() -> str:
    command = "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash"
    output = '{"sha":"abc123","merged":true,"message":"Pull Request successfully merged"}'
    result = resolve_merge(command, output, exit_code=0, cwd="C:/repo")
    assert result == {"is_merge_shaped": True, "confirmed": True, "reason": "rest_marker"}, result
    return "REST merge marker present -> confirmed, reason='rest_marker' (dev-env#986)"


def test_resolve_merge_not_merge_shape_for_unrelated_command() -> str:
    result = resolve_merge("git push origin main", "some output", exit_code=0, cwd="C:/repo")
    assert result == {"is_merge_shaped": False, "confirmed": False, "reason": "not_merge_shape"}, result
    return "unrelated command -> not merge-shaped, not confirmed, not traced by main()"


def test_resolve_merge_not_merge_shape_still_traces_unconfirmed_rest_call() -> str:
    # A REST-merge-shaped call that ran but didn't report "merged":true (e.g. a
    # conflict/failure response) doesn't satisfy `_check_merge_stmt` (it's not a
    # `gh pr merge` shape), but IS still worth tracing as an informative outcome.
    command = "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash"
    output = '{"message":"Merge already in progress"}'
    result = resolve_merge(command, output, exit_code=1, cwd="C:/repo")
    assert result == {"is_merge_shaped": True, "confirmed": False, "reason": "not_merge_shape"}, result
    return "REST call without merged:true -> unconfirmed but still traced (is_merge_shaped=True)"


def test_resolve_merge_help_only_reason() -> str:
    command = "gh pr merge --help"
    output = "FLAGS\n      --admin   Use administrator privileges to merge a pull request"
    result = resolve_merge(command, output, exit_code=0, cwd="C:/repo")
    assert result == {"is_merge_shaped": True, "confirmed": False, "reason": "help_only"}, result
    return "gh pr merge --help -> not confirmed, reason='help_only' (dev-env#557)"


def test_resolve_merge_no_confirm_needed_reason() -> str:
    # A queued --auto exits 0 with no marker -- should_confirm_via_gh is False
    # (exit_code == 0), so no live gh pr view call is paid.
    command = "gh pr merge --auto --squash --delete-branch"
    output = "Pull request #466 will be automatically merged when checks pass"

    def confirm_fn(*a, **k):
        raise AssertionError("must not pay a live gh pr view call on exit 0")

    result = resolve_merge(command, output, exit_code=0, cwd="C:/repo", confirm_fn=confirm_fn)
    assert result == {"is_merge_shaped": True, "confirmed": False, "reason": "no_confirm_needed"}, result
    return "queued --auto, exit 0 -> not confirmed, reason='no_confirm_needed', no network call paid"


def test_resolve_merge_gh_view_confirmed_reason() -> str:
    # The dev-env#489/#496 shape: marker lost, non-zero exit -> live gh pr view
    # fallback is paid and confirms the merge.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    calls = []

    def confirm_fn(pr_number, repo, cwd):
        calls.append((pr_number, repo, cwd))
        return 954

    result = resolve_merge(command, output, exit_code=1, cwd="C:/repo", confirm_fn=confirm_fn)
    assert result == {"is_merge_shaped": True, "confirmed": True, "reason": "gh_view_confirmed"}, result
    assert calls == [(None, "", "C:/repo")], calls
    return "marker lost + live gh pr view confirms -> confirmed, reason='gh_view_confirmed' (dev-env#489/#496)"


def test_resolve_merge_gh_view_unconfirmed_reason() -> str:
    # Live gh pr view found nothing (genuinely failed merge, or the network call
    # itself errored/timed out) -> stays unconfirmed, distinguishable in the
    # trace from every other unconfirmed reason.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    result = resolve_merge(command, output, exit_code=1, cwd="C:/repo", confirm_fn=lambda *a, **k: None)
    assert result == {"is_merge_shaped": True, "confirmed": False, "reason": "gh_view_unconfirmed"}, result
    return "marker lost + live gh pr view finds nothing -> not confirmed, reason='gh_view_unconfirmed'"


def test_resolve_merge_worktree_holding_branch_url_argument_issue_1028() -> str:
    # dev-env#1028's exact command and stderr text: the URL-argument form of
    # `gh pr merge` (never exercised by any fixture above -- all use the bare
    # `--squash --delete-branch` form operating on cwd's checked-out branch)
    # combined with the worktree-holding-branch local-abort stderr, against a
    # real-world worktree cwd (not this file's usual synthetic "C:/repo"). The
    # literal hypothesis in the issue's own title -- that this exact text
    # causes an early return inside resolve_merge() -- does not hold: this
    # pins that it correctly reaches the same gh_view_* fallback every other
    # exit-1 "marker lost" case above reaches.
    command = 'gh pr merge "https://github.com/brownm09/career-playbook/pull/1356" --squash --delete-branch'
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/career-playbook'"
    cwd = "C:/Users/brown/Git/career-playbook/.claude/worktrees/suspicious-bun-9dfa4b"
    calls = []

    def confirm_fn(pr_number, repo, resolved_cwd):
        calls.append((pr_number, repo, resolved_cwd))
        return 1356

    result = resolve_merge(command, output, exit_code=1, cwd=cwd, confirm_fn=confirm_fn)
    assert result == {"is_merge_shaped": True, "confirmed": True, "reason": "gh_view_confirmed"}, result
    assert calls == [(None, "", cwd)], calls
    return "dev-env#1028's exact URL-argument command + stderr -> gh_view_confirmed (the original hypothesis, not confirmed)"


def test_log_merge_trace_appends_and_never_raises() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "nested" / "trace.log")
        _log_merge_trace({"reason": "marker", "confirmed": True}, path=path)
        _log_merge_trace({"reason": "gh_view_unconfirmed", "confirmed": False}, path=path)
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, lines
        import json as _json
        assert _json.loads(lines[0])["reason"] == "marker", lines[0]
        assert _json.loads(lines[1])["reason"] == "gh_view_unconfirmed", lines[1]
    return "_log_merge_trace appends one JSON line per call, creating parent dirs as needed"


def test_log_merge_trace_swallows_write_failure() -> str:
    # An unwritable path (a directory, not a file) must not raise -- matches
    # the never-raise contract every other best-effort I/O helper in this
    # hook family carries.
    with tempfile.TemporaryDirectory() as tmp:
        _log_merge_trace({"reason": "marker"}, path=tmp)  # tmp is a directory, not a file
    return "_log_merge_trace swallows a write failure (path is a directory) without raising"


def test_log_merge_trace_caps_to_max_lines() -> str:
    # dev-env#474 review finding: an uncapped trace grows forever. Writing 5
    # entries with max_lines=3 must keep only the 3 most recent.
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "trace.log")
        for i in range(5):
            _log_merge_trace({"i": i}, path=path, max_lines=3)
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        import json as _json
        indices = [_json.loads(ln)["i"] for ln in lines]
        assert indices == [2, 3, 4], indices
    return "_log_merge_trace caps to the most recent max_lines entries, dropping the oldest"


# ---------------------------------------------------------------------------
# main() end-to-end dispatch (dev-env#1028) -- the first tests in this file
# that invoke main() itself rather than resolve_merge()/merge_confirmed()
# directly. Necessary because the bug this section pins lived in main()'s own
# stdin -> JSON -> extraction wiring, upstream of every function this file's
# existing tests already exercise directly. Scoped to the three scenarios
# that never reach the live gh pr view call (matching this file's own
# no-subprocess-mock convention, noted at the top of this file) --
# resolve_merge()'s gh_view_* branches are already pinned above via the
# established confirm_fn-injection pattern, not re-tested here.
# ---------------------------------------------------------------------------

def _run_main_capturing_trace(payload) -> tuple:
    """Invoke the real usage_snapshot.main() against *payload* (JSON-
    serialized via json.dumps -- typically a dict, but any JSON-serializable
    value works, e.g. a list for the non-dict-top-level-data test) fed over
    stdin, with _log_merge_trace monkeypatched to capture calls instead of
    touching the real trace file. Returns (exit_code, [trace_entry, ...]).
    """
    import json as _json

    calls = []
    real_stdin = usage_snapshot.sys.stdin
    real_log_merge_trace = usage_snapshot._log_merge_trace
    usage_snapshot.sys.stdin = io.StringIO(_json.dumps(payload))
    usage_snapshot._log_merge_trace = lambda entry: calls.append(entry)
    try:
        try:
            usage_snapshot.main()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        usage_snapshot.sys.stdin = real_stdin
        usage_snapshot._log_merge_trace = real_log_merge_trace
    return exit_code, calls


def test_main_help_command_with_null_tool_response_does_not_crash() -> str:
    # dev-env#1028's actual regression pin: a present-but-None tool_response
    # crashed main()'s pre-fix `data.get("tool_response", {}).get("exitCode",
    # -1)` chain before resolve_merge() -- and the trace write -- was ever
    # reached. `gh pr merge --help` is used specifically because it resolves
    # via is_merge_help_only without ever needing a live gh pr view call, so
    # this pins the fix through REAL main() dispatch with zero network
    # involvement.
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge --help"},
        "tool_response": None,
        "cwd": "C:/repo",
    }
    exit_code, calls = _run_main_capturing_trace(payload)
    assert exit_code == 0, exit_code
    assert len(calls) == 1, calls
    assert calls[0]["reason"] == "help_only", calls[0]
    assert calls[0]["is_merge_shaped"] is True, calls[0]
    assert calls[0]["confirmed"] is False, calls[0]
    return "tool_response: null + gh pr merge --help -> main() does not crash, still traces (dev-env#1028 regression pin)"


def test_main_classify_error_fallback_fires_on_resolve_merge_exception() -> str:
    # Forces the one branch that cannot otherwise be reached: resolve_merge()
    # itself raising. Since the real trigger (if one exists in
    # split_top_level's parser) is not identified, this proves the safety
    # net engages correctly when *something* unanticipated throws, rather
    # than pinning a specific root cause.
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge --squash --delete-branch"},
        "tool_response": {"exitCode": 1, "stderr": "boom"},
        "cwd": "C:/repo",
    }

    def raising_resolve_merge(*_a, **_k):
        raise RuntimeError("simulated classification failure")

    real_resolve_merge = usage_snapshot.resolve_merge
    usage_snapshot.resolve_merge = raising_resolve_merge
    try:
        exit_code, calls = _run_main_capturing_trace(payload)
    finally:
        usage_snapshot.resolve_merge = real_resolve_merge
    assert exit_code == 0, exit_code
    assert len(calls) == 1, calls
    assert calls[0]["reason"] == "classify_error", calls[0]
    assert calls[0]["is_merge_shaped"] is True, calls[0]
    assert calls[0]["confirmed"] is False, calls[0]
    # dev-env#1028 post-review finding: the exception itself must be
    # recorded, not just the fact that *something* threw -- a bare
    # "classify_error" with no detail would force yet another
    # live-instrumented reproduction to learn which layer failed.
    assert calls[0]["error"] == "RuntimeError: simulated classification failure", calls[0]
    return "resolve_merge() raising -> classify_error fallback traces the merge-shaped command AND the exception detail"


def test_main_classify_error_fallback_does_not_fire_for_non_merge_command() -> str:
    # Same forced exception as above, but a genuinely non-merge command --
    # _plausibly_merge_shaped must gate the fallback so an unrelated
    # exception (however unlikely) doesn't spam the trace log on the
    # overwhelming majority of non-merge Bash/PowerShell calls this global
    # hook sees on every tool call.
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
        "tool_response": {"exitCode": 0, "stdout": "clean"},
        "cwd": "C:/repo",
    }

    def raising_resolve_merge(*_a, **_k):
        raise RuntimeError("simulated classification failure")

    real_resolve_merge = usage_snapshot.resolve_merge
    usage_snapshot.resolve_merge = raising_resolve_merge
    try:
        exit_code, calls = _run_main_capturing_trace(payload)
    finally:
        usage_snapshot.resolve_merge = real_resolve_merge
    assert exit_code == 0, exit_code
    assert calls == [], calls
    return "resolve_merge() raising for a non-merge command -> classify_error fallback does not fire, no trace spam"


# ---------------------------------------------------------------------------
# dev-env#1028 POST-REVIEW findings (both review passes independently
# executed the real main() against the PR's own diagnosed root-cause payload
# and found it still produced ZERO trace entries -- the fix prevented the
# crash but not the original symptom, because a destroyed `command` is
# trivially not_merge_shape). These tests pin the corrected behavior.
# ---------------------------------------------------------------------------

def test_main_malformed_tool_input_with_marker_traces_and_confirms() -> str:
    # THE critical regression case both review passes independently executed
    # and found broken: tool_input present-but-None (dev-env#1028's actual
    # payload shape) destroys `command`, so resolve_merge("") always
    # classifies not_merge_shape/is_merge_shaped=False and the ordinary
    # trace-write guard never fires -- reproducing dev-env#1028's exact
    # symptom (zero trace entries) via a different mechanism than the
    # original crash. main() must detect the malformed tool_input directly
    # and, when the merge marker still survives in `output` (tool_response is
    # intact here), treat it as confirmed and proceed toward the snapshot.
    payload = {
        "tool_name": "Bash",
        "tool_input": None,
        "tool_response": {
            "exitCode": 1,
            "stdout": "Squashed and merged pull request #1356 (fix)",
            "stderr": "failed to run git: fatal: 'main' is already checked out at 'C:/repo'",
        },
        "cwd": "C:/repo",
    }
    exit_code, calls = _run_main_capturing_trace(payload)
    assert len(calls) == 1, calls
    assert calls[0]["reason"] == "malformed_payload", calls[0]
    assert calls[0]["is_merge_shaped"] is True, calls[0]
    assert calls[0]["confirmed"] is True, calls[0]
    # Confirmed via the surviving marker -> main() must NOT bail at exit 0;
    # it falls through toward the snapshot logic exactly like any other
    # confirmed merge (exit code depends on downstream credential state, but
    # must not be the "nothing happened" 0 a lost event would produce).
    assert exit_code != 0, exit_code
    return "tool_input:null + merge marker still in output -> traced as malformed_payload, confirmed=True, falls through (dev-env#1028 post-review)"


def test_main_malformed_tool_input_without_marker_traces_unconfirmed() -> str:
    # Same malformed tool_input, but no independent signal survives in
    # `output` either -- must still trace (so the event is never silently
    # invisible), but cannot claim confirmation it has no evidence for.
    payload = {
        "tool_name": "Bash",
        "tool_input": None,
        "tool_response": {
            "exitCode": 1,
            "stderr": "failed to run git: fatal: 'main' is already checked out at 'C:/repo'",
        },
        "cwd": "C:/repo",
    }
    exit_code, calls = _run_main_capturing_trace(payload)
    assert exit_code == 0, exit_code
    assert len(calls) == 1, calls
    assert calls[0]["reason"] == "malformed_payload", calls[0]
    assert calls[0]["is_merge_shaped"] is True, calls[0]
    assert calls[0]["confirmed"] is False, calls[0]
    return "tool_input:null + no surviving marker -> traced as malformed_payload, confirmed=False, exits cleanly (dev-env#1028 post-review)"


def test_main_cwd_none_does_not_crash_after_confirmed_trace() -> str:
    # dev-env#1028 post-review finding: `cwd = data.get("cwd", "")` had the
    # identical present-but-None gap as the original command/exit_code bug,
    # except its crash landed AFTER a confirmed:true trace entry was already
    # written (downstream, in the snapshot/session-lookup machinery) --
    # producing a permanent record that actively asserts a merge was
    # confirmed while no snapshot ever appeared. A marker-confirmed merge
    # with cwd:null must not crash; read_cwd() converts None -> "".
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge --squash --delete-branch"},
        "tool_response": {"exitCode": 0, "stdout": "Squashed and merged pull request #99"},
        "cwd": None,
    }
    exit_code, calls = _run_main_capturing_trace(payload)
    assert len(calls) == 1, calls
    assert calls[0]["reason"] == "marker", calls[0]
    assert calls[0]["confirmed"] is True, calls[0]
    assert calls[0]["cwd"] == "", calls[0]
    # Must fall through toward snapshot logic without crashing -- not the
    # bare 0 a crash caught only by the outer safe-exit guard would produce
    # after having ALREADY written a misleading confirmed:true trace line.
    assert exit_code != 0, exit_code
    return "cwd:null + confirmed merge -> traced correctly (cwd='') and does not crash downstream (dev-env#1028 post-review)"


def test_main_non_dict_top_level_data_does_not_crash() -> str:
    # dev-env#1028 post-review finding: a valid-JSON-but-non-dict top-level
    # payload (a list, here -- _run_main_capturing_trace's own json.dumps()
    # serializes it the same as any dict payload) crashed at
    # `data.get("tool_name")` -- one level above every read_* helper's own
    # guard, and the identical silent-crash class this whole fix exists to
    # close, caught only by the outermost safe-exit guard with nothing traced.
    exit_code, calls = _run_main_capturing_trace(["not", "an", "object"])
    assert exit_code == 0, exit_code
    assert calls == [], calls
    return "non-dict top-level JSON (a list) -> main() exits cleanly, no crash, no trace (dev-env#1028 post-review)"


def test_plausibly_merge_shaped_true_cases() -> str:
    assert _plausibly_merge_shaped("gh pr merge --squash --delete-branch")
    assert _plausibly_merge_shaped('gh pr merge "https://github.com/o/r/pull/1" --squash')
    assert _plausibly_merge_shaped("GH PR MERGE --auto"), "case-insensitive"
    assert _plausibly_merge_shaped("gh api -X PUT repos/o/r/pulls/5/merge")
    return "gh pr merge (any case/args) and the gh api REST-merge shape -> True"


def test_plausibly_merge_shaped_false_cases() -> str:
    assert not _plausibly_merge_shaped("git status")
    assert not _plausibly_merge_shaped("gh pr create --fill")
    assert not _plausibly_merge_shaped("")
    return "non-merge commands and empty string -> False"


def test_plausibly_merge_shaped_word_boundaries_reject_substring_matches() -> str:
    # dev-env#1028 post-review finding: the original unbounded substring test
    # ("gh" in lowered and "pr" in lowered) false-positived on ordinary words
    # containing those letters -- flooding the 500-line capped trace log in
    # exactly the failure-correlated scenario (resolve_merge() throwing on a
    # common command shape) this fallback exists for, risking eviction of the
    # genuine merge entries the log exists to preserve.
    assert not _plausibly_merge_shaped("git merge origin/print-highlights"), \
        "'gh' inside 'highlights' and 'pr' inside 'print' must not satisfy word-bounded \\bgh\\b/\\bpr\\b"
    assert not _plausibly_merge_shaped("echo approved and merged manually"), \
        "'pr' inside 'approved' must not satisfy word-bounded \\bpr\\b"
    return "unbounded substring matches ('highlights'/'print', 'approved') correctly excluded by word boundaries"


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


# ---------------------------------------------------------------------------
# find_session_jsonl worktree-cwd resolution (dev-env#775)
# ---------------------------------------------------------------------------

def test_find_session_jsonl_resolves_nested_worktree_convention() -> str:
    # Regression check: the nested `.claude/worktrees/<name>` convention must keep
    # resolving now that the hardcoded marker split was replaced by the shared
    # canonical_root_from_worktree resolver.
    with tempfile.TemporaryDirectory() as tmp:
        projects_root = Path(tmp) / "projects"
        canonical = str(Path(tmp) / "dev-env")
        worktree_cwd = str(Path(tmp) / "dev-env" / ".claude" / "worktrees" / "some-fix")
        session_id = "nested-session-id"
        canon_dir = projects_root / encode_cwd(canonical)
        canon_dir.mkdir(parents=True)
        (canon_dir / f"{session_id}.jsonl").write_text("", encoding="utf-8")

        original_root = usage_snapshot.PROJECTS_ROOT
        usage_snapshot.PROJECTS_ROOT = projects_root
        try:
            result = find_session_jsonl(worktree_cwd, session_id)
        finally:
            usage_snapshot.PROJECTS_ROOT = original_root

        expected = canon_dir / f"{session_id}.jsonl"
        assert result == expected, f"expected {expected!r}, got {result!r}"
    return "nested .claude/worktrees/<name> cwd still resolves via the shared resolver"


def test_find_session_jsonl_resolves_sibling_worktree_convention() -> str:
    # The fix: a cwd under the sibling `<repo>-worktrees/<name>` convention
    # (dev-env#760) used to skip straight to the full directory scan (step 3)
    # because the old code only recognized the nested marker. It now resolves
    # directly via canonical_root_from_worktree, same as post-tool-use.py.
    with tempfile.TemporaryDirectory() as tmp:
        projects_root = Path(tmp) / "projects"
        canonical = str(Path(tmp) / "dev-env")
        worktree_cwd = str(Path(tmp) / "dev-env-worktrees" / "fix-775-usage-snapshot")
        session_id = "sibling-session-id"
        canon_dir = projects_root / encode_cwd(canonical)
        canon_dir.mkdir(parents=True)
        (canon_dir / f"{session_id}.jsonl").write_text("", encoding="utf-8")

        original_root = usage_snapshot.PROJECTS_ROOT
        usage_snapshot.PROJECTS_ROOT = projects_root
        try:
            result = find_session_jsonl(worktree_cwd, session_id)
        finally:
            usage_snapshot.PROJECTS_ROOT = original_root

        expected = canon_dir / f"{session_id}.jsonl"
        assert result == expected, f"expected {expected!r}, got {result!r}"
    return "sibling <repo>-worktrees/<name> cwd now resolves via canonical_root_from_worktree (dev-env#775)"


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
            "missing token + successful refresh recovers it (dev-env#819)",
            test_attempt_token_refresh_recovers_a_missing_token,
        ),
        (
            "refresh runs but token still unusable -> stays None (dev-env#825 case)",
            test_attempt_token_refresh_refresh_succeeds_but_still_broken,
        ),
        (
            "refresh_fn failure leaves inputs unchanged, no reload attempted",
            test_attempt_token_refresh_refresh_fails_leaves_everything_unchanged,
        ),
        (
            "reload returning None falls back to original creds",
            test_attempt_token_refresh_reload_none_falls_back_to_original_creds,
        ),
        (
            "recovered token classifies ok -> proceeds to fetch (dev-env#819 happy path)",
            test_missing_token_refresh_recovery_then_classifies_ok,
        ),
        ("blanked creds accessToken='' falls to branch A (dev-env#915)", test_get_access_token_blank_string_token),
        ("parse_auth_status classifies in/out/None (dev-env#915)", test_parse_auth_status_classifies_states),
        (
            "cli_auth_status: no packaged exe -> None, no spawn (dev-env#915)",
            test_cli_auth_status_no_exe_returns_none_without_spawning,
        ),
        (
            "cli_auth_status: loggedIn:false subprocess -> out (dev-env#915)",
            test_cli_auth_status_out_when_subprocess_reports_logged_out,
        ),
        ("cli_auth_status: subprocess error -> None (dev-env#915)", test_cli_auth_status_none_on_subprocess_error),
        (
            "worktree-merge output confirms despite non-zero exit",
            test_merge_confirmed_true_for_worktree_merge_despite_nonzero_exit,
        ),
        ("unconfirmed merge (no marker) is not confirmed", test_merge_confirmed_false_without_marker),
        ("non-merge command is not confirmed", test_merge_confirmed_false_for_non_merge_command),
        ("REST merge fallback + \"merged\":true -> confirmed (dev-env#986)", test_merge_confirmed_true_for_rest_merge_fallback),
        ("REST merge fallback without marker -> not confirmed (dev-env#986)", test_merge_confirmed_false_for_rest_merge_without_marker),
        ("REST merge path in quoted string -> not confirmed (dev-env#986)", test_merge_confirmed_false_for_rest_path_in_quoted_string),
        ("gh pr merge --help: guard fires (dev-env#557)", test_help_command_not_merge_confirmed_and_is_help_only),
        ("unresolved real merge: guard does not suppress fallback", test_unresolved_real_merge_is_not_help_only),
        ("resolve_merge: marker -> confirmed", test_resolve_merge_marker_reason),
        ("resolve_merge: REST marker -> confirmed", test_resolve_merge_rest_marker_reason),
        ("resolve_merge: unrelated command -> not merge-shaped", test_resolve_merge_not_merge_shape_for_unrelated_command),
        (
            "resolve_merge: unconfirmed REST call still traced",
            test_resolve_merge_not_merge_shape_still_traces_unconfirmed_rest_call,
        ),
        ("resolve_merge: --help -> help_only", test_resolve_merge_help_only_reason),
        ("resolve_merge: queued --auto -> no_confirm_needed, no network call", test_resolve_merge_no_confirm_needed_reason),
        ("resolve_merge: live gh pr view confirms -> gh_view_confirmed", test_resolve_merge_gh_view_confirmed_reason),
        ("resolve_merge: live gh pr view finds nothing -> gh_view_unconfirmed", test_resolve_merge_gh_view_unconfirmed_reason),
        (
            "resolve_merge: dev-env#1028 exact URL-argument command + stderr -> gh_view_confirmed",
            test_resolve_merge_worktree_holding_branch_url_argument_issue_1028,
        ),
        ("_log_merge_trace: appends JSON lines, creates parent dirs", test_log_merge_trace_appends_and_never_raises),
        ("_log_merge_trace: swallows write failure", test_log_merge_trace_swallows_write_failure),
        ("_log_merge_trace: caps to max_lines, drops oldest", test_log_merge_trace_caps_to_max_lines),
        (
            "main(): --help + tool_response:null does not crash, still traces (dev-env#1028)",
            test_main_help_command_with_null_tool_response_does_not_crash,
        ),
        (
            "main(): resolve_merge() exception -> classify_error fallback traces (dev-env#1028)",
            test_main_classify_error_fallback_fires_on_resolve_merge_exception,
        ),
        (
            "main(): classify_error fallback does not fire for non-merge command (dev-env#1028)",
            test_main_classify_error_fallback_does_not_fire_for_non_merge_command,
        ),
        (
            "main(): malformed tool_input + surviving marker -> malformed_payload, confirmed, falls through (dev-env#1028 post-review)",
            test_main_malformed_tool_input_with_marker_traces_and_confirms,
        ),
        (
            "main(): malformed tool_input, no marker -> malformed_payload, unconfirmed (dev-env#1028 post-review)",
            test_main_malformed_tool_input_without_marker_traces_unconfirmed,
        ),
        (
            "main(): cwd:null does not crash after a confirmed trace (dev-env#1028 post-review)",
            test_main_cwd_none_does_not_crash_after_confirmed_trace,
        ),
        (
            "main(): non-dict top-level JSON does not crash (dev-env#1028 post-review)",
            test_main_non_dict_top_level_data_does_not_crash,
        ),
        ("_plausibly_merge_shaped: gh pr merge / REST-merge shapes -> True", test_plausibly_merge_shaped_true_cases),
        ("_plausibly_merge_shaped: non-merge commands -> False", test_plausibly_merge_shaped_false_cases),
        (
            "_plausibly_merge_shaped: word boundaries reject substring matches (dev-env#1028 post-review)",
            test_plausibly_merge_shaped_word_boundaries_reject_substring_matches,
        ),
        (
            "nested worktree convention still resolves (dev-env#775)",
            test_find_session_jsonl_resolves_nested_worktree_convention,
        ),
        (
            "sibling worktree convention now resolves via shared resolver (dev-env#775)",
            test_find_session_jsonl_resolves_sibling_worktree_convention,
        ),
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
