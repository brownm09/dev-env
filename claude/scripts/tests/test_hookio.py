#!/usr/bin/env python3
"""Unit tests for _hookio.read_command_output — the shared PostToolUse output read.

Claude Code's Bash hook payload exposes a command's output under
`tool_response.stdout` / `tool_response.stderr`, NOT `output`. `post-tool-use.py`
read the legacy `output` field and therefore silently never fired (dev-env #377 /
ADR-049); the same wrong read existed in four sibling hooks (#380). The fix is the
shared `read_command_output` helper in `claude/scripts/_hookio.py`, imported by all
five hooks. These tests pin its field precedence offline (no network, no gh).

Usage:
    py -3 claude/scripts/tests/test_hookio.py

Exit 0 = all pass.
"""

import re
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _hookio import (  # noqa: E402
    effective_merge_dir,
    is_absolute_path,
    is_help_only,
    is_merge_help_only,
    mask_prose_flag_values,
    mask_quoted_spans,
    merge_pr_number_from_output,
    output_has_merge_marker,
    read_command_output,
    scan_top_level,
    should_confirm_via_gh,
    split_top_level,
    strip_line_continuations,
)

URL = "https://github.com/brownm09/dev-env/issues/377"


def test_reads_stdout() -> str:
    # The real Bash payload shape: output lives under `stdout`, not `output`.
    payload = {"tool_response": {"stdout": URL, "stderr": "", "interrupted": False}}
    assert read_command_output(payload) == URL, "stdout should be read"
    return "stdout-shaped payload -> stdout content (the #377 regression)"


def test_combines_stdout_and_stderr() -> str:
    # `gh pr merge` prints its success line to stderr; both must be captured.
    payload = {"tool_response": {"stdout": "done", "stderr": "warn"}}
    assert read_command_output(payload) == "done\nwarn", "stdout+stderr joined"
    return "stdout + stderr are both captured, newline-joined"


def test_stderr_only() -> str:
    # gh writes the merge success marker to stderr with no stdout.
    payload = {"tool_response": {"stdout": "", "stderr": "Squashed and merged pull request #380"}}
    assert read_command_output(payload) == "Squashed and merged pull request #380"
    return "stderr-only payload -> stderr content (the gh pr merge shape)"


def test_legacy_output_fallback() -> str:
    # If a build ever sends the legacy `output` field, still read it.
    payload = {"tool_response": {"output": URL}}
    assert read_command_output(payload) == URL, "legacy output fallback"
    return "legacy `output` field still works (forward/backward compatible)"


def test_stdout_preferred_over_legacy_output() -> str:
    payload = {"tool_response": {"stdout": "real", "output": "legacy"}}
    assert read_command_output(payload) == "real", "stdout wins over legacy output"
    return "stdout/stderr take precedence over the legacy output field"


def test_empty_and_malformed_payloads() -> str:
    assert read_command_output({}) == "", "missing tool_response -> ''"
    assert read_command_output({"tool_response": {}}) == "", "empty tool_response -> ''"
    assert read_command_output({"tool_response": None}) == "", "None tool_response -> ''"
    assert read_command_output({"tool_response": "x"}) == "", "non-dict tool_response -> ''"
    return "missing/empty/None/non-dict tool_response all yield '' (no crash)"


def test_old_output_read_would_have_been_empty() -> str:
    # Pin the root cause: the pre-fix read (`.get("output")`) on the real shape
    # is empty, which is exactly what silently broke the four sibling hooks.
    real_shape = {"stdout": URL, "stderr": "", "interrupted": False, "isImage": False}
    assert real_shape.get("output", "") == "", "pre-fix read must be empty on real shape"
    assert read_command_output({"tool_response": real_shape}) == URL, "fixed read recovers content"
    return "pre-fix `output` read was '' on the real payload; fixed read recovers it"


def test_merge_marker_detected() -> str:
    assert output_has_merge_marker("✓ Squashed and merged pull request #380 (T)")
    assert output_has_merge_marker("✓ Merged pull request #1")
    assert output_has_merge_marker("✓ Rebased and merged pull request brownm09/dev-env#7")
    return "real merge markers (incl. cross-repo owner/repo#N) -> True"


def test_merge_marker_excludes_auto_failure_and_stray() -> str:
    assert not output_has_merge_marker("✓ Pull request #380 will be automatically merged")
    assert not output_has_merge_marker("X Pull request #380 is not mergeable")
    # A stray verb phrase WITHOUT "pull request #N" must not count — this is the
    # chained-output false positive the line-anchored regex closes (#380 review).
    assert not output_has_merge_marker("note: it was 'Squashed and merged' last time")
    assert not output_has_merge_marker("")
    return "queued --auto / failure / stray phrase / empty -> False"


def test_merge_pr_number_from_output() -> str:
    assert merge_pr_number_from_output("✓ Squashed and merged pull request #380 (T)") == 380
    assert merge_pr_number_from_output("✓ Rebased and merged pull request o/r#7") == 7
    assert merge_pr_number_from_output("no marker here") is None
    return "marker PR number extracted; None when absent"


# ---------------------------------------------------------------------------
# should_confirm_via_gh  (dev-env#489)
#
# confirm_merge_via_gh itself shells out to `gh pr view` and is intentionally
# not tested (repo convention: no subprocess mocks) -- these tests cover only
# the pure "is it worth paying for a live confirmation?" decision. Its UTF-8
# output-decoding behavior (dev-env#503) is covered separately by
# test_winsubp.py's tests of _apply_windows_subprocess_defaults, the shared
# helper the call now goes through via the _winsubp import this module added.
# ---------------------------------------------------------------------------

def test_should_confirm_nonzero_exit_no_marker() -> str:
    # The dev-env#489 case: gh exited non-zero and its success marker did not
    # survive to the captured output -- worth a live confirmation.
    assert should_confirm_via_gh(1, "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'")
    return "exit 1, no marker -> confirm (the lost-marker case)"


def test_should_confirm_false_when_marker_present() -> str:
    # The marker already confirms the merge -- no need to pay for a network call.
    assert not should_confirm_via_gh(1, "Squashed and merged pull request #380")
    return "exit 1, marker present -> no confirm needed (cheap path already succeeded)"


def test_should_confirm_false_on_clean_exit() -> str:
    # A clean exit 0 with no marker is a genuine non-merge (gh pr create, git
    # push, or a queued --auto) -- never worth a network call.
    assert not should_confirm_via_gh(0, "")
    assert not should_confirm_via_gh(0, "✓ Pull request #380 will be automatically merged")
    return "exit 0, no marker -> no confirm (common non-merge path stays cheap)"


def test_should_confirm_default_exit_code_attempts() -> str:
    # The real payload can omit exitCode (ADR-049); hooks default it to -1, which
    # is != 0 -- so a missing exit code still attempts confirmation rather than
    # silently skipping a possibly-real merge.
    assert should_confirm_via_gh(-1, "")
    return "default/missing exit code (-1) with no marker -> confirm (safer default)"


# ---------------------------------------------------------------------------
# effective_merge_dir  (dev-env#446 / ADR-067)
# ---------------------------------------------------------------------------

def test_merge_dir_bare_merge_is_cwd() -> str:
    assert effective_merge_dir("gh pr merge --squash --delete-branch", "/session/cwd") == "/session/cwd"
    return "bare gh pr merge -> session cwd"


def test_merge_dir_cd_chain_redirects() -> str:
    out = effective_merge_dir("cd /Git/dev-env && gh pr merge --squash", "/Git/lifting-logbook")
    assert out == "/Git/dev-env", f"expected /Git/dev-env, got {out!r}"
    return "cd <repo> && gh pr merge -> that repo, not session cwd"


def test_merge_dir_cd_chain_multi_segment() -> str:
    # The merge is usually the tail of a longer chain.
    out = effective_merge_dir(
        "cd /Git/dev-env && git add . && gh pr merge --squash --delete-branch",
        "/Git/lifting-logbook",
    )
    assert out == "/Git/dev-env", f"got {out!r}"
    return "cd <repo> && ... && gh pr merge -> the repo dir"


def test_merge_dir_quoted_path() -> str:
    out = effective_merge_dir('cd "/Git/dir with spaces" && gh pr merge --squash', "/base")
    assert out == "/Git/dir with spaces", f"got {out!r}"
    return "quoted cd path -> unquoted target dir"


