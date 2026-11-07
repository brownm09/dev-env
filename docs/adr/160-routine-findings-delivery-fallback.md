# ADR-160: A Routine's Undelivered Finding Waits for the Next Prompt in a Person's Session

**Date:** 2026-11-07
**Status:** Accepted
**Closes:** [dev-env#1308](https://github.com/brownm09/dev-env/issues/1308)
**Tags:** routines, push-notification, remote-control, hooks, user-prompt-submit, observability
**Related:** [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md), [ADR-095](095-session-boundary-summaries-and-idle-refresher.md), [ADR-098](098-dev-env-sync-advisories-to-stdout.md), [ADR-106](106-hook-liveness-heartbeat.md)

---

## Context

Scheduled routines report through the `PushNotification` tool. The 2026-09-19 retrospective found those pushes failing with "Remote Control inactive" on most routine days since about 2026-09-04, so a finding reached nobody unless someone read the journal ([dev-env#1103](https://github.com/brownm09/dev-env/issues/1103)).

The message's source and conditions are [on the issue](https://github.com/brownm09/dev-env/issues/1308#issuecomment-6712094777). In short, the tool finds no way to reach the phone and doesn't try to send. In the sample read there, all three pushes from scheduled runs got this result and all three from an interactive session were handed off. So this looks like the normal state of a scheduled run rather than an outage, though the code that decides whether a scheduled session gets a Remote Control connection wasn't read.

## Decision

**After each push, a routine passes the tool's result to `claude/scripts/routine-findings.py save`.** Only the two "Mobile push requested." results count as delivered. Anything else is saved, including an empty result and wording the script doesn't recognise, so a changed message can cause an extra finding but not a lost one. The tool gets no receipt from the phone, so "requested" is as much as a routine can observe.

**Findings wait in `~/.claude/routine-findings/pending/`, and the same script, wired as a `UserPromptSubmit` hook, shows them.** It moves each file to `shown/` and injects its text as `additionalContext` on stdout, the stream that event forwards on exit 0 (ADR-027). The move makes this once only, including when two sessions race, because only one rename can succeed. At most 20 are shown per prompt, oldest first, and the rest follow on the next prompt.

## Alternatives rejected

- **A `SessionStart` hook.** The event carries no prompt, so a scheduled run couldn't be told from a person's session and could use up the findings itself. `UserPromptSubmit` can skip automated prompts the way `idle-refresher.py` does. It also reaches a person in a session that was already open.
- **The journal.** That's the place findings already went unread, and the issue asks for somewhere else.
- **A GitHub issue per finding.** dev-env is public, findings can name private repositories, and nightly findings would add to a backlog that's already not being worked down.
- **Fixing the transport.** Connecting scheduled runs to Remote Control isn't a setting this repo controls, and the fallback is needed whenever a push fails for any reason.

## Consequences

- Most routine pushes are expected to end up saved, so a person returning after several days can see a long list. [dev-env#1307](https://github.com/brownm09/dev-env/issues/1307), which limits notifications to changed findings, reduces that.
- The hook adds one process per prompt, doing one directory listing when nothing is pending.
- A session started by another session looks like a person's session to the hook, so it can claim findings. The cue asks that session to repeat them in its report.
- The findings are shown to the model, which is told to show them to the user. A model that ignores the instruction would lose the display, but the file stays in `shown/`.
- `shown/` is never pruned. A finding is a few hundred bytes, so this can wait until it matters.
- `~/.claude/scheduled-tasks/` holds a separate live copy of each routine, so a registered routine needs its live prompt updated to pick this up. On 2026-11-07 no routine from `claude/routines/` was registered there.
- Scheduled tasks defined outside `claude/routines/` don't use this until their own prompts call the script.
