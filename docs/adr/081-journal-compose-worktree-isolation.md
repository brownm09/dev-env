# ADR-081: Journal-Compose Worktree Isolation, Shard Reconciliation, and Structure Assertion

**Date:** 2026-07-04
**Status:** Accepted
**Tags:** journal, composition, skill, worktrees, concurrency, canonical-checkout, detached-head, open-prs, structure-check, routines

---

## Context

The `/journal-compose` skill (`claude/skills/journal-compose/SKILL.md`) runs every operation —
stub discovery, validators, subagent output, README edits, deletions, commit/push/PR — directly
against the **shared canonical checkout** `C:/Users/brown/Git/engineering-journal`. That checkout
is heavily concurrent: `git worktree list` there routinely shows 40+ registered worktrees, many
from unrelated sessions active at the same time.

**2026-07-03 incident (compose of 2026-07-02 → engineering-journal
[PR #150](https://github.com/brownm09/engineering-journal/pull/150)):** a concurrent session ran
`checkout main && pull` in the canonical checkout mid-compose. All four parallel composer
subagents read a tree that no longer had the draft branch's 44 stubs: two aborted on missing
files, one composed a wrong journal from 2 stray stubs visible on `main`, one raced its reads
ahead of the switch but wrote its output into the now-wrong-branch tree. Recovery was manual:
`git worktree add .claude/worktrees/<name> draft/2026-07-02`, re-point every composer at
worktree paths, rerun Steps 7–11 from there.

This is the exact failure class **[dev-env#467](https://github.com/brownm09/dev-env/issues/467)**
already tracked, filed after a milder 2026-07-01 backfill collision (an in-progress stub blocked a
branch checkout; an unrelated shard deletion appeared/disappeared mid-session). [ADR-071](071-canonical-checkout-mutate-guard-hook.md)'s
`pre-tool-use-canonical-mutate-guard.py` — the hook that blocks git-mutating Bash commands in a
canonical checkout — deliberately exempts `git -C <path>` redirects ("deliberate, visible
authorship", ADR-071 Judgment calls) and is scoped to the *invoking* session's own commands; it
cannot and was never meant to stop a *different* session's plain `git checkout` in the same shared
repo, which is exactly what happened here.

Two adjacent defects surfaced from the same incident cluster, confirmed against real evidence in
engineering-journal PRs #147, #149, and #150:

- **Compose PRs wrote their own open-PR shard.** The generic `pr-merge-reminder.py` hook fires its
  standard "write the journal stub AND open-PR shard" advice on *any* `gh pr create` — including
  the compose PR's own — because it has no way to know that particular create call is itself a
  journal-compose operation. Compose sessions complied: `sessions/meta/open-prs/147.json` and
  `149.json` were each added by their own PR's commit and auto-merged straight onto `main`,
  immediately stale. Both sat there until PR #150's compose swept them up by hand.
- **Nothing asserted the canonical 11-section structure.** A composer subagent (discovered during
  the #467 backfill) emitted its own ad hoc "Overview/Context/Decision/..." structure with no
  `## Next Session Context` section. Step 6.5's self-check only verifies line-count fidelity, not
  heading conformance.

**Concurrent, related work while this ADR was being written (both merged 2026-07-04, same
incident cluster):** [ADR-080](080-version-probed-merge-tree-conflict-detection.md) fixed Step
10.5's conflict-detection grep, which had been a silent no-op on the installed git version and
missed the exact PR #150 conflict; the change below preserves that fix while relocating Step
10.5's git invocations onto the compose worktree. A [REFERENCE.md runbook addition](../REFERENCE.md#git-workflow-runbooks)
(companion to [ADR-058](058-worktree-squatting-main-detection-correction.md)) documented that
`gh pr merge --delete-branch` fails whenever the branch being deleted is checked out — as a named
branch — in *any* worktree, not just the canonical; this directly informs the merge-command
change in Decision §1 below.

---

## Decision

### 1. Isolated compose worktree

Every compose operation — stub/manifest/open-PR reads, validators, subagent output, README
edits, deletions, and the final commit/push — moves into a dedicated, disposable, **detached**
worktree: `C:/Users/brown/Git/engineering-journal/.claude/worktrees/compose-YYYY-MM-DD`. The
canonical checkout's *working tree* is never branch-switched or written to — the only touches
against the canonical are read-only `fetch`/`show-ref`/`ls-remote`/`ls-tree` queries,
`worktree add`/`worktree remove` registration calls, and a post-merge `branch -D` of the local
`draft`/`compose` ref (shared ref-namespace cleanup, not a working-tree change; git itself
refuses the delete — a no-op, not an error, since the caller no-ops on failure — if that branch
is still checked out as a named branch in some other worktree, e.g. a stub-writing session's).

A new **Step 0.6** ("Resolve compose date and create the isolated worktree") runs before the
existing Step 0.7/0.8 validators (which must validate the worktree's files, so the worktree must
exist first). It:
- Resolves the compose date: explicit argument → canonical's current branch if it already matches
  `draft/YYYY-MM-DD` (legacy compatibility, read-only) → a filtered `ls-remote --heads origin`
  scan for exactly one `draft/YYYY-MM-DD`-shaped branch, otherwise listing candidates and asking.
- Enforces the pre-existing today-guard (`--force` required for same-day compose, unchanged from
  [ADR-017](017-journal-compose-today-guard.md)).
- Fetches, verifies `origin/draft/YYYY-MM-DD` exists, and runs a **divergence guard**: if a local
  `draft/YYYY-MM-DD` ref exists and is not an ancestor of the origin tip, abort rather than
  silently compose an incomplete set (worktrees share refs, so this sees unpushed commits
  regardless of which checkout holds them).
- Treats a pre-existing `compose-YYYY-MM-DD` worktree as a concurrency signal: a lock file inside
  it younger than 10 minutes means another compose is genuinely active (abort); otherwise the
  worktree is stale (a crashed prior run) and is removed and recreated — always safe, since the
  worktree is fully regenerable from `origin/draft/*`.
- Creates the worktree **detached** at `refs/remotes/origin/draft/YYYY-MM-DD` — specifically
  because the draft branch may already be checked out, as a *named* branch, by a stub-writing
  session's own worktree (observed in practice during this incident's investigation). A detached
  checkout never contends for a branch ref, which also matters at merge time (see below).

All subsequent commits happen in the worktree; the push is
`git -C "$WT" push origin HEAD:refs/heads/draft/YYYY-MM-DD` rather than a branch-relative push.
A rejected push means `origin/draft` advanced mid-compose (new stubs landed) — the skill aborts
and re-runs from Step 0.6 rather than rebasing over content it hasn't read, except when the
rejection is the pre-push hook's merged-draft-branch block (blocks pushing to a `draft/YYYY-MM-DD`
that already has a merged PR, except same-day) — that case routes to the existing Step 10.5
`compose/YYYY-MM-DD` recovery branch instead, since the hook's pattern only matches `draft/*`.

**Merge step avoids `--delete-branch`.** Per the REFERENCE.md runbook noted above, `gh pr merge
--delete-branch`'s local-branch-delete step fails outright if the branch being deleted is checked
out — as a *named* branch — anywhere in the repo. The compose worktree itself is detached and
never at risk, but `draft/YYYY-MM-DD` can still be checked out as a named branch in a
*stub-writing* session's worktree (per `claude/CLAUDE.md`'s stub workflow, which does
`checkout -b draft/YYYY-MM-DD`) at the exact moment compose tries to merge. Step 11 therefore
splits the merge into two calls — `gh pr merge <N> --squash` (server-side only, always succeeds)
followed by `gh api -X DELETE repos/brownm09/engineering-journal/git/refs/heads/<branch>` (a pure
REST ref delete, independent of any local checkout state) — rather than depending on
`--delete-branch`'s local-delete step never colliding with a live stub session.

Step 11 removes the compose worktree only after confirming the PR merged, and only then deletes
local branches — a branch checked out in a worktree cannot be deleted first.

### 2. Deliberate open-PR shard reconciliation

**New Step 9.5**, after stub deletion and before commit, replaces a sweep that worktree isolation
removes: `reconcile-open-prs.py` (a `UserPromptSubmit` hook) already unlinks merged-PR shards in
the canonical working tree, but — by its own docstring — never commits, leaving them "dirty for
the next stub commit" to pick up. Compose's old `git add -u sessions/` against the canonical
opportunistically committed those unlinks; an isolated compose never touches the canonical's
working tree, so that pickup stops happening. Step 9.5 replaces it deliberately: for every
`open-prs/<N>.json` shard in the worktree, look up its PR's state via `gh pr view`, and `git rm`
it if `MERGED` or `CLOSED` — continuing the exact verification precedent PR #150's body already
set ("...all verified merged via gh before deletion").

The compose PR itself never gets a shard: Step 11 explicitly disregards `pr-merge-reminder.py`'s
generic post-create advice for this one case, since the PR opens and merges in the same session
(same-session net-zero). If the merge fails and the PR is left open, the shard is written then —
the PR genuinely now spans sessions — and the worktree is left in place until it resolves. A
post-merge check (`git -C "$EJ" fetch origin main && ls-tree | grep open-prs/<N>.json`) surfaces
a leak without ever mutating the canonical to fix it.

### 3. Structural assertion

A grep-anchored check against the canonical 11 section headings (Header, TOC, Opening Brief, Key
Decisions, Dialogue, Open Items / Next Steps, Token Usage, Token Optimization Suggestions, Next
Session Context, Reflection, Further Reading) runs in the existing single-project Step 6.5 and a
new subagent-template Step 6.6, reporting `STRUCTURE=ok|missing:<list>`. The Phase 2 coordinator
now treats `STRUCTURE != ok` the same as `STATUS != done`: a failed subagent, blocking all
README/git work until it's fixed.

### Companion: `daily-journal-compose` routine

Drops its Step 0 `sync-routine-worktree` call against the canonical engineering-journal
checkout — under worktree-isolated compose it's unnecessary, and it was itself a canonical-mutator
of this incident's exact class (it rebases the canonical if on a draft branch, hard-resets it if
`claude/*`). Replaced with a plain read-only fetch. Stub discovery, which globbed the canonical
working tree and therefore found nothing once the canonical permanently rests on `main`, is
switched to a remote `ls-tree` scan. The per-project sequential loop collapses to one
`/journal-compose ${DATE}` call, since the skill's own multi-project mode already fans out per
project.

---

## Judgment calls

### Never creates a shard for the compose PR, rather than "create then delete"

The original issue suggested a post-merge deletion step for the compose PR's own shard. Deleting
something immediately after creating it is strictly worse than never creating it: it leaves a
window (however small) where a stale shard exists on `main`, and it requires the deletion step to
never be forgotten — which is exactly how PR #147's and #149's shards went stale in the first
place (the create side was reliable; nothing reliably did the delete). Not creating the shard at
all removes the failure mode instead of adding a second step that has to succeed to cancel the
first.

### Detached HEAD, not a named branch, for the compose worktree

A named branch would need a name distinct from `draft/YYYY-MM-DD` (already potentially held by a
stub-writing session's worktree) and would need its own cleanup path. Detached HEAD sidesteps
branch-ownership entirely — the worktree exists only to hold a working tree and an index; the
branch ref it will eventually update is `origin/draft/YYYY-MM-DD`, touched only at push time. It
also means the compose worktree itself can never be the thing blocking `gh pr merge`'s branch
deletion — see the two-call merge decision above, adopted because a *different* worktree (a stub
session's) still can be.

### Prune-safety comes from the branch-prefix skip, not the liveness guard

`prune-merged-worktrees.py` skips any worktree whose branch does not start with `claude/` unless
`--include-named` is passed ([ADR-078](078-opt-in-named-branch-worktree-pruning.md)); a detached
worktree has no branch at all, so it is skipped by this guard unconditionally. This is the actual
safety mechanism — **not** the 24-hour transcript-liveness guard
([ADR-051](051-worktree-liveness-guard.md)), which keys off a session's own working directory
having a live transcript. No session's cwd ever points at `compose-YYYY-MM-DD` (the skill drives
it entirely via `git -C` and absolute paths from wherever it was invoked), so the liveness
mechanism cannot see this worktree at all. Documenting the real mechanism here matters because a
future change to the prune tooling's liveness logic could otherwise assume it protects every
recently-created worktree, which it does not.

### Reconciliation stays a verification net, not a canonical mutation

Both the shard-reconciliation step and the post-merge leak check operate read-only against the
canonical (`fetch`, `ls-tree`) or write-only inside the worktree (`git -C "$WT" rm`). If a shard
leak is ever detected on `origin/main` post-merge, the skill surfaces a warning rather than
committing a fix directly to the canonical — consistent with this ADR's whole premise that
compose must never write to the canonical checkout.

### Routine's `DATE`/`--force` mismatch is explicitly out of scope

The routine computes `DATE=$(date -u ...)` and never passes `--force`; since the skill's
today-guard refuses any same-day compose without it, the automated 7am run has likely never
successfully composed anything — a real, pre-existing defect. Fixing it requires a product
decision (compose *yesterday* at 7am instead? always pass `--force`, permanently overriding the
today-guard's purpose for every automated run?) orthogonal to concurrency hardening. Left as a
follow-up per the "truly unrelated errors go in a separate PR" default in `claude/CLAUDE.md`.

---

## Consequences

- Compose never branch-switches or writes to the canonical checkout's *working tree* — only
  read-only queries, worktree registration calls, and a shared-ref-namespace branch cleanup
  (which git itself no-ops if the branch is checked out elsewhere) touch it, closing the exact
  gap ADR-071 explicitly couldn't (a different session's plain `git checkout` in the same
  shared repo).
- Fully restart-safe: the compose worktree is disposable and regenerable from
  `origin/draft/YYYY-MM-DD` at any point; a crashed compose is cleaned up by the next invocation's
  Step 0.6, not by manual intervention.
- `reconcile-open-prs.py`'s canonical-checkout unlinks still happen (unchanged, out of scope here)
  but nothing commits them anymore — they sit as uncommitted deletions until the canonical next
  pulls a `main` that already contains Step 9.5's equivalent deletions, at which point `git status`
  goes clean on its own. A hook revisit (skip when the canonical is on `main`, or report-only) is
  a follow-up, not this change.
- A prior-date re-compose (the #147-morning/#150-evening shape) can still hit the pre-push
  merged-draft-branch block; it now has an explicit, named recovery path (Step 10.5) rather than
  being a surprise.
- The Step 11 merge no longer risks the noisy `--delete-branch` local-checkout failure mode; the
  two-call pattern always succeeds server-side regardless of what any stub session's worktree
  currently holds.
- **Testing.** No `.py`/`.sh` files change — this is a skill-markdown and documentation change.
  Verification is a full occurrence-grep of the edited skill (every remaining canonical-checkout
  path must be on the read-only allowlist), a manual step-consistency walkthrough, and the next
  real end-of-day compose as the actual integration test, run under supervision.
- **Observability.** N/A in the hook/script sense — see dev-env's `## Observability` section;
  the skill's own step-by-step user-facing messages (worktree creation, reconciled-shard list,
  structure-check result) are its diagnostic surface.
- **Security.** N/A — no new credentials, secrets, or auth surface; `gh pr view`/`gh pr create`/
  `gh api` calls are the same class the skill already made.
- **Resilience.** Improves failure isolation: a merge failure now leaves a self-contained,
  prune-safe worktree behind (holding the open-PR shard) instead of leaving the canonical
  checkout in an ambiguous state.
- **Performance.** One additional `git worktree add`/`remove` pair and a handful of `gh pr view`
  calls (bounded by the number of currently-open shards) per compose — negligible next to the
  subagent compose cost itself.
- **Data integrity.** N/A — no schema or migration surface; shard/manifest JSON formats are
  unchanged (ADR-056).

---

## Alternatives rejected

- **Stash-by-pathspec runbook** (dev-env#467's original suggestion) — reactive rather than
  preventive, and still mutates the shared canonical checkout's index; only reduces collision
  probability, doesn't eliminate the class.
- **Extend ADR-071's guard to parse into `git -C` targets** — ADR-071 deliberately scopes `git -C`
  redirects out as "deliberate, visible authorship" distinct from the silent default-cwd collision
  it exists to catch; parsing into redirect targets would also block compose's own legitimate,
  deliberate cross-repo operations.
- **Full clone per compose** — no added safety over a worktree (same shared-object-store
  correctness), strictly worse on disk and time.
- **Lock the canonical checkout** — cannot stop a different session's plain `git checkout`, which
  is the actual failure mode; a lock only helps if every session honors it, and the incident's
  colliding session had no reason to know one existed.

---

## References

- `claude/skills/journal-compose/SKILL.md` — Step 0.6, 9.5, 6.5/6.6, and the Phase 2 coordinator
  gate
- `claude/routines/daily-journal-compose/SKILL.md` — companion edit
- [dev-env#467](https://github.com/brownm09/dev-env/issues/467) — motivating issue (both original
  gaps, plus the 2026-07-03 incident comment)
- engineering-journal [PR #147](https://github.com/brownm09/engineering-journal/pull/147),
  [PR #149](https://github.com/brownm09/engineering-journal/pull/149),
  [PR #150](https://github.com/brownm09/engineering-journal/pull/150) — shard-staleness and
  concurrent-branch-switch evidence
- [ADR-002](002-journal-compose-session-isolation.md) — journal-compose session isolation
- [ADR-013](013-sync-routine-worktree-skill.md) — sync-to-main as a reusable routine skill (the
  call this ADR's companion edit removes from the routine)
- [ADR-017](017-journal-compose-today-guard.md) — the today-guard Step 0.6 preserves unchanged
- [ADR-032](032-journal-start-here-dashboard.md) — start-here dashboard block (path-rewritten,
  behavior unchanged)
- [ADR-051](051-worktree-liveness-guard.md) — worktree liveness guard (confirmed *not* the
  mechanism protecting the compose worktree from pruning)
- [ADR-056](056-per-session-sharding-journal-companion-files.md) — manifest/open-PR shard schemas
  (unchanged by this ADR)
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — worktree-squat detection; its
  companion REFERENCE.md runbook motivates this ADR's two-call merge decision
- [ADR-066](066-worktree-session-safety-rules.md) — worktree session safety rules
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — canonical-mutate guard hook; explains
  why it couldn't have prevented this incident
- [ADR-075](075-ephemeral-diff-worktree-pruning.md) — ephemeral-diff worktree pruning signal
- [ADR-078](078-opt-in-named-branch-worktree-pruning.md) — `--include-named` worktree pruning;
  confirms the branch-prefix skip that keeps the detached compose worktree prune-safe
- [ADR-080](080-version-probed-merge-tree-conflict-detection.md) — version-probed `merge-tree`
  conflict detection in Step 10.5, landed concurrently; preserved as-is and relocated onto the
  compose worktree by this ADR's Decision §1
