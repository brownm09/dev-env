# ADR-058: Detect & Auto-Correct a Worktree Squatting `main` (Canonical Off `main`)

**Date:** 2026-06-22
**Status:** Accepted
**Amended:** 2026-07-01, 2026-07-03, 2026-07-09 (see Amendment sections below)
**Tags:** worktrees, main, squat, canonical, prune, dev-env-sync, post-merge, park, safety, hooks, symlinks, detached-head, adr-093

---

## Context

During the dev-env PR #391 merge (2026-06-22), `gh pr merge --squash --delete-branch` exited 1
with `failed to run git: fatal: 'main' is already checked out at '…/.claude/worktrees/agitated-stonebraker-2156f6'`.
The remote squash-merge still succeeded; only gh's *local* post-merge checkout failed. Investigation
found a Claude-managed worktree squatting `main` **and** the canonical checkout (`~/Git/dev-env`,
whose working tree is symlinked into `~/.claude/`) itself off `main` on a stray `pr-385` branch — so
the newly-merged `posttooluse-inert-advisory.py` hook was registered in `origin/main`'s `settings.json`
but **not** in the live symlinked `~/.claude/settings.json`. Merged global tooling was silently inert
(dev-env#396).

**Root-cause chain (confirmed via reflog).** Git checks a branch out in **at most one worktree** at a
time.¹ So:

1. The **canonical** worktree was knocked off `main` onto a local `pr-385` branch (a `gh pr checkout`-style
   op run *in the canonical*, violating the "canonical stays on main" architecture rule). This **freed
   the `main` ref**.
2. With `main` free, the `agitated-stonebraker-2156f6` session merged its PR with `gh pr merge
   --delete-branch`. gh's `--delete-branch` deletes the merged local branch and **checks out the default
   branch (`main`) in the current worktree**² — so that worktree grabbed `main`.
3. Every subsequent merge from *other* worktrees then hit `fatal: 'main' is already checked out at …` on
   gh's local post-merge step; the `post-pr-merge-pull` hook fast-forwarded `main` *through* the squatting
   worktree rather than the canonical.
4. The canonical, still on `pr-385`, never returned to `main` → merged hooks/scripts stayed inert in the
   live `~/.claude/`.

The two failures are one invariant in two halves: **the canonical must always be on `main`** (its tree is
the symlink target) and **no non-canonical worktree may hold `main`**. Because of invariant ¹, a squatter
*implies* the canonical is off `main`. Nothing detected or corrected either half. `dev-env-sync` warned
generically that the canonical was off `main` but did not identify the squatter, did not correct anything,
and nothing caught the *worktree-grabbed-main* half at all.

## Decision

Add a shared, pure topology module and wire **detection + safe, non-destructive auto-correction** into
the three sites the issue identified. The correction precedent is **park-off-main**, not removal.

### Shared helper — `claude/scripts/_worktree_topology.py`

Import-only, policy-free, pure (no `_winsubp`, no subprocess, no `main()`) so its helpers unit-test offline
(mirrors `_worktree_liveness.py`):

- `parse_worktree_porcelain(text)` — parse `git worktree list --porcelain` (now also used by
  `prune-merged-worktrees.py`; `reclaim-worktree-disk.py` keeps its equivalent copy, out of this fix's scope).
- `canonical_worktree` / `main_squatter` / `canonical_on_main` — the canonical is the first list entry; a
  squatter is a non-canonical worktree on `main`.
- `park_branch_for(path)` → `claude/<basename>` — the branch a Claude-managed worktree should sit on.
- `diagnose_main_topology(worktrees)` → `MainTopology` (canonical branch, squatter path/branch, `healthy`).
- `canonical_sync_action(topo, clean)` → `warn-squatter` / `return-canonical` / `warn-dirty` / `on-main`.
- `merge_park_target(cwd, canonical, cwd_branch)` → the park branch for a worktree left on `main`, else `None`.

### Park, don't remove (non-destructive correction)

To free `main`, **recreate the worktree's own `claude/<slug>` branch at its current commit** (`git checkout
-b`). This changes **no working-tree files** (the branch is created at HEAD), so it frees the ref even for a
*dirty* squatter — which the old `git worktree remove` (no `--force`) silently *refused*, leaving `main`
locked. The freed worktree is removed later by the normal merged-branch path once it is idle and clean.

### Three wirings

- **`prune-merged-worktrees.py`** (daily routine, backstop) — the existing `branch == "main"` handler now
  **parks** the squatter instead of removing it. The ADR-051 liveness guard already sits above this block, so
  a *live* squatter is spared and only an idle one is parked.
- **`post-pr-merge-pull.py`** (the merge moment, prevention) — after the existing `git fetch origin main:main`,
  if `gh`'s `--delete-branch` left **this session's own** worktree on `main`, park it immediately. This acts on
  the hook's own just-merged cwd, so no liveness check is needed; it catches the squat at the instant it is
  created, with full session context.
- **`dev-env-sync.py`** (every prompt, the canonical half) — when the canonical is off `main`, diagnose the
  topology and **auto-return a *clean* canonical to `main`** (restoring the symlinks, then continue the
  fast-forward pull); **warn with the squatter's path + exact park command** when one is holding `main`; or
  **warn without switching when the canonical is dirty** (preserving uncommitted drift — the one case the
  2026-06-22 recovery needed human judgment for). The worktree enumeration runs **only** on the off-main path,
  so the healthy path stays cheap.

### Why split this way

Parking is generic and safe → it lives in the worktree-mutating sites (prune, post-merge) and frees `main` in
any repo, fixing the "blocks every other worktree's local post-merge checkout" blast radius beyond dev-env.
Returning the **canonical** to `main` is the dev-env-specific half (only dev-env has the symlink invariant) and
the riskiest (possible drift), so it lives only in `dev-env-sync` and only fires when the canonical is **clean**.
The three compose: post-merge frees `main` at creation → the next prompt's `dev-env-sync` returns the clean
canonical → prune is the daily backstop for any squatter that slipped through.

## Consequences

- The 2026-06-22 manual recovery session is eliminated in the common (clean-canonical) case: the squat is
  parked at the merge instant and the canonical auto-returns on the next prompt.
- Parking is strictly safer than the prior removal: it frees `main` even for a *dirty* squatter (which removal
  refused) and never deletes a worktree directory or any commits.
- **Liveness preserved (ADR-051):** prune parks only *idle* squatters (the guard runs first); post-merge acts on
  its own session; `dev-env-sync` never mutates *other* worktrees (warn-only there). No path can sever a live
  session.
- **Limitations.** (a) A *live* session squatting `main` is not parked until it goes idle (the warn covers it
  meanwhile). (b) A *dirty* canonical is not auto-returned — `dev-env-sync` warns with the manual command so
  drift is preserved. (c) If the park branch `claude/<slug>` unexpectedly already exists, the park is **skipped
  with a warning** rather than clobbering it (`git checkout -b` fails closed); the squat persists but is surfaced.
- One extra `git worktree list` + `git status` per prompt **only** when the canonical is off `main` (a rare,
  broken state); the healthy path adds nothing. post-merge adds one `git symbolic-ref` after a merge. Negligible.
- `prune`'s count semantics shift slightly: a parked worktree is reported as an action (under the pruned count)
  with a distinct "parked off main" line, not removed. Documented in the script docstring + README/REFERENCE.

## References

- [ADR-051](051-worktree-liveness-guard.md) — the liveness guard every corrective move here respects.
- [ADR-024](024-worktree-path-guard-hook.md) — the canonical/worktree write guard the orphaned squat tripped.
- [ADR-006](006-dev-env-sync-on-every-prompt.md) — the every-prompt sync hook this extends.
- [ADR-052](052-worktree-config-canonical-fallback.md) — prior canonical-vs-worktree resolution in the hooks.
- dev-env#396 — the incident this ADR remediates; surfaced during the PR #391 recovery.
- ¹ [git-worktree](https://git-scm.com/docs/git-worktree) — "the same branch cannot be checked out in more
  than one linked working tree" (the invariant that makes a squatter imply the canonical is off `main`).
- ² [gh pr merge](https://cli.github.com/manual/gh_pr_merge) — `--delete-branch` deletes the local + remote
  branch after merge (the local step checks out the default branch in the current worktree).

## Amendment (2026-07-01) — `post-pr-merge-pull.py`'s `pull_main()` needed the same canonical-on-`main` correction (dev-env#488)

This ADR's three wirings correct a worktree *squatting* `main` (a non-canonical worktree holding the
ref) and the canonical drifting *off* `main`. They did not cover the far more common topology for
dev-env's own repo: the canonical **itself** sitting on `main`, exactly as this ADR's own invariant
requires — which is precisely where `pull_main()`'s `git fetch origin main:main` (issue #275's fix)
breaks.

**Symptom (dev-env#488):** merging PR #476 left the canonical `C:/Users/brown/Git/dev-env` behind
`origin/main`. Git refuses `fetch origin main:main` whenever `main` is checked out anywhere,
including at the fetch's own target path:

    fatal: refusing to fetch into branch 'refs/heads/main' checked out at 'C:/Users/brown/Git/dev-env'

Because dev-env's canonical must always stay on `main`, `pull_main()` was guaranteed to fail this way
on **every** dev-env PR merge from a worktree — the only way dev-env PRs ever merge. The failure is
non-blocking (exit 0) and easy to miss, so the canonical silently drifted until the next prompt's
`dev-env-sync` caught up (this ADR's `return-canonical` wiring) — quietly defeating issue #275's whole
point (avoid waiting for the next prompt) for dev-env's own repo specifically.

**Fix:** `post-pr-merge-pull.py` now calls this ADR's own `canonical_on_main()` against the *same*
`git worktree list --porcelain` output it already fetches for `park_worktree_off_main`'s squatter
check (one list call per merge event, via the new `list_worktrees()` helper, not two). When the
target repo's canonical is on `main`, it runs a plain `git pull --ff-only origin main` instead of the
fetch-into-ref trick; when a feature branch (or a squatting worktree) holds `main`, the original
fetch-into-ref behavior is unchanged. The command choice itself is a pure `pull_command()` helper,
unit-tested offline in `test_post_pr_merge_pull.py` per this repo's no-subprocess-mock convention.
This ADR's existing squatter/off-main wirings are untouched — the amendment only adds the missing
third topology case (canonical on `main`) to `pull_main()`.

## Amendment (2026-07-03) — Confirmed outside dev-env; two-step merge workaround + on-demand unsquat remedy (dev-env#553)

This ADR's own Context already showed a squat blocking a *different* merge's local checkout (dev-env
PR #391, blocked by `agitated-stonebraker-2156f6`). The identical failure recurred in a project repo —
**lifting-logbook PR #664** (2026-07-03):

    failed to run git: fatal: 'main' is already checked out at
    'C:/Users/brown/Git/lifting-logbook/.claude/worktrees/fix+issue-646-restrict-db-e2e-default-role'

`git worktree list` confirmed `fix+issue-646-restrict-db-e2e-default-role` was still squatting `main`
at incident time — an idle, long-since-merged worktree whose own earlier `--delete-branch` checkout
apparently succeeded (main was free at that moment) rather than failing, landing it on `main` via
this ADR's exact root-cause chain. This confirms the "Why split this way" section's claim: parking
is generic and fixes the blast radius beyond dev-env — the squat-blocks-a-merge failure, and the
existing park wirings that eventually clear it, both apply to any repo, not only dev-env's own.

**Gap this amendment closes.** The wirings are correct but not instant everywhere: a squat in a
project repo persists until either the next daily `prune-stale-worktrees` run finds it idle and
parks it, or `post-pr-merge-pull.py`'s own-worktree park fires again on some other merge. Neither
helps the merge blocked *right now*, and nothing documented a clean way to finish that merge or to
un-squat on demand instead of waiting for the next 8am run.

**Two-step merge workaround (avoids the noisy failure entirely).** Rather than let `gh pr merge
--squash --delete-branch` hit the local-checkout failure and clean up after it, split it into two
calls that never touch the invoking checkout's local state:

```bash
gh pr merge <N> --squash                                          # server-side only; always succeeds
gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/<branch>"    # pure REST ref delete
```

Both legs are pure API calls — the second is the same REST ref-delete path
[ADR-035](035-git-push-delete-web-session-constraint.md) already standardizes on — so neither depends
on which worktree currently holds `main`.

**On-demand remedy for the underlying squat.** Don't wait for the next scheduled
`prune-stale-worktrees` run if the squat is actively blocking work — `prune-merged-worktrees.py`'s
squatter-park check (`squatter_path = main_squatter(worktrees)` → park) runs *before* the
`BRANCH_PREFIX` / `--include-named` gate, so it fires unconditionally regardless of the squatting
worktree's branch name:

```bash
py -3 ~/.claude/scripts/prune-merged-worktrees.py --repo-path C:/Users/brown/Git/lifting-logbook
```

An idle squatter is parked immediately (`git checkout -b claude/<slug>` at its current HEAD, this
ADR's non-destructive park precedent); a *live* squatter (recent session activity) is left alone per
the ADR-051 liveness guard, same as the daily routine.

No code change — this amendment is documentation only, closing the operational gap the ADR's
original wirings left (they correct the squat; they didn't document how to work around one that's
already blocking a merge). Full runbook entry:
[docs/REFERENCE.md → Git Workflow Runbooks](../REFERENCE.md#git-workflow-runbooks). Incident:
lifting-logbook [PR #664](https://github.com/brownm09/lifting-logbook/pull/664); dev-env#553.

## Amendment (2026-07-09) — `dev-env-sync.py` never detected a detached canonical HEAD (dev-env#619)

This ADR's `dev-env-sync.py` wiring determines the canonical's current branch via `git symbolic-ref
--short HEAD`. That command **fails** (non-zero exit) on a detached HEAD — there is no symbolic ref
to resolve — and the script responded with an immediate `sys.exit(0)`, **before** ever reaching the
diagnose/auto-correct/warn block this ADR documents above. So a detached canonical produced *zero*
warnings, on every single prompt, for as long as it stayed detached — confirmed via incident dev-env#617,
where the canonical sat detached for ~13 hours, undetected, found only by chance during unrelated
post-merge cleanup.

The topology helpers this ADR introduced already handled a detached canonical correctly *internally*:
`parse_worktree_porcelain` captures a detached worktree as `"<detached>"` (used by `main_squatter`'s
own bare/detached guard, above — "a bare or detached canonical cannot hold a working-tree checkout of
main at all, so a secondary worktree on main there is legitimate"). The gap was purely that nothing
routed a detached canonical *into* `diagnose_main_topology`/`canonical_sync_action` in the first
place — `dev-env-sync.py` exited before the topology was ever read.

**Fix.** A new pure helper, `resolve_current_branch(symbolic_ref_returncode, symbolic_ref_stdout)` in
`_worktree_topology.py`, maps a non-zero `symbolic-ref` return code to the same `"<detached>"` sentinel
`parse_worktree_porcelain` already produces, instead of signaling failure. `dev-env-sync.py` now calls
it unconditionally and falls into the *existing* off-main diagnostic block for `"<detached>"` exactly as
it already does for any other non-`main` branch — no other line in that block changed. Traced by hand
and confirmed by a new regression test (`test_canonical_sync_action_detached_head`,
`claude/scripts/tests/test_worktree_topology.py`) that threads `"<detached>"` through the *full*
`diagnose_main_topology` → `canonical_sync_action` pipeline, not just the isolated helper: a clean
detached canonical (zero unique commits, matching `main`'s own tip — exactly incident dev-env#617's
shape) now yields `"return-canonical"` (safe auto-return, matching this ADR's existing precedent for a
clean off-main canonical); a dirty detached canonical yields `"warn-dirty"` (preserve drift, matching
existing precedent); a detached canonical is never misdiagnosed as `"warn-squatter"`, since
`main_squatter`'s existing bare/detached guard already ensures `topo.squatter_path` is `None` in that
case.

**Consequences.** The dev-env#617-shaped incident (canonical silently detached for hours, zero warning)
is now caught and, in the common clean case, auto-corrected on the very next prompt of any session,
the same way a wrong-branch canonical already is. No behavior changes for the non-detached paths this
ADR already documents. One extra pure-helper call on the hot path (`resolve_current_branch` itself does
no I/O); the existing "extra git calls only on the rare/broken path" cost model is unchanged, since the
detached case was already the rare/broken path this ADR's off-main block exists for — it just wasn't
reachable before.

This module's `resolve_current_branch`, plus a second new predicate `is_hijacked_branch`, are also the
foundation for [ADR-093](093-journal-canonical-hijack-guard.md) — a standalone ADR (not a further
amendment here, since it targets a different repo under a materially different "healthy" invariant) for
a sibling corrective hook that defends the engineering-journal canonical against a related hijack
pattern (dev-env#630).
