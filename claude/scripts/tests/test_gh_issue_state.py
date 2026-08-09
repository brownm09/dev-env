#!/usr/bin/env python3
"""Unit tests for _gh_issue_state — the shared GitHub issue/PR REST state module
(dev-env#967, ADR-131).

`reconcile-pending-tiles.py` originally carried all of this logic inline; it moved here so
a second consumer, `retro-chain-status.py` (the read-only classifier behind the chained-tile
retro-action backlog refill mechanism), can reuse the same hardened REST-transport code
rather than growing a second, drift-prone copy of two hard-won hazards (dev-env#882,
ADR-118 Amendment 3):

  - **REST models a pull request as an issue.** `GET /issues` returns both, distinguished
    only by a `pull_request` key. `test_issue_states_from_rows_drops_pull_requests` and its
    neighbours pin that a number naming a PR resolves to unresolved, never to that PR's
    state.
  - **REST returns lowercase state.** `is_closed` compares the literal string `"CLOSED"`
    without case-folding; `test_issue_states_from_rows_uppercases_state` pins that the
    transport boundary normalizes case before that comparison ever runs, since dropping the
    normalization would leave every caller silently inert while still looking healthy.

`repo_from_issue_url` carries the strict `owner/repo` URL validation this repo standardizes
on wherever a URL might reach `gh --repo` (which accepts a `HOST/OWNER/REPO` form, making an
unvalidated segment a credential-redirect primitive) — most of its coverage here is carried
over unchanged from this module's pre-extraction home in
`tests/test_reconcile_pending_tiles.py`.

`issue_number_from_url` is new in this extraction (dev-env#967): callers of the shared
module sometimes need a number from a URL to a *different* item than their own trusted
identity source (e.g. a chained tile's referenced queue issue), which `reconcile-pending-tiles.py`
itself never needed (ADR-118 already gave it a trusted number — the shard's own filename).

`fetch_repo_issue_states`/`check_issue_state` (the live `gh` boundary) are not tested here —
subprocess boundary, matching this repo's fixture-only convention. The batching,
pagination stop-rule, and both REST hazards *around* them are tested directly through the
pure functions below.

Usage:
    py -3 claude/scripts/tests/test_gh_issue_state.py

Exit 0 = all pass.
"""

import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "claude" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _gh_issue_state as mod  # noqa: E402

from _gh_issue_state import (  # noqa: E402
    is_closed,
    issue_number_from_url,
    issue_states_from_rows,
    repo_from_issue_url,
    should_stop_paging,
)


# --- url validation: the guard in front of any repo-scoped lookup ------------


def test_repo_from_issue_url_accepts_github() -> str:
    assert repo_from_issue_url("https://github.com/brownm09/dev-env/issues/869") == "brownm09/dev-env"
    assert repo_from_issue_url("https://github.com/o/r") == "o/r", "no trailing path needed"
    assert repo_from_issue_url("https://github.com/o/r/issues/1?x=y#z") == "o/r"
    assert repo_from_issue_url("https://GitHub.COM/o/r/issues/1") == "o/r", \
        "host compare is case-insensitive (RFC 3986 s3.2.2 normalization, not a relaxation)"
    assert repo_from_issue_url("https://github.com/o/r/pull/1") == "o/r", \
        "a PR URL resolves the same way as an issue URL -- both are just 'owner/repo'"
    return "well-formed github.com issue/PR URLs resolve to owner/repo"


def test_repo_from_issue_url_rejects_foreign_and_crafted_hosts() -> str:
    # `gh --repo` accepts HOST/OWNER/REPO, so a non-github host is a credential-redirect
    # primitive, not a cosmetic problem.
    for bad in [
        "https://evil.com/o/r/issues/1",
        "https://github.com.evil.com/o/r/issues/1",
        "https://user@github.com/o/r/issues/1",   # userinfo -> netloc != github.com
        "https://github.com:8080/o/r/issues/1",   # port -> netloc != github.com
        "https://raw.githubusercontent.com/o/r",
        "ssh://github.com/o/r",                   # non-http scheme still carries a netloc
    ]:
        assert repo_from_issue_url(bad) is None, f"must reject foreign/crafted host: {bad}"
    return "any netloc that is not exactly github.com is rejected (incl. userinfo, port, subdomain)"


def test_repo_from_issue_url_rejects_bad_owner_repo_chars() -> str:
    for bad in [
        "https://github.com/o r/repo",         # space
        "https://github.com/o;rm -rf/repo",    # shell metacharacters
        "https://github.com/o/re:po",          # colon -> could read as HOST/OWNER/REPO
        "https://github.com/../../etc/passwd",
        "https://github.com/o",                # only one path segment
        "https://github.com/",                 # no segments
    ]:
        assert repo_from_issue_url(bad) is None, f"must reject: {bad}"
    return "owner/repo must match ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$; anything else rejected"


