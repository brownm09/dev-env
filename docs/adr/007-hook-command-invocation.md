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

### 2026-06-01 (follow-up) — Suppress console flashes from hook *subprocesses* via `_winsubp` ([dev-env#297](https://github.com/brownm09/dev-env/issues/297))

The launcher swap above removed the flash from the Python launcher itself, but a residual flash remained. Hook scripts spawn `git`, `gh`, `bash`, and (in `awake-blocker.py`) `py` via `subprocess.run` / `subprocess.Popen`. Under `pythonw.exe` (no console), Windows allocates a fresh console window for any child console application unless the parent passes `CREATE_NO_WINDOW` (0x08000000) in `creationflags`. Source: [Microsoft — Process Creation Flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags) and [Python — `subprocess.CREATE_NO_WINDOW`](https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NO_WINDOW).

**Fix:** Add `claude/scripts/_winsubp.py` — on import, monkey-patches `subprocess.Popen.__init__` to OR `CREATE_NO_WINDOW` into `creationflags`. The patch is idempotent (sentinel `subprocess._winsubp_patched`), no-op on non-Windows, and wrapped in `try/except` so a future Python signature change cannot break a hook. Every hook script in `claude/scripts/` that spawns a subprocess adds one line near its imports:

```python
import _winsubp  # noqa: F401  -- suppress console windows on Windows
```

**Why monkey-patch rather than rewrite every call site:** the patch touches one well-defined seam (`Popen.__init__`) and every existing call uses `subprocess.run`/`Popen` with no `creationflags` set, so OR-merging the flag is invariant-preserving. The alternative — replacing 30+ call sites with a `helper.run(...)` wrapper — adds equal surface area in the hooks themselves while leaving any newly-added call vulnerable to the same regression.

**Verification:** `claude/scripts/tests/test_pyw_stdio.py` gained two checks: (4) under `pyw -3`, importing `_winsubp` sets the sentinel and leaves `subprocess.run` functional; (5) every subprocess-using hook script imports `_winsubp` (static scan — prevents new hooks from silently re-introducing the flash).

### 2026-06-01 (follow-up 2) — `awake-blocker` watcher: spawn via `sys.executable`, not `py -3` ([dev-env#300](https://github.com/brownm09/dev-env/issues/300))

After the `_winsubp` follow-up above, a residual `py.exe` flash persisted on every prompt. Diagnosis: `awake-blocker.py` spawned its detached watcher via `["py", "-3", str(__file__), "--watcher"]`. `_winsubp` correctly OR'd `CREATE_NO_WINDOW` into `creationflags`, so the `py.exe` launcher process itself had no console. But `py.exe` is a console-subsystem program: it then calls `CreateProcess` on `python.exe` *without* passing `CREATE_NO_WINDOW` through, and because `py.exe`'s own process had no console (thanks to our flag), Windows allocated a fresh console for the grandchild `python.exe`. Visible flash.

**Fix:** Spawn the watcher via `sys.executable` rather than the `py -3` launcher. The hook itself runs under `pyw -3` (settings.json), so `sys.executable` is `pythonw.exe` — the Windows-subsystem interpreter that allocates no console at any level. No launcher chain, no grandchild console, no flash. Also derive `WATCHER_IMAGE` (used by the `tasklist`-based PID-reuse defense in `_pid_is_watcher`) from `Path(sys.executable).name` so it tracks whatever interpreter actually spawned the watcher.

**Why not just keep `_winsubp` and trust it:** the flag only affects the immediate child Popen spawns. It does not propagate through a console-launcher intermediary. Hooks that spawn Python should prefer `sys.executable` (or `pyw -3` if the command must be a string); naming `py` re-introduces the same chain-of-console-allocation bug.

**Verification:** Test suite still passes (5/5). Cosmetic flash absence is verified manually after Claude Code reloads the patched script — there is no programmatic way to detect "no console window was allocated."

### 2026-07-02 (follow-up 3) — Default subprocess text-mode output to UTF-8 via `_winsubp` ([dev-env#503](https://github.com/brownm09/dev-env/issues/503))

`post-tool-use.py`'s `add_to_project()` crashed with `UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d` reading `gh project item-add`'s stdout, immediately after a successful `gh pr create` for PR #502. Root cause: `subprocess.run(..., text=True)` with no `encoding=` decodes a child's output using `locale.getpreferredencoding(False)` — cp1252 on this machine — rather than UTF-8. `gh`/`git` can emit UTF-8 multi-byte sequences (e.g. em-dashes or curly quotes echoed back from a PR/issue title), and cp1252 cannot represent every UTF-8 byte value, so the read crashes. The same gap existed in `canonical_root_via_git()` (same file) and `confirm_merge_via_gh()` in `_hookio.py`.

The crash happens inside `subprocess.run`'s internal stdout-reader *thread*, so it never reaches the calling code's `try/except` — Python's default unhandled-thread-exception hook prints it to stderr and the read is simply lost (`result.stdout` ends up `None`). `add_to_project()`'s own `except Exception: return None` then caught the *secondary* failure (`json.loads(None)`), which is why the hook's user-visible symptom was the generic "auto-add to project failed" fallback rather than a stack trace pointing at the real cause.

