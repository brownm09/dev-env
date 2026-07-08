#!/usr/bin/env python3
"""Claude Code Stop hook — state-keyed tile-enumeration gate (ADR-088, extended ADR-092).

The command-keyed ``post-merge-tile-checkpoint.py`` (ADR-060) fires on a
``gh pr merge`` *you run*, but is BLIND to a PR that reaches merged state some
other way: auto-merge landing it server-side while you were away, or a pure
``gh api`` merge. Those are exactly the cases where the tile checkpoint's
salience is lowest (post-merge is pure bookkeeping) — the lifting-logbook
PR #700 incident that motivated the ADR-046 2026-07-05 forcing-function
refinement (dev-env#595).

This hook is STATE-keyed instead: at every Stop it scans the just-ended session
transcript for TWO independent triggers, each requiring the same recorded
tile-enumeration artifact before the stop is allowed:

1. **Merged PR** (ADR-088): a PR reached MERGED state this session (by any
   path) but no enumeration was recorded.
2. **Dangling created issue** (ADR-092, dev-env#638): a `gh issue create`
   ran this session and the created issue was NOT resolved by session end
   (resolved = closed via a same-session merged PR carrying a GitHub
   auto-close keyword, or explicitly closed via `gh issue close`) but no
   enumeration was recorded. Investigation sessions that file well-scoped
   issues and implement nothing get no mechanical nudge otherwise — unlike a
   merged PR, which this hook already covers.

Both triggers are the direct Stop-hook analog of ``pre-merge-findings-gate``
(ADR-039) and BLOCK the stop (exit 2) with a reminder on stderr. A recorded
enumeration is either an actual ``spawn_task`` tile, or the prescribed text
("Follow-ups considered: ... -> tiled (task_id / #N) / -> not tiled, because
<reason>") — session-global and shared by both triggers (one enumeration
satisfies either or both). A bare "no follow-ups" assertion does NOT satisfy
the gate: per the ADR-046 refinement, "No follow-ups" is valid only as the
visible result of an enumeration, never as a bare assertion (the #700 skip).

Complements — does not replace — the command-keyed hook: that one is the
immediate in-the-moment nudge when ``gh pr merge`` runs; this one is the
Stop-time verification that the enumeration actually happened, covering every
merge path (and, since ADR-092, the dangling-issue path too). It is also NOT
inert in background / SDK-launched sessions (ADR-053), where every
PostToolUse hook — including the command-keyed sibling — silently never
fires; the Stop event still dispatches, so this is the only tile enforcement
that survives there.

Detection is a pure transcript scan — no ``gh`` calls, no network, no
subprocess (so no ``_winsubp``). Fail-open: any error exits 0. Fires at most
once per session via a scratch sentinel (mirrors ``posttooluse-inert-advisory.py``),
and honors the ``stop_hook_active`` loop-guard flag (Claude Code hooks
reference: a Stop hook must check it and exit 0 early once continuing, or it can
block forever).

The transcript-record readers (``load_records`` / ``_parse_records`` /
``iter_bash_calls`` / ``_result_text`` / ``_content_items``) now live in
``_hookutil`` — the same shared module the sentinels / transcript-locate come
from — so this hook and ``posttooluse-inert-advisory.py`` can no longer drift on
how a transcript is parsed (ADR-090, reversing ADR-088's original replicate-them
decision after both PR #604 reviewers flagged the duplication). This gate imports
only the three it uses: ``_content_items`` and ``_parse_records`` (its ``main()``
parses the transcript text directly, after the cheap pre-filter), and
the shared ``iter_bash_calls`` (aliased) — wrapped in a thin 2-tuple adapter below,
since ``_hookutil``'s ``iter_bash_calls`` returns ``(command, output, cwd)`` and
this gate never needs ``cwd``. (``load_records`` and ``_result_text`` also live in
``_hookutil`` but the gate needs neither directly — ``_result_text`` is used only
inside the shared ``iter_bash_calls``.) The merge-marker / segment parser is
imported from ``_hookio``; ``_first_line`` stays local — a command-segment helper
(not a transcript reader), intentionally duplicated with ``_hookio._first_line``
as a separate decision (ADR-088).

Stdin JSON shape (Stop):
  {"session_id": "...", "transcript_path": "/abs/path.jsonl",
   "stop_hook_active": false, ...}

Exit 0 — no merged-state PR and no dangling created issue this session,
         enumeration already recorded, a "skip tiles" override present,
         already fired (sentinel), stop_hook_active set, or any error
         (fail-open).
Exit 2 — a PR merged and/or a created issue remains unresolved this session,
         with no recorded tile-enumeration; blocking reminder(s) on stderr.
"""
from __future__ import annotations

