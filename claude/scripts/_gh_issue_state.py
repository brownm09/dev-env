#!/usr/bin/env python3
"""Shared GitHub issue/PR REST state helpers (dev-env#967, ADR-131).

Extracted from ``reconcile-pending-tiles.py`` (which re-imports these names so its
existing callers and tests are unaffected) so a second consumer — ``retro-chain-status.py``,
the read-only classifier behind the chained-tile retro-action backlog refill mechanism —
can reuse the same hardened REST-transport logic without duplicating it. This mirrors the
repo's established extraction convention: shared logic lives in an underscore module that
both the original script and a new consumer import directly (see ``_journal_shards.py`` /
ADR-057, ``_worktree_canon.py`` / ADR-073, ``_journal_schema.py`` / ADR-081). No production
script ever ``importlib``s a hyphenated sibling.

Two REST-specific hazards live here, both hard-won (dev-env#882, ADR-118 Amendment 3):

  - **REST models a pull request as an issue.** ``GET /repos/<repo>/issues`` returns both,
    distinguished only by the presence of a ``pull_request`` key — the GraphQL predecessor
    (``gh issue list``) filtered PRs server-side, so a caller that only ever used GraphQL
    never needed this check. ``issue_states_from_rows`` drops any row carrying the key.
  - **REST returns lowercase state.** ``gh issue list`` returned ``"OPEN"``/``"CLOSED"``;
    REST returns ``"open"``/``"closed"``. ``issue_states_from_rows`` upper-cases it so
    downstream case-sensitive comparisons (``is_closed``) behave identically regardless of
    transport.

Also carries the strict ``owner/repo`` URL validation this repo standardizes on
(``repo_from_issue_url``) wherever a GitHub issue/PR URL might otherwise be handed
unvalidated to ``gh --repo`` — which accepts a ``HOST/OWNER/REPO`` form, making an
unvalidated URL segment a credential-redirect primitive, not a cosmetic risk. See that
function's own docstring for the four required checks.

This module is import-only in the sense that it has no ``main()`` and no ``_winsubp`` —
but it DOES perform two subprocess calls (``fetch_repo_issue_states``, ``check_issue_state``),
both wrapping ``gh api`` against the REST Issues endpoint
(https://docs.github.com/en/rest/issues/issues). Those two are the untested subprocess
boundary, matching this repo's fixture-only convention; every pure function around them
(``repo_from_issue_url``, ``issue_states_from_rows``, ``should_stop_paging``,
``issue_number_from_url``, ``is_closed``) unit-tests offline
(``tests/test_gh_issue_state.py``).
"""
from __future__ import annotations

import json
import re
import subprocess
from urllib.parse import urlparse

# Conservative owner/repo character class. Deliberately narrower than what GitHub
# technically permits so anything surprising fails closed (return None) rather than being
# handed to `gh --repo` or interpolated into a REST path.
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

_GITHUB_HOST = "github.com"

# REST page size (100 is the GitHub maximum) and the default per-repo page cap for
# `fetch_repo_issue_states`. `GET /issues` returns newest-first, and that function stops as
# soon as every requested number is resolved or it has paged past them.
ISSUE_PAGE_SIZE = 100
MAX_ISSUE_PAGES = 2

# Per-HTTP-page timeout for the batched fetch, and for a single-item GET. Callers with
# tighter or looser latency budgets may override via the `timeout`/`page_size`/`max_pages`
# parameters below; these are the values `reconcile-pending-tiles.py` already runs in
# production (its own module docstring/comments explain the budget math these are balanced
# against — that reasoning is hook-specific and stays there, not duplicated here).
GH_CALL_TIMEOUT = 10
GH_ITEM_CALL_TIMEOUT = 5

# jq projection applied by `gh api`, for payload size only — NEVER for classification. It
# preserves the *presence* of `pull_request` rather than deciding what it means, so
# `issue_states_from_rows` applies one identical rule to both this shape and a full
# unprojected REST row (where `pull_request` is an object), and the filter stays testable
# against real API output. The `[...]` wrapper keeps each page a single `json.loads`-able
# array.
_ISSUE_PROJECTION = (
    '[.[] | if has("pull_request") then {number, state, pull_request: true} '
    'else {number, state} end]'
)

# The single-issue-GET counterpart of `_ISSUE_PROJECTION` — same shape, no `[.[] | ...]`
# wrapper since `repos/<repo>/issues/<n>` returns one object, not a page.
_ISSUE_ITEM_PROJECTION = (
    'if has("pull_request") then {number, state, pull_request: true} else {number, state} end'
)


