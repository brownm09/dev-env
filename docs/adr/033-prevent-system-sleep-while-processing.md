# ADR-033 — Prevent System Sleep While Claude Is Processing

**Date:** 2026-05-29
**Status:** Accepted
**Closes:** [dev-env#288](https://github.com/brownm09/dev-env/issues/288)
**Tags:** hooks, windows, sleep, UserPromptSubmit, Stop, Notification, background-process
**Related:** [ADR-006](006-dev-env-sync-on-every-prompt.md), [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md)

---

## Context

On Windows, long Claude turns (large agent fan-out, multi-file edits, CI polling) can run past the system idle-sleep timeout. When the machine sleeps, the Claude process tree is suspended; on wake, the session must resume from a degraded state. Manually toggling sleep settings or launching [Microsoft PowerToys Awake](https://learn.microsoft.com/en-us/windows/powertoys/awake) before every long session is friction, and global "never sleep" is overkill — sleep is desirable when Claude is idle (waiting on input or permission).

Three constraints shape any hook-based approach:

1. **Hook scripts are short-lived child processes.** Calling Windows' [`SetThreadExecutionState`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate) directly from a hook does nothing — the flag clears on process exit. The wake-lock must be held by a persistent process the hook spawns.
2. **Orphan-safety.** If the Claude session crashes or is killed, no Stop hook fires. A naive long-lived watcher would leave the machine permanently awake. The watcher needs a heartbeat-with-timeout.
3. **Idempotency.** UserPromptSubmit fires on every prompt. Spawning a new watcher each time would leak processes; spawning a watcher when one is already healthy is wasted work.

---

## Decision

Add `claude/scripts/awake-blocker.py`, a single script with three modes:

- **`start` mode** — invoked from `UserPromptSubmit`. Touches a sentinel file (`scratch/awake.lock`) to refresh its mtime as a heartbeat. If `scratch/awake.pid` names a live process **whose image name is `py.exe`**, returns immediately. The image-name filter defeats Windows PID-reuse false positives: a dead watcher's PID can be recycled for an unrelated process, and a PID-only check would silently skip respawning. Otherwise spawns a detached watcher (`subprocess.Popen` with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`) running this same script with `--watcher`, and writes the watcher PID to `awake.pid`.
- **`stop` mode** — invoked from `Stop` and (subtype-aware) `Notification`. Deletes the sentinel file. The watcher's next poll (within 1 second) observes the missing sentinel, clears the execution-state flag, and exits.
- **`--watcher` mode** — the detached background loop. Every second: check sentinel exists; check sentinel mtime is within 30 minutes; call `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` every 30 seconds (no need to call on every poll). On loop exit: explicitly clear the flag via `SetThreadExecutionState(ES_CONTINUOUS)` and remove `awake.pid`.

Wire into `settings.json` hooks:

- `UserPromptSubmit` — append `awake-blocker.py`
- `Stop` — append `awake-blocker.py`
- `Notification` — register `awake-blocker.py` (new hook block)

The hook entry-point dispatches on the `hook_event_name` field from the stdin JSON Claude Code passes to every hook ([hooks reference](https://docs.claude.com/en/docs/claude-code/hooks)).

---

## Rationale

**Why a sentinel file rather than a pipe/socket.** Files survive crashes; a pipe/socket requires a coordinated handshake to clean up. File mtime gives us the heartbeat for free.

**Why a 30-minute heartbeat timeout.** Long enough that the largest realistic single turn (multi-agent workflow, CI poll loop) will not stale out, short enough that an orphaned watcher cannot keep the machine awake overnight. The heartbeat is refreshed on every UserPromptSubmit, so any interactive session resets the timer.

**Why `ES_SYSTEM_REQUIRED` only, not `ES_DISPLAY_REQUIRED`.** The goal is to keep computation running, not to keep the monitor lit. Display sleep is a separate user-visible behavior and should follow the user's power-plan settings.

**Why three hook events, not two.** `Stop` fires when Claude's turn ends. `Notification` fires when Claude is explicitly waiting on user input or permission — same semantic ("Claude is idle") but happens mid-turn. Both should release the lock so the machine can sleep during a permission-prompt pause.

**Why subtype-aware Notification handling.** `Notification` may fire for non-idle reasons (status surfaces, transient banners). The hook inspects the payload's `notification_type` / `type` / `subtype` / `kind` field and only releases the wake-lock on idle/permission-wait subtypes. If no subtype field is present, it falls back to releasing — preserving the original behavior on Claude Code builds whose payload lacks the field.

**Why an explicit non-Windows short-circuit.** Without it, every UserPromptSubmit on Linux/macOS would invoke `Popen(["py", "-3", ...])` with Windows-only `creationflags`, the safe-exit guard would catch the failure, and a `hook error:` line would accumulate in `awake.log` on every prompt. The platform check at the top of `main()` makes the hook a true no-op off-Windows: no log line, no Popen, no error.

**Why not PowerToys Awake.** PowerToys Awake is an external dependency and provides no hook-driven start/stop. The ctypes approach is self-contained, ~150 lines, and ships with the dev-env repo.

**Why a single script with mode dispatch instead of three.** The watcher and the hook entry-point share the sentinel/PID-file paths and constants. Splitting would duplicate that contract. The script is small enough that the dispatch is trivial.

---

## Alternatives considered

- **`powercfg /requestsoverride PROCESS claude.exe SYSTEM DISPLAY`** — persistent across reboots, no hook needed. Rejected: blanket "never sleep when Claude is installed" defeats the goal of allowing idle sleep.
- **PowerToys Awake CLI from hooks** — `Start-Process PowerToys.Awake.exe --time-limit 0` from start hook, `Stop-Process` from stop hook. Rejected: external dependency, PID-file management still needed, no equivalent heartbeat-with-timeout safety.
- **Per-turn PowerShell wrapper** (`claude-awake` function in `$PROFILE`) — works without hooks but requires the user to remember to invoke a different command. Useful as a fallback for users who do not want hook spawn behavior; not mutually exclusive with this ADR.

---

## Consequences

**Positive:**
- Long Claude turns complete without hitting Windows sleep.
- Idle waits (waiting on user, after Stop) allow normal sleep.
- Crash-safe: orphaned watcher self-terminates within 30 minutes.
- No external dependencies — pure stdlib + ctypes.

**Negative:**
- Windows-only. The hook short-circuits cleanly on other platforms via a `sys.platform == "win32"` check in `main()` — no log line, no Popen, no error — but the feature provides no value outside Windows.
- One additional hook script in three hook events. Hook overhead is ~50ms per invocation per [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) measurements; the start path is a fast PID-alive check when a watcher is already running.
- A detached background `py.exe` process appears in Task Manager while a session is active. Documented behavior.
- External CI waiting after `Stop` fires (user has returned control to Claude, Claude has returned, user is now waiting on remote CI) is not covered — hooks have no signal for "user is watching CI". Out of scope.
- **Multi-session sentinel is shared.** Two concurrent Claude sessions share a single `awake.lock` / `awake.pid`. If session A's `Stop` fires while session B is still mid-turn, the watcher exits within ~1 s; session B's next `UserPromptSubmit` respawns a watcher, but the gap window is real. Acceptable for single-session use; multi-session use should accept brief lapses or extend the script to track active session IDs in the sentinel.
- `awake.log` is single-generation rotated at 256 KiB (`awake.log` → `awake.log.1`). Older history is overwritten.
