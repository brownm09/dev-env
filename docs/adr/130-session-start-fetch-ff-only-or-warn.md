# ADR-130: Session-Start Fetch + Fast-Forward-Only-or-Warn Drift Detection

**Date:** 2026-08-08
**Status:** Accepted
**Tags:** hooks, session-start, git, drift-detection, silent-failure, fast-forward, worktree, canonical-checkout, shared-module, dev-env-sync-fast-follow, adr-071, adr-051, adr-085

---

## Context

Stale-checkout / `origin/main` drift has been named the #1 cross-repo friction three biweekly
retros running ([dev-env#910](https://github.com/brownm09/dev-env/issues/910) item 1, and
[dev-env#966](https://github.com/brownm09/dev-env/issues/966)) and remained unbuilt through all
three. It hit essentially every actively-worked repo in the 07-11..08-08 window:
career-playbook (detached HEAD 20+ commits behind, near-daily, producing two false "this isn't
implemented" blockers), win11-init-tools (drift 3x, detached HEAD, scripts genuinely missing
from disk), gas-lifting-logbook (a draft branch cut behind main, leaving a stale open-PR
shard), lifting-logbook (a wrong-branch pull scattering 26 files), and dev-env itself (a
41-hour, 21-commit silent drift — the incident behind ADR-110).

The failure mode is never a loud error. It is a silent wrong answer: a repo-wide absence check
that only ever saw a stale subtree, a "this feature is unbuilt" conclusion reached against a
checkout that was simply behind, or a branch cut from an already-stale base. `claude/CLAUDE.md`
already carries several hand-written rules telling Claude to fetch first before drawing a
conclusion (the CLI Scripting Checklist's "Ref scoping" item, the "Missing-file investigation"
rule, the "A bare local `main` ref can be silently stale" rule) — none of them is mechanical;
all of them depend on Claude remembering to apply them every time.

**Prior art already solves a narrower version of this problem for one repo.**
`claude/scripts/dev-env-sync.py` ([ADR-006](006-dev-env-sync-on-every-prompt.md),
[ADR-058](058-worktree-squatting-main-detection-correction.md),
[ADR-110](110-escalate-persistent-dev-env-sync-ff-failures.md)) already does exactly this
mechanic — fetch, compare local vs. `origin/main`, `pull --ff-only` when safe, warn otherwise —
but it is hardcoded to one repo (`~/Git/dev-env`) and one branch name (`"main"`), and runs on
`UserPromptSubmit` (fires every prompt, not literally at session start). Generalizing that
mechanic to any repo a session works in was explicitly out of that hook's scope (its job is
keeping `~/.claude/`'s symlinked tooling current, not general drift detection).

## Decision

Add a new hook, `claude/scripts/session-start-sync.py`, registered on the `SessionStart` event
(no matcher — fires on every source: startup, resume, clear, compact). No hook in this repo
previously used `SessionStart`; every existing "fires early in a session" hook
(`dev-env-sync.py`, `reconcile-open-prs.py`, `reconcile-pending-tiles.py`,
`journal-onboard-check.py`) instead rides `UserPromptSubmit` with a once-per-session sentinel
file. `SessionStart` was chosen deliberately over extending that established pattern — see
Judgment calls.

On every firing:

1. Resolve the repo the session actually started in from the hook's own `cwd` payload field
   (`git rev-parse --show-toplevel`) — not a hardcoded path. Not a git repo -> exit 0.
2. Skip dev-env's own canonical (`~/Git/dev-env`) unconditionally — it already has the more
   thorough `dev-env-sync.py` mechanism (topology auto-correction + persistent-failure
   escalation); running both against the same repo on every dev-env session would double the
   network/git cost for no added coverage. The one explicit repo exclusion.
3. Honor a per-project opt-out: `"session_start_sync_disabled": true` in that repo's own
   `.claude/hook-config.json`.
4. `git fetch origin --quiet` — every remote-tracking branch in one round-trip, not just the
   default branch (fixes a "wrong-branch pull scattered N files"-class incident too, not only
   default-branch staleness). Fetch failure (network/auth) -> exit 0 silently.
5. Classify the checkout: is it the canonical/sole checkout or a linked worktree
   (`git worktree list --porcelain`, reusing `_worktree_topology.find_worktree_by_path` against
   `canonical_worktree`'s result by identity)? What is the repo's actual default branch
   (`git symbolic-ref --short refs/remotes/origin/HEAD`, falling back to `"main"` on failure)?
   What branch is currently checked out (`_worktree_topology.resolve_current_branch`, routing a
   detached HEAD to the shared `DETACHED` sentinel)?
6. Compare HEAD against the right reference: the branch's own upstream if it has one, else a
   best-effort `origin/<default-branch>` fallback — the fallback is what generalizes
   `dev-env-sync.py`'s single-branch check to "detached HEAD N commits behind" (a detached HEAD
   or a purely local branch has no upstream by definition).
7. If HEAD is already even with, or strictly ahead of, the comparison ref -> exit 0 silently
   (don't spam the healthy path, matching `dev-env-sync.py`'s own convention).
8. Otherwise, decide eligibility for an automatic `git pull --ff-only`: **only** when the
   checkout is canonical/sole, currently on exactly its own default branch, has zero local-only
   commits (a true fast-forward), a clean working tree, and no other session's transcript
   active in this exact checkout in the last 5 minutes
   (`_worktree_liveness.worktree_session_is_live`, extended with a new `exclude_session_id`
   parameter — see below). Any single failing condition routes to advisory-only.
9. Eligible -> run the pull; emit a one-line success advisory naming the SHAs and commit count,
   or (on a race-condition failure) fall through to a warning rather than swallowing it.
   Ineligible -> emit a loud advisory naming the repo, branch, how far behind, and precisely
   why it was not auto-fixed.

All advisories are delivered via `_hookout.emit_advisory("SessionStart", text, audience="both")`
— `SessionStart` is one of the three events whose exit-0 stdout reaches the model
(`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`, alongside `UserPromptSubmit`/`UserPromptExpansion`),
and `audience="both"` additionally surfaces a systemMessage toast to the user, matching the
issue's own "loud advisory" framing.

**Fail-open, unconditionally.** Every subprocess failure (not a git repo, a failed fetch, a
failed rev-parse, an unreadable worktree list) exits 0 silently or degrades gracefully into the
next check; every ineligible-to-autofix case falls through to an advisory, never a block. This
hook can never block a prompt or a session start, never exits non-zero, and its own absence or
failure never regresses a session below today's (manual-discipline-only) baseline. It is a
drift *detector*, not a gate — the module docstring and the `__main__` guard's
`except Exception: sys.exit(0)` both state this explicitly, and the safe-exit-guard structural
test (`## Testing` item 62) enforces the fail-open direction mechanically.

## Judgment calls

### `SessionStart` vs. `UserPromptSubmit` + once-per-session sentinel

Confirmed directly with the user before implementation (this repo's own established pattern —
four close analogs — all use `UserPromptSubmit` + sentinel, so departing from it needed an
explicit decision, not a silent one). `SessionStart` won: it is a semantically exact match
("session-start fetch" is literally the issue's own title), needs no sentinel-file bookkeeping
since the event itself only fires once, and covers a resumed/compacted session for free (a
`UserPromptSubmit`+sentinel hook cannot distinguish "the first prompt of a freshly resumed
session after a long gap" from "the first prompt of any session" without additional state).
`_hookout.py`'s `STDOUT_MODEL_VISIBLE_EVENTS` frozenset already listed `SessionStart` as a
model-visible-stdout event before this change — pre-wired for, simply never adopted. The
accepted risk: `SessionStart` is unexercised elsewhere in this repo, unlike the proven
`UserPromptSubmit` pattern; it is nonetheless a standard, documented Claude Code hook event
(the same hooks-reference family ADR-098 already quotes from), and the four structural gates
(output-contract, safe-exit, wiring, heartbeat — `## Testing` items 61/62/63/68) all passed
against it with zero special-casing required, which is reasonable first-order evidence the
event is wired correctly, short of a live end-to-end session observation.

### Why `git fetch origin` (all branches), not `git fetch origin <default-branch>`

A single network round-trip either way; fetching every remote-tracking branch also freshens
`origin/<current-branch>` for the "compare against my own upstream" path (step 6 above), which
directly targets the "wrong-branch pull scattered 26 files" class of incident
(lifting-logbook), not just default-branch staleness.

### Why the default-branch fallback is a hardcoded `"main"`, not a `gh repo view` call

Every repo referenced anywhere in this corpus uses `"main"`. Querying
`gh repo view --json defaultBranchRef` to cover a currently-nonexistent case would add a
network- and auth-dependent call to a path this hook otherwise keeps cheap and offline-capable
(a `symbolic-ref` read against the local `origin/HEAD` ref, no network).

### Auto-fix scope: canonical-or-sole checkout, exactly on its own default branch, and nothing broader

A linked worktree is **never** auto-mutated, only fetch-and-warn — it is legitimately expected
to sit on a feature branch, and this hook has no invariant to auto-correct that against. A
detached HEAD is never auto-checked-out onto the default branch either, even though doing so
would technically be a "safer" git operation than a bare `pull` — a detached HEAD might be a
deliberate investigation (someone intentionally checked out an old commit), and this hook
cannot distinguish that from an accident, so it always falls through to advisory. This
deliberately does **not** replicate `dev-env-sync.py`'s off-main topology auto-correction
(auto-returning a clean canonical to `main`, warning about a squatter) — that behavior encodes
dev-env's own narrower "the canonical must always be on `main`" invariant
([ADR-058](058-worktree-squatting-main-detection-correction.md)), which is not a general rule
for every repo's canonical (most canonicals are legitimately used for feature-branch work
between sessions).

### Why dev-env's own canonical is excluded, and why engineering-journal needs no exclusion

dev-env's canonical already has a strictly more thorough, dev-env-specific mechanism
(`dev-env-sync.py`: topology auto-correction + persistent-failure escalation, ADR-058/ADR-110)
— running this generic hook against it too would double the network/git cost on every dev-env
session for zero added coverage, so it is excluded by an explicit path comparison.
engineering-journal's canonical needs **no** special-case: its canonical is essentially never
checked out on its own default branch during normal operation (it legitimately lives on
`draft/YYYY-MM-DD` for most of every day per the Stub file workflow), so `can_autofix` is
naturally `False` there by construction — the rare warn-path firing when it briefly is on
`main` is harmless, orthogonal signal, not a conflict with `journal-canonical-guard.py`'s
different concern (branch-hijack correction, i.e. detached/`claude/*`-branch recovery, not ref
staleness).

### The concurrent-session liveness check: a new `exclude_session_id` parameter, a short window, and a cost-gated call site

`_worktree_liveness.worktree_session_is_live()` ([ADR-051](051-worktree-liveness-guard.md)) was
built to answer "is a Claude session active in this worktree" for the prune/reclaim routines,
which run **out-of-process** — so neither existing caller ever needed to ask "...other than
me." This hook runs **as** the session it would otherwise always match, so calling the function
unmodified would always read the checkout as live and permanently disable auto-fix. Extended
`newest_jsonl_mtime()`/`worktree_session_is_live()` with an optional `exclude_session_id`
parameter (default `None`, fully backward compatible — both existing callers are unaffected)
that skips the transcript file whose filename stem matches the given session id. Uses a
5-minute window, deliberately much shorter than prune's 24h / reclaim's 6h — those ask "was
this worktree used recently enough to be worth keeping" over a long horizon; this asks the
narrower "is another session actively working right now," where a multi-hour-old transcript
should not block a safe, `--ff-only` sync. The scan itself is skipped entirely unless every
*other* eligibility condition already holds (canonical, on-default-branch, ff-safe, clean) — a
path that is going to warn regardless never pays the transcript-directory-scan cost.

### Residual safety margin beyond the liveness check

Even without the liveness check, `--ff-only` combined with the clean-tree requirement already
rules out the actually destructive failure mode this hook must avoid: a `pull --ff-only` cannot
rewrite history (no `merge`/`rebase`) and a clean tree means there is nothing uncommitted to
clobber, so a race against a concurrent session can, at worst, either succeed harmlessly (the
concurrent session wanted the same fresh `main` anyway) or fail cleanly (the concurrent session
made its own commits, which breaks the fast-forward and this hook falls through to a warning).
The liveness check is a defense-in-depth addition on top of that already-strong baseline,
closing the narrower residual risk of a concurrent session's file reads shifting
mid-investigation — not the only thing standing between this hook and data loss.

### Deferred, explicitly out of scope for this PR

- **Mid-session repo-hops.** Only the session's own primary repo, resolved once at firing time,
  is checked. A session that later `cd`s or `-C`s into an entirely different repo is not
  re-checked by this hook (though the pre-existing `pre-bash-drift-check.py`/ADR-101 family
  still covers cwd/branch drift on every subsequent Bash call for the *original* repo).
- **Multi-repo-in-play coverage in a single firing.** `SessionStart` only knows about one `cwd`.

Both are natural extension points for a future ADR amendment if real-world use shows they
matter, not silently dropped considerations.

## Consequences

- **Testing.** New `claude/scripts/tests/test_session_start_sync.py` (44 cases) covers every
  pure decision/formatting helper (`resolve_default_branch`, `is_canonical_checkout`,
  `resolve_compare_ref`, `can_autofix`'s full truth table, `classify_block_reason`'s fixed
  precedence including a multi-failure tie-break, all three message formatters, and
  `load_disable_flag`) plus the two small file-I/O helpers against `tempfile.TemporaryDirectory()`.
  `claude/scripts/tests/test_worktree_liveness.py` gains 4 cases for the new
  `exclude_session_id` parameter (exclusion works, exclusion doesn't hide a genuinely different
  concurrent session, and omitted/explicit-`None` preserve prior behavior exactly — regression
  safety for the two existing callers). `main()`'s own subprocess orchestration is not
  unit-tested, matching the established convention for this class of hook
  (`dev-env-sync.py`/`pre-bash-drift-check.py`'s own test files, `## Testing` items 22/56/59).
  New `## Testing` item 89.
- **Observability.** `SessionStart` exit-0 stdout is model-visible
  (`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`); every emission goes through
  `_hookout.emit_advisory`, never a hand-rolled `print`/`sys.stderr.write` — verified by the
  repo-wide output-contract gate (`## Testing` item 61), which passed against this hook with
  zero allowlist additions needed.
- **Security.** N/A — no secrets, no user-controlled input beyond a harness-supplied
  `cwd`/`session_id` already trusted elsewhere in this hook family.
- **Resilience / failure modes.** Fail-open unconditionally, detailed in the Decision section
  above. Verified by the repo-wide safe-exit-guard gate (`## Testing` item 62).
- **Performance.** Bounded to roughly ten subprocess calls plus one network fetch, once per
  session-start event (not repeated per-prompt) — cheaper in aggregate over a long session than
  the `UserPromptSubmit`+sentinel alternative would have been, since that pattern still pays a
  sentinel-file existence check on every single prompt even after the one real check has run.
- **Data integrity.** N/A — the only schema change is one new optional boolean key,
  `session_start_sync_disabled`, in a project's own `.claude/hook-config.json`.
- **ADR warranted** because this introduces a new hook script wired into `claude/settings.json`
  on a hook event this repo has never previously used, and generalizes an existing single-repo
  mechanism (`dev-env-sync.py`) into a new, repo-agnostic shared pattern — the same warranting
  shape as ADR-071/ADR-085/ADR-101.

---

## References

- `claude/scripts/session-start-sync.py` — implementation
- `claude/scripts/_worktree_liveness.py` — extended with `exclude_session_id`
- `claude/scripts/tests/test_session_start_sync.py`,
  `claude/scripts/tests/test_worktree_liveness.py` — test coverage
- `claude/settings.json` — hook wiring (new `SessionStart` event array)
- `claude/CLAUDE.md` — cross-referenced from the "A bare local `main` ref can be silently
  stale" bullet, the "Missing-file investigation" bullet, and the CLI Scripting Checklist's
  "Ref scoping" item
- [dev-env#966](https://github.com/brownm09/dev-env/issues/966) — motivating issue
- [dev-env#910](https://github.com/brownm09/dev-env/issues/910) — the 07-25 retro issue that
  first surfaced this as item 1
- `claude/scripts/dev-env-sync.py` — the single-repo mechanism this hook generalizes
  ([ADR-006](006-dev-env-sync-on-every-prompt.md),
  [ADR-058](058-worktree-squatting-main-detection-correction.md),
  [ADR-110](110-escalate-persistent-dev-env-sync-ff-failures.md))
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the canonical-mutate guard this
  hook's operations (`fetch`, `pull --ff-only`) are already exempt from by design
- [ADR-051](051-worktree-liveness-guard.md) — `_worktree_liveness.py`'s original design, now
  extended with `exclude_session_id`
- [ADR-085](085-bash-repo-branch-drift-detection.md),
  [ADR-101](101-bash-drift-check-every-call.md) — the sibling cwd/branch-drift-detection family
  this hook complements rather than replaces (those catch mid-session Bash-call drift on the
  session's original repo; this hook catches the checkout's staleness relative to origin at
  session start)
- [Claude Code Hooks documentation](https://code.claude.com/docs/en/hooks) — `SessionStart`
  event and payload schema, and the exit-code/output-channel contract
