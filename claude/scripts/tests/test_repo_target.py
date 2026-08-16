#!/usr/bin/env python3
r"""Unit tests for _repo_target.py (dev-env#779 / ADR-111).

`_repo_target.py` is the single quote-aware resolver five hooks
(`post-pr-merge-project.py`, `pr-merge-reminder.py`, `posttooluse-inert-advisory.py`,
`post-pr-merge-pull.py`, `stop-tile-enumeration-gate.py`) converged on for
`--repo`/`-R` flag / PR-URL / issue-URL / positional-number / merge-args
extraction. The five had drifted into three distinct `--repo`/`-R` regex shapes;
this pins the one canonical behavior they all now delegate to. Each consumer's
own suite still exercises the same functions through its delegation, unchanged by
the extraction — this file is the direct, exhaustive coverage of the primitive.

dev-env#838 added a sixth consumer, `post-tool-use.py`, which folded its own
`--repo` extraction in: `repo_from_flag` now also normalizes a full-URL /
`github.com/` host-prefixed value, and `issue_create_args` was added as the
`gh issue create` counterpart of `create_args`.

Mirrors the shared-module test convention of `test_worktree_canon.py` /
`test_journal_schema.py`: a flat list of `(name, fn)` cases, each returning a
one-line description, a PASS/FAIL print, and a final `Tests: N passed, ...` line.

Usage:
    py -3 claude/scripts/tests/test_repo_target.py

Exit 0 = all pass.
"""
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _repo_target as rt  # noqa: E402


# --- repo_from_flag: canonical --repo/-R extraction --------------------------

def test_flag_long_space_form() -> str:
    assert rt.repo_from_flag("gh pr merge 5 --repo brownm09/dev-env --squash") == "brownm09/dev-env"
    return "--repo owner/repo (space form) -> owner/repo"


def test_flag_long_equals_form() -> str:
    # The `=` form was silently MISSED by three of the five pre-consolidation
    # copies (space-only `\s+`); the shared canonical accepts both (dev-env#482).
    assert rt.repo_from_flag("gh pr merge 5 --repo=brownm09/dev-env") == "brownm09/dev-env"
    return "--repo=owner/repo (equals form) -> owner/repo (dev-env#482)"


def test_flag_short_space_form() -> str:
    assert rt.repo_from_flag("gh pr merge 5 -R brownm09/dev-env") == "brownm09/dev-env"
    return "-R owner/repo (gh's --repo shorthand) -> owner/repo (dev-env#616)"


def test_flag_short_equals_form() -> str:
    assert rt.repo_from_flag("gh pr merge 5 -R=brownm09/dev-env") == "brownm09/dev-env"
    return "-R=owner/repo (shorthand, equals) -> owner/repo"


def test_flag_strict_slug_case_preserved() -> str:
    # The strict slug charset never lower-cases; repo identity is caller-decided.
    assert rt.repo_from_flag("gh pr merge --repo BrownM09/Dev-Env 5") == "BrownM09/Dev-Env"
    return "flag value case is preserved (strict [A-Za-z0-9_.-] slug)"


def test_flag_midword_not_matched() -> str:
    # (?<!\S) requires -R/--repo to start a standalone token — a coincidental
    # mid-word "-R" (e.g. a PR title containing "add-R support") must not match.
    assert rt.repo_from_flag("gh pr merge 42 xx-R brownm09/dev-env") is None
    return "mid-word 'xx-R brownm09/dev-env' -> None (standalone-token lookbehind)"


def test_flag_quoted_decoy_masked() -> str:
    # repo_from_flag masks quoted spans internally, so a "-R other/repo" substring
    # inside a --subject value can never be mistaken for a real flag (dev-env#626).
    cmd = 'gh pr merge 42 --subject "see -R other/repo for context"'
    assert rt.repo_from_flag(cmd) is None
    return "quoted --subject decoy '-R other/repo' -> None (masked internally, dev-env#626)"


def test_flag_real_survives_alongside_quoted_decoy() -> str:
    cmd = 'gh pr merge 42 --repo brownm09/dev-env --subject "see -R other/repo for context"'
    assert rt.repo_from_flag(cmd) == "brownm09/dev-env"
    return "real --repo resolves correctly alongside a quoted decoy"