def test_merge_dir_relative_resolved_against_cwd() -> str:
    import os
    out = effective_merge_dir("cd sub/repo && gh pr merge --squash", "/base")
    # is_absolute_path (not os.path.isabs) so the "was the relative target
    # resolved to a rooted path?" check is itself version-agnostic: on 3.13
    # os.path.isabs("\\base\\sub\\repo") is False, which broke this assertion
    # independently of the effective_merge_dir fix (dev-env#732).
    assert is_absolute_path(out), f"relative target not resolved: {out!r}"
    assert os.path.basename(out) == "repo"
    assert out == os.path.normpath(os.path.join("/base", "sub/repo"))
    return "relative cd path -> normalized join under cwd"


# ---------------------------------------------------------------------------
# is_absolute_path  (dev-env#732 — the ntpath.isabs Python 3.13 regression)
# ---------------------------------------------------------------------------

def test_is_absolute_path_forward_slash_rooted() -> str:
    # The 3.13-sensitive case: a driveless, forward-slash-rooted path. `startswith`
    # carries it True independent of os.path.isabs, which returns False on 3.13
    # (True on <=3.12) for exactly this shape.
    assert is_absolute_path("/Git/dev-env") is True
    assert is_absolute_path("/Git/dir with spaces") is True
    return "forward-slash-rooted driveless path -> absolute"


def test_is_absolute_path_backslash_rooted() -> str:
    assert is_absolute_path("\\Git\\dev-env") is True
    assert is_absolute_path("\\\\server\\share") is True  # UNC
    return "backslash-rooted / UNC path -> absolute"


def test_is_absolute_path_drive_absolute() -> str:
    # Drive-absolute paths are unaffected by the 3.13 change; os.path.isabs
    # keeps them absolute on every version.
    assert is_absolute_path("C:/Git/dev-env") is True
    assert is_absolute_path("C:\\Git\\dev-env") is True
    return "drive-absolute path -> absolute"


def test_is_absolute_path_relative_and_empty() -> str:
    assert is_absolute_path("sub/repo") is False
    assert is_absolute_path("repo") is False
    assert is_absolute_path("") is False  # total: no exception on empty
    return "relative / empty path -> not absolute"


def test_is_absolute_path_simulates_py313_isabs() -> str:
    # Directly simulate Python 3.13 on this 3.12 interpreter: force os.path.isabs
    # to always return False (as 3.13 does for driveless-rooted paths) and prove
    # the leading-separator short-circuit still classifies rooted targets absolute
    # — i.e. the fix does NOT depend on the interpreter's isabs semantics. On bare
    # 3.12 the startswith and isabs branches are indistinguishable for "/x", so
    # this patch is the only way to pin the 3.13 behaviour locally. is_absolute_path
    # resolves os.path.isabs on the shared os.path module at call time, so patching
    # it here reaches _hookio's own lookup.
    import os as _os
    real_isabs = _os.path.isabs
    _os.path.isabs = lambda _p: False
    try:
        assert is_absolute_path("/Git/dev-env") is True
        assert is_absolute_path("\\Git\\dev-env") is True
        # A genuinely relative path is still False when isabs is neutralised.
        assert is_absolute_path("sub/repo") is False
    finally:
        _os.path.isabs = real_isabs
    assert _os.path.isabs("C:/x") is True, "os.path.isabs not restored"
    return "leading-separator short-circuit is independent of os.path.isabs (3.13 sim)"


def test_merge_dir_semicolon_chain() -> str:
    out = effective_merge_dir("cd /Git/dev-env ; gh pr merge --squash", "/base")
    assert out == "/Git/dev-env", f"got {out!r}"
    return "cd <repo> ; gh pr merge -> that repo (semicolon chain)"


def test_merge_dir_cd_after_merge_ignored() -> str:
    # A cd appearing only AFTER the merge does not govern it -> fall back to cwd.
    out = effective_merge_dir("gh pr merge --squash && cd /Git/elsewhere", "/base")
    assert out == "/base", f"cd after merge must not redirect: {out!r}"
    return "cd after the merge -> cwd (merge region excludes it)"


# ---------------------------------------------------------------------------
# scan_top_level  (dev-env#499, ADR-050 Amendment 5)
#
# The generic engine originally written for pr-merge-reminder.py, now the
# shared home for both pr-merge-reminder.py's and post-tool-use.py's command
# detection. Callers anchor check_fn via .match() on the lstripped token; the
# engine's job is only to make sure a top-level `;`/`\n`/`&&`/`||` split never
# happens INSIDE quoted/subshell/heredoc content in a way that would carve out
# a token whose start happens to look like a real invocation.
# ---------------------------------------------------------------------------


def _starts_with(*prefixes: str):
    return lambda token: token.lstrip().startswith(prefixes)


def test_scan_top_level_matches_bare_statement() -> str:
    assert scan_top_level("gh pr create --fill", _starts_with("gh pr create"))
    return "bare statement -> check_fn sees it"


def test_scan_top_level_no_match_returns_false() -> str:
    assert not scan_top_level("echo hello world", _starts_with("gh pr create"))
    return "no matching statement anywhere -> False"


def test_scan_top_level_does_not_split_inside_double_quotes() -> str:
    # Without quote-tracking, the && inside the quoted string would wrongly
    # split into a second token starting with "gh pr merge" -- exactly the
    # dev-env#499 false-positive class. Quote-tracking keeps this one unsplit
    # token, which starts with "git", not "gh pr create"/"gh pr merge".
    cmd = 'git commit -m "gh pr create --fill && gh pr merge --auto"'
    assert not scan_top_level(cmd, _starts_with("gh pr create", "gh pr merge"))
    return "&& inside double quotes is not a statement separator (dev-env#499)"


def test_scan_top_level_does_not_split_inside_single_quotes() -> str:
    cmd = "git commit -m 'gh pr create --fill && gh pr merge --auto'"
    assert not scan_top_level(cmd, _starts_with("gh pr create", "gh pr merge"))
    return "&& inside single quotes is not a statement separator"


def test_scan_top_level_does_not_split_inside_subshell() -> str:
    cmd = "echo $(gh pr create --fill && gh pr merge --auto)"
    assert not scan_top_level(cmd, _starts_with("gh pr create", "gh pr merge"))
    return "&& inside $() subshell is not a statement separator"


def test_scan_top_level_skips_heredoc_body() -> str:
    # The heredoc body is data (e.g. a commit message), not commands -- its
    # internal newline must not be treated as a statement separator, and its
    # content must never reach check_fn as its own token.
    cmd = "git commit -F - <<'EOF'\ngh pr create --fill\nEOF"
    assert not scan_top_level(cmd, _starts_with("gh pr create"))
    return "heredoc body lines are not scanned as top-level statements (dev-env#499)"


def test_scan_top_level_splits_on_and_and() -> str:
    assert scan_top_level("cd /a && gh pr create --fill", _starts_with("gh pr create"))
    return "&& outside quotes splits into independently-checked statements"


def test_scan_top_level_splits_on_semicolon() -> str:
    assert scan_top_level("cd /a ; gh pr create --fill", _starts_with("gh pr create"))
    return "; splits into independently-checked statements"


def test_scan_top_level_splits_on_or_or() -> str:
    assert scan_top_level("false || gh pr create --fill", _starts_with("gh pr create"))
    return "|| splits into independently-checked statements"


def test_scan_top_level_splits_on_newline() -> str:
    assert scan_top_level("echo hi\ngh pr create --fill", _starts_with("gh pr create"))
    return "newline splits into independently-checked statements"


# ---------------------------------------------------------------------------
# split_top_level  (dev-env#511, ADR-050 Amendment 7)
#
# scan_top_level (above) is now a thin `any(check_fn(seg) for seg in
# split_top_level(command))` wrapper over this segment-yielding engine --
# every scan_top_level test above already re-runs against split_top_level's
# implementation, so these tests focus on what only split_top_level exposes:
# the actual segment list (needed by pre-tool-use-canonical-mutate-guard.py's
# per-segment cd-scope / -C-redirect-skip / verb classification) and the
# opt-in split_pipe behavior scan_top_level never uses.
# ---------------------------------------------------------------------------


