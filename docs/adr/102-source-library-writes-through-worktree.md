# ADR-102: Route Source-Library Writes Through a Dedicated Worktree, Not the Canonical Checkout

**Date:** 2026-07-10
**Status:** Accepted
**Tags:** research, journal, worktrees, canonical-checkout, skill, sources-md, dev-env-sync

---

## Context

`~/.claude/skills/` is a directory junction onto `C:/Users/brown/Git/dev-env/claude/skills/`
(dev-env's own `CLAUDE.md` → Dev-Env Architecture). Two skills append new citations to the
shared source library at `~/.claude/skills/sources.md` when their Pass-1 grep cache-misses and a
subagent finds a high-quality new source:

- `claude/skills/research/SKILL.md` — the interactive `/research` command, invoked from any
  project's session.
- `claude/skills/journal-compose/SKILL.md` Section 11 — an inlined "grep, then research on
  cache-miss" pass over each day's Key Decisions, invoked automatically every morning by the
  fully-autonomous `daily-journal-compose` scheduled routine (`0 7 * * *`, "run fully
  autonomously — do not ask the user anything").

Both wrote directly to `~/.claude/skills/sources.md` with a plain `Edit`/`Write` call. Because
that path is a junction, the write always lands in the **canonical dev-env checkout's working
tree** (`C:/Users/brown/Git/dev-env`) — regardless of which project the invoking session is
actually working in, and regardless of whether that session is a dev-env session at all. Neither
skill then committed the write. The canonical checkout going dirty blocks
`dev-env-sync.py`'s `git pull --ff-only` for **every session on the machine** until someone
notices and manually commits or discards the change (`claude/CLAUDE.md`'s own rule: "The
canonical dev-env worktree must stay on `main` at all times").

This happened twice:

1. [PR #649](https://github.com/brownm09/dev-env/pull/649) — landed "two pending source-library
   entries a background research subagent had left uncommitted in canonical," bundled
   opportunistically into an unrelated docs PR.
2. [dev-env#697](https://github.com/brownm09/dev-env/issues/697) — the identical pattern
   recurred, leaving the canonical checkout 21 commits / ~41 hours behind `origin/main` before
   being noticed — silencing every dev-env fix merged in that window, on this machine, for
   everyone. Investigation confirmed the pending content was legitimate, correctly-formatted
   `/research`-style output (four source-library entries), not junk.

Both incidents were framed around "a background research subagent," which fits `journal-compose`
Section 11's fully-unattended nightly invocation better than the interactive `/research` command
— a human is present for the latter and can typically notice and commit it themselves. Section 11
was not named in either incident's investigation, but a repo-wide grep for `sources.md` during
this fix confirmed it performs the identical write, with no worktree isolation of its own: `/journal-compose`
isolates all of *its* mutations into a dedicated engineering-journal worktree
([ADR-082](082-journal-compose-worktree-isolation.md)), but that isolation covers only the
engineering-journal repo — dev-env is a different repo entirely, so Section 11's `sources.md`
write was never inside it.

**Neither existing hook prevents this.** `pre-tool-use-canonical-mutate-guard.py`
([ADR-071](071-canonical-checkout-mutate-guard-hook.md)) blocks mutating **git** commands issued
against a canonical checkout — a plain `Edit`/`Write` tool call is not a git command and is
outside its scope entirely. `pre-tool-use-worktree-path-check.py`
([ADR-024](024-worktree-path-guard-hook.md)) blocks `Write`/`Edit`/`NotebookEdit` calls that
escape a *live worktree* to the canonical root — but it only fires when the invoking session's
own `cwd` matches the worktree pattern. Neither `/research` (invoked from an arbitrary project,
usually not a dev-env worktree at all) nor `journal-compose` Section 11 (invoked from an
engineering-journal worktree, a different repo's worktree entirely) has a `cwd` this hook
recognizes as relevant to dev-env, so it never fires for either write path.

---

## Decision

### 1. A new, shared, reusable helper skill

`claude/skills/queue-source-library-entry/SKILL.md` — mirroring the existing
`sync-routine-worktree` pattern ([ADR-013](013-sync-routine-worktree-skill.md)): a small
`Parameters` / `Behavior` / `Return semantics` skill invoked by other skills via "read
`~/.claude/skills/<name>/SKILL.md` and execute its Behavior section," not by users directly.

Its job: append one entry to `claude/skills/sources.md` **inside a dedicated worktree** instead
of through the `~/.claude/skills/sources.md` junction, so the canonical checkout is never
touched.

**Fixed, shared locations** (every caller converges on the same place, so queued entries don't
scatter):
```
DEV_ENV_REPO   = C:/Users/brown/Git/dev-env
QUEUE_WORKTREE = C:/Users/brown/Git/dev-env/.claude/worktrees/research-sources-queue
QUEUE_BRANCH   = chore/research-sources-queue
```

**Behavior, summarized** (full steps in the skill file itself; hardened during `/review` — see
Review Hardening below):
1. Reuse the queue worktree only if it's genuinely live (`${QUEUE_WORKTREE}/.git` exists **and**
   `git rev-parse --show-toplevel` resolves it) — otherwise clear any remnant and, after
   `worktree prune` + a pruning fetch, create it: attaching to an existing local
   `chore/research-sources-queue` branch first (checked before the remote-tracking ref, since a
   `-b` against an already-existing local branch hard-fails), falling back to the remote branch,
   then to fresh from `origin/main`. A local branch already merged into `origin/main` (a prior
   sweep) is deleted first so creation starts fresh rather than resurrecting merged commits.
2. A second dedup pass greps the **queue worktree's own** `sources.md` (not just the canonical
   copy the invoking skill's Pass-1 grep already checked) — an entry already queued-but-unmerged
   by an earlier invocation is recognized and skipped rather than duplicated.
3. Edit `${QUEUE_WORKTREE}/claude/skills/sources.md` (the worktree's own absolute path) — never
   `~/.claude/skills/sources.md`. The documented Bash fallback (for the one case `Edit` is
   blocked) passes the untrusted, web-sourced `ENTRY_MARKDOWN`/`SECTION` values via environment
   variables into a fixed `node -e` script — never string-interpolated into the command.
4. `git -C "$QUEUE_WORKTREE" add ... && commit -- claude/skills/sources.md` inside the worktree,
   with an explicit pathspec (this worktree is shared across every caller). A commit failure from
   a concurrent invocation (`index.lock`, or a no-op "nothing to commit") retries the whole
   sequence once before reporting failure.
5. Verify the commit actually landed (`git log -1`) before declaring success — closes the gap
   where a silent no-op earlier would otherwise be reported as queued.
6. `git -C "$QUEUE_WORKTREE" push` — best-effort, for durability (a local-only commit on one
   machine is more fragile than one also visible on `origin`); failure here is not fatal to the
   invoking skill's primary task.
7. Return SUCCESS (verified) with a one-line summary the invoking skill relays to the user, or an
   explicit FAILURE the invoking skill must not silently swallow into a false "queued" claim.

Every git operation targets the worktree (or, for `worktree add`/`fetch`/`show-ref`/`prune`, the
canonical repo path) via `-C`, never a bare `cd` — consistent with `CLAUDE.md`'s own
`EnterWorktree`-targets-primary-repo-only guidance, since this worktree is very often created
from a session whose primary repo isn't dev-env at all.

### 2. Both existing writers now call the helper

`research/SKILL.md`'s "Library feedback loop" step and `journal-compose/SKILL.md` Section 11
Pass 2 both now read `queue-source-library-entry/SKILL.md` and execute its Behavior section
(`CALLER` = `research` / `journal-compose` respectively) instead of writing to
`~/.claude/skills/sources.md` directly. Both keep their existing Pass-1 **grep** against
`~/.claude/skills/sources.md` unchanged — reading through the junction is harmless (it always
reflects the latest content actually merged to `main`); only the **write** path changes.
`research/SKILL.md`'s `allowed-tools` frontmatter gains `Bash` (needed for the worktree git
operations; `journal-compose` already had it).

### 3. No auto-opened PR

The queue branch accumulates entries indefinitely; this fix does **not** auto-open a PR from it.
A human sweeps it into a PR whenever convenient — the same manual pattern already used twice (PR
#649, issue #697), just now happening from a clean, discoverable, isolated worktree/branch
instead of from a dirty canonical checkout nobody immediately understands the cause of.

---

## Judgment calls

### Isolate-and-push, not isolate-and-auto-PR

Considered instead: create a fresh worktree per invocation, commit, push, and immediately `gh pr
create` — fully closing the loop with no manual sweep ever required. Rejected for this fix:

- The originating issue ([dev-env#708](https://github.com/brownm09/dev-env/issues/708)) frames
  the acceptable failure mode explicitly as "an uncommitted branch sits unnoticed in a worktree
  (recoverable, low-blast-radius)" — not "a PR is auto-opened." Isolate-and-push is the more
  conservative, smaller-surface reading of that ask.
- A citation addition is content curation, not a "problem" in the sense `CLAUDE.md`'s
  issue-before-changes rule is scoped to; auto-opening a PR (and, by that rule, an issue) per
  single citation would add process weight neither incident nor the issue asked for, and there is
  no existing precedent in this repo for a skill auto-opening PRs from unattended background
  work.
- The demonstrated recovery pattern (both #649 and #697) was a human finding **one place** with
  **multiple** pending entries and bundling them into one PR. A single reused queue branch
  preserves that convenience; a fresh worktree+PR per entry would instead scatter entries across
  many small PRs, each requiring separate discovery and review.

### One shared, reused queue worktree/branch, not one per invocation

A fresh worktree+branch per call would fully avoid any cross-invocation collision risk, at the
cost of scattering queued entries across many places a future human sweep would need to
separately discover. A single, well-known, reused location (`.claude/worktrees/research-sources-queue`,
`chore/research-sources-queue`) directly mirrors the "everything pending was in one place"
property that made both prior manual recoveries tractable. Two concurrent invocations both
committing to this worktree at nearly the same instant is a real but rare edge case (no worse
than today's identical race writing straight to canonical) and is not specially guarded against.

### The queue worktree is never rebased onto `origin/main` automatically

Rebasing on every append would keep the branch current, but a rebase can conflict, and resolving
a conflict is not something a markdown-driven skill invocation should attempt unattended
(`journal-compose` Section 11 runs with **no user present at all**). The branch is created once
from `origin/main` and only ever gains commits after that; whoever eventually sweeps it into a PR
resolves any drift then, exactly as the two prior manual recoveries already did.

### New skill file, not inlined logic duplicated in both callers

`research/SKILL.md` and `journal-compose/SKILL.md` Section 11 perform structurally the same
"queue an entry" operation. A shared, reusable `Behavior`-section skill — the exact shape
`sync-routine-worktree` already established for routines — means one place to fix if the
mechanics ever need to change, and guarantees both callers converge on the identical worktree and
branch rather than two independently-maintained, potentially-diverging copies of the same
procedure.

### Push for durability, but push failure is non-fatal

A push failure (no network, transient GitHub outage) must not block the invoking skill's primary
output — `/research`'s citation list, or `journal-compose`'s composed journal — since the entry
is already safely isolated in the local worktree commit regardless of whether the push succeeds.

---

## Consequences

- **The canonical dev-env checkout is never touched by either skill's source-library writes
  again** — closing the exact recurring failure mode both PR #649 and issue #697 demonstrated.
- **A new accumulation point exists**: `chore/research-sources-queue`. Unlike before, it is
  self-documenting (a named branch, a clean commit history, one commit per queued entry) rather
  than an unexplained dirty file in the canonical checkout — but it still requires a human to
  periodically notice it and open a PR. No automated sweep is introduced by this fix; a future
  follow-up could add one if manual sweeping proves to lag in practice.
- **`research/SKILL.md` gains `Bash`** in its `allowed-tools` frontmatter.
- **Testing.** No `.py`/`.sh` files change — this is a skill-markdown and documentation change,
  matching [ADR-082](082-journal-compose-worktree-isolation.md)'s own precedent for the same
  class of change. Verification: a repo-wide grep confirming neither skill retains a direct write
  path to `~/.claude/skills/sources.md` (only the unchanged Pass-1 **grep** reads remain), plus a
  manual dry run of the new skill's exact command sequence against a disposable throwaway fixture
  repo (worktree creation, append, commit, push, and confirmation that
  `pre-tool-use-canonical-mutate-guard.py` does not block any step — `git worktree add`/`prune`/
  `show-ref`/`fetch` are not in that hook's mutating-verb list at all, and `commit`/`push` inside
  the queue worktree resolve, via `git rev-parse --show-toplevel`, to the worktree's own root —
  not the canonical root — so the hook's redirect-target check does not classify them as
  canonical-directed).
- **Observability.** N/A in the hook/script sense (dev-env's `## Observability` section) — this
  is a skill-markdown change with no runtime to instrument. The new skill's own step-by-step
  "Return SUCCESS with a one-line summary" is its user-facing diagnostic surface, matching
  `sync-routine-worktree`'s existing convention.
- **Security.** N/A — no new credentials, secrets, or auth surface; the same `git`/`gh` operation
  classes both skills already performed.
- **Resilience.** Strictly improves failure isolation: a worktree-creation failure now surfaces
  immediately as a reported failure (with an explicit "do not fall back to the direct write" rule
  in the new skill), rather than the previous behavior of silently succeeding at the direct write
  and only failing much later, invisibly, at the next `dev-env-sync.py` pull attempt.
- **Performance.** One additional `git worktree add`-or-reuse, one commit, and one best-effort
  push per queued entry — negligible next to the subagent web-search cost that produced the
  entry in the first place.
- **Data integrity.** N/A — no schema or migration surface; `sources.md`'s existing entry format
  is unchanged.

---

## Review Hardening (dev-env#708/PR#714)

`/review` on this PR (two parallel Opus subagents — correctness/security and
reliability/performance/maintainability, each independently confirming the git/hook interaction
claims above by tracing the actual hook source rather than trusting this ADR's prose) found the
first implementation of `queue-source-library-entry/SKILL.md` incomplete in seven blocking ways
and four non-blocking ways, all fixed in the same PR before merge:

**Blocking:**

1. **Branch-resolution order bug.** The original Step 3 checked the remote-tracking ref first and
   ran `worktree add -b ... origin/$QUEUE_BRANCH` whenever it existed — but `-b` hard-fails if a
   local branch of that name already exists, which is the ordinary state from the second real
   invocation onward (`worktree prune` clears the worktree registration, never the branch ref).
   Every subsequent invocation would have silently stopped queuing entries. **Fixed** — check the
   local branch first.
2. **Command injection in the untrusted-content fallback.** The documented Bash fallback for a
   blocked `Edit` call ("e.g. `node -e` ... or `sed`") gave no guidance on keeping
   `ENTRY_MARKDOWN` — arbitrary, attacker-influenceable web-search output — out of the
   interpreter's command string. Following the guidance literally invites shell/JS injection the
   moment that fallback path is exercised. **Fixed** — the fallback is now a concrete snippet that
   passes all values via environment variables, never string interpolation.
3. **Weak liveness/orphan detection.** Reuse was gated on `${QUEUE_WORKTREE}/.git` existing alone
   — weaker than this repo's own `pre-tool-use-worktree-path-check.py`, which also requires
   `git rev-parse --show-toplevel` to resolve back to the worktree. Both orphan shapes the step
   claimed to cover (disconnected `.git` link; missing link with the directory still present)
   would have failed later, at the commit step, instead of being caught up front. **Fixed** — the
   liveness check now requires both signals, with cleanup before recreation.
4. **Dedup gap.** The invoking skill's own Pass-1 grep checks only the canonical, merged
   `sources.md` — it structurally cannot see an entry already sitting queued-but-unmerged on the
   long-lived branch, guaranteeing duplicate accumulation on any repeated topic before a human
   sweep. **Fixed** — a second dedup pass now greps the queue worktree's own copy.
5. **Unconditional success return.** The original Step 8 returned SUCCESS unconditionally, with no
   verification the commit actually landed — a silent no-op (e.g. from the concurrency race below)
   would have been reported to the user as "queued." **Fixed** — a new verification step confirms
   the commit before SUCCESS is ever returned.
6. **Unhandled concurrency.** Every caller converges on one shared worktree with no mutual
   exclusion, and the unattended daily `journal-compose` run can genuinely overlap an interactive
   `/research` invocation. A race on `worktree add` or the shared index (`index.lock`, or a
   "nothing to commit" false negative) would have silently dropped the losing side's entry.
   **Fixed** — a lock/collision failure now retries the whole sequence once before reporting
   failure, rather than silently succeeding or looping forever.
7. **Missing commit pathspec.** The commit was a bare `git commit -m ...`, contradicting this
   repo's own `CLAUDE.md` mandate for an explicit pathspec on any commit into a checkout shared
   across concurrent writers — exactly this worktree's situation, and exactly what would have let
   one invocation's commit sweep in another's staged-but-uncommitted change. **Fixed** — the
   commit now carries `-- claude/skills/sources.md`.

**Non-blocking:**

8. **`SECTION` exact-match fragility.** A near-miss (case, pluralization, trailing whitespace) in
   an LLM-chosen `SECTION` value would silently create a near-duplicate section. **Fixed** — the
   Parameters table now requires the caller copy the heading verbatim from its own grep output,
   and the insert step matches case-/whitespace-insensitively before deciding no section fits.
9. **Commit-message type mismatch.** The message hardcoded `research:` regardless of `CALLER`,
   reading as "research: ... (via journal-compose)" for that caller. **Fixed** — changed to the
   neutral `chore:` type matching the branch prefix.
10. **`CALLER` interpolation, latent risk.** Not exploitable today (both documented callers pass
    fixed literals), but undocumented as a constraint despite the skill's own "invoked by other
    skills" framing inviting future callers. **Fixed** — the Parameters table now states `CALLER`
    must be a fixed literal from this document.
11. **Stale-branch resurrection after a sweep.** Reusing a local branch already merged into
    `origin/main` (a completed prior sweep) would have re-created the just-deleted remote branch,
    carrying already-merged commits alongside the new one. **Fixed** — worktree creation now
    checks whether the local branch's tip is already an ancestor of `origin/main` and deletes it
    first if so, so a post-sweep invocation starts fresh.

None of these findings change the *shape* of the Decision above (worktree isolation, a single
reused queue branch, no auto-PR) — they correct the implementation to actually deliver it
reliably and safely, the same "hardening, not re-litigation" pattern this repo's other
review-hardening addenda follow (e.g. [ADR-071 Amendment 2](071-canonical-checkout-mutate-guard-hook.md)).

---

## References

- `claude/skills/queue-source-library-entry/SKILL.md` — new shared helper skill
- `claude/skills/research/SKILL.md` — Library feedback loop step, updated
- `claude/skills/journal-compose/SKILL.md` — Section 11 Pass 2, updated
- [ADR-013](013-sync-routine-worktree-skill.md) — the `sync-routine-worktree` precedent this
  ADR's helper-skill shape follows
- [ADR-082](082-journal-compose-worktree-isolation.md) — journal-compose's own worktree
  isolation; explains why it does not already cover this dev-env-targeting write
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — canonical-mutate guard; explains why a
  plain `Edit`/`Write` call is outside its scope
- [ADR-024](024-worktree-path-guard-hook.md) — worktree-path guard; explains why it never fires
  for either writer, and is the source of this ADR's `Edit`/`Write`-blocked fallback guidance
- [PR #649](https://github.com/brownm09/dev-env/pull/649) — first incident
- [dev-env#697](https://github.com/brownm09/dev-env/issues/697) — second incident, 21
  commits / ~41h of drift
- [dev-env#708](https://github.com/brownm09/dev-env/issues/708) — motivating issue for this fix