import _hookutil
import json
import re
import sys
from pathlib import Path

from _hookio import (
    merge_pr_number_from_output,
    output_has_merge_marker,
    split_top_level,
)
from _hookutil import (
    _content_items,
    _parse_records,
    iter_bash_calls as _iter_bash_calls,
)

SENTINEL_PREFIX = "tile-enumeration-gate-"

# --- command-shape detection (anchored via split_top_level, not raw substring) --
# Each of these is matched against the lstripped FIRST LINE of a top-level
# segment, so a phrase appearing inside a heredoc body / quoted argument / $()
# subshell is never mistaken for a genuine invocation (the dev-env#499 class).
_MERGE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+merge\b", re.IGNORECASE)
_PR_CREATE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+create\b", re.IGNORECASE)
_PR_VIEW_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+(?:view|checks)\b", re.IGNORECASE)
_GH_API_STMT_RE = re.compile(r"gh(?:\.exe)?\s+api\b", re.IGNORECASE)

# Strip the `gh pr <verb>` prefix so the positional PR arg can be read.
_STRIP_VERB_RE = re.compile(r"\s*gh(?:\.exe)?\s+pr\s+\w+\b(.*)", re.IGNORECASE | re.DOTALL)
# A PR URL (`.../pull/N`) capturing owner/repo and number; a bare positional
# integer token; and an explicit `--repo`/`-R owner/name` flag value.
_PR_URL_RE = re.compile(r"github\.com/(?P<repo>[^/\s]+/[^/\s]+)/pull/(?P<num>\d+)")
_POS_NUM_RE = re.compile(r"(?<!\S)(\d+)(?=\s|$)")
_REPO_FLAG_RE = re.compile(r"(?:--repo|-R)(?:=|\s+)(?P<repo>[^\s/]+/[^\s]+)")

# --- dangling-created-issue detection (ADR-092, dev-env#638) -------------------
_ISSUE_CREATE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+issue\s+create\b", re.IGNORECASE)
_ISSUE_CLOSE_STMT_RE = re.compile(r"gh(?:\.exe)?\s+issue\s+close\b", re.IGNORECASE)
# `gh pr edit` can attach a Closes-keyword body to an already-created PR
# (review of PR #639) -- reuses _STRIP_VERB_RE / _target_pr below, which
# already generically strip "gh pr <any-verb>" and were written for
# merge/view/checks, since gh's syntax (`gh pr edit <number|url> ...`) is
# identical in shape.
_PR_EDIT_STMT_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+edit\b", re.IGNORECASE)
# An issue URL (`.../issues/N`) capturing owner/repo and number.
_ISSUE_URL_RE = re.compile(r"github\.com/(?P<repo>[^/\s]+/[^/\s]+)/issues/(?P<num>\d+)")
# Strip the `gh issue close` prefix so the positional issue number can be read.
_STRIP_ISSUE_CLOSE_VERB_RE = re.compile(r"\s*gh(?:\.exe)?\s+issue\s+close\b(.*)", re.IGNORECASE | re.DOTALL)
# GitHub's documented auto-close keywords (close/closes/closed, fix/fixes/fixed,
# resolve/resolves/resolved) immediately followed by an optional colon and a
# same-repo issue reference -- https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/linking-a-pull-request-to-an-issue
_CLOSES_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*#(\d+)", re.IGNORECASE,
)

