# ADR-152 — Auto-Resume Desktop Sessions Cut Off by a Reboot or App Crash

**Date:** 2026-11-01
**Status:** Accepted
**Closes:** [dev-env#1182](https://github.com/brownm09/dev-env/issues/1182)
**Tags:** hooks, session-start, async-rewake, desktop-app, deep-link, scheduled-task, windows, crash-recovery, resume, opt-in, fail-closed, backup-restore, adr-041, adr-043, adr-079

---

## Context

On angstrom-b the Claude desktop app is often closed abruptly: a reset-button reboot, a hang, or a forced restart. Every session that was mid-turn then stops. The app reopens the sessions, but it doesn't continue the interrupted turn. Someone has to type "continue" into each one, and with many sessions running at once that is a recurring chore. Work also stays stalled until someone notices.

What the app and CLI offer, checked against Claude desktop 4.4212.0 and Claude Code 2.1.309:

- **Nothing outside the app can send a message to a session.** The in-app tools (`SendMessage`, `ccd_session_mgmt__send_message`) talk over a per-process named pipe that needs a secret token (`CLAUDE_CODE_MESSAGING_SOCKET` / `_TOKEN`). That protocol is internal.
- **Deep links navigate but don't submit.** In the app's `claudeURLHandler`, `claude://code/new?q=` only prefills the composer, and no parameter submits it. `claude://code/continue?session=local_<id>` opens that session.
- **After a restart, the app starts a session's CLI only when the session is opened.** After the 2026-11-01 11:16 reboot, the session on screen got a `claude.exe --resume=` process 9 s after the app started. Two interrupted dev-env sessions had no process 30 minutes later.
- **Opening a session with the `code/continue` deep link starts its CLI.** Verified: `claude.exe --resume=<cliSessionId>` appeared within 25 s.
- **`asyncRewake: true` on a command hook** (in the 2.1.309 hook schema: "runs in background and wakes the model on exit code 2"). Verified for `SessionStart` in the desktop's stream-json mode: with no user input, the hook's exit 2 started a turn and the model answered the hook's message.

## Decision

Auto-resume has two parts. Both read a machine-local opt-in file, `~/.claude/auto-resume.json`.

1. **`session-start-auto-resume.py`**, a `SessionStart` hook with matcher `resume`, `asyncRewake: true` and a 180 s timeout. It wakes the model only when all of these hold:
   - the config is enabled and its rules opt the session in;
   - the transcript's last turn is classified `interrupted` (`_auto_resume.classify`);
   - the interruption is under `maxAgeHours` old;
   - the session has had fewer than `maxAttempts` wakes in that window;
   - the last message is unchanged after `settleSeconds`.

   The wake message asks the model to check what the interruption left behind first: a half-run command, a partly written file, or a stale `index.lock` (remove one only if it predates the boot and no git process is running). Then the model carries on.
2. **`auto-resume-launcher.py`**, run by the hidden, non-elevated `ClaudeAutoResumeLauncher` task at sign-in plus 1 minute and every 3 minutes after (`register-auto-resume.ps1`). It does real work once per app instance. It waits until the app has been up for 90 s, then opens each opted-in session that was interrupted before this instance started and has no running CLI, using the `code/continue` deep link. It confirms each one started a CLI. It never sends anything itself.

**Opt-in.** The config's `include` rules are `all`, `pinned` (the sidebar pin, read from the app's session files), `cwd:<glob>` and `session:<id>`. `exclude` rules use the same syntax and win. A missing, unreadable or disabled file means nothing is resumed.

**No double send.** The launcher skips any session whose CLI is already running. Opening a running session wouldn't fire `SessionStart` anyway. The hook's settle re-check cancels the wake when the user, or a queued message, has already started a turn. The attempt cap stops a turn that keeps crashing from being resumed indefinitely.

**Undo.** `register-auto-resume.ps1 -Unregister` backs up the task definition (write-if-absent, ADR-079), removes the task, and sets `"enabled": false`, which also stops the hook.

## Consequences

- An interrupted session continues within about 3 to 5 minutes of the app starting, with no one typing "continue". A session the user opens sooner continues as soon as it opens.
- The launcher brings each resumed session on screen in turn, about 10 s apart, so the app window changes view while it runs.
- Classification reads the CLI transcript format, and the `pinned` rule reads the app's session files. Neither format is documented. If either changes, the result fails closed: a session isn't classified `interrupted`, or isn't matched, so nothing is resumed by mistake. Unit tests pin the shapes seen on 2026-11-01.
- The launcher depends on the app's `code/continue` deep link. If the app turns its OS entry points off, the launcher logs `not_started` for each session it tried.

## Alternatives Considered

- **A desktop scheduled task (`create_scheduled_task`) that lists sessions and sends "continue".** It would use the sanctioned in-app tools. Rejected as the trigger: a task can't run "at app start". A missed cron slot only runs on the next launch, so a short outage triggers it only if the outage spans a slot. Polling often enough to be reliable means a full LLM session every few minutes (about 50k+ tokens of context each), and each run adds a sidebar entry.
- **`claude --resume <id> -p continue` from the scheduled task.** Rejected: it runs a second CLI against the transcript the app owns, outside the app's view. Two writers on the same session.
- **Driving the internal messaging pipe.** Rejected: an undocumented protocol and a secret token.
- **`CronCreate` inside each session.** Rejected: it is session-only, and its `durable` flag is documented as having no effect, so it doesn't survive the restart.
- **The `SessionStart` hook alone.** Rejected as incomplete: after a restart, interrupted sessions get no CLI process until opened, so the hook never fires for them.
