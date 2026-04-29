# ADR 007 — Hook Command Invocation: Direct `python3` vs `bash -c` Wrapper

**Date:** 2026-04-27  
**Status:** Accepted

---

## Context

Claude Code hook commands in `settings.json` are spawned by the Claude Code Desktop process, which runs in a non-interactive Windows context (not a Git Bash shell). This creates a PATH ambiguity for `python3`:

- **Git Bash PATH** — resolves `python3` to the real Python binary managed by the user's shell profile.
- **Windows system PATH** — resolves `python3` to the Windows App Execution Alias stub (`C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\python3.exe`), which redirects to the Microsoft Store and cannot run scripts.

Separately, `bash.exe` (Git Bash) is on the *Git Bash PATH* but **not** on the *Windows system PATH*.

---

## Decision History

### 2026-04-19 — Wrapped in `bash -c '...'`

All hook commands were wrapped as `bash -c 'python3 /path/to/script.py'` to re-enter the Git Bash PATH and avoid the Windows App Execution Alias stub.

### 2026-04-27 — Removed `bash -c` wrapper (PR #81)

The `bash -c` wrapper itself became the problem: Claude Code's hook runner could not locate `bash.exe` because it is not on the Windows system PATH. Every hook invocation failed, and `PreToolUse` failures blocked all `Bash` tool calls for the session.

**Root cause:** `bash -c '...'` requires `bash` to be resolvable from the Windows system PATH. It is not — only Git Bash's own PATH (which the hook runner never loads) contains it.

**Fix:** Removed the `bash -c` wrapper. Hook commands invoke `python3` directly, relying on Claude Code resolving it correctly.

---

## Decision

Invoke hook scripts as **`python3 C:/path/to/script.py`** — no `bash -c` wrapper.

If `python3` resolves to the Windows App Execution Alias stub in a future Claude Code version, the correct fix is to use an absolute path to the Python binary (e.g., `C:/Users/<user>/AppData/Local/Programs/Python/Python3x/python.exe`) — not to re-introduce the `bash -c` wrapper.

---

## Consequences

- All hook entries in `settings.json` use bare `python3 C:/...` syntax.
- Any new hook added to this repo must follow the same pattern — no `bash -c` wrapper.
- If the Python resolution breaks again, check the Claude Code hook runner's PATH before adding wrappers.

---

## References

- Engineering journal: `sessions/dev-env/2026-04-27-workflow-discipline-sprint.md` (root cause diagnosis)
- Engineering journal: `sessions/dev-env/2026-04-28-hook-fix-and-workflow-rules.md` (PR #81 review and merge)
- PR #81: `fix: remove bash -c wrapper from hook commands`
