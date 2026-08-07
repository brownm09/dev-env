# ADR-119: Day Rollover Cuts a Fresh Draft Branch; Orphaned Open-PR Shard Deletions Are Surfaced, Not Auto-Committed

**Date:** 2026-07-22 (amended 2026-07-26)
**Status:** Accepted
**Tags:** journal, stubs, draft-branch, day-rollover, open-prs, sharding, hooks, UserPromptSubmit, new-day-journal-check, reconcile-open-prs, canonical-checkout, silent-failure, data-loss, global-rule, self-healing, stale-canonical, auto-recovery, concurrency, toctou, adr-017, adr-056, adr-082, adr-084, adr-071, adr-093, tiles, adr-118

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

**One lineage consequence, found while implementing this — and then observed live.** "Shard edits" includes shard *deletions*, and the governing invariant is **branch lineage, not session ordering**:

> A shard deletion committed to draft branch **A** stays invisible to any branch **B** cut from `main` until **A** merges. A deletion is durable only once its carrying branch merges to `main`.

A merged PR's shard removed only on an unmerged branch is still present on `main`, so a branch cut from `main` resurrects it, and the reconcile hook must re-unlink it there before anyone can commit the deletion again.

The first draft of this ADR stated only the *corollary* — "cut the branch at the start of a session, not after cleanup work" — which is true but strictly narrower, and it was falsified within hours. `draft/2026-07-22` was cut correctly, at the start of a session, from a `main` that still carried all four shards this decision's own implementation had just cleaned up (`main` was 43 commits behind `draft/2026-07-21`). All four resurrected anyway; they had to be re-verified merged and re-committed on the new branch. **No session ordering would have prevented that** — only landing `draft/2026-07-21` would have. The corollary still holds for the single-session case (cutting after your own cleanup leaves your deletions behind on the old branch), but it is a consequence of the lineage rule, not the rule itself.

This is also why this ADR's implementing session deliberately did **not** cut `draft/2026-07-22` itself: doing so would have resurrected the four shards it had just committed deletions for. It wrote its stub onto the stale branch and tiled the compose-first remediation — path (c) as the remediation, exactly as this decision prescribes.

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

### Implementation constraints the review pinned down

