# ADR-051: Worktree Liveness Guard — Skip Active-Session Worktrees in Prune/Reclaim

**Date:** 2026-06-22
**Status:** Accepted
**Tags:** worktrees, liveness, prune, reclaim, transcript, session, data-loss, routines, safety, hooks

---

## Context

On 2026-06-21 (~23:42 local) the `prune-stale-worktrees` routine removed the Claude-managed
worktree `pensive-taussig-448bc7` **while a Claude Code session was actively running inside it**
(dev-env#384). `git worktree remove` deleted the `.git` link and the admin dir
`…/.git/worktrees/pensive-taussig-448bc7/` and deregistered the worktree — the session's
transcript was still being written at that moment (last line `23:42:10`). The orphaned session
then had every `Write`/`Edit` blocked by the ADR-024 worktree-path guard, and bare `git` from
the session cwd silently resolved to the canonical main checkout — unsafe.

`prune-merged-worktrees.py` is the only worktree *remover*: `reclaim-worktree-disk.py` and
`post-pr-merge-reclaim.py` (ADR-037/045) merely `shutil.rmtree` regenerable `node_modules`/`.turbo`.
Two pre-existing facts combined to remove a live worktree:

1. **`is_merged()` is true for a branch sitting *at* `origin/main`.** It runs
   `git merge-base --is-ancestor <branch> origin/main`, which returns 0 when the branch tip *is*
   `origin/main` (a commit is its own ancestor). A fresh Claude worktree that hasn't committed yet
   (0 commits ahead) is therefore classified "merged" — exactly the incident's state
   (`claude/pensive-taussig-448bc7` at `origin/main`, *not* merged-and-gone).
2. **No liveness signal.** The only active-session protection was `path == os.getcwd()`, which
   shields only the *pruning process's own* worktree. The routine runs out-of-process in its own
   worktree (ADR-013 sync preamble) and passes no `--protect-cwd`, so it cannot see a live session
   in any *other* worktree. With the tree momentarily clean, removal succeeded.

An out-of-process maintenance routine fundamentally cannot detect another worktree's session via
`os.getcwd()`/`--protect-cwd`. It needs a signal that crosses the process boundary. Claude Code
writes each session's transcript to `~/.claude/projects/<slug>/<uuid>.jsonl`, where `<slug>` is the
session cwd with every `:` `\` `/` `.` replaced by `-`. Each worktree has a distinct cwd, hence its
own transcript dir, so a recent newest-`*.jsonl` mtime there is a reliable cross-process heartbeat —
verified empirically: the deleted worktree's transcript stopped at the removal instant, while a
live worktree's transcript updates every few seconds. Claude Code exposes no per-session lock file
(only the `<uuid>.jsonl` plus a `subagents/` subdir), so the transcript mtime is the available
signal.

## Decision

Add a shared, policy-free liveness helper and call it from both maintenance scripts' per-worktree
loops, **in addition to** (never instead of) the existing merged/clean/cwd checks.

### Shared helper — `claude/scripts/_worktree_liveness.py`

Import-only module (no `_winsubp`, no `main()`) so its helpers unit-test offline:

- `encode_project_slug(path)` — replicates the `:`/`\`/`/`/`.` → `-` transcript-dir encoding.
- `transcript_dirs_for(worktree, projects_root)` — the exact-slug dir alone if it exists, else
  *every* `projects/` subdir ending in `-worktrees-<basename>`; `worktree_session_is_live` takes the
  newest `.jsonl` mtime **across all** matches. The random worktree basename hedges a future
  encoding change, and aggregating the newest across matches keeps the over-protect-only property
  even if a same-basename dir from another repo is also matched (a stale wrong-repo match can't mask
  a live correct one). The `projects/` scan runs only on the rare exact-miss fallback path.
- `newest_jsonl_mtime(dir)` — newest `*.jsonl` mtime found **recursively** (so an active
  `<uuid>/subagents/*.jsonl` keeps a worktree live even when the top transcript is briefly quiet).
- `worktree_session_is_live(worktree, *, window_seconds, …)` — composes the above; `now`/
  `projects_root` injectable for tests.

**Fail-safe and additive.** A missing/empty/unreadable transcript dir yields `False` (not live ⇒
eligible) — identical to the pre-guard behavior for genuinely-idle worktrees, so cleanup of
abandoned worktrees keeps working. The guard only ever *adds* a skip, so a bug in it can at worst
over-protect (less cleanup), **never** remove or strip more than before.

### Window scales with the action's blast radius

Each script owns its window constant (the module is policy-free); both accept `--liveness-window-min`:

- **`prune-merged-worktrees.py` — 24h.** `git worktree remove` *severs* the session, so the
  destructive op gets the long guard. The only cost is a merged worktree lingering up to a day
  longer before removal.
- **`reclaim-worktree-disk.py` — 6h.** Stripping `node_modules` is self-healing (ADR-016 reinstalls
  on next use) and only disrupts a build/dev-server running *right now*, so a short window keeps
  disk reclamation aggressive against ENOSPC (the failure ADR-037/045 exist to prevent) and matches
  the routine's 6-hourly cadence. `--protect-cwd` already shields the current session; this guard
  additionally shields live sessions in *other* worktrees the routine cannot otherwise see.

The guard sits immediately after each loop's primary/cwd check, before the merged/dirty checks, so
the `active Claude session (recent transcript activity)` skip reason takes precedence.

### Deliberately *not* tightening `is_merged()`

Removing a clean, sessionless worktree whose branch never diverged is *correct* cleanup (prune's
job); tightening `is_merged()` to exclude at-`origin/main` branches would let abandoned fresh
worktrees accumulate forever. The liveness guard fixes the only harmful case — an *active* session —
which is precisely "in addition to the existing merged-branch check." The at-`origin/main`
classification stays.

## Consequences

- A worktree with transcript activity within its window is skipped by both prune and reclaim, across
  every repo the routine scans — so an out-of-process routine can no longer sever a live session in
  any repo, not just protect its own cwd.
- A recently-active *merged* worktree is removed/stripped one run later (once its transcript goes
  quiet past the window) instead of immediately — an intentional, safe delay.
- **Limitation:** a session left idle at the prompt longer than its window (24h prune / 6h reclaim)
  loses protection, because mtime cannot distinguish "abandoned" from "idle but will resume." A true
  process heartbeat would close this gap; Claude Code exposes none today. The windows are tuned so
  this is rare and the failure is the survivable one (a stripped `node_modules` reinstalls; a removed
  idle-merged worktree is recreatable).
- **TOCTOU:** the check is non-locking, so a session could in principle begin in the sub-second gap
  between the liveness check and the `git worktree remove` / `rmtree`. This is vanishingly unlikely
  and self-correcting (the next run protects a now-active worktree); a lock file was rejected as
  disproportionate for a periodic maintenance routine.
- A negative `--liveness-window-min` would make `is_recent()` false for every real transcript and
  silently disable the guard, so `liveness_minutes_arg` rejects it (`>= 0` only); `0` is
  allowed as an explicit opt-out.
- The two-window split is observable: in the verification dry-run, a worktree active within 24h but
  not 6h was protected by prune yet eligible for reclaim — the design working as intended.
- Adds one `is_dir()` + a small recursive `*.jsonl` glob per candidate worktree (few per repo);
  the `projects/` scandir fallback fires only when the exact slug is absent. Negligible.

## Amendment 1 (2026-11-07): a worktree's own git state

**Problem ([dev-env#1330](https://github.com/brownm09/dev-env/issues/1330)).** The transcript check
assumes a session's working folder is the worktree. A session started elsewhere that reaches a worktree
through `git worktree add` or `git -C` writes its transcript under its own folder, so the check can't see
it. A `--dry-run --scan-dir` preview on ANGSTROM-B on 2026-11-06 listed 99 removals, including worktrees
sessions had created minutes earlier.

**Decision.** `prune-merged-worktrees.py` also skips a worktree when `git_activity_skip_reason()` finds
its own git state written within the same `--liveness-window-min` window (24h). The check sits right
after the transcript check, so it also spares a recently used worktree from parking. Unlike the transcript
check, it fails toward keeping the worktree: one that exists but whose state can't be read may be in use.
`reclaim-worktree-disk.py` doesn't use the new guard: stripping `node_modules` is self-healing, as above.

**Measured property.** The newest mtime among `index`, `HEAD`, `FETCH_HEAD` and `ORIG_HEAD` in the
worktree's admin folder, found through its `.git` file.

Rejected signals, from a throwaway-repo probe and the real worktrees:

| # | Signal | Why not |
|---|--------|---------|
| 1 | Admin folder mtime | `git gc` in the primary moved it in the probe. On the real worktrees it was within 24h for 96 of 96, and written after the preview for 93 |
| 2 | `logs/HEAD` | `git gc` in the primary moved it in the probe |
| 3 | Worktree folder mtime | Sat in shared clusters across a repo's worktrees (for example 62.0h before the preview across lifting-logbook), the pattern of a bulk sweep and not of use |
| 4 | `.git` file and `gitdir` | Written once at creation, and `HEAD` already covers that |

**Calibration corpus.** The preview's 99 removals, with mtimes read on 2026-11-07 and ages taken relative to
the preview's end (23:51 on 2026-11-06). 3 were gone by then, leaving 96.

| # | Group | Count | Newest signal age at the preview | Verdict at 24h |
|---|-------|-------|-----------------------------------|----------------|
| 1 | Known bad, in use: `issue-dup-retrieval` | 1 | 0.2h | Skipped |
| 2 | Known bad, in use: `fix-prune-unknown-flags`, `issue-dup-rerun-1282` | 2 | Under 0.2h, from `HEAD` (their other files were rewritten after the preview) | Skipped |
| 3 | Created minutes before the preview and used after it: `1309-testing-index-merge` | 1 | About 0.1h at most. Its four signal files were rewritten after the preview, so this is inferred: its `.git` file shows it was created 0.1h before the preview, and `git worktree add` writes `index` and `HEAD` at creation, so at the preview they were no older | Skipped |
| 4 | Known good: pull request merged over 7 days before the preview | 17 | Youngest is 169.5h | 17 of 17 removable |
| 5 | Pull request merged over 24h before the preview | 50 (includes row 4) | | 49 of 50 removable |
| 6 | Written after the preview with no earlier value left to read | 3 | Unknown | Not counted |

Row 5's exception is `acceptance-criteria`, merged 62h before the preview with a fetch 23.5h before it.
That's real later activity, and the cost is one day's delay.

Merged-pull-request labels are independent of the signal, but came back only for 61 of 96: lookups for
lifting-logbook returned nothing, so that repo's 28 worktrees have ages and no label.

**Constants.** The 24h window is inherited from this ADR, not fitted, and is a policy choice. With the 90
worktrees whose values survived, it holds 9. A shorter window would release more: 5 at 12h, and 2 at 6h or
1h. The file list comes from the probe above.

**Margin.** The oldest known-bad age is 0.2h against a 24h window, so 23.8h. The youngest known-good age is
169.5h, so 145.5h past the window. The real worktrees give no sample of an in-use worktree idle for many
hours, so the known-bad side says nothing about sessions quiet that long.

**Effect on the preview.** 12 of the 99 removals become skips: the 9 above, and the 3 in rows 2 and 3. 81
stay removals. 6 can't be determined: the 3 gone and the 3 in row 6. Of the 12, 8 are merged worktrees that
simply go a day later.

**A dry run must not renew the signal.** A plain `git status` can rewrite the index, as the probe showed,
and that made a dry run followed by a real run skip the worktree. `is_dirty()` in this script now uses
`git --no-optional-locks`, and so does `reclaim-worktree-disk.py`'s, which runs on the same worktrees every
6 hours.

**A zero-commit branch still counts as merged.** This repeats the decision above, now with data: 7 of the
worktrees are at 0 commits ahead with no merged pull request and were idle at least 27.9h at the preview.
Calling those unmerged would keep abandoned worktrees forever, as "not merged" never expires, and a removal
loses no commits. A fast-forward or merge-commit merge also looks identical to a fresh branch by ancestry,
so telling them apart needs a `gh` call per worktree from a shared rate-limit budget. Recent activity is
what separates a fresh worktree in use from an abandoned one.

**Limits.**

- A session that only reads writes nothing here, so it's invisible until it does. Edits show as dirty, and
  that check still applies.
- A session quiet in git for over 24h loses protection, as the transcript check's limitation above says.
- The corpus is one machine on one evening, and the labels above leave out one repo. `git gc` and a fetch in
  the primary were probed, but other tools that write these files were not surveyed. A false "in use" costs
  one day's delay.
- Alternatives rejected: scanning recent transcripts for the worktree's path, because a transcript that
  merely mentions a path (this preview's own session lists all 99) would block every removal it names; and
  process command lines, which miss the common case of a session using `git -C` between commands.

## References

- [ADR-024](024-worktree-path-guard-hook.md) — the worktree-path guard whose orphaned-worktree
  addendum the severed session tripped; this ADR removes the upstream cause
- [ADR-037](037-worktree-disk-reclamation.md) — `reclaim-worktree-disk.py` + the 6-hourly routine
  the 6h reclaim window matches
- [ADR-045](045-pre-install-freespace-gate.md) — the ENOSPC pressure that argues for keeping the
  reclaim window short
- [ADR-013](013-sync-routine-worktree-skill.md) — why the routine runs out-of-process (and thus
  cannot see other sessions via cwd)
- dev-env#384 — the incident this ADR remediates; discovered alongside #381 (PostToolUse hooks not
  firing)
