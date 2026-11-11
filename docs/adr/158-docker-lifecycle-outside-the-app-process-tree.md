# ADR-158: Start and Stop Docker Desktop From Outside the Claude App's Process Tree

**Date:** 2026-11-06
**Status:** Accepted
**Closes:** [dev-env#1278](https://github.com/brownm09/dev-env/issues/1278)
**Tags:** docker, wsl, memory, hooks, scheduled-task, watchdog, windows, hypervisor, adr-041, adr-079
**Related:** [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), [ADR-079](079-backup-restore-convention.md), [ADR-144](144-gate-calibration-pass-3-dimension.md), [ADR-155](155-record-console-flashes-before-fixing-them.md)

---

## Context

The user asked for Docker Desktop to start when a session needs it and stop when sessions are done, because it holds RAM while idle. [Docker's Resource Saver](https://docs.docker.com/desktop/use-desktop/resource-saver/) doesn't help: it "does not reduce Docker's memory utilization" on WSL. So the engine has to actually stop.

Testing that design found two failures that had already stopped Docker from working.

**1. A backend started from a session crashes on its next start.** Starting Docker from a session produced this dialog: "initializing Inference manager: listening on unix://...\Docker\run\dockerInference: remove ...: The file cannot be accessed by the system." Docker's only offered actions were Quit and "Reset to factory defaults".

Measured 2026-11-06 by creating a new AF_UNIX socket file, closing it, then opening and deleting it:

| # | Socket directory | Inside the Claude app's process tree | Started by Task Scheduler |
|---|---|---|---|
| 1 | `%USERPROFILE%` | Works | Works |
| 2 | `%USERPROFILE%\Documents` | Works | Not run |
| 3 | `%USERPROFILE%\.claude\scratch` | Works | Not run |
| 4 | `%LOCALAPPDATA%\Temp` | Works | Not run |
| 5 | `%USERPROFILE%\AppData`, `Local`, `Roaming`, `Local\Programs`, `Local\Docker`, `Local\Docker\run` | Error 1920 on open and delete | `Local` itself: works |

The dead `dockerInference` file also deleted normally from a process Task Scheduler started. Directories a process in the tree created under `AppData` turned up in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache`, so that view is redirected. The redirection is how [packaged desktop apps treat AppData](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes), and Claude is installed as a package. That its children inherit this, while reporting no package identity, is observed behaviour; which component returns 1920 isn't proven. `fltmc` shows `bindflt` attached to `C:`. [docker/desktop-feedback#625](https://github.com/docker/desktop-feedback/issues/625) and [a Docker forum thread](https://forums.docker.com/t/docker-desktop-4-88-1-fails-to-start-on-windows-af-unix-socket-file-cannot-be-accessed/152314) report the same error with no fix, and neither mentions where Docker was started from, so this may or may not be their cause.

**2. WSL couldn't boot at all.** The System log shows `Hypervisor launch failed; Either SVM not present or not enabled in BIOS` (event 42) on every boot since 2026-11-03 00:09, the boot after an unexpected shutdown. It had already happened once: the two boots at 11:41 and 11:53 on 2026-11-02 failed the same way, the hypervisor then started on the next five, and the twelve boots from 2026-10-30 on had started normally. Both switches to failing followed an unexpected shutdown, but so did most of the 17 logged since 2026-10-30 that changed nothing, so the cause isn't established. `Win32_Processor.VirtualizationFirmwareEnabled` and `Win32_ComputerSystem.HypervisorPresent` both read False, and WSL fails with `HCS_E_HYPERV_NOT_INSTALLED`. Docker gave no sign of this: its backend logged "still waiting for init control API" for over 35 hours, leaving a tray with no engine. In that state `docker desktop start` printed "already running" and exited 0, and a plain `docker desktop stop` failed until `--force` was used.

## Decision

1. **Docker is only ever started from outside the app's tree.** `pre-tool-use-docker-start.py` runs the `ClaudeDockerStart` scheduled task and waits for the engine. It blocks `docker desktop start`/`restart` and launching `Docker Desktop.exe` from a session, naming the way out. These commands are enumerated, and each one recreates failure 1.
2. **A watchdog runs from Task Scheduler at sign-in and every 5 minutes.** It has to live outside the tree, because only there can it delete the sockets.
   - **Monitor:** once per boot it reads `HypervisorPresent` and shows a Windows notification if it's False. Software can't change a firmware setting, so telling the user is the fix. The session hook also skips starting Docker in that state, since a start would only wedge.
   - **Repair:** it deletes AF_UNIX socket files in Docker's directories whenever no backend runs, checking the reparse tag (`0x80000023`) so nothing else can be deleted. It force-stops a Docker stuck with no engine for 20 minutes.
   - **Idle stop:** see the next point.
3. **Stop rules, set by the user.** A session-started engine stops 15 minutes after the last `docker` command, or after 2 hours if containers are still running, so a long test keeps its database. An engine the user started has no marker and is never stopped. A failed idle stop retries and never escalates; only the wedge path forces.
4. **Never `wsl --shutdown`.** WSL stops its VM itself ([`vmIdleTimeout`](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)), and that command would also kill other distros.
5. **Projects list test commands that reach Docker without naming it.** lifting-logbook's `npm test` starts Postgres through Testcontainers. `docker_test_commands` and `docker_test_skip` in `.claude/hook-config.json` cover this. A missed command costs one retry: the test reports Docker as unreachable, and the session's next `docker` command starts it.

## Calibration

Per [ADR-144](144-gate-calibration-pass-3-dimension.md).

**Command detector.** It keys on the first word of a top-level command segment, not on the text appearing anywhere. Corpus: 30,940 recorded shell commands from 304 session transcripts, 214 of them mentioning "docker".

| # | Class | Count | Result |
|---|---|---|---|
| 1 | Classified as needing the engine | 66 | The 40 most common of 49 distinct commands were read, and each runs `docker`. A false match costs up to 15 minutes of RAM and blocks nothing. |
| 2 | Classified as a direct start | 10 | Real starts, after a `--help` false positive found here was fixed. |
| 3 | No match | 138 | Greps, log reads, and `docker desktop status`. A separate pattern for real-looking calls found one miss, `(docker info ...)`, now covered. |

**Hypervisor check.** Known bad: this machine now, where it reads False and WSL fails. Known good: not yet observed, since it needs the BIOS fix. It therefore only reports and skips a start that would wedge; it blocks no command. An unreadable answer reads as present.

**Constants.** The 15-minute and 2-hour limits are the user's choices, not derived from data. The 20-minute wedge limit is a judgment from two measured starts and one wedge, not a calibrated threshold. On 2026-11-11 the first start after SVM was enabled ran 7.4 minutes with no engine, and the next took 35 seconds; the wedge observed earlier lasted 35 hours. The limit was 10 minutes until that first start showed only 2.6 minutes of margin. A false wedge is costly, because it stops a slow start that would then be just as slow next time, while a real wedge held 10 minutes longer costs only RAM.

## Alternatives Considered

1. **Stop at the end of each turn with a `Stop` hook.** Rejected: nothing fires 15 minutes after a session goes quiet, a crashed session would never release Docker, and a background test would lose its database when the turn ended.
2. **`wsl --shutdown` to free the memory.** Rejected: it kills every distro, and WSL stops by itself.
3. **Rename `Docker\run` aside when the socket is stuck.** Earlier sessions did this twice, leaving `run.stale-*` directories. It clears the current file, and the next backend started from a session recreates the failure.
4. **Repair from a session hook instead of a scheduled task.** Rejected: inside the tree the delete itself fails with 1920.
5. **Turn off Docker's inference settings.** Rejected: reports in [docker/desktop-feedback#625](https://github.com/docker/desktop-feedback/issues/625) say the failure persists, and the same error also hit `docker-secrets-engine\engine.sock`.

## Consequences

- A session's first `docker` command after a stop waits for the engine, up to 165 seconds.
- Every shell call pays one more `pyw` launch. Check with `hook-timing-report.py`.
- A second machine needs `register-docker-watchdog.ps1` run once; until then the hook reports that it can't start Docker.
- A stack started by hand on a session-started engine is stopped after the 2-hour limit. Named volumes and images survive. The user accepted this.
- If a later app version stops redirecting AppData, the block on direct starts would be needlessly strict, though harmless. Re-run the table above before relaxing it.
