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
2. **`auto-resume-launcher.py`**, run by the hidden, non-elevated `ClaudeAutoResumeLauncher` task (`register-auto-resume.ps1`). The task runs every 3 minutes around the clock from a daily trigger that repeats for 24 hours, plus once at sign-in plus 1 minute. It ignores everything until a new app instance has been up for 90 s. Then it opens each opted-in session that was interrupted before that instance started and has no running CLI, using the `code/continue` deep link. It confirms each one started a CLI. A session that didn't start is opened again on later runs, up to 3 times per instance. A failing run is retried up to 3 times before that instance is given up, so a slow or half-loaded app doesn't lose a session and a recurring failure doesn't repeat all day. The launcher never sends anything itself.

**Opt-in.** The config's `include` rules are `all`, `pinned` (the sidebar pin, read from the app's session files), `cwd:<glob>` and `session:<id>`. `exclude` rules use the same syntax and win. A missing file, or one with an unknown rule or an out-of-range number, means nothing is resumed, and the reason is logged. So does a session with no readable app metadata: that is how terminal and headless resumes are kept out, and how exclude rules and the archived check avoid being skipped while the app is rewriting the file.

**No double send.**
- The launcher skips any session whose CLI is already running, and opening a running session doesn't fire `SessionStart` anyway.
- The hook ignores an "interrupted" message newer than its own start, since that is the prompt that launched this CLI.
- Any turn activity during the settle wait cancels the wake: a message, an `api-request` or an `attachment` line.
- The per-session attempt cap stops a turn that keeps crashing from being resumed indefinitely.

**Not interruptions.** A `/compact` or other local slash command leaves user entries with no reply. These are not interruptions: `classify` skips them and treats `compact_boundary` as a finished turn.

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

## Amendment — 2026-11-01: background tasks, the CLI's own resume turn, a per-instance cap

[dev-env#1185](https://github.com/brownm09/dev-env/issues/1185). Three gaps showed up after the reboots at 16:48 and 18:39 the same day. The fixes refine the decision above rather than replace it.

1. **A turn waiting on a background task counts as resumable.** "Fix #845" finished a turn while it waited on a background `npm ci`, typecheck and test run. The 18:39 reboot killed that task, and the transcript ended cleanly, so nothing reopened the session. `classify` now returns `waiting_on_background` when a finished turn has a background task with no completion notification. That covers Bash/PowerShell or Agent with `run_in_background`, and a Monitor still inside its `timeout_ms` at the restart. Completion is searched in user entries and in the `queued_command` attachments that carry notifications arriving mid-turn. The launcher and the hook both act on this state, and the hook's message asks the model to rerun the task.
2. **The resumed CLI's own turn no longer hides the interruption or cancels the wake.** On resume, Claude Code writes two things: a synthetic assistant placeholder (`model: "<synthetic>"`, "No response requested.") when the transcript ends on a user message, and a `stopped` task-notification ("didn't finish before the previous session ended") for each dead background task. The notification starts a turn by itself. At 17:09 that turn ended with nothing done, and the hook's settle check read it as activity and cancelled the wake. Now:
   - synthetic placeholders are ignored;
   - a trailing run of stop notices, and the one turn they set off, is set aside when classifying (`trim_stop_notices`);
   - the settle check counts only a non-notice user entry as a turn starting after a stop notice. Any other non-bookkeeping line still counts when there's no notice.

   The settle check also reads exactly the bytes appended since the first read, rather than comparing the last line.
3. **The attempt cap counts per app instance.** "Hook dispatcher" used its two wakes on the 15:58 and 16:07 restarts. After the 18:39 reboot it was skipped as `attempts_exhausted`. `maxAttempts` now counts wakes since the current Claude app instance started: the launcher uses its app start, and the hook queries it, falling back to the boot time. Every app restart or reboot gets fresh tries, and a session that crashes on every reopen is still stopped within one instance.

Considered and not taken: keying the cap on the boot time alone, as first suggested. App crashes without a reboot happen several times a day here, and they would share one boot's cap.