def test_repo_from_issue_url_rejects_non_strings_and_garbage() -> str:
    for bad in [None, "", 42, [], {}, "not a url", "github.com/o/r"]:
        assert repo_from_issue_url(bad) is None, f"must reject: {bad!r}"
    return "None/non-str/empty/scheme-less input returns None rather than raising"


# --- issue/PR number extraction (new in this extraction, dev-env#967) --------


def test_issue_number_from_url_parses_issues_and_pull() -> str:
    assert issue_number_from_url("https://github.com/o/r/issues/1203") == 1203
    assert issue_number_from_url("https://github.com/o/r/pull/814") == 814
    assert issue_number_from_url("https://github.com/o/r/issues/1?x=y#z") == 1, \
        "a query string / fragment must not leak into the parsed number"
    return "the trailing /issues/<N> or /pull/<N> segment parses to an int"


def test_issue_number_from_url_rejects_non_matching_shapes() -> str:
    for bad in [
        None, "", 42, [], {},
        "https://github.com/o/r",                  # no issues/pull segment at all
        "https://github.com/o/r/discussions/5",    # a real GitHub path, wrong kind
        "https://github.com/o/r/issues/abc",       # non-numeric
        "not a url",
    ]:
        assert issue_number_from_url(bad) is None, f"must reject: {bad!r}"
    return "anything not ending in /issues/<N> or /pull/<N> with a numeric N returns None"


def test_issue_number_from_url_does_not_validate_host() -> str:
    # Deliberately a separate concern from repo_from_issue_url -- a caller that needs both
    # trust guarantees calls both functions, never relies on one to imply the other.
    assert issue_number_from_url("https://evil.com/o/r/issues/5") == 5, \
        "number extraction alone does not gate on host -- pair with repo_from_issue_url for that"
    return "issue_number_from_url extracts the number only; host/owner/repo trust is a separate check"


# --- is_closed: the shared, conservative removal/liveness contract -----------


def test_is_closed() -> str:
    assert is_closed("CLOSED") is True
    assert is_closed("OPEN") is False
    assert is_closed(None) is False, "a gh failure (None) is conservative -- not closed"
    assert is_closed("") is False
    assert is_closed("closed") is False, "the state vocabulary is uppercase; no case-folding"
    assert is_closed("MERGED") is False, "issues never merge -- an unknown state is not closed"
    return "only a literal 'CLOSED' is closed; OPEN/None/unknown/lowercase are all not"


# --- REST transport: PR rows and state case (dev-env#882) --------------------


def _rest_issue(number, state="open", **over):
    """A row shaped as REST `GET /issues` actually returns it -- lowercase state, no marker."""
    row = {"number": number, "state": state, "title": f"issue {number}"}
    row.update(over)
    return row


def _rest_pr(number, state="closed"):
    """A PR row: REST models pull requests as issues, marked only by `pull_request`."""
    return {"number": number, "state": state, "title": f"pr {number}",
            "pull_request": {"url": f"https://api.github.com/repos/o/r/pulls/{number}"}}


def test_issue_states_from_rows_drops_pull_requests() -> str:
    # REST /issues returns PRs too. Issues and PRs share ONE number sequence per repo, so
    # this is not a collision between two live objects -- the hazard is a caller resolving a
    # number that actually names a PR, which without the filter resolves to that PR's state.
    rows = [_rest_pr(885), _rest_pr(884), _rest_issue(883), _rest_issue(882)]
    got = issue_states_from_rows(rows)
    assert got == {883: "OPEN", 882: "OPEN"}, f"only issue rows survive, got {got}"
    for pr in (885, 884):
        assert pr not in got, f"PR #{pr} must be absent (-> None -> not closed), not resolved"
    assert is_closed(got.get(885)) is False, \
        "a number naming a closed PR must resolve to unresolved, never to 'closed'"
    return "PR rows are dropped, so a number naming a PR resolves to None (not closed)"


def test_issue_states_from_rows_filters_projected_pr_shape_identically() -> str:
    # `fetch_repo_issue_states` asks gh for a projection that keeps `pull_request` as a bare
    # `true` instead of the full object, purely to shrink the payload. The rule keys on the
    # key's PRESENCE, so both shapes must classify the same -- otherwise the projection
    # silently becomes classification logic living in an untested jq string.
    full = issue_states_from_rows([_rest_pr(885), _rest_issue(883)])
    projected = issue_states_from_rows([
        {"number": 885, "state": "closed", "pull_request": True},
        {"number": 883, "state": "open"},
    ])
    assert full == projected == {883: "OPEN"}, f"{full} != {projected}"
    return "the projected `pull_request: true` shape and a full REST row classify identically"