def test_flag_absent_returns_none() -> str:
    assert rt.repo_from_flag("gh pr merge 380 --squash --delete-branch") is None
    return "no --repo/-R flag -> None"


def test_flag_non_slug_value_returns_none() -> str:
    # A flag value with no slash is not a valid owner/repo; the strict slug
    # requires exactly the owner/repo shape, so it does not match.
    assert rt.repo_from_flag("gh pr merge --repo notaslug 5") is None
    return "non-slug --repo value (no slash) -> None (strict slug)"


def test_flag_host_prefixed_form() -> str:
    # gh also accepts a bare `github.com/owner/repo` host-prefixed value
    # (https://cli.github.com/manual/gh#--repo-string); the host prefix is
    # consumed but NOT captured, so the return stays owner/repo (dev-env#838,
    # folding in post-tool-use.py's former private _REPO_HOST_PREFIX_RE). All
    # three prior copies mis-captured "github.com/owner" for this form.
    assert rt.repo_from_flag("gh issue create --repo github.com/brownm09/dev-env") == "brownm09/dev-env"
    return "--repo github.com/owner/repo host-prefixed form -> owner/repo (dev-env#838)"


def test_flag_full_url_form() -> str:
    assert rt.repo_from_flag("gh pr create --repo https://github.com/brownm09/dev-env") == "brownm09/dev-env"
    return "--repo https://github.com/owner/repo full URL -> owner/repo (dev-env#838)"


def test_flag_url_equals_form() -> str:
    assert rt.repo_from_flag("gh pr create --repo=https://github.com/brownm09/dev-env") == "brownm09/dev-env"
    return "--repo=https://github.com/owner/repo (URL, equals form) -> owner/repo (dev-env#838)"


def test_flag_url_www_prefix_stripped() -> str:
    # The optional (?:www\.)? mirrors the former _REPO_HOST_PREFIX_RE exactly.
    assert rt.repo_from_flag("gh pr create -R https://www.github.com/brownm09/dev-env") == "brownm09/dev-env"
    return "--repo https://www.github.com/owner/repo (www prefix) -> owner/repo (dev-env#838)"


# --- merge_args / create_args: quote-aware statement bounding -----------------

def test_merge_args_basic() -> str:
    assert rt.merge_args("gh pr merge 5 --repo o/r --squash") == " 5 --repo o/r --squash"
    return "merge_args returns the merge invocation's own (real, unmasked) args"


def test_merge_args_no_merge_returns_none() -> str:
    assert rt.merge_args("gh pr create --fill") is None
    return "merge_args on a non-merge command -> None"


def test_merge_args_bare_merge_empty_args() -> str:
    # A bare `gh pr merge` with nothing after captures an empty (but present) args
    # string — distinct from None, so a caller can tell "no args" from "no merge".
    assert rt.merge_args("gh pr merge") == ""
    return "bare `gh pr merge` -> '' (empty args, not None)"


def test_create_args_basic() -> str:
    assert rt.create_args("gh pr create --repo o/r --fill") == " --repo o/r --fill"
    return "create_args returns the create invocation's own args"


def test_issue_create_args_basic() -> str:
    assert rt.issue_create_args("gh issue create --repo o/r --title x") == " --repo o/r --title x"
    return "issue_create_args returns the gh issue create invocation's own args (dev-env#838)"


def test_issue_create_args_no_issue_create_returns_none() -> str:
    assert rt.issue_create_args("gh pr create --repo o/r --fill") is None
    return "issue_create_args on a command with no gh issue create -> None (dev-env#838)"


def test_issue_create_args_chained_sibling_not_leaked() -> str:
    # A --repo on a chained sibling command must not leak into the issue-create
    # args region -- statement-bounded, mirroring create_args/merge_args.
    cmd = "gh issue list --repo brownm09/other && gh issue create --title x"
    args = rt.issue_create_args(cmd)
    assert args is not None and rt.repo_from_flag(args) is None
    return "issue_create_args: a chained sibling's --repo does not leak in (dev-env#838)"


