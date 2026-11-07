#!/usr/bin/env python3
"""Tests for _closing_negation.py (dev-env#1300).

The two known-bad fixtures are the lines that closed an issue by accident in switchyard PR 21 and
lifting-logbook PR 510. Both repos are private, so only the lines #1300 already quotes are kept here.
The sweep's own tests cover says_it_does_not_close (test_deferred_followup_sweep.py, Negation).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from _closing_negation import accidental_closes  # noqa: E402

REPO = "me/mine"
SWITCHYARD_PR_21 = (
    "Refs #12. Records two decisions the owner made on 2026-11-05. "
    "It doesn't close #12, whose calibration criteria still need a live run.\n\n"
    "| 1 | The owner-action lists on #10 and #12 still say to check the plan's terms | fixed |\n"
)
LIFTING_LOGBOOK_PR_510 = (
    "## Why this advances but does not close #468\n\n"
    "The token-bearing parameters in #468 can only be settled from **production results**.\n"
)


def test_switchyard_pr_21_names_the_issue():
    assert accidental_closes(SWITCHYARD_PR_21, REPO) == ["#12"]


def test_lifting_logbook_pr_510_names_the_issue():
    assert accidental_closes(LIFTING_LOGBOOK_PR_510, REPO) == ["#468"]


def test_markdown_emphasis_and_colon_match():
    assert accidental_closes("It does **not** close #5: it stays open.", REPO) == ["#5"]
    assert accidental_closes("Noted, not fixed: #6", REPO) == ["#6"]
    assert accidental_closes("**Does not close #7**", REPO) == ["#7"]


def test_curly_apostrophe_and_other_keywords_match():
    assert accidental_closes("It doesn’t fix #7, and never resolved #8.", REPO) == ["#7", "#8"]
    assert accidental_closes("This hasn't yet closed #9.", REPO) == ["#9"]


def test_another_repo_keeps_its_name_and_a_url_to_this_repo_is_a_bare_number():
    assert accidental_closes("It doesn't close o/r#8", REPO) == ["o/r#8"]
    assert accidental_closes("It doesn't close https://github.com/me/mine/issues/8", REPO) == ["#8"]


def test_ordinary_wording_does_not_match():
    for body in (
        "Closes #12",
        "doesn't block closing #388",
        "It's not blocked.\n\nCloses #5",
        "PR #85 (which fixed #83)",
        "Does not fully close it (fixes #1)",
        "Does not close it\n\nFixes #4",
        "",
        None,
    ):
        assert accidental_closes(body, REPO) == [], body


def test_code_does_not_match_because_github_does_not_link_it():
    for body in (
        "This proposal does not use `Closes #477`",
        "Not a `Closes #126` PR",
        "**Not** `Closes #12` by design",
        "Example:\n```\nIt doesn't close #12\n```\n",
    ):
        assert accidental_closes(body, REPO) == [], body


def test_emphasis_closing_after_the_colon_does_not_match_because_github_does_not_link_it():
    assert accidental_closes("**Noted, not fixed:** #12", REPO) == []
    assert accidental_closes("**Noted, not fixed:** [#12](https://github.com/me/mine/issues/12)", REPO) == []


def test_a_real_close_of_the_same_issue_is_not_an_accident():
    assert accidental_closes("The earlier PR did not fix #12. This one does.\n\nCloses #12", REPO) == []
    assert accidental_closes("It doesn't close #12.\n\nCloses https://github.com/me/mine/issues/12", REPO) == []
    assert accidental_closes("Closes #12. It does not close other/repo#12", REPO) == ["other/repo#12"]


def test_an_unknown_repo_still_reports_a_bare_reference():
    assert accidental_closes("It doesn't close #12", "") == ["#12"]


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS: {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
