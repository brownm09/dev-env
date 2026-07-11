#!/usr/bin/env python3
"""Tests for _hookout.py — the shared hook advisory/block emitter (dev-env #719,
ADR-103).

Exercises the pure surface offline (no stdin, network, gh, or disk): the
``plan_emission`` channel-routing matrix, ``ascii_sanitize``, the exit-0 JSON
shape, and the ``.isascii()`` wire-safety guarantee. The ``emit_advisory`` /
``emit_block`` deliverers are exercised in-process by redirecting stdout/stderr to
``io.StringIO`` and catching the ``SystemExit`` they raise — still offline, no
subprocess.

Cases pinned:
- ``ascii_sanitize``: ASCII passthrough, the punctuation/operator map (dashes,
  curly quotes, ellipsis, arrows, comparison ops, bullet, middle dot, no-break
  space), the emoji/unmapped "?" backstop, None/non-str coercion, and that the
  result is ALWAYS ``.isascii()``.
- ``plan_emission`` matrix — every (audience x event-class x blocking) cell:
  model+context+non-blocking -> additionalContext JSON (exit 0); model+context+
  blocking and model+non-context+blocking -> exit-2 stderr; model+non-context+
  non-blocking -> ValueError (no non-blocking model channel there); user+any+
  non-blocking -> systemMessage JSON; user+blocking -> ValueError; both+context+
  non-blocking -> both keys in one JSON; both in every other cell -> ValueError;
  invalid audience and event=None-non-blocking -> ValueError; the three context
  events each stamp their own hookEventName.
- JSON shape: exit-0 stdout is valid JSON with exactly the expected keys, and is
  ``.isascii()`` even when the advisory text carries Unicode (ensure_ascii escaping)
  while ``json.loads`` restores the original content; exit-2 stderr is ``.isascii()``
  (ascii_sanitize applied).
- ``emit_advisory`` / ``emit_block``: the deliverers write the planned stream and
  exit with the planned code; an undeliverable ``emit_advisory`` propagates the
  ``ValueError`` (it never reaches a stream write).
- ``STDOUT_MODEL_VISIBLE_EVENTS`` is exactly the three context events.
"""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _hookout as mod  # noqa: E402

plan_emission = mod.plan_emission
ascii_sanitize = mod.ascii_sanitize
emit_advisory = mod.emit_advisory
emit_block = mod.emit_block
Emission = mod.Emission
STDOUT_MODEL_VISIBLE_EVENTS = mod.STDOUT_MODEL_VISIBLE_EVENTS

CONTEXT_EVENTS = ("UserPromptSubmit", "SessionStart", "UserPromptExpansion")
NONCONTEXT_EVENTS = ("PreToolUse", "PostToolUse", "Stop", "SubagentStop", "Notification")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception as e:  # noqa: BLE001
        raise AssertionError(
            f"expected {exc.__name__}, got {type(e).__name__}: {e}"
        )
    raise AssertionError(f"expected {exc.__name__}, no exception raised")


def _run(fn, *args, **kwargs):
    """Run a deliverer, capturing (stdout, stderr, exit_code). Lets a non-SystemExit
    exception propagate so a mis-routed deliverer surfaces as a test error."""
    out, err = io.StringIO(), io.StringIO()
    code = "no-exit"
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(*args, **kwargs)
        except SystemExit as e:
            code = e.code
    return out.getvalue(), err.getvalue(), code


# ---------------------------------------------------------------------------
# ascii_sanitize
# ---------------------------------------------------------------------------

def test_ascii_passthrough_plain():
    assert ascii_sanitize("hello world 123 -> <= != done") == "hello world 123 -> <= != done"


def test_ascii_dashes_all_map_to_hyphen():
    assert ascii_sanitize("a—b–c‒d‐e‑f−g") == "a-b-c-d-e-f-g"


def test_ascii_curly_quotes():
    assert ascii_sanitize("‘x’ “y” „z‟ ‚a‛") == "'x' \"y\" \"z\" 'a'"


def test_ascii_ellipsis_multichar_expansion():
    assert ascii_sanitize("wait…") == "wait..."