def test_split_top_level_no_separators_returns_whole_command() -> str:
    assert split_top_level("git status") == ["git status"]
    return "no separators -> single-element list of the whole command"


def test_split_top_level_splits_and_preserves_order() -> str:
    out = split_top_level("git status && git checkout -b foo ; git log")
    assert [s.strip() for s in out] == ["git status", "git checkout -b foo", "git log"], out
    return "&&/; split into segments, in original order"


def test_split_top_level_segments_are_unstripped() -> str:
    out = split_top_level("git status && git log")
    assert out == ["git status ", " git log"], out
    return "segments are returned unstripped (leading/trailing whitespace preserved)"


def test_split_top_level_no_split_inside_double_quotes() -> str:
    # The dev-env#511 false-positive: without quote-tracking, a naive splitter
    # carves "git checkout -b evil" out as its own segment from inside this
    # grep pattern, misclassifying a harmless git log as a checkout.
    cmd = 'git log --grep="foo && git checkout -b evil"'
    assert split_top_level(cmd) == [cmd]
    return "&& inside double quotes stays inside one segment (dev-env#511)"


def test_split_top_level_no_split_inside_single_quotes() -> str:
    cmd = "git log --grep='foo && git checkout -b evil'"
    assert split_top_level(cmd) == [cmd]
    return "&& inside single quotes stays inside one segment"


def test_split_top_level_pipe_not_split_by_default() -> str:
    # split_pipe defaults to False so scan_top_level's existing two callers
    # (pr-merge-reminder.py, post-tool-use.py, neither pipe-aware) see zero
    # behavior change from this function's introduction.
    cmd = "echo hi | git checkout -b foo"
    assert split_top_level(cmd) == [cmd]
    return "lone | is not a split point when split_pipe=False (default)"


def test_split_top_level_pipe_split_when_enabled() -> str:
    out = split_top_level("echo hi | git checkout -b foo", split_pipe=True)
    assert [s.strip() for s in out] == ["echo hi", "git checkout -b foo"], out
    return "lone | splits into segments when split_pipe=True (canonical-mutate-guard's need)"


def test_split_top_level_double_pipe_stays_one_operator_even_with_split_pipe() -> str:
    # || must stay the single or-operator, not two lone-pipe splits, even
    # when split_pipe=True enables lone-| splitting.
    out = split_top_level("false || git checkout -b foo", split_pipe=True)
    assert [s.strip() for s in out] == ["false", "git checkout -b foo"], out
    return "|| is not double-counted as two pipe-splits when split_pipe=True"


def test_split_top_level_pipe_inside_quotes_not_split_even_when_enabled() -> str:
    cmd = 'git log --grep="foo | git checkout -b evil"'
    assert split_top_level(cmd, split_pipe=True) == [cmd]
    return "quote-tracking applies to | too -- not split inside quotes even with split_pipe=True"


def test_split_top_level_bare_heredoc_body_not_its_own_segment() -> str:
    # A body line that itself STARTS with a mutating-looking verb must not
    # become its own segment -- the bare-heredoc analogue of dev-env#481
    # (which only covered a heredoc fed through a $(cat <<...) command sub).
    cmd = "git status <<EOF\ngit commit --amend\nEOF"
    assert split_top_level(cmd) == [cmd]
    return "bare (non-command-sub) heredoc body stays inside one segment"


def test_split_top_level_command_sub_heredoc_not_its_own_segment() -> str:
    cmd = (
        "gh issue create --body \"$(cat <<'EOF'\n"
        "git commit example\n"
        "EOF\n"
        ")\""
    )
    assert split_top_level(cmd) == [cmd]
    return "$(cat <<'MARKER'...) span stays inside one segment (dev-env#481, generalized)"


def test_split_top_level_real_command_after_heredoc_still_split() -> str:
    cmd = (
        "gh issue create --body \"$(cat <<'EOF'\n"
        "git commit example\n"
        "EOF\n"
        ")\" && git checkout -b evil"
    )
    out = split_top_level(cmd)
    assert len(out) == 2 and out[1].strip() == "git checkout -b evil", out
    return "a real segment chained after a heredoc-containing segment still splits out"


def test_split_top_level_unterminated_quote_drops_trailing_segment() -> str:
    # Mirrors scan_top_level's pre-existing fail-permissive contract: an
    # unterminated quote/subshell/heredoc means the trailing segment is
    # dropped rather than returned (matches the original `if stack ==
    # ["top"]: ...` guard this function was extracted from).
    out = split_top_level('git status && git commit -m "unterminated')
    assert out == ["git status "], out
    return "unterminated trailing quote drops that segment (matches scan_top_level's prior contract)"


# ---------------------------------------------------------------------------
# PowerShell here-string + brace-group support  (dev-env#620, ADR-071
# Amendment 4)
#
# PowerShell 5.1 has no && / || (confirmed parser errors in this environment),
# so its documented "run B only if A succeeds" idiom is `A; if ($?) { B }` --
# and split_top_level had no concept of an unquoted `{` as a statement
# boundary, so every check_fn's segment-start-anchored `.match()` never saw
# `B`. Separately, a PowerShell here-string (@'...'@ / @"..."@) is the
# functional equivalent of a bash heredoc but uses a different opener/closer;
# without recognizing it, the naive POSIX single/double-quote tracking can
# close prematurely at a stray quote character embedded in the body, leaving
# the parser desynchronized for the remainder of the command.
# ---------------------------------------------------------------------------


def test_scan_top_level_detects_powershell_conditional_brace_idiom() -> str:
    # Before the { fix: split_top_level never made a new segment at `{`, so
    # this whole tail was one segment starting with "if", never "gh pr merge"
    # -- the PowerShell tool's own documented conditional-chain idiom silently
    # evaded every check_fn anchored via .match() on a segment's start.
    cmd = "git add -A; if ($?) { gh pr merge --squash }"
    assert scan_top_level(cmd, _starts_with("gh pr merge"))
    return "PowerShell 'A; if ($?) { B }' idiom: B is now its own detectable segment"


def test_scan_top_level_detects_bash_brace_group_idiom() -> str:
    # The identical gap already existed for bash's own brace-group syntax.
    cmd = "{ git checkout main; }"
    assert scan_top_level(cmd, _starts_with("git checkout"))
    return "bash brace-group '{ cmd; }' idiom: cmd is now its own detectable segment (bonus fix)"


def test_split_top_level_brace_inside_quotes_not_split() -> str:
    # An unquoted { is a split trigger; one inside a quoted string (e.g. a
    # commit message with a literal brace) must stay protected, exactly like
    # every other separator this engine already respects quote state for.
    cmd = 'git commit -m "release {beta}" && git push'
    out = split_top_level(cmd)
    assert len(out) == 2, out
    assert out[0] == 'git commit -m "release {beta}" ', out
    assert out[1].strip() == "git push", out
    return "{ inside a quoted string is not a split trigger -- only an unquoted { is"


def test_split_top_level_brace_splits_even_with_split_pipe_enabled() -> str:
    # pre-tool-use-canonical-mutate-guard.py runs with split_pipe=True -- the
    # new { handling must not be accidentally scoped out under that mode.
    cmd = "git add -A; if ($?) { git checkout main }"
    out = split_top_level(cmd, split_pipe=True)
    assert any(s.lstrip().startswith("git checkout") for s in out), out
    return "{ splitting still applies when split_pipe=True (canonical-mutate-guard's mode)"


def test_scan_top_level_herestring_with_embedded_quote_no_longer_hides_real_command() -> str:
    # The load-bearing regression. A SYNTACTICALLY VALID PowerShell here-string
    # (opener immediately followed by a line break, per real PowerShell
    # grammar -- dev-env#762 review: an earlier fixture used an invalid
    # same-line body and was rewritten to this valid multi-line form).
    # WITHOUT here-string awareness, the naive POSIX single-quote tracking
    # closes prematurely at the stray apostrophe in "don't" (rather than the
    # real line-start '@ closer), toggles back into "single" state at the
    # closer's own leading ', and then never finds another ' to close it --
    # the entire rest of the command, including the real "gh pr merge"
    # invocation, was silently DROPPED as an "unterminated quote" tail
    # (split_top_level's existing fail-permissive contract) before this fix.
    # Traced by hand against the pre-fix parser: it returns exactly one
    # segment, "@'\ndon't merge yet", with "gh pr merge --squash" gone.
    cmd = "@'\ndon't merge yet\n'@\ngh pr merge --squash"
    assert scan_top_level(cmd, _starts_with("gh pr merge"))
    return "PowerShell here-string body with an embedded apostrophe no longer hides a real command after it"


