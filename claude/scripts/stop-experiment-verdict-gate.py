#!/usr/bin/env python3
"""Claude Code Stop hook -- experiment-verdict gate (ADR-115).

The global CLAUDE.md ``## Experimental Rigor`` protocol makes any comparative
claim about a process change (an A/B spike, a challenger-vs-incumbent test, a
before/after) an experiment that must be pre-registered before results exist and
verdict-gated before a conclusion is stated -- via the ``/experiment-audit``
skill (``design`` mode, then ``verdict`` mode). Pass-3 dimension 7 enforces the
DESIGN half at plan time. This hook is the Stop-time backstop for the VERDICT
half: the moment a session *states* an experiment conclusion, it should have run
``/experiment-audit verdict`` -- and nothing else fires when it hasn't.

The motivating incident (career-playbook #806/#809/#811, 2026-07-21): an A/B
spike of "generate-then-decide" was concluded a **failure** from a single
confounded run (paradigm contamination of the treatment arm, an uncalibrated
instrument, unequal processing, N=1 with no pre-registered bar). The wrong
verdict was stated directly in chat with no rigor audit in front of it; the user
had to catch and correct it by hand. This hook makes that class of unaudited
conclusion produce a mechanical nudge.

At every Stop it scans the just-ended transcript and BLOCKS the stop (exit 2,
reminder on stderr) when ALL of the following hold:

  1. **Verdict language** -- an assistant text item states an experiment
     conclusion in one of a bounded, high-precision set of operative idioms
     (``verdict_conclusion_present``): "the spike failed", "the experiment was a
     success", "adopt the challenger", "the challenger outperformed ...", etc.
     Deliberately NARROW (an experiment noun anchored tightly to an outcome /
     adopt-reject verb) so ordinary prose about experiments -- including a design
     session merely *discussing* the protocol, or a session that quotes a past
     "was concluded a failure" -- does not trip it. Scanned in assistant text
     ONLY, so verdict wording written INTO a file (an ADR, a report -- a Write /
     Edit ``tool_use`` input) never counts; that is what keeps a rigor-docs PR
     from flagging its own prose.
  2. **No experiment-audit ran** (``audit_marker_present`` is False) -- neither
     the ``[experiment-audit]`` marker the skill emits in its output blocks, nor
     a ``/experiment-audit`` slash-command invocation, appears this session.
  3. **No skip override** (``skip_override``) -- a genuine user "skip experiment
     audit" instruction waives the checkpoint up front.

Delivery is **exit 2 + stderr, blocking-once**: per ADR-091 a Stop hook's exit-0
output is invisible to Claude (transcript-only), so exit 2 + stderr is the only
Stop delivery that reaches the model -- the same mechanism the tile gate and
``stop-journal-stub-checkpoint.py`` use. It is advisory in spirit, not a hard
gate: a once-per-session scratch sentinel means it fires at most once, and the
reminder tells Claude how to dismiss a false positive in one line ("reply that
no experiment conclusion was drawn"). Because it is a natural-language heuristic,
a false positive is possible; the once-only + dismissable design (mirroring
``stop-journal-stub-checkpoint.py``, itself a keyword heuristic that blocks) keeps
the cost to a single glance, never a self-correction loop. Stop fires reliably in
background / SDK-launched sessions (ADR-053/055), unlike PostToolUse.

Detection is a pure transcript scan -- no ``gh`` calls, no network, no
subprocess (so no ``_winsubp``). Fail-open: any error (empty/malformed stdin,
missing transcript, parse/scan failure, the outer ``__main__`` guard) exits 0.
Honors the ``stop_hook_active`` loop-guard flag. A single per-session sentinel
suffices -- this hook has one trigger.

The transcript-record readers (``_parse_records`` / ``_content_items``) and the
sentinel / transcript-locate helpers come from the shared ``_hookutil`` module
(ADR-090 / ADR-064), the same modules the sibling Stop hooks build on.

Stdin JSON shape (Stop):
  {"session_id": "...", "transcript_path": "/abs/path.jsonl",
   "stop_hook_active": false, ...}

Exit 0 -- no experiment verdict stated, an audit ran or was invoked, a "skip
         experiment audit" override, this hook already fired/resolved this
         session (sentinel), stop_hook_active set, or any error (fail-open).
Exit 2 -- an experiment conclusion was stated with no audit; blocking reminder
         on stderr.
"""
from __future__ import annotations

import _hookutil
import json
import re
import sys
from pathlib import Path

from _hookutil import _content_items, _is_synthetic_user, _parse_records, _user_message_texts

SENTINEL_PREFIX = "experiment-verdict-gate-"

