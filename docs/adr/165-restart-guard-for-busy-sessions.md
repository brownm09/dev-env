# ADR-165: Hold Off Restarts While Claude Sessions Are Working

**Date:** 2026-11-22
**Status:** Accepted
**Closes:** [dev-env#1398](https://github.com/brownm09/dev-env/issues/1398) (part of [dev-env#1396](https://github.com/brownm09/dev-env/issues/1396))
**Tags:** restart-guard, windows, windows-update, scheduled-task, shutdown, auto-resume, adr-041, adr-079
**Related:** [ADR-152](152-auto-resume-interrupted-desktop-sessions.md), [ADR-158](158-docker-lifecycle-outside-the-app-process-tree.md), [ADR-079](079-backup-restore-convention.md), [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), [ADR-033](033-awake-blocker.md), [ADR-003](003-config-in-version-control.md)

---

## Context

Auto-resume ([ADR-152](152-auto-resume-interrupted-desktop-sessions.md)) brings back a session that a restart cut off. It can't bring back what the restart killed: a half-run command, a background test run, a build. On LETHEON, the System log for 2026-10-23 to 2026-11-22 shows five shutdowns:

| # | When | Initiator | Kind |
|---|---|---|---|
| 1 | 2026-10-27 16:03 | StartMenuExperienceHost | the user clicked Restart |
| 2 | 2026-11-03 13:12 | (none) | unexpected shutdown (Kernel-Power 41 / 6008) |
| 3 | 2026-11-11 02:29 | MoUsoCoreWorker, then TrustedInstaller | Windows Update |
| 4 | 2026-11-21 10:36 | StartMenuExperienceHost | the user clicked Restart |
| 5 | 2026-11-22 02:29 | MoUsoCoreWorker | Windows Update |

Active hours were 08:00 to 02:00, and both Windows Update restarts came at 02:29, just after they ended. Nothing can prevent row 2. Rows 1 and 4 could have been stopped with a question, and rows 3 and 5 by keeping the time inside active hours.

## Decision

`claude/scripts/restart-guard.py` is a long-running, windowless `pyw` process. The `ClaudeRestartGuard` scheduled task (`register-restart-guard.ps1`, non-elevated, hidden, at sign-in and retried every 5 minutes, `IgnoreNew`) starts it outside the Claude app's process tree, as [ADR-158](158-docker-lifecycle-outside-the-app-process-tree.md) requires. It is listed in `claude/machine-installs.json`, so the doctor and the daily nudge from [ADR-003](003-config-in-version-control.md)'s 2026-11-22 amendment check it on every machine.

1. **What "busy" means** (`_busy_sessions.py`). Each running CLI keeps `~/.claude/sessions/<pid>.json` with `sessionId`, `cwd`, `name`, `procStart` and `status`. A session counts as busy when its process is alive, matched by pid **and** creation time (files from dead processes stay behind, and Windows reuses pids), and either its status is `busy` or it is `idle` with a transcript that `_auto_resume.classify` reads as `waiting_on_background`. A session waiting on the user is not busy: a restart loses nothing there that auto-resume can't bring back. Polled every 30 s. Each session has its own state, so there is no shared lock for one session to release on another's behalf, which is the gap in [ADR-033](033-awake-blocker.md)'s awake-blocker.
2. **Layer 1, restarts the user starts.** While anything is busy, the guard sets a [`ShutdownBlockReasonCreate`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-shutdownblockreasoncreate) reason naming the sessions and answers `WM_QUERYENDSESSION` with FALSE. Windows then shows its "apps are preventing restart" screen with that reason, plus Cancel and "Restart anyway", so it is a question, not a lock. A critical shutdown (`ENDSESSION_CRITICAL`) is never refused. [Microsoft's shutdown changes for Windows Vista](https://learn.microsoft.com/en-us/windows/win32/shutdown/shutdown-changes-for-windows-vista) say an application without a visible top-level window can't block shutdown, so the window is visible but 1×1 pixel, off-screen, with no taskbar button (`WS_EX_TOOLWINDOW`) and never activated (`WS_EX_NOACTIVATE`).
3. **Layer 2, Windows Update restarts.** Windows Update doesn't restart automatically during active hours, which can span up to 18 hours ([Microsoft support](https://support.microsoft.com/windows/manage-device-restarts-after-updates)). While anything is busy, the guard moves `ActiveHoursStart`/`ActiveHoursEnd` under `HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings` so the window starts 2 hours before the current hour and runs 18 hours. When nothing is busy it puts the captured values back. `BUILTIN\Users` has `SetValue` on that key, so this needs no elevation and is an `HKLM` write, which reaches Windows from any process. It follows [ADR-079](079-backup-restore-convention.md): a written-if-absent anchor at `Documents\LOGS\ClaudeRestartGuardActiveHoursBackup.json`, read back after every write, no change unless the anchor is usable, and `restart-guard.py --restore`.
4. **A 24-hour cap** (`maxDeferHours`, 1 to 72). Deferral counts from the first busy step. At the cap the guard puts active hours back even while sessions are busy, so a stuck session can't hold off security updates for days. The clock resets only on an idle step outside the captured active hours, when Windows Update could have restarted. An idle moment during the day doesn't reset it, or a short gap each day could defer an update forever.
5. **Report-only first.** `register-restart-guard.ps1` creates `~/.claude/restart-guard.json` with `"block": false` and `"deferUpdates": false`. The guard logs what it would do to `Documents\LOGS\claude-restart-guard_<date>.jsonl` and changes nothing. Each layer is turned on separately in that file, which the guard rereads every 30 s.
6. **Fail open.** If the busy check raises, the guard treats nothing as busy: it lets every restart through and puts active hours back. A broken guard never pins the machine.

## Alternatives considered

1. **A per-session marker file written by hooks.** Rejected: the CLI's own session file and the transcript already record this per session, and a second record could disagree with them. Hooks also can't see a restart coming, because `SessionEnd` isn't documented to fire on a kill or a shutdown.
2. **Disable the `UpdateOrchestrator\Reboot` task.** Rejected: Windows protects that task and turns it back on.
3. **`NoAutoRebootWithLoggedOnUsers`.** Rejected: it is always on, so it blocks restarts when nothing is busy, and it is documented for Windows Update for Business, not confirmed on Windows 11 Home.
4. **Pause updates.** Rejected: it holds off every update for days, not just the restart during work.
5. **`SetThreadExecutionState` or another keep-awake.** Rejected: it stops sleep, not restart.
6. **An elevated task for Layer 2.** Rejected once the key's ACL was read: Users can already set those values, so elevation would add a `sudo` step and a larger attack surface for nothing.
7. **Block outright with no cap.** Rejected: this user nearly always has a session working, so an uncapped Layer 2 would mean updates never install.

## Consequences

- **Calibration before blocking.** The busy check decides a block, so it is a gate ([ADR-144](144-gate-calibration-pass-3-dimension.md)). Fixtures cover the known-busy cases (a turn in progress, an idle session waiting on a background task) and the known-idle cases (waiting on the user, a clean end, a dead process, a reused pid). A first live read on LETHEON found 8 busy sessions out of the registry, all plausible on inspection. Run report-only for several days and count false busy results in the log before setting `block` or `deferUpdates` to true.
- **Not yet measured.** Whether Windows lists the off-screen window on its restart screen, whether Windows Update reschedules a restart it has already planned when active hours move, and whether its restart honours Layer 1. Verify each on the first real occurrence: the guard log, plus System events 1074 and 6006.
- **Undocumented input.** `~/.claude/sessions/<pid>.json` isn't documented by Anthropic. If its fields change, every session reads as not busy, and the guard blocks nothing.
- **Undo.** `register-restart-guard.ps1 -Unregister` puts active hours back, backs up the task definition to `Documents\LOGS\ClaudeRestartGuardBackup.xml`, removes the task and sets `"enabled": false`.

## References

- Microsoft Learn, [Shutdown Changes for Windows Vista](https://learn.microsoft.com/en-us/windows/win32/shutdown/shutdown-changes-for-windows-vista)
- Microsoft Learn, [ShutdownBlockReasonCreate function (winuser.h)](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-shutdownblockreasoncreate)
- Microsoft Learn, [WM_QUERYENDSESSION message](https://learn.microsoft.com/en-us/windows/win32/shutdown/wm-queryendsession)
- Microsoft Support, [Manage device restarts after updates](https://support.microsoft.com/windows/manage-device-restarts-after-updates)
