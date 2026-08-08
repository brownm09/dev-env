# ADR-130: Session-Start Fetch + Fast-Forward-Only-or-Warn Drift Detection

**Date:** 2026-08-08 (amended same-day, pre-merge, after a two-pass adversarial `/review`)
**Status:** Accepted
**Tags:** hooks, session-start, git, drift-detection, silent-failure, fast-forward, worktree, canonical-checkout, shared-module, dev-env-sync-fast-follow, adr-071, adr-051, adr-085, correction, review-findings

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
with `"matcher": "startup|resume"` (**not** unmatched — see Amendment 1's matcher-scope finding
for why `clear`/`compact` are deliberately excluded). No hook in this repo previously used
`SessionStart`; every existing "fires early in a session" hook (`dev-env-sync.py`,
`reconcile-open-prs.py`, `reconcile-pending-tiles.py`, `journal-onboard-check.py`) instead
rides `UserPromptSubmit` with a once-per-session sentinel file. `SessionStart` was chosen
deliberately over extending that established pattern — see Judgment calls.

On every firing:

1. Resolve the repo the session actually started in from the hook's own `cwd` payload field
   (`git rev-parse --show-toplevel`) — not a hardcoded path. Not a git repo -> exit 0. Also
   resolve the checkout's **canonical** root (`_worktree_canon.canonical_repo_root`) — used
   only for the next two steps, never for the actual git operations below, which always run
   against the real checkout.
2. Skip every dev-env checkout — canonical **or worktree** — unconditionally (compared via the
   canonical root from step 1, so a dev-env worktree session is excluded too, not just a
   canonical-cwd session): dev-env already has the more thorough `dev-env-sync.py` mechanism
   (topology auto-correction + persistent-failure escalation), which fires unconditionally
   every prompt regardless of which dev-env checkout the current session happens to be in.
   Running both against the same repo would double the network/git cost for no added coverage.
   The one explicit repo exclusion.
3. Honor a per-project opt-out: `"session_start_sync_disabled": true` in the checkout's
   **canonical** root's `.claude/hook-config.json` (not a worktree's own copy — `.claude/` is
   commonly gitignored, so a worktree checkout usually has no copy of this file to read at all).
4. `git fetch origin --quiet` — every remote-tracking branch in one round-trip, not just the
   default branch (fixes a "wrong-branch pull scattered N files"-class incident too, not only
   default-branch staleness). Fetch failure (network/auth) -> exit 0 silently.
5. Classify the checkout: is it the canonical/sole checkout or a linked worktree
   (`git worktree list --porcelain`, comparing resolved path **value** against
   `canonical_worktree`'s result — not the `find_worktree_by_path` identity shortcut that
   module's own docstring explicitly disclaims as something a caller should rely on)? What is
   the repo's actual default branch (`git symbolic-ref --short refs/remotes/origin/HEAD`,
   **stripping the `origin/` prefix `--short` retains** — verified live that this command's own
   output is `origin/main`, not `main` — falling back to `"main"` on failure)? What branch is
   currently checked out (`_worktree_topology.resolve_current_branch`, routing a detached HEAD
   to the shared `DETACHED` sentinel)?
6. Compare HEAD against the right reference: the branch's own upstream if it has one, else a
   best-effort `origin/<default-branch>` fallback — the fallback is what generalizes
   `dev-env-sync.py`'s single-branch check to "detached HEAD N commits behind" (a detached HEAD
   or a purely local branch has no upstream by definition). Validate the resolved ref against a
   conservative name pattern before using it in any command (defense in depth — a leading-dash
   ref is format-valid to git and would otherwise be parsed as an option).
7. If HEAD is already even with, or strictly ahead of, the comparison ref -> exit 0 silently
   (don't spam the healthy path, matching `dev-env-sync.py`'s own convention). If the
   commit-behind count itself cannot be measured (a `git rev-list` failure) -> warn rather than
   exit silently, since a failed measurement must never read as "confirmed up to date."
