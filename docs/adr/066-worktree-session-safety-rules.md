# ADR-066 — Worktree Session Safety: Origin Verification, Bash `cd` Prevention, and Deregistration Recovery

**Date:** 2026-06-30
**Status:** Accepted
**Tags:** git, worktree, workflow, memory, documentation, global-rule

---

## Context

The 2026-06-30 cross-project memory→repo reconciliation audit
([dev-env#363](https://github.com/brownm09/dev-env/issues/363)) found three durable
worktree-session-safety rules that existed **only** in lifting-logbook agent memory — never ported
into the version-controlled instructions that load every session. Per
[ADR-038](038-durable-preferences-documented-in-repo.md) and
[ADR-048](048-memory-immortalization-issue-pairing.md), a durable rule must not live only in memory
(memory is a private, unreliable cache; the instructions are loaded every session and visible to all
collaborators); the immortalization issue is the floor, the instruction edit is the finish.

Each rule was learned from a concrete lifting-logbook incident where a worktree's relationship to the
main checkout cost tokens or nearly lost work:

1. **Stale worktree base hid a merged fix (#434).** 2026-06-09: a brief named a bug in
   `apps/api/src/programs/import.controller.ts`. The file was absent from the worktree and the
   in-memory adapter showed the buggy behavior, so the bug looked live — but it had been fixed and
   merged ~1h earlier in PR #485; the worktree base was exactly one commit behind. A full fix + PR was
   built before the duplication was caught at journal-writing time (salvaged to a docs-only JSDoc, #492).
2. **`cd`-to-main split-brain (#435).** 2026-06-09, PR #502: an errant
   `git pull --ff-only origin <pr-branch>` run against the main checkout fast-forwarded local `main` to
   the PR merge commit, while `git status` reported "clean" — the in-progress env-var fix was sitting
   uncommitted in the worktree the whole time. Significant token waste and a near-lost fix.
3. **Worktree deregistration routed git to main (#436).** 2026-06-04 and again 2026-06-12
   (mid-session): a disk-full / cleanup event removed a worktree's `.git` link, so git from that dir
   silently walked up and resolved to the main repo — `git checkout -b` landed branches on main and the
   edit hook blocked all Edits. Recovery existed only as tribal knowledge.

These extend prior worktree/remote decisions rather than replace them:
[ADR-004](004-pr-review-reads-from-remote.md) (PR review reads from the remote, not the local
worktree) and [ADR-024](024-worktree-path-guard-hook.md) (PreToolUse path guard / orphan-liveness
check). Neither had itself been written down as a standing behavioral rule.

## Decision

Port all three rules into their instruction homes and record the bundle here.

1. **Missing-file investigation → verify `origin/main` first.** Added as a Git Workflow bullet in the
   global [`claude/CLAUDE.md`](../../claude/CLAUDE.md). When a brief names a file absent from the active
   worktree, run `git fetch origin` then `git ls-tree -r origin/main --name-only | grep <file>` /
   `git show origin/main:<path>` before concluding the feature is unbuilt. Generalizes ADR-004's
   remote-read principle from PR review to investigation-start reads.

2. **Worktree Bash commands must not `cd` to the main repo.** Added as a Git Workflow bullet. In a
   worktree session, Edit/Read/Write target the worktree by absolute path, but a `cd`-ed Bash git/npm
   command runs against the main checkout — split-brain. Use the default cwd (no `cd`); use
   `git -C <path>` for another repo. Reinforces the existing "verify branch before editing" rule.

3. **Worktree deregistration recovery runbook.** Added to [`docs/REFERENCE.md`](../REFERENCE.md) → Git
   Workflow Runbooks: restore the main checkout to `main` → `git worktree prune` →
   `git worktree add --force .claude/worktrees/<name> <branch>` → `npm install`. Complements ADR-024's
   orphan-liveness guard with the recovery procedure.

   > **Correction (2026-07-22, [ADR-116](116-single-source-worktree-recovery-recipe.md) /
   > [dev-env#862](https://github.com/brownm09/dev-env/issues/862)):** the sequence quoted above is
   > superseded and must not be followed as written. `git worktree add --force` does nothing for a
   > non-empty target directory ([dev-env#751](https://github.com/brownm09/dev-env/issues/751)), and the
   > opening `git -C <canonical> checkout main` is now hard-blocked by ADR-071's canonical-mutate guard
   > (`prune` frees the branch anyway). The live sequence — `worktree repair` first, since it preserves
   > uncommitted work, then `prune` → plain `add`, emptying the directory in place only if needed — is
   > single-sourced in `claude/scripts/_worktree_recovery.py` and rendered into both the runbook and the
   > guard hook's block message.

The two memory `feedback_*` rules and the `project_worktree_deregistration` note are deleted once these
edits land on `main`, and issues #434/#435/#436 are closed by the porting PR.

## Considered alternatives

- **No new ADR — rely on the issues + git history.** Rejected (this is the user-confirmed call): the
  rationale would survive only in closed issues and the about-to-be-deleted memory files. The repo's
  precedent is to give worktree/git operational rules a citable in-repo decision record (ADR-004,
  ADR-024, [ADR-035](035-git-push-delete-web-session-constraint.md)); a consolidated ADR keeps the
  three incidents' rationale in-repo and gives the three new rules one anchor to cite.
- **Three separate ADRs (one per rule).** Rejected: the three were surfaced together by one audit,
  share a single theme (worktree-session safety), and each is a short prose rule — three files would
  fragment a coherent bundle and inflate the index without adding clarity.
- **Fold the rules into ADR-038/048.** Rejected: those are meta-ADRs about the *porting process*
  (document durable preferences; pair memory writes with issues). Embedding three operational worktree
  rules would blur their purpose.

## Consequences

- Three worktree-session-safety rules now load every session via the global instructions, instead of
  living in one project's invisible memory cache.
- New worktree/remote rules cite this ADR as their anchor; ADR-004 and ADR-024 gain an explicit
  "standing rule" companion.
- The lifting-logbook memory cache shrinks by three files with no loss of rationale (preserved here and
  in the closed issues).
- Pure documentation change — no hook/script/skill/settings touched, so no Documentation-Maintenance-
  table updates and no runtime behavior change.

## References

- [ADR-004](004-pr-review-reads-from-remote.md) — reads come from the remote, not the local worktree
  (rule 1 generalizes it).
- [ADR-024](024-worktree-path-guard-hook.md) — PreToolUse path guard + orphan-liveness (rule 3
  complements it).
- [ADR-035](035-git-push-delete-web-session-constraint.md) — precedent for a runbook-style git
  constraint recorded as an ADR.
- [ADR-038](038-durable-preferences-documented-in-repo.md),
  [ADR-048](048-memory-immortalization-issue-pairing.md) — durable-preferences-in-repo + memory↔issue
  pairing (the framework this port follows).
- Issues [#434](https://github.com/brownm09/dev-env/issues/434),
  [#435](https://github.com/brownm09/dev-env/issues/435),
  [#436](https://github.com/brownm09/dev-env/issues/436); audit
  [#363](https://github.com/brownm09/dev-env/issues/363).
- Incidents: lifting-logbook PRs [#485](https://github.com/merickvaughn/lifting-logbook/pull/485) /
  [#492](https://github.com/merickvaughn/lifting-logbook/pull/492),
  [#502](https://github.com/merickvaughn/lifting-logbook/pull/502); worktree orphan events 2026-06-04 /
  2026-06-12.
