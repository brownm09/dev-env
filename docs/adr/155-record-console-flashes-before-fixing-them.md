# ADR-155: Record Console Flashes and Their Source Before Choosing a Fix

**Date:** 2026-11-05
**Status:** Accepted
**Closes:** [dev-env#1225](https://github.com/brownm09/dev-env/issues/1225)
**Tags:** console-flash, windows, focus, scheduled-task, measurement, observability, privacy, win32, adr-007, adr-041, adr-079
**Related:** [ADR-007](007-hook-command-invocation.md), [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), [ADR-079](079-backup-restore-convention.md), [ADR-144](144-gate-calibration-pass-3-dimension.md)

---

## Context

Brief console windows still flash on this machine and can take keyboard focus. [ADR-007](007-hook-command-invocation.md) stopped them for Python hooks, through `pyw -3` and `_winsubp.py`. It does not cover commands a session launches, scheduled tasks, or the app's own spawns.

A flash can't be traced after the fact. The window is gone in a fraction of a second and its owner process has often exited, so a person sees that something happened but not what. A read-only check of 46 top-level windows under `claude.exe` found one visible, which says nothing about windows that come and go.

On this machine the default terminal is "Let Windows decide", and a new console opens as a real Windows Terminal window. The [SetForegroundWindow rules](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow) let a process take the foreground once the foreground lock time-out has expired, and that time-out here is 200 seconds. A flash can therefore take focus and not just draw a window. That link between the time-out and any one flash is inferred from the documentation, not measured.

## Decision

1. **Measure first.** An always-on recorder, `claude/scripts/window-flash-monitor.py`, runs from a hidden logon task. Its report decides which fixes get filed. It changes nothing about how windows open.
2. **Keep a process history so an exited process can still be named.** The monitor snapshots processes every 0.5 seconds and again when a console window appears, keeping each entry for 60 seconds after it exits. Each flash record carries the processes that had just started.
3. **Name the source from what each window reports.** A classic console window and a `PseudoConsoleWindow` report the program using them as their owner. A Windows Terminal window reports only itself, so the fallback is the program named in its title, matched against the processes that had just started. Anything else is reported as unattributed and never as "not Claude".
4. **Log as little as possible.** Titles are kept only for windows descended from `claude.exe`. A console window keeps only the program name found in its title. Other windows aren't logged, apart from the one foreground change that ends a flash's time in front, which has no title. This is narrower than logging every focus change, which would be a record of what the user was doing.
5. **Watch young console windows directly instead of hooking every hide and name change.** The monitor hooks only show and foreground events, through [`SetWinEventHook`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook). While a console window is under 5 seconds old, the loop checks it every 20 ms.
6. **Say what wasn't seen.** The report states the times the monitor was recording, and a missing log is an error rather than zero flashes.

## Calibration

The monitor reports and never blocks, so it is not a gate, but its count is only useful if it can be trusted. `test_window_flash_monitor.py --live` checks both directions against real windows:

| # | Reference | Input | Expected | Measured |
|---|---|---|---|---|
| 1 | Known-bad | `cmd.exe` started with `CREATE_NEW_CONSOLE` | One flash, with the test's process in its chain | 1, `cmd.exe < python.exe < py.exe` |
| 2 | Known-good | The same command with [`CREATE_NO_WINDOW`](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags) | No flash | 0 |
| 3 | Privacy | A titled window started through WMI, outside Claude's process tree | Title absent from the log | Absent |

The 5-second limit that defines a flash is a judgment, not a measurement. Flashes seen so far lasted 0.15 to 1.2 seconds, so it has room, and a console window a person opened on purpose and closed within 5 seconds would be miscounted.

The first run found a real source before any report was published: `apps/SyncTrayzor/test_guard_synctrayzor_config.ps1` in win11-init-tools opened a Windows Terminal window roughly every 0.8 seconds, filed as [win11-init-tools#99](https://github.com/brownm09/win11-init-tools/issues/99).

## Cost

Snapshotting every 0.1 seconds with hooks on hide, destroy and name-change events used about 22% of one core over 24 seconds. Decisions 2 and 5 brought that down, and the measured figure is stated in the pull request.

## Alternatives considered

| # | Alternative | Why rejected |
|---|---|---|
| 1 | Change the default terminal to Windows Console Host now | It is machine-wide, may only change what the flash looks like, and would hide the evidence of which sources need fixing. It remains a stopgap if sources can't be fixed. |
| 2 | Raise `ForegroundLockTimeout` now | It affects every application, and the window still appears. |
| 3 | Process-creation auditing (event 4688) or `Win32_ProcessStartTrace` | Both need administrator rights or a system policy change, for a tool that only observes. |
| 4 | A run-on-demand monitor | It misses whatever happens while it is off, and flashes are noticed after the fact. The user chose always-on. |
| 5 | A hook that rewrites window-opening commands | It would cover only commands a session runs, and today's data can't say that is where flashes come from. |

## Consequences

- A hidden task now runs all the time on any machine where it is registered, at about 3% of one core on a 16-thread machine. `-Unregister` removes it and keeps the logs.
- Lookups can still miss. A parent that starts and exits between snapshots drops out of a chain, and a program that sets its own console title can defeat the name match. Such a flash reads as unattributed or with a shorter chain.
- Two instances of one program at once can't be told apart by name, so the newest match is used.
- Fixes are separate work, each with its own issue, starting from what the report shows.
