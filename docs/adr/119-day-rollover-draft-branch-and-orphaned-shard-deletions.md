# ADR-119: Day Rollover Cuts a Fresh Draft Branch; Orphaned Open-PR Shard Deletions Are Surfaced, Not Auto-Committed

**Date:** 2026-07-22
**Status:** Accepted
**Tags:** journal, stubs, draft-branch, day-rollover, open-prs, sharding, hooks, UserPromptSubmit, new-day-journal-check, reconcile-open-prs, canonical-checkout, silent-failure, data-loss, global-rule, adr-017, adr-056, adr-082, adr-084

## Context

Two engineering-journal drift states observed on 2026-07-22 ([dev-env#866](https://github.com/brownm09/dev-env/issues/866)), both leaving the shared canonical checkout in a shape the documented Stub file workflow does not describe.

### 1. A day rolled over with the prior day's draft unmerged

The canonical sat on `draft/2026-07-21` with no `draft/2026-07-22` on the remote — yet that branch already carried commits whose own subjects read `draft: 2026-07-22 session`, across career-playbook, engineering-playbooks, and lifting-logbook. The 21st had never been composed, and the 22nd's work was piling onto its branch.

The Stub file workflow says the first session of a day cuts `draft/YYYY-MM-DD` from `main`. The session that hit this read that literally and concluded it would *fragment* the day — today's other sessions' stubs were already on `draft/2026-07-21`, so a fresh `draft/2026-07-22` would split one day across two branches. It joined the existing branch instead, reasoning that stub filenames still carried the correct date and that is what compose discovers on. That was an undocumented judgment call, and it was the wrong one.

**Why it was wrong — both halves of the key must agree.** Discovery is not filename-only. It is filename **and** branch:

- `/journal-compose` resolves `SOURCE_BRANCH = draft/<DATE>` and then globs `sessions/*/<DATE>_*.stub.md` **on that branch** (`claude/skills/journal-compose/SKILL.md`, Step 0.6).
- The nightly `daily-journal-compose` routine gates on `git show-ref --verify --quiet refs/remotes/origin/draft/${DATE} || exit 0`.

So a stub whose filename date differs from its branch's date is invisible to both — and the routine's miss is **silent**, not an error. Joining the stale branch guarantees the newer day is never composed *and never reported*. Worse, the stale branch composes only its own date, so when it finally merges, the newer day's stubs ride into `main` uncomposed, where nothing looks for them again.

That is not hypothetical. At the time of writing, **26 stubs across 5 dates** (2026-05-11, 2026-05-31, 2026-06-29, 2026-07-01, 2026-07-03) sat uncomposed on `origin/main` — the accumulated residue of exactly this shape. The codebase already half-knew: `new-day-journal-check.py`'s `stale_draft_artifacts()` docstring describes stubs "carried forward because the new-day branch was cut from the previous day's draft instead of from main" — and *suppresses the resulting false positive* rather than preventing or flagging the state.

The premise behind the "fragmentation" worry also does not survive inspection. Several unmerged `draft/*` branches coexisting is the **normal** steady state — 33 existed on the remote that day — because each is an independent per-day unit that composes on its own. The apparent fragmentation only appears in the already-broken state where some of today's stubs are *already* misfiled; that is a recovery problem, not a reason to change the steady-state rule.

### 2. Orphaned open-PR shard deletions persisting in the canonical

`git status` in the canonical showed four uncommitted deletions — `sessions/lifting-logbook/open-prs/{853,856,859,861}.json` — for PRs merged 2026-07-20/21. The deletions were *correct* post-merge bookkeeping performed by `reconcile-open-prs.py`, which unlinks merged shards but deliberately never commits. A prior session left them uncommitted, and the observing session correctly declined to sweep another session's paths into its own commit — the [ADR-056](056-per-session-sharding-journal-companion-files.md) explicit-pathspec rule working as designed.

But nothing else picks them up either. Since ADR-056 moved stub commits to a per-file pathspec and [ADR-082](082-journal-compose-worktree-isolation.md) removed compose's bulk `git add -u`, no mechanism commits a *different* session's unlink. The hook's own advisory said to "include these paths in your next stub commit's pathspec" — unreachable for the many sessions that open no PR and therefore write no stub. The result is permanent `git status` noise every later session must re-triage, one `git restore` away from resurrecting the stale records, and a *committed* branch that keeps listing merged PRs as open until a compose reconciles it.

Reading the hook rather than trusting the issue's framing sharpened one detail: because the shards are already unlinked *on disk*, an on-disk reader does not list those PRs as open — the "Open PRs:" line was already correct. The harm is the uncommitted-and-unowned state itself, not a mis-listed open-PR set.

Inspecting the same code surfaced a genuine latent defect. `find_dirty_open_pr_paths` matched **any** dirty `sessions/*/open-prs*` path regardless of status, and the message told Claude to add all of them to its pathspec. An *added* or *modified* shard belonging to a concurrent session matches that filter — so following the advisory literally would sweep another session's in-flight shard into the reader's commit. That is precisely the clobber ADR-056's explicit-pathspec rule exists to prevent, emitted as advice by the very hook meant to help.

## Decision

### 1. Day rollover: always cut `draft/<today>` from `main`

Regardless of how many prior days' drafts are unmerged. A stub's filename date and its branch date must always match, because both halves are load-bearing for discovery.

Rejected alternatives:

- **(a) Join the existing branch** — the observed behavior. Guarantees the newer day is never composed and never reported, and silently orphans its stubs onto `main` when the stale branch merges. This is the failure being fixed, not a candidate.
- **(c) Compose-and-merge the stale branch first** — the correct *remediation*, but it cannot be the rule. `/journal-compose` is a dedicated-session operation ("Never run it alongside other tasks"), so a session that merely needs to write a stub cannot perform it; requiring it would either block the stub or violate the composition rule.

The accepted cost of (b) is real and bounded: `draft/<today>` cut from `main` does not carry yesterday's open-PR shard edits, so its `open-prs/` view is only as current as the last merge to `main`. `reconcile-open-prs.py` corrects that live at session start via `gh pr view`, which is the authority anyway.

**One ordering consequence, found while implementing this.** "Shard edits" includes shard *deletions*: a merged PR's shard removed only on the unmerged branch is still present on `main`, so a fresh branch **resurrects** it. The self-heal is real but lands on a *later* session, because the reconcile hook's sentinel is once-per-session. So the branch cut belongs at the **start** of a session, not after that session has already done shard cleanup — otherwise it leaves the resurrected shards dirty for someone else to inherit, which is the very state decision 3 exists to stop. This was verified concretely: all four shards this decision's own implementation cleaned up were still on `main`, which was 43 commits behind `draft/2026-07-21`. That is also why this ADR's implementing session deliberately did **not** cut `draft/2026-07-22` — doing so would have undone its own cleanup commit — and instead wrote its stub onto the stale branch and tiled the compose-first remediation, i.e. took path (c) as the remediation exactly as this decision prescribes.

### 2. The divergence gets a name and a handling: *date-mismatched stub*

A stub whose filename date differs from the date of the draft branch it is committed on. When the state already exists, repair **additively** — never rewrite the stale branch's history, since every concurrent session shares that checkout:

1. Still cut `draft/<today>` from `main` for your own stub. This bounds the damage to the already-misfiled set instead of adding to it.
2. Surface it and tile the remediation ([ADR-113](113-cross-session-handoff-tiles.md)) — never leave it implicit.
3. After `draft/<D>` composes and merges, the mismatched stubs are on `main` uncomposed. Bring them into their own day's branch with `git fetch origin` + `checkout draft/<today>` + `merge origin/main`; `/journal-compose <today>` then discovers them by filename date.
4. If `draft/<D>` had already merged, it is the resurrected-branch path instead: `reconcile-late-stubs.py draft/<D>`.

Detection is advisory-only, added to `new-day-journal-check.py` as a fourth check. It fires when the canonical rests on a `draft/<D>` with `D != today`, and additionally names stubs on that branch dated **after** `D`.

**The rollover check alone is not suppressed in Claude-managed worktree sessions.** The hook has always exited early there on the grounds that "journal warnings are only actionable in main-checkout sessions." That is true of checks 1–3 (canonical housekeeping) but false for this one: worktree sessions write stubs into the canonical via `git -C` exactly like any other session, and the session that hit dev-env#866 *was* a worktree session, so it saw nothing. Because worktree sessions can now emit, `cleanup_stale_flags()` must run for them too.

**The mismatch predicate is deliberately one-sided** — only stubs dated *after* the branch. On its first live run the two-sided version returned 33 hits, burying the 6 real ones under 27 older lineage artifacts that `stale_draft_artifacts`/`unmerged_draft_branches` already cover.

### 3. Orphaned shard deletions are surfaced, never auto-committed — but the surfacing is fixed

`reconcile-open-prs.py` does not commit. Four reasons, any one sufficient:

1. It is an advisory `UserPromptSubmit` hook bound to fail open (REFERENCE → Hooks → Authoring rules, rules 2 and 5). Committing is a mutation whose failure modes are worse than the state it fixes.
2. The canonical is shared by every concurrent session, and they share one git index — an auto-commit would race a session mid-`git add`, the exact hazard ADR-056's addendum exists to prevent.
3. It would commit onto whatever branch the canonical happens to hold — possibly a stale draft (decision 1) or `main`, which is forbidden outright.
4. [ADR-071](071-canonical-checkout-mutate-guard-hook.md)'s `pre-tool-use-canonical-mutate-guard.py` blocks `git commit` at a canonical root; a hook auto-committing there would contradict the repo's own guard.

What changes is the surfacing, in three parts:

- **Classify by porcelain status, not just path shape.** Deletions (`D` in either column) are separated from additions/modifications/renames. Only deletions are ever recommended for commit; everything else is reported as a concurrent session's in-flight shard, explicitly hands-off. This closes the latent clobber-advice defect above.
- **Confirm the PR before recommending.** The deleted shard is gone from the working tree, so its `url` is read from `git show HEAD:<path>` and the state from `gh pr view`. Four buckets: `merged` (safe to commit, with a ready-to-run explicit pathspec), `open` (a live record was deleted — an anomaly, flagged, never recommended), `unverified` (`gh` failed — notably when the GraphQL budget is exhausted, which `gh pr view --json` draws on and which was live at implementation time), and `skipped` (beyond a probe cap, reported rather than silently dropped, per the no-silent-caps principle).
- **Give the advisory an owner.** A global CLAUDE.md rule makes an orphaned deletion for a confirmed-merged PR **yours to commit immediately**, whether or not you write a stub — the one deliberate exception to "commit only your own files." It is safe precisely because ADR-056 made each shard a disjoint per-PR file: the delete cannot touch another PR's record, and the state was verified live.

## Consequences

**Positive.** The silent-orphan path closes: a stub can no longer land on a branch no discovery path will look at, and when it already has, the state is named, detected, and repairable without rewriting shared history. The hook stops emitting advice that could clobber a concurrent session's shard. Orphaned deletions get an owner instead of being inherited indefinitely. The `unmerged_draft_branches()` message no longer asserts the opposite of its own predicate (it claimed the branches "have composed journal files" while the filter selects dates with *no* composed file on `main`).

**Negative / accepted.** More `draft/*` branches will coexist, each needing its own compose — already true, now deliberate. A `draft/<today>` cut from `main` starts with a slightly stale `open-prs/` view until the reconcile hook runs. Each orphaned deletion costs two subprocesses (`git show` + `gh pr view`) within the hook's 30s budget, capped at 10 and reported when capped. Committing another session's deletion is a shared-checkout write, mitigated by the explicit pathspec and by per-PR disjointness.

**Not addressed here.** Why the nightly compose did not run for 2026-07-21 in the first place is a separate question — this ADR governs what a session does once the rollover has happened, not the upstream compose failure. The 26 pre-existing orphaned stubs on `main` are likewise left for a dedicated recovery pass; the detector's one-sided predicate deliberately does not surface them.

## References

- [dev-env#866](https://github.com/brownm09/dev-env/issues/866) — the originating observation (both drift states)
- [ADR-017](017-journal-compose-today-guard.md) — today-guard; establishes the date→branch coupling this ADR depends on
- [ADR-056](056-per-session-sharding-journal-companion-files.md) — per-session/per-PR sharding and the explicit-pathspec commit rule that makes a lone shard deletion safe to commit
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — canonical-checkout mutate guard (why a hook must not commit there)
- [ADR-082](082-journal-compose-worktree-isolation.md) — compose worktree isolation; removed the last mechanism that opportunistically committed a stray unlink
- [ADR-084](084-nightly-compose-targets-yesterday.md) — the nightly routine's date selection
- [ADR-113](113-cross-session-handoff-tiles.md) — cross-session hand-offs are tiles (the remediation-capture step)
- [Git — `git-status` porcelain format](https://git-scm.com/docs/git-status#_short_format) — the `XY <path>` status columns the classifier reads
- [Git — `git-worktree`](https://git-scm.com/docs/git-worktree) — one-worktree-per-branch, why the canonical is shared rather than isolated
- [GitHub — REST vs GraphQL rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) — independent budgets; why `gh pr view --json` can fail while REST is healthy