def test_issue_states_from_rows_treats_pull_request_key_presence_not_truthiness() -> str:
    # If the projection ever emits an explicit null for issues, presence-based filtering
    # drops every row -> everything unresolved. That is the safe direction, and it is the
    # reason the rule is presence rather than truthiness; pinned so it stays that way.
    got = issue_states_from_rows([{"number": 883, "state": "open", "pull_request": None}])
    assert got == {}, f"a null `pull_request` still marks a PR row, got {got}"
    return "presence of `pull_request` classifies, not its value -- degrades to unresolved, not mispruned"


def test_issue_projection_preserves_the_pull_request_marker() -> str:
    # `_ISSUE_PROJECTION` decides what SHAPE reaches issue_states_from_rows, and it cannot be
    # executed offline (gh owns the jq), so without this gate it is the one piece of the
    # transport covered by nothing but a comment. The dangerous edit is the natural-looking
    # simplification `[.[] | {number, state}]` -- dropping the marker as redundant, since
    # Python does the filtering anyway. That makes PR rows arrive INDISTINGUISHABLE from
    # issues, so a number naming a closed PR is resolved as closed: the exact
    # misclassification this module exists to prevent, with every other test here still
    # green.
    #
    # Structural, in the same spirit as `_composed_output_scan.py`'s fence-aware AST scan:
    # pin the properties that make the projection safe, not its exact spelling.
    proj = mod._ISSUE_PROJECTION
    assert 'has("pull_request")' in proj, \
        "the projection must DETECT the PR marker"
    assert proj.count("pull_request") >= 2, \
        "the marker must be detected AND re-emitted -- one occurrence cannot round-trip it"
    assert "select(" not in proj, \
        ("the projection must not filter rows: classification belongs in "
         "issue_states_from_rows, where it is testable, not in an unexecutable jq string")
    for field in ("number", "state"):
        assert field in proj, f"the projection must carry `{field}` through"
    return "the jq projection detects and re-emits `pull_request`, and never filters rows itself"


def test_issue_item_projection_preserves_the_pull_request_marker() -> str:
    # The single-issue-GET counterpart, for check_issue_state -- same properties, no `[.[]
    # | ...]` wrapper since a single-object GET returns one row, not a page.
    proj = mod._ISSUE_ITEM_PROJECTION
    assert 'has("pull_request")' in proj
    assert proj.count("pull_request") >= 2
    assert "select(" not in proj
    for field in ("number", "state"):
        assert field in proj
    return "the single-item jq projection carries the same PR-marker-preserving properties"


def test_issue_states_from_rows_uppercases_state() -> str:
    got = issue_states_from_rows([_rest_issue(1, "open"), _rest_issue(2, "closed")])
    assert got == {1: "OPEN", 2: "CLOSED"}, f"REST lowercase must be normalized, got {got}"
    assert is_closed(got[2]) is True and is_closed(got[1]) is False, \
        "the normalized values must satisfy is_closed's case-sensitive contract"
    return 'REST "open"/"closed" are upper-cased into the vocabulary is_closed expects'


def test_issue_states_from_rows_tolerates_junk() -> str:
    for junk in [None, {}, "rows", 42]:
        assert issue_states_from_rows(junk) == {}, f"non-list input must yield {{}}: {junk!r}"
    got = issue_states_from_rows([
        "not a dict",
        None,
        {"number": 1},                                # no state
        {"number": 2, "state": 7},                    # non-string state
        {"state": "open"},                            # no number
        {"number": "3", "state": "open"},             # string number
        {"number": True, "state": "closed"},          # isinstance(True, int) is True
        _rest_issue(9, "open"),                       # the one good row
    ])
    assert got == {9: "OPEN"}, f"only the well-formed row survives, got {got}"
    return "malformed rows degrade to omission (-> unresolved), never to a spurious CLOSED"


# --- REST transport: bounded, early-exit pagination --------------------------


def _page(numbers, state="open"):
    return [_rest_issue(n, state) for n in numbers]


