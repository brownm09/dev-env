#!/usr/bin/env python3
"""Tests for _composed_output_scan.py — stray-terminal-output gate (dev-env #894, ADR-121).

Exercises ``scan_text`` / ``find_signature_hits`` / ``strip_code_spans`` offline (no disk,
no network, no subprocess). ``validate-composed-output.py``'s ``main()`` is the only impure
surface and is not unit-tested, matching the ``validate-manifest.py`` convention.

Cases pinned:

- **The motivating corruption** (engineering-journal#183) verbatim as a fixture: both checks
  fire on it, and specifically the non-indented ``Please specify which branch`` /
  ``See git-rebase(1)`` lines are caught by ``signature`` while the indented
  ``git rebase '<branch>'`` / ``git branch --set-upstream-to=...`` lines are caught inside
  ``## Progress Summary``. The two checks are complementary on the real input — that is the
  whole design — so a regression in either still leaves the incident detected, and the test
  asserts both independently rather than just "some finding exists".
- **The three real false-positive sites** that made fence-awareness a hard requirement:
  journal entries legitimately quote ``fatal:`` inside code fences
  (``career-playbook/2026-06-21-…``, ``dev-env/2026-07-02-…``, ``dev-env/2026-07-13-…``).
  Flagging those would make the gate noise and it would be ignored.
- **The meta case**: a journal entry *about* this bug names the signatures in inline code
  spans. Composing that journal must not trip the gate it describes — including the
  double-backtick span containing single backticks, which a naive ``` `[^`]*` ``` regex
  splits and leaks.
- Fence mechanics: info strings, tilde fences, a ``~~~`` inside a ``` block not closing it,
  a shorter closer not closing a longer opener, and the delimiter lines themselves being
  treated as fenced (so a signature cannot hide in an info string).
- Anchored vs. unanchored signatures: ``fatal: ``/``hint: `` match only at line start, since
  mid-sentence prose mentions of them are common.
- ``## Progress Summary`` section bounds: the indent check starts at that heading, stops at
  the next heading of any level, does not fire elsewhere in the file, and ignores blank
  indented lines.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _composed_output_scan as mod  # noqa: E402

scan_text = mod.scan_text
find_signature_hits = mod.find_signature_hits
strip_code_spans = mod.strip_code_spans


def _kinds(findings):
    return sorted({f["kind"] for f in findings})


def _lines_of_kind(findings, kind):
    return sorted(f["line"] for f in findings if f["kind"] == kind)


# ---------------------------------------------------------------------------
# The motivating corruption (engineering-journal#183), verbatim
# ---------------------------------------------------------------------------

# Reproduced from sessions/dev-env/README.md as committed by the 2026-07-11 compose
# (de23d2f0). Line 1 is the heading, line 3 the corrupted paragraph's first line.
CORRUPTION = """## Progress Summary

