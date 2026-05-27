# ADR 007 — Hook Command Invocation: Direct `python3` vs `bash -c` Wrapper

**Date:** 2026-04-27  
**Status:** Accepted  
**Formerly:** ADR 001 (renumbered 2026-04-29 to maintain chronological order)

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

### 2026-05-26 — Switched `python3` → `py -3` ([dev-env#261](https://github.com/brownm09/dev-env/issues/261))

The failure mode this ADR predicted occurred. On this machine, `python3` resolves only to the Microsoft Store App Execution Alias stub at `C:\Users\brown\AppData\Local\Microsoft\WindowsApps\python3.exe`, which prints "Python was not found" and exits with code 49. The real Python at `C:\Users\brown\AppData\Local\Programs\Python\Python312\python.exe` is named `python.exe`, not `python3.exe`, so the literal command `python3` cannot reach it.

**Discovery path:** While debugging why `session-mode-prompt.py` was not surfacing its banner, observation that the scratch directory contained no artifacts from any hook (no `ctx-warn-*` from turn-count-hook, no `session_mode_ack.txt`) — every hook had been silently failing for an unknown duration. Confirmed `where python3` returns only the stub.

**Decision:** Switch all `settings.json` hook commands from `python3 C:/...` to **`py -3 C:/...`**.

`py.exe` is the Python Launcher for Windows, installed to `C:\Users\brown\AppData\Local\Programs\Python\Launcher\py.exe`, which is on the Windows system PATH (verified). `py -3` invokes the highest installed Python 3.x — version-agnostic across future upgrades — whereas hardcoding an absolute path (the original prescription in this ADR) breaks when Python 3.13 is installed.

**Why `py -3` rather than the absolute path prescribed in 2026-04-27:**
- Absolute path (`C:/.../Python312/python.exe`) is deterministic but breaks on every Python upgrade.
- `py -3` defers version selection to the Python Launcher, which discovers all installed Pythons and picks the latest Python 3.

---

## Decision

Invoke hook scripts as **`py -3 C:/path/to/script.py`** — no `bash -c` wrapper, no bare `python3`.

`py.exe` (the Windows Python Launcher) must be installed and on the Windows system PATH. It ships with python.org installers by default.

If a future Claude Code version invokes hooks through a different shell context (e.g., a hook runner that pre-resolves to Git Bash where `python3` is the user-shell alias), this decision may revisit. Until then, `py -3` is the only invocation that works reliably from Claude Code's non-interactive hook context on Windows.

---

## Consequences

- All hook entries in `settings.json` use `py -3 C:/...` syntax.
- The `## Testing` test commands in this repo and downstream projects also use `py -3` — running `python3 -m py_compile ...` would silently no-op on this machine.
- Any new hook added to this repo must follow the same pattern.
- If `py.exe` is missing on a future machine, install the python.org distribution which bundles the launcher.

---

## References

- Engineering journal: `sessions/dev-env/2026-04-27-workflow-discipline-sprint.md` (root cause diagnosis)
- Engineering journal: `sessions/dev-env/2026-04-28-hook-fix-and-workflow-rules.md` (PR #81 review and merge)
- PR #81: `fix: remove bash -c wrapper from hook commands`