def test_page_budget_cannot_outrun_default_constants() -> str:
    # Mirrors reconcile-pending-tiles.py's own invariant pin, at the level this module now
    # owns: a caller balancing its own wall-clock budget against these defaults needs
    # MAX_ISSUE_PAGES * GH_CALL_TIMEOUT to be a small, known number, not a moving target.
    # This does not assert a *specific* caller's budget (that belongs to the caller's own
    # test, e.g. reconcile-pending-tiles.py's test_page_budget_cannot_outrun_the_lookup_budget)
    # -- it just pins the module's own defaults stay small enough to reason about.
    worst_case = mod.MAX_ISSUE_PAGES * mod.GH_CALL_TIMEOUT
    assert worst_case <= 30, (
        f"default MAX_ISSUE_PAGES ({mod.MAX_ISSUE_PAGES}) * GH_CALL_TIMEOUT "
        f"({mod.GH_CALL_TIMEOUT}) = {worst_case}s -- a caller sizing its own budget against "
        "these defaults needs this to stay small and deliberate, not silently grow"
    )
    return "MAX_ISSUE_PAGES * GH_CALL_TIMEOUT default worst-case stays small and known"


def test_should_stop_paging_stops_on_short_or_unusable_page() -> str:
    assert should_stop_paging(_page(range(200, 195, -1)), {}, {150}, page_size=100) is True
    for junk in [None, "rows", {}]:
        assert should_stop_paging(junk, {}, {150}, page_size=100) is True, \
            f"an unusable page ends the walk rather than looping: {junk!r}"
    return "a short page is the last page; an unusable one ends the walk instead of looping"


def test_should_stop_paging_stops_when_everything_wanted_is_resolved() -> str:
    full = _page(range(200, 100, -1))
    assert len(full) == 100, "fixture must be a full page or the short-page rule masks this one"
    assert should_stop_paging(full, {150: "OPEN", 160: "OPEN"}, {150, 160}, page_size=100) is True
    assert should_stop_paging(full, {150: "OPEN"}, {150, 99}, page_size=100) is False, \
        "one number still missing and the floor not yet crossed -> another page is worth fetching"
    assert should_stop_paging(full, {}, set(), page_size=100) is True, \
        "nothing requested -> one page is already more than enough"
    return "the walk stops once every requested number resolves (page 1, in the normal case)"


def test_should_stop_paging_stops_after_crossing_the_lowest_wanted_number() -> str:
    # REST /issues is created-desc and numbers are assigned in creation order, so a row
    # below the lowest wanted number proves every later page is older still. #101 here is a
    # PR, so it never reaches `resolved` -- exactly the case that would otherwise page to
    # the cap for nothing.
    full = _page(range(200, 101, -1)) + [_rest_pr(101)]
    assert len(full) == 100
    assert should_stop_paging(full, {150: "OPEN"}, {150, 105}, page_size=100) is True, \
        "row #101 is below the floor of 105 -> stop; #105 stays unresolved (kept by callers)"
    assert should_stop_paging(full, {150: "OPEN"}, {150, 100}, page_size=100) is False, \
        "floor 100 is not crossed by this page -> keep paging"
    return "crossing min(wanted) ends the walk; the unfound number resolves to None"


def main() -> int:
    tests = [
        ("url validation accepts github.com", test_repo_from_issue_url_accepts_github),
        ("url validation rejects foreign/crafted hosts", test_repo_from_issue_url_rejects_foreign_and_crafted_hosts),
        ("url validation rejects bad owner/repo chars", test_repo_from_issue_url_rejects_bad_owner_repo_chars),
        ("url validation rejects non-strings/garbage", test_repo_from_issue_url_rejects_non_strings_and_garbage),
        ("issue_number_from_url parses issues/pull", test_issue_number_from_url_parses_issues_and_pull),
        ("issue_number_from_url rejects non-matching shapes", test_issue_number_from_url_rejects_non_matching_shapes),
        ("issue_number_from_url does not validate host", test_issue_number_from_url_does_not_validate_host),
        ("is_closed", test_is_closed),
        ("REST: PR rows dropped", test_issue_states_from_rows_drops_pull_requests),
        ("REST: projected PR shape filtered alike", test_issue_states_from_rows_filters_projected_pr_shape_identically),
        ("REST: pull_request presence, not truthiness", test_issue_states_from_rows_treats_pull_request_key_presence_not_truthiness),
        ("REST: jq projection preserves the PR marker", test_issue_projection_preserves_the_pull_request_marker),
        ("REST: item jq projection preserves the PR marker", test_issue_item_projection_preserves_the_pull_request_marker),
        ("REST: state upper-cased", test_issue_states_from_rows_uppercases_state),
        ("REST: malformed rows omitted, never CLOSED", test_issue_states_from_rows_tolerates_junk),
        ("page budget defaults stay small and known", test_page_budget_cannot_outrun_default_constants),
        ("paging stops on a short/unusable page", test_should_stop_paging_stops_on_short_or_unusable_page),
        ("paging stops once wanted is resolved", test_should_stop_paging_stops_when_everything_wanted_is_resolved),
        ("paging stops after crossing min(wanted)", test_should_stop_paging_stops_after_crossing_the_lowest_wanted_number),
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
