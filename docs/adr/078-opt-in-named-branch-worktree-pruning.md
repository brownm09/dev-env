# ADR-078: Opt-In Named-Branch Worktree Pruning (`--include-named`)

**Date:** 2026-07-03
**Status:** Accepted
**Tags:** worktrees, prune, hooks, routines, cli-flag, safety, opt-in

---

## Context

`prune-merged-worktrees.py`'s `prune_one()` loop only ever considers pruning a worktree whose
branch starts with `claude/` (`BRANCH_PREFIX`). That prefix-guard check runs *before* the
merged/dirty/liveness checks, so any worktree on a hand-named branch (`feat/…`, `fix/…`,
`docs/…`, etc.) is skipped unconditionally, regardless of merge status — even a branch that is
fully merged into `origin/main`, has a clean working tree, and has no live Claude session
attached is left in place forever.

A manual, read-only audit across several repos under `C:/Users/brown/Git` (dev-env#545) found
that the vast majority of named-branch worktrees are in exactly that state: merged, clean, no
active session — as prunable as any `claude/*` worktree, just under a different branch-naming
convention (the convention this codebase itself uses for feature branches, per `claude/CLAUDE.md`
→ Git Workflow → Branch naming). Left alone, these worktrees accumulate disk usage and clutter
`git worktree list` output indefinitely, with no mechanism to reclaim them short of a manual
`git worktree remove`.

## Decision

Add an **opt-in** `--include-named` CLI flag to `prune-merged-worktrees.py`. Unflagged behavior
is byte-for-byte unchanged — every existing caller (the `prune-stale-worktrees` routine's
pre-existing invocation, any manual on-demand run, any other script that might shell out to this
one) sees identical output to today unless it explicitly opts in.

Mechanism, inside `prune_one()`'s per-worktree loop:

```python
if not branch.startswith(BRANCH_PREFIX) and not include_named:
    skipped.append((path, f"branch '{branch}' not in {BRANCH_PREFIX}* prefix"))
    continue
```

When `include_named=True`, a non-`claude/*` branch no longer short-circuits here — it falls
through to the **same** `is_merged()` / ephemeral-diff (`files_are_all_ephemeral`, ADR-075) /
`is_dirty()` checks that `claude/*` branches already go through, and is pruned via the identical
mechanism (`git worktree remove` + `git branch -d`, no new deletion pathway). Every check that
runs *before* the prefix guard in the existing loop — primary/cwd skip, live-session skip
(ADR-051), and main-squatter parking (ADR-058) — is structurally unconditional already (it runs
ahead of the branch-prefix check for every worktree regardless of branch name), so those
protections apply to named branches exactly as they do today; this change does not touch them.

`include_named` is threaded as a parameter through `prune_one()` (default `False`, preserving
every existing call site's behavior without modification) and `main()`'s `--scan-dir` loop, which
calls `prune_one()` once per discovered repo.

The `prune-stale-worktrees` scheduled routine (`claude/routines/prune-stale-worktrees/SKILL.md`)
is updated to pass `--include-named` on its scan-dir invocation — the actual point of this
change: without a caller passing the flag, the safer code path would ship inert.

## Consequences

- **Zero behavior change for every unflagged caller.** The default (`include_named=False`)
  reproduces the pre-existing prefix-guard skip reason verbatim
  (`"branch '<name>' not in claude/* prefix"`), so any downstream code or routine parsing that
  skip reason is unaffected.
- **Same safety envelope, extended to a wider branch-name surface.** No new prunability logic was
  added — named branches are now evaluated by the exact merged/ephemeral/dirty/liveness checks
  `claude/*` branches already pass through, not a looser or different set of checks.
- **The routine, not the script, decides the default policy.** A repo or caller that wants the
  conservative (claude/*-only) behavior simply omits the flag; `prune-stale-worktrees` opts every
  scanned repo in globally. A future need for a more granular, per-repo opt-in (mirroring
  ADR-075's `.claude/hook-config.json` pattern) is not addressed here — this is a global CLI
  toggle, not a per-repo config key, because unlike ADR-075's ephemeral-diff signal (which
  encodes repo-specific knowledge about what's safe to discard), "is this branch merged and
  clean" is a repo-agnostic question with the same answer shape everywhere.
- **Test coverage** added to `claude/scripts/tests/test_prune_merged_worktrees.py`: a
  regression case pinning that `--include-named` unset still skips a merged/clean named-branch
  worktree via the unchanged prefix-guard reason, and a companion case pinning that the same
  fixture is pruned once `--include-named` is passed.

## References

- [ADR-051](051-worktree-liveness-guard.md) — the live-session guard this change leaves
  unconditional and unmodified
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — the main-squatter parking
  logic this change leaves unconditional and unmodified
- [ADR-075](075-ephemeral-diff-worktree-pruning.md) — the sibling per-repo opt-in prunability
  signal in the same script, contrasted above (per-repo config vs. this ADR's global CLI flag)
- dev-env#545 — the motivating issue and manual audit