# --- merged-state signals in command output ------------------------------------
_PULLS_MERGE_PATH_RE = re.compile(r"/pulls/(\d+)/merge\b")     # gh api PUT target
_MERGED_TRUE_RE = re.compile(r'"merged"\s*:\s*true')          # gh api merge result
_MERGED_STATE_RE = re.compile(r'"state"\s*:\s*"MERGED"')      # gh pr view state
_OUTPUT_NUMBER_RE = re.compile(r'"number"\s*:\s*(\d+)')       # gh pr view number

# --- enumeration / override text markers ---------------------------------------
# An actual spawn_task tool call (MCP name mcp__ccd_session__spawn_task) — the
# strongest "the checkpoint fired" signal. Bare verb so any namespacing hits.
_SPAWN_TASK_RE = re.compile(r"spawn_task", re.IGNORECASE)
# Text forms of the prescribed enumeration. A BARE "no follow-ups" matches none
# of these on purpose — that is the #700 skip the ADR-046 refinement invalidated.
_ENUM_MARKERS = (
    re.compile(r"follow[\s-]*ups?\b[^\n]{0,60}\bconsidered", re.IGNORECASE),
    re.compile(r"\bconsidered\b[^\n]{0,60}\bfollow[\s-]*ups?", re.IGNORECASE),
    re.compile(r"\bnot\s+tiled\b", re.IGNORECASE),
    re.compile(r"(?:->|→)\s*tiled\b", re.IGNORECASE),
)
# The only valid waiver — an explicit user instruction naming the tile step.
_SKIP_RE = re.compile(r"\b(?:skip\s+tiles?|don'?t\s+(?:spawn\s+)?tiles?|no\s+tiles?)\b",
                      re.IGNORECASE)


# --- transcript readers -------------------------------------------------------
# The record readers (``load_records`` / ``_parse_records`` / ``iter_bash_calls`` /
# ``_result_text`` / ``_content_items``) now live in ``_hookutil`` (ADR-090); this
# gate imports the three it uses (``_content_items``, ``_parse_records``, and the
# shared ``iter_bash_calls`` aliased as ``_iter_bash_calls``). Only ``_first_line``
# (a command-segment helper, not a transcript reader) and the 2-tuple
# ``iter_bash_calls`` adapter below stay local.

def _first_line(segment: str) -> str:
    """A segment's own first physical line — its invocation/flags only ever live
    here; everything after is heredoc/`$()` body (cf. _hookio._first_line)."""
    return segment.split("\n", 1)[0].split("\r", 1)[0]


def iter_bash_calls(records: list) -> list:
    """The gate's ``(command, output)`` view of ``_hookutil.iter_bash_calls``.

    The shared reader yields ``(command, output, cwd)``; this gate never uses
    ``cwd`` (only posttooluse-inert-advisory.py's dev-env-cwd scoping does), so
    this thin adapter drops it — keeping ``session_merged_prs`` and the existing
    tests on the historical 2-tuple contract while the transcript-pairing logic
    lives in one shared place (ADR-090).
    """
    return [(command, output) for command, output, _cwd in _iter_bash_calls(records)]


# --- pure detection helpers (offline-testable) ---------------------------------

def _target_pr(segment_first_line: str) -> int | None:
    """PR number a ``gh pr merge|view|checks`` invocation targets (a ``/pull/N``
    URL or the first bare positional integer), else ``None`` (bare form — infers
    from the current branch, no number in the command)."""
    m = _STRIP_VERB_RE.match(segment_first_line)
    tail = m.group(1) if m else ""
    u = _PR_URL_RE.search(tail)
    if u:
        return int(u.group("num"))
    n = _POS_NUM_RE.search(tail)
    return int(n.group(1)) if n else None


