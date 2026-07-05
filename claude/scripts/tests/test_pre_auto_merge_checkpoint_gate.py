#!/usr/bin/env python3
"""Tests for pre-auto-merge-checkpoint-gate.py's pure helpers (ADR-083).

Exercises the hook's flag-parsing, marker-parsing, freshness-comparison, and qualifying-comment
selection logic offline (no disk, no network, no gh) -- the pure-helper coverage layer, added
alongside the behavioral self-test `test-auto-merge-checkpoint-gate.sh` (which drives the real
hook end-to-end via the MERGE_GATE_TEST_JSON seam to pin the allow/block decision paths named in
ADR-083's Follow-up item 3). `main()`'s stdin/exit-2 plumbing and the live `gh pr view` call
(`_fetch_pr_json`, reused from `pre-merge-findings-gate.py` and already covered by that file's own
test suite) are not covered here -- pure-helper convention, matching
`test_pre_merge_findings_gate.py`'s own scope note.
"""
import importlib.util
import os
import sys

# ---------------------------------------------------------------------------
# Load the module under test without executing main()
# ---------------------------------------------------------------------------
_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-auto-merge-checkpoint-gate.py")
# The script imports _winsubp/_hookio (siblings in scripts/) and dynamically loads its own sibling
# pre-merge-findings-gate.py; make all of them resolvable.
sys.path.insert(0, os.path.dirname(_SCRIPT))
spec = importlib.util.spec_from_file_location("pre_auto_merge_checkpoint_gate", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

wants_auto_merge = mod.wants_auto_merge
_is_stale = mod._is_stale
_qualifying_comment = mod._qualifying_comment
_CHECKPOINTS_RE = mod._CHECKPOINTS_RE
_VALID_ADR_WARRANT = mod._VALID_ADR_WARRANT
_VALID_DOC_RECONCILIATION = mod._VALID_DOC_RECONCILIATION
is_pr_merge_command = mod.is_pr_merge_command

# is_merge_help_only lives in _hookio (a sibling); its directory is already on sys.path.
from _hookio import is_merge_help_only  # noqa: E402


# ---------------------------------------------------------------------------
# wants_auto_merge / --auto flag detection
# ---------------------------------------------------------------------------

def test_bare_auto():
    assert wants_auto_merge("gh pr merge 999 --auto --squash")

def test_auto_equals_true():
    assert wants_auto_merge("gh pr merge --auto=true --squash")

def test_auto_equals_false_is_falsy():
    assert not wants_auto_merge("gh pr merge --auto=false --squash")

def test_auto_equals_zero_is_falsy():
    assert not wants_auto_merge("gh pr merge --auto=0 --squash")

def test_auto_equals_no_is_falsy():
    assert not wants_auto_merge("gh pr merge --auto=no --squash")

def test_auto_equals_quoted_false_is_falsy():
    # Quote-stripping is a deliberate improvement over is_mutating_gh_segment's
    # --delete-branch=false precedent, which doesn't strip quotes.
    assert not wants_auto_merge("gh pr merge --auto='false' --squash")
    assert not wants_auto_merge('gh pr merge --auto="false" --squash')

def test_no_auto_at_all():
    assert not wants_auto_merge("gh pr merge --squash --delete-branch")

def test_disable_auto_is_not_auto():
    # --disable-auto turns OFF a pending auto-merge -- always safe, never in scope here.
    assert not wants_auto_merge("gh pr merge --disable-auto")

def test_auto_survives_cd_chain():
    assert wants_auto_merge("cd C:/Users/brown/Git/dev-env && gh pr merge --auto --squash")

def test_auto_scoped_to_merge_tail_not_earlier_chain_segment():
    # --auto appearing before the merge statement, in an unrelated earlier command, must not count.
    assert not wants_auto_merge("echo --auto && gh pr merge --squash")

def test_non_merge_command_never_matches():
    assert not wants_auto_merge("git status --auto")


# ---------------------------------------------------------------------------
# is_merge_help_only composition with --auto (dev-env#557's guard, exercised alongside --auto)
# ---------------------------------------------------------------------------

def test_auto_help_is_help_only():
    # gh pr merge --auto --help must still resolve as help-only downstream, even though
    # wants_auto_merge itself correctly reports True for the --auto token's presence -- the hook's
    # actual check order (is_pr_merge_command -> wants_auto_merge -> is_merge_help_only) reaches
    # this guard and exits 0 before any live PR lookup.
    command = "gh pr merge --auto --help"
    assert wants_auto_merge(command) is True
    assert is_merge_help_only(command) is True
    return "gh pr merge --auto --help: wants_auto_merge True, is_merge_help_only True -> hook exits 0 via the help-only check"


# ---------------------------------------------------------------------------
# premerge-checkpoints marker parsing
# ---------------------------------------------------------------------------

def test_checkpoints_marker_parses_valid_values():
    body = "<!-- premerge-checkpoints: adr_warrant=written doc_reconciliation=updated -->"
    m = _CHECKPOINTS_RE.search(body)
    assert m is not None
    assert m.groups() == ("written", "updated")

def test_checkpoints_marker_parses_missing_values():
    body = "<!-- premerge-checkpoints: adr_warrant=missing doc_reconciliation=missing -->"
    m = _CHECKPOINTS_RE.search(body)
    assert m is not None
    assert m.groups() == ("missing", "missing")

def test_checkpoints_marker_absent():
    assert _CHECKPOINTS_RE.search("just a normal comment, no marker here") is None

def test_checkpoints_marker_alongside_review_findings_marker():
    body = (
        "Review done.\n"
        "<!-- review-findings: blocking=0 non_blocking=0 -->\n"
        "<!-- premerge-checkpoints: adr_warrant=not-warranted doc_reconciliation=not-applicable -->"
    )
    m = _CHECKPOINTS_RE.search(body)
    assert m is not None
    assert m.groups() == ("not-warranted", "not-applicable")

def test_valid_value_sets_reject_missing():
    # "missing" is a deliberate third literal that must NOT satisfy either valid-value set --
    # it exists so an unresolved gap is visibly recorded rather than silently blank, while still
    # correctly failing the hook's validity check.
    assert "missing" not in _VALID_ADR_WARRANT
    assert "missing" not in _VALID_DOC_RECONCILIATION
    assert _VALID_ADR_WARRANT == {"written", "not-warranted"}
    assert _VALID_DOC_RECONCILIATION == {"updated", "not-applicable"}


# ---------------------------------------------------------------------------
# freshness comparison (_is_stale)
# ---------------------------------------------------------------------------

def test_fresh_comment_after_head_commit_is_not_stale():
    assert not _is_stale(comment_created_at="2026-07-05T10:00:00Z", head_committed_at="2026-07-05T09:00:00Z")

def test_stale_comment_before_head_commit_is_stale():
    assert _is_stale(comment_created_at="2026-07-04T06:52:08Z", head_committed_at="2026-07-04T06:54:43Z")

def test_equal_timestamps_are_not_stale():
    # "not older than" -- equal counts as fresh, not stale.
    ts = "2026-07-05T10:00:00Z"
    assert not _is_stale(comment_created_at=ts, head_committed_at=ts)


# ---------------------------------------------------------------------------
# _qualifying_comment -- single-comment-carries-both-markers requirement
# ---------------------------------------------------------------------------

_FINDINGS_CLEAN = "<!-- review-findings: blocking=0 non_blocking=0 -->"
_CHECKPOINTS_OK = "<!-- premerge-checkpoints: adr_warrant=written doc_reconciliation=updated -->"

def test_qualifying_comment_requires_both_markers_together():
    # Each comment carries only ONE of the two markers -- neither qualifies on its own, and
    # crucially this must not be satisfied by combining across the two comments.
    comments = [
        {"body": _FINDINGS_CLEAN},
        {"body": _CHECKPOINTS_OK},
    ]
    assert _qualifying_comment(comments) is None

def test_qualifying_comment_finds_comment_with_both():
    comments = [
        {"body": "unrelated comment"},
        {"body": f"{_FINDINGS_CLEAN}\n{_CHECKPOINTS_OK}"},
    ]
    found = _qualifying_comment(comments)
    assert found is not None
    _, mk, ck = found
    assert mk.groups() == ("0", "0")
    assert ck.groups() == ("written", "updated")

def test_qualifying_comment_last_wins_over_earlier_qualifying_comment():
    older = f"{_FINDINGS_CLEAN}\n<!-- premerge-checkpoints: adr_warrant=missing doc_reconciliation=updated -->"
    newer = f"{_FINDINGS_CLEAN}\n{_CHECKPOINTS_OK}"
    comments = [{"body": older}, {"body": newer}]
    _, _, ck = _qualifying_comment(comments)
    assert ck.groups() == ("written", "updated")  # the LATER comment's values win, not the earlier

def test_qualifying_comment_marker_order_within_comment_does_not_matter():
    reversed_order = f"{_CHECKPOINTS_OK}\n{_FINDINGS_CLEAN}"
    assert _qualifying_comment([{"body": reversed_order}]) is not None

def test_qualifying_comment_empty_list():
    assert _qualifying_comment([]) is None


# ---------------------------------------------------------------------------
# Reused is_pr_merge_command composition sanity (imported from pre-merge-findings-gate.py)
# ---------------------------------------------------------------------------

def test_is_pr_merge_command_reused_correctly():
    assert is_pr_merge_command("gh pr merge 999 --auto --squash") is True
    assert is_pr_merge_command("git status") is False


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
