# ADR-093: Engineering-Journal Canonical Hijack Guard

**Date:** 2026-07-09
**Status:** Accepted
**Tags:** journal, worktrees, canonical, hijack, hooks, UserPromptSubmit, correction, safety, dev-env-sync, ADR-058

---

## Context

On at least two consecutive mornings (2026-07-07, 2026-07-08), the moment the
`daily-journal-compose-local` scheduled task fired, the **canonical** engineering-journal checkout
(`C:/Users/brown/Git/engineering-journal`, no worktree, no `-C` redirect) had its own HEAD checked
out to a brand-new `claude/<adjective>-<name>-<hex>` branch — hijacking it from whatever branch a
concurrent stub-writing session had left it on — and was later abandoned detached, for hours.
Confirmed via reflog forensics ([dev-env#630](https://github.com/brownm09/dev-env/issues/630)):
both hijack events fall strictly *outside* the scheduled task's own session transcript (before it
begins, and briefly again after it ends during teardown) — the session itself is properly isolated
in its own worktree throughout. The defect is in whatever mechanism provisions/tears down a
scheduled task's isolated worktree, not in `claude/routines/daily-journal-compose/SKILL.md`, not in
`/journal-compose`, and not in anything the agent itself does. It is very likely not fixable from
within dev-env at all (a Claude Code SDK / scheduled-task-mechanism issue).

Both incidents left the canonical broken — on a throwaway branch, or detached — for roughly 3-4
hours, undetected by any automation, found only because a human happened to check the reflog. Any
concurrent session touching the canonical during that window inherits its wrong branch state.

dev-env#630's own "Proposed fix" names the actionable interim mitigation this ADR implements: *"a
defensive check at the start of any canonical-touching routine (or a standalone periodic/hook
check): if the canonical's current branch is unexpectedly `claude/*` or detached, restore it to
`main` before proceeding... consistent with how `dev-env-sync.py` already defends dev-env's own
canonical for the analogous case"* ([ADR-058](058-worktree-squatting-main-detection-correction.md)).

### Why not wire the correction into the routine or the compose skill

`claude/routines/daily-journal-compose/SKILL.md`'s Step 0 and `claude/skills/journal-compose/SKILL.md`
both **deliberately** avoid any mutating git operation against the engineering-journal canonical.
`daily-journal-compose`'s Step 0 docstring states this explicitly: it isolates itself into its own
detached worktree ([ADR-082](082-journal-compose-worktree-isolation.md)) specifically to avoid "this
routine mutating the canonical checkout itself... the same class of hazard ADR-082 closes."
`journal-compose` uses a `--detach` worktree for the identical reason — never contending for the
canonical's branch ref. Adding a mutating correction to either would reintroduce exactly the hazard
class both were built to eliminate. `sync-routine-worktree` (the shared routine-sync skill) is also
not a fit: its own "Scope boundary" section explicitly excludes draft-branch workflows, and it has an
open bug ([dev-env#618](https://github.com/brownm09/dev-env/issues/618)) where a detached HEAD is
misclassified as a feature branch and rebased — the opposite of the correction this hijack needs.

So the correction needs to live **outside** all three, as its own mechanism.

---

## Decision

**A new `UserPromptSubmit` hook, `claude/scripts/journal-canonical-guard.py`**, modeled on
`dev-env-sync.py`'s existing self-healing design (ADR-058) but with a narrower, repo-appropriate
gate, registered unconditionally alongside it in `claude/settings.json` (no matcher mechanism exists
for `UserPromptSubmit`; every entry in that array fires on every prompt, in every session,
regardless of project — the same convention `dev-env-sync.py`'s own `DEV_ENV_REPO` constant and
`new-day-journal-check.py`'s `JOURNAL_REPO` constant already establish for "check one specific repo
every prompt").

### Why the gate can't reuse `dev-env-sync.py`'s "off main" condition

dev-env's canonical must **always** be `main` — that's the invariant ADR-058 defends. Engineering-
journal's canonical has no such invariant: it is intentionally on `draft/YYYY-MM-DD` for most of
every working day (`claude/CLAUDE.md`'s documented Stub file workflow — dozens of concurrent
sessions run `checkout main && pull` then `checkout -b draft/YYYY-MM-DD` directly against this exact
canonical, all day, every day). Gating on "off main" would fight that normal workflow and yank a
legitimately in-progress session's canonical back to `main` mid-work.

Instead, a new pure predicate in `claude/scripts/_worktree_topology.py`, `is_hijacked_branch(branch)`,
targets only the two states never legitimate for *any* canonical:

```python
def is_hijacked_branch(branch: str) -> bool:
    return bool(branch) and (branch == "<detached>" or branch.startswith("claude/"))
```

Validated against live evidence, not just reasoning: at design time, `git branch -a` on the real
engineering-journal canonical showed the two exact branches from the #630 incidents
(`claude/dreamy-yalow-9a684c`, `claude/priceless-kalam-4255c1`) still sitting there, unreferenced by
any worktree (no `+` marker) — while every *legitimate* `claude/*` branch has a `+` marker and a
matching `.claude/worktrees/<slug>` entry (git allows a branch checked out in at most one worktree at
a time, so a live worktree's own `claude/*` branch can never simultaneously appear on the canonical).
A full reflog scan back to 2026-06-14 showed every other checkout on this canonical is `main`,
`draft/YYYY-MM-DD`, or (pre-ADR-082) `compose/YYYY-MM-DD` — never any other shape.

### Reusing ADR-058's shared topology module

`journal-canonical-guard.py` reuses `_worktree_topology.py`'s `diagnose_main_topology` and
`canonical_sync_action` — the same functions `dev-env-sync.py` already uses — for the "is it safe to
auto-correct" sub-decision, once `is_hijacked_branch` has independently decided correction is
warranted:

```python
branch = run(["git", "symbolic-ref", "--short", "HEAD"])
current_branch = resolve_current_branch(branch.returncode, branch.stdout)
if not is_hijacked_branch(current_branch):
    sys.exit(0)  # on main, draft/YYYY-MM-DD, or anything else legitimate -> leave alone

worktrees = parse_worktree_porcelain(run(["git", "worktree", "list", "--porcelain"]).stdout)
topo = diagnose_main_topology(worktrees)
if not is_hijacked_branch(topo.canonical_branch):
    sys.exit(0)  # re-check against the FRESH read (see TOCTOU below)

action = canonical_sync_action(topo, canonical_clean)
# action.kind: "return-canonical" (safe -> git checkout main) / "warn-squatter" / "warn-dirty"
```

`canonical_sync_action`'s own framing of "healthy" (on `main`) doesn't match engineering-journal's
full legitimate-branch set — but it's only ever consulted *after* `is_hijacked_branch` has already
gated on the narrower, repo-agnostic invariant, so its narrower "is `main` free and is the canonical
clean" question is exactly the sub-decision needed at that point, not the overall health signal.

`resolve_current_branch` (also new, shared with the [ADR-058 Amendment (2026-07-09)](058-worktree-squatting-main-detection-correction.md#amendment-2026-07-09--dev-env-syncpy-never-detected-a-detached-canonical-head-dev-env619)
fixing dev-env#619 in the same PR) maps a `symbolic-ref` failure to the same `"<detached>"` sentinel
`parse_worktree_porcelain` already produces for a detached worktree, so both `dev-env-sync.py` and
this new hook feed detached HEAD into the same already-tested topology pipeline instead of exiting
early on it.

### TOCTOU: re-check immediately before every mutating point, not just once

The gate reads the branch **three times**, not two, after a first design-review pass found the
two-read version still left a materially-reachable window. Once cheaply via `symbolic-ref`
(keeping the common healthy path cheap, matching ADR-058's cost model); a second time via the
more expensive `worktree list --porcelain` needed to build `topo` for `canonical_sync_action`;
and a **third, final, cheap `symbolic-ref` re-check immediately before the mutating `git checkout
main` call itself** — after the `git status --porcelain` read that decides clean-vs-dirty, which
is itself a subprocess call with its own (small) duration.

Between any of these reads, a concurrent stub-writing session — and this canonical sees many,
routinely, within seconds of each other — could complete its own `checkout main && checkout -b
draft/YYYY-MM-DD` sequence. Acting on a stale read would still issue `git checkout main`, yanking
that session's just-established legitimate branch back to `main` — the same class of harm this
whole guard exists to prevent, just introduced by the guard itself.

A second round of review (post-implementation, on the opened PR) correctly pointed out that the
*first* fix here — a single re-check right after the `worktree list` read — left the `git status`
call itself, plus everything after it, still inside the exposed window, and that this residual is
**not** symmetric with `dev-env-sync.py`'s own equivalent gap: dev-env's canonical is never
*legitimately* moved off `main` at all, so that hook's identical-shaped residual window is rarely
if ever actually raced against in practice, whereas engineering-journal's canonical is moved
between `main` and `draft/YYYY-MM-DD` constantly by design. The third re-check — placed after the
`status` read and immediately before the `checkout` call — removes the `status` call from the
exposed window entirely, narrowing the residual to the same single-subprocess-call order of
magnitude `dev-env-sync.py`'s own gap actually is, making the ADR's parity claim accurate rather
than aspirational. The window cannot be closed to zero without a lock this repo has no
infrastructure for; three checks, one right before the mutation, is the accepted stopping point.

### Non-destructive correction

`git checkout main` never deletes the hijacked branch or its commits — the branch ref remains, by
name, exactly where it was. No "park" step (ADR-058's `park_branch_for`) is needed here, unlike the
squatter-worktree case ADR-058 addresses: the hijacked branch already has a name distinct from
`main`, so there's nothing to free by renaming it. The guard's success message says so explicitly
(`"... 'claude/priceless-kalam-4255c1' still exists if needed: git -C <repo> checkout
claude/priceless-kalam-4255c1"`) — except when the prior state was `"<detached>"`, which is a
sentinel, not a real ref, so no (bogus) recovery command is offered for it.

### Scope: engineering-journal only, stated explicitly

This is scoped to engineering-journal — the one repo with confirmed, reproduced incidents. Other
repos with daily/weekly scheduled routines against their own canonicals (career-playbook via
`nightly-cover-letters`, firing at the *identical* 07:00 local time; research-notes via
`nightly-research`) carry the same theoretical exposure to whatever the underlying provisioning
defect is, but are unconfirmed and uninstrumented — nobody has checked their reflogs, unlike
engineering-journal, which was only investigated because a separate same-morning bug
([dev-env#615](https://github.com/brownm09/dev-env/issues/615)) drew attention to that morning's run.
Stated here explicitly, rather than left implicit in a hardcoded `JOURNAL_REPO` constant, so a future
reader doesn't mistake this fix for closing the whole class of bug. Precedent for tolerating
single-consumer duplication until a second confirmed case appears:
[ADR-072](072-shared-repo-scan-module.md)'s own context (`find_git_repos` existed in three copies
before extraction).

---

## Consequences

- The dev-env#630-shaped incident (canonical hijacked for hours, zero automated detection) is now
  caught and, in the common clean case, self-corrected on the very next prompt of *any* session on
  the machine — bounding the exposure window from "hours until a human happens to look" to "however
  long until the next Claude Code prompt anywhere," typically minutes. This does not fix the root
  cause (still very likely outside dev-env's own reach) — it bounds the damage window, exactly as
  dev-env#630's own "Proposed fix" scoped this mitigation.
- A hijacked-but-dirty canonical is left alone with a warning rather than auto-corrected — the same
  drift-preservation precedent ADR-058 already established for dev-env's own canonical.
- A hijacked canonical blocked by a squatting worktree holding `main` is warned, not silently ignored
  — reusing ADR-058's existing `warn-squatter` decision and message shape unchanged.
- **Testing.** No new dedicated test file for `journal-canonical-guard.py`'s orchestration itself —
  it has zero local pure logic (everything delegates to `_worktree_topology.py`), the same shape as
  `dev-env-sync.py`, which likewise has none. `resolve_current_branch` and `is_hijacked_branch` are
  covered by new offline pure-helper tests in `claude/scripts/tests/test_worktree_topology.py`
  (dev-env `## Testing` item 22, extended — not a new numbered item, matching the established
  convention that a consumer script with no local pure logic doesn't get its own item). The
  orchestration itself (subprocess calls, the TOCTOU re-check, the actual git mutation) was verified
  manually against a throwaway git repo fixture via the `JOURNAL_CANONICAL_GUARD_REPO_PATH` env-var
  override — four scenarios confirmed live: `claude/*` hijack + clean → restored; detached + clean →
  restored (with the sentinel-aware message); a legitimate `draft/YYYY-MM-DD` branch → left untouched
  (the specific false-positive this predicate exists to avoid); `claude/*` hijack + dirty → warned,
  not switched. Re-verified against the same fixture pattern after the review-driven TOCTOU
  (third re-check) and exception-handling fixes landed, confirming no regression. This matches this
  repo's established convention for topology-diagnosing orchestration
  scripts (`## Testing` items 22, 26, 30, and others: "exercised end-to-end by `--dry-run` / a
  throwaway-repo run in the PR, not here").
- **Observability.** Success and warning messages mirror `dev-env-sync.py`'s existing stdout/stderr
  split exactly (stdout for a successful auto-correction Claude should know about; stderr for a
  warning that needs a human to run a manual command) and are plain ASCII, consistent with ADR-086's
  cp1252-safety convention (not matching `dev-env-sync.py`'s own pre-existing emoji usage, which
  predates that convention and isn't this ADR's to fix).
- **Security.** N/A — no auth/secrets/PII surface; only local git state on the developer's own
  machine.
- **Resilience / failure modes.** Fails open throughout: a missing repo directory, a failed
  `worktree list` read, or a failed `checkout` all degrade to a warning or a silent no-op, never a
  crash or a blocked prompt on the *expected*-failure paths. Review additionally found the script's
  own `sys.exit(0)` calls did not protect against an *unexpected* one — an uncaught subprocess
  exception (`TimeoutExpired`, `FileNotFoundError` if `git` were ever off `PATH`, or another `OSError`)
  would have propagated out of `main()` as a traceback with a non-zero exit, contradicting the
  docstring's own "Exit 0 always" promise and deviating from the established fail-open convention the
  closest sibling hook (`new-day-journal-check.py`, which guards this same repo on this same event)
  already follows by wrapping every `subprocess.run` call. Fixed: the `if __name__ == "__main__":`
  entry point now wraps `main()` in a `try/except Exception: sys.exit(0)`, matching that convention.
  The three-read TOCTOU re-check (above) is itself a resilience fix against the dominant failure mode
  this specific canonical actually sees — concurrent legitimate sessions, not adversarial or malformed
  state.
- **Performance.** Two extra `git symbolic-ref` calls added to every prompt in every session that
  reaches the hijacked branch (cheap; only one on the common healthy path); the more expensive
  `worktree list` / `status` / `checkout` calls run only on the rare hijacked path, mirroring ADR-058's
  "healthy path stays cheap" design. Registering a 12th unconditional `UserPromptSubmit` hook adds one
  more per-prompt process spawn machine-wide, including in sessions that never touch the journal —
  reviewed and accepted as the same always-on cost `dev-env-sync.py` already pays; no cheaper
  correct shape exists without merging the two canonical-guard hooks, which isn't warranted (different
  repos, different invariants).
- **Data integrity.** N/A schema-wise; the correction is non-destructive by construction (see above).
- **Maintainability.** The `"<detached>"` sentinel is now a single `DETACHED` constant in
  `_worktree_topology.py`, referenced by every producer (`parse_worktree_porcelain`) and consumer
  (`main_squatter`, `resolve_current_branch`, `is_hijacked_branch`, and this hook's own recovery-message
  check) — previously a bare string literal repeated at each site, which review flagged as a silent-drift
  risk if the spelling ever changed at one site and not another. The correction *flow* itself (the
  git-call sequence and the three-way `warn-squatter`/`warn-dirty`/`return-canonical` dispatch) remains
  duplicated between `journal-canonical-guard.py` and `dev-env-sync.py` — only the *decision* logic is
  shared via `_worktree_topology.py`. This is the same single-consumer-duplication trade-off the Scope
  section above already accepts for not generalizing to other repos, just visible one level lower (two
  scripts, not yet a third): acceptable for two consumers; extract a shared
  `correct_canonical(repo, gate_predicate, message_set)` orchestrator when a third
  canonical-guarding hook appears, rather than copying the flow a third time.
- `_worktree_topology.py`'s module docstring now documents **two distinct invariants** it hosts —
  dev-env's "canonical always `main`" (the module's original framing) and this ADR's repo-agnostic
  "canonical never detached or on a stray `claude/*` branch" — so a future reader isn't misled into
  thinking the module is dev-env-only.

---

## Alternatives rejected

- **Wire the correction into `daily-journal-compose/SKILL.md` Step 0 or `journal-compose/SKILL.md`.**
  Rejected — both deliberately avoid mutating the canonical for exactly this hazard class
  (ADR-082); adding a mutating correction there would reintroduce it. Also wouldn't catch the
  post-session teardown hijack (confirmed to occur *after* the session's own transcript ends) or
  protect any *other* concurrent session touching the canonical during the exposure window — a
  global hook does both automatically.
- **Reuse `sync-routine-worktree`.** Rejected — explicitly out of scope per that skill's own "Scope
  boundary" section (excludes draft-branch workflows), and it has an open, confirmed bug
  (dev-env#618) that misclassifies and rebases a detached HEAD as a feature branch — the opposite of
  the correction needed here.
- **Fold the check into `new-day-journal-check.py`** (the existing `JOURNAL_REPO`-scoped
  `UserPromptSubmit` hook). Rejected on two independent grounds: (1) its per-session flag suppression
  skips *all* three of its existing checks for the rest of a session after the first fired message —
  wrong for a continuously self-healing corrective check that should re-evaluate every prompt; (2) it
  is purely advisory today (zero git mutation) — bolting a mutating action onto it changes its risk
  and testing profile. Precedent for keeping a differently-shaped mechanism in its own file even
  within the same domain: [ADR-071](071-canonical-checkout-mutate-guard-hook.md),
  [ADR-085](085-bash-repo-branch-drift-detection.md).
- **Generalize immediately to every repo with a scheduled routine** (career-playbook,
  research-notes). Considered, given the identical 07:00 fire time as `nightly-cover-letters`
  suggests the same exposure. Rejected for this PR — no confirmed incident in either repo yet, and
  generalizing a fix for a still-unconfirmed theoretical exposure risks over-building. Scoped
  explicitly above instead of silently; a second confirmed incident would warrant either extending
  this hook to take a repo list or extracting the gate/correction into a shared parameterized
  function.
- **Gate on "off `main`"**, mirroring `dev-env-sync.py` exactly. Rejected — engineering-journal's
  canonical is intentionally off `main` (on `draft/YYYY-MM-DD`) for most of every working day; that
  gate would fight the documented Stub file workflow and yank legitimate in-progress sessions back to
  `main`.

---

## References

- `claude/scripts/journal-canonical-guard.py` — the new hook
- `claude/scripts/_worktree_topology.py` — `is_hijacked_branch`, `resolve_current_branch` (shared
  with the ADR-058 amendment fixing dev-env#619 in the same PR), `diagnose_main_topology`,
  `canonical_sync_action` (reused unchanged)
- `claude/scripts/tests/test_worktree_topology.py` — offline coverage for the new predicates
- `claude/settings.json` — `UserPromptSubmit` registration, alongside `dev-env-sync.py`
- [dev-env#630](https://github.com/brownm09/dev-env/issues/630) — motivating issue, full reflog/
  transcript evidence, and the "Proposed fix" this ADR implements
- [dev-env#619](https://github.com/brownm09/dev-env/issues/619) — the closely related detached-HEAD
  gap in `dev-env-sync.py`, fixed as an amendment to ADR-058 in the same PR
- [dev-env#618](https://github.com/brownm09/dev-env/issues/618) — `sync-routine-worktree`'s own
  detached-HEAD-misclassification and missing-abort-path bugs, why it isn't reused here
- [dev-env#657](https://github.com/brownm09/dev-env/issues/657) — adjacent, out-of-scope finding from
  the same design review: two routines still run `sync-routine-worktree`'s mutating sequence against
  this same canonical, the exact hazard ADR-082 removed from their sibling
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — the dev-env-canonical precedent
  this ADR extends to a second repo under a different invariant; shared module source
- [ADR-086](086-journal-compose-liveness-guard.md) — closest structural precedent: a standalone,
  engineering-journal-specific corrective hook that cross-references an older, thematically-related
  ADR without amending it
- [ADR-082](082-journal-compose-worktree-isolation.md) — why the compose skill/routine can't host the
  mutating correction themselves
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the engineering-journal carve-out from the
  canonical-mutate guard (permanent by design, not pending dev-env#346 — corrected by dev-env#747 /
  ADR-105) that makes this hook's `git -C <repo> checkout main` call reachable at all