def test_split_top_level_herestring_literal_embedded_quote_segments_correctly() -> str:
    cmd = "@'\ndon't merge yet\n'@\ngh pr merge --squash"
    out = split_top_level(cmd)
    assert len(out) == 2, out
    assert out[0] == "@'\ndon't merge yet\n'@", out
    assert out[1] == "gh pr merge --squash", out
    return "literal here-string (incl. embedded apostrophe) is one segment; real command after is separate"


def test_scan_top_level_herestring_expandable_with_embedded_quote_no_longer_hides_real_command() -> str:
    # Same regression, expandable (@"..."@) form, also syntactically valid
    # (opener immediately followed by a line break): one embedded " prematurely
    # closes the naive double-quote tracking, then a second " (the real
    # closer's own leading quote) reopens it with nothing left to close --
    # dropping "gh pr merge --squash" the same way.
    cmd = '@"\nsay "hi now\n"@\ngh pr merge --squash'
    assert scan_top_level(cmd, _starts_with("gh pr merge"))
    return "PowerShell expandable here-string body with an embedded double-quote no longer hides a real command after it"


def test_split_top_level_herestring_expandable_embedded_quote_segments_correctly() -> str:
    cmd = '@"\nsay "hi now\n"@\ngh pr merge --squash'
    out = split_top_level(cmd)
    assert len(out) == 2, out
    assert out[0] == '@"\nsay "hi now\n"@', out
    assert out[1] == "gh pr merge --squash", out
    return "expandable here-string (incl. embedded double-quote) is one segment; real command after is separate"


def test_split_top_level_herestring_no_closer_runs_to_end() -> str:
    # Mirrors _find_heredoc_end's own fallback: no closer anywhere -> the
    # entire rest of the string is consumed as one (safely opaque) segment
    # rather than raising or leaving the parser in a dropped-trailing-segment
    # state (that state is specific to an unterminated QUOTE, not a heredoc/
    # here-string, neither of which touch `stack` at all). The opener must
    # still be a syntactically valid one (immediately followed by a line
    # break) for _is_herestring_opener to recognize it at all.
    cmd = "@'\nno closer anywhere\ngh pr merge --squash"
    out = split_top_level(cmd)
    assert out == [cmd], out
    return "here-string with no closer found -> runs to end of string (matches heredoc's fallback)"


def test_split_top_level_herestring_same_line_body_not_recognized_as_opener() -> str:
    # dev-env#762 review regression proof: a bare "@'"/'@"' NOT followed by a
    # line break (real PowerShell requires the opener to be the last thing on
    # its line) must NOT be treated as a here-string opener -- otherwise
    # ordinary bash text containing "@'"/'@"' anywhere (e.g. a stray
    # `user@'host'`-shaped token) would swallow a real trailing separator and
    # command. Both confirmed live regressions before this fix.
    cmd1 = "echo a@'b' ; git checkout -b evil"
    out1 = split_top_level(cmd1)
    assert any(s.lstrip().startswith("git checkout") for s in out1), out1

    cmd2 = 'echo x@"y" && git reset --hard HEAD'
    out2 = split_top_level(cmd2)
    assert any(s.lstrip().startswith("git reset") for s in out2), out2
    return "a same-line '@'/'@\"' (not a real here-string opener) no longer swallows a real trailing command"


# ---------------------------------------------------------------------------
# mask_quoted_spans  (dev-env#626, ADR-050 Amendment 15)
#
# The _REPO_FLAG_RE family's (?<!\S) lookbehind (Amendment 14) stops a
# mid-word match but not a legitimately space-separated "-R other/repo"
# substring sitting inside a quoted --subject/--body value. mask_quoted_spans
# blinds single/double-quoted spans, $() subshells, and heredoc bodies before
# such a regex ever runs.
# ---------------------------------------------------------------------------

def test_mask_quoted_spans_no_quotes_unchanged() -> str:
    cmd = "gh pr merge 42 --repo brownm09/dev-env --squash"
    assert mask_quoted_spans(cmd) == cmd
    return "no quotes/subshells/heredocs anywhere -> unchanged"


def test_mask_quoted_spans_double_quoted_span_masked() -> str:
    # The exact dev-env#626 repro shape.
    cmd = 'gh pr merge 42 --subject "see -R other/repo for context"'
    masked = mask_quoted_spans(cmd)
    assert masked == "gh pr merge 42 --subject " + "#" * len('"see -R other/repo for context"'), masked
    assert "-R" not in masked
    return "double-quoted --subject value (incl. the quote chars) fully masked (dev-env#626 repro)"


def test_mask_quoted_spans_single_quoted_span_masked() -> str:
    cmd = "gh pr merge 42 --subject 'see -R other/repo for context'"
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked
    assert masked.count("#") == len("'see -R other/repo for context'")
    return "single-quoted value fully masked"


def test_mask_quoted_spans_escaped_quote_does_not_end_span_early() -> str:
    cmd = 'echo "a \\"b\\" -R x/y" && echo done'
    masked = mask_quoted_spans(cmd)
    before, after = masked.split("&&")
    assert "-R" not in before, masked
    assert after == " echo done", masked
    return "an escaped quote inside double quotes does not end the span early"


def test_mask_quoted_spans_subshell_masked() -> str:
    cmd = "echo $(echo -R x/y) && echo done"
    masked = mask_quoted_spans(cmd)
    before, after = masked.split("&&")
    assert "-R" not in before, masked
    assert after == " echo done", masked
    return "$() subshell content masked"


def test_mask_quoted_spans_nested_subshell_inside_double_quotes_is_one_span() -> str:
    # A $() nested inside "..." must close as ONE contiguous opaque span --
    # the inner subshell could itself contain a quote that would otherwise
    # end the outer double-quote early (the reason split_top_level tracks
    # nested state at all, per its own docstring).
    cmd = 'echo "a $(echo -R x/y) c" && echo done'
    masked = mask_quoted_spans(cmd)
    assert masked == "echo " + "#" * len('"a $(echo -R x/y) c"') + " && echo done", masked
    return "nested $() inside double quotes closes as one contiguous opaque span"


def test_mask_quoted_spans_bare_heredoc_body_masked() -> str:
    cmd = "git status <<EOF\nsome -R x/y body\nEOF\necho after"
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked
    assert masked.endswith("\necho after"), masked
    return "bare heredoc body masked; trailing real command after it survives"


def test_mask_quoted_spans_command_sub_heredoc_masked() -> str:
    cmd = "echo \"$(cat <<'EOF'\n-R x/y\nEOF\n)\" && echo done"
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked
    assert masked.endswith(" && echo done"), masked
    return "$(cat <<'EOF' ...) heredoc-in-subshell-in-quotes masked as one span"


def test_mask_quoted_spans_herestring_literal_masked() -> str:
    # PowerShell literal here-string (@'...'@) -- the heredoc-equivalent
    # opacity rule, dev-env#620 / ADR-071 Amendment 4. The body deliberately
    # embeds a stray apostrophe ("don't") -- dev-env#762 review: a body with
    # NO embedded quote masks identically under the naive pre-fix parser (it
    # closes "by luck" at the real closer's own leading quote), so it never
    # actually exercised _find_herestring_end's masking path. With the stray
    # apostrophe, the pre-fix parser desyncs (closes early, reopens at the
    # closer, then runs unterminated to end-of-string) and FAILS both
    # assertions below: "-R" survives unmasked (it falls in the gap between
    # the two mis-toggled spans) and "echo after" gets swallowed into the
    # trailing unterminated span instead of surviving. Verified by hand.
    cmd = "echo before && @'\nsome don't merge -R x/y body\n'@\necho after"
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked, masked
    assert masked.endswith("\necho after"), masked
    return "PowerShell literal here-string body (incl. embedded apostrophe) masked; trailing real command after it survives"


