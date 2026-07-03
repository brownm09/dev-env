# ADR-079 — Back Up Before You Mutate: Reversible Data & Config Convention

**Date:** 2026-07-03
**Status:** Accepted
**Closes:** [dev-env#548](https://github.com/brownm09/dev-env/issues/548)
**Tags:** workflow, code-quality, backup, restore, idempotency, global-rule, windows, powershell, claude-behavior
**Related:** [ADR-038](038-durable-preferences-documented-in-repo.md), [ADR-040](040-global-claudemd-layering-and-slimming.md), [ADR-041](041-no-terminal-spawn-in-windows-scripts.md), [ADR-048](048-memory-immortalization-issue-pairing.md), [ADR-059](059-multi-pr-issue-hierarchy.md)

---

## Context

The win11-init-tools repo accumulates scripts that mutate persistent system state — registry keys, pagefile configuration, file associations, Quick Access pins, `PATH`, installed-app state. A change that goes wrong (a bad pagefile size, a hijacked file association, a wiped pin list) can leave the machine worse than before, with no way back, unless the script captured the prior state *before* it started writing.

`configure_pagefile.ps1` (win11-init-tools PR #36, commit `c1b75a0`) implements the right pattern end to end: before its first mutation, `-SetCustomSize` captures the live `AutomaticManagedPagefile` flag plus every `Win32_PageFileSetting` to a one-time JSON anchor (`Documents\LOGS\ConfigurePagefileBackup.json`), refuses to proceed if it cannot read or write that backup, and `-Restore` reconciles the system back to exactly that captured state. The anchor is written only if absent and is never deleted by restore, so both directions are idempotent; every WMI write is verified by read-back.

The maintainer asked that this pattern be generalized so future scripts follow it. The problem: the convention lived nowhere durable.

- Neither `win11-init-tools/CLAUDE.md` nor `win11-init-tools/.claude/CLAUDE.md` documented any backup rule.
- The global `claude/CLAUDE.md` carried only an *ask-first* stance for destructive actions (via the harness), not an *always-reversible* one.
- A prior attempt to capture a "config-backup rule" landed only on the **unmerged** win11 PR #30 (`fix_quick_access.ps1` pin-backup + `set_default_explorer.ps1`'s `Backup-RegKey` / `Restore-RegKey`), so the wording was stranded on a branch, absent from `main`.

Per [ADR-038](038-durable-preferences-documented-in-repo.md) and [ADR-048](048-memory-immortalization-issue-pairing.md), a durable convention must be immortalized in the version-controlled instructions, not left implicit in one reference script or on an unmerged branch.

---

## Decision

Document a **backup-and-restore convention** at two altitudes, following the [ADR-040](040-global-claudemd-layering-and-slimming.md) layering rule (principle global, concrete form in the project file):

1. **Global `claude/CLAUDE.md`** — a new `### Back up before you mutate (data & config)` subsection under `## Code Quality` states the cross-project principle. Any operation that mutates persistent data or configuration must be reversible by construction:
   - capture prior state to a restorable artifact first, read *live* at backup time, and **refuse to proceed if a restorable backup cannot be captured**;
   - provide an **idempotent restore** that returns the system to the *captured* state, not a generic default;
   - prefer a **written-if-absent anchor that restore does not delete**, so repeated applies preserve the original state and repeated restores converge;
   - **verify each mutating write by read-back**, logging a no-op as a skip.

2. **Project `win11-init-tools` `CLAUDE.md` + `.claude/CLAUDE.md`** — a new `## Backup & restore` section gives the concrete Windows/PowerShell form: the `Documents\LOGS\<ScriptName>Backup.json` anchor convention, verified WMI/registry writes, fail-fast `#Requires -RunAsAdministrator`, and three anchor forms keyed to the state being changed (structured → JSON; registry key → `.reg` export + sentinel; opaque list → text snapshot). `configure_pagefile.ps1` is cited as the reference implementation; the registry/`.reg` and pin-backup variants revive the wording previously stranded on PR #30.

This is a workflow rule other CLAUDE.md files reference, so it is recorded as an ADR (the [ADR-011](011-adr-warrant-check.md) warrant).

---

## Rationale

**Why "refuse if you can't back up" rather than "back up if you can."** The failure this prevents is a one-way door: a script that mutates state it could not capture leaves `-Restore` unable to return the system to where it was. Aborting before the first write is strictly safer than proceeding with a best-effort backup — an aborted run leaves the system untouched, which is always recoverable. `configure_pagefile.ps1` encodes exactly this: *"Refusing to change settings without a restorable backup."*

**Why read state _live_ at backup time.** Diagnostic scans run earlier in a script can be stale by the time the mutation happens. The backup must reflect the state the mutation is about to overwrite, so it is read fresh — immediately before the write and *after* the pre-flight guards, so an aborted guarded run leaves neither a backup nor a change.

**Why a written-if-absent anchor that restore never deletes.** Two idempotency properties fall out of it: repeated *apply* runs never overwrite the original pristine capture (they would otherwise snapshot an already-modified state, making restore a no-op), and repeated *restore* runs converge to the same result. Re-baselining is then an explicit, deliberate act (delete the artifact by hand), not an accident of running the script twice.

**Why a generic "reset to default" is not enough.** A default is not the user's prior state. Restoring to automatic pagefile management on a machine that had a deliberate custom size, or clearing a file association to "let Windows pick," silently discards the configuration the user actually had. Restore reconciles to the *captured* state and falls back to a default only when no anchor exists.

**Why verify writes by read-back and log no-ops as skips.** Registry/WMI writes can silently no-op or partially apply; a read-back confirms the change landed. Counting a no-op as a change inflates the summary and hides whether anything actually happened — so a no-op is logged as a `Skip` and excluded from the change total.

**Why global principle + project specifics.** [ADR-040](040-global-claudemd-layering-and-slimming.md): the reversibility principle is cross-project (it applies to a database migration as much as a registry write), so it belongs in the global file; the `Documents\LOGS\*.json` / `.reg` mechanics are Windows-specific and belong in the win11 project file. Keeping the global entry lean avoids taxing every session with platform detail.

---

## Alternatives considered

- **Leave the pattern implicit in `configure_pagefile.ps1`.** Rejected: a convention discoverable only by reading one script is not a convention. The next author has no reason to look there — which is how PR #30's rule ended up stranded in the first place.
- **Document only in the project (win11) CLAUDE.md.** Rejected: the reversibility principle is not Windows-specific. A future database or config-file mutation in another repo should inherit it; that requires a global home.
- **Merge PR #30 to un-strand its wording.** Rejected as out of scope: PR #30 also lands two unrelated scripts. Reviving the *wording* into the durable convention immortalizes the idea without coupling it to that PR's fate; PR #30 can rebase onto the convention.
- **A hook that blocks mutations lacking a backup.** Rejected as over-engineering: reliably detecting "does this script back up before it writes" statically is hard, and judging what counts as a persistent-state mutation is the kind of call a behavioral rule handles better than a grep — the same reasoning as [ADR-038](038-durable-preferences-documented-in-repo.md).

---

## Consequences

**Positive:**
- Future scripts that mutate persistent state have a documented, discoverable rule to follow, at both global and project altitude.
- The reversibility principle generalizes beyond Windows to any data/config mutation in any repo.
- PR #30's previously-stranded registry-backup wording is immortalized regardless of whether PR #30 merges.

**Negative:**
- Adds ~14 lines to the global CLAUDE.md (read every session) and a fuller section to the win11 project files — mitigated by keeping the global entry to the principle only.
- Enforcement is behavioral, not mechanical; compliance depends on session-by-session and review-time attention, like the other Code Quality rules.
- Three open win11 PRs (#30, #36, and this convention PR) touch the same instruction files; whichever merges first, the others rebase. The convention PR is placed in a distinct section to minimize textual conflict.

---

## References

- [dev-env#547](https://github.com/brownm09/dev-env/issues/547) — top-level initiative issue.
- [dev-env#548](https://github.com/brownm09/dev-env/issues/548) — dev-env sub-issue this ADR closes.
- [win11-init-tools#37](https://github.com/brownm09/win11-init-tools/issues/37) — project sub-issue for the win11 doc change.
- win11-init-tools PR #36 (`configure_pagefile.ps1`, commit `c1b75a0`) — the reference implementation.
- win11-init-tools PR #30 (`fix_quick_access.ps1` pin-backup, commit `8ff463f`; `set_default_explorer.ps1` `Backup-RegKey` / `Restore-RegKey`) — the registry / list-snapshot variants whose wording this convention revives.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences must be documented in the repo, not only in memory.
- [ADR-040](040-global-claudemd-layering-and-slimming.md) — global/project CLAUDE.md layering.
- [ADR-048](048-memory-immortalization-issue-pairing.md) — memory writes paired with an immortalization issue.
- [Microsoft Learn — `reg export` command](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/reg-export) and [`reg import`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/reg-import) — primary source for the registry `.reg` export/import backup mechanism used by the registry-key anchor form. The broader "capture-then-idempotent-restore" convention is a project heuristic generalized from `configure_pagefile.ps1`, not an external standard.
