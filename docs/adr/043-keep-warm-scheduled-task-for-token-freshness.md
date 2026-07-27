# ADR-043 — Keep-Warm Scheduled Task for OAuth Token Freshness

**Date:** 2026-06-15 (amended 2026-07-27)
**Status:** Accepted
**Closes:** [dev-env#359](https://github.com/brownm09/dev-env/issues/359)
**Supersedes:** [dev-env#356](https://github.com/brownm09/dev-env/issues/356) (self-healing refresh — not built)
**Tags:** hooks, windows, scheduled-task, oauth, token-refresh, usage-snapshot, automation, desktop-app, msix, backup-restore, adr-124
**Related:** [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md), [ADR-031](031-auto-merge-disabled.md), [ADR-124](124-usage-snapshot-desktop-app-keychain-deadend.md), [ADR-079](079-backup-restore-convention.md)

---

## Context

The post-merge `usage-snapshot.py` hook (PostToolUse on `gh pr merge`) reads the short-lived OAuth
**access token** from `~/.claude/.credentials.json`. The desktop/Cowork client refreshes its own
token *elsewhere* and never writes that file back, so the on-disk token goes stale. It sat ~8 days
stale before anyone noticed, because the hook's expired-token branch exited 0 silently.

[#357](https://github.com/brownm09/dev-env/pull/357) (closing [#355](https://github.com/brownm09/dev-env/issues/355))
fixed the **visibility** half: a stale/missing token now emits a stderr advisory + `exit 2` instead
of failing silently. But restoring the snapshot still requires a **manual** interactive `claude` run
on each machine to rewrite the file — a chore that recurs on every lapse and every machine.

Two ways to remove the chore were investigated and rejected:

- **Long-lived `setup-token` (`CLAUDE_CODE_OAUTH_TOKEN`).** The plan was to read this one-year token
  in the hook instead of the short-lived file token. Verified empirically (no-secrets file handoff):
  a valid `sk-ant-oat…` token returns **HTTP 403** at `https://api.anthropic.com/api/oauth/usage` —
  authenticated but not authorized; the long-lived token class lacks the subscription-usage scope the
  `/login` token carries. Dead end.
- **Self-healing refresh inside the hook ([#356](https://github.com/brownm09/dev-env/issues/356)).**
  The hook would mint a new access token from the stored `refreshToken` and write it back. Rejected:
  it re-implements a private OAuth refresh client against an undocumented endpoint, and if Anthropic
  rotates the refresh token on use (RFC 6749 §6), the hook could invalidate or race the desktop
  client's session. Writing credentials from a hook is the risk we most want to avoid.

## Decision

Keep `~/.claude/.credentials.json` fresh with a **local Windows scheduled task** that periodically
runs the Claude CLI — the same refresh the user performs manually — so the CLI (not our code) owns
the token write.

Two committed scripts under `claude/scripts/`:

1. **`keep-token-warm.ps1`** — the task payload. Resolves `claude.exe` dynamically (newest versioned
   binary under the `Claude_pzs8sxrjxfjjc` package, mirroring `~/bin/claude`), runs
   `claude -p 'ok' --model haiku` in a timeout-bounded child job, and logs the credentials-file
   mtime and the token's **minutes-to-expiry before/after** each run to
   `Documents\LOGS\keep-token-warm_<date>.txt`. It is best-effort: every failure path still `exit 0`
   so the task is never left in an error state. **The token value is never read or logged** — only
   its numeric `expiresAt` and the file mtime.
2. **`register-keep-token-warm.ps1`** — one-time, per-machine registration. Creates the
   `ClaudeKeepTokenWarm` task: every 4 hours, **non-elevated** (`RunLevel Limited`, Interactive logon,
   no stored password), **windowless** (`powershell -WindowStyle Hidden -NonInteractive`, task marked
   Hidden). Idempotent (`-Force`); `-Unregister` removes it. The action points at the **junctioned**
   path `~/.claude/scripts/keep-token-warm.ps1` so payload updates from dev-env `main` land without
   re-registering.

The task is **per-machine by necessity** — it runs the local binary and writes the local file, so
each machine registers its own. This is *not* a Claude cloud routine (`claude/routines/`); those run
in cloud worktrees and cannot touch a machine's local credential store.

## Rationale

**Why a local scheduled task and not a cloud routine.** The whole point is to refresh a file on a
specific machine's disk using that machine's CLI binary. A cloud routine has neither. The
`claude/routines/` mechanism is therefore the wrong tool; this is local OS automation.

**Why instrument refresh instead of asserting it works.** Interactive `claude` is confirmed to
rewrite `.credentials.json` (the user's manual refresh this session moved the mtime). Headless
`claude -p` refreshing-and-writing the file *near expiry* is **unverified** — and Claude Code's
refresh is lazy (it does nothing when the token is healthy, as the smoke test confirmed:
`expiry=455min → no-change`). Rather than ship on faith, the payload logs before/after expiry so a few
days of real runs either show a `REFRESHED` line (token jumps from near-expiry back to ~8h) or prove
the mechanism doesn't work headlessly. #357's advisory is the safety net during that trial; if the
mechanism is disproven, the fallback is honest — keep the advisory and refresh manually.

**Why every 4 hours.** The access token TTL is ~8h. A 4h cadence bounds how long a lapsed token can
persist before a run catches the expiry window, without the token cost of running hourly (each run is
a real, if tiny, authenticated turn that loads the global CLAUDE.md context).

**Why non-elevated and windowless.** Per [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), a new
Windows script must not spawn a console or trigger UAC. Registering a task for the current user at
`Limited` run level needs no elevation (verified on this machine), and Hidden + `-WindowStyle Hidden`
keeps the 4-hourly run invisible. Diagnostics go to `Documents\LOGS`, not a kept-open window.

**Why this supersedes #356.** It reaches the same end state (a fresh on-disk token) without our code
ever performing an OAuth refresh or writing the credential file — the CLI does both, exactly as it
does for the user's manual refresh, sidestepping the rotation/race risk that gated #356.

## Alternatives considered

- **Long-lived `setup-token` read by the hook.** Rejected — HTTP 403 at the usage endpoint (token
  class lacks the scope). Empirically verified, not assumed.
- **Self-healing refresh in the hook ([#356]).** Rejected — credential write-back from a hook +
  refresh-token rotation risk against an undocumented endpoint.
- **Do nothing beyond #357.** Tenable (failures are now visible) but leaves the recurring manual
  chore the user explicitly asked to remove. Kept as the fallback if the keep-warm mechanism is
  disproven.
- **Claude cloud routine.** Rejected — no access to the machine's local binary or credential file.
- **Hourly cadence.** Rejected — marginal staleness improvement for several times the token cost.
- **A lighter refresh trigger than `claude -p`.** No confirmed CLI command refreshes auth without a
  model turn; `claude -p` is the known-working trigger. A lighter trigger is a future optimization if
  one is found.

## Consequences

**Positive:**

- Once the refresh mechanism is confirmed, the post-merge snapshot stays populated with no manual
  intervention, on every machine that registers the task.
- No credential write-back from our own code; the CLI owns the token lifecycle (the #356 risk is gone).
- The before/after logging makes the previously-opaque refresh behavior observable.
- Registration is reproducible and reversible (`register-keep-token-warm.ps1 [-Unregister]`).

**Negative / residual:**

- The headless-refresh mechanism is **unverified until observed** over a few days. This ADR ships the
  instrumentation, not a proof.
- Lazy refresh means there is still a short per-cycle window where the token is expiring/expired and a
  merge would show the advisory instead of a snapshot — bounded to hours, versus the prior 8 days.
- Per-machine registration is manual (one `register` run per machine); revoking is likewise per-machine.
- Each run is a small authenticated turn (token cost); 6/day at the 4h cadence.

## References

- [dev-env#359](https://github.com/brownm09/dev-env/issues/359) — issue this ADR closes.
- [dev-env#356](https://github.com/brownm09/dev-env/issues/356) — superseded self-healing-refresh spec.
- [dev-env#355](https://github.com/brownm09/dev-env/issues/355) / [PR #357](https://github.com/brownm09/dev-env/pull/357) — the visibility fix this builds on.
- [ADR-041](041-no-terminal-spawn-in-windows-scripts.md) — no terminal-spawn / non-elevated Windows scripts.
- [Claude Code Authentication](https://code.claude.com/docs/en/authentication.md) — `setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` (the rejected long-lived-token path).
- OAuth 2.0 refresh-token grant + rotation — [RFC 6749 §6](https://datatracker.ietf.org/doc/html/rfc6749#section-6).
- Microsoft Learn — [`Register-ScheduledTask`](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask) and [`New-ScheduledTaskTrigger`](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger) (repetition with no `-RepetitionDuration` repeats indefinitely; `[TimeSpan]::MaxValue` is rejected as out-of-range).

---

## Addendum (2026-07-27) — Inert under the MSIX desktop app; keep-warm self-gates, `-Unregister` now backs up first (dev-env#917, ADR-124)

[ADR-124](124-usage-snapshot-desktop-app-keychain-deadend.md) established that under the MSIX
Claude desktop app, OAuth lives in the OS keychain and is injected in-process to the desktop
app's own sessions — a `claude.exe` invoked as a **subprocess** (this task's exact context) is
unauthenticated and reports `loggedIn:false`. That makes every run of `keep-token-warm.ps1` on
such a machine permanently futile: the CLI it invokes can refresh nothing, so the task spends a
subprocess every 4 hours writing `no-change` to its log, indefinitely. ADR-124 fixed the
**hook** side of this (`usage-snapshot.py` skips its own on-demand refresh with an accurate
advisory) but deliberately left the **scheduled task** itself alone, filing the task-side fix as
[dev-env#917](https://github.com/brownm09/dev-env/issues/917) to keep that PR scoped.

**What changed:**

1. **`keep-token-warm.ps1` now self-gates.** A new `Test-DesktopAppDeadEnd` helper mirrors
   `usage-snapshot.py`'s `cli_auth_status`/`parse_auth_status` pair: it runs `<claude.exe> auth
   status --json` (an 8s timeout) against the already-resolved `$claudeExe` and treats an exact
   JSON `loggedIn: false` as the dead-end signature — nothing else (a parse failure, a timeout,
   `loggedIn: true`, or a missing field) is ever treated as the dead-end, so the change is a
   no-op on npm-CLI installs. On the dead-end signature the script exits early with a logged
   `desktop-app: nothing to refresh` line instead of spawning the doomed `claude -p ok` call —
   the same "probe before the expensive futile work" shape ADR-124 used, just in PowerShell
   instead of Python, reusing this script's own existing `Resolve-ClaudeExe` rather than
   duplicating a second MSIX-detection routine.

2. **`register-keep-token-warm.ps1 -Unregister` now follows the back-up-before-mutate
   convention ([ADR-079](079-backup-restore-convention.md)).** Before calling
   `Unregister-ScheduledTask`, it exports the live task definition to
   `Documents\LOGS\ClaudeKeepTokenWarmBackup.xml` — write-if-absent, so the first pristine
   capture is never overwritten by a later run — and refuses to proceed if the export fails or
   produces an empty file. Removal is then verified by read-back (`Get-ScheduledTask` must
   report the task gone afterward). No separate `-Restore` switch was added: the task carries
   no state beyond what this script's own registration logic defines (its only tunable is
   `-IntervalHours`, applied identically every time), so re-running the script with no switches
   already **is** the idempotent restore path — it deterministically reconstructs the exact
   definition the backup captured, without needing to re-import the XML.

**Why gate the task's own payload instead of relying solely on unregistering it everywhere.**
Per-machine registration is manual by design (see Consequences, above) — a machine that
migrates to the desktop app later, or one whose registration this addendum's author doesn't
control, would otherwise keep running the futile task until someone remembers to unregister it
by hand. The self-gate makes the *task's own payload* cheap and honest (a fast, logged skip)
regardless of whether anyone has unregistered it on that particular machine, while `-Unregister`
remains the complete, reversible fix for a machine that no longer needs the task registered at
all.

**Status:** both scripts committed. Any machine that no longer needs the task registered (e.g.
one that has fully migrated to the desktop app) should run `-Unregister` to remove it via the
now-backup-safe path; the task's self-gate (item 1) keeps it harmless in the meantime even on
machines where it stays registered. See
[dev-env#917](https://github.com/brownm09/dev-env/issues/917) for the full follow-up discussion.