def test_issue_create_args_repo_on_continued_line() -> str:
    # The gh issue create counterpart of test_create_args_repo_on_continued_line
    # (dev-env#831): a --repo on a backslash-continued line survives the join.
    cmd = "gh issue create --title t \\\n  --repo brownm09/dev-env"
    args = rt.issue_create_args(cmd)
    assert args is not None and rt.repo_from_flag(args) == "brownm09/dev-env"
    return "issue_create_args keeps a --repo on a continued line (dev-env#838/#831)"


def test_chained_create_merge_flag_scoping() -> str:
    # dev-env#667/#482 Gap 1: whichever --repo appeared textually FIRST in the
    # whole command used to win for BOTH resolvers. Scoping the flag search to
    # each invocation's own args region fixes the cross-contamination.
    cmd = "gh pr create --repo brownm09/repo-a --fill && gh pr merge 5 --repo brownm09/repo-b --squash"
    assert rt.repo_from_flag(rt.merge_args(cmd)) == "brownm09/repo-b"
    assert rt.repo_from_flag(rt.create_args(cmd)) == "brownm09/repo-a"
    return "chained create+merge with different --repo values resolve independently (dev-env#667)"


def test_chained_create_merge_reversed_order() -> str:
    # Reversing statement order must reverse which flag each resolver picks —
    # proving the bounding is statement-scoped, not position-in-string.
    cmd = "gh pr merge 5 --repo brownm09/repo-b --squash && gh pr create --repo brownm09/repo-a --fill"
    assert rt.repo_from_flag(rt.merge_args(cmd)) == "brownm09/repo-b"
    assert rt.repo_from_flag(rt.create_args(cmd)) == "brownm09/repo-a"
    return "chained order reversed -> each resolver still picks its own statement's flag"


def test_merge_args_quoted_separator_no_early_truncation() -> str:
    # dev-env#660 (Amendment 20): a bare `&&`/`&` inside a quoted --subject value
    # must not truncate the args region before a later real --repo is seen. The
    # region is bounded against a mask_quoted_spans copy, so the quoted separator
    # is blinded first. An ordinary subject like "R&D tracking" is enough.
    cmd = 'gh pr merge 42 --subject "part1 && part2" --repo brownm09/dev-env'
    assert rt.repo_from_flag(rt.merge_args(cmd)) == "brownm09/dev-env"
    cmd2 = 'gh pr merge 42 --subject "R&D tracking" --repo brownm09/dev-env'
    assert rt.repo_from_flag(rt.merge_args(cmd2)) == "brownm09/dev-env"
    return "quoted '&&'/'&' in --subject does not truncate args before a real --repo (dev-env#660)"


def test_merge_args_real_chain_still_bounds() -> str:
    # The fix must not simply widen the region unconditionally: a genuine
    # top-level `&&` chaining a real sibling command still bounds it, so a
    # sibling's --repo does NOT leak into the merge args.
    cmd = "gh pr merge 5 --squash && gh pr create --repo brownm09/other --fill"
    assert rt.repo_from_flag(rt.merge_args(cmd)) is None
    return "a genuine top-level && still bounds the args (sibling --repo does not leak)"


# --- PR-URL parsing ----------------------------------------------------------

def test_repo_from_pr_url_basic() -> str:
    assert rt.repo_from_pr_url("gh pr merge https://github.com/brownm09/dev-env/pull/380") == "brownm09/dev-env"
    return "PR URL -> owner/repo"


def test_pr_number_from_pr_url_basic() -> str:
    assert rt.pr_number_from_pr_url("x https://github.com/o/r/pull/99 y") == 99
    return "PR URL -> number (int)"


def test_pr_url_scheme_agnostic() -> str:
    # Scheme-agnostic: a bare `github.com/...` (no https://) still matches — a
    # superset of the copies that required https://, harmless for real output.
    assert rt.repo_from_pr_url("github.com/o/r/pull/5") == "o/r"
    return "PR URL matches with or without an https:// scheme"


def test_pr_url_absent_returns_none() -> str:
    assert rt.repo_from_pr_url("gh pr merge 380 --squash") is None
    assert rt.pr_number_from_pr_url("gh pr merge 380 --squash") is None
    return "no PR URL -> None (repo and number)"