def _explicit_repo(segment_first_line: str) -> str | None:
    """owner/repo explicitly named on a ``gh pr …`` invocation — a ``--repo``/``-R``
    flag value or a ``/pull/N`` URL — else ``None`` (the command infers its repo
    from cwd, which this hook cannot resolve). Makes the auto-merge correlation
    repo-aware so a same-numbered PR in a *different* repo, merely inspected as
    MERGED in-session, cannot be mistaken for this session's own PR (review of
    PR #604)."""
    rf = _REPO_FLAG_RE.search(segment_first_line)
    if rf:
        return rf.group("repo")
    u = _PR_URL_RE.search(segment_first_line)
    return u.group("repo") if u else None


def session_merged_prs(calls: list) -> set:
    """PRs that reached merged state this session, by any path.

    One pass over the paired Bash calls — ``split_top_level`` is run once per
    command, not once per merge-path helper (review of PR #604). The result is
    direct merge evidence UNION an observed MERGED state correlated with a PR the
    session actually acted on (the auto-merge / pure-``gh api`` case):

    - **Direct evidence:** a real ``gh pr merge`` success marker (a manual merge
      or the two-step workaround's first command; ``--help`` / a queued
      ``--auto`` print no marker, dev-env#485), or a ``gh api .../pulls/N/merge``
      whose result is ``"merged": true``.
    - **Observed (auto-merge):** a ``gh pr view|checks`` output at ``"state":
      "MERGED"`` for a PR the session created (``gh pr create`` URL) or targeted
      (``gh pr merge`` incl. ``--auto``). The PR number is taken from the
      authoritative JSON ``"number"`` first, falling back to the positional arg.
      Correlation is by ``(repo, number)``: a ``None`` repo on either side (the
      command named no explicit repo → cwd's) falls back to a number match,
      preserving every same-repo true positive, while two *explicit* differing
      repos never match — so an unrelated same-numbered PR in another repo,
      inspected as MERGED, does not false-fire.
    """
    acted: dict = {}      # number -> set of repos (or None) the session acted on it in
    observed: list = []   # (repo_or_None, number) seen at MERGED state
    directly: set = set()  # numbers with direct in-session merge evidence

    for command, output in calls:
        output = output or ""
        firsts = [_first_line(seg).lstrip() for seg in split_top_level(command)]
        merge_firsts = [f for f in firsts if _MERGE_STMT_RE.match(f)]
        view_firsts = [f for f in firsts if _PR_VIEW_STMT_RE.match(f)]
        has_create = any(_PR_CREATE_STMT_RE.match(f) for f in firsts)
        has_api = any(_GH_API_STMT_RE.match(f) for f in firsts)

        # --- acted-on: created PRs (repo from the URL) + merge targets ----------
        if has_create:
            for m in _PR_URL_RE.finditer(output):
                acted.setdefault(int(m.group("num")), set()).add(m.group("repo"))
        for f in merge_firsts:
            n = _target_pr(f)
            if n is not None:
                acted.setdefault(n, set()).add(_explicit_repo(f))

        # --- direct merge evidence ---------------------------------------------
        if merge_firsts and output_has_merge_marker(output):
            n = merge_pr_number_from_output(output)
            if n is None:
                for f in merge_firsts:
                    n = _target_pr(f)
                    if n is not None:
                        break
            if n is not None:
                directly.add(n)
        if has_api and _MERGED_TRUE_RE.search(output):
            pm = _PULLS_MERGE_PATH_RE.search(command)
            if pm:
                directly.add(int(pm.group(1)))

        # --- observed MERGED state (auto-merge) --------------------------------
        if view_firsts and _MERGED_STATE_RE.search(output):
            nm = _OUTPUT_NUMBER_RE.search(output)  # authoritative -> prefer it
            n = int(nm.group(1)) if nm else None
            repo = None
            for f in view_firsts:
                repo = _explicit_repo(f)
                if n is None:
                    n = _target_pr(f)
                if repo is not None:
                    break
            if n is not None:
                observed.append((repo, n))

    auto: set = set()
    for repo, n in observed:
        acted_repos = acted.get(n)
        if acted_repos is None:
            continue
        if repo is None or None in acted_repos or repo in acted_repos:
            auto.add(n)
    return directly | auto


