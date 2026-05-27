# ADR 027 — UserPromptSubmit Hook Output: stderr for Blocking, Per-Session Marker Files

**Date:** 2026-05-27
**Status:** Accepted
**Tags:** hooks, UserPromptSubmit, stderr, per-session-state, claude-code-contract

---

## Context

`session-mode-prompt.py` (added 2026-04-13, refined through dev-env #241, #243, #249, #250, #258, #260, #262) shipped with two latent defects that were masked for over a month by the `python3`-stub hook-execution failure documented in [ADR-007](007-hook-command-invocation.md). Once #262 restored hook execution, the defects became observable.

### Defect 1 — Banner written to stdout, which Claude Code ignores on exit 2

The hook wrote its mode-confirmation banner via `print(...)` (later `sys.stdout.write(...)`), then exited 2. Per [Claude Code's hook documentation](https://code.claude.com/docs/en/hooks):

- *"Exit 2 means a blocking error. Claude Code ignores stdout and any JSON in it. Instead, stderr text is fed back to Claude as an error message."*
- For `UserPromptSubmit` specifically, exit 2 *"Blocks prompt processing and erases the prompt."*

So when the hook exited 2 with the banner on stdout: stdout was discarded, the user's prompt was erased, and **no information reached either the user or the model**. The hook fired correctly, the log recorded `banner_printed`, and the user saw nothing — their prompt simply vanished. This matches the user's report that "nothing happened" in fresh sessions after #262 made the hooks executable.

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

`UserPromptSubmit` hooks that intend to block a prompt and surface a message must write to `stderr` and exit 2. Per the documented contract: on exit 2, Claude Code ignores stdout entirely; stderr is forwarded to Claude as the blocking error message, which Claude then incorporates into its next response to the user.

```python
sys.stderr.write(banner)
sys.stderr.flush()
sys.exit(2)
```

Stdout-on-exit-2 is silently discarded. A hook that needs to **add context** to the prompt (rather than block it) must exit 0 instead, and either print to stdout as plain text or emit a `hookSpecificOutput.additionalContext` JSON field per the [Claude Code hook input/output spec](https://code.claude.com/docs/en/hooks). These are two distinct control flows; conflating them by trying to "show a banner via stdout + exit 2" produces silent failure.

Same applies to other blocking hooks:
- `PreToolUse` exit 2 → use the JSON `{"reason": "..."}` payload (preferred — explicitly designed for tool-call blocks) or stderr.
- Any other blocking hook → stderr.

### Per-session state

Per-session state files (markers, sentinels, flags) must be keyed by `session_id`. The Claude Code hook payload supplies `session_id` as a UUID; use it directly as part of the filename:

```python
def _marker_path(session_id, event=None):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")
    if not safe:
        safe = "unknown"
        if event is not None:
            event["fallback_marker"] = True
    return f"{MARKER_DIR}/session_mode_ack_{safe}.txt"
```

Global per-machine markers (e.g. `session_mode_ack.txt` with no session suffix) are forbidden for any state intended to scope to a session — they will cross-contaminate, as Defect 2 demonstrated. The only correct global markers are ones that genuinely span sessions (e.g. cross-session caches that all sessions should see).

When the hook payload omits `session_id` (the current contract guarantees it), the fallback writes to `session_mode_ack_unknown.txt`. The diagnostic log marks the event with `fallback_marker: true` so a future contract change is visible in `scratch/session-mode-prompt.log` rather than silently re-introducing cross-session contamination.

Old per-session markers are orphaned but harmless when their session closes (~18 bytes each). A periodic sweep can be added if disk usage becomes a concern; not currently a concern.

### Cooldown semantics

The original 120-second cooldown was a workaround for the global-marker bug — it bounded the cross-session suppression window. With per-session markers, the cooldown is unnecessary: marker existence alone (for the current session) is the correct signal, and it covers the user's re-submit case exactly as well as the time-bounded version did.

---

## Consequences

- `claude/scripts/session-mode-prompt.py` rewritten: banner → stderr; marker path → per-session; `fallback_marker` log field added.
- `docs/REFERENCE.md` updated with the corrected behavior description.
- Any future blocking hook author follows the two invariants from this ADR. The Hook authoring rules in `docs/REFERENCE.md` reference this ADR.
- Hook-debug log entries now include `marker_path` and (when applicable) `fallback_marker` so cross-session contamination or contract regressions would be obvious in future diagnostics.

---

## References

- [Anthropic — Claude Code Hooks documentation](https://code.claude.com/docs/en/hooks) — defines the exit-2 contract (stdout ignored, stderr forwarded to Claude as the blocking error message; `UserPromptSubmit` exit 2 erases the prompt) and the exit-0 `hookSpecificOutput.additionalContext` JSON for injecting context alongside the prompt.
- [ADR-007](007-hook-command-invocation.md) — the related amendment chain that restored hook execution and made these defects observable.
- [dev-env #264](https://github.com/brownm09/dev-env/issues/264) — the defect report with the timeline that produced this ADR.
- [dev-env #268](https://github.com/brownm09/dev-env/issues/268) — the follow-up defect that produced the 2026-05-27 amendment below.

---

## Amendment 2026-05-27 (issue #268) — choose hook contract by intent

After #266 shipped (per-session marker + banner to stderr), two fresh sessions verified the hook:
the banner was still invisible to the user, and the user's prompt vanished. Re-reading the Claude
Code hook docs surfaced the real design error: `session-mode-prompt.py` was never a *block* — its
goal is a one-time advisory reminder. Forcing that goal through the exit-2 contract was wrong
regardless of which stream the banner was written to. Exit 2 on `UserPromptSubmit` deletes the
prompt and forwards stderr to *Claude* as an error message, which Claude cannot meaningfully act on
when the prompt itself has been erased. The user sees nothing useful; the session stalls.

### Rule: choose the hook contract by intent

For `UserPromptSubmit` hooks:

- **Block the prompt** (refuse it with a reason — e.g., the prompt contains a secret, the prompt
  violates a policy): write the reason to **stderr** and **exit 2**. The prompt is erased, the
  reason is forwarded to Claude as a blocking error, Claude surfaces it to the user.
- **Inject context alongside the prompt** (advisory, reminder, link, pre-fetched data — anything
  that should travel *with* the prompt rather than replace it): write a JSON object to **stdout**
  and **exit 0**:
  ```python
  json.dumps({
      "hookSpecificOutput": {
          "hookEventName": "UserPromptSubmit",
          "additionalContext": "<reminder text>",
      }
  })
  ```
  The prompt is preserved, the `additionalContext` is delivered to Claude alongside it, Claude
  weaves the advisory into its response naturally. See the [Claude Code Hooks documentation](https://code.claude.com/docs/en/hooks)
  for the full input/output schema.

Per-session markers, XML-tag automated-session suppression, and the diagnostic log remain
unchanged — they are orthogonal to which output contract is used.

### Consequences (amendment)

- `claude/scripts/session-mode-prompt.py` rewritten again: stdout JSON + exit 0 (no longer a
  blocking hook). Docstring updated. Log stage renamed `banner_printed` → `additional_context_emitted`.
- The stderr-on-exit-2 rule from the original 2026-05-27 decision **still stands** for hooks that
  genuinely intend to block a prompt. The amendment adds the second rule (exit-0 + additionalContext
  for advisory output), not a replacement.
- `docs/REFERENCE.md` and `README.md` updated to describe the corrected behavior.
