# ADR-124 — Usage-Snapshot Detects the Desktop-App Keychain Dead-End Instead of a Futile Refresh

**Date:** 2026-07-27
**Status:** Accepted
**Closes:** [dev-env#915](https://github.com/brownm09/dev-env/issues/915)
**Tags:** hooks, usage-snapshot, oauth, credentials, keychain, msix, desktop-app, post-tool-use, token-refresh, graceful-degradation, error-message-diligence, global-rule, adr-043, adr-044
**Related:** [ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md), [ADR-044](044-eliminate-usage-snapshot-gap-on-demand-refresh.md), [ADR-034](034-error-message-diligence.md), [ADR-103](103-shared-hookout-emitter.md)

---

## Context

`usage-snapshot.py` (PostToolUse, fires after `gh pr merge`) reads an OAuth access token from
`~/.claude/.credentials.json` and calls `https://api.anthropic.com/api/oauth/usage`. When the token
is missing/blank or expired it refreshes on demand by shelling to `keep-token-warm.ps1`, which invokes
the Claude CLI so **the CLI owns the token write** — never our code ([ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md),
[ADR-044](044-eliminate-usage-snapshot-gap-on-demand-refresh.md)). That whole design rests on one
assumption: a CLI on this machine writes a **readable** token to that file.

That assumption broke when the machine migrated from the **npm Claude Code CLI** to the **MSIX-packaged
Claude desktop app**. Verified live during the dev-env#915 investigation (the token value was never
printed):

- `claude` on PATH resolves into `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\<ver>\claude.exe`
  (an MSIX package), and a stale `%LOCALAPPDATA%\claude-cli-nodejs` dir is the npm remnant.
- The desktop app keeps OAuth in the **OS/Electron keychain** — `%APPDATA%\Claude\Local State` carries
  an `os_crypt.encrypted_key` (Electron safeStorage / DPAPI), and `claude --bare` help states that
  normal auth reads "OAuth and keychain." It **injects** auth in-process to child sessions
  (`CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH=1`) and **never writes a readable `.credentials.json`** —
  none exists anywhere on the machine except the orphan, last written weeks earlier and now fully
  blanked (`accessToken` and `refreshToken` both empty strings, `expiresAt` 0).
- The bundled CLI invoked **as a subprocess** — the exact context of both this hook and
  `keep-token-warm.ps1` — reports `{"loggedIn": false, "authMethod": "none"}`.

The consequence: the token can be neither **read** (blank orphan file) nor **refreshed** (the keep-warm
CLI subprocess is unauthenticated, so it writes nothing), and the hook's own advisory — "Run `claude`
interactively to rewrite it" — is **impossible to act on** (an interactive standalone CLI is equally
unauthenticated, and the desktop app writes the keychain, not the file). So on **every** merge the hook
paid a ~35s futile `keep-token-warm.ps1` refresh and then emitted a remedy that could never work.