def test_ascii_arrows_and_operators():
    assert ascii_sanitize("→←⇒≤≥≠×÷•·") == "-><-=><=>=!=x/*."


def test_ascii_no_break_space():
    # chr(0xA0) is the no-break space, spelled explicitly so the source is
    # unambiguous vs. a plain ASCII space.
    assert ascii_sanitize("a" + chr(0xA0) + "b") == "a b"


def test_ascii_emoji_backstop_to_question_mark():
    out = ascii_sanitize("🔴 over ✅")
    assert out.isascii()
    assert "over" in out
    assert "?" in out  # the two emoji became "?" placeholders


def test_ascii_none_coerced_to_empty():
    assert ascii_sanitize(None) == ""


def test_ascii_nonstr_coerced():
    assert ascii_sanitize(123) == "123"


def test_ascii_empty_string():
    assert ascii_sanitize("") == ""


def test_ascii_result_always_isascii():
    for s in ("plain", "em—dash", "🔴✅⚠", "≤≥≠", "mixed 語 text", None, 42):
        assert ascii_sanitize(s).isascii()


# ---------------------------------------------------------------------------
# plan_emission — model audience
# ---------------------------------------------------------------------------

def test_model_context_nonblocking_additional_context():
    em = plan_emission("UserPromptSubmit", "act on this", audience="model")
    assert em.stderr is None
    assert em.exit_code == 0
    payload = json.loads(em.stdout)
    assert set(payload) == {"hookSpecificOutput"}
    assert payload["hookSpecificOutput"] == {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "act on this",
    }


