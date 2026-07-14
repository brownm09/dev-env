# ADR-108: PostToolUse Hook Family — PowerShell Matcher Parity

**Date:** 2026-07-14
**Status:** Accepted
**Tags:** hooks, post-tool-use, powershell, matcher, wiring, parity, adr-050, adr-071

---

## Context

PR9 of the #717 hook-reliability initiative (`config/powershell-matchers-620`, PR
[#762](https://github.com/brownm09/dev-env/pull/762)) extended all 12 **PreToolUse** safety hooks
to also fire for the PowerShell tool, not just Bash — PowerShell is a fully sanctioned parallel
shell in this environment (the PowerShell tool's own description: *"for terminal operations via
PowerShell: git, npm, docker, and PS cmdlets"*; global `claude/CLAUDE.md` lists it as the primary
shell), but every hook was wired under `claude/settings.json`'s `"Bash"` matcher only. That fix is
documented as Amendment 4 of [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the
canonical-mutate-guard hook's own ADR, reused as the PreToolUse family's documentation home since
canonical-mutate-guard is one of the 12 hooks and the most heavily-documented one in that batch.

That same session filed [dev-env#761](https://github.com/brownm09/dev-env/issues/761) to track four
deliberately-deferred follow-ups and spawned a tile for the highest-priority one: the **PostToolUse**
family (`pr-merge-reminder.py`, `post-tool-use.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`,
`stub-push-archive-reminder.py`, `post-pr-merge-project.py`, `usage-snapshot.py`,
`journal-shard-write-advisory.py`, `post-tool-use-cwd-track.py`, `post-merge-tile-checkpoint.py`) is
equally Bash-only. A PR opened, pushed, or merged via the PowerShell tool silently skipped: the
journal-stub reminder, the project-board add/move, the local-`main` fast-forward, the disk reclaim,
the tile-enumeration checkpoint, and the usage-snapshot report — [dev-env#763](https://github.com/brownm09/dev-env/issues/763)
is the sub-issue for this specific follow-up.

Reusing ADR-071 for this fix (mirroring PR9's own precedent) was considered and rejected — see
*Alternatives considered* below.

## Decision

Audited each of the 10 hooks for the same `tool_name`-gate pattern PR9 found, and found three
distinct fix shapes rather than one:

1. **8 hooks** needed a one-line gate widen (`!= "Bash"` → `not in ("Bash", "PowerShell")`) plus a
   docstring note: `pr-merge-reminder.py`, `post-tool-use.py`, `post-pr-merge-pull.py`,
   `post-pr-merge-reclaim.py`, `post-pr-merge-project.py`, `usage-snapshot.py`,
   `post-tool-use-cwd-track.py`, `post-merge-tile-checkpoint.py`.
2. **1 hook** needed its tuple gate extended: `journal-shard-write-advisory.py`
   (`tool_name not in ("Write", "Edit", "Bash")` → add `"PowerShell"`) — it is wired under three
   PostToolUse matchers (Write/Edit/Bash), and only the Bash-equivalent trigger needed the mirror.
3. **1 hook** needed **zero code change**: `stub-push-archive-reminder.py` has no `tool_name` gate
   at all — it reads `tool_input.command` unconditionally, relying entirely on the settings.json
   matcher to control which invocations reach it. Once a PowerShell matcher exists, it just works —
   the identical shape `disk-space-check.py` had in PR9's own PreToolUse audit (left completely
   untouched there for the same reason).

**Unlike PR9's PreToolUse audit, none of the 10 PostToolUse hooks needed the deeper "layer 2/3" fix**
that 2 of the 12 PreToolUse hooks required (a hand-rolled, non-shared command-detection regex needing
its own `{`-boundary addition to recognize PowerShell's `A; if ($?) { B }` conditional idiom, absent
`&&`/`||` in PowerShell 5.1). Confirmed by reading all 10 files in full plus a targeted grep for PR9's
tell-tale hand-rolled anchor-regex shape (`(?:^|&&|\|\||;|\n)...`): 9 of the 10 hooks already route
command-shape detection through the shared `_hookio.scan_top_level` (which already carries PR9's
here-string/`{`-boundary parser fix — inherited automatically once this branch is rebased onto or
past PR9, with no duplicate work needed here), and the 10th (`post-tool-use-cwd-track.py`) does no
command-content parsing at all. This PR is layer-1 only: matcher wiring + `tool_name` gate.

Mechanically:

- `claude/settings.json` — new `"PowerShell"` matcher block under `PostToolUse`, mirroring the
  `"Bash"` block's 10 entries (identical commands/timeouts).
- `claude/scripts/tests/test_settings_hook_wiring.py` — new
  `test_posttooluse_bash_and_powershell_matchers_are_mirrored()`, structurally identical to PR9's
  `test_pretooluse_bash_and_powershell_matchers_are_mirrored()` (event scoped to `"PostToolUse"`
  instead of `"PreToolUse"`), so a future 11th hook added to one matcher and forgotten on the other
  is caught mechanically rather than silently reopening this gap.
- `README.md` — the Hooks table's `PostToolUse (Bash)` / `PostToolUse (Write/Edit/Bash)` labels for
  these 10 rows updated to `PostToolUse (Bash/PowerShell)` / `PostToolUse (Write/Edit/Bash/PowerShell)`
  — the same "Bash-exclusive language that became stale" cleanup PR9 did for `docs/REFERENCE.md`.
  `docs/REFERENCE.md`'s own hook-table trigger descriptions were checked and found already
  tool-agnostic (they describe command *content* — e.g. "Command contains `gh pr merge`" — not which
  tool invoked it), so no change was needed there.

**Deliberately out of scope** (per #761/#763's own scoping, items 1–3 of #761): `Set-Location`/`sl`
parsing, the `shlex.split(posix=True)` vs. PowerShell-quoting mismatch, and PowerShell-native override
syntax — none of which are exercised by any of these 10 hooks' own logic in a way this audit
surfaced.

## Consequences

- A PR opened, pushed, or merged via the PowerShell tool now reaches every PostToolUse hook a
  Bash-run equivalent already did: the journal-stub reminder, the project-board add/move, the
  local-`main` fast-forward, the disk reclaim, the tile-enumeration checkpoint, and the
  usage-snapshot report.
- The new regression test closes the same class of silent-drift risk PR9's test closed for
  PreToolUse — a future hook wired to only one of the two PostToolUse matchers now fails the test
  suite instead of silently reopening the Bash-only gap.
- Items 1–3 of #761 remain open, tracked there for opportunistic pickup or a further dedicated PR.

## Alternatives considered

- **Amend [ADR-071](071-canonical-checkout-mutate-guard-hook.md)** (PR9's own precedent for the
  PreToolUse fix). Rejected — ADR-071's title and tags (`pre-tool-use`, `canonical-checkout`, etc.)
  are specifically scoped to the canonical-mutate-guard hook; none of the 10 PostToolUse hooks in
  this change relate to that hook's subject (blocking git-mutating commands in a canonical checkout)
  at all. Appending an unrelated PostToolUse-family amendment there would be topically confusing to
  a future reader scanning ADR-071 for canonical-mutate-guard rationale.
- **Amend [ADR-050](050-shared-hookio-sibling-hook-fixes.md)** (the other broad "fixes accumulator"
  candidate, already carrying 22 amendments for assorted PostToolUse hook fixes). Rejected — ADR-050
  is specifically about `_hookio.read_command_output` / command-parsing-layer fixes; this change made
  zero such fixes (the audit's central finding is that none were needed), so filing under it would
  misrepresent the change's actual nature.
- **New, standalone ADR** (chosen) — a topically clean, independently discoverable home for
  "PostToolUse hook family PowerShell matcher wiring," matching the *"open ADR files only on a tag
  match"* guidance in `claude/CLAUDE.md`'s ADR-warrant check: no existing ADR tagged `matcher`,
  `wiring`, or `posttooluse` (generically) existed to match against.

## References

- [dev-env#620](https://github.com/brownm09/dev-env/issues/620),
  [PR #762](https://github.com/brownm09/dev-env/pull/762) (PR9) — the PreToolUse precedent this
  change mirrors.
- [dev-env#761](https://github.com/brownm09/dev-env/issues/761) — parent tracking issue documenting
  all four deferred PowerShell-coverage follow-ups.
- [dev-env#763](https://github.com/brownm09/dev-env/issues/763) — this change's own sub-issue.
- [dev-env#717](https://github.com/brownm09/dev-env/issues/717) — top-level hook-reliability
  initiative.
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) Amendment 4 — the PreToolUse-family analog
  this ADR is the PostToolUse-family sibling of.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — the shared `_hookio.scan_top_level` parser
  this audit confirmed already covers 9 of the 10 PostToolUse hooks' command-shape detection.