def test_mask_quoted_spans_herestring_expandable_masked() -> str:
    # Same discriminating-fixture rationale as the literal-form test above,
    # with a stray embedded double-quote instead of an apostrophe.
    cmd = 'echo before && @"\nsay "hi -R x/y body\n"@\necho after'
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked, masked
    assert masked.endswith("\necho after"), masked
    return "PowerShell expandable here-string body (incl. embedded double-quote) masked; trailing real command after it survives"


def test_mask_quoted_spans_preserves_newlines() -> str:
    cmd = 'echo "line1\nline2 -R x/y\nline3"'
    masked = mask_quoted_spans(cmd)
    assert masked.count("\n") == cmd.count("\n"), masked
    assert "-R" not in masked
    return "newlines survive unmasked inside a masked multi-line double-quoted span"


def test_mask_quoted_spans_unterminated_double_quote_masks_tail() -> str:
    cmd = 'git commit -m "unterminated -R x/y'
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked
    assert masked.startswith("git commit -m "), masked
    return "unterminated double quote masks the rest of the string (fail-permissive, no crash)"


def test_mask_quoted_spans_unterminated_subshell_masks_tail() -> str:
    cmd = "echo $(echo -R x/y"
    masked = mask_quoted_spans(cmd)
    assert "-R" not in masked
    assert masked.startswith("echo "), masked
    return "unterminated $() subshell masks the rest of the string (fail-permissive, no crash)"


def test_mask_quoted_spans_real_flag_survives_alongside_quoted_decoy() -> str:
    cmd = 'gh pr merge 42 --repo brownm09/dev-env --subject "see -R other/repo for context"'
    masked = mask_quoted_spans(cmd)
    assert "--repo brownm09/dev-env" in masked, masked
    assert "-R" not in masked
    return "a real, unquoted --repo flag survives byte-for-byte alongside a masked quoted decoy"


# ---------------------------------------------------------------------------
# mask_quoted_spans / split_top_level cross-consistency  (dev-env#626, ADR-050
# Amendment 15)
#
# mask_quoted_spans is an independent state machine, not a refactor of
# split_top_level's internals (see _hookio.py's module comment for why), so
# nothing statically guarantees the two never drift apart on what counts as
# "inside a quote/subshell/heredoc." This is the enforced, not prose, guard
# against that drift -- ADR-050 Amendment 11's own precedent: a written "keep
# these in sync" reminder is exactly as missable as the bug it guards
# against; the durable form is a running test.
#
# Each fixture places a decoy "&&" INSIDE an opaque span, followed by a real
# top-level "&&" outside it. split_top_level must produce exactly 2 segments
# (the decoy did not split); mask_quoted_spans must mask the decoy (so it no
# longer reads as literal "&&") while leaving the later, real "&&" untouched
# -- tying both functions' opacity judgments to the same fixture string.
# ---------------------------------------------------------------------------

_CONSISTENCY_FIXTURES = [
    'git commit -m "a && b" && git push',
    "git commit -m 'a && b' && git push",
    'echo "$(echo a && b)" && git push',
    "git status <<EOF\na && b\nEOF\ngit push && echo done",
    # PowerShell here-string (dev-env#620, ADR-071 Amendment 4): a decoy &&
    # inside the here-string body, followed by a real && outside it. Opener
    # immediately followed by a line break -- the syntactically valid form.
    "@'\na && b\n'@ && git push",
]


def test_mask_quoted_spans_agrees_with_split_top_level() -> str:
    for cmd in _CONSISTENCY_FIXTURES:
        segments = split_top_level(cmd)
        assert len(segments) == 2, (cmd, segments)
        masked = mask_quoted_spans(cmd)
        real_and_idx = cmd.rindex("&&")
        assert "&&" not in masked[:real_and_idx], (cmd, masked)
        assert masked[real_and_idx:real_and_idx + 2] == "&&", (cmd, masked)
    return (
        f"{len(_CONSISTENCY_FIXTURES)} fixtures: a decoy && inside an opaque span "
        "agrees between split_top_level (does not split there) and "
        "mask_quoted_spans (masks it), while the real && after it still "
        "splits / stays unmasked in both"
    )


# ---------------------------------------------------------------------------
# mask_prose_flag_values  (dev-env#634, ADR-050 Amendment 17)
#
# The PR-URL-regex analog of mask_quoted_spans's own fix: a --subject/--body
# value can hide a URL-shaped decoy the same way it could hide a --repo/-R
# decoy (dev-env#626) -- but mask_quoted_spans itself can't be reused
# unmodified, since a bare quoted positional URL argument is a legitimate,
# already-tested shape (post-pr-merge-project.py's test_repo_from_cross_repo_url)
# that blanket-masking every quoted span would blind along with the decoy.
# mask_prose_flag_values instead masks only the value immediately following a
# --subject/-t/--body/-b flag.
# ---------------------------------------------------------------------------

def test_mask_prose_flag_values_no_prose_flags_unchanged() -> str:
    cmd = 'gh pr merge "https://github.com/brownm09/dev-env/pull/554" --squash'
    assert mask_prose_flag_values(cmd) == cmd
    return "no --subject/--body/-t/-b anywhere -> unchanged"


def test_mask_prose_flag_values_double_quoted_subject_masked() -> str:
    # The dev-env#634 repro shape: a URL-shaped decoy inside --subject prose.
    cmd = 'gh pr merge 42 --subject "see https://github.com/other/repo/pull/1 for context"'
    masked = mask_prose_flag_values(cmd)
    assert masked == "gh pr merge 42 --subject " + "#" * len(
        '"see https://github.com/other/repo/pull/1 for context"'
    ), masked
    assert "github.com" not in masked
    return "URL-shaped decoy inside a double-quoted --subject value fully masked (dev-env#634)"


def test_mask_prose_flag_values_single_quoted_body_masked() -> str:
    cmd = "gh pr merge 42 --body 'see https://github.com/other/repo/pull/1 for context'"
    masked = mask_prose_flag_values(cmd)
    assert "github.com" not in masked
    assert masked.count("#") == len("'see https://github.com/other/repo/pull/1 for context'")
    return "single-quoted --body value fully masked"


def test_mask_prose_flag_values_short_flag_forms_masked() -> str:
    cmd_t = 'gh pr merge 42 -t "see https://github.com/other/repo/pull/1 for context"'
    cmd_b = 'gh pr merge 42 -b "see https://github.com/other/repo/pull/1 for context"'
    assert "github.com" not in mask_prose_flag_values(cmd_t)
    assert "github.com" not in mask_prose_flag_values(cmd_b)
    return "short flag forms -t/-b masked identically to --subject/--body"


def test_mask_prose_flag_values_equals_form_masked() -> str:
    cmd = 'gh pr merge 42 --subject="see https://github.com/other/repo/pull/1 for context"'
    assert "github.com" not in mask_prose_flag_values(cmd)
    return "--subject=<quoted value> (equals form) masked"


def test_mask_prose_flag_values_unquoted_value_also_masked() -> str:
    # Review finding: an unquoted single-token value can ITSELF be the decoy
    # -- no surrounding prose needed to hide in (e.g. --body <bare-decoy-url>
    # with no quotes at all). Masked the same way a quoted value is.
    cmd = "gh pr merge 42 --subject urgent-fix --squash"
    masked = mask_prose_flag_values(cmd)
    assert masked == "gh pr merge 42 --subject " + "#" * len("urgent-fix") + " --squash", masked
    return "unquoted single-token --subject value masked too (not just quoted-with-prose decoys)"


def test_mask_prose_flag_values_unquoted_url_decoy_masked() -> str:
    # The concrete repro this fix closes: an unquoted --body value that IS a
    # decoy URL, with no quotes and no surrounding prose at all.
    cmd = "gh pr merge 380 --body https://github.com/other/repo/pull/1 --squash"
    masked = mask_prose_flag_values(cmd)
    assert "other/repo" not in masked, masked
    assert masked.startswith("gh pr merge 380 --body ") and masked.endswith(" --squash"), masked
    return "unquoted --body value that is itself a URL-shaped decoy is masked"


