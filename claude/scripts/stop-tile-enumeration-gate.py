#!/usr/bin/env python3
"""Claude Code Stop hook — state-keyed post-merge tile-enumeration gate (ADR-088).

The command-keyed ``post-merge-tile-checkpoint.py`` (ADR-060) fires on a
``gh pr merge`` *you run*, but is BLIND to a PR that reaches merged state some
other way: auto-merge landing it server-side while you were away, or a pure
``gh api`` merge. Those are exactly the cases where the tile checkpoint's
salience is lowest (post-merge is pure bookkeeping) — the lifting-logbook
PR #700 incident that motivated the ADR-046 2026-07-05 forcing-function
refinement (dev-env#595).

This hook is STATE-keyed instead: at every Stop it scans the just-ended session
transcript and, when a PR reached MERGED state this session (by any path) but
the session recorded NO tile-enumeration artifact, it BLOCKS the stop (exit 2)
with the reminder — the direct Stop-hook analog of ``pre-merge-findings-gate``
(ADR-039). A recorded enumeration is either an actual ``spawn_task`` tile, or
the prescribed text ("Follow-ups considered: ... -> tiled (task_id / #N) /
-> not tiled, because <reason>"). A bare "no follow-ups" assertion does NOT
satisfy the gate: per the ADR-046 refinement, "No follow-ups" is valid only as
the visible result of an enumeration, never as a bare assertion (the #700 skip).

Complements — does not replace — the command-keyed hook: that one is the
immediate in-the-moment nudge when ``gh pr merge`` runs; this one is the
Stop-time verification that the enumeration actually happened, covering every
merge path. It is also NOT inert in background / SDK-launched sessions
(ADR-053), where every PostToolUse hook — including the command-keyed sibling —
silently never fires; the Stop event still dispatches, so this is the only tile
enforcement that survives there.

Detection is a pure transcript scan — no ``gh`` calls, no network, no
subprocess (so no ``_winsubp``). Fail-open: any error exits 0. Fires at most
once per session via a scratch sentinel (mirrors ``posttooluse-inert-advisory.py``),
and honors the ``stop_hook_active`` loop-guard flag (Claude Code hooks
reference: a Stop hook must check it and exit 0 early once continuing, or it can
block forever).

The three transcript readers (``load_records`` / ``iter_bash_calls`` /
``_result_text``) are deliberately replicated here rather than shared: the same
functions live in ``posttooluse-inert-advisory.py``. This mirrors the repo's
established tolerance for small-helper replication when sharing would over-couple
two otherwise-independent hooks (cf. ``_first_line`` intentionally duplicated
across ``_hookio.py`` and ``pre-tool-use-canonical-mutate-guard.py``); the truly
shared bits (sentinels / transcript-locate, the merge-marker / segment parser)
are imported from ``_hookutil`` and ``_hookio``.

Stdin JSON shape (Stop):
  {"session_id": "...", "transcript_path": "/abs/path.jsonl",
   "stop_hook_active": false, ...}

Exit 0 — no merged-state PR this session, enumeration already recorded, a
         "skip tiles" override present, already fired (sentinel), stop_hook_active
         set, or any error (fail-open).
Exit 2 — a PR merged this session with no recorded tile-enumeration; blocking
         reminder emitted on stderr.
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
# A PR URL (`.../pull/N`) and a bare positional integer token.
_PR_URL_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")
_POS_NUM_RE = re.compile(r"(?<!\S)(\d+)(?=\s|$)")

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


# --- transcript readers (deliberately replicated — see module docstring) -------

def _first_line(segment: str) -> str:
    """A segment's own first physical line — its invocation/flags only ever live
    here; everything after is heredoc/`$()` body (cf. _hookio._first_line)."""
    return segment.split("\n", 1)[0].split("\r", 1)[0]


def _content_items(rec: dict) -> list:
    msg = rec.get("message") or {}
    c = msg.get("content")
    return c if isinstance(c, list) else []


def _result_text(item: dict, record: dict) -> str:
    """Best-available text of a tool_result: the per-id content the model saw,
    falling back to the record's structured ``toolUseResult`` (stdout+stderr)."""
    c = item.get("content")
    if isinstance(c, str) and c.strip():
        return c
    if isinstance(c, list):
        joined = "\n".join(
            x.get("text", "")
            for x in c
            if isinstance(x, dict) and x.get("type") == "text"
        )
        if joined.strip():
            return joined
    tur = record.get("toolUseResult")
    if isinstance(tur, dict):
        parts = [p for p in (tur.get("stdout"), tur.get("stderr")) if p]
        if parts:
            return "\n".join(parts)
        out = tur.get("output")
        if out:
            return str(out)
    return ""


def iter_bash_calls(records: list) -> list:
    """Pair each Bash tool_use with its tool_result by ``tool_use_id``.

    Returns (command, output) tuples. Pairing by id (not adjacency) keeps
    parallel tool calls from mismatching.
    """
    commands: dict = {}
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "Bash"
            ):
                tid = item.get("id")
                if tid:
                    commands[tid] = (item.get("input") or {}).get("command", "")

    calls: list = []
    for rec in records:
        if rec.get("type") != "user":
            continue
        for item in _content_items(rec):
            if isinstance(item, dict) and item.get("type") == "tool_result":
                tid = item.get("tool_use_id")
                if tid in commands:
                    calls.append((commands[tid], _result_text(item, rec)))
    return calls


