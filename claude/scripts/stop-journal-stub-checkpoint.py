#!/usr/bin/env python3
"""Claude Code Stop hook — journal-stub checkpoint for report/analysis sessions (ADR-100).

The global CLAUDE.md "Report / analysis generated" journal trigger (ADR-062)
makes a report, investigation write-up, verification / deploy-check, audit,
comparison, or findings summary its own journal boundary — a stub is required
even with no PR. Unlike the PR-open and PR-merge triggers, which fire mechanical
reminders (``pr-merge-reminder.py`` on ``gh pr create`` / ``gh pr merge``, the
``git push`` reminder, ``journal-stop-check.py``'s archive reminder), that
trigger was prose-only: nothing fired, so a productive non-merge session could
silently skip the stub (motivating incident: a lifting-logbook session verifying
PR #770's production fix that wrote no stub until the user asked — dev-env#702).

This hook is the Stop-time mechanical nudge for that class. At every Stop it
scans the just-ended transcript and BLOCKS the stop (exit 2, reminder on stderr)
when ALL of the following hold:

  1. **Report intent** — a genuine user prompt carries a report/analysis or
     verify/deploy keyword (``report_intent``). Scoped to real user-typed text
     (``type == "user"``, skipping ``isMeta`` / ``isCompactSummary`` synthetic
     records and slash-command wrappers), so a keyword in tool output, assistant
     text, or command machinery never counts — the primary false-positive guard.
  2. **Substantive work** — the session made at least ``SUBSTANTIVE_THRESHOLD``
     substantive tool calls (``substantive_tool_count``: Read/Grep/Glob/Bash/
     Edit/Write/… — report sessions are read-dominated, so reads count), so a
     trivial keyworded lookup does not fire.
  3. **No PR opened or merged** (``opened_or_merged_pr``) — those already nudge
     via ``pr-merge-reminder.py`` and the tile gate; suppress the double-nudge.
  4. **No journal stub written** (``wrote_stub``) — a Write/Edit to a
     ``*.stub.md`` path, or a Bash command referencing one (a pre-written stub
     staged with ``git add``/``commit``).
  5. **Not a ``/review`` session** (``is_review_only_session``) — review-only
     sessions are exempt (their findings live on the PR — global CLAUDE.md).
  6. **No skip override** (``skip_override``) — a genuine user "skip journal" /
     "no stub" instruction waives the checkpoint up front.

Delivery is **exit 2 + stderr, blocking-once**: per ADR-091 a Stop hook's exit-0
stdout is written to the debug log, NOT added to Claude's context, so exit-2 +
stderr is the only Stop delivery that reaches Claude — the same mechanism the
PR-event reminders and the tile gate already use. It is advisory in spirit, not
a hard gate: a once-per-session scratch sentinel means it fires at most once, and
the reminder tells Claude how to dismiss a false positive in one line. Stop fires
reliably in background / SDK-launched sessions (ADR-053/055), unlike PostToolUse,
so this is the right event for coverage of the population that most needs it. A
Stop hook fires at every turn-end (not only at session end), so the nudge lands
at the FIRST turn where the condition holds and — being once-per-session — is not
re-delivered later; a multi-turn report session receives it early rather than at
true session end (accepted; see ADR-100 Limitations).

Detection is a pure transcript scan — no ``gh`` calls, no network, no subprocess.
Fail-open: any error (empty/malformed stdin, missing transcript, parse/scan
failure, the outer ``__main__`` guard) exits 0. Honors the ``stop_hook_active``
loop-guard flag (Claude Code hooks reference: a Stop hook must check it and exit
0 early once continuing, or it can block forever). A single per-session sentinel
suffices — this hook has one trigger (unlike the tile gate's three, ADR-097).

The transcript-record readers (``_parse_records`` / ``_content_items``) and the
sentinel / transcript-locate helpers come from the shared ``_hookutil`` module
(ADR-090 / ADR-064); the anchored command scanner (``scan_top_level``) and the
``--help``-only filter (``is_help_only``) come from ``_hookio`` (ADR-050) — the
same shared modules ``stop-tile-enumeration-gate.py`` builds on.

Stdin JSON shape (Stop):
  {"session_id": "...", "transcript_path": "/abs/path.jsonl",
   "stop_hook_active": false, "cwd": "...", ...}

Exit 0 — no report intent, not enough substantive work yet, a PR/stub already
         covers the session, ``/review`` or a "skip journal" override, this
         hook already fired/resolved this session (sentinel), stop_hook_active
         set, or any error (fail-open).
Exit 2 — a report/analysis/verification session is ending with no stub;
         blocking reminder on stderr.
"""
from __future__ import annotations