def test_iter_pr_urls_multiple_in_order() -> str:
    text = "https://github.com/a/b/pull/1 and https://github.com/c/d/pull/2"
    assert rt.iter_pr_urls(text) == [("a/b", 1), ("c/d", 2)]
    return "iter_pr_urls returns every (owner/repo, number) pair in order"


def test_iter_pr_urls_empty() -> str:
    assert rt.iter_pr_urls("no urls here") == []
    return "iter_pr_urls with no URLs -> []"


def test_pr_url_not_masked_internally() -> str:
    # The URL functions take text AS-IS — the caller decides masking. A URL
    # inside a quoted --subject value is STILL matched here (proving no internal
    # masking); the four consumers that need decoy-safety mask with
    # mask_prose_flag_values themselves before calling (dev-env#634/#685).
    cmd = 'gh pr merge 380 --subject "see https://github.com/other/repo/pull/99"'
    assert rt.pr_number_from_pr_url(cmd) == 99
    return "repo_from_pr_url/pr_number_from_pr_url do NOT mask internally (caller decides)"


# --- issue-URL parsing -------------------------------------------------------

def test_issue_number_from_issue_url_basic() -> str:
    assert rt.issue_number_from_issue_url("https://github.com/o/r/issues/7") == 7
    return "issue URL -> number (int)"


def test_issue_url_absent_returns_none() -> str:
    assert rt.issue_number_from_issue_url("gh issue close 7") is None
    return "no issue URL -> None"


def test_iter_issue_urls_multiple() -> str:
    text = "https://github.com/a/b/issues/3 https://github.com/c/d/issues/4"
    assert rt.iter_issue_urls(text) == [("a/b", 3), ("c/d", 4)]
    return "iter_issue_urls returns every (owner/repo, number) pair in order"


def test_pull_and_issue_urls_do_not_cross() -> str:
    # A /pull/N URL is not an issue URL and vice-versa.
    assert rt.iter_issue_urls("https://github.com/o/r/pull/5") == []
    assert rt.iter_pr_urls("https://github.com/o/r/issues/5") == []
    return "pull-URL and issue-URL parsers never match each other's shape"


# --- positional_number -------------------------------------------------------

def test_positional_number_basic() -> str:
    assert rt.positional_number("gh pr merge 380 --squash") == 380
    return "bare positional integer -> int"


def test_positional_number_flag_before() -> str:
    assert rt.positional_number("gh pr merge --squash 380") == 380
    return "positional number after flags is still found"


def test_positional_number_url_digit_not_matched() -> str:
    # A digit run inside a /pull/N URL is not a standalone token.
    assert rt.positional_number("gh pr merge https://github.com/o/r/pull/5") is None
    return "digit inside /pull/N URL is not a positional token"


def test_positional_number_flag_value_digit_not_matched() -> str:
    assert rt.positional_number("gh pr merge --foo=12 --squash") is None
    return "digit inside a --flag=12 value is not a positional token"


def test_positional_number_quoted_decoy_masked() -> str:
    # positional_number masks quoted spans internally, so a bare number inside a
    # quoted value ("resolves 42 items") is not a false positional match
    # (dev-env#650). Here the only bare number is the decoy inside the quotes.
    assert rt.positional_number('gh pr merge --subject "resolves 42 items" --squash') is None
    return "bare number inside a quoted value -> None (masked internally, dev-env#650)"


def test_positional_number_real_survives_quoted_decoy() -> str:
    assert rt.positional_number('gh pr merge 380 --subject "resolves 42 items"') == 380
    return "real positional number resolves alongside a quoted decoy number"


def test_positional_number_absent_returns_none() -> str:
    assert rt.positional_number("gh pr merge --squash --delete-branch") is None
    return "no positional number -> None"


def test_positional_number_branch_name_digit_not_matched() -> str:
    # A digit that is part of a hyphenated branch name is not a standalone token.
    assert rt.positional_number("gh pr merge my-branch-2 --squash") is None
    return "digit in a branch name (my-branch-2) is not a positional token"


# ---------------------------------------------------------------------------
# Multi-line shell line-continuations (dev-env#831)
#
# _invocation_args strips backslash+LF continuations (via
# _hookio.strip_line_continuations) before the `[^\n...]` region regex, so a
# --repo / PR-number on a continued line is no longer truncated away. A real
# top-level separator must still bound the region.
# ---------------------------------------------------------------------------

