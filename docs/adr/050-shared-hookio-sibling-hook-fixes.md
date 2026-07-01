# ADR-050 — Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes

**Date:** 2026-06-21
**Status:** Accepted
**Amended:** 2026-07-01 (three amendments — see Amendment sections below)
**Tags:** hooks, post-tool-use, tool_response, payload, github-project, automation, reliability, dry, usage-snapshot, pr-merge-reminder, gh-pr-view, api-fallback

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
   touching `tool_response` directly. `_hookio` also owns the merge-success-marker detection
   the `post-pr-merge-*` hooks share (`output_has_merge_marker` / `merge_pr_number_from_output`,
   anchored on a verb + `pull request #N` regex), so the marker set lives in one place rather
   than triplicated across the three merge hooks.

2. **`post-pr-merge-project.py` derives the PR number from the command, then the output
   marker.** `gh pr merge` output has no `/pull/N` URL, so the command is the reliable source
   when the PR is named (`gh pr merge 380` or a `/pull/380` URL) — extraction is scoped to the
   merge invocation's own arguments (not the whole command) and prefers the positional number
   over a URL argument, so a `/pull/N` in a `--subject`/`--body` value or a chained sibling
   command cannot hijack it. The dominant
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
   a no-op in practice; the marker gate replaces it with a correct, observable signal.) For
   completeness, `post-pr-merge-{pull,reclaim}.py` default the absent `exitCode` to `-1` (not
   `0`) and rely on the marker fallback — correcting ADR-049's note that the sibling `exitCode`
   reads "default to `0`".

4. **`post-pr-merge-pull.py` gains the same pure `is_successful_merge()` predicate as
   reclaim**, plus the safe-exit `try/except` guard its `__main__` was missing (a Hook-Safety
   invariant). `stub-push-archive-reminder.py` gains a pure `has_push_error()` guard. Both
   extractions exist so the revived behavior is unit-testable offline.