**Fix:** Extend the same `_winsubp.py` seam the 2026-06-01 follow-up above established, rather than editing each call site. `_apply_windows_subprocess_defaults` (the creationflags-merge logic, now extracted into a standalone pure function) also defaults `encoding="utf-8", errors="replace"` when a call requests text mode (`text=True` / `universal_newlines=True`) and doesn't already specify an encoding. An explicit `encoding=` (or `errors=`) from the caller is never overridden.

**Why extend `_winsubp` rather than fix the 3 known call sites:** identical reasoning to the 2026-06-01 follow-up above — one well-defined seam, invariant-preserving (no existing call passes `encoding=`, so defaulting it is safe), and it protects every subprocess-using script in the repo for free, since `test_pyw_stdio.py` already requires all of them to import `_winsubp`. Verified: a repo-wide scan at fix time found the identical `text=True`-without-`encoding=` gap in ~20 other scripts; extending `_winsubp` closed all of them in one change instead of ~20 individual edits.

**Also fixed:** `_hookio.py`'s `confirm_merge_via_gh` used `subprocess.run` without `_hookio.py` itself importing `_winsubp` — it was only protected because every current caller happens to import `_winsubp` first. Added the import directly to `_hookio.py` so the module is self-sufficiently correct regardless of caller diligence. (The blanket underscore-prefixed-file exemption in `test_every_subprocess_using_hook_imports_winsubp`, which is why this gap wasn't already caught statically, is tracked as a separate follow-up rather than fixed here.)

**Verification:** `claude/scripts/tests/test_winsubp.py` (new) pins `_apply_windows_subprocess_defaults`'s behavior offline — pure-function tests, no subprocess spawn, matching this repo's test convention. `test_pyw_stdio.py` gained a 7th check that reproduces the exact reported crash end-to-end: a `pyw -3` child spawns a grandchild that writes byte `0x9d` (the exact byte from the dev-env#503 traceback) and asserts it decodes to U+FFFD instead of being silently lost.

---

## Decision

Invoke hook scripts as **`pyw -3 C:/path/to/script.py`** in `settings.json` hook command entries — no `bash -c` wrapper, no bare `python3`, no `py -3` (which flashes a console window per spawn).

Outside of `settings.json` hook commands, continue to use `py -3` (shell-invoked contexts do not flash).

`py.exe` and `pyw.exe` both ship with the python.org installer's Python Launcher for Windows.

If a future Claude Code version invokes hooks through a different shell context (e.g., a hook runner that pre-resolves to Git Bash where `python3` is the user-shell alias), this decision may revisit. Until then, `pyw -3` is the invocation that works reliably from Claude Code's non-interactive hook context on Windows without flashing a console.

---

## Consequences

- All hook command entries in `settings.json` use `pyw -3 C:/...` syntax.
- Shell-invoked Python (the `## Testing` command, skill docs, the `pre-push` hook) continues to use `py -3`. Note that `python3` still resolves to the Microsoft Store App Execution Alias on this machine — shell commands must use `py -3`, not `python3`, or they will silently no-op (e.g., `python3 -m py_compile ...` produces no error and no compiled output).
- Any new hook added to this repo must follow the `pyw -3` pattern for its `settings.json` entry.
- Any new hook that imports `subprocess` (directly or indirectly) **must** add `import _winsubp` near the top of its imports. The static test in `test_pyw_stdio.py` fails the build if a subprocess-using hook ships without it.
- Any hook in `claude/scripts/` that spawns Python as a subprocess **must** use `sys.executable` (or `pyw -3` if the command must be a literal string), never `py` or `py -3`. Naming `py` chains through the `py.exe` console-subsystem launcher, which spawns `python.exe` without propagating `CREATE_NO_WINDOW` — Windows then allocates a fresh console for the grandchild, re-introducing the flash that `_winsubp` thought it had eliminated. Enforced by `test_pyw_stdio.py` test 6 (AST-based scan).
- If `py.exe` or `pyw.exe` is missing on a future machine, install the python.org distribution which bundles the launcher.
- Any text-mode (`text=True` / `universal_newlines=True`) `subprocess.run`/`Popen` call made anywhere in a process that has imported `_winsubp` is decoded as UTF-8 with `errors="replace"` by default, unless the caller explicitly passes its own `encoding=`. New subprocess calls do **not** need to (and should not redundantly) pass `encoding="utf-8"` themselves. This is process-wide, not per-file: it takes effect once *anything* in the process has imported `_winsubp` (typically the entry-point hook script, near its top), not only in the file that happens to make the call — see `_hookio.py`'s subprocess call for an example that relies on this. `test_winsubp.py`'s tests of `_apply_windows_subprocess_defaults` are the source of truth for the exact defaulting rules (creationflags OR-merge + encoding/errors default, both caller-overridable).

---

## References

- Engineering journal: `sessions/dev-env/2026-04-27-workflow-discipline-sprint.md` (root cause diagnosis)
- Engineering journal: `sessions/dev-env/2026-04-28-hook-fix-and-workflow-rules.md` (PR #81 review and merge)
- PR #81: `fix: remove bash -c wrapper from hook commands`
