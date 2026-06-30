# ADR-021 — Auto-Write Journal Stub on git push to Open PR Branch

**Date:** 2026-05-11
**Status:** Accepted
**Refined by:** [ADR-065](065-scope-push-reminder-to-target-repo.md) — push-target scoping + once-per-PR-per-session
**Tags:** journal, stubs, hooks, post-tool-use, git-push, automation

---

## Context

The engineering journal auto-writes stubs at two session boundaries: PR creation
(`gh pr create`) and PR merge (`gh pr merge`). The `pr-merge-reminder.py` PostToolUse
hook detects those commands and emits a reminder so Claude writes the stub without being
asked by the user.

Push events to an existing PR branch — the most common mid-review activity (addressing
review findings, follow-up fixes) — were explicitly excluded from auto-stub. The rationale
at the time was that "the stub was written when the PR was first opened." In practice this
left a gap: iterative push sessions produced no journal record, making the review cycle
invisible in the engineering journal unless the user remembered to request one manually.

---

## Decision

Extend `pr-merge-reminder.py` to detect `git push` commands and, when the pushed branch
has an open PR, emit a journal-update reminder with the PR number, URL, and title.

**Detection approach:**

1. `_PUSH_RE` regex identifies top-level `git push` statements in the command string using
   the existing `_scan_top_level` shell parser (same mechanism as `_MERGE_RE` / `_CREATE_RE`).
2. If the command is a push (and not already a `gh pr create` or `gh pr merge`), the hook
   calls `_open_pr_for_cwd(cwd)`, which:
   - Returns `None` immediately if `cwd` contains `"engineering-journal"` (those pushes are
     handled by `stub-push-archive-reminder.py`).
   - Runs `git branch --show-current` in `cwd` to get the active branch.
   - Runs `gh pr list --head <branch> --json number,url,title --state open --limit 1`.
   - Returns the first result, or `None` if no open PR exists.
3. If a PR is found, the hook emits a reminder (exit 2) with step-by-step stub instructions,
   instructing Claude to check whether a stub already exists for the current session —
   update it in place if yes, create a new stub if no.

**CLAUDE.md update:** "PR updated (push to a branch with an open PR)" is added to the
Update triggers section with check-first-update-or-create semantics. The former exclusion
bullet ("Pushing commits to an existing PR") is removed.

---

## Consequences

**Positive:**
- Every push to a PR branch now yields a stub, closing the main gap in journal coverage.
- The push stub captures review findings addressed, approach decisions, and what changed —
  context that is lost if the session ends before a merge.
- Consistent with the existing pattern: the hook detects the event; CLAUDE.md governs the
  automatic behavior; no user prompt required.

**Trade-off:**
- Every successful `git push` in a non-engineering-journal repo now triggers two subprocess
  calls (`git branch --show-current` + `gh pr list`). Both are fast (~100 ms combined) but
  add latency to pushes on branches without an open PR. The `except Exception: return None`
  guard ensures the hook fails silently if either call errors (no valid cwd, auth issue, etc.).
- Branches with no open PR produce no reminder and no latency beyond the two subprocess calls.
- `_PUSH_RE` matches `git push --delete origin <branch>` (remote branch deletion). If the
  currently checked-out branch has an open PR, this would emit a spurious reminder. In
  practice Claude uses `gh pr merge --delete-branch` rather than `git push --delete`, so
  this case does not arise in the normal workflow. Accepted as a known edge case.
- `_open_pr_for_cwd` uses `git branch --show-current` (the checked-out branch), not the
  push refspec. Pushes with an explicit `other:target` refspec would look up the wrong
  branch. The normal workflow always pushes the currently checked-out branch, so this is
  not a practical concern.

---

## Alternatives considered

**Reminder only, no subprocess lookup:** Emit the reminder on every `git push` and let
Claude check for open PRs. Rejected — too noisy on pushes to branches without a PR (e.g.,
pushing the draft journal branch itself, though that is separately filtered by the
engineering-journal path check).

**Read `open-prs.jsonl` instead of calling `gh`:** The in-repo tracking file lists open
PRs. Rejected — `open-prs.jsonl` only covers PRs Claude opened in a tracked session; it
would miss PRs opened in other sessions or by other tools.
