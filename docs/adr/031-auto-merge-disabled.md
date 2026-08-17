# ADR-031 — Auto-Merge Disabled Across All Repos

**Date:** 2026-05-28
**Status:** Accepted
**Closes:** [dev-env#284](https://github.com/brownm09/dev-env/issues/284)
**Tags:** git, pr, merge, workflow, hooks, post-merge
**Related:** [ADR-011](011-adr-warrant-check.md), [ADR-012](012-post-merge-checklist-board-done-roadmap-update.md), [ADR-019](019-doc-reconciliation-enforcement.md), [ADR-028](028-all-findings-merge-gate.md)

---

## Context

GitHub's [auto-merge feature](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request) lands a PR server-side as soon as branch protection requirements (CI, approvals) are satisfied. The merge happens asynchronously, often after the Claude session that opened the PR has ended.

The documented workflow across `brownm09/*` repos has accumulated six rules that fire at the merge moment, all anchored to the in-session `gh pr merge` Bash invocation:

1. **PostToolUse `usage-snapshot.py` hook** — queries the Anthropic usage API and parses the session JSONL after `gh pr merge` returns. Emits a `### Usage Snapshot (post-merge)` block that the post-merge stub includes verbatim. The hook is bound to the Bash tool call; a server-side merge bypasses it entirely, and the cost data for that PR is permanently lost from the journal.
2. **Post-merge journal stub** (global `claude/CLAUDE.md` → "Write a stub on PR merge") — a merge is a session boundary requiring stub coverage. Async merges produce no session boundary to attach a stub to.
3. **ADR-warrant checkpoint 3** ([ADR-011](011-adr-warrant-check.md)) — "immediately before `gh pr merge`." Server-side merges run no Claude code at all at that moment.
4. **Doc-reconciliation checkpoint 3** ([ADR-019](019-doc-reconciliation-enforcement.md)) — same anchor as ADR-011's checkpoint 3.
5. **`/review` all-findings merge gate** ([ADR-028](028-all-findings-merge-gate.md)) — sequence: `gh pr create` → stub → `/compact` → `/review --post-comment` → address findings → merge. Auto-merge lands the PR as soon as CI is green, often before `/review` even runs.
6. **Project board → Done transition + roadmap shipped-row move** ([ADR-012](012-post-merge-checklist-board-done-roadmap-update.md), automated by the post-tool-use hook from [ADR-014](014-auto-move-project-item-done-on-merge.md)) — the automation is triggered by the local `gh pr merge` tool call, not by GitHub webhooks.

Pre-PR gates ([ADR-026](026-suppression-policy.md) suppression check, [ADR-029](029-test-integrity-policy.md) test-integrity check, [ADR-030](030-baseline-test-failure-policy.md) baseline-tests diff, test-before-PR) all run before `gh pr create` and are unaffected by auto-merge.

The question is whether to enable auto-merge as a convenience (no in-session wait for CI) at the cost of these six rules, refactor the rules to run pre-merge, or keep auto-merge off and accept the in-session wait.

---

## Decision

**Repo-wide auto-merge stays disabled across all `brownm09/*` repos.** Every PR is merged in-session via `gh pr merge --squash --delete-branch` after `/review`, ADR-warrant checkpoint 3, doc-reconciliation checkpoint 3, project board transition, and roadmap update have completed.

**Per-PR escape hatch:** `gh pr merge --auto --squash` is permitted on a per-PR basis *only after* all in-session gates above have already passed in the same session. The auto-merge flag in that case waits for green CI but no longer guards any rule — the gates already ran. This is the only sanctioned use of GitHub's auto-merge feature. Escape-hatch use accepts two unavoidable losses for that specific PR, both flowing from the same root cause (the actual merge completes async, after the session has moved on): (1) the post-merge `usage-snapshot.py` hook does not fire — the `gh pr merge --auto` Bash call returns immediately rather than at merge time; (2) no post-merge journal stub is written — there is no in-session merge moment to attach one to. Escape-hatch PRs should be noted in the opening stub (written at `gh pr create` time) with a `merge: auto` annotation so the gap is visible without having to infer it from a missing snapshot.

Auto-merge must never be enabled at the repo level (no default branch protection rule that auto-merges on green CI).

---

## Rationale

**Why not refactor the rules to fire pre-merge?** Considered and rejected:

- The merge moment is load-bearing as the *last reversible point* before code lands on `main`. Running ADR-warrant and doc-reconciliation checks pre-merge weakens the forcing function: the checks already exist at PR-create time (checkpoints 1 and 2); checkpoint 3 is the final catch for things that surfaced during review.
- The `usage-snapshot.py` hook fundamentally cannot run pre-merge — its purpose is to capture the *complete* session cost up to and including the merge. Moving it earlier loses the data it exists to collect.
- The post-merge journal stub is a session boundary marker. Pre-merge would not be a session boundary; the session continues until the merge completes.

**Why not per-PR auto-merge without the in-session gates?** Rejected: that's just repo-wide auto-merge with extra steps. The whole point of the gates is that they're sequenced after `gh pr create` and before the merge lands. Skipping them on a per-PR basis is no different from skipping them on every PR.

**Why accept the in-session wait?** In practice CI on these repos runs in 2–10 minutes. The session is already paid-for context; sleeping through CI is not a significant cost compared to losing six workflow guarantees on every PR.

---

## Consequences

**Forced:** every merge is in-session. One `### Usage Snapshot (post-merge)` block per merged PR. ADR-warrant and doc-reconciliation get a real third checkpoint. `/review` always runs before merge. Project board and roadmap stay in sync via the existing automation.

**Cost:** cannot walk away after `gh pr create` and let CI auto-land the change. For PRs where in-session waiting for CI is wasteful, the per-PR `--auto` escape hatch is available after the gates have completed — at the price of one missing usage-snapshot block.

**Detection:** if a project later enables auto-merge as a default branch protection rule, the symptoms will be (a) journals missing post-merge usage-snapshot blocks, (b) merged PRs with no review comment from `/review`, (c) project board items stuck in "In Progress" after merge. (a) alone is expected on escape-hatch PRs (see Decision) and is not diagnostic on its own; (b) or (c) on a recent merge — or all three co-occurring — is a signal this ADR has been silently violated.

---

## Alternatives considered

**A — Enable repo-wide auto-merge.** Rejected: breaks all six workflow rules above with no compensating benefit beyond convenience.

**B — Per-PR `--auto` without gates.** Rejected: equivalent to A in practice; bypasses [ADR-028](028-all-findings-merge-gate.md).

**C — Refactor all merge-time rules to pre-merge.** Rejected: see Rationale. The merge moment is the correct anchor for the rules that fire there.

**D (chosen) — Disable repo-wide auto-merge; allow per-PR `--auto` only after in-session gates pass.** Preserves all six rules. Provides a narrow escape hatch for cases where the in-session CI wait is genuinely wasteful.

---

## References

- [ADR-011 — ADR-Warrant Check at Plan, PR-Open, and PR-Merge Checkpoints](011-adr-warrant-check.md)
- [ADR-012 — Post-Merge Checklist: Board Done + Roadmap Update Rules](012-post-merge-checklist-board-done-roadmap-update.md)
- [ADR-014 — Auto-Move GitHub Project Item to Done on PR Merge](014-auto-move-project-item-done-on-merge.md)
- [ADR-019 — Doc-Reconciliation Enforcement](019-doc-reconciliation-enforcement.md)
- [ADR-028 — All-Findings Merge Gate](028-all-findings-merge-gate.md)
- GitHub Docs — [Automatically merging a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request)
- GitHub Docs — [Managing auto-merge for pull requests in your repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-auto-merge-for-pull-requests-in-your-repository)

---

## Addendum (2026-07-03) — The repo-level toggle and the escape hatch are the same switch

The Decision above treats "auto-merge disabled at the repo level" and the "per-PR escape hatch"
(`gh pr merge --auto`) as independent controls. They are not: GitHub exposes exactly one
repo-level control for this feature — **Settings → General → Pull Requests → "Allow auto-merge"**
(API field `allow_auto_merge`) — and it gates *every* per-PR auto-merge request, CLI escape hatch
included. Per GitHub's docs:

> "If you allow auto-merge for pull requests in your repository, people with write permissions
> can configure individual pull requests in the repository to merge automatically when all merge
> requirements are met."

There is no separate "auto-merge everything by default" branch-protection mechanism distinct from
this toggle — auto-merge is always requested per-PR (via the web UI or `--auto`), and this one
setting is the only gate on that request. So the Decision's parenthetical ("no default branch
protection rule that auto-merges on green CI") was guarding against a mechanism GitHub doesn't
actually have; the real, single toggle also happens to be the escape hatch's prerequisite.

**Observed:** lifting-logbook PR #664 (2026-07-03) — `gh pr merge 664 --squash --delete-branch
--auto` failed immediately with `GraphQL: Auto merge is not allowed for this repository
(enablePullRequestAutoMerge)`. `gh api repos/merickvaughn/lifting-logbook` confirmed
`allow_auto_merge: false` at the repo-settings level. This is the deterministic, expected result
of the Decision as literally written — not a bug — but the ADR never previously said so, so a
session hitting this error had no way to tell from this document alone.

**Consequence the Decision didn't spell out:** with the toggle off, the "Per-PR escape hatch"
clause describes an operation that cannot be invoked in that repo, full stop — it fails at the
GitHub API before any in-session judgment about whether the gates have passed even comes into
play. `allow_auto_merge: false` was confirmed for lifting-logbook this session (`gh api
repos/merickvaughn/lifting-logbook`); no repo-by-repo survey was run across the rest of `brownm09/*`.
ADR-031's Decision sets the toggle-off posture as the repo-wide default with no documented
exceptions, so absent evidence of a deliberate opt-in elsewhere, treat the escape hatch as
**currently inert everywhere** — but a session on an unfamiliar repo should still verify rather
than assume (see Operational guidance below).

**Operational guidance:** don't rediscover this per-PR.

- The primary path — wait in-session for CI, then run plain `gh pr merge --squash
  --delete-branch` — is the only path that works today, not merely the preferred one. Plain
  `gh pr merge` still requires all required status checks to have already completed and passed
  (GitHub rejects the merge call otherwise), so the session's job is to poll/wait for checks
  (e.g. `gh pr checks --watch`), not to reach for `--auto` as a way to avoid waiting.
- To confirm the setting before ever attempting `--auto` on an unfamiliar repo:
  `gh api repos/{owner}/{repo}` and read the `allow_auto_merge` field (parse with `node -e`, not
  `jq` — see `claude/CLAUDE.md`).

**Open question, not resolved here:** enabling `allow_auto_merge` at the repo level does not, by
itself, cause any PR to merge — it only unlocks the per-PR opt-in request the Decision already
sanctions as the escape hatch. Whether "must never be enabled at the repo level" was meant to
block that too, or was written assuming the non-existent "auto-merge everything by default"
mechanism addressed above, is a policy question left for whoever revisits this ADR next. This
addendum documents the mechanical reality and leaves the Decision's toggle instruction unchanged.

---

## Addendum (2026-07-04) — Open question resolved: escape hatch stays inert by design ([dev-env#565](https://github.com/brownm09/dev-env/issues/565))

The question the 2026-07-03 addendum left open is resolved: **`allow_auto_merge` stays `false`
across every `brownm09/*` repo, with no exceptions, and the Decision's "Per-PR escape hatch"
clause is permanently inert by design** — not a temporarily-off convenience awaiting activation.

**Survey.** `gh api repos/brownm09/{repo}` was run against all 52 active (non-archived,
non-fork) `brownm09/*` repos on 2026-07-04. Every one reports `allow_auto_merge: false`. No repo
has drifted to an accidental opt-in since the Decision was written.

**Why this isn't merely "leave it as-is."** The 2026-07-03 addendum's mechanical correction — that
GitHub has no distinct "auto-merge everything by default" mechanism, so the fear behind "never
enable at repo level" targeted something that doesn't exist — is right, but it doesn't make the
toggle purposeless. With the toggle off, `gh pr merge --auto` cannot be invoked, by anyone or
anything, before the six in-session rules this ADR protects (`usage-snapshot.py`, the post-merge
journal stub, ADR-warrant checkpoint 3, doc-reconciliation checkpoint 3, the `/review`
all-findings gate, project-board automation) have already run in the same session. That is a
*mechanical* guarantee. Turning the toggle on would not itself break any of the six rules — but
it would downgrade their protection from "impossible to skip" to "skipped only if the session
remembers to sequence `--auto` correctly," which is exactly the discipline-only enforcement this
environment avoids relying on elsewhere (see the canonical-checkout-mutate-guard hook, ADR-071;
the memory-immortalization issue-pairing rule, ADR-048; the pre-install-freespace-gate hook,
ADR-045).
Rejecting Alternative B in the original Rationale ("per-PR auto-merge without the in-session
gates... is no different from skipping them on every PR") already made this argument for the
manual-invocation case; the same logic applies to trusting a session to self-police *when* it
reaches for `--auto`.

**Evidence there's no real cost to closing this off.** The only concrete attempt to use the
escape hatch — lifting-logbook PR #664 — failed at the GitHub API (`allow_auto_merge` was, and
is, `false` there) and the session fell back to the primary path: wait for CI, then plain
`gh pr merge --squash --delete-branch`. PR #664 merged cleanly through that path with no further
incident. Combined with the original Rationale's already-accepted cost (CI runs 2–10 minutes;
"sleeping through CI is not a significant cost"), there is no evidenced case where the escape
hatch's unavailability blocked real work.

**Operational guidance, updated.** Do not attempt `gh pr merge --auto` on any `brownm09/*` repo.
A failure from it is expected, permanent behavior, not a bug or a gap to fix — go straight to the
primary path (poll/wait for CI, then plain `gh pr merge --squash --delete-branch`). The
"Per-PR escape hatch" paragraph in the Decision above is retained for historical context (it
explains why `--auto` was once considered sanctioned) but should be read as superseded by this
addendum: it does not describe an available operation.

**What would change this.** This resolution stands until a *mechanical* pre-check exists that
verifies, at the moment `--auto` is invoked, that `/review`, ADR-warrant checkpoint 3, and
doc-reconciliation checkpoint 3 have already completed in the current session — i.e., something
that restores the "impossible to skip" property this addendum is unwilling to trade away for
convenience. Only these three are named because they are the only ones a pre-invocation check
could ever verify: the other three rules (`usage-snapshot.py`, the post-merge journal stub,
project-board automation) are tied to the physical merge moment itself, which `--auto` always
defers to an async, out-of-session event — no pre-check, however sophisticated, can make an
async merge produce an in-session moment to hook. Their loss is inherent to using `--auto` at
all, not a gap this future gate could close. Absent the three-rule gate, re-opening this question
should default to "no" for the same reasons given here.

---

## Addendum (2026-07-05) — The mechanical pre-check now exists ([ADR-083](083-auto-merge-checkpoint-gate.md))

The condition the addendum above named — "a mechanical pre-check that verifies, at the moment
`--auto` is invoked, that `/review`, ADR-warrant checkpoint 3, and doc-reconciliation checkpoint 3
have already completed in the current session" — is now met. [ADR-083](083-auto-merge-checkpoint-gate.md)
designed it and [dev-env#574](https://github.com/brownm09/dev-env/issues/574) shipped it as
`claude/scripts/pre-auto-merge-checkpoint-gate.py`, wired in `settings.json` immediately after
`pre-merge-findings-gate.py`.

This does **not** reopen `--auto` anywhere by itself. The operational guidance above ("Do not
attempt `gh pr merge --auto`") is superseded by the new hook's own behavior — it will now
mechanically block or allow, rather than the operation being universally inert — but
`allow_auto_merge` still defaults to `false` on every `brownm09/*` repo, so `--auto` still fails at
the GitHub API today regardless of the hook. Flipping that toggle for any specific repo remains a
separate, smaller decision (this ADR's Decision point 6 / ADR-083 Follow-up item 7) — this
addendum does not make it. See `claude/CLAUDE.md` → Git Workflow → "Auto-merge is off by design"
for the current operational guidance.
