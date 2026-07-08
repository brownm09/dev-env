#!/usr/bin/env python3
"""Unit tests for post-pr-merge-pull.py's merge-detection predicate.

`post-pr-merge-pull.py` fast-forwards the local `main` after a successful
`gh pr merge`. The "was this a successful merge?" decision is the pure
`is_successful_merge()` predicate (extracted in #380, mirroring
post-pr-merge-reclaim.py), exercised offline here. Before #380 the hook read the
legacy `output` field (always empty on the real payload), so the stdout/stderr
success-marker fallback was dead and only a clean exit-0 merge triggered the
pull; the predicate now receives output via the shared `read_command_output`.

dev-env#485 removed the `exit_code` parameter entirely: `exit_code == 0 OR
marker` fired on any exit-0 command matching "gh pr merge" as a substring,
including `gh pr merge --help`. The predicate now gates solely on the success
marker, matching post-pr-merge-project.py's `merge_succeeded()`.

dev-env#529 (ADR-050 Amendment 9) converged the command-shape check itself
from a raw `"gh pr merge" not in command` substring test onto the
`scan_top_level`-anchored predicate already used by usage-snapshot.py /
pr-merge-reminder.py / post-pr-merge-project.py. The three
heredoc/quote/subshell tests below pin the false-positive shapes that
substring test was blind to (dev-env#499's original repro class) but the
anchored predicate correctly rejects.

`pull_command()` (dev-env#488) is the pure decision of which git invocation
fast-forwards local main: `git fetch origin main:main` fails ('refusing to fetch
into branch ... checked out') whenever main is the branch currently checked out
at the target path — always true for dev-env's own canonical, which must stay on
`main` per its symlink architecture — so a plain `pull --ff-only` is used there
instead; the feature-branch-checked-out case (issue #275) is unchanged.

dev-env#557: `main()` adds a second guard — `if is_merge_help_only(command):
sys.exit(0)`, right after the existing `if not scan_top_level(command,
_check_merge_stmt): sys.exit(0)` line, before computing `exit_code` — so a
`gh pr merge --help` command never reaches the live `gh pr view` fallback
that would otherwise misattribute an unrelated already-merged PR to the
harmless `--help` invocation, and never falls through to the live
`extract_repo`/`list_worktrees`/`pull_main` git calls further down `main()`.
`is_merge_help_only` itself is exhaustively tested in `test_hookio.py`; the
composition test below pins that `is_successful_merge` (the predicate the
guard sits behind) returns False for exactly the `--help` shape
`is_merge_help_only` returns True for.

The `pull_main` / `extract_repo` git calls are intentionally not tested (they
shell out and the repo avoids subprocess mocks).

Usage:
    py -3 claude/scripts/tests/test_post_pr_merge_pull.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-pr-merge-pull.py"

# The script imports _winsubp and _hookio (siblings in scripts/); make resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_pr_merge_pull", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
ppmp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ppmp)  # safe: main() is guarded by __main__
is_successful_merge = ppmp.is_successful_merge
extract_repo = ppmp.extract_repo
pull_command = ppmp.pull_command

# is_merge_help_only lives in _hookio (a sibling); SCRIPT.parent already on
# sys.path via the insert above.
from _hookio import is_merge_help_only  # noqa: E402


def test_clean_merge_with_marker_pulls() -> str:
    # The success marker is what confirms a completed merge; the exit code is
    # no longer consulted at all (dev-env#485) — true whether it came from a
    # clean canonical-checkout exit or a worktree's non-zero cleanup failure
    # (issue #275).
    assert is_successful_merge(
        "gh pr merge 380 --squash --delete-branch",
        "Squashed and merged pull request #380",
    )
    return "'Squashed and merged' marker -> pull"


def test_non_merge_command_ignored() -> str:
    assert not is_successful_merge("gh pr create --fill", "")
    assert not is_successful_merge("git push", "")
    return "non-merge commands -> no-op"


def test_failed_merge_no_marker_ignored() -> str:
    # A genuine merge failure (no success marker) must not pull.
    assert not is_successful_merge(
        "gh pr merge 380 --squash", "X Pull request #380 is not mergeable",
    )
    return "gh pr merge failed (no marker) -> no-op"


def test_help_invocation_no_marker_ignored() -> str:
    # dev-env#485 regression: `gh pr merge --help` exits 0 but prints no
    # success marker. The old exit_code==0 OR marker gate fired here; gating
    # on the marker alone fixes it.
    assert not is_successful_merge(
        "gh pr merge --help",
        "FLAGS\n      --admin   Use administrator privileges to merge a pull request",
    )
    return "gh pr merge --help (exit 0, no marker) -> no-op (dev-env#485)"


# ---------------------------------------------------------------------------
# command-shape anchoring (dev-env#529, ADR-050 Amendment 9)
#
# Each command below contains the literal substring "gh pr merge" but not as
# a genuine top-level invocation. Paired with an output that DOES carry a
# real success marker, isolating the command-shape check: the old crude
# `"gh pr merge" not in command` substring test would have proceeded past
# this check straight to the (passing) marker check and fired -- a false
# positive. The scan_top_level-anchored check returns False before the
# marker is ever consulted.
# ---------------------------------------------------------------------------

def test_merge_text_in_heredoc_body_not_matched() -> str:
    command = "git commit -F - <<'EOF'\ngh pr merge --squash --delete-branch\nEOF"
    assert not is_successful_merge(command, "Squashed and merged pull request #380")
    return "'gh pr merge' text inside a heredoc body -> no match (dev-env#529)"


def test_merge_text_inside_double_quotes_not_matched() -> str:
    # The && inside the quoted commit message would, without quote-tracking,
    # wrongly carve out a second top-level segment starting with "gh pr
    # merge" -- the dev-env#499 false-positive class scan_top_level exists
    # to prevent.
    command = 'git commit -m "gh pr create --fill && gh pr merge --auto"'
    assert not is_successful_merge(command, "Squashed and merged pull request #380")
    return "'gh pr merge' text inside a double-quoted commit message -> no match (dev-env#529)"


def test_merge_text_inside_subshell_not_matched() -> str:
    command = "echo $(gh pr create --fill && gh pr merge --auto)"
    assert not is_successful_merge(command, "Squashed and merged pull request #380")
    return "'gh pr merge' text inside a $() subshell -> no match (dev-env#529)"


# ---------------------------------------------------------------------------
# extract_repo — pure-string resolution paths (dev-env#446 / ADR-067)
#
# The git-remote subprocess fallback is not tested (repo convention: no mocks).
# The --repo flag path is already exercised implicitly by is_successful_merge
# tests; these cover the new URL-extraction path added in ADR-067.
# ---------------------------------------------------------------------------

def test_extract_repo_from_url_in_command() -> str:
    repo = extract_repo(
        "gh pr merge https://github.com/brownm09/dev-env/pull/443 --squash --delete-branch",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"got {repo!r}"
    return "GitHub PR URL in command -> correct owner/repo regardless of cwd"


def test_extract_repo_from_url_other_repo() -> str:
    repo = extract_repo(
        "gh pr merge https://github.com/brownm09/lifting-logbook/pull/99 --squash",
        "/Git/dev-env",
    )
    assert repo == "brownm09/lifting-logbook", f"got {repo!r}"
    return "GitHub PR URL for a different repo -> that repo's slug"


def test_extract_repo_repo_flag_takes_precedence() -> str:
    # --repo flag wins over any URL in the command string (first check in order).
    repo = extract_repo(
        "gh pr merge --repo brownm09/dev-env https://github.com/brownm09/lifting-logbook/pull/1",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"--repo flag should win: got {repo!r}"
    return "--repo flag takes precedence over GitHub URL in command"


def test_extract_repo_short_flag_form() -> str:
    # dev-env#616: gh's -R shorthand for --repo was not recognized, so a
    # `-R owner/repo` merge command fell through to the URL/cd-chain/cwd
    # fallbacks instead of the flag's own explicit repo.
    repo = extract_repo(
        "gh pr merge 611 -R brownm09/dev-env --squash",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"got {repo!r}"
    return "-R flag (gh's --repo shorthand) resolves identically to --repo (dev-env#616)"


def test_extract_repo_dash_r_mid_word_not_matched() -> str:
    # dev-env#626 / review finding on PR #623: mid-word "-R" (not a standalone
    # token) must not be mistaken for the flag -- proven here by combining it
    # with a real PR URL later in the command; the pre-fix unanchored regex
    # would have wrongly matched the mid-word text first and returned the
    # wrong repo (this file's extract_repo() checks the whole raw command,
    # not just the merge invocation's own arg span, so it is the most exposed
    # of the four fixed sites to this class of false match).
    repo = extract_repo(
        "gh pr merge 42 xx-R brownm09/other-repo https://github.com/brownm09/dev-env/pull/42 --squash",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"got {repo!r}"
    return "mid-word '-R' skipped, falls through to the real PR URL (dev-env#626)"


def test_extract_repo_dash_r_inside_quoted_subject_not_matched() -> str:
    # dev-env#626, ADR-050 Amendment 15: a --subject value containing a
    # legitimately space-separated "-R other/repo" substring must not be
    # mistaken for the flag either -- mirrors the mid-word case above but for
    # the quoted-value shape the (?<!\S) lookbehind alone cannot catch. Falls
    # through to the real PR URL later in the command.
    repo = extract_repo(
        'gh pr merge 42 --subject "see -R other/repo for context" '
        "https://github.com/brownm09/dev-env/pull/42 --squash",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"got {repo!r}"
    return "quoted --subject decoy '-R other/repo' skipped, falls through to the real PR URL (dev-env#626)"


def test_extract_repo_flag_survives_alongside_quoted_decoy() -> str:
    # A real, unquoted --repo flag must still resolve correctly even when a
    # quoted --subject value elsewhere in the same command contains a decoy.
    repo = extract_repo(
        'gh pr merge 42 --repo brownm09/dev-env --subject "see -R other/repo for context"',
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"got {repo!r}"
    return "real --repo flag resolves correctly alongside a quoted decoy (dev-env#626)"


def test_pull_command_on_main_uses_ff_only_pull() -> str:
    cmd = pull_command("C:/Users/brown/Git/dev-env", True)
    assert cmd == ["git", "-C", "C:/Users/brown/Git/dev-env", "pull", "--ff-only", "origin", "main"], cmd
    return "canonical on main -> plain ff-only pull ('git fetch origin main:main' would fail: refusing to fetch into branch ... checked out)"


def test_pull_command_off_main_uses_fetch_into_ref() -> str:
    cmd = pull_command("C:/Users/brown/Git/lifting-logbook", False)
    assert cmd == ["git", "-C", "C:/Users/brown/Git/lifting-logbook", "fetch", "origin", "main:main"], cmd
    return "canonical on a feature branch (or worktree squatting main) -> fetch-into-ref, unchanged (issue #275)"


# ---------------------------------------------------------------------------
# is_merge_help_only composition (dev-env#557)
# ---------------------------------------------------------------------------

def test_help_command_not_successful_merge_and_is_help_only() -> str:
    command = "gh pr merge --help"
    output = "FLAGS\n      --admin   Use administrator privileges to merge a pull request"
    assert not is_successful_merge(command, output), "no success marker -> not is_successful_merge"
    assert is_merge_help_only(command), "gh pr merge --help -> is_merge_help_only True"
    return "gh pr merge --help: is_successful_merge False, is_merge_help_only True -> guard fires (dev-env#557)"


def test_unresolved_real_merge_is_not_help_only() -> str:
    # A genuine merge with no marker (e.g. dev-env#489's lost-marker shape) and
    # a non-zero exit must NOT be classified as help-only -- the live gh-pr-view
    # fallback must still be attempted for this shape, unchanged.
    command = "gh pr merge --squash --delete-branch"
    output = "failed to run git: fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'"
    assert not is_successful_merge(command, output)
    assert not is_merge_help_only(command), "bare merge, no --help -> guard must not suppress it"
    return "unresolved real merge (no marker, non-help) -> is_merge_help_only False (fallback unaffected)"


def main() -> int:
    tests = [
        ("merge marker present -> pulls", test_clean_merge_with_marker_pulls),
        ("non-merge command ignored", test_non_merge_command_ignored),
        ("failed merge with no marker ignored", test_failed_merge_no_marker_ignored),
        ("gh pr merge --help (no marker) ignored (dev-env#485)", test_help_invocation_no_marker_ignored),
        ("'gh pr merge' text in heredoc body ignored (dev-env#529)", test_merge_text_in_heredoc_body_not_matched),
        ("'gh pr merge' text in double quotes ignored (dev-env#529)", test_merge_text_inside_double_quotes_not_matched),
        ("'gh pr merge' text in $() subshell ignored (dev-env#529)", test_merge_text_inside_subshell_not_matched),
        ("extract_repo: GitHub URL in command -> owner/repo", test_extract_repo_from_url_in_command),
        ("extract_repo: URL for different repo", test_extract_repo_from_url_other_repo),
        ("extract_repo: --repo flag beats URL", test_extract_repo_repo_flag_takes_precedence),
        ("extract_repo: -R shorthand resolves same as --repo (dev-env#616)", test_extract_repo_short_flag_form),
        ("extract_repo: mid-word '-R' not matched (dev-env#626)", test_extract_repo_dash_r_mid_word_not_matched),
        ("extract_repo: '-R' inside quoted --subject not matched (dev-env#626)", test_extract_repo_dash_r_inside_quoted_subject_not_matched),
        ("extract_repo: --repo flag survives alongside quoted decoy (dev-env#626)", test_extract_repo_flag_survives_alongside_quoted_decoy),
        ("pull_command: canonical on main -> ff-only pull", test_pull_command_on_main_uses_ff_only_pull),
        ("pull_command: canonical off main -> fetch-into-ref", test_pull_command_off_main_uses_fetch_into_ref),
        ("gh pr merge --help: guard fires (dev-env#557)", test_help_command_not_successful_merge_and_is_help_only),
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