The [review of PR #873](https://github.com/brownm09/dev-env/pull/873) found that the first implementation did not actually deliver decision 3's guarantee. Four of its findings are load-bearing enough to record as part of the decision rather than as commit trivia:

- **"Deletion" must be an exact porcelain match (` D` / `D `), never a `"D" in status` substring test.** The two-char status field puts a `D` in `AD`/`RD` — a concurrent session's *staged* shard, precisely the class this decision exists to keep out of your pathspec — and in the unmerged `DD`/`DU`/`UD`. The conflict codes are the worst case: the recommended `git add` *silently resolves* the conflict and the following partial `git commit` fails outright, stranding the shared canonical mid-merge, reachable straight from this ADR's own `git merge origin/main` remediation. The whole deletion advisory is therefore also suppressed while `MERGE_HEAD` exists.
- **PR identity must be cross-checked, not taken from the filename.** `journal-shard-write-advisory.py` exists to flag a shard whose embedded `pr` disagrees with its filename stem, and it is only *advisory* — so such shards land on disk. Trusting the filename alone lets a still-OPEN PR be reported as "confirmed merged, commit now". A mismatch routes to `unverified`.
- **PR state must not be GraphQL-only.** `gh pr view --json` is GraphQL; GraphQL and REST have separate budgets, and GraphQL is the one that empties (0/5000 for hours while REST held ~4990 during this very implementation). Without a REST fallback the orphan cleanup fails exactly when the documented rate-limit hazard is live.
- **The probe budget must be a wall clock, not a count.** N probes × two 15s timeouts vastly exceeds the hook's declared 30s, and the probes run *after* the once-per-session sentinel is set and *before* the single `print()` — so a timeout discarded the entire message, including the pre-existing `Open PRs:` line that is this hook's original ADR-018 purpose, with no retry. The message is now assembled before probing, and probing stops on a deadline.

A fifth finding concerns detection rather than the decision: the day-rollover check and checks 1–3 must use **separate** sentinels. Sharing one let a worktree session's rollover emission suppress checks 1–3 for the remainder of that session — including after its cwd left the worktree, which is when they become actionable.

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
- [ADR-118](118-tile-persistence-shards.md) Amendment 5 (2026-08-07) — decision 3's classification model (exact porcelain-code deletions, `git show HEAD:<path>` identity recovery, mid-merge suppression, hook-never-commits rationale) reapplied unchanged to tile shards, one artifact type over; see that amendment for what is genuinely tile-specific rather than repeated here
- [Git — `git-status` porcelain format](https://git-scm.com/docs/git-status#_short_format) — the `XY <path>` status columns the classifier reads
- [Git — `git-worktree`](https://git-scm.com/docs/git-worktree) — one-worktree-per-branch, why the canonical is shared rather than isolated
- [GitHub — REST vs GraphQL rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) — independent budgets; why `gh pr view --json` can fail while REST is healthy

## Amendment 1 (2026-07-26, dev-env#911) — Stale-canonical self-healing: auto-restore to `main` after a bounded idle window

### The gap

Decision 1 above ("day rollover: always cut `draft/<today>` from `main`") assumes every session reliably completes that ordinary rollover. In practice, two concurrent sessions can still collide on the canonical's single shared HEAD — nothing serializes them. [dev-env#911](https://github.com/brownm09/dev-env/issues/911) documents two live incidents (2026-07-25 and 2026-07-26) with an identical shape: a session correctly cuts `draft/<today>` from `main`, commits its first stub, and within 9-11 seconds a *different*, concurrent session checks the canonical out to an unrelated, much older `draft/<D>` branch — almost certainly to commit an orphaned open-PR shard deletion per this ADR's own decision 3 ("yours to commit immediately... whether or not it writes a stub"). The loser of that race leaves the canonical stranded on the old branch. One incident recovered in ~6 minutes (a concurrent session's own `git stash push` intervened); the other left the canonical stranded for **~32 hours**, blocking every session's documented "first session of the day" `git checkout main` step in the meantime, and very nearly cost the fresh `draft/2026-07-25` branch's own ref (recovered from the reflog only because it stayed reachable).

Root cause is architectural, not a bug in any one hook: the Stub file workflow requires every session to operate directly on one shared canonical checkout via `-C`, by design (this is what lets concurrent sessions reach the same checkout instead of racing for worktrees). Decision 2's date-mismatch detector already gave the canonical-on-a-stale-branch *state* a name and a manual repair procedure, but nothing bounded how long that state could silently persist before a human happened to notice.

A real fix — a coordination lock serializing canonical-branch hops — was considered and explicitly deferred: it is a new primitive with its own failure modes (stale locks on crash, contention, retry semantics) that deserves its own design pass, not a rider on this amendment. This amendment instead bounds the **blast radius** of the un-fixed collision: not preventing it, but ensuring it cannot silently persist for hours.

### The fix

A fifth check in `new-day-journal-check.py` (the same `UserPromptSubmit` hook Decision 2 already extended for the date-mismatch detector): `stale_canonical_recovery_message()`. It fires only when **all** of the following hold, checked in this order:

1. The canonical's current branch is `draft/<D>` and `D` is not today (reusing `branch_date()`, so a suffixed form like `draft/<D>-recovery` is judged by the same semantics Decision 2 already established).
2. The **entire** working tree is clean — not just `sessions/`, unlike the sessions/-scoped `canonical_is_dirty()` this hook already uses for the day-rollover CAUTION text. A real `git checkout` can silently carry uncommitted changes on any tracked file across branches when they don't conflict with the target, so a narrower gate would miss exactly the concurrent-session collision this check exists to bound.
3. HEAD hasn't moved — a checkout **or** a commit, whichever is more recent — in at least `STALE_CANONICAL_IDLE_MINUTES` (15) minutes. 15 minutes is chosen with real headroom: the observed legitimate hops (checkout the old branch, commit, checkout away) took 9 seconds to 6 minutes; the incident this check exists to bound left the canonical stranded ~32 hours.

Only when all three hold does the check perform an actual `git checkout main` — the one mutating action in this otherwise entirely advisory hook. Immediately before that checkout, a final re-read of the current branch and dirty state narrows the residual TOCTOU window (mirroring `journal-canonical-guard.py`'s identical precaution for its own auto-checkout of this same shared canonical): if either changed since the first read, the check silently stands down rather than acting on stale information. A failed or errored checkout is caught and reported inline, never raised — this hook's existing fail-open contract (exit 0 always) applies to check 5 exactly as it does to checks 1-4; on a genuine failure it also re-reads the current branch once more before reporting a manual-fix advisory, so a concurrent process (e.g. `journal-canonical-guard.py`, which auto-checkouts this same canonical for a different reason) resolving the situation a moment later doesn't produce a misleading "still broken" message.

The restore target is always `main`, never a specific `draft/*` branch — `main` is always safe to be on, and any subsequent session's ordinary "first session of the day" procedure moves it to `draft/<today>` from there, per Decision 1.

Check 5 shares check 4's sentinel (rather than a third of its own) and runs in Claude-managed worktree sessions exactly like check 4 does, for the identical reason: worktree sessions write stubs into the canonical via `git -C` just like any other session, so they are exactly who benefits from the auto-recovery. Check 5 runs first in `main()`'s check order specifically so that when it fires, check 4's own subsequent (deliberately unmemoized) read of the current branch naturally observes the post-restore state and stays silent, rather than immediately reporting a stale-branch warning about a problem check 5 just fixed.

### Implementation constraint the review of PR #912 pinned down

Mirroring how this ADR's own PR #873 review findings are recorded in-line above rather than as commit trivia, one finding from `/review`ing PR #912 is load-bearing enough to record as part of the decision:

**Idle time must be measured from HEAD's own last movement, never from the stale branch's tip-commit time.** The first implementation of condition 3 read `git log -1 --format=%ct <branch>` — the branch's own last commit. That signal is backwards for exactly the collision this check exists to bound: a branch this check considers "stale" has, by construction, an already-old tip commit, so the instant *any* session checks it out to do legitimate work (the ADR-119 decision-3 shard-deletion hop named above), idle time reads as already past threshold — giving that in-flight checkout **zero** of the headroom `STALE_CANONICAL_IDLE_MINUTES` is meant to provide, and exposing it to exactly the kind of yank-back-to-`main`-mid-work this whole amendment exists to prevent, from any *other* concurrent session's hook firing moments later. The fix reads HEAD's reflog instead (`git log -g -1 --date=unix --format=%gd HEAD`, parsing the embedded epoch): a reflog entry is written on both a checkout and a commit, so its most recent entry is exactly "the last time anyone did anything with this checkout," correctly resetting the idle clock at the moment of a fresh legitimate checkout. Verified empirically during review that `%gd` with `--date=unix` carries the reflog operation's own wall-clock timestamp, while `%ct` on the identical `-g` walk still reports the (possibly long-past) commit's own author/committer date — the two are not interchangeable despite both being drawn from the same reflog walk. Pinned by a dedicated regression test (`test_stale_recovery_noop_when_just_checked_out`) that checks out a branch with a deliberately ancient tip commit and asserts no restore happens.

### Why this is an amendment, not a new ADR

Same file, same hook, same shared canonical, same underlying hazard class this ADR's decision 1 and 2 already govern (the canonical stranded on a non-today `draft/*` branch) — only a new, bounded, self-healing response layered on top of the detection this ADR already introduced. This mirrors [ADR-071 Amendment 1](071-canonical-checkout-mutate-guard-hook.md)'s own justification for extending an already-shipped hook's coverage rather than re-litigating the original decision.

### Consequences (amendment)

**Positive.** A collision that used to require a human to notice a stalled canonical (observed: up to ~32 hours) now self-heals within `STALE_CANONICAL_IDLE_MINUTES` of HEAD's last movement, with no coordination primitive required. Checks 4 and 5 together mean the canonical is never silently stranded for long: check 5 fixes the common case automatically, and check 4 keeps reporting whenever check 5's stricter gate (clean + sufficiently idle) does not apply — most importantly, a genuinely dirty stale branch, which is never auto-touched and instead surfaces via check 4's existing advisory exactly as before this amendment.

**Negative / accepted.** The collision itself is still possible — this amendment bounds it, it does not prevent it (that remains a candidate for a future coordination-lock design, deliberately out of scope here). Idle-since-HEAD-last-moved is still an imperfect proxy for "is anyone actively using this branch right now" — it resets on checkout and commit, but not on e.g. a long read-only investigation of the branch with no git-observable action in between — so a session that checks out a stale branch and takes longer than 15 minutes to do anything git-visible is, in principle, still exposed to having that branch yanked back to `main` out from under it mid-work. Accepted because (a) the dirty-tree gate means no uncommitted work is ever lost even if this fires, at worst the session's next `git status`/`git branch` shows a surprising `main` instead of the branch it expected, and (b) the documented ADR-119 decision-3 workflow this risk is downstream of is "commit the deletion immediately," i.e. the legitimate window this could interrupt is meant to be seconds, not 15+ minutes, by the workflow's own design — and unlike the pre-fix implementation, that window now actually gets the full 15 minutes of headroom rather than none.

### References (amendment)

- [dev-env#911](https://github.com/brownm09/dev-env/issues/911) — the originating investigation (both 2026-07-25 and 2026-07-26 incidents, reflog forensics, root-cause analysis, and the three candidate fix directions this amendment picks from)
- [dev-env#912](https://github.com/brownm09/dev-env/pull/912) — implementing PR; its `/review` pass caught the tip-commit-vs-HEAD-reflog idle-signal defect recorded above
- `journal-canonical-guard.py` — the sibling hook whose final-re-check-before-mutating pattern this amendment's TOCTOU narrowing mirrors
