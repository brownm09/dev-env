# ADR-041 — No Spawning New Terminal Windows in Windows Scripts

**Date:** 2026-06-06 (amended 2026-11-05)
**Status:** Accepted
**Closes:** [dev-env#324](https://github.com/brownm09/dev-env/issues/324)
**Tags:** code-quality, global-rule, windows, powershell, claude-behavior, review, sudo, uac, elevation
**Related:** [ADR-038](038-durable-preferences-documented-in-repo.md), [ADR-040](040-global-claudemd-layering-and-slimming.md), [ADR-039](039-merge-gate-findings-enforcement.md)

---

## Context

A recurring failure mode in Windows PowerShell scripts authored or reviewed by Claude: the script spawns a new console window or triggers a UAC re-launch from inside itself. Concretely:

- `Start-Process powershell -Verb RunAs` self-relaunch elevation
- `Start-Process ... -NoExit` to open a "stays open" sibling console
- `cmd /c start "title" powershell ...`
- Any other "open in a new console" round-trip

In **agent-driven and non-interactive contexts** these patterns fail in predictable ways:

1. The UAC dialog has no desktop to render against. The prompt hangs the session, or is silently dismissed by the OS, and the script never makes forward progress.
2. The spawned console dies before producing output, so the agent has no log to react to.
3. The user has to manually disentangle orphan processes — every encounter spends real cleanup tokens.

Even in **interactive use** the patterns are a usability regression: the spawned window is detached from the parent terminal, output is fragmented across windows, and exit codes do not propagate.

The motivating sequence:

- The user fixed a script that did this **yesterday** (2026-06-05) at some cost.
- This session, win11-init-tools PR #19 landed `configure_dev_env.ps1` containing exactly the same `Start-Process -Verb RunAs` self-relaunch (`Ensure-Admin` function, lines 42–50).
- I reviewed the PR and only flagged the side effect ("PATH detection runs before admin relaunch") as a **non-blocking** correctness note, on the grounds that win11-init-tools' project `CLAUDE.md` "documents the self-relaunch pattern as kept for end-user convenience."
- That documentation applies to a closed allowlist of pre-existing files the user opens by double-click from Explorer. It is **not** a license to extend the pattern.
- The PR merged; the user reminded me again, citing token cost.

The cost of the missed rule is real (tokens spent fixing the script after the fact) and recurring (twice in two days). The rule is **not** win11-init-tools-specific — it applies to any PowerShell or batch script in any project, including dev-env scheduled-tasks routines, lifting-logbook helper scripts, ad-hoc fixes in scratch, and any future Windows automation.

## Decision

Two additions, one each at the rule layer and the rationale layer:

1. **A new `### No spawning new terminal windows (Windows scripts)` subsection in global `claude/CLAUDE.md` under `## Code Quality`.** Placed immediately after `### Fix errors on encounter` (same altitude: a short behavioral rule, not a grep policy). States the rule, the patterns it covers, the rationale, the `#Requires -RunAsAdministrator` and inline `IsInRole` alternatives, a closed allowlist of three pre-existing files, and a review-time enforcement clause that any new file matching the patterns is a **blocking [reliability] finding** regardless of any project CLAUDE.md exemption text.

2. **This ADR.** Captures the incident sequence, the token-cost rationale, the allowlist mechanics, and the rejected alternatives.

The allowlist is closed: `win11-init-tools/install_base_apps.ps1`, `set_irfanview_image_assoc.ps1`, and `configure_dev_env.ps1`. New files needing elevation must use `#Requires -RunAsAdministrator`. A follow-up issue in `win11-init-tools` tracks converting `configure_dev_env.ps1` so the allowlist shrinks to the two files with the strongest Explorer-double-click UX justification.

## Rationale

**Why global CLAUDE.md and not the win11-init-tools project CLAUDE.md.** Per [ADR-040](040-global-claudemd-layering-and-slimming.md), project-specific content belongs in project CLAUDE.md and the global file retains only genuinely cross-project content. The anti-pattern is not specific to win11-init-tools — it would apply identically to a scheduled-tasks routine in dev-env, a recovery helper in lifting-logbook, or a scratch script anywhere. Placing the rule in the global file means it loads in every Windows-context session, not only when the user is editing win11-init-tools.

**Why not memory-only.** Per [ADR-038](038-durable-preferences-documented-in-repo.md), durable rules must live in the version-controlled repo, not only in agent memory: memory is invisible to humans and unreliably consulted. A machine-local `feedback_no_terminal_spawn.md` was written this session as a recall convenience and is kept (it points to this ADR), but the source of truth for the rule is `claude/CLAUDE.md`.

**Why a closed allowlist instead of a blanket ban.** The three exempted files exist primarily for users who double-click them from Explorer. In that path the UAC dialog has a desktop, the new console hosts the install/configure UX, and the pattern is genuinely the lowest-friction option. Removing it would force every consumer to first open an elevated terminal, which is the same UX regression the original authors avoided. The cost only materializes in agent contexts; an allowlist captures the trade-off honestly.

**Why review enforcement is blocking, not advisory.** This session demonstrated that a non-blocking "by the way" note loses against an existing project CLAUDE.md sentence that reads like an exemption. Blocking enforcement at `/review` ensures the rule cannot be downgraded by a context that mentions the historical pattern. Pairs with [ADR-039](039-merge-gate-findings-enforcement.md) which mechanically enforces "all findings must be addressed before merge."

**Why the file-size budget for the addition is small.** The global file just landed under the ~41k-character budget set by ADR-040. The new subsection is ~1.4k characters, leaving headroom for other rules. Verbosity here would re-bloat the global file; the ADR carries the detailed rationale instead.

## Alternatives considered

- **Project CLAUDE.md only (win11-init-tools).** Rejected on layering grounds (the rule is cross-project) and on enforcement grounds (a project-scoped rule does not fire when authoring a PS script in scratch, a scheduled-task routine, or any other repo).
- **Hook-based grep blocker.** Rejected for now. The patterns are easy to grep (`Start-Process.*-Verb RunAs`, `-NoExit`, `cmd /c start`) but high false-positive risk on existing legitimate exempted files, and the failure mode is rare enough that a behavioral review-time block is proportionate. Re-evaluate if the rule is violated again after this ADR lands.
- **Ban the pattern entirely, including for the allowlist files.** Rejected: removes a real UX path for Explorer-double-click users and produces no agent-context benefit (those files are not run by agents).
- **Memory-only rule.** Rejected per ADR-038.
- **Wait and only flag at review.** Rejected: the rule needs to fire at *authoring* time, not just review time, or a session that writes its own PowerShell script reaches the review step with the anti-pattern already committed.

## Consequences

**Positive:**

- The rule is loaded into every session that opens a Windows script, not only sessions on win11-init-tools.
- `/review` has explicit, blocking language to cite — no more "documented project pattern" downgrade.
- The allowlist is auditable; future deletions from it are visible in git history.
- The recall memory file (`feedback_no_terminal_spawn.md`) becomes a pointer to the canonical rule rather than the source of truth, matching the ADR-038 pattern.

**Negative:**

- Global CLAUDE.md grows by ~1.4k characters. Net delta after ADR-040's slim is still well under the ~41k budget.
- Adds one more rule to the behavioral set the model must internalize. Mitigated by the placement (immediately after `### Fix errors on encounter`, a related short behavioral rule).
- Enforcement remains behavioral, not mechanical. Pairs with the all-findings merge gate (ADR-039) to bound the cost of misses.

## References

- [dev-env#324](https://github.com/brownm09/dev-env/issues/324) — issue tracking this change.
- [ADR-038](038-durable-preferences-documented-in-repo.md) — durable preferences in the repo, not memory only.
- [ADR-040](040-global-claudemd-layering-and-slimming.md) — global CLAUDE.md layering rule (cross-project content only).
- [ADR-039](039-merge-gate-findings-enforcement.md) — mechanical all-findings merge gate (pairs with this rule's blocking review enforcement).
- [win11-init-tools#19](https://github.com/brownm09/win11-init-tools/pull/19) — the missed-flag incident that motivated the ADR.
- [win11-init-tools#20](https://github.com/brownm09/win11-init-tools/issues/20) — followup tracking conversion of `configure_dev_env.ps1` and subsequent allowlist shrink.
- Microsoft Learn — [`Start-Process` parameter reference](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process) (documents `-Verb`, `-NoNewWindow`, `-PassThru` and confirms `-Verb RunAs` triggers UAC).
- Microsoft Learn — [`#Requires` statement](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_requires) (documents `-RunAsAdministrator`, the recommended replacement).

## Amendment 1 (2026-11-05) — a session elevates with `sudo` ([dev-env#1231](https://github.com/brownm09/dev-env/issues/1231))

The script rule above stands: a script fails fast with `#Requires -RunAsAdministrator` or an `IsInRole` check, and never relaunches itself. This amendment changes only what a *session* does when it needs to run something elevated. The rule used to say "Tell the user to open an elevated terminal". A session now runs `sudo <command>` and asks the user only if `sudo` fails or is unavailable. The rule itself lives in `claude/CLAUDE.md` → *Platform & Environment*.

**Why.** On 2026-11-05 a win11-init-tools session asked the user to run `fix_bluetooth_reconnect.ps1 -RestartService` in an elevated terminal. The user replied "You run it", and the command then ran from the Bash tool with `sudo`, returning its output and exit code ([win11-init-tools#102](https://github.com/brownm09/win11-init-tools/pull/102)). Windows `sudo` is in Inline mode on this machine. The old sentence, and a matching line in *User-Actionable References*, pushed sessions toward the ask.

**What was established** (2026-11-05, Windows 11 10.0.26200). Rows 1–5 ran from Git Bash with `MSYS_NO_PATHCONV=1` set and stdin closed:

| # | Check | Result |
|---|---|---|
| 1 | `sudo cmd /c "exit 7"` | returned 7, in 5 s |
| 2 | `sudo powershell -NoProfile -Command "exit 3"` | returned 3, in 4 s |
| 3 | `sudo cmd /c "net session >nul 2>&1 && echo elevated \|\| echo not-elevated"` | printed `elevated`, returned 0; the same `net session` from the unelevated shell returned 2 |
| 4 | `sudo nonexistent-command-xyz` | printed `Command not found`, returned 49, in under 1 s |
| 5 | `sudo cmd /c "echo to-stderr 1>&2 & exit 5"` with stderr redirected | returned 5; the text landed in the redirect target, so stderr stays separate |
| 6 | Polling for `consent.exe` during `sudo cmd /c "exit 0"` | `consent.exe` ran for about 2.3 s of a 3.1 s call |
| 7 | `sudo cmd /c "exit 7"` from Git Bash, no prefix | `/c` reached `cmd` as `C:/` (confirmed by printing `argv`); an interactive elevated prompt opened and hung |
| 8 | Row 7 with `</dev/null`, under `timeout 40` | still hung, until `timeout` killed `sudo` at 40 s; the elevated `cmd` survived, identified by name, a null `CommandLine` and a start time during the call, and needed `sudo taskkill /PID <pid> /F` |

**UAC.** [Microsoft's documentation](https://learn.microsoft.com/en-us/windows/advanced-settings/sudo/) says a prompt opens for verification, and `sudo --help` says the same. The [source](https://github.com/microsoft/sudo/blob/main/sudo/src/run_handler.rs) elevates through `ShellExecuteExW` with the `runas` verb. So UAC is involved on every call, and the Inline-mode security risks Microsoft describes are the ones already accepted by enabling that mode. Row 6 shows the prompt process ran. It does not show what was on screen, and the 3–5 s calls don't say who answered. The [source](https://github.com/microsoft/sudo/blob/main/sudo/src/main.rs) maps a cancelled prompt to a printed message and a non-zero exit, which was read but not run, since declining needs a person.

**Correction to Context item 1.** "The UAC dialog has no desktop to render against" is not true of every agent-driven context: rows 1–6 elevated from an agent's shell in a logged-in desktop session. A scheduled run was not tested, so the rule says only that a routine shouldn't count on `sudo`.

**No mechanical check.** A Stop hook matching text like "open an elevated terminal" was considered and not added. The only known-bad reference is the one incident, there is no known-good corpus, and the shape the rule allows (asking after `sudo` failed) reads the same as the shape it forbids. Under Pass 3 Gate calibration that would be a diagnostic at most. As with the original rule, re-evaluate if it recurs now that the instruction is written down.
