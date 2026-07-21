#!/usr/bin/env python3
"""Unit + behavioral tests for stop-experiment-verdict-gate.py (ADR-115).

`stop-experiment-verdict-gate.py` is a Stop hook that scans the just-ended
session transcript and blocks the stop (exit 2) when an assistant message states
a process-experiment conclusion (one of a bounded set of operative idioms) with
no /experiment-audit having run and no "skip experiment audit" override.

Two layers, mirroring this repo's hook-test convention:

  * Pure-helper tests exercise the detection/decision core offline (no stdin,
    network, gh, or disk): verdict-idiom detection (each of the four idioms;
    NOT ordinary prose, NOT a bare "the test failed" unit-test sentence, NOT
    meta-discussion that keeps words between the noun and the verb, and -- the
    load-bearing guarantee -- NOT verdict wording confined to a Write/Edit
    tool_use input, so a rigor-docs session never flags its own file content);
    audit-marker detection (the [experiment-audit] marker in assistant text and
    the /experiment-audit command wrapper, but not a bare prose mention); the
    skip override (and that a tool_result mention does NOT waive); the evaluate()
    composition; the reminder's cp1252-encodability; and that malformed records
    don't disable the gate.

  * A behavioral layer drives the real hook end-to-end over stdin via subprocess
    against a synthetic transcript, with HOME/USERPROFILE pointed at a temp dir
    so the once-per-session sentinel is isolated from the real ~/.claude/scratch.
    It pins: fire -> exit 2 with the reminder on stderr and empty stdout;
    audit-ran / skip / no-verdict -> exit 0; the docs-editing regression fixture
    -> exit 0; the stop_hook_active loop-guard -> exit 0; the sentinel suppresses
    a second fire; and the malformed-stdin fail-open paths.

Usage:
    py -3 claude/scripts/tests/test_stop_experiment_verdict_gate.py

Exit 0 = all pass.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "stop-experiment-verdict-gate.py"

# The script imports _hookutil (sibling in scripts/); make it resolvable.
sys.path.insert(0, str(SCRIPT.parent))

_spec = importlib.util.spec_from_file_location("stop_experiment_verdict_gate", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)  # safe: main() is guarded by __main__


# --- transcript-record builders ------------------------------------------------

def _asst_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _asst_tool(tid, name, inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "id": tid, "input": inp}]}}


def _asst_write(tid, path, content):
    return _asst_tool(tid, "Write", {"file_path": path, "content": content})


def _tool_result(tid, output):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": output}]}}


def _user_str(text):
    return {"type": "user", "message": {"content": text}}


def _user_text_item(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _user_compact(text):
    return {"type": "user", "isCompactSummary": True, "message": {"content": text}}


def _user_audit_cmd(args):
    return _user_str(
        f"<command-name>/experiment-audit</command-name>\n"
        f"<command-message>experiment-audit</command-message>\n"
        f"<command-args>{args}</command-args>"
    )


# --- shared fixtures -----------------------------------------------------------

# A session that STATES an experiment verdict with no audit -> evaluate == (True, False).
_VERDICT_NO_AUDIT = [
    _user_str("Run the generate-then-decide spike against the HungerRush letter."),
    _asst_text("I generated both arms and scored them. The spike failed on two of the "
               "four Part 4 checks, so plan-first still wins."),
]

# Same, but /experiment-audit was invoked -> resolved.
_VERDICT_THEN_AUDIT_CMD = [_VERDICT_NO_AUDIT[0]] + [
    _user_audit_cmd("verdict calibration/hungerrush-dir-swe"),
    _VERDICT_NO_AUDIT[1],
]

# Same, but the skill emitted its [experiment-audit] marker -> resolved.
_VERDICT_WITH_MARKER = _VERDICT_NO_AUDIT + [
    _asst_text("[experiment-audit] Verdict: inconclusive -- confounded by T2 (uncalibrated "
               "instrument). Scope: holds for N=1 under the recorded SHAs."),
]

# The rigor-docs regression fixture: the verdict wording lives ONLY in a Write
# tool_use input (an ADR being authored), and the assistant's own PROSE uses
# meta-phrasing that keeps words between the noun and the outcome word -- neither
# should fire. This is the "this PR's own text must not trip it" guarantee.
_DOCS_EDITING = [
    _user_str("Add the experimental-rigor ADR describing the incident."),
    _asst_text("This ADR records how an A/B spike was wrongly concluded a failure from a "
               "single confounded run, and how the protocol prevents that."),
    _asst_write("t1", "docs/adr/115-experimental-rigor-protocol.md",
                "The spike failed and was a failure; adopt the challenger. "
                "(quoted incident text inside the ADR body)"),
    _tool_result("t1", "File created"),
]


# ---------------------------------------------------------------------------
# verdict_conclusion_present
# ---------------------------------------------------------------------------

def test_verdict_spike_failed():
    recs = [_asst_text("The spike failed to beat the incumbent.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'the spike failed' -> True (idiom 1)"


def test_verdict_experiment_was_a_success():
    recs = [_asst_text("The experiment was a clear success across the corpus.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'experiment was a success' -> True (idiom 2)"


def test_verdict_adopt_the_challenger():
    recs = [_asst_text("Given the results, we should adopt the challenger going forward.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'adopt the challenger' -> True (idiom 3)"


def test_verdict_abandon_generate_then_decide():
    recs = [_asst_text("Given the wash, let's abandon generate-then-decide.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'abandon generate-then-decide' -> True (idiom 3, anchored)"


def test_verdict_unanchored_new_flow_not_matched():
    # Idiom 3 deliberately requires an experiment-anchored object; a bare
    # "adopt the new flow" (no experiment framing) is an ordinary decision and
    # must NOT fire (precision + keeps the main() pre-filter a sound superset).
    recs = [_asst_text("Let's adopt the new flow for all future letters.")]
    assert hook.verdict_conclusion_present(recs) is False
    return "verdict: unanchored 'adopt the new flow' -> False (idiom 3 is experiment-anchored)"


def test_verdict_challenger_outperformed():
    recs = [_asst_text("The challenger outperformed the incumbent on every input.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'challenger outperformed ...' -> True (idiom 4)"


def test_verdict_challenger_loses():
    recs = [_asst_text("Net, the challenger loses to the incumbent on 3 of 4 inputs.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'challenger loses' -> True (idiom 4, the 'loses' pre-filter edge)"


def test_verdict_generate_then_decide_succeeded():
    recs = [_asst_text("So generate-then-decide succeeded and should ship.")]
    assert hook.verdict_conclusion_present(recs) is True
    return "verdict: 'generate-then-decide succeeded' -> True (idiom 1)"


def test_verdict_unit_test_failed_not_matched():
    recs = [_asst_text("The test failed because the fixture path was wrong; fixed it.")]
    assert hook.verdict_conclusion_present(recs) is False
    return "verdict: bare 'the test failed' (unit test, no experiment noun) -> False"


def test_verdict_meta_discussion_not_matched():
    recs = [_asst_text("An A/B spike (Arm 2) vs. the incumbent plan-first flow (Arm 1) "
                       "was initially concluded a failure, then corrected.")]
    assert hook.verdict_conclusion_present(recs) is False
    return "verdict: meta-discussion with words between noun and outcome -> False"


def test_verdict_plain_experiment_mention_not_matched():
    recs = [_asst_text("This experiment in refactoring the parser went smoothly.")]
    assert hook.verdict_conclusion_present(recs) is False
    return "verdict: 'experiment' with no outcome/decision verb -> False"


def test_verdict_in_tool_use_input_not_matched():
    # Verdict wording confined to a Write input (file content) -> NOT assistant text.
    recs = [_asst_write("t1", "docs/adr/115.md", "The spike failed; adopt the challenger.")]
    assert hook.verdict_conclusion_present(recs) is False
    return "verdict: idiom only inside a Write tool_use input -> False (docs-editing guard)"


# ---------------------------------------------------------------------------
# Pre-filter superset invariant — the guard both reviewers flagged as missing
# (its absence let the idiom-3 'new X' and idiom-4 'loses' pre-filter holes hide)
# ---------------------------------------------------------------------------

# One+ sentence per _VERDICT_RES idiom branch, including the tricky verb forms
# (loses / wins / beats / succeeded / passed / was-a-failure) and the anchored
# idiom-3 objects. Every one MUST both (a) match verdict_conclusion_present and
# (b) clear the main() pre-filter. If any matches the regex but not the
# pre-filter, the pre-filter is not a sound superset and the hook silently fails
# to fire — exactly the bug this test exists to prevent regressing.
_SUPERSET_SENTENCES = [
    # idiom 1 — experiment noun + outcome verb
    "the spike failed on two checks",
    "generate-then-decide succeeded here",
    "the a/b test passed cleanly",
    "the pilot won outright",
    "the trial lost on cohesion",
    # idiom 2 — experiment noun + was/is a failure|success|win|loss
    "the experiment was a failure",
    "the challenger was a clear success",
    "the spike is a net win",
    "the pilot was a loss",
    # idiom 3 — adopt/reject/roll out/abandon an anchored arm
    "adopt the challenger",
    "reject the incumbent",
    "abandon generate-then-decide",
    "roll out the challenger",
    # idiom 4 — challenger + won/lost/beat/beats/wins/loses/out|underperform
    "the challenger won",
    "the challenger loses",
    "the challenger beat the incumbent",
    "the challenger beats plan-first",
    "the challenger wins",
    "the challenger lost",
    "the challenger outperformed the incumbent",
    "the challenger underperformed",
]


def test_prefilter_is_superset_of_verdict_res():
    offenders = []
    for s in _SUPERSET_SENTENCES:
        matches = hook.verdict_conclusion_present([_asst_text(s)])
        assert matches, f"fixture should match a verdict idiom but did not: {s!r}"
        if not hook._prefilter_passes(s.lower()):
            offenders.append(s)
    assert not offenders, (
        "pre-filter is NOT a superset of _VERDICT_RES — these match the regex but the "
        f"pre-filter would fast-exit before parsing (silent no-fire): {offenders}"
    )
    return f"pre-filter superset holds for all {len(_SUPERSET_SENTENCES)} idiom-branch phrasings"


# ---------------------------------------------------------------------------
# audit_marker_present
# ---------------------------------------------------------------------------

def test_audit_marker_in_assistant_text():
    recs = [_asst_text("[experiment-audit] Pre-registration frozen; tier 1.")]
    assert hook.audit_marker_present(recs) is True
    return "audit_marker: [experiment-audit] in assistant text -> True"


def test_audit_marker_command_wrapper():
    recs = [_user_audit_cmd("design the narrative-flow spike")]
    assert hook.audit_marker_present(recs) is True
    return "audit_marker: /experiment-audit command wrapper -> True"


def test_audit_marker_prose_mention_not_matched():
    recs = [_asst_text("I could run experiment-audit later if you want.")]
    assert hook.audit_marker_present(recs) is False
    return "audit_marker: bare prose mention (no bracket, no wrapper) -> False"


def test_audit_marker_absent():
    recs = [_asst_text("The spike failed.")]
    assert hook.audit_marker_present(recs) is False
    return "audit_marker: no marker/invocation -> False"


def test_audit_marker_compact_summary_wrapper_not_counted():
    # A compact-summary/isMeta echo of the command is not a real invocation.
    recs = [_user_compact("Earlier the session ran <command-name>/experiment-audit</command-name> verdict.")]
    assert hook.audit_marker_present(recs) is False
    return "audit_marker: /experiment-audit wrapper in an isCompactSummary -> False (synthetic guard)"


# ---------------------------------------------------------------------------
# skip_override
# ---------------------------------------------------------------------------

def test_skip_override_user_string():
    recs = [_user_str("skip experiment audit for this one")]
    assert hook.skip_override(recs) is True
    return "skip_override: user 'skip experiment audit' -> True"


def test_skip_override_hyphenated():
    recs = [_user_text_item("no experiment-audit needed here")]
    assert hook.skip_override(recs) is True
    return "skip_override: 'no experiment-audit' (hyphenated) -> True"


def test_skip_override_tool_result_not_counted():
    recs = [_user_str("run the spike"), _tool_result("t1", "note: skip experiment audit step")]
    assert hook.skip_override(recs) is False
    return "skip_override: phrase only in a tool_result -> False"


def test_skip_override_compact_summary_not_counted():
    recs = [_user_compact("Earlier the user said skip experiment audit.")]
    assert hook.skip_override(recs) is False
    return "skip_override: phrase in an isCompactSummary -> False"


def test_skip_override_absent():
    recs = [_user_str("run the spike")]
    assert hook.skip_override(recs) is False
    return "skip_override: no skip instruction -> False"


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def test_evaluate_fire():
    fire, resolved = hook.evaluate(_VERDICT_NO_AUDIT)
    assert fire is True and resolved is False
    return "evaluate: verdict stated, no audit/skip -> (True, False) [FIRE]"


def test_evaluate_audit_cmd_resolved():
    fire, resolved = hook.evaluate(_VERDICT_THEN_AUDIT_CMD)
    assert fire is False and resolved is True
    return "evaluate: /experiment-audit invoked -> (False, True) [resolved]"


def test_evaluate_marker_resolved():
    fire, resolved = hook.evaluate(_VERDICT_WITH_MARKER)
    assert fire is False and resolved is True
    return "evaluate: [experiment-audit] marker emitted -> (False, True) [resolved]"


def test_evaluate_skip_resolved():
    recs = _VERDICT_NO_AUDIT + [_user_str("actually, skip experiment audit here")]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is True
    return "evaluate: skip override -> (False, True) [resolved]"


def test_evaluate_no_verdict_noop():
    recs = [_user_str("help me tune the spike parameters"),
            _asst_text("I adjusted the k value and reran the calibration.")]
    fire, resolved = hook.evaluate(recs)
    assert fire is False and resolved is False
    return "evaluate: no verdict stated -> (False, False) [no-op]"


def test_evaluate_docs_editing_noop():
    fire, resolved = hook.evaluate(_DOCS_EDITING)
    assert fire is False and resolved is False
    return "evaluate: rigor-docs session (verdict only in file content) -> (False, False) [no-op]"


# ---------------------------------------------------------------------------
# format_reminder / robustness
# ---------------------------------------------------------------------------

def test_reminder_is_cp1252_encodable():
    msg = hook.format_reminder()
    assert msg.isascii(), "reminder must be ASCII (Claude Code pipes hook output as cp1252)"
    msg.encode("cp1252")  # must not raise
    assert "[experiment-verdict-gate]" in msg
    assert "/experiment-audit verdict" in msg
    assert "skip experiment audit" in msg
    return "format_reminder: ASCII/cp1252-encodable, carries prefix + dismissal text"


def test_malformed_records_do_not_disable():
    recs = [None, "str", 123, [], {"type": "weird"}] + list(_VERDICT_NO_AUDIT)
    fire, resolved = hook.evaluate(recs)
    assert fire is True and resolved is False
    return "evaluate: malformed/non-dict records around FIRE fixture -> still (True, False)"


# ---------------------------------------------------------------------------
# behavioral layer -- real hook over stdin via subprocess (HOME-isolated sentinel)
# ---------------------------------------------------------------------------

def _py_cmd():
    return ["py", "-3"] if shutil.which("py") else ["python3"]


def _run_hook(records, home, *, stop_hook_active=False, session_id="sess-test"):
    """Drive the real hook once. Returns (exit_code, stdout, stderr)."""
    home = Path(home)
    (home / ".claude").mkdir(parents=True, exist_ok=True)  # so the sentinel mkdir works
    tpath = home / "transcript.jsonl"
    tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    payload = json.dumps({
        "session_id": session_id,
        "transcript_path": str(tpath),
        "stop_hook_active": stop_hook_active,
        "hook_event_name": "Stop",
    })
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Path.home() honors USERPROFILE on Windows
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=payload,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_e2e_fire_blocks_on_stderr():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_VERDICT_NO_AUDIT, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[experiment-verdict-gate]" in err
    assert out.strip() == "", f"stdout must be empty on exit 2, got {out!r}"
    return "e2e verdict + no audit -> exit 2, reason on stderr, empty stdout"


def test_e2e_audit_cmd_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_VERDICT_THEN_AUDIT_CMD, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e verdict + /experiment-audit invoked -> exit 0 (allowed)"


def test_e2e_marker_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_VERDICT_WITH_MARKER, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e verdict + [experiment-audit] marker -> exit 0 (allowed)"


def test_e2e_skip_allows():
    records = _VERDICT_NO_AUDIT + [_user_str("skip experiment audit for this one")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e skip-experiment-audit override -> exit 0 (allowed)"


def test_e2e_no_verdict_allows():
    # Fixture deliberately CLEARS the pre-filter (ctx 'spike' + verdict stems
    # 'pass'/'win') so the hook actually reaches evaluate() and returns no-fire on
    # a non-idiom sentence -- not a pre-filter fast-exit (reviewer-flagged weak test).
    records = [_user_str("tune the spike parameters"),
               _asst_text("Adjusted k and reran calibration; the pass rate looks cleaner and win margins tightened.")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e no verdict idiom (reaches evaluate) -> exit 0 (allowed)"


def test_e2e_anchored_idiom3_fires():
    # idiom-3-only session (anchored object) reaches the real hook through the
    # pre-filter and fires -- closes the 'idiom-3 e2e untested' gap.
    records = [_user_str("Should we keep the generate-then-decide spike?"),
               _asst_text("Given the results, let's abandon generate-then-decide.")]
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(records, home)
    assert rc == 2, f"expected exit 2, got {rc} (stderr={err!r})"
    assert "[experiment-verdict-gate]" in err
    return "e2e anchored idiom-3 ('abandon generate-then-decide'), no audit -> exit 2"


def test_e2e_docs_editing_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_DOCS_EDITING, home)
    assert rc == 0, f"expected exit 0, got {rc} (stderr={err!r})"
    return "e2e rigor-docs session (verdict only in file content) -> exit 0 (allowed)"


def test_e2e_stop_hook_active_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook(_VERDICT_NO_AUDIT, home, stop_hook_active=True)
    assert rc == 0, f"expected exit 0 with stop_hook_active, got {rc} (stderr={err!r})"
    return "e2e stop_hook_active=true -> exit 0 (loop guard)"


def test_e2e_sentinel_suppresses_refire():
    with tempfile.TemporaryDirectory() as home:
        rc1, _, _ = _run_hook(_VERDICT_NO_AUDIT, home, session_id="sess-refire")
        rc2, out2, _ = _run_hook(_VERDICT_NO_AUDIT, home, session_id="sess-refire")
    assert rc1 == 2, f"first run expected exit 2, got {rc1}"
    assert rc2 == 0, f"second run expected exit 0 (sentinel), got {rc2}"
    return "e2e once-per-session sentinel: first fire exit 2, second exit 0"


def _run_hook_raw(raw_stdin, home):
    """Drive the real hook with arbitrary raw stdin (for the fail-open paths)."""
    home = Path(home)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    proc = subprocess.run(
        _py_cmd() + [str(SCRIPT)], input=raw_stdin,
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_e2e_empty_stdin_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook_raw("", home)
    assert rc == 0, f"empty stdin expected exit 0, got {rc} (stderr={err!r})"
    assert out.strip() == "" and err.strip() == ""
    return "e2e empty stdin -> exit 0, no output (fail-open)"


def test_e2e_non_dict_payload_allows():
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = _run_hook_raw("[1, 2, 3]", home)
    assert rc == 0, f"non-dict payload expected exit 0, got {rc} (stderr={err!r})"
    assert out.strip() == "" and err.strip() == ""
    return "e2e non-dict JSON payload -> exit 0, no output (fail-open)"


def main():
    tests = [
        # verdict_conclusion_present
        ("verdict spike failed", test_verdict_spike_failed),
        ("verdict experiment was a success", test_verdict_experiment_was_a_success),
        ("verdict adopt the challenger", test_verdict_adopt_the_challenger),
        ("verdict abandon generate-then-decide", test_verdict_abandon_generate_then_decide),
        ("verdict unanchored new flow not matched", test_verdict_unanchored_new_flow_not_matched),
        ("verdict challenger outperformed", test_verdict_challenger_outperformed),
        ("verdict challenger loses", test_verdict_challenger_loses),
        ("verdict generate-then-decide succeeded", test_verdict_generate_then_decide_succeeded),
        ("verdict unit-test 'failed' not matched", test_verdict_unit_test_failed_not_matched),
        ("verdict meta-discussion not matched", test_verdict_meta_discussion_not_matched),
        ("verdict plain 'experiment' not matched", test_verdict_plain_experiment_mention_not_matched),
        ("verdict in tool_use input not matched", test_verdict_in_tool_use_input_not_matched),
        ("prefilter is superset of verdict_res", test_prefilter_is_superset_of_verdict_res),
        # audit_marker_present
        ("audit marker in assistant text", test_audit_marker_in_assistant_text),
        ("audit marker command wrapper", test_audit_marker_command_wrapper),
        ("audit marker prose mention not matched", test_audit_marker_prose_mention_not_matched),
        ("audit marker absent", test_audit_marker_absent),
        ("audit marker compact-summary wrapper not counted", test_audit_marker_compact_summary_wrapper_not_counted),
        # skip_override
        ("skip override user string", test_skip_override_user_string),
        ("skip override hyphenated", test_skip_override_hyphenated),
        ("skip override tool_result not counted", test_skip_override_tool_result_not_counted),
        ("skip override compact summary not counted", test_skip_override_compact_summary_not_counted),
        ("skip override absent", test_skip_override_absent),
        # evaluate
        ("evaluate fire", test_evaluate_fire),
        ("evaluate audit-cmd resolved", test_evaluate_audit_cmd_resolved),
        ("evaluate marker resolved", test_evaluate_marker_resolved),
        ("evaluate skip resolved", test_evaluate_skip_resolved),
        ("evaluate no-verdict no-op", test_evaluate_no_verdict_noop),
        ("evaluate docs-editing no-op", test_evaluate_docs_editing_noop),
        # format_reminder / robustness
        ("reminder cp1252-encodable", test_reminder_is_cp1252_encodable),
        ("malformed records do not disable", test_malformed_records_do_not_disable),
        # behavioral end-to-end
        ("e2e fire blocks on stderr", test_e2e_fire_blocks_on_stderr),
        ("e2e anchored idiom-3 fires", test_e2e_anchored_idiom3_fires),
        ("e2e audit-cmd allows", test_e2e_audit_cmd_allows),
        ("e2e marker allows", test_e2e_marker_allows),
        ("e2e skip allows", test_e2e_skip_allows),
        ("e2e no-verdict allows", test_e2e_no_verdict_allows),
        ("e2e docs-editing allows", test_e2e_docs_editing_allows),
        ("e2e stop_hook_active allows", test_e2e_stop_hook_active_allows),
        ("e2e sentinel suppresses re-fire", test_e2e_sentinel_suppresses_refire),
        ("e2e empty stdin allows", test_e2e_empty_stdin_allows),
        ("e2e non-dict payload allows", test_e2e_non_dict_payload_allows),
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
