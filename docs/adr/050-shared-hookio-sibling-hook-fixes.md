# ADR-050 — Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes

**Date:** 2026-06-21
**Status:** Accepted
**Tags:** hooks, post-tool-use, tool_response, payload, github-project, automation, reliability, dry

---

## Context

[ADR-049](049-hook-payload-output-field.md) established that Claude Code's Bash hook
payload exposes a command's output under `tool_response.stdout` / `tool_response.stderr`,
never `output`, and fixed `post-tool-use.py` to read it via a local
`read_command_output()` helper. ADR-049 explicitly flagged that the same wrong read had
been copied into four sibling PostToolUse hooks and left them as a tracked follow-up
(dev-env [#380](https://github.com/brownm09/dev-env/issues/380)):

- **`post-pr-merge-project.py`** (move-to-Done) read `output` for the PR number and only
  matched a `/pull/N` URL — which `gh pr merge` output never contains. It never fired; the
  board move was silently carried by GitHub's *native* "issue closed → Done" project
  automation, which masked the dead hook (and led #369 to misattribute a board move to it).
- **`post-pr-merge-pull.py`** and **`post-pr-merge-reclaim.py`** used `output` as the
  success-marker fallback for the worktree-merge case (`gh pr merge` exits non-zero on local
  cleanup but the remote merge succeeds — issue #275). With `output` always empty and
  `exitCode` defaulting to `-1`, that fallback was dead; only a clean `exitCode==0` merge
  triggered them.
- **`stub-push-archive-reminder.py`** fed `output` to an "obvious error" guard that was
  consequently a no-op (a failed journal push could still arm the archive reminder).

## Decision

1. **Promote the correct read to a shared `claude/scripts/_hookio.py`.**
   `read_command_output(data)` (join `stdout`+`stderr`, fall back to legacy `output`) now
   lives in one module imported by all five hooks — the same sibling-module-on-`sys.path`
   pattern as `_winsubp` ([ADR-007](007-hook-command-invocation.md)). One implementation
   means the field-precedence rule cannot be re-derived divergently. New PostToolUse Bash
   hooks that read command output must `from _hookio import read_command_output` rather than
   touching `tool_response` directly.

2. **`post-pr-merge-project.py` derives the PR number from the command, then the output
   marker.** `gh pr merge` output has no `/pull/N` URL, so the command is the reliable source
   when the PR is named (`gh pr merge 380` or a `/pull/380` URL). The dominant
   `gh pr merge --squash --delete-branch` form names no PR, so extraction falls back to gh's
   success marker (`Squashed and merged pull request #N`, including the cross-repo
   `owner/repo#N` variant) now visible via the shared read. Move-to-Done therefore works from
   the hook itself, independent of GitHub's native automation.

3. **Gate the board move on a confirmed-merge marker, not the exit code.** The project hook
   now proceeds only when the output contains a real merge marker (`Merged` /
   `Squashed and merged` / `Rebased and merged` `pull request`). This is deliberately
   *stricter* than the `exitCode==0 OR marker` predicate that pull/reclaim share: a queued
   `--auto` exits 0 but is not yet merged, and moving its linked issue to Done would be wrong —
   whereas a premature local-`main` pull or `node_modules` reclaim is harmless. The marker is
   printed even from a worktree (before gh's non-zero local-cleanup tail), so this also makes
   move-to-Done work from worktrees, where the old `exitCode != 0` early-exit would have
   suppressed it. (The real payload omits `exitCode` entirely — ADR-049 — so the old check was
   a no-op in practice; the marker gate replaces it with a correct, observable signal.)

4. **`post-pr-merge-pull.py` gains the same pure `is_successful_merge()` predicate as
   reclaim**, plus the safe-exit `try/except` guard its `__main__` was missing (a Hook-Safety
   invariant). `stub-push-archive-reminder.py` gains a pure `has_push_error()` guard. Both
   extractions exist so the revived behavior is unit-testable offline.

5. **Offline, fixture-only tests cover each change:** `test_hookio.py` (the shared read — the
   common fix for all five hooks), `test_post_pr_merge_project.py` (command/marker extraction
   + the `--auto`-safe `merge_succeeded` gate), `test_post_pr_merge_pull.py`
   (`is_successful_merge`), and `test_stub_push_archive_reminder.py` (`has_push_error`).
   Reclaim keeps its existing predicate test and `post-tool-use` its existing test (which
   still resolves the now-imported helper). The live `gh` / `git` calls remain untested per
   the repo's no-subprocess-mock convention.

## Consequences

- All five PostToolUse Bash hooks now read command output correctly: move-to-Done fires from
  `post-pr-merge-project.py` itself (no longer reliant on GitHub's issue-closed automation),
  the worktree-merge `pull`/`reclaim` fallbacks are live, and the journal push-error guard
  works.
- `_hookio` is the canonical read for this repo's PostToolUse hooks; the constraint is now
  enforced in one place instead of copied per hook.
- The `--auto`-safe marker gate keeps the board correct: an issue moves to Done only on a
  *completed* merge, matching the semantics of the native automation it replaces.
- General lesson (continuing ADR-049): a guard's confirmation signal must be the same one the
  action depends on — here the merge marker — not a proxy (`exitCode`) that the payload may
  omit or that a queued `--auto` satisfies without merging.
