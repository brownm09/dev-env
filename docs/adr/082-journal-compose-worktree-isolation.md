# ADR-082: Journal-Compose Worktree Isolation, Shard Reconciliation, and Structure Assertion

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

**Concurrent, related work landing the same day (same incident cluster):**
[ADR-080](080-version-probed-merge-tree-conflict-detection.md) fixed Step 10.5's
conflict-detection grep, which had been a silent no-op on the installed git version and missed
the exact PR #150 conflict; the change below preserves that fix while relocating Step 10.5's git
invocations onto the compose worktree. A [REFERENCE.md runbook addition](../REFERENCE.md#git-workflow-runbooks)
(companion to [ADR-058](058-worktree-squatting-main-detection-correction.md)) documented that
`gh pr merge --delete-branch` fails whenever the branch being deleted is checked out — as a named
branch — in *any* worktree, not just the canonical; this directly informs the merge-command
change in Decision §1 below. **This ADR was originally drafted as ADR-080** (before #551 claimed
that number for the conflict-detection fix above, discovered before this PR opened) **and then as
ADR-081** (before a second, independent PR — [dev-env#561](https://github.com/brownm09/dev-env/pull/561),
`docs/adr/081-write-time-journal-shard-validation-hook.md`, unrelated write-time shard-validation
work from the same day's incident cluster — was confirmed to have already claimed 081 while both
PRs were still open); renumbered to 082 proactively upon discovering the second collision, rather
than waiting for `pre-merge-numbering-check.py` to catch it at merge time.

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
- Also accepts a full branch name (e.g. `draft/YYYY-MM-DD-recovery`) in place of a bare date, so
  the [draft-branch recovery runbook](../REFERENCE.md#engineering-journal-internals) can target a
  recovery branch explicitly instead of the plain (already merged/deleted) `draft/YYYY-MM-DD`.

All subsequent commits happen in the worktree; the push is
`git -C "$WT" push origin HEAD:refs/heads/$SOURCE_BRANCH` rather than a branch-relative push.
A rejected push means `origin/$SOURCE_BRANCH` advanced mid-compose (new stubs landed) — the
skill aborts and re-runs from Step 0.6 rather than rebasing over content it hasn't read, except
when the rejection is the pre-push hook's merged-draft-branch block (blocks pushing to a
`draft/YYYY-MM-DD` that already has a merged PR, except same-day) — that case routes to the
existing Step 10.5 `compose/YYYY-MM-DD` recovery branch instead, since the hook's pattern only
matches undecorated `draft/YYYY-MM-DD`.

**Merge step avoids `--delete-branch`.** Per the REFERENCE.md runbook noted above, `gh pr merge
--delete-branch`'s local-branch-delete step fails outright if the branch being deleted is checked
out — as a *named* branch — anywhere in the repo. The compose worktree itself is detached and
never at risk, but `$SOURCE_BRANCH` can still be checked out as a named branch in a
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
follow-up per the "truly unrelated errors go in a separate PR" default in `claude/CLAUDE.md`. The
same gap exists independently in `claude/scripts/journal-compose-with-retry.sh`'s Windows Task
Scheduler invocation (a bare `/journal-compose` with no date), so both share the same follow-up.

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
  a follow-up, not this change. **Resolved — see Addendum (2026-07-05) below; both floated fixes
  turned out to have a regression risk neither this ADR nor the original follow-up anticipated.**
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
- **Extend ADR-071's guard to parse into `git -C` targets** — at the time of this ADR, rejected:
  ADR-071 deliberately scoped `git -C` redirects out as "deliberate, visible authorship" distinct from
  the silent default-cwd collision it exists to catch, and parsing into redirect targets would also
  block the journal workflow's own legitimate, deliberate cross-repo operations. **Superseded by
  [ADR-071 Amendment 2](071-canonical-checkout-mutate-guard-hook.md) (dev-env#576, 2026-07-05):** the
  guard now *does* resolve `-C`/`--git-dir`/`--work-tree` targets and applies the canonical-root check
  to them, after a real recurrence showed the "deliberate authorship" distinction doesn't prevent the
  collision. The concern raised here — blocking legitimate journal cross-repo ops — is preserved by a
  narrow, temporary carve-out exempting the engineering-journal checkout (`_REDIRECT_TARGET_ALLOWLIST`,
  pending the #346 journal→worktree migration). This ADR's compose-in-a-worktree isolation is
  orthogonal and unaffected.
- **Full clone per compose** — no added safety over a worktree (same shared-object-store
  correctness), strictly worse on disk and time.
- **Lock the canonical checkout** — cannot stop a different session's plain `git checkout`, which
  is the actual failure mode; a lock only helps if every session honors it, and the incident's
  colliding session had no reason to know one existed.

---

## Addendum (2026-07-05): `reconcile-open-prs.py` follow-up resolved

The Consequences section above flagged the canonical-checkout unlinks left permanently
uncommitted by this ADR, and named two candidate fixes: "skip when the canonical is on
`main`, or report-only." [dev-env#578](https://github.com/brownm09/dev-env/issues/578)
found both insufficient and shipped a third.

**Why the two originally-floated fixes don't work:**

- **Skip-when-on-`main`** doesn't address the common case — per the Stub file workflow,
  the canonical checkout sits on `draft/YYYY-MM-DD` for most of the working day, not
  `main`. Conditioning on branch state doesn't change the actual mechanism: nothing
  commits the unlink either way, whichever branch is checked out.
- **Report-only (never unlink)** regresses a dependency this ADR didn't account for:
  `claude/scripts/post-compact.py` reads `open-prs/<N>.json` shards **directly off the
  canonical checkout's disk** — no git, no network — to decide whether to remind Claude
  to `/review` an open PR. [ADR-018](018-reconcile-open-prs-hook.md) (the hook's founding
  ADR) names this as the hook's *original, primary rationale* for mutating the working
  tree at all. Stopping the unlink would silently leave an already-merged PR looking open
  to `post-compact.py`, within the same or a later same-day session.

**What shipped instead:** the unlink stays (still load-bearing for `post-compact.py`). The
hook's docstring was corrected — it no longer claims the deletion is "picked up by the next
stub commit," which stopped being true once ADR-056's per-file-pathspec discipline and this
ADR's worktree isolation, together, removed every path that used to sweep it in. Instead,
the hook now runs a scoped `git status --porcelain -- sessions` after its unlink pass and
surfaces any currently-dirty `sessions/*/open-prs*` paths — this session's own fresh
unlinks, or a prior session's never-committed ones — in its existing `systemMessage`. This
restores [ADR-018](018-reconcile-open-prs-hook.md)'s original "picked up by the next
commit" guarantee, adapted to ADR-056's sharded shape: Claude gets an explicit, ready-to-use
path list for its next stub commit's pathspec, rather than relying on the now-defunct
assumption that *something* would blanket-add it.

A hook-side commit (this ADR's rejected option (b), above) remains the wrong direction —
it would reintroduce exactly the canonical-checkout-mutation risk this ADR eliminates, for
a purely cosmetic (`git status` noise) gain.

See [dev-env#578](https://github.com/brownm09/dev-env/issues/578) and
`claude/scripts/reconcile-open-prs.py`'s module docstring for the full causal chain
(ADR-018 → ADR-056 → this ADR → dev-env#578).

---

## Addendum (2026-07-23): the compose lock is project-scoped — a peer's lock never gates a subagent

Decision §1 above states the lock rule in **worktree** terms: "a lock file inside it younger than
10 minutes means another compose is genuinely active (abort)." That is correct for **Step 0.6**,
which runs *before* `worktree add` and is therefore asking "is another *invocation* using this
worktree?" It is wrong for **Step 1**, whose lock is per project — and it was read as a
worktree-wide rule by a multi-project composer subagent.

**2026-07-21 incident** (a 5-project compose run while remediating
[dev-env#874](https://github.com/brownm09/dev-env/issues/874)): the `dev-env` composer subagent
refused to compose, reporting *"a concurrent compose for 2026-07-21 is currently active
(lifting-logbook lock age: 20s)"*. That lock had been written seconds earlier by a **peer subagent
in the same run** — precisely the expected signal that the fan-out was working. Recovery cost a
full re-dispatch of the subagent carrying a hand-written correction.

Root cause: Phase 1's subagent prompt template delegated the whole procedure in one line —
"Follow the lock check/create procedure in SKILL.md Step 1" — and Step 1 is written for the
single-project flow, where the only lock that can exist *is* yours. Nothing in either place said
that locks outside your own project directory are irrelevant.

**The invariant, now stated in both copies:**

> Every `.draft-compose.lock` inside a run's compose worktree belongs to **that run**.

It follows from two facts that were already true before this fix — neither is newly asserted by
it. The first is established by this ADR: Step 0.6 creates the worktree fresh from
`refs/remotes/origin/<branch>`, aborting if any other invocation still holds a pre-existing one.
The second is established by the skill, not by this ADR — SKILL.md Step 9's "Lock file hygiene"
rule makes `.draft-compose.lock` ephemeral and never committed, verified live rather than taken
on the prose's word (`git -C …/engineering-journal ls-files --error-unmatch
'*.draft-compose.lock'` exits 1; that repo's `.gitignore` holds only `.claude/worktrees/`, so the
lock is untracked-but-not-ignored). Together they mean a newly created compose worktree starts
with zero lock files. A subagent's Step 1 is therefore a single-path check:

- a lock under **another** project = a peer subagent in this run — never a reason to stop, warn,
  or wait; do not glob `sessions/*/.draft-compose.lock`;
- a lock under its **own** project = its re-spawned predecessor in this run (Phase 2 re-spawns a
  failed subagent once) — take it over and report `LOCK_TAKEOVER=<age>s`, never abort.

That second case was a **latent trap on the documented recovery path itself**, found while tracing
the first: a re-spawned subagent would find its dead predecessor's under-600s lock and abort as
`LOCK_ACTIVE`, so the one remedy Phase 2 prescribes for a failed subagent could not run. Both
halves share the one root cause and are fixed by the one invariant.

**Step 0.6's cross-project glob is deliberately not narrowed.** It answers a different question at
a moment when any lock it sees necessarily belongs to a different invocation; narrowing it to
match Step 1 would delete the only genuine concurrent-compose guard. Both edited sites say so
explicitly, so the next reader doesn't "fix" it.

**Where the rule lives.** The full invariant is inline in the Phase 1 template — the copy a
subagent actually has in context, and whose one-line delegation is what failed — with a scoped
note plus a `<!-- keep in sync -->` marker in Step 1 for readers of the single-project flow.
Restating it only behind the delegation would have reproduced the original defect.

---

## Addendum (2026-10-02): meta triggers are composed in the same run — the skill never asks about them

Step 2b ("Check for meta-relevant content") predates both multi-project mode and per-session stubs: it
scans session blocks for meta-journal triggers, then **asks the user** whether to open a meta draft block.
Two issues described it from different sides — [dev-env#52](https://github.com/brownm09/dev-env/issues/52)
(nobody can answer in a scheduled run) and [dev-env#892](https://github.com/brownm09/dev-env/issues/892) (the
"yes" branch is broken even when somebody can) — and they need one design, because answering the prompt
automatically would not have produced an entry.

**The defects, as found.**

- **Nobody to ask.** The nightly `daily-journal-compose-local` run is a scheduled task whose first message says
  no user is present, and the skill has no other signal: no flag, no environment variable, `$ARGUMENTS` is a
  bare date, and neither launcher passes anything else. The coordinators reasoned their way to "unattended" from
  the harness preamble and declined — the compose that merged engineering-journal
  [#272](https://github.com/brownm09/engineering-journal/pull/272) ("Since this is an unattended scheduled run,
  I'm deferring the optional meta-journal entry") and the one that merged
  [#273](https://github.com/brownm09/engineering-journal/pull/273) (four trigger types detected in
  career-playbook's sessions, none written). The newest meta journal before 2026-10-02 was dated 2026-08-31.
  The user then had the 2026-10-01 entry written by hand
  ([#274](https://github.com/brownm09/engineering-journal/pull/274)) and decided: "In the future, please do the
  meta journal entries."
- **The "yes" branch orphans its output.** It created `sessions/meta/YYYY-MM-DD_draft.md`, a legacy monolithic
  draft. Step 1 reads a legacy draft only when *no* stubs are found, so nothing composes it — whether
  `sessions/meta/` was among the composed projects (2026-07-21, #892: meta's stubs were already consumed) or not
  (2026-10-01: only career-playbook and dev-env were composed, so the file would have reached `main`
  uncomposed). That is the debt class [ADR-119](119-day-rollover-draft-branch-and-orphaned-shard-deletions.md)
  exists to eliminate; `journal-stop-check.py` and `new-day-journal-check.py` flag such a file as stale and
  `merge-stale-pr.sh` deletes it.
- **Order and report format.** Phase 1 subagents report `META_TRIGGERS` after Step 1 has fixed the composed
  set, as free text (observed: `CLAUDE.md-modified (dev-env#1120: …)`,
  `cross_project_convention, workflow_failure, platform_constraint`), and a re-spawned subagent's report
  *replaces* the first one's (the 2026-09-30 compose, #272: three trigger types, then `none`).
- **Write path.** The harness refuses the coordinator's Write/Edit into the isolated compose worktree
  ([dev-env#1119](https://github.com/brownm09/dev-env/issues/1119)), and
  [ADR-129](129-journal-shell-write-guard.md)'s guard blocks the Bash alternatives for stub and manifest paths.

**Decision.**

1. **No prompt, in any mode.** Step 2b records triggers and moves on; it creates no file. There is no
   `--unattended` flag: the skill cannot tell the modes apart reliably, a missing flag would silently restore
   the bug, and [dev-env#631](https://github.com/brownm09/dev-env/issues/631) is the standing lesson against
   letting the model infer a mode from the task's framing. The user's decision was unconditional. The cost is
   that an attended run loses its y/n; the PR reviewer can still drop or edit the meta journal.
2. **Derived stubs, composed in the same run.** After every non-meta journal exists, a new **Step 6.7** turns
   verified trigger records into compose-generated *derived stubs* (one per trigger category,
   `sessions/meta/YYYY-MM-DD_2359NN.stub.md` plus a manifest shard) in the compose worktree, then composes
   `sessions/meta/` from real and derived stubs together. Meta is therefore composed last, once, on every day:
   with real meta stubs or without, in the composed set or not, there is exactly one canonical meta journal per
   date and no edit to a finished document. Step 1's mode choice counts *non-meta* project directories, so
   meta's real stubs are always composed here.
3. **The coordinator composes meta, not a subagent.** The input is small and bounded; it depends on every other
   project's report, so it is serial regardless; the Haiku composers are the surface that returned fabricated
   `STRUCTURE=ok` and wrong token sections ([dev-env#971](https://github.com/brownm09/dev-env/issues/971),
   [#1090](https://github.com/brownm09/dev-env/issues/1090), and the 2026-10-01 compose); and the 2026-08-25
   meta entry was already "authored directly" in that run's meta pass.
4. **A script does the mechanical parts** — `claude/scripts/journal-compose-meta.py`, four subcommands.
   `stub` verifies each trigger record against the stub it cites (the `evidence` phrase must appear verbatim,
   after NFKC and whitespace normalization, on one line or two adjacent lines) and writes the derived stubs and
   manifest shards; `install` gates the coordinator's composed journal (the eleven required headings, and every
   derived `Source:` path cited) before copying it into the compose worktree; `abandon` removes derived files;
   `check-clean` fails if any stub, manifest or `_draft.md` for the date remains anywhere under `sessions/`. A
   Python process launched by Bash is the only writer that both the harness restriction and the ADR-129 guard
   allow, and it keeps inline-literal snippets out of the skill
   ([dev-env#1101](https://github.com/brownm09/dev-env/issues/1101)).
5. **Claims are verified, not trusted.** An unverifiable record is rejected by name
   (`META_TRIGGER_REJECTED …`), never silently included or dropped, and the rejection list goes in the PR body.
   Phase 2 unions the `META_TRIGGER=` lines across every attempt of a subagent.
6. **No file nothing composes, by construction.** Derived files are untracked, never pushed, and consumed by
   Step 9's existing deletion globs in the same run, so the draft branch never carries one and a crashed run
   cannot leave a stub the next run would compose as real; `check-clean` is the net. Step 2b no longer writes
   `_draft.md`; the Step 1 legacy read fallback stays for old days.
7. **Meta never blocks the project journals.** If the pass fails and only derived stubs are involved, the
   coordinator retries once, then `abandon`s; the PR body states `Meta journal: FAILED — …` with the triggers
   not captured and a pointer to
   [REFERENCE.md → Late meta entry recovery](../REFERENCE.md#late-meta-entry-recovery). If real meta stubs are
   present the compose stops, as for any project.

**Judgment calls.**

- **One derived stub per trigger category, not one stub or one per source session.** A stub is a session (the
  file boundary delimits it), and the hand-written 2026-08-25 and 2026-10-01 entries are organized by category;
  per-category stubs give "Session N" sections of that shape, with names (`2359NN`, NN the category's row in
  Step 2b's table) that are stable across re-runs and sort after real stubs. They carry no opening brief and no
  next-session-context, so they cannot displace a real stub's.
- **Never pushed, so the pre-compose commit holds no copy.** Old Step 2b pushed its draft; pushing a derived
  stub would let a crashed run leave one that the next run composes as real, duplicating the entry. Nothing is
  lost: the source stubs are in the pre-compose commit, and the composed journal cites each `Source:` path.
- **The gate checks tokens the pass itself generated, with no threshold.** The eleven heading regexes are the
  existing structural assertion; the `Source:` paths are extracted from the derived stubs, and a derived stub
  with no extractable `Source:` fails instead of passing vacuously. The fidelity ratio is reported but never
  gates: the skill's 80% and 50% figures are rough heuristics that have never been calibrated against derived
  input.
- **An exact-match evidence check can reject a real trigger whose quote was mis-copied.** That is loud and
  recoverable (PR body plus runbook); a fuzzy matcher would need a calibrated similarity cutoff and would let a
  fabricated claim through at the margin.
- **ADR-129's "sole method" claim is bounded, not broken.** See its Amendment 2: derived stubs are
  compose-internal and never session records.
- **Steps 7 and 8 are unchanged for meta.** Until #1119 is fixed, its script-file recipe applies to meta's
  READMEs exactly as it does to every other project's.

**Alternatives rejected.**

- *Fold the trigger content into an already-composed meta journal* — a structural edit of a finished
  eleven-section document (TOC, Key Decisions, Session N, token tables) after its composer reported done.
- *Carry triggers to the next day's stub, or a `sessions/meta/.pending-triggers.md` hand-off (#52 option 2)* —
  misses "same PR", adds a day of latency, and a dated stub or hand-off file on `main` is itself a file nothing
  composes.
- *An `--unattended` flag (#52 option 3)* — decision 1.
- *Push the derived stub to the draft branch first, as old Step 2b did* — see the judgment call above.
- *An extra Phase 1 Haiku subagent for meta (a second wave)* — decision 3; its writes into the compose
  worktree would also need the #1119 relay.
- *A pre-fan-out scout agent to find triggers before Phase 1* — re-reads every stub.

**Consequences.**

- A compose that detects meta triggers composes `sessions/meta/YYYY-MM-DD-<slug>.md` in the same run and PR,
  with no prompt, and the PR body carries a `Meta journal:` status line — the only surface an unattended run
  leaves.
- Real meta stubs are now composed by the coordinator instead of a Phase 1 subagent (a behavior change on days
  that have them).
- Step 10.5's replay pathspecs must name `sessions/meta/` whenever meta was composed; omitting it would silently
  drop the entry on the conflict-recovery path.
- **Testing.** `claude/scripts/tests/test_journal_compose_meta.py` (Testing item 100, 50 cases) replays a
  fixture day end to end — records, verified derived stubs and schema-valid manifest shards, gated
  install, simulated Step 9, `check-clean` — and carries the #892 regression (the old `_draft.md`
  shape fails the tree-wide check). Drift gates tie the skill's heading regexes, its trigger slugs (in
  Step 2b *and* the Phase 1 template's inline copy), the Step 6.7 / Step 10 wiring and both Step 10.5
  pathspec lists to the helper. Calibrated once against real corpora (recorded in `docs/TESTING.md`):
  the heading check passes the four real meta journals and flags exactly the four headings missing from
  the #273 career-playbook journal; 17 of 17 known-bad mutations of the skill and routine are caught;
  and a dry run on the real 2026-10-01 day (7 records, 6 accepted, the fabricated one rejected by name)
  ended with only the composed meta journal in the worktree. The LLM steps themselves are not
  exercised offline.
- **Observability.** N/A in the hook sense (dev-env's `## Observability`): the helper reports `KEY=value` lines
  on stdout and errors on stderr.
- **Security.** N/A — no credentials or network; the helper validates every path and writes only inside the
  compose worktree's `sessions/` tree and the scratch directory.
- **Resilience.** A meta failure cannot block the project journals; a crashed run leaves nothing on the draft
  branch; re-running Step 0.6 regenerates everything.
- **Data integrity.** Derived manifest shards are validated against the five-field schema
  ([ADR-056](056-per-session-sharding-journal-companion-files.md)) before they are written; one meta journal
  per date.

---

## References

- `claude/skills/journal-compose/SKILL.md` — Step 0.6, 9.5, 6.5/6.6, and the Phase 2 coordinator
  gate
- `claude/routines/daily-journal-compose/SKILL.md` — companion edit
- [dev-env#467](https://github.com/brownm09/dev-env/issues/467) — motivating issue (both original
  gaps, plus the 2026-07-03 incident comment)
- [dev-env#578](https://github.com/brownm09/dev-env/issues/578) — Addendum (2026-07-05): resolves
  the `reconcile-open-prs.py` follow-up this ADR's Consequences section flagged
- [dev-env#889](https://github.com/brownm09/dev-env/issues/889) — Addendum (2026-07-23): the
  compose lock is project-scoped; a multi-project subagent aborted on a peer's lock
- [ADR-018](018-reconcile-open-prs-hook.md) — `reconcile-open-prs.py`'s founding ADR; the
  Addendum traces the causal chain from here through ADR-056 to this ADR
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
  conflict detection in Step 10.5, landed the same day; preserved as-is and relocated onto the
  compose worktree by this ADR's Decision §1
- [dev-env#561](https://github.com/brownm09/dev-env/pull/561) / `docs/adr/081-write-time-journal-shard-validation-hook.md` —
  the concurrent PR whose independent claim on ADR number 081 (same incident cluster, unrelated
  shard-validation work) is why this ADR is numbered 082
- [dev-env#52](https://github.com/brownm09/dev-env/issues/52) and
  [dev-env#892](https://github.com/brownm09/dev-env/issues/892) — Addendum (2026-10-02): meta triggers are
  composed in the same run; the skill never asks, and never writes `YYYY-MM-DD_draft.md`
- [dev-env#1119](https://github.com/brownm09/dev-env/issues/1119),
  [#1101](https://github.com/brownm09/dev-env/issues/1101),
  [#971](https://github.com/brownm09/dev-env/issues/971),
  [#1090](https://github.com/brownm09/dev-env/issues/1090) — related open issues the Addendum (2026-10-02)
  designs around and deliberately does not absorb
- [ADR-129](129-journal-shell-write-guard.md) Amendment 2 — the one script-written exception to its
  Write-tool-only rule (derived stubs)
- engineering-journal [#272](https://github.com/brownm09/engineering-journal/pull/272),
  [#273](https://github.com/brownm09/engineering-journal/pull/273),
  [#274](https://github.com/brownm09/engineering-journal/pull/274) — the composes that dropped the triggers, and
  the hand-written 2026-10-01 meta entry that prompted the decision
