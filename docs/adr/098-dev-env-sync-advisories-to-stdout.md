# ADR-098: Deliver dev-env-sync.py Advisories on stdout (Not stderr) on This Always-Exit-0 UserPromptSubmit Hook

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** hooks, UserPromptSubmit, dev-env-sync, exit-code, stdout, stderr, claude-facing, silent-failure, adr-027, adr-058, adr-091

---

## Context

`dev-env-sync.py` fast-forward-pulls the canonical dev-env checkout to `origin/main` on every
prompt, so `~/.claude/`'s symlinked `CLAUDE.md`, hooks, and scripts stay current. It always
exits 0 (never blocks the prompt) and, prior to this change, routed every warning — a failed
fast-forward pull, a diverged local `main`, a worktree squatting `main`, an unreadable worktree
list, a failed auto-return-to-`main` checkout — to **stderr**, matching the surface pattern of
[ADR-027](027-userpromptsubmit-blocking-hook-conventions.md)'s original "stderr for blocking"
guidance. Only the successful-pull message used `print()` to stdout.

[dev-env#694](https://github.com/brownm09/dev-env/issues/694) reported that the hook printed
"Pulled 0 commits from origin/main" while the canonical checkout was in fact ~45 merged PRs
behind `origin/main`. Investigating live in the canonical checkout surfaced a **currently
active occurrence of the same class of drift**: `git status` showed the checkout 21 commits
behind with an uncommitted, unrelated modification to `claude/skills/sources.md` (leftover
`/research`-sourced content from an earlier session, never committed). Reflog evidence pinned
the exact mechanism:

- Local `main` last successfully fast-forwarded 2026-07-09 00:06:01 (commit `33b0036`).
- 34 minutes later, PR #649 merged on `origin/main`, touching the exact same file
  (`claude/skills/sources.md`) that was locally dirty.
- From that moment forward, **every single** `git pull --ff-only origin main` attempt failed
  identically (`error: Your local changes to the following files would be overwritten by
  merge`) — reproduced live, manually invoking the hook, 36+ hours and 21+ commits later.
- The hook's existing code already detected this failure correctly (`pull.returncode != 0`)
  and printed a `WARNING` — to stderr.

A separate session earlier the same day independently discovered this identical dirty-file
condition from a different angle (an engineering-journal canonical-hijack investigation that
traced back to `journal-canonical-guard.py` never having actually deployed on this machine,
because the same 21-commit-behind dev-env canonical never received it) and filed
[dev-env#697](https://github.com/brownm09/dev-env/issues/697) with the direct remediation —
committing the pending `sources.md` content, mirroring PR #649's exact precedent — plus a
spawned tile to carry it out. That issue and this ADR are complementary, not duplicative:
#697 fixes *this specific occurrence* of the drift; this ADR fixes the hook's *general*
inability to ever surface such an occurrence to begin with, so the next one (whatever future
file conflicts) doesn't again require an hours-long investigation to notice.

That warning never reached anyone. Direct proof from this same investigation session: the hook
fired (confirmed — the dirty-file-blocked state matched exactly what a fresh invocation would
produce) immediately before this conversation's first turn was processed, yet **no
`[dev-env-sync]` text of any kind appeared anywhere in the model's context that turn**. Per the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the same primary source
[ADR-091](091-journal-stop-check-archive-reminder-blocking.md) already quotes for the mirror-
image bug on a `Stop` hook — **only `UserPromptSubmit`, `UserPromptExpansion`, and
`SessionStart` get their exit-0 **stdout** added to Claude's context; stderr is not surfaced for
those event types on exit 0.** `dev-env-sync.py` is a `UserPromptSubmit` hook that always exits
0, so its stderr warnings were never anything but silently discarded — for as long as the
underlying condition (here, a dirty conflicting file) persisted. `new-day-journal-check.py`
already documents the correct half of this contract in its own docstring ("Stdout is injected as
context Claude sees before processing the user's message"); `dev-env-sync.py` simply never
followed it for anything but its success path.

This is the same **failure class** ADR-091 fixed for `journal-stop-check.py`, approached from
the opposite direction: ADR-091's hook needed to move a message *from* exit-0 stdout *to* exit-2
stderr, because `Stop` hooks forward *neither* stream on exit 0. `dev-env-sync.py` needs the
mirror-image move — stderr to stdout — because `UserPromptSubmit` (unlike `Stop`) *does*
forward exit-0 stdout. Both bugs share one root cause: assuming a single blanket stream
convention applies across every hook event type, when the actual contract is per-event-type
and exit-code-dependent. `claude/CLAUDE.md`'s own `## Observability` section stated exactly that
false blanket rule ("routes diagnostics to stderr, not stdout, which Claude Code consumes") —
corrected in this same change.

A second file, `journal-canonical-guard.py` (merged the day before this investigation, PR #661),
shares the identical stderr-on-exit-0-`UserPromptSubmit` pattern for all of its warnings. It is
**not** fixed here — see Scope below — but is filed as a same-class follow-up
([dev-env#699](https://github.com/brownm09/dev-env/issues/699)).

## Decision

Route every advisory in `dev-env-sync.py` to **stdout**, keeping the hook's existing **exit 0
always** contract unchanged (this is not a blocking hook, and none of its conditions warrant
erasing the user's prompt):

- All six warning paths (worktree-list-unreadable, warn-squatter, warn-dirty off-main,
  auto-return-checkout-failed, diverged, fast-forward-pull-failed) drop `file=sys.stderr` and
  print to stdout via a plain `print(...)`, matching the file's own pre-existing, proven-working
  success-message convention (the ADR-027 2026-05-27 amendment's `additionalContext` JSON
  envelope was considered and rejected — see Alternatives).
- The `⚠️` emoji prefix used by four of those warnings is replaced with a plain `WARNING:` tag.
  This is not merely cosmetic: `⚠️` (U+26A0 + U+FE0F) has no Windows-1252 mapping, while these
  messages were previously on stderr (silently discarded regardless of encoding). Moving them to
  stdout — a channel Claude Code actually decodes — makes the encoding-safety requirement live
  for the first time; `WARNING:` avoids trading one invisibility bug for a
  `UnicodeEncodeError`-on-cp1252 one, consistent with the ASCII-only convention already
  established for other hooks piping through this same Windows cp1252 constraint (ADR-091;
  `stop-tile-enumeration-gate.py`; `posttooluse-inert-advisory.py`). The pre-existing em dash
  (`—`, U+2014) in the success message is unchanged — it *is* representable in cp1252 (0x97),
  and the original issue reporter directly quoted it back verbatim, which is itself empirical
  proof it survives this hook's actual stdout path.
- The fast-forward-related messages (pulled / diverged / pull-failed) now state local/remote
  short SHAs and a commit-behind count, computed once via `git rev-list --count` immediately
  after the merge-base check (so it's available to whichever of the three downstream branches
  fires, without repeating the call). This directly implements dev-env#694's own suggested
  follow-up ("print the local and remote SHAs … alongside 'Pulled N commits'").
- The success message additionally compares its pre-pull `behind_count` against the actual
  post-pull `git log --oneline` range and, on a mismatch, prints an explicit note naming a
  concurrent process as the likely cause. This targets dev-env#694's central unresolved
  ambiguity directly: the reported "Pulled 0 commits" text could not be reproduced from the code
  as a single deterministic path (that branch only executes after the code has already confirmed
  `local != remote`), and is most plausibly explained by a TOCTOU race between two sessions'
  `dev-env-sync.py` invocations against the same shared, unlocked canonical checkout — a
  scenario the code does not prevent (no lock file is introduced by this change; see
  Alternatives) but must now at least explain when it recurs.
- `main()`'s call site is wrapped in `try`/`except Exception: sys.exit(0)`, matching the
  established fail-open convention this same review cycle added to the sibling
  `journal-canonical-guard.py` (PR #661) and to `new-day-journal-check.py` — honoring the "Exit 0
  always" docstring contract even against an unexpected subprocess timeout or `git` binary
  absence, which is now marginally more likely with the two additional `rev-list` calls this
  change adds.

### Scope

Deliberately limited to `dev-env-sync.py` — the file dev-env#694 actually named, and the one
with a confirmed, reproduced, currently-active incident. `journal-canonical-guard.py` has the
identical defect (confirmed by inspection during this investigation: every warning path there
also uses `file=sys.stderr` on an always-exit-0 `UserPromptSubmit` hook) but is a different
file, introduced for a different incident, one day before this investigation began. Expanding
this change to fix it too would have roughly doubled this PR's footprint for a file this PR's
motivating issue never named. Filed as [dev-env#699](https://github.com/brownm09/dev-env/issues/699)
instead, citing this ADR directly so the fix (when done) is a small, well-precedented diff
rather than a rediscovery.

## Consequences

- A dirty-working-tree-file pull failure (or a genuine divergence, or a worktree-squatting
  condition) is now visible to Claude — and therefore to the user — on the very next prompt,
  instead of silently repeating every turn indefinitely.
- Every fast-forward-related message is self-diagnosing: a future "why did it say N commits"
  question is answerable from the message text alone, without a manual `git log`/`git fetch`
  comparison against the canonical checkout.
- Three additional cheap `git rev-list --count` subprocess calls per prompt (only on the already-
  rare path where `local != remote`); negligible relative to the fetch/pull calls already made
  on that same path.
- `journal-canonical-guard.py` still has the pre-fix defect; tracked, not fixed, here (see Scope
  and dev-env#699).
- No lock file or other TOCTOU mitigation is introduced for the shared-canonical-checkout race
  this ADR's Context section describes as the likely explanation for dev-env#694's original,
  unreproduced "0 commits" report — the new mismatch-note (Decision, 4th bullet) makes that race
  visible when it recurs, which was judged sufficient given the race is rare (requires two
  sessions' `UserPromptSubmit` hooks firing within the same few-hundred-millisecond window) and a
  proper fix (e.g. a per-repo advisory lock) is a materially larger change than this ADR's scope.

## Alternatives considered

- **Keep the messages on stderr, add an exit-2 blocking path instead (mirroring ADR-091's
  fix direction).** Rejected — unlike `journal-stop-check.py`'s archive reminder (which asks
  *Claude* to take a specific in-session action and is therefore appropriate to block on),
  every one of `dev-env-sync.py`'s conditions is advisory: a dirty file, a divergence, or a
  squatting worktree are all pre-existing states the user needs to *know about*, not states that
  should erase their in-flight prompt. The hook's own docstring ("Exit 0 always — never block the
  user's prompt") predates this ADR and is not being revisited.
- **JSON `hookSpecificOutput.additionalContext` envelope instead of plain `print()`.** A valid
  mechanism per ADR-027's 2026-05-27 amendment, and what `session-mode-prompt.py` uses. Rejected
  in favor of matching this file's own pre-existing, already-proven-working plain-text
  convention (the original issue reporter directly quoted the plain-text success message
  verbatim) — introducing a second output schema into the same file for no functional gain adds
  inconsistency without benefit.
- **Introduce a lock file to prevent concurrent `dev-env-sync.py` invocations from racing against
  the same canonical checkout.** Would close the TOCTOU race entirely rather than merely
  detecting it after the fact. Rejected as out of scope for this ADR: the race is rare, no
  corruption results (git's own ref-update atomicity and `--ff-only` guarantee that much), and a
  proper per-repo advisory lock is a materially larger change better sized as its own follow-up
  if the new mismatch-note (Decision, 4th bullet) shows the race recurring in practice.
- **Fix `journal-canonical-guard.py` in the same PR.** Rejected per Scope above — different file,
  different motivating incident, would have roughly doubled this PR's footprint;
  [dev-env#699](https://github.com/brownm09/dev-env/issues/699) tracks it instead.

## References

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — the exit-code /
  stdout-vs-stderr / per-event-type semantics this ADR (and ADR-091, which first quoted it in
  this repo) relies on.
- [ADR-027](027-userpromptsubmit-blocking-hook-conventions.md) — the base "stderr for blocking,
  stdout/additionalContext for exit-0 context injection" contract this ADR applies correctly for
  the first time in `dev-env-sync.py`.
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — the worktree-squatting
  detection/correction logic whose warning messages this ADR's stream fix also covers.
- [ADR-091](091-journal-stop-check-archive-reminder-blocking.md) — the mirror-image bug and fix
  on a `Stop` hook; the closest precedent for this exact failure class.
- [ADR-093](093-journal-canonical-hijack-guard.md) — `journal-canonical-guard.py`, the sibling
  file sharing this same pre-fix defect (see Scope).
- [dev-env#694](https://github.com/brownm09/dev-env/issues/694) — the issue this ADR closes.
- [dev-env#697](https://github.com/brownm09/dev-env/issues/697) — the complementary,
  independently-filed issue for the direct remediation (commit the pending dirty `sources.md`
  content) of this ADR's motivating live incident; see Context.
- [dev-env#699](https://github.com/brownm09/dev-env/issues/699) — the follow-up issue for
  `journal-canonical-guard.py`'s parallel defect.