def load_records(transcript_path: Path) -> list:
    records: list = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# --- pure detection helpers (offline-testable) ---------------------------------

def _segments_matching(command: str, stmt_re: re.Pattern) -> list:
    """Top-level segments of *command* whose first line starts with *stmt_re*."""
    return [
        seg for seg in split_top_level(command)
        if stmt_re.match(_first_line(seg).lstrip())
    ]


def _target_pr(segment_first_line: str) -> int | None:
    """PR number a ``gh pr merge|view|checks`` invocation targets (a ``/pull/N``
    URL or the first bare positional integer), else ``None`` (bare form — infers
    from the current branch, no number in the command)."""
    m = _STRIP_VERB_RE.match(segment_first_line)
    tail = m.group(1) if m else ""
    u = _PR_URL_RE.search(tail)
    if u:
        return int(u.group(1))
    n = _POS_NUM_RE.search(tail)
    return int(n.group(1)) if n else None


def acted_on_prs(calls: list) -> set:
    """PR numbers this session created or targeted with a merge (incl. a queued
    ``--auto``). Used to correlate an observed MERGED state (below) with an
    in-session action, so a mere lookup of an unrelated old merged PR can't be
    mistaken for a merge that happened this session."""
    prs: set = set()
    for command, output in calls:
        if _segments_matching(command, _PR_CREATE_STMT_RE):
            for m in _PR_URL_RE.finditer(output or ""):
                prs.add(int(m.group(1)))
        for seg in _segments_matching(command, _MERGE_STMT_RE):
            n = _target_pr(_first_line(seg))
            if n is not None:
                prs.add(n)
    return prs


def directly_merged_prs(calls: list) -> set:
    """PRs with direct in-session merge evidence: a real ``gh pr merge`` success
    marker (covers a manual merge and the two-step workaround's first command),
    or a ``gh api .../pulls/N/merge`` whose result is ``"merged": true``.

    ``gh pr merge --help`` and a queued ``gh pr merge --auto`` produce no success
    marker, so neither counts here (dev-env#485 shape)."""
    prs: set = set()
    for command, output in calls:
        output = output or ""
        if _segments_matching(command, _MERGE_STMT_RE) and output_has_merge_marker(output):
            n = merge_pr_number_from_output(output)
            if n is None:
                for seg in _segments_matching(command, _MERGE_STMT_RE):
                    n = _target_pr(_first_line(seg))
                    if n is not None:
                        break
            if n is not None:
                prs.add(n)
        if _segments_matching(command, _GH_API_STMT_RE) and _MERGED_TRUE_RE.search(output):
            pm = _PULLS_MERGE_PATH_RE.search(command)
            if pm:
                prs.add(int(pm.group(1)))
    return prs


def observed_merged_prs(calls: list) -> set:
    """PRs observed at MERGED state via a ``gh pr view|checks`` JSON output. This
    is how auto-merge is caught: the session enqueued ``--auto`` (or created the
    PR), came back, and confirmed the merged state with ``gh pr view``."""
    prs: set = set()
    for command, output in calls:
        output = output or ""
        if _segments_matching(command, _PR_VIEW_STMT_RE) and _MERGED_STATE_RE.search(output):
            n = None
            for seg in _segments_matching(command, _PR_VIEW_STMT_RE):
                n = _target_pr(_first_line(seg))
                if n is not None:
                    break
            if n is None:
                nm = _OUTPUT_NUMBER_RE.search(output)
                n = int(nm.group(1)) if nm else None
            if n is not None:
                prs.add(n)
    return prs


def session_merged_prs(calls: list) -> set:
    """PRs that reached merged state this session, by any path.

    Direct merge evidence, UNION an observed MERGED state correlated with a PR
    the session actually acted on (the auto-merge / pure-``gh api`` case).
    """
    return directly_merged_prs(calls) | (observed_merged_prs(calls) & acted_on_prs(calls))


def enumeration_recorded(records: list) -> bool:
    """True iff the session recorded a tile-enumeration artifact: a ``spawn_task``
    tool call, or an assistant message carrying the prescribed enumeration text.

    Session-global by design (documented ADR-088 limitation): one enumeration
    satisfies the gate for the session — the gate targets the *total skip*
    (merged, nothing recorded), not per-merge enumeration quality. A bare "no
    follow-ups" matches none of the markers and so does NOT satisfy it.
    """
    for rec in records:
        if rec.get("type") != "assistant":
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
        if rec.get("type") != "user":
            continue
        msg = rec.get("message") or {}
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
        records = load_records(tpath)
    except Exception:
        sys.exit(0)

    fire_pr, resolved = evaluate(records)
    if fire_pr is not None:
        # Set the sentinel BEFORE emitting so a re-entrant Stop cannot double-block.
        _mark_fired(session_id)
        sys.stderr.write(format_reminder(fire_pr) + "\n")
        sys.exit(2)
    if resolved:
        # Merge happened and the checkpoint is satisfied — resolve so later Stops
        # skip the re-scan (mirrors posttooluse-inert-advisory.py).
        _mark_fired(session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
