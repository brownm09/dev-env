# ADR-075: Ephemeral-Diff Worktree Pruning — Opt-In Additional Prunability Signal

**Date:** 2026-07-02
**Status:** Accepted
**Tags:** worktrees, prune, journal-compose, hook-config, per-repo-opt-in, safety, routines

---

## Context

`prune-merged-worktrees.py`'s `is_merged()` recognizes exactly two kinds of "merged": a branch
that's an ancestor of `origin/main`, or a branch with its own merged GitHub PR. Neither condition
holds for a real, verified-safe class of worktrees in `brownm09/engineering-journal`, even though
the daily `prune-stale-worktrees` routine (`--scan-dir C:/Users/brown/Git`) already scans that
repo among all others.

That repo's `/journal-compose` workflow spawns background-agent worktrees on throwaway
`claude/<slug>` branches that commit per-session `*.stub.md`/`*.manifest.jsonl`/`open-prs/*.json`
scaffolding files. That content later gets folded into a canonical composed doc on `main` by
journal-compose's Step 9 ("Delete stub files and release lock"), **which then deletes the raw
stub files from main**. So the throwaway branch's own commits never become an ancestor of `main`
(the files were removed, not carried forward) and never get their own PR (only the shared
`draft/YYYY-MM-DD` branch does) — permanently invisible to `is_merged()`.

**Empirical validation (2026-07-02).** A manual audit of engineering-journal's worktree backlog
found 47 stale worktrees plus 7 additional orphaned local branches (no worktree attached),
spanning back to April 2026. Every one was individually verified safe to remove: for each branch
not caught by the existing ancestor/PR checks, `git diff --name-only origin/main...<branch>` was
checked against the scaffolding pattern below, and for any branch whose diff included a
non-scaffolding file (e.g. a stale `README.md`, or a composed `.md` doc), `git show
origin/main:<path>` confirmed the same content already exists on `origin/main` — no data loss in
any case checked. A live re-validation during the same investigation, run against the exact
patterns below across engineering-journal's 33 `claude/*` branches, found 29/33 classify as fully
ephemeral by filename alone; the other 4 have real content (composed docs, confirmed
byte-identical on `main`) sitting alongside stale `README.md` diffs in the same branch — correctly
*not* auto-classified by a filename-only check (see "Known scope limitation" below). Without a
code fix, this backlog reaccumulates daily regardless of the existing routine.

## Decision

Add an **opt-in, per-repo** additional prunability signal to `prune-merged-worktrees.py`, checked
only as a fallback when the existing ancestor/PR checks in `is_merged()` already say "not merged."
It never tightens or replaces `is_merged()` — deliberately, the same precedent [ADR-051](051-worktree-liveness-guard.md)
set for its own liveness guard ("in addition to, never instead of, the existing merged/clean
checks").

Three functions in `claude/scripts/prune-merged-worktrees.py` (co-located with `is_merged`/
`is_dirty`, not extracted to a shared module — this feature has exactly one consumer, unlike
`_worktree_liveness.py` which is shared by two scripts):

- `diff_files(branch, repo)` — `git diff --name-only origin/main...branch` (three-dot: against
  the merge-base, not `origin/main`'s current tip, since `origin/main` normally moves on while a
  worktree sits idle).
- `files_are_all_ephemeral(files, patterns)` — pure; true only when every file matches at least
  one regex. Empty `patterns` or empty `files` always returns `False` — the opt-in gate, and
  empty-files is defensive (a zero-diff branch is already caught by `is_merged()`'s ancestor
  check upstream, per the same "branch sitting *at* `origin/main`" fact ADR-051 documents).
- `load_ephemeral_patterns(repo)` — reads `prune_ephemeral_patterns` (a list of regex strings)
  from the repo's `.claude/hook-config.json`. Fail-open to `[]` (feature off) on a missing file,
  missing key, malformed JSON, or non-list/non-string-element value — matching this codebase's
  existing `hook-config.json` reader convention. One invalid regex disables the *whole* list for
  that repo (not just the bad entry) and prints a warning, rather than silently running a reduced
  pattern set that doesn't match what's on disk.

Integration point, `prune_one()`'s existing merge check:

```python
if not is_merged(branch, gh_repo, repo):
    patterns = load_ephemeral_patterns(repo)
    if not (patterns and files_are_all_ephemeral(diff_files(branch, repo), patterns)):
        skipped.append((path, "not merged into origin/main"))
        continue
```

`load_ephemeral_patterns`/`diff_files` only run when `is_merged()` already returned `False` — zero
added subprocess overhead for every repo that hasn't opted in, or any branch merged the normal
way. Falls through to the existing `is_dirty(path)` check unchanged; no path bypasses it.

**Engineering-journal's actual opt-in** (`​.claude/hook-config.json`):

```json
{ "prune_ephemeral_patterns": ["\\.stub\\.md$", "\\.manifest\\.jsonl$", "open-prs.*\\.(json|jsonl)$", "\\.draft-compose\\.lock$"] }
```

## Consequences

- Safe by construction in every direction that matters: empty patterns/files never force a prune
  (only ever suppress one skip reason — the same additive-guard shape ADR-051 uses); a malformed
  regex disables the feature for that repo rather than crashing the scan or silently narrowing it;
  still gated by `is_dirty()` afterward, same as every other prunability path; off by default for
  every repo that doesn't explicitly configure it.
- **Known scope limitation.** A branch whose diff includes a non-scaffolding file is never
  auto-pruned by this feature even when that file is *also* safe (e.g. a byte-identical composed
  doc sitting alongside a merely-stale `README.md` in the same diff) — proving "this file differs
  but is still safe" requires content comparison, not filename matching, which is deliberately out
  of scope for this addition. 4 of engineering-journal's 33 `claude/*` branches fall in this
  residual at write time; they were removed by the one-time manual cleanup this ADR accompanies,
  but a future recurrence of the same shape stays unpruned by the automated routine until a
  content-aware check is warranted by more than a handful of worktrees.
- **Worktree-scoped, not branch-scoped.** `prune_one()` only ever iterates `git worktree list
  --porcelain` entries — a local branch with no worktree attached is invisible to this script
  regardless of this change. Engineering-journal's separate ~90-branch orphaned-local-branch
  backlog (discovered during the same investigation, no worktree attached to any of them) is not
  addressed by this ADR and needs its own audit.
- A repo adopting this must own its own `prune_ephemeral_patterns` list and understand exactly
  what "safe to discard because captured elsewhere" means for its own workflow — this is not a
  generic "ignore these file types" allowlist, it is a claim that content matching every listed
  pattern is *never* the sole copy of anything worth keeping.

## References

- [ADR-051](051-worktree-liveness-guard.md) — the sibling prunability-signal precedent this
  design mirrors (additive, off-by-default, deliberately not tightening `is_merged()`)
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — the other recent addition to
  this same script's `prune_one()` loop
- `claude/skills/journal-compose/SKILL.md` Step 9 — the upstream mechanism (stub deletion after
  compose) that makes engineering-journal's scaffolding branches safe to discard
