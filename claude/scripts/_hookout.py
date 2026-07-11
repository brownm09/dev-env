#!/usr/bin/env python3
"""Shared advisory/block emitter for Claude Code hooks — one encoding of the
per-event output-contract channel table.

Claude Code delivers a hook's output to the model or the user through *different*
physical channels depending on the hook event and the exit code, and getting the
mapping wrong is silent by construction: an advisory printed to the wrong stream
simply never appears. That exact bug was fixed one hook at a time — ADR-091
(journal-stop-check), ADR-098 / PR #701 (dev-env-sync), ADR-099 / PR #705
(journal-canonical-guard), ADR-100 (the new stub-checkpoint Stop hook) — each
re-deriving the same contract locally. This module encodes the contract **once**
so no hook re-derives (and re-breaks) it. See ADR-103.

Per-event output-contract channel table (the single source of truth):

  ==================  ================================  =========================
  hook event          exit 0  (non-blocking)            exit 2  (blocking)
  ==================  ================================  =========================
  UserPromptSubmit    plain stdout / additionalContext  stderr -> MODEL; the
  SessionStart          -> MODEL-visible, AND            prompt / tool / stop is
  UserPromptExpansion   {"systemMessage"} -> USER        blocked
  ------------------  --------------------------------  -------------------------
  PreToolUse          plain stdout -> transcript-only   stderr -> MODEL; the
  PostToolUse           (NOT model-visible);            prompt / tool / stop is
  Stop / SubagentStop   {"systemMessage"} -> USER        blocked
  (Notification,        (plain stdout is invisible)
   PreCompact, ...)
  ==================  ================================  =========================

Two facts do all the work:
  * plain stdout on exit 0 reaches the MODEL only on the *context* events
    (UserPromptSubmit / SessionStart / UserPromptExpansion — see
    ``STDOUT_MODEL_VISIBLE_EVENTS``); everywhere else exit-0 stdout is
    transcript-only, i.e. invisible to the model.
  * ``{"systemMessage": ...}`` JSON on exit 0 reaches the USER on ANY event, and
    ``exit 2`` + stderr reaches the MODEL on ANY event (blocking the
    prompt/tool/stop).

Corollary the emitter *enforces*: there is no non-blocking, model-visible channel
on PreToolUse / PostToolUse / Stop. Asking for one raises ``ValueError`` rather
than emitting into the void — the audience/event/blocking triple is always static
at a call site, so the error surfaces in the hook's own test, never in production.
The author is guided to the two honest options: ``blocking=True`` (exit-2 stderr,
which reaches the model *and* halts) or ``audience="user"`` (a systemMessage toast).

API
---
    emit_advisory(event, text, *, audience="model"|"user"|"both", blocking=False)
        Deliver a non-blocking advisory (exit 0), or a blocking one (exit 2), then
        ``sys.exit`` with the corresponding code. The channel is chosen from the
        table above; an undeliverable request raises ``ValueError``.
    emit_block(text)
        Deliver a blocking reason to the model (exit-2 stderr) on any event, then
        ``sys.exit(2)``. The exit-2 counterpart of a fail-closed gate's verdict.
    ascii_sanitize(text) -> str
        Best-effort ASCII rendering (guaranteed ``.isascii()``) so raw-stream text
        survives Claude Code's cp1252-decoded hook-output pipe on Windows — the
        vanishing-output class posttooluse-inert-advisory.py / idle-refresher.py
        guard against per-hook today. JSON channels don't need this (they use
        ``json.dumps(ensure_ascii=True)``, which escapes non-ASCII on the wire while
        preserving the content for the parser).
    plan_emission(event, text, *, audience, blocking) -> Emission
        The pure core: returns the ``Emission(stdout, stderr, exit_code)`` the
        deliverers perform. Exposed so hooks needing custom control (e.g. emitting
        several times, or exiting under their own logic) and the test suite can
        reach the routing decision without the ``sys.exit`` side effect.

Delivery owns the exit code on purpose: the channel *is* coupled to the exit code
(a systemMessage requires exit 0; a block requires exit 2), so "emit, then exit
with the matching code" is the only correct sequence — folding the exit into the
emitter removes the "wrote the block reason but forgot to exit 2" bug class.
``sys.exit`` raises ``SystemExit`` (a ``BaseException``, not ``Exception``), so a
hook's ``try: main() except Exception: sys.exit(0)`` safe-exit guard does not
swallow a deliberate exit-2 block.

Imported the same way as ``_hookio`` / ``_hookutil``: a sibling module in
``scripts/`` that the ``pyw -3`` hook launcher (which puts the script's own
directory on ``sys.path``) and the test harness (``sys.path.insert(0,
scripts_dir)``) both resolve. Depends only on the standard library.

Usage:
    import _hookout

    # A UserPromptSubmit advisory Claude should act on:
    _hookout.emit_advisory("UserPromptSubmit", msg, audience="model")

    # A PostToolUse / Stop status the human should see, not blocking anything:
    _hookout.emit_advisory("PostToolUse", msg, audience="user")

    # A blocking reason the model must see (any event):
    _hookout.emit_block(reason)
"""
from __future__ import annotations