import _hookutil
import json
import re
import sys
from pathlib import Path

from _hookio import is_help_only, scan_top_level
from _hookutil import _content_items, _is_synthetic_user, _parse_records, _user_message_texts

SENTINEL_PREFIX = "journal-stub-checkpoint-"

# Fire only when the session did at least this many substantive tool calls, so a
# trivial keyworded lookup ("verify my understanding of this function") does not
# block. Tunable. Report/analysis/verification sessions are read-dominated, so
# the count deliberately includes read-only tools (see _SUBSTANTIVE_TOOLS).
SUBSTANTIVE_THRESHOLD = 5

# --- report / analysis / verification intent (user-prompt keywords) ------------
# Two tunable groups. Word-boundaried so a keyword is a whole word, not a
# substring of an unrelated one. `review` is deliberately absent (a /review
# session is exempt — see is_review_only_session). Bare `production` and bare
# `deploy` are absent (too broad — bare `deploy` matches "the deploy broke", a
# plain dev task): deploy/production intent is instead carried by `verif*`,
# `production <fix|...>`, `deploy(ment) verif/check`, and `check the deploy`.
# `analy...` matches analysis/analyze/analyse but NOT analytics/analytical (a
# product feature, not a report request). Both narrowings are review-of-PR-#706
# false-positive fixes; a residual incidental-keyword class remains and is
# documented in ADR-100 (the nudge is advisory and once-per-session).
_REPORT_ANALYSIS_RE = re.compile(
    r"\b(?:reports?|reporting|reported|"
    r"analy(?:s[ei]s|z(?:e|es|ed|ing)|s(?:e|es|ed|ing))|"
    r"investigat\w*|audit\w*|compar\w*|findings|write-?ups?|summar\w*)\b",
    re.IGNORECASE,
)
_VERIFY_DEPLOY_RE = re.compile(
    r"\b(?:verif\w*|"
    r"production\s+(?:fix|deploy\w*|verif\w*|issue|bug|incident|regression|hotfix)|"
    r"deploy(?:ment)?\s+(?:verif\w*|check\w*)|"
    r"check(?:ed|ing|s)?\s+the\s+deploy\w*)\b",
    re.IGNORECASE,
)
_INTENT_RES = (_REPORT_ANALYSIS_RE, _VERIFY_DEPLOY_RE)

# A cheap, GUARANTEED SUPERSET of _INTENT_RES for the main() pre-filter: the
# atomic word-stems every _INTENT_RES branch contains, matched against the raw
# transcript text before parsing. The `\s+`-bearing compound branches (e.g.
# `production\s+fix`, `check\s+the\s+deploy`) are NOT a sound superset of the
# parsed detector — a user prompt's inter-word whitespace can be JSON-escaped
# (`\t`, `\n`) in the raw transcript, so `\s+` fails there while the decoded
# detector matches. The atomic stems ARE sound (each raw keyword's own
# characters are never split by JSON escaping), so the pre-filter can never
# fast-exit a fire-worthy session; it only skips the parse when no stem appears
# at all (review of PR #706). Selectivity is deliberately loose — these stems
# are common in ordinary prose / tool output, so a genuine skip-the-parse is the
# exception, not the rule (see ADR-100 Consequences).
_PREFILTER_RE = re.compile(
    r"report|analy|investigat|audit|compar|findings|write-?up|summar|"
    r"verif|deploy|production",
    re.IGNORECASE,
)

# --- command-shape detection (anchored via scan_top_level, not raw substring) --
# Matched against the lstripped start of each top-level segment, so a phrase
# inside a heredoc body / quoted argument / $() subshell is never mistaken for a
# genuine invocation (the dev-env#499 class). Built on _hookio.scan_top_level to
# stay clear of the item-39 crude-substring AST gate.
_PR_CREATE_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+create\b", re.IGNORECASE)
_PR_MERGE_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+merge\b", re.IGNORECASE)