def test_merge_args_repo_on_continued_line() -> str:
    cmd = 'gh pr merge 42 --squash \\\n  --repo brownm09/dev-env \\\n  --subject "x"'
    args = rt.merge_args(cmd)
    assert args is not None and "--repo brownm09/dev-env" in args
    assert rt.repo_from_flag(args) == "brownm09/dev-env"
    return "merge_args keeps a --repo on a continued line (dev-env#831)"


def test_merge_args_pr_number_on_continued_line() -> str:
    # The continuation sits between the verb and its positional argument.
    cmd = 'gh pr merge \\\n  42 --squash'
    args = rt.merge_args(cmd)
    assert args is not None and rt.positional_number(args) == 42
    return "merge_args keeps a PR number on a continued line (dev-env#831)"


def test_create_args_repo_on_continued_line() -> str:
    cmd = 'gh pr create --title t \\\n  --repo brownm09/dev-env'
    args = rt.create_args(cmd)
    assert args is not None and rt.repo_from_flag(args) == "brownm09/dev-env"
    return "create_args keeps a --repo on a continued line (dev-env#831)"


def test_merge_args_real_separator_still_bounds_after_continuation() -> str:
    # --repo sits AFTER the continuation (so the join is load-bearing) and a real
    # top-level && follows: the fix must join the continuation AND still bound the
    # region at the && (never swallow the chained command). Discriminating both
    # ways -- with the fix off --repo is truncated away; if over-widened `rm -rf`
    # leaks into the args region.
    cmd = 'gh pr merge 42 \\\n  --repo brownm09/dev-env --squash && rm -rf /'
    args = rt.merge_args(cmd)
    assert args is not None and "rm -rf" not in args
    assert rt.repo_from_flag(args) == "brownm09/dev-env"
    return "a --repo on a continued line resolves AND a real top-level && still bounds (dev-env#831)"


# --- REST merge fallback path (dev-env#986) --------------------------------

def test_repo_from_rest_merge_path_basic() -> str:
    cmd = "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash"
    assert rt.repo_from_rest_merge_path(cmd) == "brownm09/dev-env"
    return "repos/<owner>/<repo>/pulls/<N>/merge -> owner/repo (dev-env#986)"


def test_pr_number_from_rest_merge_path_basic() -> str:
    cmd = "gh api -X PUT repos/brownm09/dev-env/pulls/42/merge -f merge_method=squash"
    assert rt.pr_number_from_rest_merge_path(cmd) == 42
    return "repos/<owner>/<repo>/pulls/<N>/merge -> N (dev-env#986)"


def test_rest_merge_path_absent_returns_none() -> str:
    assert rt.repo_from_rest_merge_path("gh pr merge 42 --squash") is None
    assert rt.pr_number_from_rest_merge_path("gh pr merge 42 --squash") is None
    return "no REST merge path -> None for both"


def test_rest_merge_path_not_confused_with_pull_web_url() -> str:
    # "pull" (web URL) vs "pulls" (REST path) -- must not cross-match.
    cmd = "gh pr merge https://github.com/brownm09/dev-env/pull/42 --squash"
    assert rt.repo_from_rest_merge_path(cmd) is None
    assert rt.pr_number_from_rest_merge_path(cmd) is None
    return "a /pull/N web URL does not match the /pulls/N/merge REST path regex"


