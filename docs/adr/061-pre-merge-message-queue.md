# ADR-061: Pre-Merge User Message Queue

**Date:** 2026-06-28  
**Status:** Accepted  
**Tags:** hooks, pre-tool-use, merge, workflow, bypass-mode, feedback, global-rule

---

## Context

In bypass permissions mode, Claude executes multi-step workflows — including PR merges — without pausing for user approval. The user may form opinions mid-session that they want Claude to act on before the merge executes, but interrupting an autonomous session to deliver that feedback is disruptive and easy to miss.

No prior mechanism existed for the user to queue asynchronous feedback that would be reliably surfaced at the merge checkpoint.

---

## Decision

Introduce a **machine-local user message queue** at `C:/Users/brown/.claude/merge-queue.md` and a **`PreToolUse` hook** (`pre-merge-message-check.py`) that reads it before any `gh pr merge` command.

**Hook behaviour:**
- Detects `gh pr merge` using the same proven regex as `pre-merge-findings-gate.py`.
- Reads the queue file; if it contains non-whitespace content, **blocks the merge (exit 2)** and writes the messages to stderr.
- Missing file, empty file, whitespace-only file, or any I/O error → exit 0 (fail open, merge proceeds).

**Workflow:**
1. User writes feedback to `C:/Users/brown/.claude/merge-queue.md` at any point during the session.
2. When Claude attempts `gh pr merge`, the hook fires and surfaces the messages.
3. Claude reads the messages, acts on them, **clears the queue file** (writes empty content), then re-attempts `gh pr merge`.
4. Second attempt: queue is empty → hook exits 0 → merge proceeds.

The hook fires **before** `pre-merge-findings-gate.py` so user feedback is surfaced first.

---

## Consequences

**Benefits:**
- User feedback is never silently missed at merge time, even in fully autonomous sessions.
- Zero friction when no messages are queued — empty queue → hook is silent.
- Fail-open design: a misconfigured or unwritable queue file never permanently wedges a merge.

**Tradeoffs:**
- Claude must remember to clear the queue after acting; a forgotten non-empty queue will re-block the next unrelated merge. Mitigation: the hook message instructs Claude to clear it explicitly.
- The queue file is machine-local and not version-controlled — feedback is ephemeral. This is intentional (the queue is a session-level communication channel, not a durable record).

---

## Alternatives Considered

**Interrupt + prompt:** The user could interrupt the session to type feedback. Rejected — disrupts autonomous workflow; the whole motivation is to avoid the interrupt.

**PostToolUse read-after-merge:** Surfacing messages after the merge is too late — the merge has already executed and any feedback about what to change before merging is moot.

**Dedicated queue tool:** A structured JSON queue with timestamps and message IDs. Rejected — over-engineered for the use case; a plain markdown file is easier for the user to write quickly and easier for Claude to display.