# A slash-command wrapper — Claude Code emits "<command-name>/review</command-name>
# ..." as a genuine user record with string content. Its keywords are command
# machinery, not a typed request, so report_intent skips a text starting with it.
_COMMAND_WRAPPER_PREFIX = "<command-name>"
# A /review command invocation exempts the session (its findings live on the PR).
# Keyed on the wrapper so prose merely mentioning "/review" never trips it. The
# `(?!-)` after the `\b` stops a future `/review-<suffix>` command (e.g.
# `/review-followups`) from being mistaken for `/review` (review of PR #706).
_REVIEW_CMD_RE = re.compile(r"<command-name>\s*/?review\b(?!-)", re.IGNORECASE)

# The only valid waiver — an explicit user instruction to skip the journal/stub.
_SKIP_RE = re.compile(
    r"\b(?:skip(?:\s+the)?\s+journal|skip(?:\s+the)?\s+stub|"
    r"no\s+(?:journal\s+)?stub|no\s+journal\b|"
    r"don'?t\s+(?:bother\s+)?(?:journal|stub))",
    re.IGNORECASE,
)

# A journal stub path in a Write/Edit file_path. A regex .search (never
# `"..." in command`) keeps this hook clear of the item-39 crude-substring gate.
_STUB_PATH_RE = re.compile(r"\.stub\.md\b", re.IGNORECASE)
# A *write* of a stub via Bash — `git add`/`git commit` of, a redirect into, or
# an `mv`/`cp`/`tee` to a `*.stub.md`. Deliberately NOT a bare mention: a read
# (`ls sessions/…/*.stub.md | sort | tail -1` — the journal workflow's own
# "find the latest stub" step — or `cat …stub.md`) must not be mistaken for
# having written one (review of PR #706).
_STUB_BASH_WRITE_RE = re.compile(
    r"git\s+(?:add|commit)\b[^\n]*\.stub\.md"
    r"|>>?\s*\S*\.stub\.md"
    r"|\b(?:mv|cp|tee)\b[^\n]*\.stub\.md",
    re.IGNORECASE,
)

# Substantive, hands-on tools. Reads count (report sessions are read-dominated).
# Two families are deliberately excluded (an allowlist -- anything not named
# below is already excluded by omission, so this frozenset's membership itself
# never needs to change; only this comment's naming did -- dev-env#1020):
#   - TaskCreate/TaskUpdate (bookkeeping) -- this harness's real task-list tool.
#     It has NO TodoWrite tool at all: 0 occurrences across every transcript on
#     the machine vs. thousands of TaskCreate/TaskUpdate calls, confirmed live
#     the same way dev-env#1002 confirmed it for journal-stop-check.py's sibling
#     counter (ADR-091 Amendment 3). Re-derived, not a find/replace: a session
#     can TaskCreate/TaskUpdate a whole plan (each call is one task, unlike
#     TodoWrite's single whole-list-replace, so the volume can be large) without
#     touching any real content, so counting them would let planning overhead
#     alone cross SUBSTANTIVE_THRESHOLD -- exactly the "trivial lookup" false
#     positive this threshold exists to prevent. They stay excluded.
#   - Agent/spawn_task/mcp__* (delegation) -- bare "Task" never appears either
#     (confirmed live: 0 occurrences); the real subagent-spawning tool name is
#     "Agent". Excluded so a single delegation can't inflate the count past the
#     threshold on its own.
_SUBSTANTIVE_TOOLS = frozenset({
    "Bash", "Read", "Grep", "Glob", "Edit", "Write",
    "NotebookEdit", "WebFetch", "WebSearch",
})


# --- transcript helpers --------------------------------------------------------
#
# ``_user_message_texts`` / ``_is_synthetic_user`` were promoted to ``_hookutil``
# (dev-env#710) and are imported above, shared with
# ``stop-tile-enumeration-gate.py``'s ``skip_override``. ``_bash_commands`` below
# stays local — its unpaired walk is specific to this hook (see its docstring).