def _closed_issue_number(segment_first_line: str) -> int | None:
    """Issue number a `gh issue close` invocation targets: an `.../issues/N`
    URL first, else the first bare positional integer after the verb, else
    ``None``. `gh issue close` accepts `{<number> | <url>}` -- a session
    commonly copy-pastes the URL `gh issue create` itself just printed, and
    the bare-integer-only lookup previously missed that form entirely (the
    issue number in a URL is preceded by `/`, never whitespace, so it never
    satisfied `_POS_NUM_RE`'s `(?<!\\S)` boundary -- review of PR #639,
    confirmed independently by both reviewers). Mirrors `_target_pr`'s
    URL-first-then-positional precedence."""
    m = _STRIP_ISSUE_CLOSE_VERB_RE.match(segment_first_line)
    tail = m.group(1) if m else ""
    u = _ISSUE_URL_RE.search(tail)
    if u:
        return int(u.group("num"))
    n = _POS_NUM_RE.search(tail)
    return int(n.group(1)) if n else None


def session_created_issues(calls: list) -> dict:
    """``{issue_number: repo_or_None}`` for every `gh issue create` this
    session, keyed off the created issue's URL in the command output —
    mirrors ``session_merged_prs``'s ``acted``-dict *construction* for
    `gh pr create` (same URL-in-output extraction shape). Unlike ``acted``,
    the captured repo is not yet consumed by any correlation logic —
    ``session_resolved_issue_numbers`` matches purely on issue number (see
    its own docstring and the ADR-092 Limitations section on cross-repo
    scoping) — so treat this as a captured-but-currently-unused field, not
    evidence that resolution is already repo-aware (review of PR #639)."""
    created: dict = {}
    for command, output in calls:
        firsts = [_first_line(seg).lstrip() for seg in split_top_level(command)]
        if not any(_ISSUE_CREATE_STMT_RE.match(f) for f in firsts):
            continue
        for m in _ISSUE_URL_RE.finditer(output or ""):
            created[int(m.group("num"))] = m.group("repo")
    return created