8. Otherwise, decide eligibility for an automatic `git merge --ff-only`: **only** when the
   checkout is canonical/sole, currently on exactly its own default branch, has zero local-only
   commits (a true fast-forward — an *unmeasurable* count is treated the same as a positive
   one, never as zero), a clean **tracked** working tree (untracked files never block a
   fast-forward and are not counted as dirty), and no other session's transcript — checked
   against both the repo root and the session's own `cwd`, and against nested subagent
   transcripts too — active in this exact checkout in the last 5 minutes
   (`_worktree_liveness.worktree_session_is_live`, extended with a new `exclude_session_id`
   parameter — see below; skipped when no `session_id` is available, defaulting to "no
   concurrency detected"). Any single failing condition routes to advisory-only.
9. Eligible -> merge the **same ref eligibility was measured against** (never a separately
   hardcoded `origin/<default-branch>`, which could silently diverge from what was actually
   compared); on success, re-read `HEAD` and report the **actual** result, flagging a mismatch
   against the pre-merge measurement explicitly (a concurrent-process race) rather than
   reporting the pre-merge value as confirmed; on a race-condition failure, fall through to a
   warning rather than swallowing it. Ineligible -> emit a loud advisory naming the repo,
   branch, how far behind, and precisely why it was not auto-fixed (softened wording for a
   not-yet-pushed local branch, which is expected rebase distance, not evidence of staleness).

Every one of the ~12 git subprocess calls in one firing shares a single time budget — a
`time.monotonic()` deadline computed once at the top of `main()`, under this hook's own
`claude/settings.json` timeout — rather than each independently claiming up to 15s.

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
would technically be a "safer" git operation than a bare `merge` — a detached HEAD might be a
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
session for zero added coverage, so it is excluded by a canonical-root path comparison (see
Amendment 1 — the original version compared the raw checkout path, which only matched the
canonical itself, so a dev-env *worktree* session still paid both hooks' network cost; every
dev-env checkout, canonical or worktree, is excluded now). engineering-journal's canonical
needs **no** special-case: its canonical is essentially never
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
- **Persistent-failure escalation across sessions**, unlike `dev-env-sync.py`'s ADR-110
  mechanism (added to this list in Amendment 1, a `/review` finding). This hook fires once per
  session (less often than `dev-env-sync.py`'s every-prompt firing), so a repo whose
  fast-forward is permanently blocked repeats the identical single-line advisory forever with
  no escalation — the same silent-drift shape ADR-110 was built to prevent, left unaddressed
  here deliberately rather than absorbing a second state-machine into this PR's already large
  scope.

All three are natural extension points for a future ADR amendment if real-world use shows they
matter, not silently dropped considerations.

## Amendment 1 — dev-env#966 `/review` findings (same-day, pre-merge)

The PR carrying this ADR was reviewed by two independent, parallel adversarial passes
(correctness/security; reliability/performance/maintainability — both `opus`), which
converged on several of the same defects from different angles, plus more each found alone.
The two most severe were independently verified live against real repos on this machine
*before* being accepted as findings, not just asserted:

- **`resolve_default_branch` did not strip the `origin/` prefix `--short
  refs/remotes/origin/HEAD` retains.** Verified live: `win11-init-tools` and
  `lifting-logbook` both return `origin/main`, not `main`, with returncode 0. This silently
  disabled `can_autofix` unconditionally (`"main" == "origin/main"` is always `False`) and
  turned the detached-HEAD fallback into an invalid ref (`origin/origin/main`) — the ADR's own
  lead motivating scenario produced zero output. All 44 original tests passed only because
  the fixture asserted against a value the real command never produces.
- **`tree_clean` counted untracked files as dirty.** Verified live against `career-playbook`
  (10 `git status --porcelain` lines, 9 untracked) and `lifting-logbook` (5 lines, all 5
  untracked) — the auto-fix path was permanently unreachable in exactly the repos this ADR's
  own Context section names as the motivation.

Both defects meant the feature's headline capability — auto-fixing a stale checkout — could
never fire in practice, while still passing every test written against it; the review is the
reason this is documented as amended-before-merge rather than shipped and rediscovered later.

**Full finding list and disposition** (all fixed in the same PR before merge; none deferred as
a bug — only the one already-scoped feature-addition, persistent-failure escalation, moved to
the Judgment calls "Deferred" list above):

1. `resolve_default_branch` origin/ prefix (above) — fixed: strip the prefix; test fixture
   corrected to assert the real output shape.
2. `tree_clean` counting untracked files as dirty (above) — fixed:
   `--untracked-files=no`; reworded the dirty-tree reason to "tracked files."
3. The mutating call (`git pull --ff-only origin <default_branch>`) was decoupled from
   `compare_ref`, the ref eligibility was actually measured against — fixed: merge
   `compare_ref` directly (already fetched, no second round-trip), which is also strictly more
   correct for a branch whose upstream isn't literally `origin/<default_branch>` (a fork
   remote).
4. `_count_from`'s fail-open-to-`0` was unsafe in both directions it fed (`ahead_count`
   falsely eligible; `behind_count` silently exiting on an unmeasured, possibly-stale
   checkout) — fixed by replacing it and the two separate `rev-list --count` calls with one
   combined `git rev-list --left-right --count`, parsed by a new `_parse_left_right_counts`
   that fails open to `(None, None)`, never `(0, 0)`; `can_autofix`/`classify_block_reason`
   updated to treat `None` as ineligible/unmeasurable rather than silently comparing against
   it.
