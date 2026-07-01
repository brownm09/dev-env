# ADR-058: Detect & Auto-Correct a Worktree Squatting `main` (Canonical Off `main`)

**Date:** 2026-06-22
**Status:** Accepted
**Amended:** 2026-07-01 (see Amendment section below)
**Tags:** worktrees, main, squat, canonical, prune, dev-env-sync, post-merge, park, safety, hooks, symlinks

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
