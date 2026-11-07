# ADR-161: Maintenance Routines Report What Changed Since Their Last Run

**Date:** 2026-11-07
**Status:** Accepted
**Closes:** [dev-env#1307](https://github.com/brownm09/dev-env/issues/1307)
**Tags:** routines, token-efficiency, reconciliation, worktrees, prune, notifications, cli-flag
**Related:** [ADR-051](051-worktree-liveness-guard.md), [ADR-070](070-reconcile-project-board-scan-dir.md), [ADR-078](078-opt-in-named-branch-worktree-pruning.md)

---

## Context

`prune-stale-worktrees` and `reconcile-project-board` printed the same lists every night: the Impact/Why gaps, the not-merged worktrees and the fetch failures. In the 2026-09-19 retro window, about 19.5 of dev-env's 24 journaled days were repeating runs like these with no code change ([dev-env#1103](https://github.com/brownm09/dev-env/issues/1103)). [dev-env#1130](https://github.com/brownm09/dev-env/issues/1130) estimated about $10-12 a day at list price, which is plan-limit use and not a charge. A list that repeats nightly also hides the one new entry that matters, and a push notification that fires every night stops being read.

## Decision

1. **A `--scan-dir` run compares against what the last one found.** `claude/scripts/_last_run.py` stores findings as `{repo: {section: {key: value}}}` under `~/.claude/scratch/last-run/`, one file per script. A repeat run lists only the keys that are new or whose value changed, and names the ones that have gone.
2. **The `RESULT:` line keeps the totals and gains the change counts.** The routines' notification steps key on the `new_*` fields, not the totals. `prune-merged-worktrees.py` had no `RESULT:` line and now has one in `--scan-dir` mode.
3. **A missing or unreadable state file gives the full report,** marked `baseline=true`. The file holds nothing that can't be rebuilt, so losing it costs one long report and one large notification.
4. **Comparing and committing are separate steps.** A run writes its findings to `<state>.pending`, and `py -3 claude/scripts/_last_run.py commit <state-file>` makes them the state the next run compares against. The routine commits only after it has sent its notification, or decided none was needed. A failed push, a crashed or timed-out session, a run with no `RESULT:` line, or a `project`-scope abort therefore reports the same findings again the next night, rather than losing them. A scope abort writes nothing at all.
5. **`--full` prints everything and neither reads nor updates the state.** A person looking at the list shouldn't use up the news the next routine run would have reported. `--dry-run` compares but doesn't update, for the same reason.
6. **`--repo-path` and `--repo-root` runs are unchanged.** Someone naming one repo wants its report, and no routine runs that way.

### What counts as a change

| # | Situation | Treatment | Why |
|---|---|---|---|
| 1 | A repo's `gh` call fails, or its worktree list can't be read | Its stored findings are kept | Otherwise everything reads as resolved tonight and as new tomorrow |
| 2 | A worktree is the primary or current one, or has a live session | Counted in `skipped`, not listed, and not a finding | The routine's own worktree is new every run, and a session's worktree goes live and idle on its own. A worktree already on record keeps its entry while live. On a baseline run they are still printed, with the rest of the full report |
| 3 | An issue lacks different fields than last time | Listed again | What it needs has changed |
| 4 | An orphan whose add failed gets added | Listed again | Only now can the report print its edit commands |
| 5 | A failure that continues (`add_failed`, `repos_failed`, `fetch_failed`) | In the totals, no new notification | It was reported the night it started |
| 6 | A finding whose reason changes (a different skip reason, a different set of missing fields) | Listed again and notified | The value stored for the key changed, so it counts as new |
| 7 | A prune repo whose GitHub remote can't be read, or a reconcile repo whose `gh` calls work again | Reported once, as `new_remote_failed` or a `gh works again` line | A repo that silently drops out of the scan is the failure this change must not hide |

## Alternatives rejected

- **Lower the routines' cadence.** The retro proposed this as an option. It cuts cost by the same factor it delays a real finding: an orphan issue or a stuck worktree would wait days. It also leaves each run's report as long as before.
- **Have the routine's model compare tonight's output with the journal.** That keeps the cost this change exists to remove, since the model still reads the whole list, and leaves the comparison to judgment rather than a test.
- **Make change reporting opt-in behind a flag the routines pass.** Rejected by the issue's acceptance criteria, and it would leave a hand-run `--scan-dir` as the odd one out.

## Consequences

- A hand-run `--scan-dir` writes only the pending file and never commits it, so it can't use up the next routine run's news. A routine run that follows replaces that pending file with its own. [`docs/REFERENCE.md`](../REFERENCE.md) still suggests `--full` for reading the whole list.
- A hand-run prune `--scan-dir` without `--include-named` records different reasons for named branches, which the next routine run reports as changes. This is noise, not a missed finding.
- A repo removed from the scan directory leaves an inert entry in the state file. Deleting the file resets everything to a full report.
- Skip reasons that carry `git` stderr compare as text, so a reworded error is listed as a change.
