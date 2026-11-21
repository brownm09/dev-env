# ADR-164: Notify the User From the Shell While an Install or Uninstall Waits on a Dialog

**Date:** 2026-11-21
**Status:** Accepted
**Closes:** [dev-env#1388](https://github.com/brownm09/dev-env/issues/1388)
**Tags:** notifications, windows, installers, push-notification, idle-detection, claude-md
**Related:** [ADR-159](159-instruction-files-hold-rules-not-stories.md), [ADR-160](160-routine-findings-delivery-fallback.md), [ADR-163](163-production-deploy-approval-notifications.md)

---

## Context

On 2026-11-21 a win11-init-tools session removed about 50 unused programs from LETHEON ([win11-init-tools#127](https://github.com/brownm09/win11-init-tools/issues/127)). One uninstaller needed a click, opened behind other windows, and the run waited about 14 minutes for it while the user didn't know it was there. The user asked to be notified when an install or uninstall needs them to click a dialog, and again if they don't act right away and are at the computer.

## Decision

A rule in `claude/CLAUDE.md` → *Platform & Environment*, with its story in `docs/REFERENCE.md` → *Detail: Platform and environment* ([ADR-159](159-instruction-files-hold-rules-not-stories.md)):

1. **Notify when the dialog starts waiting.** Any session that starts an installer or uninstaller needing a click does this, however it runs it.
2. **Repeat about every 2 minutes, only while the user is active.** "Active" means keyboard or mouse input since the last reminder, read with [`GetLastInputInfo`](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo). It reports input for the calling session only, which is the session the dialog is on.
3. **Show the notification from the shell**, such as a [`NotifyIcon.ShowBalloonTip`](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.showballoontip) balloon, not only through `PushNotification`.

The worked example is `Wait-Uninstall` in win11-init-tools' `uninstall/UnusedApps/uninstall_unused_apps.ps1`.

## Alternatives considered

1. **`PushNotification` alone.** Rejected: it is skipped while the user is active in the Claude app (observed behavior, stated in [dev-env#1388](https://github.com/brownm09/dev-env/issues/1388); not documented by Anthropic that we found). That is exactly when a dialog hidden behind the app is easiest to miss.
2. **Repeat whether or not the user is there.** Rejected: reminders nobody sees only pile up in the notification center, and the user asked for repeats while they are at the computer.
3. **One notification, no repeats.** Rejected: a single balloon is easy to miss or dismiss while the dialog keeps waiting, which is the failure the user reported.
4. **A hook that detects a waiting installer.** Rejected for now: nothing in a session's tool calls reliably says a dialog is waiting, so a hook would have to guess. A script that starts the installer knows, which is why the rule sits with whoever starts it.

## Consequences

- Nothing enforces the rule mechanically; a session follows it, and a script that runs interactive installers can build it in, as the win11-init-tools one does.
- If the app stops suppressing `PushNotification` while the user is active, alternative 1 becomes viable and the shell notification could be dropped. Revisit this ADR then.

## References

- Microsoft Learn, [GetLastInputInfo function (winuser.h)](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo)
- Microsoft Learn, [NotifyIcon.ShowBalloonTip Method](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.showballoontip)