5. `repo_root` was never canonicalized — fixed: route the dev-env exclusion and the
   hook-config read through `_worktree_canon.canonical_repo_root`, closing two live bugs at
   once (a worktree session's opt-out was silently unreadable; a dev-env worktree session paid
   both this hook's and `dev-env-sync.py`'s network cost).
6. `exclude_session_id` matched only the top-level transcript stem, missing nested
   `<session>/subagents/*.jsonl` — fixed in `_worktree_liveness.py`: also exclude any path
   whose parts contain the session id, not only its own filename stem.
7. The concurrency gate queried only `repo_root` (missing a session started in a subdirectory,
   where Claude Code's transcript slug is keyed on `cwd`) and ran unconditionally even when
   `session_id` was absent from a malformed payload (silently matching the hook's own
   just-written transcript) — fixed: check both `repo_root` and `cwd`; skip the liveness call
   entirely (default not-concurrent) when there is no `session_id` to exclude by.
8. `format_autofix_success` reported the pre-merge `remote_sha` as if it were the confirmed
   result — fixed: re-read `HEAD` after the merge and report the actual value, with an explicit
   mismatch note against the pre-merge measurement (matching `dev-env-sync.py`'s own PR #701
   convention).
9. No shared time budget across the firing's ~12 subprocess calls, each independently able to
   claim up to 15s against this hook's own 30s harness timeout — fixed: a single
   `time.monotonic()` deadline threaded through every call.
10. `provisionally_eligible` hand-restated four of `can_autofix`'s five conditions inline,
    inside the untested `main()` — divergence risk with no test able to catch it — fixed:
    computed by calling `can_autofix(..., concurrent_session=False)` directly.
11. `_resolve_path` dropped `_norm`'s falsy-path empty-string guard while its docstring
    claimed to match it — fixed: added the guard; added test coverage.
12. Resolved ref names were interpolated into git commands with no validation — a
    leading-dash ref is format-valid to git and would parse as an option (low severity: no
    shell, list-form `subprocess.run`, local-state-derived input) — fixed: `is_valid_ref_name`
    gate before any use.
13. The stale-checkout advisory's "files on disk may be stale... resolve manually" framing
    was wrong for the (likely most-frequent) case of an intentional, not-yet-pushed branch —
    fixed: softened wording, conditioned on `upstream_ref is None and branch != DETACHED`.
14. `SessionStart` firing on `clear`/`compact` (no matcher) synchronously blocked routine
    mid-session operations on a network fetch, and widened the false-concurrency window
    against this session's own just-written transcript — fixed: `"matcher":
    "startup|resume"`.
15. Four subprocess calls collapsible to two (`rev-parse HEAD <ref>` combined;
    `rev-list --left-right --count` — see finding 4) — fixed, incidentally, by the finding-4
    fix.
16. `is_canonical_checkout`'s docstring cited `find_worktree_by_path`'s docstring as
    endorsing an identity-comparison shortcut that source explicitly disclaims — fixed:
    switched to value comparison; corrected the citation.
17. `_worktree_liveness.py`'s module docstring was not updated for its new third,
    in-process caller or the caller-dependent fail direction — fixed.
18. `docs/REFERENCE.md` and `claude/scripts/README.md` both stated a file count for
    `claude/scripts/` in language implying the same metric, while actually counting different
    things (`.py`-only vs. all top-level file types) — fixed: removed the redundant,
    differently-scoped count from `docs/REFERENCE.md` rather than trying to force one number
    to describe two different measurements; it now points to the README's own gated count as
    the single source of truth.
19. The "`main()` is untested by convention" citation was stated three different ways across
    ADR/TESTING.md/the test file's own docstring (`22/56/59`, `56/59`, `22/26/56/59`) — item
    22 (`_worktree_topology`) has no `main()` at all and item 26 is a utility script, so
    neither is valid precedent for this specific claim — fixed: cite `56/59` consistently
    (this ADR, `docs/TESTING.md` item 89, and the test file's own docstring all now agree).
20. `format_autofix_failure` left a dangling trailing newline when `git_stderr` was empty —
    fixed: an explicit "(git produced no diagnostic output)" fallback.
21. `_plural`/`_count_from`/`run()` are duplicated (near-verbatim) from `dev-env-sync.py` —
    **not fixed in this PR**; extracting a shared module is a separable refactor from fixing
    this PR's bugs, filed as a follow-up rather than absorbed here (see References).

`claude/scripts/tests/test_session_start_sync.py` grew from 44 to 61 cases (findings 1/2/4/
10/11/12/13/16/20 each gained dedicated regression coverage, largely replacing rather than
purely adding — several original cases were rewritten against the corrected behavior, e.g.
`resolve_default_branch`'s fixture). `claude/scripts/tests/test_worktree_liveness.py` grew
from 17 to 18 (finding 6's nested-subagent-transcript case).

## Consequences

- **Testing.** `claude/scripts/tests/test_session_start_sync.py` (61 cases, post-Amendment-1)
  covers every pure decision/formatting helper (`is_valid_ref_name`, `resolve_default_branch`,
  `is_canonical_checkout`, `resolve_compare_ref`, `can_autofix`'s full truth table including an
  unmeasurable-count case, `classify_block_reason`'s fixed precedence including the
  unmeasurable tier and a multi-failure tie-break, all four message formatters, and
  `load_disable_flag`) plus the small file-I/O and path-normalization helpers.
  `claude/scripts/tests/test_worktree_liveness.py` (18 cases, post-Amendment-1) covers the new
  `exclude_session_id` parameter, including the nested-subagent-transcript case (exclusion
  works at both the top level and nested under a nested subagent directory, exclusion doesn't
  hide a genuinely different concurrent session, and omitted/explicit-`None` preserve prior
  behavior exactly — regression safety for the two existing callers). `main()`'s own
  subprocess orchestration is not unit-tested, matching the established convention for this
  class of hook (`dev-env-sync.py`/`pre-bash-drift-check.py`'s own test files, `## Testing`
  items 56/59 — see Amendment 1 finding 19 for why items 22/26 are not valid precedent for
  this specific citation, despite two of the three surfaces originally citing them). New
  `## Testing` item 89.
