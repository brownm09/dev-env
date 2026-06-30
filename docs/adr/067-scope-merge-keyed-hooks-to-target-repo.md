# ADR-067 — Scope Merge-Keyed Hook Operations to the Merge-Target Repo

**Date:** 2026-06-30
**Status:** Accepted
**Refines:** [ADR-065](065-scope-push-reminder-to-target-repo.md)
**Tags:** hooks, post-tool-use, gh-pr-merge, cross-repo, correction

---

## Context

[ADR-065](065-scope-push-reminder-to-target-repo.md) scoped `pr-merge-reminder.py`'s
`git push` branch to the push-target repo by adding `_effective_push_dir` — a
best-effort parser that resolves the `cd <path> &&` prefix that governs which repo
a push actually targets. The same cwd-vs-target gap exists in two merge hooks:

**1. `post-pr-merge-pull.py` — `extract_repo` cwd fallback.**
When `gh pr merge` is run with no `--repo` flag and no GitHub URL in the command
string, `extract_repo` falls back to `git -C cwd remote get-url origin`. When the
session cwd is a lifting-logbook worktree (e.g. a session is at
`/Git/lifting-logbook/.claude/worktrees/fix-foo`), this infers
`brownm09/lifting-logbook` as the merged repo and fast-forwards lifting-logbook's
`main` instead of the dev-env `main` that was actually merged. The dev-env canonical
worktree's `main` stays stale until a manual `git -C ~/Git/dev-env pull --ff-only`.
This incident was observed during the #442 / ADR-065 session.

**2. `pr-merge-reminder.py` — `is_merge` branch step 1.**
The merge reminder told Claude: *"1. Identify the project journal path from cwd."*
When cwd ≠ the merged PR's repo (the same cross-repo scenario), Claude is directed to
identify the journal path from the wrong project's cwd and may write the stub to the
wrong journal path.

**Audit of all merge-triggered PostToolUse hooks** (all five, for completeness):

| Hook | cwd role | Cross-repo risk |
|---|---|---|
| `post-pr-merge-pull.py` | inferred repo slug for git-fetch target | **Bug — fixed here** |
| `pr-merge-reminder.py` | step-1 wording for journal lookup | **Bug — fixed here** |
| `post-pr-merge-reclaim.py` | `--protect-cwd` for disk reclamation | N/A — no repo targeting; protecting active session is correct |
| `post-merge-tile-checkpoint.py` | not used — pure reminder | N/A |
| `post-tool-use.py` (board-move) | determines which project board to use | N/A — keying off cwd is intentional for `gh issue create` / `gh pr create`; canonical-root resolution via `git rev-parse --git-common-dir` handles worktrees |

---

## Decision

**1. Add `effective_merge_dir(command, cwd)` to `_hookio.py` as a shared helper.**

Mirrors `_effective_push_dir` in `pr-merge-reminder.py` (ADR-065) but scans to
`gh pr merge` instead of `git push`. Parses a `cd <path> &&` (or `;`) prefix that
chains into the merge and returns `<path>` (resolved against `cwd` when relative); a
bare `gh pr merge` returns `cwd`. Conservative: any shape it cannot parse falls back
to `cwd` — under-corrects rather than mis-fires. Shared in `_hookio.py` because both
`post-pr-merge-pull.py` and `pr-merge-reminder.py` need it.

**2. Update `post-pr-merge-pull.py` — widen `extract_repo` to three resolution paths.**

New resolution order:
1. `--repo owner/repo` flag in the command string (unchanged — highest confidence).
2. GitHub PR URL in the command string (e.g. `gh pr merge https://…/pull/N`) — pure
   string parse, no subprocess, unit-testable.
3. `cd-chain` scoping: call `effective_merge_dir(command, cwd)` to get the effective
   directory, then run `git -C effective_dir remote get-url origin` (the subprocess
   fallback from before ADR-067, now directed at the correct directory).

The worktree-parking logic (`git -C cwd checkout -b park`) intentionally remains
cwd-based — it parks THIS session's own worktree regardless of which repo was merged.

**3. Update `pr-merge-reminder.py` — add `repo:` field and fix step 1 wording.**

In the `is_merge` reminder block: compute `merge_dir = effective_merge_dir(command, cwd)`,
emit `repo: {merge_dir}` (mirrors the `is_push` branch, which already shows the
resolved push dir), and change step 1 from *"Identify the project journal path from
cwd"* to *"Identify the project journal path from the repo above."* The `cwd:` field
remains for debugging context.

---

## Consequences

**Positive:**
- A `cd /Git/dev-env && gh pr merge` run from a lifting-logbook session now
  fast-forwards dev-env's `main`, not lifting-logbook's — the motivating incident is
  fixed.
- The journal stub reminder names the correct project after a cross-repo merge,
  eliminating the stub-to-wrong-journal-path defect.
- `effective_merge_dir` is a single shared implementation; both hooks benefit from any
  future improvements to the cd-chain parser.

**Trade-offs / limits:**
- A bare `gh pr merge --squash --delete-branch` with no URL, no `--repo`, and no cd-chain
  prefix still falls back to `git -C cwd remote get-url origin` (unchanged from before
  this ADR). This under-corrects for the case where cwd happens to be a foreign repo's
  directory but the merge was typed without a chain prefix — the same silent fallback
  as before, never a wrong-repo positive.
- The cd-chain parser has the same conservative limits as ADR-065's push-dir parser:
  a `cd` hidden behind quoting or an unusual construct falls back to `cwd`; a relative
  `cd` in a multi-`cd` chain resolves against `cwd` not against an earlier absolute
  `cd`. Both under-correct silently.

---

## Alternatives considered

**Add per-hook local `effective_merge_dir` (not shared).** Would duplicate the
implementation across the two hooks; rejected for the same reason `read_command_output`
was centralised in `_hookio.py` (ADR-050).

**Parse the `gh pr merge` output for the PR URL.** The success output (`✓ Squashed and
merged pull request #443`) does not include the repo slug; a URL-in-output approach
would require an additional `gh pr view` subprocess call. Rejected: complex, requires
network, and the cd-chain approach handles the common cross-repo merge shape without
a subprocess.

**Leave `post-pr-merge-pull.py` unchanged; fix only the reminder.** The reminder fix
alone does not prevent the fast-forward going to the wrong repo's `main`. Both bugs
share the same root cause; fixing only one leaves the other silently wrong.

---

## References

- [ADR-065](065-scope-push-reminder-to-target-repo.md) — the push-scoping precedent
  this ADR mirrors and extends to merge operations.
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) — rationale for centralising
  shared hook helpers in `_hookio.py`.
- Issue [#446](https://github.com/brownm09/dev-env/issues/446) — tracked this fix.
- `claude/scripts/_hookio.py`; `claude/scripts/post-pr-merge-pull.py`;
  `claude/scripts/pr-merge-reminder.py`.
- `claude/scripts/tests/test_hookio.py`; `claude/scripts/tests/test_post_pr_merge_pull.py`;
  `claude/scripts/tests/test_pr_merge_reminder.py`.