def main() -> int:
    tests = [
        # repo_from_flag
        ("flag: --repo space form", test_flag_long_space_form),
        ("flag: --repo=... equals form (dev-env#482)", test_flag_long_equals_form),
        ("flag: -R space form (dev-env#616)", test_flag_short_space_form),
        ("flag: -R=... equals form", test_flag_short_equals_form),
        ("flag: case preserved", test_flag_strict_slug_case_preserved),
        ("flag: mid-word -R not matched", test_flag_midword_not_matched),
        ("flag: quoted decoy masked (dev-env#626)", test_flag_quoted_decoy_masked),
        ("flag: real survives alongside quoted decoy", test_flag_real_survives_alongside_quoted_decoy),
        ("flag: absent -> None", test_flag_absent_returns_none),
        ("flag: non-slug value -> None", test_flag_non_slug_value_returns_none),
        ("flag: host-prefixed github.com/owner/repo -> owner/repo (dev-env#838)", test_flag_host_prefixed_form),
        ("flag: full https URL -> owner/repo (dev-env#838)", test_flag_full_url_form),
        ("flag: URL equals form -> owner/repo (dev-env#838)", test_flag_url_equals_form),
        ("flag: www. URL prefix stripped (dev-env#838)", test_flag_url_www_prefix_stripped),
        # merge_args / create_args
        ("merge_args: basic", test_merge_args_basic),
        ("merge_args: no merge -> None", test_merge_args_no_merge_returns_none),
        ("merge_args: bare merge -> '' ", test_merge_args_bare_merge_empty_args),
        ("create_args: basic", test_create_args_basic),
        ("issue_create_args: basic (dev-env#838)", test_issue_create_args_basic),
        ("issue_create_args: no issue-create -> None (dev-env#838)", test_issue_create_args_no_issue_create_returns_none),
        ("issue_create_args: chained sibling not leaked (dev-env#838)", test_issue_create_args_chained_sibling_not_leaked),
        ("issue_create_args: --repo on a continued line (dev-env#838/#831)", test_issue_create_args_repo_on_continued_line),
        ("chained create+merge flag scoping (dev-env#667)", test_chained_create_merge_flag_scoping),
        ("chained order reversed", test_chained_create_merge_reversed_order),
        ("merge_args: quoted separator no early truncation (dev-env#660)", test_merge_args_quoted_separator_no_early_truncation),
        ("merge_args: real chain still bounds", test_merge_args_real_chain_still_bounds),
        # PR URL
        ("repo_from_pr_url: basic", test_repo_from_pr_url_basic),
        ("pr_number_from_pr_url: basic", test_pr_number_from_pr_url_basic),
        ("PR URL scheme-agnostic", test_pr_url_scheme_agnostic),
        ("PR URL absent -> None", test_pr_url_absent_returns_none),
        ("iter_pr_urls: multiple in order", test_iter_pr_urls_multiple_in_order),
        ("iter_pr_urls: empty", test_iter_pr_urls_empty),
        ("PR URL functions do not mask internally", test_pr_url_not_masked_internally),
        # issue URL
        ("issue_number_from_issue_url: basic", test_issue_number_from_issue_url_basic),
        ("issue URL absent -> None", test_issue_url_absent_returns_none),
        ("iter_issue_urls: multiple", test_iter_issue_urls_multiple),
        ("pull/issue URL parsers do not cross", test_pull_and_issue_urls_do_not_cross),
        # positional_number
        ("positional_number: basic", test_positional_number_basic),
        ("positional_number: flag before", test_positional_number_flag_before),
        ("positional_number: URL digit not matched", test_positional_number_url_digit_not_matched),
        ("positional_number: flag-value digit not matched", test_positional_number_flag_value_digit_not_matched),
        ("positional_number: quoted decoy masked (dev-env#650)", test_positional_number_quoted_decoy_masked),
        ("positional_number: real survives quoted decoy", test_positional_number_real_survives_quoted_decoy),
        ("positional_number: absent -> None", test_positional_number_absent_returns_none),
        ("positional_number: branch-name digit not matched", test_positional_number_branch_name_digit_not_matched),
        ("merge_args: --repo on a continued line (dev-env#831)", test_merge_args_repo_on_continued_line),
        ("merge_args: PR number on a continued line (dev-env#831)", test_merge_args_pr_number_on_continued_line),
        ("create_args: --repo on a continued line (dev-env#831)", test_create_args_repo_on_continued_line),
        ("merge_args: real && still bounds after a continuation (dev-env#831)", test_merge_args_real_separator_still_bounds_after_continuation),
        # REST merge fallback path (dev-env#986)
        ("repo_from_rest_merge_path: basic", test_repo_from_rest_merge_path_basic),
        ("pr_number_from_rest_merge_path: basic", test_pr_number_from_rest_merge_path_basic),
        ("REST merge path: absent -> None", test_rest_merge_path_absent_returns_none),
        ("REST merge path: not confused with /pull/N web URL", test_rest_merge_path_not_confused_with_pull_web_url),
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
