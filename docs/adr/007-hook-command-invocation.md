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

### 2026-06-01 — Switched hook commands `py -3` → `pyw -3` ([dev-env#294](https://github.com/brownm09/dev-env/issues/294))

`py.exe` invokes `python.exe`, which is built against the Windows console subsystem. Each hook spawn briefly allocates and tears down a console window — visible as a flash on every prompt and tool call. With ~25 hooks across PreToolUse, UserPromptSubmit, Stop, PostToolUse, etc., the flashes are frequent enough to be a persistent cosmetic annoyance.

**Fix:** Swap `py -3` → `pyw -3` in `settings.json` hook commands only. `pyw.exe` is the Python Launcher's windowless variant — it invokes `pythonw.exe` (Windows subsystem), so no console allocation. Stdin/stdout/stderr still work normally over the pipes Claude Code wires to the subprocess.

**Scope:** Hook command entries only. Left unchanged:
- `Bash(py -3 *)` permission entries — these govern Claude running `py` via the Bash tool, which executes inside the existing Git Bash shell and does not pop a window.
- The `## Testing` command and skill/script `py -3` examples — these run from a shell.
- The `pre-push` hook — runs from a shell (`git push` context).

---

## Decision

Invoke hook scripts as **`pyw -3 C:/path/to/script.py`** in `settings.json` hook command entries — no `bash -c` wrapper, no bare `python3`, no `py -3` (which flashes a console window per spawn).

Outside of `settings.json` hook commands, continue to use `py -3` (shell-invoked contexts do not flash).

`py.exe` and `pyw.exe` both ship with the python.org installer's Python Launcher for Windows.

If a future Claude Code version invokes hooks through a different shell context (e.g., a hook runner that pre-resolves to Git Bash where `python3` is the user-shell alias), this decision may revisit. Until then, `pyw -3` is the invocation that works reliably from Claude Code's non-interactive hook context on Windows without flashing a console.

---

## Consequences

- All hook command entries in `settings.json` use `pyw -3 C:/...` syntax.
- Shell-invoked Python (the `## Testing` command, skill docs, the `pre-push` hook) continues to use `py -3`.
- Any new hook added to this repo must follow the `pyw -3` pattern for its `settings.json` entry.
- If `py.exe` or `pyw.exe` is missing on a future machine, install the python.org distribution which bundles the launcher.

---

## References

- Engineering journal: `sessions/dev-env/2026-04-27-workflow-discipline-sprint.md` (root cause diagnosis)
- Engineering journal: `sessions/dev-env/2026-04-28-hook-fix-and-workflow-rules.md` (PR #81 review and merge)
- PR #81: `fix: remove bash -c wrapper from hook commands`