def test_model_context_stamps_each_event_name():
    for event in CONTEXT_EVENTS:
        em = plan_emission(event, "ctx", audience="model")
        payload = json.loads(em.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == event


def test_model_context_blocking_is_exit2_stderr():
    em = plan_emission("UserPromptSubmit", "blocked", audience="model", blocking=True)
    assert em.stdout is None
    assert em.stderr == "blocked"
    assert em.exit_code == 2


def test_model_noncontext_nonblocking_raises():
    for event in NONCONTEXT_EVENTS:
        _raises(ValueError, plan_emission, event, "x", audience="model")


def test_model_noncontext_blocking_is_exit2_stderr():
    for event in NONCONTEXT_EVENTS:
        em = plan_emission(event, "stop reason", audience="model", blocking=True)
        assert em.stdout is None
        assert em.stderr == "stop reason"
        assert em.exit_code == 2


# ---------------------------------------------------------------------------
# plan_emission — user audience
# ---------------------------------------------------------------------------

def test_user_nonblocking_systemmessage_on_noncontext_event():
    em = plan_emission("PostToolUse", "fyi", audience="user")
    assert em.stderr is None
    assert em.exit_code == 0
    payload = json.loads(em.stdout)
    assert set(payload) == {"systemMessage"}
    assert payload["systemMessage"] == "fyi"


def test_user_nonblocking_systemmessage_on_context_event():
    em = plan_emission("UserPromptSubmit", "toast", audience="user")
    payload = json.loads(em.stdout)
    assert set(payload) == {"systemMessage"}
    assert payload["systemMessage"] == "toast"


def test_user_nonblocking_works_on_all_events():
    for event in CONTEXT_EVENTS + NONCONTEXT_EVENTS:
        em = plan_emission(event, "u", audience="user")
        assert em.exit_code == 0
        assert json.loads(em.stdout) == {"systemMessage": "u"}


def test_user_blocking_raises():
    _raises(ValueError, plan_emission, "Stop", "x", audience="user", blocking=True)


# ---------------------------------------------------------------------------
# plan_emission — both audience
# ---------------------------------------------------------------------------

def test_both_context_nonblocking_carries_both_keys():
    em = plan_emission("UserPromptSubmit", "msg", audience="both")
    assert em.exit_code == 0
    payload = json.loads(em.stdout)
    assert set(payload) == {"hookSpecificOutput", "systemMessage"}
    assert payload["systemMessage"] == "msg"
    assert payload["hookSpecificOutput"] == {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "msg",
    }


def test_both_context_blocking_raises_user_half_undeliverable():
    _raises(ValueError, plan_emission, "UserPromptSubmit", "x", audience="both", blocking=True)


def test_both_noncontext_nonblocking_raises_model_half_undeliverable():
    _raises(ValueError, plan_emission, "PostToolUse", "x", audience="both")


def test_both_noncontext_blocking_raises():
    _raises(ValueError, plan_emission, "PostToolUse", "x", audience="both", blocking=True)


# ---------------------------------------------------------------------------
# plan_emission — validation
# ---------------------------------------------------------------------------

def test_invalid_audience_raises():
    _raises(ValueError, plan_emission, "UserPromptSubmit", "x", audience="everyone")


def test_event_none_nonblocking_raises():
    _raises(ValueError, plan_emission, None, "x", audience="user")
    _raises(ValueError, plan_emission, None, "x", audience="model")


def test_event_none_blocking_ok():
    # emit_block's path: event is irrelevant to a block.
    em = plan_emission(None, "reason", audience="model", blocking=True)
    assert em.stderr == "reason"
    assert em.exit_code == 2


# ---------------------------------------------------------------------------
# JSON shape / wire safety (.isascii())
# ---------------------------------------------------------------------------

def test_exit0_stdout_is_valid_json_for_each_audience():
    for em in (
        plan_emission("UserPromptSubmit", "m", audience="model"),
        plan_emission("PostToolUse", "u", audience="user"),
        plan_emission("UserPromptSubmit", "b", audience="both"),
    ):
        json.loads(em.stdout)  # raises on invalid JSON -> test failure


def test_json_stdout_isascii_with_unicode_text():
    # ensure_ascii=True escapes the em-dash + emoji to \uXXXX -> pure-ASCII wire bytes,
    # while json.loads restores the original content for the parser.
    text = "build — done 🔴"
    for audience, event in (("model", "UserPromptSubmit"), ("user", "Stop"), ("both", "SessionStart")):
        em = plan_emission(event, text, audience=audience)
        assert em.stdout.isascii(), f"{audience} stdout must be ASCII on the wire"
        payload = json.loads(em.stdout)
        restored = payload.get("systemMessage") or payload["hookSpecificOutput"]["additionalContext"]
        assert restored == text


def test_blocking_stderr_is_ascii_sanitized():
    em = plan_emission("Stop", "halt — because ✅", audience="model", blocking=True)
    assert em.stderr.isascii()
    assert em.stderr == "halt - because ?"


# ---------------------------------------------------------------------------
# STDOUT_MODEL_VISIBLE_EVENTS constant
# ---------------------------------------------------------------------------

def test_context_events_constant_exact():
    assert STDOUT_MODEL_VISIBLE_EVENTS == frozenset(
        {"UserPromptSubmit", "SessionStart", "UserPromptExpansion"}
    )


# ---------------------------------------------------------------------------
# emit_advisory / emit_block deliverers
# ---------------------------------------------------------------------------

def test_emit_advisory_user_delivers_systemmessage_exit0():
    out, err, code = _run(emit_advisory, "PostToolUse", "hi", audience="user")
    assert err == ""
    assert code == 0
    assert json.loads(out) == {"systemMessage": "hi"}


def test_emit_advisory_model_context_delivers_additional_context_exit0():
    out, err, code = _run(emit_advisory, "UserPromptSubmit", "hi", audience="model")
    assert err == ""
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "hi"


def test_emit_advisory_blocking_delivers_stderr_exit2():
    out, err, code = _run(
        emit_advisory, "Stop", "stop now", audience="model", blocking=True
    )
    assert out == ""
    assert err.strip() == "stop now"
    assert code == 2


def test_emit_block_delivers_stderr_exit2():
    out, err, code = _run(emit_block, "blocked reason")
    assert out == ""
    assert err.strip() == "blocked reason"
    assert code == 2


def test_emit_block_sanitizes_nonascii():
    out, err, code = _run(emit_block, "blocked — reason ✅")
    assert code == 2
    assert err.isascii()
    assert err.strip() == "blocked - reason ?"


def test_emit_advisory_undeliverable_propagates_valueerror():
    # The loud-failure property: a non-blocking model advisory on a non-context
    # event raises rather than writing anything to a stream.
    _raises(ValueError, emit_advisory, "PostToolUse", "x", audience="model")


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
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