def test_mask_prose_flag_values_unquoted_value_at_end_of_string_masked() -> str:
    # No trailing whitespace after the unquoted value -- masking must stop at
    # the string's end without running past it or raising.
    cmd = "gh pr merge 380 --body urgent-fix"
    masked = mask_prose_flag_values(cmd)
    assert masked == "gh pr merge 380 --body " + "#" * len("urgent-fix"), masked
    return "unquoted value at end of string masked cleanly to end of string"


def test_mask_prose_flag_values_bare_quoted_url_argument_not_masked() -> str:
    # The critical negative case: a bare quoted PR-URL positional argument
    # (never preceded by --subject/--body/-t/-b) is NOT a prose-flag value,
    # so it must survive byte-for-byte -- this is what keeps
    # test_repo_from_cross_repo_url passing after this fix.
    cmd = 'gh pr merge "https://github.com/brownm09/dev-env/pull/554" --squash --delete-branch'
    assert mask_prose_flag_values(cmd) == cmd
    return "bare quoted positional PR-URL argument untouched (not a prose-flag value)"


def test_mask_prose_flag_values_real_url_survives_alongside_masked_decoy() -> str:
    cmd = (
        'gh pr merge 42 --subject "see https://github.com/other/repo/pull/1 for context" '
        "https://github.com/brownm09/dev-env/pull/42 --squash"
    )
    masked = mask_prose_flag_values(cmd)
    assert "other/repo/pull/1" not in masked, masked
    assert "https://github.com/brownm09/dev-env/pull/42" in masked, masked
    return "decoy URL inside --subject masked while the real, later URL survives byte-for-byte"


def test_mask_prose_flag_values_subshell_body_masked() -> str:
    # A --body value built via $(cat <<'EOF' ... EOF) is a real, precedented
    # shape in this hook family (stop-tile-enumeration-gate.py's own
    # session_resolved_issue_numbers docstring) -- reusing _opaque_spans means
    # this is masked as the single opaque span it already is, with no extra
    # logic in mask_prose_flag_values itself.
    cmd = "gh pr merge 42 --body \"$(cat <<'EOF'\nsee https://github.com/other/repo/pull/1\nEOF\n)\" --squash"
    masked = mask_prose_flag_values(cmd)
    assert "other/repo/pull/1" not in masked, masked
    assert masked.endswith(' --squash'), masked
    return "$(cat <<'EOF' ...) --body value masked as one opaque span"


def test_mask_prose_flag_values_mid_word_flag_not_matched() -> str:
    # The (?<!\S) lookbehind requires -t/-b to start a standalone token, so a
    # flag-like substring mid-word is never mistaken for a real prose flag.
    cmd = 'gh pr merge 42 xx-b "see https://github.com/other/repo/pull/1 for context"'
    assert mask_prose_flag_values(cmd) == cmd
    return "mid-word '-b' (not a standalone token) -> unchanged, not falsely matched"


# ---------------------------------------------------------------------------
# is_merge_help_only  (dev-env#557)
#
# `gh pr merge --help` textually satisfies every `is_pr_merge_command` /
# `_check_merge_stmt` predicate in the hook family -- it *is* a `gh pr merge`
# invocation. Since --help prints no success marker, `should_confirm_via_gh`
# returns True (no marker + non-zero/-1 exit), triggering a live `gh pr view`
# confirmation with no explicit PR number that resolves against cwd's checked-
# out branch and can misattribute an unrelated already-merged PR to the
# --help invocation (dev-env#557 -- a live incident: `gh pr merge --help` run
# purely to check flag semantics moved an unrelated issue's project-board item
# to Done). --help/-h can *categorically never* attempt a real merge, so a
# command consisting only of --help/-h `gh pr merge` invocations should
# short-circuit before that marker/exit-code logic runs at all.
# ---------------------------------------------------------------------------

def test_is_merge_help_only_bare_help_long_flag() -> str:
    assert is_merge_help_only("gh pr merge --help")
    return "gh pr merge --help -> True"


def test_is_merge_help_only_bare_help_short_flag() -> str:
    assert is_merge_help_only("gh pr merge -h")
    return "gh pr merge -h -> True"


def test_is_merge_help_only_real_merge_with_number_is_false() -> str:
    assert not is_merge_help_only("gh pr merge 380 --squash")
    return "gh pr merge 380 --squash (no --help) -> False"


def test_is_merge_help_only_bare_merge_no_help_is_false() -> str:
    # The dominant workflow form -- current-branch merge, no --help anywhere.
    assert not is_merge_help_only("gh pr merge --squash --delete-branch")
    return "gh pr merge --squash --delete-branch (bare current-branch merge) -> False"


def test_is_merge_help_only_no_merge_invocation_at_all_is_false() -> str:
    # Callers already gate on their own is_pr_merge_command/_check_merge_stmt
    # check first, so this predicate seeing zero merge segments must also be
    # False -- it never independently claims "this is a help-only command"
    # when there was no merge command to begin with.
    assert not is_merge_help_only("git status")
    return "no gh pr merge invocation anywhere -> False"


def test_is_merge_help_only_chained_help_then_real_merge_is_false() -> str:
    # A real merge attempt elsewhere in the same command must never be
    # suppressed just because an earlier segment was a harmless --help check.
    assert not is_merge_help_only("gh pr merge --help && gh pr merge 380 --squash")
    return "gh pr merge --help && gh pr merge 380 --squash -> False (real merge not suppressed)"


def test_is_merge_help_only_chained_two_help_invocations_is_true() -> str:
    assert is_merge_help_only("gh pr merge --help && gh pr merge -h")
    return "gh pr merge --help && gh pr merge -h -> True (all segments are help-only)"


def test_is_merge_help_only_heredoc_mention_of_help_text_ignored() -> str:
    # A heredoc body merely mentioning "--help" as prose must not make an
    # unrelated real merge elsewhere in the same command look help-only.
    command = (
        "git commit -m \"$(cat <<'EOF'\n"
        "run gh pr merge --help to see flag semantics\n"
        "EOF\n"
        ")\" && gh pr merge 380 --squash"
    )
    assert not is_merge_help_only(command)
    return "heredoc prose mentioning 'gh pr merge --help' does not affect a real merge elsewhere (dev-env#557)"


def test_is_merge_help_only_heredoc_mention_with_only_help_segment_present() -> str:
    # Same heredoc-mention shape, but this time the only real top-level `gh pr
    # merge` segment IS a genuine --help invocation -- the heredoc prose must
    # not count as a second (non-help) merge segment that would flip the
    # all() to False.
    command = (
        "git commit -m \"$(cat <<'EOF'\n"
        "run gh pr merge --squash to actually merge\n"
        "EOF\n"
        ")\" && gh pr merge --help"
    )
    assert is_merge_help_only(command)
    return "heredoc prose mentioning a non-help 'gh pr merge' is not a segment -> True"


def test_is_merge_help_only_quoted_argument_mention_ignored() -> str:
    # "gh pr merge" appearing only inside a quoted argument (not a genuine
    # invocation) must not be treated as a second merge segment.
    command = 'git commit -m "reminder: gh pr merge --squash later" && gh pr merge --help'
    assert is_merge_help_only(command)
    return "quoted-argument mention of 'gh pr merge' is not a segment -> True"


def test_is_merge_help_only_help_flag_not_confused_with_similar_flag() -> str:
    # --help must be matched as a standalone token, not as a substring of a
    # differently-named flag or value.
    assert not is_merge_help_only("gh pr merge --helpful-flag-name 380")
    return "a flag merely containing 'help' as a substring is not --help -> False"


def test_is_merge_help_only_cd_prefixed_help_is_true() -> str:
    assert is_merge_help_only("cd C:/Users/brown/Git/dev-env && gh pr merge --help")
    return "cd <repo> && gh pr merge --help -> True (cd is its own segment, ignored)"


def test_is_merge_help_only_case_insensitive() -> str:
    # gh.exe on Windows and the invocation itself are matched case-insensitively.
    assert is_merge_help_only("GH.EXE PR MERGE --HELP")
    return "GH.EXE PR MERGE --HELP -> True (case-insensitive)"


