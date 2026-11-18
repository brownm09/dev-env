# ADR-163: A Windows Notification With a One-Click Approve Button for Production Deploys Waiting on Approval

**Date:** 2026-11-18
**Status:** Accepted
**Closes:** [dev-env#1380](https://github.com/brownm09/dev-env/issues/1380) (part of [dev-env#1379](https://github.com/brownm09/dev-env/issues/1379))
**Tags:** notifications, scheduled-task, windows, github-actions, deploy, url-scheme, adr-041, adr-079
**Related:** [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), [ADR-079](079-backup-restore-convention.md), [ADR-158](158-docker-lifecycle-outside-the-app-process-tree.md)

---

## Context

lifting-logbook's `Deploy` workflow pauses at `deploy-production` until a required reviewer approves the `production` environment ([reviewing deployments](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/reviewing-deployments)). The owner learned about a waiting run from email or by opening Actions, so a release could sit at the gate for hours. They asked for a Windows notification that takes them straight to the approval, or approves it with a button.

## Decision

`claude/scripts/deploy-approval-notifier.py`, registered per machine by `register-deploy-approval-notifier.ps1`:

1. **Poll.** A hidden, non-elevated scheduled task runs `--tick` at sign-in and every 2 minutes. For each watched repo (default `merickvaughn/lifting-logbook`, overridable in `~/.claude/deploy-approval-notifier.json`) it lists runs with `status=waiting` and reads each one's [pending deployments](https://docs.github.com/en/rest/actions/workflow-runs#get-pending-deployments-for-a-workflow-run) over REST, never GraphQL, whose shared budget other sessions exhaust. The listing costs 30 calls an hour per repo; each waiting run not yet toasted (one on another environment, or one the user can't approve) adds 30 more an hour while it waits, up to about 900 an hour for a repo with 30 such runs, against the 5,000-an-hour core limit. In practice a repo has a run or two waiting at a time. After 5 failed polls in a row (`gh` missing, auth expired, network down) it shows one "can't reach GitHub" toast, since a silent poller would read as "nothing waiting".
2. **Notify once.** A run waiting on `production` that `current_user_can_approve` gets one toast, using the same PowerShell WinRT route and AUMID as `docker-watchdog.py`. It uses the `reminder` [scenario](https://learn.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/toast-schema) so it stays on screen until a button is pressed. The toast and its **Review** button open the run page through protocol activation. A run stops being remembered once it no longer waits.
3. **Approve in one click.** The **Approve** button opens `deploy-approve:<owner>/<repo>/<run-id>/<nonce>`, a URL scheme [registered](https://learn.microsoft.com/en-us/previous-versions/windows/internet-explorer/ie-developer/platform-apis/aa767914(v=vs.85)) under `HKCU\Software\Classes` whose handler is `--approve`. The handler [approves](https://docs.github.com/en/rest/actions/workflow-runs#review-pending-deployments-for-a-workflow-run) with the user's `gh` token only when the repo is watched, the nonce matches the one stored when that toast was shown, and a fresh read shows the run still waiting on `production`. A result toast says what happened.

The owner chose the Approve button knowing it approves without showing the `plan-production` summary, which lifting-logbook's `docs/deploy.md` says to read first, and which the API doesn't expose (2026-11-18). The Review button stays for releases that need that read.

### Why a nonce

Any web page can link to a custom URL scheme. Without a secret, a page could approve a waiting deploy on a click. The nonce exists only in the toast and the local state file (`~/.claude/deploy-approval-notifier/state.json`, outside `scratch/`, which sessions clean up freely), so only a button this script showed can approve. Each record is saved as soon as its toast shows, so the button works even if the rest of the tick fails.

### Registration runs outside the Claude app

A Claude desktop session's `HKCU` writes stay private to the app (global `CLAUDE.md`, Platform & Environment), so the URL scheme would exist only for the app. The register script is run from a normal terminal or a one-shot scheduled task. It backs up the task and the key before removing them, removes the key only when its handler is this notifier, and puts back any `deploy-approve` key another tool registered first ([ADR-079](079-backup-restore-convention.md)). It spawns no windows and doesn't self-elevate ([ADR-041](041-no-terminal-spawn-in-windows-scripts.md)).

## Alternatives considered

1. **Link only, no Approve button.** Keeps the plan-summary read in the path every time. Rejected by the owner in favour of one click; the Review button keeps this path available.
2. **Approve only when the release looks safe** (no `infra/terraform` or migration change since the last production deploy). Rejected by the owner as more than they asked for.
3. **GitHub's own notifications** (email, the web inbox, GitHub Mobile). They exist already and weren't reaching the owner on the desktop when it mattered, which is the problem this solves.
4. **A webhook.** Needs a public endpoint to receive it; polling every 2 minutes is enough for a gate that waits for minutes to hours.

## Consequences

- A waiting production deploy shows on the desktop within about 2 minutes, while the machine is signed in. Nothing notifies while it's off.
- One click can apply a production change without the plan read. That trade-off is the owner's, recorded here.
- The default `-ScriptPath` follows `~/.claude/scripts`, which tracks dev-env `main`, so a merged change reaches the task without re-registering.

## References

- GitHub Docs, [Reviewing deployments](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/reviewing-deployments)
- GitHub REST API, [Workflow runs: pending deployments](https://docs.github.com/en/rest/actions/workflow-runs#get-pending-deployments-for-a-workflow-run)
- Microsoft Learn, [App notification content schema](https://learn.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/toast-schema)
- Microsoft Learn, [Registering an Application to a URI Scheme](https://learn.microsoft.com/en-us/previous-versions/windows/internet-explorer/ie-developer/platform-apis/aa767914(v=vs.85))
