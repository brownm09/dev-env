#!/usr/bin/env python3
"""Claude Code PreToolUse(Agent) hook -- SMOKE TEST STAGE (observation-only).

Problem this hook will eventually solve (dev-env#935): the `Agent` tool
defaults to `run_in_background: true`. When a subagent spawns its own child
subagent without setting `run_in_background` explicitly, the child does not
block the spawning turn -- the parent can return before the child finishes,
and the orphaned child's completion routes to `general-purpose`/`main`
instead of back to the subagent that spawned it, silently stalling until a
human notices and manually resumes it via `SendMessage`. Confirmed recurring
in career-playbook (ADR-090, PR #749, and a still-earlier 2026-05-13
precedent) despite a prose-only fix.

THIS STAGE does not enforce anything -- it only confirms, empirically, that
a `PreToolUse` hook matching the `Agent` tool actually fires for nested
spawns (i.e. that `agent_id` is present in the payload when the calling
context is itself a subagent), including during an unattended/scheduled
run, before any real blocking logic is written. Always exits 0; cannot
block or otherwise affect any tool call.

Once confirmed live (interactively, and during one real unattended
`/batch-cover-letters`-style run), this script body is replaced with the
real enforcement logic in a follow-up PR -- see the dev-env#935 issue and,
once written, `docs/adr/<N>-nested-agent-spawn-background-guard.md`.

Fail direction: FAILS OPEN unconditionally -- this stage never blocks
anything, so there is no fail-closed path to speak of.

Stdin JSON shape (PreToolUse): {"tool_name":"Agent","tool_input":{...},
"agent_id": <present only when fired inside a subagent>, "agent_type": ...}
"""
import json
import os
import sys
import time

import _hookutil

_LOG_PATH = os.path.join(
    os.path.expanduser("~"), ".claude", "scratch", "agent_hook_smoketest.jsonl"
)


def _append_log(record: dict) -> None:
    """Best-effort append -- never let a logging failure affect the hook's
    own fail-open guarantee (mirrors _hookutil.record_heartbeat's own
    best-effort I/O philosophy)."""
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        pass


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-nested-agent-background-guard")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    tool_input = data.get("tool_input")
    _append_log({
        "observed_at": time.time(),
        "tool_name": data.get("tool_name"),
        "agent_id": data.get("agent_id"),
        "agent_type": data.get("agent_type"),
        "run_in_background_present": isinstance(tool_input, dict) and "run_in_background" in tool_input,
        "run_in_background_value": (tool_input or {}).get("run_in_background") if isinstance(tool_input, dict) else None,
    })
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