# ---------------------------------------------------------------------------
# is_help_only  (dev-env#636)
#
# is_merge_help_only is now a thin wrapper over this generalized core (the
# tests above already pin its externally-visible behavior unchanged). These
# tests exercise is_help_only directly with a NON-merge invocation_re, proving
# the extraction is genuinely generic rather than merge-specific -- the real
# motivating second caller is post-tool-use.py's is_issue_create_help_only /
# is_pr_create_help_only (test_post_tool_use.py), which reuse this exact core.
# ---------------------------------------------------------------------------

_ISSUE_CREATE_INVOCATION_RE = re.compile(r"gh(?:\.exe)?\s+issue\s+create\b", re.IGNORECASE)


def test_is_help_only_generic_help_invocation_is_true() -> str:
    assert is_help_only("gh issue create --help", _ISSUE_CREATE_INVOCATION_RE)
    return "is_help_only with a non-merge invocation_re: --help invocation -> True (dev-env#636)"


def test_is_help_only_generic_real_invocation_is_false() -> str:
    assert not is_help_only('gh issue create --title "x"', _ISSUE_CREATE_INVOCATION_RE)
    return "is_help_only: a real (non-help) invocation of the custom invocation_re -> False"


def test_is_help_only_generic_no_matching_segment_is_false() -> str:
    assert not is_help_only("git status", _ISSUE_CREATE_INVOCATION_RE)
    return "is_help_only: no segment matches invocation_re at all -> False"


def test_is_help_only_generic_chained_help_then_real_is_false() -> str:
    assert not is_help_only(
        'gh issue create --help && gh issue create --title "x"', _ISSUE_CREATE_INVOCATION_RE
    )
    return "is_help_only: real invocation elsewhere in the chain is not suppressed -> False"


# ---------------------------------------------------------------------------
# strip_line_continuations (dev-env#823 fix hoisted to _hookio in dev-env#831)
#
# A backslash immediately followed by LF is a shell line-continuation (a
# line-join), NOT a statement separator. The four `gh pr merge` boundary-finders
# strip these before their newline-split / `[^\n...]` region regex, or a
# multi-line command truncates at the first continuation and a flag/value on a
# continued line is silently lost. LF-only: bash does not treat a backslash
# followed by CRLF as a continuation.
# ---------------------------------------------------------------------------

def test_strip_line_continuations_joins_backslash_lf() -> str:
    assert strip_line_continuations("a \\\nb") == "a b"
    return "a backslash+LF continuation is removed, joining the two physical lines"


def test_strip_line_continuations_bare_newline_untouched() -> str:
    # A bare newline (no preceding backslash) is a real separator -- untouched.
    assert strip_line_continuations("a\nb") == "a\nb"
    return "a bare newline (no backslash) is left intact as a real separator"


def test_strip_line_continuations_crlf_left_intact() -> str:
    # LF-only by design: bash does NOT treat a backslash followed by CRLF as a
    # continuation, so `r"\\\n"` (not `r"\\\r?\n"`) leaves this unchanged.
    assert strip_line_continuations("a \\\r\nb") == "a \\\r\nb"
    return "a backslash+CRLF is left intact (the LF-only rule)"


def test_strip_line_continuations_lone_backslash_untouched() -> str:
    assert strip_line_continuations("a\\b") == "a\\b"
    return "a lone backslash not followed by a newline is untouched"


def test_strip_line_continuations_multiple() -> str:
    assert strip_line_continuations("a \\\nb \\\nc") == "a b c"
    return "every backslash+LF continuation in the string is joined"


def test_strip_line_continuations_realistic_gh_merge() -> str:
    # The dev-env#831 motivating shape: a --repo on a continued line survives.
    cmd = 'gh pr merge 42 --squash \\\n  --repo brownm09/dev-env \\\n  --subject "x"'
    joined = strip_line_continuations(cmd)
    assert "\\\n" not in joined
    assert "--repo brownm09/dev-env" in joined and "--subject" in joined
    return "a multi-line gh pr merge joins to one logical line, --repo preserved"