The 2026-07-11 session continued the hook hardening sprint. Key deliverables: (1) PR [#726](https://github.com/brownm09/dev-env/pull/726) - output-contract AST gates; and (6) PR [#725](https://github.com/brownm09/dev-env/pull/725) opened documenting that bare `git rebase --force` auto-closes open PRs.
Please specify which branch you want to rebase against.
See git-rebase(1) for details.

    git rebase '<branch>'

If you wish to set tracking information for this branch you can do so with:

    git branch --set-upstream-to=<remote>/<branch> claude/festive-carson-43d65a/ pattern that auto-closes open PRs. ADR count now at 101+.

The 2026-07-08 day had 13 sessions across two compose passes. ADR count now at 92.
"""


def test_motivating_corruption_is_detected():
    findings = scan_text(CORRUPTION)
    assert findings, "the real 2026-07-11 corruption must be detected"


def test_motivating_corruption_fires_both_checks():
    # The design is two complementary checks; a regression in either must still leave the
    # incident caught, so assert each independently rather than a bare "something fired".
    assert _kinds(scan_text(CORRUPTION)) == ["progress-summary-indent", "signature"]


def test_motivating_corruption_signature_lines():
    # Lines 4 and 5 are the non-indented pasted prose; line 11 is the indented
    # --set-upstream-to line, which the signature check also sees (indent is not exempt).
    assert _lines_of_kind(scan_text(CORRUPTION), "signature") == [4, 5, 11]


def test_motivating_corruption_indent_lines():
    # Lines 7 and 11 are the two 4-space-indented pasted command lines.
    assert _lines_of_kind(scan_text(CORRUPTION), "progress-summary-indent") == [7, 11]


def test_motivating_corruption_surviving_prose_line_is_reported_not_stripped():
    # The welded-on real sentence lives on line 11. The finding must carry the full line so
    # a reader can see the content that would be lost by blind deletion.
    findings = [f for f in scan_text(CORRUPTION) if f["line"] == 11]
    assert findings
    assert "ADR count now at 101+." in findings[0]["text"]


def test_clean_progress_summary_paragraph_passes():
    clean = (
        "## Progress Summary\n\n"
        "The 2026-07-11 session continued the hook hardening sprint. PR [#725](x) opened\n"
        "documenting the bare `git push --force`-after-rebase pattern that auto-closes open\n"
        "PRs. ADR count now at 101+.\n"
    )
    assert scan_text(clean) == []


# ---------------------------------------------------------------------------
# False positives that made fence-awareness a hard requirement
# ---------------------------------------------------------------------------

def test_fenced_fatal_quote_is_exempt():
    # sessions/dev-env/2026-07-02-…:392 and career-playbook/2026-06-21-…:75 shape.
    text = (
        "Attempting the merge from the worktree failed:\n\n"
        "```\n"
        "fatal: 'main' is already checked out at 'C:/Users/brown/Git/dev-env'\n"
        "```\n\n"
        "Resolved by merging from the canonical instead.\n"
    )
    assert scan_text(text) == []


def test_fenced_worktree_exists_quote_is_exempt():
    # sessions/dev-env/2026-07-13-…:190 shape.
    text = "```bash\nfatal: '.claude/worktrees/<name>' already exists\n```\n"
    assert scan_text(text) == []


def test_fenced_full_rebase_usage_block_is_exempt():
    # A journal entry documenting this very incident quotes the whole usage message.
    text = (
        "The pasted block was:\n\n"
        "```\n"
        "There is no tracking information for the current branch.\n"
        "Please specify which branch you want to rebase against.\n"
        "See git-rebase(1) for details.\n"
        "\n"
        "    git branch --set-upstream-to=<remote>/<branch> claude/x\n"
        "```\n"
    )
    assert scan_text(text) == []


def test_fence_info_string_cannot_hide_a_signature():
    # The delimiter lines are themselves treated as fenced, so an info string is not a
    # smuggling route -- but neither is it a false positive.
    assert scan_text("```fatal: nope\nbody\n```\n") == []


# ---------------------------------------------------------------------------
# The meta case: a journal about this bug names the signatures in prose
# ---------------------------------------------------------------------------

def test_inline_code_span_is_exempt():
    text = "The paste included `See git-rebase(1) for details.` verbatim.\n"
    assert scan_text(text) == []


def test_double_backtick_span_containing_single_backticks_is_exempt():
    # A naive `[^`]*` regex splits this and leaks the inner text, producing a false hit on
    # a real sentence from the #183 stub.
    text = "The clause read ``bare `git rebase --force`/`--onto` auto-closes`` -- wrong.\n"
    assert scan_text(text) == []


def test_inline_span_with_set_upstream_to_is_exempt():
    text = "It was welded onto the tail of a `git branch --set-upstream-to` line.\n"
    assert scan_text(text) == []


def test_multiple_spans_on_one_line_are_all_stripped():
    text = "Both `hint: ` and `fatal: ` appear in the signature list.\n"
    assert scan_text(text) == []


def test_prose_outside_the_span_is_still_scanned():
    # Stripping spans must not blind the rest of the line.
    text = "Quoting `something harmless` then: There is no tracking information here.\n"
    assert [f["kind"] for f in scan_text(text)] == ["signature"]


def test_unbalanced_backtick_is_conservative():
    # No closing tick -> no span recognised -> the line is still scanned. For an advisory
    # gate, over-reporting is the correct direction.
    assert scan_text("A stray ` tick then Please specify which branch to use.\n") != []


# ---------------------------------------------------------------------------
# Fence mechanics
# ---------------------------------------------------------------------------

def test_tilde_fence_is_honoured():
    assert scan_text("~~~\nfatal: boom\n~~~\n") == []


def test_tilde_inside_backtick_fence_does_not_close_it():
    text = "```\n~~~\nfatal: still inside the backtick fence\n```\n"
    assert scan_text(text) == []


def test_shorter_closer_does_not_close_longer_opener():
    text = "````\n```\nfatal: still fenced\n````\n"
    assert scan_text(text) == []


def test_unclosed_fence_swallows_the_rest_of_the_file():
    # CommonMark: an unclosed fence runs to end of document. Matching that keeps the gate
    # aligned with how the file actually renders.
    assert scan_text("```\nfatal: boom\nmore text\n") == []


def test_signature_after_a_closed_fence_is_caught():
    text = "```\nfatal: quoted\n```\nfatal: this one leaked into prose\n"
    assert _lines_of_kind(scan_text(text), "signature") == [4]


# ---------------------------------------------------------------------------
# Anchoring
# ---------------------------------------------------------------------------

def test_anchored_signature_matches_at_line_start():
    assert find_signature_hits("fatal: could not read from remote") == ["fatal: "]


def test_anchored_signature_matches_after_leading_whitespace():
    assert find_signature_hits("    fatal: could not read") == ["fatal: "]


def test_anchored_signature_does_not_match_mid_sentence():
    # "the fatal: line" is ordinary prose; an unanchored match here would be noise.
    assert find_signature_hits("Git printed the fatal: line and exited.") == []


def test_unanchored_signature_matches_mid_sentence():
    assert find_signature_hits("It said Everything up-to-date and stopped.") == [
        "Everything up-to-date"
    ]


def test_multiple_signatures_on_one_line_all_reported():
    hits = find_signature_hits("Please specify which branch -- See git-rebase(1) for details.")
    assert hits == ["See git-rebase(1)", "Please specify which branch"]


def test_clean_line_has_no_hits():
    assert find_signature_hits("An ordinary sentence about rebasing and force pushes.") == []


# ---------------------------------------------------------------------------
# Progress Summary section bounds
# ---------------------------------------------------------------------------

def test_indent_check_fires_inside_progress_summary():
    text = "## Progress Summary\n\n    indented prose line\n"
    assert _kinds(scan_text(text)) == ["progress-summary-indent"]


def test_indent_check_does_not_fire_before_the_heading():
    text = "# Journal\n\n    indented line\n\n## Progress Summary\n\nclean prose\n"
    assert scan_text(text) == []


def test_indent_check_stops_at_the_next_heading():
    text = "## Progress Summary\n\nclean prose\n\n## Entries\n\n    indented table stuff\n"
    assert scan_text(text) == []


def test_indent_check_stops_at_a_deeper_heading_too():
    # Any ATX heading closes the section -- a Progress Summary never contains subsections
    # in this corpus, so the conservative bound is the right one.
    text = "## Progress Summary\n\nclean\n\n### Notes\n\n    indented\n"
    assert scan_text(text) == []


def test_indent_check_ignores_blank_indented_lines():
    text = "## Progress Summary\n\nclean prose\n    \n\nmore prose\n"
    assert scan_text(text) == []


def test_indent_check_ignores_fenced_indented_content():
    text = "## Progress Summary\n\n```\n    indented inside a fence\n```\n"
    assert scan_text(text) == []


def test_tab_indent_is_caught():
    assert _kinds(scan_text("## Progress Summary\n\n\ttabbed line\n")) == [
        "progress-summary-indent"
    ]


def test_a_second_progress_summary_section_reopens_the_check():
    text = (
        "## Progress Summary\n\nclean\n\n## Entries\n\n| a | b |\n\n"
        "## Progress Summary\n\n    indented\n"
    )
    assert _lines_of_kind(scan_text(text), "progress-summary-indent") == [11]


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

def test_findings_are_sorted_by_line():
    findings = scan_text(CORRUPTION)
    assert [f["line"] for f in findings] == sorted(f["line"] for f in findings)


def test_finding_carries_required_keys():
    finding = scan_text("fatal: leaked\n")[0]
    assert set(finding) == {"line", "kind", "detail", "text"}


def test_detail_names_the_matched_signature():
    finding = scan_text("Everything up-to-date, apparently.\n")[0]
    assert "Everything up-to-date" in finding["detail"]


def test_empty_text_is_clean():
    assert scan_text("") == []


def test_strip_code_spans_replaces_with_space_not_nothing():
    # Deleting spans could join neighbours into a new false match; a space cannot.
    assert strip_code_spans("a`x`b") == "a b"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