def _bash_commands(records: list) -> list:
    """Every Bash tool_use command string in the transcript.

    A thin UNPAIRED walk (unlike ``_hookutil.iter_bash_calls``, which pairs each
    Bash call with its tool_result output): this hook's detection needs only the
    command text — PR-create/merge presence and stub-file references — never the
    command's output, and an unpaired walk also catches a trailing Bash call
    whose result isn't in the transcript yet at Stop time.
    """
    commands: list = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "Bash"
            ):
                commands.append((item.get("input") or {}).get("command", "") or "")
    return commands


# --- pure detection helpers (offline-testable) ---------------------------------

def report_intent(records: list) -> bool:
    """True iff a genuine user prompt this session carries a report/analysis or
    verify/deploy keyword. Synthetic records and slash-command wrappers are
    skipped, so only real typed intent counts — the primary false-positive
    discriminator against a keyword appearing in tool output or assistant text."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "user" or _is_synthetic_user(rec):
            continue
        for text in _user_message_texts(rec):
            if text.lstrip().startswith(_COMMAND_WRAPPER_PREFIX):
                continue
            if any(rx.search(text) for rx in _INTENT_RES):
                return True
    return False


def substantive_tool_count(records: list) -> int:
    """Count of assistant ``tool_use`` items whose ``name`` is a substantive
    tool (see ``_SUBSTANTIVE_TOOLS``). Parallel tool_use items in one record
    each count individually."""
    n = 0
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") in _SUBSTANTIVE_TOOLS
            ):
                n += 1
    return n


def _check_pr_create(seg: str) -> bool:
    return bool(_PR_CREATE_RE.match(seg.lstrip()))


def _check_pr_merge(seg: str) -> bool:
    return bool(_PR_MERGE_RE.match(seg.lstrip()))


def opened_or_merged_pr(records: list) -> bool:
    """True iff the session ran a real top-level ``gh pr create`` or ``gh pr
    merge`` (those events already nudge for a stub). Command-shape only — a
    ``--help``-only invocation is dropped via ``is_help_only`` so a
    ``gh pr create --help`` doesn't falsely suppress the checkpoint."""
    for cmd in _bash_commands(records):
        if scan_top_level(cmd, _check_pr_create) and not is_help_only(cmd, _PR_CREATE_RE):
            return True
        if scan_top_level(cmd, _check_pr_merge) and not is_help_only(cmd, _PR_MERGE_RE):
            return True
    return False