def repo_from_issue_url(url) -> str | None:
    """Extract a validated ``owner/repo`` from a GitHub issue or PR URL, or ``None``.

    ``None`` means "do not trust this URL" — every caller should treat it as a reason to
    skip whatever action the URL would otherwise drive (a lookup, a destructive unlink, a
    filed cross-reference), never as a reason to fall back to a naive parse. The return
    value is meant to reach ``gh --repo`` or a REST ``repos/<owner>/<repo>/...`` path, and
    ``gh --repo`` accepts a ``HOST/OWNER/REPO`` form — so an unvalidated path segment is a
    redirect primitive that could aim a lookup (or a destructive action gated on its
    result) at another host entirely, carrying the user's credentials with it.

    Four checks, all required:

      - the scheme must be https. A GitHub-emitted issue/PR URL is always this; anything
        else (``ssh://``, ``file://``, a bare ``http://``) is anomalous — and a scheme-only
        check on ``netloc`` would pass ``ssh://github.com/o/r``;
      - the host must be github.com. Compared case-insensitively because host names are
        case-insensitive (RFC 3986 s3.2.2), so lower-casing is normalisation rather than a
        relaxation — a ``userinfo@`` prefix or any other host still fails, since the whole
        ``netloc`` must match;
      - the first two path segments must exist and match the conservative
        ``[A-Za-z0-9._-]+/[A-Za-z0-9._-]+`` character class, which excludes ``/``, ``:``,
        whitespace, and every shell metacharacter;
      - neither segment may be ``.`` or ``..``. Both are spelled entirely from characters
        the character class allows, so the regex alone would accept
        ``https://github.com/../..`` and hand ``../..`` to ``gh --repo``. Relative-path
        segments have no meaning in an ``owner/repo`` and are exactly the kind of surprise
        that must fail closed.

    Anything unparseable is a failure, not an exception.

    Note what is deliberately *not* derived here: any issue/PR number. Extract that
    separately (``issue_number_from_url``) if needed, so a caller that already has its own
    trusted source for the number (e.g. a shard's own filename, per ADR-118) never has a
    reason to prefer this function's parse over that trusted source.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() != "https":
        return None
    if parsed.netloc.lower() != _GITHUB_HOST:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[0], parts[1]
    if owner in (".", "..") or name in (".", ".."):
        return None
    repo = f"{owner}/{name}"
    if not _OWNER_REPO_RE.match(repo):
        return None
    return repo


def issue_number_from_url(url) -> int | None:
    """Parse the trailing issue/PR number from a GitHub issue or PR URL, or ``None``.

    Matches a URL ending in ``/issues/<N>`` or ``/pull/<N>`` (GitHub's own two shapes for
    "a numbered item in this repo"); anything else — a non-string, an unparseable URL, a
    URL with fewer than two path segments, or a URL whose second-to-last segment is neither
    ``issues`` nor ``pull`` — returns ``None`` rather than guessing.

    Deliberately does not validate the host/owner/repo — pair with ``repo_from_issue_url``
    for that. Kept separate because a caller with its own already-trusted issue number (a
    shard's filename, per ADR-118's "the number comes from the filename, never the URL"
    rule) has no reason to call this at all; it exists for callers that only have a URL to
    a *different* item (e.g. a chain tile's referenced queue issue) and no other source for
    its number.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    kind, number_str = parts[-2], parts[-1]
    if kind not in ("issues", "pull"):
        return None
    try:
        return int(number_str)
    except ValueError:
        return None


def is_closed(state) -> bool:
    """True only for the literal string ``"CLOSED"`` — the shared, conservative contract.

    ``"OPEN"``, an unrecognized state, and ``None`` (a ``gh`` failure, or an item outside a
    lookup window) all return ``False``. Deliberately case-sensitive: the state vocabulary
    is normalized once, at the transport boundary (``issue_states_from_rows``), so relaxing
    this to a case-fold would remove the regression pin that catches that normalization
    being dropped — which is precisely the failure the REST migration could reintroduce,
    silently: REST answers ``"closed"``, this returns ``False`` for everything, and every
    caller goes inert while still looking healthy.
    """
    return state == "CLOSED"


def issue_states_from_rows(rows) -> dict[int, str]:
    """Parsed REST ``GET /issues`` rows -> ``{issue_number: STATE}``.

    Pure, and deliberately so: this is where both REST-specific hazards live, and both are
    the kind that pass a naive test. ``fetch_repo_issue_states``/``check_issue_state`` are
    untested subprocess boundaries (this repo's fixture-only convention), so parsing here is
    what makes the hazards coverable at all.

    **Pull requests are dropped.** GitHub REST models a PR as an issue, so ``GET /issues``
    returns both, distinguished only by a ``pull_request`` key. Issues and PRs share ONE
    number sequence per repo, so this is not a collision between two live objects: the
    hazard is a caller resolving a number that actually names a PR, which without this
    filter would silently resolve to that PR's state. A dropped row is simply absent, so
    such a number resolves to ``None`` from every caller here.

    *Presence* of the key is the test, never its value — a row carrying
    ``pull_request: null`` is still treated as a PR. If the projection in
    ``fetch_repo_issue_states``/``check_issue_state`` ever changes shape, that direction
    degrades to "everything unresolved" rather than to a misclassification.

    **`state` is upper-cased.** REST returns "open"/"closed" where ``gh issue list``
    returned "OPEN"/"CLOSED", and ``is_closed`` compares against "CLOSED" without
    case-folding. Skipping this would leave every caller *inert* rather than fixed —
    fail-safe in direction, but a total, unreported loss of function.

    Malformed input degrades to omission, never to a spurious state: a non-list, a non-dict
    row, a missing or non-string ``state``, and a JSON ``true`` masquerading as issue #1
    (``isinstance(True, int)`` is ``True``) are all skipped.
    """
    states: dict[int, str] = {}
    if not isinstance(rows, list):
        return states
    for item in rows:
        if not isinstance(item, dict):
            continue
        if "pull_request" in item:
            continue
        number, state = item.get("number"), item.get("state")
        if isinstance(number, int) and not isinstance(number, bool) and isinstance(state, str):
            states[number] = state.upper()
    return states


def should_stop_paging(rows, resolved, wanted, page_size: int = ISSUE_PAGE_SIZE) -> bool:
    """True when another page of ``GET /issues`` cannot usefully change the result.

    Any of four independent reasons ends the walk:

      - the page came back short (or unusable), so it was the last one;
      - nothing was requested, so one page is already more than enough;
      - every requested number is resolved — the common case when the wanted numbers are
        recent, satisfied by page 1;
      - the page carries a number below the lowest one requested. REST ``GET /issues``
        defaults to ``sort=created&direction=desc`` and GitHub assigns numbers in creation
        order, so crossing that floor means every remaining page is older than anything
        wanted.

    The floor check reads *raw* rows, PRs included: they share the number sequence, so a PR
    below the floor proves the same thing an issue would.

    A transferred issue can break the created/number correspondence and stop the walk
    early; it then resolves to ``None`` for that number — the same fail-safe as a number
    outside the window. Both wrong directions are bounded and safe: stopping late costs one
    extra page, stopping early costs one unresolved number.
    """
    if not isinstance(rows, list) or len(rows) < page_size:
        return True
    if not wanted:
        return True
    if set(wanted) <= set(resolved):
        return True
    floor = min(wanted)
    for item in rows:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if isinstance(number, int) and not isinstance(number, bool) and number < floor:
            return True
    return False


def fetch_repo_issue_states(repo: str, numbers=(), page_size: int = ISSUE_PAGE_SIZE,
                            max_pages: int = MAX_ISSUE_PAGES,
                            timeout: int = GH_CALL_TIMEOUT) -> dict[int, str] | None:
    """REST ``GET /repos/<repo>/issues`` for *repo* -> ``{issue_number: STATE}``; ``None``
    if no page was read.

    Interpolating *repo* into a REST **path** is strictly safer than the ``--repo`` flag it
    could otherwise use: ``gh --repo`` accepts a ``HOST/OWNER/REPO`` form, which is the
    credential-redirect primitive ``repo_from_issue_url`` exists to block, while
    ``repos/<owner>/<repo>/issues`` cannot name a host at all. Validate *repo* via
    ``repo_from_issue_url`` before calling this regardless — defence in depth costs nothing
    here.

    Pagination is bounded and stops early (``should_stop_paging``): the normal case is one
    page, when the wanted numbers are recent. On any page failure the walk stops and
    whatever was already collected is returned — a partial result can only ever *omit* a
    number, never mis-resolve one. ``None`` is returned only when not even the first page
    was read.

    Not unit-tested — subprocess boundary, matching this repo's fixture-only convention.
    Everything around it is: row parsing through ``issue_states_from_rows``, the stop rule
    through ``should_stop_paging``.
    """
    wanted = {n for n in numbers if isinstance(n, int) and not isinstance(n, bool)}
    states: dict[int, str] = {}
    read_a_page = False
    for page in range(1, max_pages + 1):
        try:
            result = subprocess.run(
                ["gh", "api",
                 f"repos/{repo}/issues?state=all&per_page={page_size}&page={page}",
                 "--jq", _ISSUE_PROJECTION],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                break
            rows = json.loads(result.stdout)
        except Exception:
            break
        read_a_page = True
        states.update(issue_states_from_rows(rows))
        if should_stop_paging(rows, states, wanted, page_size):
            break
    if not read_a_page:
        return None
    return states


def check_issue_state(issue_number: int, repo: str, timeout: int = GH_ITEM_CALL_TIMEOUT) -> str | None:
    """Return 'OPEN' or 'CLOSED' for one issue; ``None`` on any failure. One REST call.

    Deliberately per-item rather than routed through ``fetch_repo_issue_states``: a caller
    checking one specific, possibly-old number has no "recent by construction" guarantee
    the batched fetch's paging window relies on, so the batch could silently miss it.

    Reuses the already-tested ``issue_states_from_rows([row])`` so PR-row-filtering and
    state-casing hazards are handled in exactly one place for both the batched and per-item
    paths, rather than a second, drift-prone copy. Not unit-tested: subprocess boundary,
    matching ``fetch_repo_issue_states``'s convention.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{issue_number}",
             "--jq", _ISSUE_ITEM_PROJECTION],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        row = json.loads(result.stdout)
    except Exception:
        return None
    return issue_states_from_rows([row]).get(issue_number)