# --- verdict-language detection (assistant text) -------------------------------
# A deliberately NARROW, bounded set of operative experiment-conclusion idioms --
# high precision over recall, the same stance as stop-tile-enumeration-gate.py's
# deferral-question trigger (ADR-109) and the reverse of a broad keyword sweep.
# Each pattern anchors an EXPERIMENT noun tightly to an OUTCOME or ADOPT/REJECT
# verb, so ordinary prose ("this experiment in refactoring", "the test failed" --
# a unit test) and meta-discussion of the protocol ("an experiment was wrongly
# concluded a failure", with words BETWEEN the noun and the verb) do not match.
# All ASCII; scanned only against assistant `text` items (never tool_use inputs),
# so verdict wording written into an ADR / report file is invisible here.
_VERDICT_RES = (
    # 1. experiment noun + outcome verb in tight adjacency (<=2 words between):
    #    "the spike failed", "generate-then-decide succeeded", "A/B test passed".
    re.compile(
        r"\b(?:experiment|spike|pilot|trial|a/b\s*test|generate-then-decide)\b"
        r"(?:\s+\w+){0,2}\s+"
        r"(?:failed|succeeded|passed|won|lost)\b",
        re.IGNORECASE,
    ),
    # 2. experiment noun + "is/was a failure|success|win|loss" (short proximity).
    re.compile(
        r"\b(?:experiment|spike|pilot|trial|challenger|a/b\s*test|generate-then-decide)\b"
        r"[^.\n]{0,24}\b(?:is|was|were)\s+a\s+(?:clear\s+|net\s+|qualified\s+)?"
        r"(?:failure|success|win|loss)\b",
        re.IGNORECASE,
    ),
    # 3. adopt / reject / roll out / abandon an experiment-anchored arm
    #    (challenger / incumbent / generate-then-decide). Deliberately NOT a bare
    #    "adopt the new flow": without an experiment-anchored object that is
    #    indistinguishable from an ordinary product decision (a false-positive
    #    risk), and its object carries no _CTX_PREFILTER token, which would break
    #    main()'s superset guarantee. Anchoring the object keeps both precision
    #    and a sound pre-filter superset.
    re.compile(
        r"\b(?:adopt|reject|roll\s+out|abandon)\b\s+(?:the\s+)?"
        r"(?:challenger|incumbent|generate-then-decide)\b",
        re.IGNORECASE,
    ),
    # 4. the challenger won / lost / beat / out(under)performed the incumbent.
    re.compile(
        r"\bchallenger\b[^.\n]{0,24}"
        r"\b(?:won|lost|beat|beats|wins|loses|outperform\w*|underperform\w*)\b",
        re.IGNORECASE,
    ),
)

# --- experiment-audit-ran markers ----------------------------------------------
# The stable marker every /experiment-audit output block opens with (design
# declarations, the pre-registration block, the verdict audit table). Its
# presence in assistant text proves the audit process actually engaged. Bare
# bracketed literal -- the skill emits it verbatim.
_AUDIT_MARKER_RE = re.compile(r"\[experiment-audit\]")
# A /experiment-audit slash-command invocation -- Claude Code emits a real user
# record wrapping "<command-name>/experiment-audit</command-name> ...". Keyed on
# the wrapper so prose merely naming the command never counts.
_AUDIT_CMD_RE = re.compile(r"<command-name>\s*/?experiment-audit\b", re.IGNORECASE)

# The only valid waiver -- an explicit user instruction to skip the audit.
_SKIP_RE = re.compile(
    r"\b(?:skip(?:\s+the)?\s+experiment[\s-]*audit|"
    r"no\s+experiment[\s-]*audit|skip\s+experiment[\s-]*rigor)\b",
    re.IGNORECASE,
)

# --- cheap main() pre-filter (guaranteed superset of _VERDICT_RES) -------------
# Every _VERDICT_RES pattern contains BOTH an experiment-context token AND a
# verdict/decision token, so requiring one of each (as raw lowercase substrings,
# before the JSON parse) can never fast-exit a fire-worthy session; it only skips
# the parse when the transcript could not possibly match. Deliberately generous
# stems (over-inclusion merely costs an occasional extra parse -- the same loose
# pre-filter tradeoff stop-journal-stub-checkpoint.py accepts).
_CTX_PREFILTER = (
    "experiment", "spike", "pilot", "challenger", "incumbent",
    "a/b", "trial", "generate-then-decide",
)
_VERDICT_PREFILTER = (
    "fail", "succe", "pass", "won", "lost", "loss", "lose", "win",
    "adopt", "reject", "roll out", "abandon", "beat",
    "outperform", "underperform",
)


def _prefilter_passes(text_lower: str) -> bool:
    """The cheap pre-filter, factored out of ``main()`` so the sound-superset
    invariant is directly testable against ``_VERDICT_RES``. A verdict idiom
    needs BOTH an experiment-context token and a verdict/decision token; this
    returns whether both are present as raw substrings. Every ``_VERDICT_RES``
    pattern carries one of each as a *contiguous* literal (idiom objects are
    experiment-anchored — no whitespace-splittable compound like ``new\\s+flow``
    — and ``loses`` is covered by the ``lose`` stem), so this can never fast-exit
    a fire-worthy session (the property ``test_prefilter_is_superset`` pins)."""
    return (any(s in text_lower for s in _CTX_PREFILTER)
            and any(s in text_lower for s in _VERDICT_PREFILTER))


# --- pure detection helpers (offline-testable) ---------------------------------