def main() -> int:
    tests = [
        ("reads command output from stdout", test_reads_stdout),
        ("combines stdout and stderr", test_combines_stdout_and_stderr),
        ("stderr-only (gh pr merge shape)", test_stderr_only),
        ("legacy output field still works", test_legacy_output_fallback),
        ("stdout preferred over legacy output", test_stdout_preferred_over_legacy_output),
        ("empty/malformed payloads yield ''", test_empty_and_malformed_payloads),
        ("pre-fix output read was empty (#377 root cause)", test_old_output_read_would_have_been_empty),
        ("merge marker detected (incl cross-repo)", test_merge_marker_detected),
        ("merge marker excludes auto/failure/stray", test_merge_marker_excludes_auto_failure_and_stray),
        ("merge PR number from output", test_merge_pr_number_from_output),
        ("should_confirm_via_gh: exit!=0, no marker -> True", test_should_confirm_nonzero_exit_no_marker),
        ("should_confirm_via_gh: marker present -> False", test_should_confirm_false_when_marker_present),
        ("should_confirm_via_gh: clean exit -> False", test_should_confirm_false_on_clean_exit),
        ("should_confirm_via_gh: default exit code -> True", test_should_confirm_default_exit_code_attempts),
        ("merge dir: bare merge -> cwd", test_merge_dir_bare_merge_is_cwd),
        ("merge dir: cd <repo> && merge -> that repo", test_merge_dir_cd_chain_redirects),
        ("merge dir: cd <repo> && ... && merge -> repo dir", test_merge_dir_cd_chain_multi_segment),
        ("merge dir: quoted cd path", test_merge_dir_quoted_path),
        ("merge dir: relative path resolved vs cwd", test_merge_dir_relative_resolved_against_cwd),
        ("merge dir: semicolon chain", test_merge_dir_semicolon_chain),
        ("merge dir: cd after merge ignored", test_merge_dir_cd_after_merge_ignored),
        ("is_absolute_path: forward-slash rooted (dev-env#732)", test_is_absolute_path_forward_slash_rooted),
        ("is_absolute_path: backslash / UNC rooted", test_is_absolute_path_backslash_rooted),
        ("is_absolute_path: drive-absolute", test_is_absolute_path_drive_absolute),
        ("is_absolute_path: relative / empty -> False", test_is_absolute_path_relative_and_empty),
        ("is_absolute_path: 3.13 isabs simulation", test_is_absolute_path_simulates_py313_isabs),
        ("scan_top_level: bare statement matches", test_scan_top_level_matches_bare_statement),
        ("scan_top_level: no match anywhere -> False", test_scan_top_level_no_match_returns_false),
        ("scan_top_level: no split inside double quotes (dev-env#499)", test_scan_top_level_does_not_split_inside_double_quotes),
        ("scan_top_level: no split inside single quotes", test_scan_top_level_does_not_split_inside_single_quotes),
        ("scan_top_level: no split inside $() subshell", test_scan_top_level_does_not_split_inside_subshell),
        ("scan_top_level: heredoc body skipped (dev-env#499)", test_scan_top_level_skips_heredoc_body),
        ("scan_top_level: splits on &&", test_scan_top_level_splits_on_and_and),
        ("scan_top_level: splits on ;", test_scan_top_level_splits_on_semicolon),
        ("scan_top_level: splits on ||", test_scan_top_level_splits_on_or_or),
        ("scan_top_level: splits on newline", test_scan_top_level_splits_on_newline),
        ("split_top_level: no separators -> whole command", test_split_top_level_no_separators_returns_whole_command),
        ("split_top_level: splits and preserves order", test_split_top_level_splits_and_preserves_order),
        ("split_top_level: segments are unstripped", test_split_top_level_segments_are_unstripped),
        ("split_top_level: no split inside double quotes (dev-env#511)", test_split_top_level_no_split_inside_double_quotes),
        ("split_top_level: no split inside single quotes", test_split_top_level_no_split_inside_single_quotes),
        ("split_top_level: pipe not split by default", test_split_top_level_pipe_not_split_by_default),
        ("split_top_level: pipe split when enabled", test_split_top_level_pipe_split_when_enabled),
        ("split_top_level: || stays one operator with split_pipe", test_split_top_level_double_pipe_stays_one_operator_even_with_split_pipe),
        ("split_top_level: pipe inside quotes not split even when enabled", test_split_top_level_pipe_inside_quotes_not_split_even_when_enabled),
        ("split_top_level: bare heredoc body not its own segment", test_split_top_level_bare_heredoc_body_not_its_own_segment),
        ("split_top_level: $(cat <<'MARKER'...) not its own segment", test_split_top_level_command_sub_heredoc_not_its_own_segment),
        ("split_top_level: real command after heredoc still split", test_split_top_level_real_command_after_heredoc_still_split),
        ("split_top_level: unterminated quote drops trailing segment", test_split_top_level_unterminated_quote_drops_trailing_segment),
        ("scan_top_level: PowerShell 'A; if ($?) { B }' idiom detected (dev-env#620)", test_scan_top_level_detects_powershell_conditional_brace_idiom),
        ("scan_top_level: bash brace-group '{ cmd; }' idiom detected", test_scan_top_level_detects_bash_brace_group_idiom),
        ("split_top_level: { inside quotes is not a split trigger", test_split_top_level_brace_inside_quotes_not_split),
        ("split_top_level: { splits even with split_pipe=True", test_split_top_level_brace_splits_even_with_split_pipe_enabled),
        ("scan_top_level: here-string (literal) no longer hides real command", test_scan_top_level_herestring_with_embedded_quote_no_longer_hides_real_command),
        ("split_top_level: here-string (literal) segments correctly", test_split_top_level_herestring_literal_embedded_quote_segments_correctly),
        ("scan_top_level: here-string (expandable) no longer hides real command", test_scan_top_level_herestring_expandable_with_embedded_quote_no_longer_hides_real_command),
        ("split_top_level: here-string (expandable) segments correctly", test_split_top_level_herestring_expandable_embedded_quote_segments_correctly),
        ("split_top_level: here-string with no closer runs to end", test_split_top_level_herestring_no_closer_runs_to_end),
        ("split_top_level: same-line '@'/'@\"' not a real opener (dev-env#762 review)", test_split_top_level_herestring_same_line_body_not_recognized_as_opener),
        ("mask_quoted_spans: no quotes -> unchanged", test_mask_quoted_spans_no_quotes_unchanged),
        ("mask_quoted_spans: double-quoted span masked (dev-env#626)", test_mask_quoted_spans_double_quoted_span_masked),
        ("mask_quoted_spans: single-quoted span masked", test_mask_quoted_spans_single_quoted_span_masked),
        ("mask_quoted_spans: escaped quote does not end span early", test_mask_quoted_spans_escaped_quote_does_not_end_span_early),
        ("mask_quoted_spans: $() subshell masked", test_mask_quoted_spans_subshell_masked),
        ("mask_quoted_spans: nested $() inside quotes is one span", test_mask_quoted_spans_nested_subshell_inside_double_quotes_is_one_span),
        ("mask_quoted_spans: bare heredoc body masked", test_mask_quoted_spans_bare_heredoc_body_masked),
        ("mask_quoted_spans: $(cat <<'EOF'...) heredoc masked", test_mask_quoted_spans_command_sub_heredoc_masked),
        ("mask_quoted_spans: here-string (literal) masked (dev-env#620)", test_mask_quoted_spans_herestring_literal_masked),
        ("mask_quoted_spans: here-string (expandable) masked", test_mask_quoted_spans_herestring_expandable_masked),
        ("mask_quoted_spans: newlines preserved", test_mask_quoted_spans_preserves_newlines),
        ("mask_quoted_spans: unterminated double quote masks tail", test_mask_quoted_spans_unterminated_double_quote_masks_tail),
        ("mask_quoted_spans: unterminated subshell masks tail", test_mask_quoted_spans_unterminated_subshell_masks_tail),
        ("mask_quoted_spans: real flag survives alongside quoted decoy", test_mask_quoted_spans_real_flag_survives_alongside_quoted_decoy),
        ("mask_quoted_spans: agrees with split_top_level (cross-consistency)", test_mask_quoted_spans_agrees_with_split_top_level),
        ("mask_prose_flag_values: no prose flags -> unchanged", test_mask_prose_flag_values_no_prose_flags_unchanged),
        ("mask_prose_flag_values: double-quoted --subject decoy masked (dev-env#634)", test_mask_prose_flag_values_double_quoted_subject_masked),
        ("mask_prose_flag_values: single-quoted --body decoy masked", test_mask_prose_flag_values_single_quoted_body_masked),
        ("mask_prose_flag_values: -t/-b short forms masked", test_mask_prose_flag_values_short_flag_forms_masked),
        ("mask_prose_flag_values: --subject=<value> equals form masked", test_mask_prose_flag_values_equals_form_masked),
        ("mask_prose_flag_values: unquoted value also masked", test_mask_prose_flag_values_unquoted_value_also_masked),
        ("mask_prose_flag_values: unquoted URL decoy masked", test_mask_prose_flag_values_unquoted_url_decoy_masked),
        ("mask_prose_flag_values: unquoted value at end of string masked", test_mask_prose_flag_values_unquoted_value_at_end_of_string_masked),
        ("mask_prose_flag_values: bare quoted PR-URL argument NOT masked", test_mask_prose_flag_values_bare_quoted_url_argument_not_masked),
        ("mask_prose_flag_values: real URL survives alongside masked decoy", test_mask_prose_flag_values_real_url_survives_alongside_masked_decoy),
        ("mask_prose_flag_values: $(cat <<'EOF'...) --body masked", test_mask_prose_flag_values_subshell_body_masked),
        ("mask_prose_flag_values: mid-word '-b' not matched", test_mask_prose_flag_values_mid_word_flag_not_matched),
        ("is_merge_help_only: --help long flag -> True", test_is_merge_help_only_bare_help_long_flag),
        ("is_merge_help_only: -h short flag -> True", test_is_merge_help_only_bare_help_short_flag),
        ("is_merge_help_only: real merge with number -> False", test_is_merge_help_only_real_merge_with_number_is_false),
        ("is_merge_help_only: bare merge, no --help -> False", test_is_merge_help_only_bare_merge_no_help_is_false),
        ("is_merge_help_only: no merge invocation at all -> False", test_is_merge_help_only_no_merge_invocation_at_all_is_false),
        ("is_merge_help_only: chained help-then-real-merge -> False", test_is_merge_help_only_chained_help_then_real_merge_is_false),
        ("is_merge_help_only: chained two help invocations -> True", test_is_merge_help_only_chained_two_help_invocations_is_true),
        ("is_merge_help_only: heredoc mention of --help ignored (dev-env#557)", test_is_merge_help_only_heredoc_mention_of_help_text_ignored),
        ("is_merge_help_only: heredoc mention, only segment is real --help", test_is_merge_help_only_heredoc_mention_with_only_help_segment_present),
        ("is_merge_help_only: quoted-argument mention ignored", test_is_merge_help_only_quoted_argument_mention_ignored),
        ("is_merge_help_only: --help not confused with similar flag", test_is_merge_help_only_help_flag_not_confused_with_similar_flag),
        ("is_merge_help_only: cd-prefixed --help -> True", test_is_merge_help_only_cd_prefixed_help_is_true),
        ("is_merge_help_only: case-insensitive match", test_is_merge_help_only_case_insensitive),
        ("is_help_only: generic --help invocation -> True (dev-env#636)", test_is_help_only_generic_help_invocation_is_true),
        ("is_help_only: generic real invocation -> False", test_is_help_only_generic_real_invocation_is_false),
        ("is_help_only: generic no matching segment -> False", test_is_help_only_generic_no_matching_segment_is_false),
        ("is_help_only: generic chained help-then-real -> False", test_is_help_only_generic_chained_help_then_real_is_false),
        ("strip_line_continuations: joins backslash+LF", test_strip_line_continuations_joins_backslash_lf),
        ("strip_line_continuations: bare newline untouched", test_strip_line_continuations_bare_newline_untouched),
        ("strip_line_continuations: backslash+CRLF intact (LF-only)", test_strip_line_continuations_crlf_left_intact),
        ("strip_line_continuations: lone backslash untouched", test_strip_line_continuations_lone_backslash_untouched),
        ("strip_line_continuations: multiple continuations", test_strip_line_continuations_multiple),
        ("strip_line_continuations: realistic gh merge (dev-env#831)", test_strip_line_continuations_realistic_gh_merge),
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