def wrote_stub(records: list) -> bool:
    """True iff the session wrote a journal stub — a Write/Edit/NotebookEdit whose
    ``file_path`` is a ``*.stub.md``, or a Bash command that *writes* one
    (``git add``/``git commit``, a redirect, or ``mv``/``cp``/``tee`` — NOT a
    bare read like ``ls``/``cat``, which the journal workflow itself runs to find
    the latest stub; review of PR #706). Errs toward suppression (the safe
    advisory direction: a false "stub written" only silences the nudge)."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            name = item.get("name")
            inp = item.get("input") or {}
            if name in ("Write", "Edit", "NotebookEdit"):
                if _STUB_PATH_RE.search(inp.get("file_path", "") or ""):
                    return True
            elif name == "Bash":
                if _STUB_BASH_WRITE_RE.search(inp.get("command", "") or ""):
                    return True
    return False


def is_review_only_session(records: list) -> bool:
    """True iff a ``/review`` command was invoked this session. Review-only
    sessions are exempt from the report-analysis journal trigger (their findings
    live on the PR, not a free-standing report — global CLAUDE.md). Keyed on the
    ``<command-name>`` wrapper a real invocation emits, so prose merely mentioning
    ``/review`` never trips it."""
    for rec in records:
        if any(_REVIEW_CMD_RE.search(t) for t in _user_message_texts(rec)):
            return True
    return False


def skip_override(records: list) -> bool:
    """True iff a genuine user message this session waived the checkpoint ("skip
    journal" / "no stub" / …). Only real user-typed text is considered — never a
    tool_result or a compact summary that merely contains the phrase."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "user" or _is_synthetic_user(rec):
            continue
        if any(_SKIP_RE.search(t) for t in _user_message_texts(rec)):
            return True
    return False


def evaluate(records: list) -> tuple:
    """Return ``(fire, resolved)``.

    ``fire`` — True when this looks like an unrecorded report/analysis/verification
    boundary (intent present, enough work, no PR, no stub, not /review, not
    waived): the caller writes the sentinel and blocks (exit 2).
    ``resolved`` — True when a report intent was present but the checkpoint is
    already satisfied (a stub was written, a PR carries it, /review, or the user
    waived): the caller writes the sentinel so later Stops skip the re-scan.
    ``(False, False)`` — not applicable / not yet: no report intent, or intent
    but the work threshold isn't met — no sentinel, re-evaluate on the next Stop
    (intent may still arise, or more work may cross the threshold later).
    """
    if not report_intent(records):
        return False, False
    if is_review_only_session(records):
        return False, True
    if skip_override(records):
        return False, True
    if wrote_stub(records):
        return False, True
    if opened_or_merged_pr(records):
        return False, True
    if substantive_tool_count(records) < SUBSTANTIVE_THRESHOLD:
        return False, False
    return True, False


def format_reminder(cwd: str = "") -> str:
    """The exit-2 stderr message. ASCII-only: Claude Code pipes hook output as
    cp1252 on Windows, so a char outside it (an arrow, an em-dash) would raise
    UnicodeEncodeError and the whole reminder would vanish — use ``--`` and
    ``->`` (mirrors stop-tile-enumeration-gate.py / journal-stop-check.py)."""
    # The static text below is ASCII, but *cwd* is a caller-supplied path that
    # could contain a non-cp1252 character (a Unicode-named project folder). An
    # unsanitized non-cp1252 cwd would raise UnicodeEncodeError at the stderr
    # write in main() — caught by the outer guard, but only AFTER the sentinel is
    # written, silently self-disabling the hook for the whole session (review of
    # PR #706). Coerce to ASCII so the entire message is cp1252-safe by construction.
    if cwd:
        cwd = cwd.encode("ascii", "replace").decode("ascii")
    where = f"\n  cwd: {cwd}" if cwd else ""
    return (
        "[journal-stub-checkpoint] This session requested a report, analysis, or "
        "verification and did substantive work, but is ending with no "
        "engineering-journal stub." + where + "\n"
        "Per the global CLAUDE.md \"Report / analysis generated\" trigger, a report / "
        "investigation / verification is itself a journal boundary -- no PR required. "
        "Before ending the turn:\n"
        "  1. Identify the project journal path from cwd (sessions/<project>/ under "
        "C:/Users/brown/Git/engineering-journal).\n"
        "  2. Check out or create today's draft/YYYY-MM-DD branch there.\n"
        "  3. Write sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md capturing what was "
        "investigated/verified and the outcome. Inline a short analysis; put anything "
        "longer in sessions/<project>/reports/YYYY-MM-DD-<slug>.md and link it from the "
        "stub.\n"
        "  4. Write this session's manifest shard, then git add + commit + push per the "
        "Engineering Journal workflow.\n"
        "If this session produced nothing worth journaling (a quick lookup, exploratory "
        "Q&A, or work that will land later in a PR that carries its own stub), reply that "
        "no stub is needed and continue -- this is an advisory checkpoint, not a hard "
        "gate. An explicit \"skip journal\" instruction suppresses it up front."
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
    _hookutil.record_heartbeat("stop-journal-stub-checkpoint")
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
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

    # Cheap pre-filter: no report/analysis/verify keyword STEM anywhere in the
    # raw transcript text -> no user prompt can carry the intent -> skip the
    # parse. _PREFILTER_RE is a guaranteed superset of the parsed detector (see
    # its definition): using the atomic stems rather than the `\s+`-bearing
    # _INTENT_RES regexes avoids the JSON-escaped-whitespace gap that would
    # otherwise let a compound keyword ("production\tfix") fast-exit a fire-worthy
    # session (review of PR #706).
    if not _PREFILTER_RE.search(text):
        sys.exit(0)

    # Fail-open: a parse/scan failure is a deliberate exit-0 skip, not an
    # accident of the outer __main__ guard.
    try:
        records = _parse_records(text)
        fire, resolved = evaluate(records)
    except Exception:
        sys.exit(0)

    if fire:
        # Set the sentinel BEFORE emitting so a re-entrant Stop cannot double-block.
        _mark_fired(session_id)
        sys.stderr.write(format_reminder(data.get("cwd", "") or "") + "\n")
        sys.exit(2)
    if resolved:
        _mark_fired(session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