def _assistant_texts(records: list):
    """Yield every assistant ``text`` item's string, skipping tool_use inputs.

    Verdict wording written INTO a file (a Write/Edit ``tool_use`` input -- an
    ADR, a report) lives in tool_use, never in a ``text`` item, so scanning text
    only is what keeps a rigor-docs session from flagging the very prose it is
    authoring."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if isinstance(item, dict) and item.get("type") == "text":
                yield item.get("text", "") or ""


def verdict_conclusion_present(records: list) -> bool:
    """True iff an assistant text item states an experiment conclusion in one of
    the bounded operative idioms (``_VERDICT_RES``). The single fire signal."""
    for text in _assistant_texts(records):
        if any(rx.search(text) for rx in _VERDICT_RES):
            return True
    return False


def audit_marker_present(records: list) -> bool:
    """True iff /experiment-audit engaged this session -- either the
    ``[experiment-audit]`` marker in assistant text (the skill's output blocks)
    or a ``<command-name>/experiment-audit`` invocation in any user text. Errs
    toward suppression (a false "audit ran" only silences the nudge, the safe
    advisory direction)."""
    for text in _assistant_texts(records):
        if _AUDIT_MARKER_RE.search(text):
            return True
    for rec in records:
        if isinstance(rec, dict) and _is_synthetic_user(rec):
            continue  # a compact-summary/isMeta echo of the command is not a real invocation
        if any(_AUDIT_CMD_RE.search(t) for t in _user_message_texts(rec)):
            return True
    return False


def skip_override(records: list) -> bool:
    """True iff a genuine user message this session waived the checkpoint ("skip
    experiment audit" / "no experiment audit"). Only real user-typed text counts
    -- never a tool_result or a compact summary that merely contains the phrase
    (mirrors the sibling Stop hooks' skip_override scoping)."""
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "user" or _is_synthetic_user(rec):
            continue
        if any(_SKIP_RE.search(t) for t in _user_message_texts(rec)):
            return True
    return False


def evaluate(records: list) -> tuple:
    """Return ``(fire, resolved)``.

    ``fire`` -- True when an experiment conclusion was stated with no audit and no
    waiver: the caller writes the sentinel and blocks (exit 2).
    ``resolved`` -- True when a verdict was stated but the checkpoint is already
    satisfied (an audit ran / was invoked, or the user waived it): the caller
    writes the sentinel so later Stops skip the re-scan.
    ``(False, False)`` -- no experiment verdict stated yet: no sentinel, re-check
    on the next Stop (a conclusion may still be drawn later in the session).
    """
    if not verdict_conclusion_present(records):
        return False, False
    if audit_marker_present(records):
        return False, True
    if skip_override(records):
        return False, True
    return True, False


def format_reminder() -> str:
    """The exit-2 stderr message. ASCII-only: Claude Code pipes hook output as
    cp1252 on Windows, so a char outside it (an arrow, an em-dash) would raise
    UnicodeEncodeError and the whole reminder would vanish -- use ``--`` (mirrors
    stop-journal-stub-checkpoint.py / stop-tile-enumeration-gate.py)."""
    return (
        "[experiment-verdict-gate] This session states a conclusion about a process "
        "experiment (an A/B spike, challenger-vs-incumbent, or before/after comparison), "
        "but no /experiment-audit ran. Per the global CLAUDE.md \"## Experimental "
        "Rigor\" protocol, no conclusion is valid without a design that could have "
        "produced the opposite conclusion. Before ending the turn:\n"
        "  1. Run /experiment-audit verdict <results path | issue #N | description> to "
        "gate the conclusion -- it checks that a pre-registration existed and was "
        "followed, instruments were calibrated, arms were contamination-checked and "
        "stage-matched, and reports the verdict as supported / refuted / inconclusive "
        "(never \"failure\"), with a scope statement.\n"
        "  2. An experiment without a fair, pre-registered design cannot yield adopt / "
        "reject -- only a hypothesis for a proper run.\n"
        "If this session drew no experiment conclusion (the phrasing was incidental, or "
        "it merely discusses/documents experiments), reply that no experiment audit is "
        "needed and continue -- this is an advisory checkpoint, not a hard gate. An "
        "explicit \"skip experiment audit\" instruction suppresses it up front."
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
    _hookutil.record_heartbeat("stop-experiment-verdict-gate")
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
    # never block again (Claude Code hooks reference -- check stop_hook_active).
    if data.get("stop_hook_active"):
        sys.exit(0)

    session_id = data.get("session_id") or ""
    # Fire at most once per session -- the sentinel short-circuits later Stops.
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

    # Cheap pre-filter (see _prefilter_passes): a verdict idiom needs BOTH an
    # experiment-context token and a verdict/decision token in the raw transcript.
    # It is a guaranteed superset of _VERDICT_RES, so it only skips the parse when
    # no match is possible, never fast-exiting a fire-worthy session.
    if not _prefilter_passes(text.lower()):
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
        sys.stderr.write(format_reminder() + "\n")
        sys.exit(2)
    if resolved:
        _mark_fired(session_id)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
