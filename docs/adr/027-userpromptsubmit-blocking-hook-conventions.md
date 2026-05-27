# ADR 027 — UserPromptSubmit Hook Output: stderr for Blocking, Per-Session Marker Files

**Date:** 2026-05-27
**Status:** Accepted
**Tags:** hooks, UserPromptSubmit, stderr, per-session-state, claude-code-contract

---

## Context

`session-mode-prompt.py` (added 2026-04-13, refined through dev-env #241, #243, #249, #250, #258, #260, #262) shipped with two latent defects that were masked for over a month by the `python3`-stub hook-execution failure documented in [ADR-007](007-hook-command-invocation.md). Once #262 restored hook execution, the defects became observable.

### Defect 1 — Banner written to stdout, invisible to the user

The hook wrote its mode-confirmation banner via `print(...)` (later `sys.stdout.write(...)`), then exited 2. Per Claude Code's `UserPromptSubmit` hook contract:

> Exit code 2: a blocking error. **stderr is fed back to Claude to process**, and any output on stdout is shown to the model as additional context.

Source: [Anthropic — Claude Code Hooks documentation, "Hook input and output"](https://docs.claude.com/en/docs/claude-code/hooks#hook-input-output).

The original implementation got this exactly backwards. The banner was being silently routed into Claude's prompt context as added text rather than surfaced to the human, with no visible artifact in the terminal. The user reported "no banner appeared" for every session for the full lifetime of the hook.

### Defect 2 — Cross-session marker contamination

A single shared file at `scratch/session_mode_ack.txt` was used as the cooldown marker, with a 120-second age threshold so that the user's re-submit after seeing the banner would pass through. The marker was global. Observed in `scratch/session-mode-prompt.log` after #262:

```
01:07:10  session A  /review 352  → banner_printed, marker written
01:08:19  session B  Test         → cooldown_passthrough (read A's 68s-old marker)
01:08:41  session B  "...banner?" → cooldown_passthrough (read A's 91s-old marker)
01:09:13  session C  Test 2       → banner_printed (marker finally aged past 120s)
```

Two fresh sessions opened in quick succession after a third silently lost their banner. The 120-second cooldown — meant to handle the user's re-submit within a single session — was suppressing the banner across sessions.

---

## Decision

### Output stream

`UserPromptSubmit` hooks that intend to display a message to the **user** must write to `stderr` and exit 2. Writing to `stdout` and exiting 2 silently feeds the message into the model's context, invisible to the human.

```python
sys.stderr.write(banner)
sys.stderr.flush()
sys.exit(2)
```

The same rule applies to any blocking hook that surfaces user-visible output:
- `PreToolUse` exit-2 messages → stderr (or the `{"reason": "..."}` JSON payload, which is the preferred mechanism for tool-call blocks because it lets Claude see the reason).
- `UserPromptSubmit` exit-2 messages → stderr.
- Exit-0 hooks that emit a `systemMessage` field via JSON output → use the documented JSON protocol, not stdout text.

Stdout from a hook is never a user-visible channel. If a hook needs to inject context into the model's input, that is what stdout is for. If a hook needs to talk to the human, stderr is the only correct choice.

### Per-session state

Per-session state files (markers, sentinels, flags) must be keyed by `session_id`. The Claude Code hook payload supplies `session_id` as a UUID; use it directly as part of the filename:

```python
def _marker_path(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "") or "unknown"
    return f"{MARKER_DIR}/session_mode_ack_{safe}.txt"
```

Global per-machine markers (e.g. `session_mode_ack.txt` with no session suffix) are forbidden for any state intended to scope to a session. The only correct global markers are ones that genuinely span sessions (e.g. cross-session caches that all sessions should see).

Old per-session markers are orphaned but harmless when their session closes. A periodic sweep that removes markers older than N days can be added if disk usage becomes a concern — at ~18 bytes per marker and on the order of dozens of sessions per day, it is not currently a concern.

### Cooldown semantics

The original 120-second cooldown was a workaround for the global-marker bug — it bounded the cross-session suppression window. With per-session markers, the cooldown is unnecessary: marker existence alone (for the current session) is the correct signal, and it covers the user's re-submit case exactly as well as the time-bounded version did.

---

## Consequences

- `claude/scripts/session-mode-prompt.py` rewritten: banner → stderr; marker path → per-session.
- `docs/REFERENCE.md` updated with the corrected behavior description.
- Any future blocking hook author follows the two invariants from this ADR. The Hook authoring rules in `docs/REFERENCE.md` reference this ADR.
- Hook-debug log entries now include `marker_path` so cross-session contamination would be obvious in future diagnostics.

---

## References

- [Anthropic — Claude Code Hooks documentation](https://docs.claude.com/en/docs/claude-code/hooks) — defines the `UserPromptSubmit` exit-2 contract (stdout = added context, stderr = surfaced to user).
- [ADR-007](007-hook-command-invocation.md) — the related amendment chain that restored hook execution and made these defects observable.
- [dev-env #264](https://github.com/brownm09/dev-env/issues/264) — the defect report with the timeline that produced this ADR.