5. **Offline, fixture-only tests cover each change:** `test_hookio.py` (the shared read and merge-marker helpers — the
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

## Amendment 1 (2026-07-01) — `usage-snapshot.py` was a sixth, missed sibling

ADR-049's sweep named four sibling hooks with the wrong-field read (`post-pr-merge-project.py`,
`post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `stub-push-archive-reminder.py`); this ADR
fixed all four plus `post-tool-use.py` itself. **`usage-snapshot.py` was not on that list** —
it fires on the same `gh pr merge` event and its header comment claimed to "mirror
post-pr-merge-project.py," but it never imported `_hookio` and gated solely on
`tool_response.exitCode != 0`, exactly the proxy this ADR's "General lesson" warns against.

**Symptom (dev-env#474):** merging dev-env PR #466 from a worktree hit the documented
worktree-cleanup failure ("'main' is already checked out") — `gh pr merge` exited 1, the
squash-merge itself succeeded, and `post-pr-merge-project.py` correctly moved the linked issue
to Done. But no `### Usage Snapshot (post-merge)` block appeared: the exit-code gate discarded
the event before the hook ever read `stdout`/`stderr` or reached the credential/token logic.

**Fix:** `usage-snapshot.py` now imports `output_has_merge_marker` / `read_command_output` from
`_hookio` and gates on a new pure `merge_confirmed(command, output)` predicate — the same
marker-based check as `post-pr-merge-project.py`'s `merge_succeeded()` — instead of the exit
code. Covered offline in `claude/scripts/tests/test_usage_snapshot.py`. `usage-snapshot.py` is
effectively a sixth hook brought into this ADR's pattern; the count in "Consequences" above
("All five PostToolUse Bash hooks...") should be read as five-plus-this-one going forward.

## Amendment 2 (2026-07-01) — retiring the `exitCode==0 OR marker` predicate in pull/reclaim/tile-checkpoint/reminder

Decision point 3 above and Amendment 1 both framed the `exitCode==0 OR marker` predicate shared
by `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `post-merge-tile-checkpoint.py`, and
`pr-merge-reminder.py` as a *deliberate*, safe choice: "a premature local-`main` pull or
`node_modules` reclaim is harmless." That framing assumed the false positive was limited to
*premature* races — a merge that's about to succeed, just not confirmed yet. It does not hold:
the `exitCode == 0` branch fires on **any** exit-0 command matched as `gh pr merge`, including
one that never attempts a merge at all.

**Symptom (dev-env#485):** running `gh pr merge --help` (checking flag syntax per the CLI
Scripting Checklist's own "run `--help` first" step) exits 0 with no merge marker in its output.
All four hooks misdetected it as a completed merge — firing the journal-update reminder, the
tile-checkpoint reminder, a local-`main` fetch, and a `node_modules` reclaim spawn, for a command
that merged nothing. Reproduced live in the session that filed #485.

**Fix:** converge all four hooks onto the same marker-only gate `post-pr-merge-project.py` and
`usage-snapshot.py` already use — drop the `exit_code` parameter from each hook's
`is_successful_merge()` / `_is_successful_merge_call()` entirely; a real merge always prints the
marker (confirmed via `gh pr merge --help`'s own flag list: no `--quiet`/dry-run flag exists), so
this is a strict improvement with no false-negative regression. Also fixes, as a side effect, the
previously-permitted false positive on a queued `--auto` (exits 0, not yet merged) for these four
hooks — the same fix Amendment 1 and decision point 3 already applied to
`post-pr-merge-project.py` and `usage-snapshot.py`.

Covered offline in the four hooks' `test_*.py` files (added a `gh pr merge --help`-shaped
regression case: exit 0, no marker, must not fire). The "Consequences" and decision-point-3
framing above should now be read as superseded — there is no longer a looser OR-based predicate
anywhere in this hook family; all six hooks (five original + `usage-snapshot.py`) gate solely on
`output_has_merge_marker()`.

## Amendment 3 (2026-07-01) — a live `gh pr view` fallback for when the marker itself is lost

Amendments 1 and 2 (and decision point 3) all assume gh's success marker reliably reaches
`tool_response.stdout`/`stderr` whenever gh actually prints it. That assumption itself does not
always hold.

**Symptom (dev-env#489):** merging dev-env PR #493 hit the same worktree local-cleanup failure
this ADR's Context section describes ("main is already checked out"). gh's own source
(`pkg/cmd/pr/merge/merge.go`, `mergeRun`) confirms the success line prints via `infof()` *before*
`deleteLocalBranch()` (the step that fails) ever runs — so the marker is logically emitted. It did
not, however, survive to the captured Bash-tool result: only the local-cleanup fatal error was
visible, `gh pr view` confirmed the remote merge had genuinely succeeded, and **none** of the six
marker-gated hooks fired (concretely verified, not just "no message seen" — the canonical
`C:/Users/brown/Git/dev-env`'s `HEAD` stayed unchanged, proving `post-pr-merge-pull.py`'s
fast-forward never ran). A control (`echo "MARKER"; exit 1`) proved stdout generally survives a
non-zero exit in this harness, ruling out a blanket "Bash tool drops stdout on failure" explanation
— the loss is specific to `gh pr merge`'s output on this exact failure path, most likely gh (a Go
CLI) buffering stdout to a non-TTY pipe and exiting abruptly right after the failing git subprocess
without flushing that buffer.

**Fix:** two new `_hookio` functions, usable as an explicit fallback *after* the cheap marker check
finds nothing:

- `should_confirm_via_gh(exit_code, output)` — pure predicate: only worth a live check when
  `exit_code != 0` and the marker is absent, so the common non-merge / clean-exit paths (`gh pr
  create`, `git push`, a queued `--auto`, `gh pr merge --help`) never pay a network call.
- `confirm_merge_via_gh(pr_number, repo, cwd)` — shells out to `gh pr view` (an explicit
  `pr_number` + `--repo` when known; otherwise no argument, letting `gh` infer the PR from *cwd*'s
  checked-out branch) and returns the confirmed PR number when `state == "MERGED"`, else `None`.

Wired into `post-pr-merge-project.py` only, for this PR — the one hook of the six with **no other
eventual-consistency backstop** for a missed action (`post-pr-merge-pull.py` has `dev-env-sync`
catching up the canonical on the next prompt, ADR-058; `post-pr-merge-reclaim.py` has the 6-hourly
reclaim routine, ADR-037; the reminder/tile-checkpoint/usage-snapshot hooks are reminders/reports,
not persistent state). Rolling this out to the other five is tracked separately
(dev-env, follow-up issue filed alongside #489's fix) rather than bundled here, because each of
the six runs as an **independent PostToolUse process** — wiring the fallback into all six naively
would mean up to six redundant `gh pr view` calls for a single failed-merge event, with no natural
place to share one result across independent hook invocations without a caching layer.

`should_confirm_via_gh` is covered offline in `test_hookio.py`. `confirm_merge_via_gh` itself is
not (it shells out), per this repo's no-subprocess-mock convention — consistent with every other
live `gh`/`git` call in this hook family.