import json
import sys
from typing import NamedTuple, NoReturn

# The three events on which plain stdout + exit 0 is model-visible (Claude Code
# adds it to context). Everywhere else exit-0 stdout is transcript-only. Kept as a
# module constant so the routing rule below, the tests, and any downstream lint
# (the AST output-contract gate planned in the same initiative) all reference one
# list. See the dev-env CLAUDE.md `## Observability` stream-choice note and ADR-027.
STDOUT_MODEL_VISIBLE_EVENTS = frozenset(
    {"UserPromptSubmit", "SessionStart", "UserPromptExpansion"}
)

_VALID_AUDIENCES = ("model", "user", "both")

# Common non-ASCII punctuation / operators with an unambiguous ASCII rendering.
# `ascii_sanitize` maps these to something readable, then replaces anything still
# non-ASCII (emoji, rare symbols) with "?" via the encode-replace backstop below.
# Deliberately domain-agnostic: a caller wanting a *semantic* ASCII rendering of a
# status glyph (e.g. a red circle -> "OVER") does that mapping itself; this helper
# only guarantees the bytes are cp1252-safe without garbling ordinary typography.
_ASCII_MAP = {
    ord("—"): "-",    # em dash
    ord("–"): "-",    # en dash
    ord("‒"): "-",    # figure dash
    ord("‐"): "-",    # hyphen
    ord("‑"): "-",    # non-breaking hyphen
    ord("−"): "-",    # minus sign
    ord("‘"): "'",    # left single quotation mark
    ord("’"): "'",    # right single quotation mark
    ord("‚"): "'",    # single low-9 quotation mark
    ord("‛"): "'",    # single high-reversed-9 quotation mark
    ord("“"): '"',    # left double quotation mark
    ord("”"): '"',    # right double quotation mark
    ord("„"): '"',    # double low-9 quotation mark
    ord("‟"): '"',    # double high-reversed-9 quotation mark
    ord("…"): "...",  # horizontal ellipsis
    ord("→"): "->",   # rightwards arrow
    ord("←"): "<-",   # leftwards arrow
    ord("⇒"): "=>",   # rightwards double arrow
    ord("≤"): "<=",   # less-than or equal to
    ord("≥"): ">=",   # greater-than or equal to
    ord("≠"): "!=",   # not equal to
    ord("×"): "x",    # multiplication sign
    ord("÷"): "/",    # division sign
    ord("•"): "*",    # bullet
    ord("·"): ".",    # middle dot
    ord(" "): " ",    # no-break space
}


def ascii_sanitize(text) -> str:
    """Return an ASCII-only rendering of *text* (guaranteed ``.isascii()``).

    Maps common Unicode punctuation and operators to sensible ASCII (dashes ->
    "-", curly quotes -> straight, ellipsis -> "...", arrows, comparison
    operators, bullet, middle dot, no-break space), then replaces anything still
    non-ASCII — emoji, rare symbols — with "?" via ``encode("ascii", "replace")``.

    Guaranteeing ``.isascii()`` also guarantees cp1252-encodability (ASCII is a
    subset of cp1252), so the result survives Claude Code's cp1252-decoded
    hook-output pipe on Windows, where a raw non-cp1252 byte on stderr/stdout
    otherwise raises ``UnicodeEncodeError`` at print time and the whole advisory
    is lost. ``None`` and non-str inputs are coerced (``None`` -> "").
    """
    if text is None:
        return ""
    translated = str(text).translate(_ASCII_MAP)
    if translated.isascii():
        return translated
    # Backstop: any remaining non-ASCII code point -> "?" (keeps a placeholder so
    # a stripped glyph leaves a visible trace rather than vanishing silently).
    return translated.encode("ascii", "replace").decode("ascii")


class Emission(NamedTuple):
    """The routing decision `plan_emission` returns and the deliverers perform.

    ``stdout`` / ``stderr`` are the exact strings to write to each stream (``None``
    = write nothing to that stream); ``exit_code`` is the process exit code the
    channel requires (0 for a non-blocking advisory, 2 for a block). Exactly one of
    ``stdout`` / ``stderr`` is set for any Emission this module produces.
    """

    stdout: str | None
    stderr: str | None
    exit_code: int