def session_resolved_issue_numbers(calls: list, merged_prs: set) -> set:
    """Issue numbers resolved this session.

    An issue counts as resolved iff EITHER:

    - A top-level `gh pr create` OR `gh pr edit` segment's own text (including
      its heredoc body, where a `--body "$(cat <<'EOF' ... EOF)"` value
      typically lives) contains a GitHub auto-close keyword (Closes/Fixes/
      Resolves #N) for it, AND that PR reaches merged state this session (per
      ``session_merged_prs``) — GitHub only auto-closes on merge, never on
      mere PR creation/editing, so a Closes-style reference in a PR that never
      merged does not resolve the issue. `gh pr edit` covers the "create the
      PR, then attach the Closes keyword afterward" flow (review of PR #639);
      its target PR number is read the same way `session_merged_prs` already
      reads a `gh pr merge|view|checks` target, via the shared `_target_pr`.
    - An explicit `gh issue close N` ran this session.

    The keyword search is scoped to each `gh pr create` / `gh pr edit`
    segment's own text (not the whole raw command), so an unrelated
    Closes-style mention on a different chained segment can never leak in —
    mirrors ``session_merged_prs``'s per-segment scoping discipline (the A4
    hardening for cross-repo/cross-segment leakage).

    Two forms are NOT covered here, both documented as accepted limitations
    (see the ADR-092 Limitations section): a Closes-keyword living only in a
    commit message (never the `gh pr create`/`gh pr edit` command text itself)
    is invisible to a pure command-transcript scan; and an explicit cross-repo
    reference (`Closes owner/repo#N`) never matches `_CLOSES_KEYWORD_RE`
    (which requires a bare `#N` immediately after the keyword) — this is a
    false NEGATIVE (the issue is treated as still dangling, prompting an
    enumeration that turns out to be unnecessary), the safe failure direction,
    never a false match against the wrong issue. No cross-repo scoping is
    applied to the resolution correlation itself either: a coincidentally
    same-numbered issue resolved in an unrelated repo this session would be
    misread as resolving this repo's own issue. Considered low-risk in
    practice (would require working across repos AND colliding issue numbers
    in the same session).
    """
    resolved: set = set()
    for command, output in calls:
        pr_create_segments: list = []
        pr_edit_segments: list = []  # (segment, its own first-line) pairs

        for seg in split_top_level(command):
            first = _first_line(seg).lstrip()
            if _ISSUE_CLOSE_STMT_RE.match(first):
                n = _closed_issue_number(first)
                if n is not None:
                    resolved.add(n)
            elif _PR_CREATE_STMT_RE.match(first):
                pr_create_segments.append(seg)
            elif _PR_EDIT_STMT_RE.match(first):
                pr_edit_segments.append((seg, first))

        if pr_create_segments:
            pr_numbers = {int(m.group("num")) for m in _PR_URL_RE.finditer(output or "")}
            if pr_numbers & merged_prs:
                for seg in pr_create_segments:
                    resolved |= {int(n) for n in _CLOSES_KEYWORD_RE.findall(seg)}

        for seg, first in pr_edit_segments:
            n = _target_pr(first)
            if n is not None and n in merged_prs:
                resolved |= {int(m) for m in _CLOSES_KEYWORD_RE.findall(seg)}

    return resolved


def session_unresolved_created_issues(calls: list, merged_prs: set) -> set:
    """Issues created this session that remain unresolved at Stop."""
    created = session_created_issues(calls)
    if not created:
        return set()
    return set(created) - session_resolved_issue_numbers(calls, merged_prs)


def enumeration_recorded(records: list) -> bool:
    """True iff the session recorded a tile-enumeration artifact: a ``spawn_task``
    tool call, or an assistant message carrying the prescribed enumeration text.

    Session-global by design (documented ADR-088 limitation): one enumeration
    satisfies the gate for the session — the gate targets the *total skip*
    (merged, nothing recorded), not per-merge enumeration quality. A bare "no
    follow-ups" matches none of the markers and so does NOT satisfy it.
    """
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and _SPAWN_TASK_RE.search(item.get("name", "") or ""):
                return True
            if item.get("type") == "text":
                text = item.get("text", "") or ""
                if any(rx.search(text) for rx in _ENUM_MARKERS):
                    return True
    return False


