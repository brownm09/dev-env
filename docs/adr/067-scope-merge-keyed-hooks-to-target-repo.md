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
`merickvaughn/lifting-logbook` as the merged repo and fast-forwards lifting-logbook's
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

## Amendment 1 (2026-07-04) — `post-pr-merge-project.py` was a sixth merge-triggered hook, missed by this ADR's own audit (dev-env#559)

**Symptom:** a session pinned to lifting-logbook's cwd ran `gh pr merge
"https://github.com/brownm09/dev-env/pull/554" --squash --delete-branch` — a bare
PR URL, no `cd`-chain, no `--repo` flag. `post-pr-merge-project.py` resolved
`repo = config.get("repo", "")` from `load_config(cwd)` (lifting-logbook's own
`.claude/hook-config.json`), fetched **lifting-logbook's own PR #554** instead of
dev-env's, parsed its unrelated "Closes #537", and moved that (coincidentally
still-Done) lifting-logbook issue to Done on lifting-logbook's own project board.
Harmless only because the issue was already Done weeks earlier — a still-open
issue would have been silently corrupted with no error.

**Root cause: this ADR's own audit table missed a sixth hook.** The Context
section above states "all five, for completeness" and lists `post-pr-merge-pull.py`,
`pr-merge-reminder.py`, `post-pr-merge-reclaim.py`, `post-merge-tile-checkpoint.py`,
and `post-tool-use.py` (issue/PR-create board add) — but never
`post-pr-merge-project.py` (the *merge*-triggered board-**move** hook, a distinct
file from `post-tool-use.py`'s create-time board **add**). Unlike the other two
"Bug — fixed here" hooks, `post-pr-merge-project.py`'s repo-targeting shape doesn't
match a `git remote get-url` inference or a displayed `cwd:`/`repo:` string — it
goes through a *config file load* (`load_config(cwd)` → `config.get("repo")`),
which reads as a different code shape at a glance even though it has the identical
cwd-vs-target gap. This is the same lesson
[ADR-050 Amendment 6](050-shared-hookio-sibling-hook-fixes.md) already drew for the
*sibling* `scan_top_level` consolidation ("a sweep is only as complete as the list
it started from... grep for the engine, not just the call pattern") — recurring
here one ADR later, for the *repo-resolution* engine instead of the
command-parsing engine.

**Fix — a different mechanism than `effective_merge_dir`'s cd-chain scoping.** The
reported incident has no `cd`-chain to resolve; the merge command's own argument is
a full PR URL. `extract_repo_from_command()` parses the owner/repo out of that URL
(mirroring the existing `extract_pr_number_from_command`, scoped to the same
`_MERGE_ARGS_RE` region). When the parsed repo does not match `config.get("repo")`,
`main()` now exits before calling `get_pr_body`, `find_project_item`, or
`move_to_done` — cwd's config (`project_number`/`project_node_id`/`status_field_id`/
`done_option_id`) is scoped to *config's* repo and does not apply to a different one
regardless of which PR's body gets fetched. This is stricter than fixing `repo`
alone: `get_pr_body(pr_number, repo)` fetching the *correct* cross-repo PR body
would still leave `find_project_item` searching *cwd's own* project board by issue
number — a same-numbered-issue collision between the two repos would reproduce the
identical corruption via a different trigger. Skipping outright, rather than
guessing, is consistent with this ADR's own "under-corrects rather than mis-fires"
philosophy for `effective_merge_dir`.

A more complete fix — resolving the *correct* repo's own config via
[ADR-077](077-cross-repo-config-resolution-for-issue-pr-create.md)'s
`_sibling_repo_config` pattern (verified sibling-checkout lookup, already proven for
`post-tool-use.py`'s equivalent create-time gap) — would let the Done-move complete
against the right board instead of just skipping it. Deferred to follow-up
[#571](https://github.com/brownm09/dev-env/issues/571): meaningfully more code
(generalizing a pattern across two independent `load_config` implementations) than
this amendment's guard, which is already a complete, correct fix for the reported
corruption.

**Scope note — cd-chain scoping for this hook remains open.** This amendment adds
URL-argument scoping only. `post-pr-merge-project.py`'s `load_config(cwd)` still
uses raw `cwd`, not `effective_merge_dir(command, cwd)` — a
`cd /Git/dev-env && gh pr merge --squash --delete-branch` (no URL, no `--repo`) run
from a lifting-logbook-pinned session would still mis-resolve today. Filed as
follow-up issue [#569](https://github.com/brownm09/dev-env/issues/569) rather than
folded into dev-env#559: it is the *same* conceptual gap this ADR already closed for
`post-pr-merge-pull.py` and `pr-merge-reminder.py`, so extending it to
`post-pr-merge-project.py` should be low-risk, but it is a distinct code change from
the URL-parsing fix above and was outside dev-env#559's reported scope. A related
but separately-mechanismed gap — `pr-merge-reminder.py`'s `is_create` branch has no
directory resolution at all, unlike its `is_merge`/`is_push` siblings — is tracked
as [#570](https://github.com/brownm09/dev-env/issues/570).

**Coverage:** `extract_repo_from_command()` is exercised offline in
`test_post_pr_merge_project.py` (cross-repo URL, bare-number, bare-form,
cd-prefixed URL, chained-command scoping, and case-insensitive matching). The
`main()`-level skip gate itself is not separately unit-tested, consistent with this
file's existing convention of testing only the pure extraction helpers, never
`main()`'s orchestration or the live `gh` calls.

**References:** Issue [#559](https://github.com/brownm09/dev-env/issues/559);
follow-ups [#569](https://github.com/brownm09/dev-env/issues/569),
[#570](https://github.com/brownm09/dev-env/issues/570), and
[#571](https://github.com/brownm09/dev-env/issues/571); `claude/scripts/post-pr-merge-project.py`;
`claude/scripts/tests/test_post_pr_merge_project.py`;
[ADR-077](077-cross-repo-config-resolution-for-issue-pr-create.md).
