#!/usr/bin/env python3
"""Claude Code PreToolUse(Agent) hook -- blocks a nested subagent spawn that
relies on the Agent tool's implicit run_in_background default instead of an
explicit choice.

Problem (dev-env#935): the `Agent` tool defaults to `run_in_background:
true`. When a subagent spawns its own child subagent without setting
`run_in_background` explicitly, the child does not block the spawning turn
-- the parent can return before the child finishes, and the orphaned
child's completion routes to `general-purpose`/`main` instead of back to
the subagent that spawned it, silently stalling until a human notices and
manually resumes it via `SendMessage`.

Confirmed as a recurring defect, not a one-off, in career-playbook: a
2026-05-13 precedent ("3 of 10 agents stall after PDF extraction", ex-ADR-
028), the 2026-07-16 incident that produced ADR-090 (PR #749), and the
identical bug recurring three more times the SAME DAY the ADR-090 prose-
only fix shipped, before every spawn site had been updated. The fix has
been enforced only by prose since -- a reminder a subagent must correctly
re-derive and re-apply at every nesting level it itself introduces. This
hook makes it a mechanical property of the harness instead.

Preceded by an observation-only smoke-test stage (merged in #936) that
confirmed live, with real data, that this hook fires reliably for both a
top-level spawn (no `agent_id`) and a nested spawn (`agent_id` present) --
see docs/adr/126-nested-agent-spawn-background-guard.md for the smoke-test
evidence and the design rationale.

How it decides:
  1. Read stdin JSON. Fail OPEN (exit 0) on anything unparseable, or a
     `tool_name` that isn't `Agent` -- payload-shape issues unrelated to
     the guard itself.
  2. Fail OPEN if `agent_id` is absent/empty -- this is a TOP-LEVEL spawn
     (from the main session, not from within a subagent). Per Claude
     Code's own docs, `agent_id` is present only when the hook fires
     inside a subagent call -- the exact, documented nesting signal. A
     top-level spawn defaulting to background is normal, harmless, and
     the documented default pattern; it is out of scope by design.
  3. Fail OPEN if `tool_input.run_in_background` is PRESENT, with EITHER
     value. An explicit `true` is a deliberate choice -- e.g. the
     documented "a reviewer subagent that dispatches a verifier per
     finding" parallel-fan-out pattern -- and must not be punished; an
     explicit `false` is exactly the fix this hook exists to require.
     A present field also proves this session's subagent `Agent` schema
     exposes the parameter, so record a per-session sentinel saying so.
  4. Fail OPEN on an OMITTED field unless that sentinel exists (dev-env#1158).
     The harness has shipped subagent `Agent` schemas with no
     `run_in_background` parameter at all; there the field can never
     arrive, the harness picks the execution mode itself, and blocking
     would make every nested spawn impossible. A missing key alone cannot
     tell "forgot to choose" from "had no choice", but a session runs one
     harness version, so a key seen earlier in the same session settles it.
  5. Otherwise: BLOCK (exit 2) with a message explaining the mechanism and
     the one-line fix, naming `agent_id`/`agent_type` for the model to
     orient on which of its own in-flight spawns triggered it.

Fail direction: FAILS OPEN (REFERENCE.md authoring rule 5) -- a crash, an
unparseable payload, or any of the four pass-through conditions above all
allow the call. This is a narrow, high-precision gate (nested AND omitted
-- both conditions required), not a blanket restriction on `Agent` calls,
so failing open on uncertainty costs little: the failure modes it guards
against are all "silently discovered hours later," never "urgent right
now," so there is no case for failing closed the way
pre-tool-use-journal-compose-force-guard.py does for its much narrower,
already-rare trigger.

Stdin JSON shape (PreToolUse): {"session_id": <str>,"tool_name":"Agent",
"tool_input":{..., "run_in_background": <bool, optional>},"agent_id":
<present only when fired inside a subagent>,"agent_type": <str>}
"""
import json
import sys

import _hookout
import _hookutil

SENTINEL_PREFIX = "nested_agent_rib_seen_"

BLOCK_MESSAGE = """Nested Agent-tool spawn with no explicit run_in_background (agent_id={agent_id}, agent_type={agent_type}).

You are a subagent spawning your own child subagent. The Agent tool defaults to run_in_background: true. A backgrounded child spawned from inside a subagent does NOT block your turn -- you can return before it finishes, and its completion routes to general-purpose/main instead of back to you, silently stalling (career-playbook ADR-090, PR #749; dev-env#935).

Fix: if you need this call's result before your next step (the common case for an audit / gate / review / compress chain), add run_in_background: false and reissue the call.
If you are deliberately fanning this out in parallel (independent work you will collect together later), reissue with run_in_background: true explicitly -- any explicit value passes this check; only an omitted one blocks."""


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-nested-agent-background-guard")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict) or data.get("tool_name") != "Agent":
        sys.exit(0)

    agent_id = data.get("agent_id")
    if not agent_id:
        sys.exit(0)  # top-level spawn -- out of scope by design

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not _hookutil._SAFE_SESSION_ID.match(session_id):
        sys.exit(0)  # no safe key for the schema evidence -- cannot judge
    sentinel = _hookutil.sentinel_path(SENTINEL_PREFIX, session_id)

    if "run_in_background" in tool_input:
        # Explicit choice, either value -- not the failure mode. It also proves
        # this session's subagent schema offers the choice.
        if not sentinel.exists():
            try:
                sentinel.parent.mkdir(parents=True, exist_ok=True)
                sentinel.touch()
            except OSError:
                pass
            _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)
        sys.exit(0)

    if not sentinel.exists():
        sys.exit(0)  # schema may have no such parameter (dev-env#1158)

    _hookout.emit_block(BLOCK_MESSAGE.format(
        agent_id=agent_id, agent_type=data.get("agent_type", "unknown")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