def skip_override(records: list) -> bool:
    """True iff a genuine user message this session waived the checkpoint
    ("skip tiles" / "don't spawn tiles" / "no tiles"). Only real user-typed text
    is considered — never tool_result output that merely contains the phrase."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "user":
            continue
        # Synthetic user-type records — compact summaries and
        # <local-command-*> caveat blocks — are not a fresh user instruction. A
        # compact summary that merely restates an earlier "skip tiles" mention
        # must not waive the gate, especially since the workflow prompts
        # /compact right after PR-create (review of PR #604).
        if rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        c = msg.get("content")
        texts: list = []
        if isinstance(c, str):
            texts.append(c)
        elif isinstance(c, list):
            texts += [
                item.get("text", "") or ""
                for item in c
                if isinstance(item, dict) and item.get("type") == "text"
            ]
        if any(_SKIP_RE.search(t) for t in texts):
            return True
    return False


def evaluate(records: list) -> tuple:
    """Return ``(fire_pr, resolved)``.

    ``fire_pr`` — the representative merged PR number to block on, or ``None``.
    ``resolved`` — True when a merge happened but the checkpoint is satisfied
    (enumerated, or waived): the caller writes the once-per-session sentinel so
    later Stops skip the re-scan. A session with no merge yet returns
    ``(None, False)`` so a merge later in the session is still caught.
    """
    calls = iter_bash_calls(records)
    merged = session_merged_prs(calls)
    if not merged:
        return None, False
    if skip_override(records) or enumeration_recorded(records):
        return None, True
    return min(merged), False


def evaluate_issues(records: list) -> tuple:
    """Return ``(fire_issue, resolved)`` for the dangling-created-issue
    trigger (ADR-092). Mirrors ``evaluate()``'s shape/semantics exactly, as a
    fully independent sibling — zero impact on ``evaluate()`` or its existing
    callers/tests.

    ``fire_issue`` — the lowest unresolved created-issue number to block on
    (deterministic across multiple dangling issues, mirroring ``evaluate()``'s
    lowest-PR determinism), or ``None``.
    ``resolved`` — True whenever there is nothing left dangling to fire on
    *and* an issue was created this session — covers both "enumerated/waived"
    (the direct ``evaluate()`` analog) and "every issue created this session
    was already resolved outright" (e.g. created then explicitly closed, with
    no merge anywhere — a state ``evaluate()`` has no equivalent of, since a
    PR merge is binary and has no separate "resolved another way" case). Both
    set the sentinel so later Stops skip the re-scan. Without this, a
    create-then-close session with no merge would never set the sentinel and
    would re-pay the full scan on every subsequent turn (review of PR #639).
    A session that created no issue at all returns ``(None, False)`` (nothing
    to resolve) so an issue created later is still caught.

    Recomputes ``iter_bash_calls``/``session_merged_prs`` independently rather
    than sharing ``evaluate()``'s — a deliberate simplicity-over-micro-
    optimization choice (see ADR-092's "Alternatives considered" section):
    keeping the two evaluators fully independent means neither's contract
    depends on how the other is invoked, at the cost of a second linear pass
    over an already-cheap transcript scan.
    """
    calls = iter_bash_calls(records)
    merged = session_merged_prs(calls)
    created = session_created_issues(calls)
    if not created:
        return None, False
    unresolved = set(created) - session_resolved_issue_numbers(calls, merged)
    if not unresolved:
        return None, True
    if skip_override(records) or enumeration_recorded(records):
        return None, True
    return min(unresolved), False


def format_reminder(pr: int) -> str:
    """The exit-2 stderr message. ASCII-only: Claude Code pipes hook output as
    cp1252 on Windows, so a char outside it (an arrow, em-dash) would raise
    UnicodeEncodeError and the whole reminder would vanish — use ``->`` not the
    Unicode arrow (mirrors posttooluse-inert-advisory.py's ASCII constraint)."""
    return (
        f"[tile-enumeration-gate] PR #{pr} reached merged state this session, but no "
        "post-merge tile enumeration was recorded. Per ADR-046, before ending the turn "
        "write out the follow-ups you considered -- scan for out-of-scope fixes, deferred "
        "work, tech debt, and ideas noticed in passing, and record each as "
        "'-> tiled (task_id / #N)' or '-> not tiled, because <reason>', spawning a "
        "spawn_task tile for each genuine follow-up. \"No follow-ups\" is valid only as "
        "the visible result of that scan, never a bare assertion. Only an explicit "
        "\"skip tiles\" instruction anywhere in this session exempts this checkpoint."
    )


def format_issue_reminder(issue: int) -> str:
    """The exit-2 stderr message for the dangling-created-issue trigger
    (ADR-092). ASCII-only, same constraint as ``format_reminder`` (Claude Code
    pipes hook output as cp1252 on Windows)."""
    return (
        f"[tile-enumeration-gate] Issue #{issue} was created this session and is still "
        "open (no same-session merged PR closed it via a Closes/Fixes/Resolves keyword, "
        "and it was not explicitly closed), but no tile enumeration was recorded. Per "
        "ADR-046/ADR-092, before ending the turn write out the follow-ups you considered "
        "-- spawn a spawn_task tile to pick up this issue, or record '-> not tiled, "
        "because <reason>'. \"No follow-ups\" is valid only as the visible result of "
        "that scan, never a bare assertion. Only an explicit \"skip tiles\" instruction "
        "anywhere in this session exempts this checkpoint."
    )


# --- I/O (thin, untested per the pure-helper convention) -----------------------

def _mark_fired(session_id: str) -> None:
    if not session_id:
        return
    try:
        _hookutil.SCRATCH.mkdir(exist_ok=True)
        _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).write_text("")
    except Exception:
        pass


def main() -> None:
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    # Loop guard: once this hook has already blocked and Claude is continuing,
    # never block again (Claude Code hooks reference — check stop_hook_active).
    if data.get("stop_hook_active"):
        sys.exit(0)

    session_id = data.get("session_id") or ""
    # Fire at most once per session — the sentinel short-circuits later Stops.
    if session_id and _hookutil.sentinel_path(SENTINEL_PREFIX, session_id).exists():
        sys.exit(0)

    tpath_str = data.get("transcript_path") or ""
    tpath = Path(tpath_str) if tpath_str else None
    if (tpath is None or not tpath.exists()) and session_id:
        tpath = _hookutil.find_transcript(session_id)
    if tpath is None or not tpath.exists():
        sys.exit(0)

    try:
        text = tpath.read_text(encoding="utf-8")
    except Exception:
        sys.exit(0)
    # Cheap pre-filter: every merge signal this hook detects contains the
    # substring "merged" case-insensitively — gh's "... merged pull request",
    # `"state":"MERGED"`, `"merged":true`. Every dangling-issue signal requires
    # a `gh issue create` to have run this session (ADR-092) — reuse the exact
    # detection regex (`.search()`, not the per-segment `.match()` the real
    # detector uses) rather than a hand-written substring, so the guard can
    # never drift from what the detector actually matches (a literal
    # single-space `"issue create"` substring check would silently miss a
    # tab/multi-space invocation the real `\s+`-based regex still matches --
    # review of PR #639, confirmed independently by both reviewers). The
    # resolution side (Closes-keyword / `gh issue close`) only ever matters
    # when there is a created issue to resolve in the first place, so gating
    # on creation alone is still sufficient for both trigger halves. A
    # transcript with NEITHER "merged" NOR a genuine `gh issue create`
    # invocation cannot contain a merged-state PR NOR a dangling created
    # issue, so skip the full JSON parse + scan. Stop fires every turn, so
    # this bounds the common no-op session to one read + substring/regex
    # check instead of re-parsing the whole transcript each turn (review of
    # PR #604, extended ADR-092).
    lower = text.lower()
    if "merged" not in lower and not _ISSUE_CREATE_STMT_RE.search(text):
        sys.exit(0)

    # Fail-open: a parse/scan failure is a deliberate exit-0 skip, not an
    # accident of the outer __main__ guard (review of PR #604).
    try:
        records = _parse_records(text)
        fire_pr, resolved_pr = evaluate(records)
        fire_issue, resolved_issue = evaluate_issues(records)
    except Exception:
        sys.exit(0)
    if fire_pr is not None or fire_issue is not None:
        # Set the sentinel BEFORE emitting so a re-entrant Stop cannot double-block.
        _mark_fired(session_id)
        messages = []
        if fire_pr is not None:
            messages.append(format_reminder(fire_pr))
        if fire_issue is not None:
            messages.append(format_issue_reminder(fire_issue))
        sys.stderr.write("\n\n".join(messages) + "\n")
        sys.exit(2)
    if resolved_pr or resolved_issue:
        # Merge and/or issue-creation happened and the checkpoint is satisfied
        # for whatever fired — resolve so later Stops skip the re-scan
        # (mirrors posttooluse-inert-advisory.py).
        _mark_fired(session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