The feature cannot be preserved under the desktop app through any sanctioned path: the long-lived
`claude setup-token` token returns **HTTP 403** at the usage endpoint (empirically verified,
[ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md)); a raw OAuth refresh from the
`refreshToken` was rejected (#356); writing/decrypting credentials ourselves is out of bounds
([ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md)) and, for the Electron safeStorage
case, would mean parsing DPAPI + AES-GCM + Chromium leveldb — fragile and app-update-brittle. The token
is simply not reachable by a subprocess in this configuration.

## Decision

In `usage-snapshot.py`'s **missing/blank-token branch**, before attempting the on-demand refresh, probe
whether a CLI **subprocess** can authenticate here — and if it demonstrably cannot (the desktop-app
signature), skip the futile refresh and emit an **accurate** advisory instead of the impossible one.

Concretely, three best-effort helpers (all offline-testable via dependency injection, mirroring
`attempt_token_refresh`'s injected-fake pattern):

- `resolve_claude_exe()` — the newest packaged `claude.exe` under the MSIX package path (mirrors
  `keep-token-warm.ps1`'s `Resolve-ClaudeExe` version-sort). Returns the real `.exe` so it can be
  exec'd directly (no `.cmd`/PATHEXT indirection). **Deliberately no PATH fallback:** the probe exists
  only to detect the MSIX dead-end, so on any other install the packaged exe is absent → `None`.
- `parse_auth_status(stdout)` — pure classifier: `"in"` for `loggedIn` exactly `True`, `"out"` for
  exactly `False`, `None` otherwise (unparseable / non-object / missing / non-boolean — never treat
  malformed output as a dead-end).
- `cli_auth_status()` — runs `<exe> auth status --json`; returns `None` immediately (no subprocess)
  when no packaged exe resolves, and `None` on any subprocess error.

When `cli_auth_status() == "out"`, `main()` emits (via `_hookout.emit_block`, which exits 2 —
so the refresh is genuinely skipped):

> `[usage-snapshot] Skipped: the Claude desktop app keeps OAuth in the OS keychain, so no readable token file exists and a CLI refresh cannot create one (dev-env#915). Post-merge usage snapshots are unavailable in this configuration.`

When the probe is `"in"` or `None` (npm-CLI install, or an unknown result), control falls through to the
**unchanged** legacy refresh-then-advise path. The usage snapshot therefore **remains unavailable under
the desktop app by necessity** — this ADR does not restore the feature there (no sanctioned token source
exists); it stops the wasted work and the misleading remedy. Measured on the repro machine: the branch
went from a ~35s stall to **0.66s** with the accurate message.

## Rationale

- **Accurate over misleading ([ADR-034](034-error-message-diligence.md)).** The old advisory prescribed
  a remedy that is *impossible* in this configuration. A guard message must describe what is actually
  true; "run claude interactively to rewrite it" is exactly the kind of author-intent-not-reality
  message ADR-034 warns against.
- **Visible over silent (user-chosen), now that it's accurate.** The creds-file-absent case already
  exits 0 silently, and silent-skip was a considered option. The user chose to keep the skip **visible**
  — consistent with the "surface, don't fail silently" ethos of the original visibility work (#355/#357)
  — because the accurate line explains *why* the snapshot is gone at the moment it's gone.
- **The probe is the semantic signal, and it's cheap.** `claude auth status` answers precisely the
  operative question — "can a CLI subprocess authenticate here?" — rather than a proxy. It is
  sub-second, so it converts a 35s futile refresh into a fast, correct diagnosis.
- **Scoped to the dead-end; npm installs untouched.** `resolve_claude_exe()` has no PATH fallback, so on
  an npm-CLI machine it returns `None`, `cli_auth_status()` spawns **no** subprocess, and the legacy
  refresh path runs exactly as before. The change adds cost and new behavior only where the dead-end
  actually exists.
- **Correct channel ([ADR-103](103-shared-hookout-emitter.md)).** The advisory rides `_hookout.emit_block`
  (exit-2 stderr, ASCII-sanitized), the same model-visible channel the hook's other three skip messages
  already use.

## Alternatives considered

- **Repoint the hook at "the real store" (the issue's own premise).** Rejected — there is no readable
  store to point at; the live token is DPAPI-encrypted in the keychain / held in-process, and the only
  `.credentials.json` on the machine is the blanked orphan.
- **Decrypt the Electron safeStorage keychain from Python.** Rejected — DPAPI-unwrap + AES-GCM +
  Chromium-leveldb parsing is fragile, breaks on every app update, and reads app secrets the hook has
  no business touching.
- **A user-minted long-lived `claude setup-token` token.** Rejected — returns **HTTP 403** at
  `/api/oauth/usage`, already established in [ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md).
- **Silent skip (exit 0), matching the creds-absent case.** Considered and offered; the user chose the
  visible, accurate advisory instead.
- **Gate on the `CLAUDE_CODE_ENTRYPOINT=claude-desktop` env var (zero subprocess).** Rejected as a
  proxy — it describes how the *session* started, not whether a token is reachable by a subprocess; the
  `claude auth status` probe is the direct, robust signal and is already cheap.
- **Retire `keep-token-warm.ps1` / the `ClaudeKeepTokenWarm` scheduled task now.** Deferred — the task
  is equally futile under the desktop app (same unauthenticated CLI subprocess), but disabling it is a
  separable system-level change; filed as a follow-up to keep this PR focused.

## Consequences

**Positive:** merges under the desktop app no longer pay a ~35s doomed refresh and no longer print an
impossible remedy; the skip is fast and its message is true. npm-CLI installs are entirely unaffected
(no probe subprocess, legacy path unchanged). The new helpers are pure/injected and offline-tested.

**Negative / residual:**

- The usage snapshot **is unavailable under the desktop app**, and this ADR does not change that — no
  sanctioned token source exists. The tracking of weekly/5-hour utilization is dark on this machine
  until (if ever) the desktop app exposes a readable token or a scriptable usage command.
- The probe adds one sub-second subprocess in the missing-token branch on MSIX installs. Detection is
  best-effort: a probe failure degrades to `None` → the legacy path, never a crash.
- The `ClaudeKeepTokenWarm` scheduled task remains registered and futile until the deferred follow-up
  disables it.
- `resolve_claude_exe()` hardcodes the `Claude_pzs8sxrjxfjjc` MSIX package family (a second copy of the
  string `keep-token-warm.ps1` already carries); a cross-reference comment keeps them together.

## References

- [dev-env#915](https://github.com/brownm09/dev-env/issues/915) — the investigation and this fix.
- [ADR-043](043-keep-warm-scheduled-task-for-token-freshness.md) — keep-warm scheduled task; the CLI
  owns the token write; the setup-token **403** finding this ADR relies on.
- [ADR-044](044-eliminate-usage-snapshot-gap-on-demand-refresh.md) — on-demand refresh at merge,
  including the missing/unparseable-token branch (#819) this ADR gates.
- [ADR-034](034-error-message-diligence.md) — a guard message must describe what is actually true; the
  old "run claude interactively" advisory violated this here.
- [ADR-103](103-shared-hookout-emitter.md) — `_hookout.emit_block`: the exit-2 stderr channel + ASCII
  wire-safety the advisory uses.
- #355 / #357 — the original "surface a missing/expired token, don't fail silently" decision this
  extends with an *accurate* (not merely visible) message.