def plan_emission(event, text, *, audience: str = "model", blocking: bool = False) -> Emission:
    """Pure: choose the channel + exit code for an advisory, per the module table.

    *event* is the Claude Code hook event name (e.g. ``"UserPromptSubmit"``,
    ``"PostToolUse"``, ``"Stop"``); it may be ``None`` only when *blocking* is True
    (a block is event-independent — exit-2 stderr reaches the model on every event).
    *audience* is one of ``"model"`` / ``"user"`` / ``"both"``. *blocking* selects
    the exit-2 stderr channel.

    Returns an :class:`Emission`. Raises ``ValueError`` when the request is not
    deliverable under the contract:
      * ``audience="user"`` with ``blocking=True`` — a block reaches the model via
        stderr, not the user via systemMessage (which needs exit 0);
      * ``audience`` includes ``"model"``, non-blocking, on a non-context event —
        there is no non-blocking model-visible channel there (use ``blocking=True``
        or ``audience="user"``);
      * a non-blocking call with ``event=None``, or an unknown *audience*.

    Every real call site passes these as literals, so a ``ValueError`` is a
    development-time signal (it surfaces in the hook's test), not a runtime risk.
    """
    if audience not in _VALID_AUDIENCES:
        raise ValueError(
            f"audience must be one of {_VALID_AUDIENCES!r}, got {audience!r}"
        )
    want_model = audience in ("model", "both")
    want_user = audience in ("user", "both")

    if blocking:
        # Exit-2 + stderr is the one model-visible channel on every event. There is
        # no exit-2 user channel (systemMessage needs exit 0), so a block cannot
        # also deliver a separate user toast.
        if want_user:
            raise ValueError(
                "a blocking advisory reaches the model via exit-2 stderr, not the "
                "user via systemMessage (which requires exit 0); use "
                "audience='model' for the block and, if the user also needs a "
                "toast, emit a separate non-blocking audience='user' advisory"
            )
        return Emission(stdout=None, stderr=ascii_sanitize(text), exit_code=2)

    # Non-blocking: everything rides exit 0 as a single JSON object.
    if event is None:
        raise ValueError("event is required for a non-blocking advisory")

    ctx = event in STDOUT_MODEL_VISIBLE_EVENTS
    payload: dict = {}

    if want_model:
        if not ctx:
            raise ValueError(
                f"a non-blocking advisory cannot reach the model on {event!r}: "
                "plain stdout is transcript-only there. Pass blocking=True "
                "(exit-2 stderr) to reach the model, or audience='user' to deliver "
                "a systemMessage toast. Non-blocking model-visible events: "
                f"{sorted(STDOUT_MODEL_VISIBLE_EVENTS)}"
            )
        payload["hookSpecificOutput"] = {
            "hookEventName": event,
            "additionalContext": text,
        }

    if want_user:
        payload["systemMessage"] = text

    # ensure_ascii=True escapes non-ASCII to \uXXXX so the emitted stdout bytes are
    # pure ASCII (cp1252-safe on the wire) while the parser restores the original
    # content — so JSON-channel text is NOT run through ascii_sanitize.
    return Emission(stdout=json.dumps(payload, ensure_ascii=True), stderr=None, exit_code=0)


def _deliver(emission: Emission) -> NoReturn:
    """Write an :class:`Emission` to the real streams and ``sys.exit`` its code.

    Writes are flushed explicitly so nothing is lost if the interpreter is torn
    down abruptly after ``sys.exit`` (the ASCII guarantee means the encode under
    Windows' cp1252 stdio never raises)."""
    if emission.stdout is not None:
        sys.stdout.write(emission.stdout + "\n")
        sys.stdout.flush()
    if emission.stderr is not None:
        sys.stderr.write(emission.stderr + "\n")
        sys.stderr.flush()
    sys.exit(emission.exit_code)


def emit_advisory(
    event, text, *, audience: str = "model", blocking: bool = False
) -> NoReturn:
    """Emit an advisory over the channel the contract prescribes, then ``sys.exit``.

    Thin wrapper over ``plan_emission`` + ``_deliver`` — see ``plan_emission`` for
    the routing rules and the ``ValueError`` conditions. Always exits: exit 0 for a
    non-blocking advisory, exit 2 for ``blocking=True``.
    """
    _deliver(plan_emission(event, text, audience=audience, blocking=blocking))


def emit_block(text) -> NoReturn:
    """Emit a blocking reason to the model (exit-2 stderr) on any event; exit 2.

    The exit-2 counterpart to a fail-closed gate's verdict, or to any hook that
    must both surface a reason to the model *and* halt (a PreToolUse tool block, a
    Stop-hook re-block). Event-independent: exit-2 stderr reaches the model on
    every event. Text is ``ascii_sanitize``-d so the reason can't vanish under the
    cp1252 hook-output pipe (rule 4 of the REFERENCE.md authoring rules applies to
    exit-2 stderr too).
    """
    _deliver(plan_emission(None, text, audience="model", blocking=True))