- **Observability.** `SessionStart` exit-0 stdout is model-visible
  (`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`); every emission goes through
  `_hookout.emit_advisory`, never a hand-rolled `print`/`sys.stderr.write` — verified by the
  repo-wide output-contract gate (`## Testing` item 61), which passed against this hook with
  zero allowlist additions needed.
- **Security.** Low-severity argument-injection surface identified in Amendment 1 (finding 12)
  and closed: a resolved ref name is validated (`is_valid_ref_name`) before being used as a
  command argument, since a leading-dash ref is format-valid to git and every call here uses
  list-form `subprocess.run` with no shell — no command-injection surface, and the values are
  git-local-state-derived, not user input, but defended anyway. Otherwise N/A — no secrets, no
  other user-controlled input beyond a harness-supplied `cwd`/`session_id` already trusted
  elsewhere in this hook family.
- **Resilience / failure modes.** Fail-open unconditionally, detailed in the Decision section
  above. Verified by the repo-wide safe-exit-guard gate (`## Testing` item 62). Amendment 1
  closed several failure-mode gaps found by review: a shared time budget (finding 9) so the
  firing's own subprocess calls cannot collectively exceed the harness timeout; treating an
  unmeasurable commit count as ineligible/unmeasurable rather than silently miscounting
  (finding 4); and re-verifying the post-merge state rather than trusting a pre-merge
  measurement (finding 8).
- **Performance.** Bounded to roughly ten subprocess calls (reduced from the original ~12 by
  Amendment 1 finding 15's call-collapsing) plus one network fetch, once per `startup`/`resume`
  event — narrowed from every `SessionStart` source by Amendment 1 finding 14, so `clear`/
  `compact` (routine mid-session operations) no longer pay a synchronous network-fetch delay.
  Cheaper in aggregate over a long session than the `UserPromptSubmit`+sentinel alternative
  would have been, since that pattern still pays a sentinel-file existence check on every
  single prompt even after the one real check has run.
- **Data integrity.** N/A — the only schema change is one new optional boolean key,
  `session_start_sync_disabled`, in a project's own `.claude/hook-config.json`.
- **ADR warranted** because this introduces a new hook script wired into `claude/settings.json`
  on a hook event this repo has never previously used, and generalizes an existing single-repo
  mechanism (`dev-env-sync.py`) into a new, repo-agnostic shared pattern — the same warranting
  shape as ADR-071/ADR-085/ADR-101.

---

## References

- `claude/scripts/session-start-sync.py` — implementation
- `claude/scripts/_worktree_liveness.py` — extended with `exclude_session_id`, and (Amendment
  1) the nested-subagent-transcript exclusion fix
- `claude/scripts/tests/test_session_start_sync.py`,
  `claude/scripts/tests/test_worktree_liveness.py` — test coverage
- `claude/settings.json` — hook wiring (new `SessionStart` event array, matcher
  `startup|resume` per Amendment 1)
- `claude/CLAUDE.md` — cross-referenced from the "A bare local `main` ref can be silently
  stale" bullet, the "Missing-file investigation" bullet, and the CLI Scripting Checklist's
  "Ref scoping" item
- [dev-env PR #968](https://github.com/brownm09/dev-env/pull/968) — the introducing PR;
  its posted `/review` comment is Amendment 1's source finding list
- [dev-env#969](https://github.com/brownm09/dev-env/issues/969) — follow-up issue for
  Amendment 1 finding 21 (shared `_git_sync.py` module extraction, deliberately not absorbed
  into this PR)
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
