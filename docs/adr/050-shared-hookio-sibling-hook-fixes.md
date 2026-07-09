# ADR-050 — Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes

**Date:** 2026-06-21
**Status:** Accepted
**Amended:** 2026-07-01, 2026-07-02, 2026-07-04, 2026-07-08, 2026-07-09 (twenty amendments — see Amendment sections below)
**Tags:** hooks, post-tool-use, tool_response, payload, github-project, automation, reliability, dry, usage-snapshot, pr-merge-reminder, gh-pr-view, api-fallback, message-dispatch, top-level-statement-scan, issue-create, false-positive, command-parsing, heredoc, regex, quote-tracking, canonical-mutate-guard, pre-tool-use, ast, regression-test, allowlist, gh-pr-merge-help, misattribution, live-confirmation-fallback, repo-flag-shorthand, quote-masking, gh-create-help, prose-flag-masking, pr-create, bare-number-masking, args-boundary, truncation

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

## Amendment 4 (2026-07-01) — decoupling create/push message gating from `is_merge` in `pr-merge-reminder.py`

Amendment 2 converged `pr-merge-reminder.py` (and its three siblings) onto a marker-only gate for
the *merge* message. It did not address a separate bug in the same function's dispatch structure:
`is_create` / `is_merge` / `is_push` messages shared **one** early-exit gate keyed on the merge
marker check, so a chained command matching both `is_create` and `is_merge` (e.g.
`gh pr create --fill && gh pr merge --auto`) had its create reminder suppressed whenever the merge
sub-check was incomplete.

**Symptom (dev-env#494):** surfaced as a non-blocking finding during `/review` of PR #491
(Amendment 2's own PR). `if is_merge: if not marker: sys.exit(0)` exited the whole function before
the messages section ever built the `is_create` message, for any chained command where merge
queued (`--auto`) or was probed (`--help`) — both exit 0 but print no marker.

**Fix:** extracted the dispatch into a pure `_build_messages()`, gating each message
independently. A first draft (`exit_ok = is_merge or exit_code == 0`) reproduced the original
truth table except for the reported bug — but `/review` on the resulting PR #500 caught a *new*
regression: `is_merge` is a static text match, true even when create fails and `&&`
short-circuits before merge ever runs, so the draft fired a false "PR created" message for a PR
that was never opened. Final gate: `create_push_ok = exit_code == 0 or merge_ok` — a confirmed
merge (marker present) is independent proof the earlier chain steps already succeeded (merge
cannot complete against a nonexistent PR), valid evidence even when the aggregate exit code is
non-zero (the worktree case, #275, chained with a preceding create); a create failure with no
confirmed merge correctly suppresses everything, matching pre-fix behavior.

Covered offline in `test_pr_merge_reminder.py` (13 new cases: the #494 repro, the regression the
review caught, and the #275-chained case).

**General lesson (continuing Amendment 2's):** the same discipline — gate each side effect on its
own confirming signal, not a proxy or a signal borrowed from an unrelated branch — applies to more
than the merge marker itself; the *dispatch structure* around multiple signals sharing one
function needs the same scrutiny. A shared early-exit is itself a proxy for "everything in this
function is OK," which breaks the moment two independently-gated outcomes share one command.

## Amendment 5 (2026-07-02) — extracting `scan_top_level` into `_hookio.py` fixes `post-tool-use.py`'s unanchored create-detection

Unlike Amendments 1-4 (all about the output-reading fix — the field-precedence bug ADR-049
identified and its knock-on effects), this amendment extends `_hookio.py`'s shared surface with an
unrelated command-*parsing* engine, for the same practical reason as every prior amendment: one
shared implementation instead of drifting copies across sibling hooks.

`pr-merge-reminder.py`'s stack-based statement parser (`_scan_top_level` / `_find_heredoc_end`) was
already settled, correct infrastructure for its own `gh pr merge` / `gh pr create` / `git push`
detection. `post-tool-use.py` (the project-board-add hook) never had that infrastructure at all: it
detected `gh issue create` / `gh pr create` with a plain, unanchored
`re.search(r"\bgh\s+issue\s+create\b", command)` / `re.search(r"\bgh\s+pr\s+create\b", command)`
over the **entire raw Bash command string** — the exact naive-substring-match failure mode
`_scan_top_level` was built to prevent, just never ported to this hook.

**Symptom (dev-env#499):** reproduced four times in the dev-env#494 fix session (2026-07-01) alone —
a `git commit -m "..."` message, a `grep -E '...'` pattern argument, a `gh project item-edit --text
"..."` value, and a `gh pr comment --body "..."` all quoted the example command `gh pr create --fill
&& gh pr merge --auto` while describing the #494 fix, and each spuriously matched `is_pr_create`. No
harmful mutation occurred in any of the four (no GitHub URL was present in the unrelated command's
output, so `add_to_project` was never reached) — the impact was a misleading stderr advisory each
time — but a false-positive command whose output *happens* to contain an unrelated GitHub URL could
add the wrong item to the project board.

**Fix:** promoted `_scan_top_level` (renamed `scan_top_level`, now public since it is a cross-hook
API) and `_find_heredoc_end` (stays private — nothing outside `scan_top_level` calls it directly)
from `pr-merge-reminder.py` into `_hookio.py`, unchanged in behavior. `pr-merge-reminder.py` now
imports `scan_top_level` rather than defining its own copy; its three `is_pr_merge_command` /
`is_pr_create_command` / `is_git_push_command` wrappers are otherwise untouched, and its previously
sole consumer of `from collections.abc import Callable` moved with the function, so that import was
removed. `post-tool-use.py` gains its own `is_issue_create_command` / `is_pr_create_command`
wrappers (new regexes + anchored check functions, mirroring `pr-merge-reminder.py`'s existing style
down to the harmless-but-consistent optional `cd ... &&` prefix tolerance) built on the shared
`scan_top_level` engine, replacing the two unanchored `re.search` calls in `main()`.

**Scope decision — only the engine is shared, not the wrapper functions.** `pr-merge-reminder.py`'s
`is_pr_create_command` and `post-tool-use.py`'s new `is_pr_create_command` are separate, near-
identical 2-line functions, not one hoisted implementation. This was a deliberate choice, not an
oversight: the two hooks' create-detection needs already diverge (`post-tool-use.py` needs an
issue-vs-PR union; `pr-merge-reminder.py` needs PR-only), so a single shared wrapper would need
immediate parameterization neither caller asks for today — premature generalization with a real
coupling cost (a future divergence in one hook's needs would ripple into the other) and no present
duplication of the part that actually matters (the ~130-line parser, which *is* shared). This
mirrors the existing precedent of `effective_merge_dir` (shared) coexisting with
`_effective_push_dir` (deliberately not shared) in this exact module.

**A related but narrower implementation, left out of scope.**
`pre-tool-use-canonical-mutate-guard.py` also has heredoc/quote-adjacent handling
(`_strip_heredoc_command_subs` + a `_SEGMENT_SPLIT` regex), but it is not equivalent protection to
`scan_top_level`: it targets one specific `$(cat <<[-]['"]MARKER['"] ... MARKER)` idiom, and its
segment splitter has no quote-tracking at all (a `git commit -m "text && rm -rf /"`-shaped command is
mis-split by that hook's splitter today — it merely doesn't matter yet because "text" isn't a
mutating verb it scans for). Converging it onto `scan_top_level` would need that hook to also expose
per-segment iteration with cwd-redirect detection, not just a boolean reducer — a real API mismatch,
not simple reuse. Flagged as a candidate follow-up (tracked via a spawned background task after this
PR merges), not bundled here.

*Resolved in Amendment 7 (dev-env#511):* the API mismatch was fixed, not accepted — `scan_top_level`'s
segmenting core was extracted into a standalone `split_top_level`, letting the guard consume the
segment list directly while `scan_top_level` itself stays a thin wrapper for its original two callers.

**Coverage:** `test_hookio.py` gains direct engine-level tests for `scan_top_level` (anchored-match
semantics; non-splitting inside single/double quotes, `$()` subshells, and heredoc bodies; splitting
on `&&`, `;`, `||`, and newline). `test_post_tool_use.py` gains tests for the two new wrapper
functions, including the four dev-env#499 false-positive reproductions (heredoc-embedded commit
body, quoted commit message, grep pattern argument, `--text` field value) for **both**
`gh pr create` and `gh issue create`, plus subshell/double-quote/cd-prefix/chained-with-merge cases
mirroring the ones already proven in `test_pr_merge_reminder.py`, plus an end-to-end pair driving the
real hook over stdin (`subprocess.run([sys.executable, ...])`, the same pattern
`test_worktree_path_check.py` and `test_canonical_mutate_guard.py` already use) that pins the
pre-existing `exit_code != 0` gate immediately downstream of the detection swap: identical
command/config/output differing only in `exitCode` yields exit 0 (silent) vs. exit 2 (the "no
GitHub URL found" advisory) — proving detection fired correctly *and* the gate still short-circuits,
without either branch ever invoking a live `gh`/network call. `test_pr_merge_reminder.py` is
unchanged and continues to pass in full, now exercising `scan_top_level` through its `_hookio` import
rather than a local definition — its four subshell/quote/heredoc-specific cases
(`test_create_inside_subshell_not_matched`, `test_create_inside_double_quotes_not_matched`,
`test_create_in_heredoc_not_matched`, `test_push_inside_subshell_not_matched`) are the canaries most
likely to catch any behavior drift from the relocation, and all four still pass unchanged.

**General lesson (continuing Amendments 1 and 2's):** a fix scoped to one hook in this family is a
standing invitation to check whether every sibling hook that does the same *kind* of thing (read
command output; detect a specific CLI invocation) has the same fix — the sweep in Amendment 1 found
one missed sibling for the output-reading fix; this amendment is that same sweep for the
statement-scanning fix, one release later.

## Amendment 6 (2026-07-02) — completing the sweep: `usage-snapshot.py` and `post-pr-merge-project.py`

Amendment 5 promoted `scan_top_level` out of `pr-merge-reminder.py` and wired it into
`post-tool-use.py`, but did not touch two other hooks that had independently reinvented the same
parser before Amendment 5 ever existed. `/review` of Amendment 5's own PR ([#508](https://github.com/brownm09/dev-env/pull/508))
surfaced them:

- **`usage-snapshot.py`** carried its own `_find_heredoc_end` plus a merge-only
  `_scan_top_level(command: str) -> bool`, hardcoded to `_check_merge_stmt` rather than
  parameterized.
- **`post-pr-merge-project.py`** carried the identical shape, with a comment that admitted it
  outright: `# _scan_top_level and helpers below are duplicated from pr-merge-reminder.py.`

**Symptom (dev-env#509):** no functional bug — both local copies handled `;`, `\n`, `&&`, `||`,
single/double quotes, `$()` subshells, and heredoc bodies identically to the by-then-shared
`_hookio.scan_top_level`. The issue was purely the drift `scan_top_level` was created to end: two
more hand-maintained copies of a ~100-line stack-based parser, invisible to Amendment 5's sweep
because neither hook was on ADR-049's original four-sibling list (both were fixed later — in
Amendment 1 and the base decision, respectively — for the unrelated output-reading bug) and
Amendment 5 scoped its own sweep to `post-tool-use.py` alone.

**Fix:** for each of the two files — import `scan_top_level` from `_hookio` in place of the local
`_scan_top_level` definition; delete the now-redundant local `_find_heredoc_end`; change the call
site from `_scan_top_level(command)` to `scan_top_level(command, _check_merge_stmt)`, using the
`_check_merge_stmt` predicate each file already defined for its own single-purpose regex match.
Behavior-preserving by construction — the two local copies and the shared engine were verified
character-for-character equivalent in control flow before this PR, so this is deletion of dead
duplication, not a rewrite. `post-pr-merge-project.py` also drops its now-inaccurate "duplicated
from pr-merge-reminder.py" comment.

**Coverage:** `test_usage_snapshot.py` and `test_post_pr_merge_project.py` exercise only the public
`merge_confirmed()` / `merge_succeeded()` and extraction helpers, never the private
`_scan_top_level` / `_find_heredoc_end` names directly, so both suites pass unchanged — confirming
the refactor is behavior-preserving rather than requiring test updates. `test_hookio.py`'s existing
`scan_top_level` coverage (Amendment 5) now backs all four hooks that need top-level statement
scanning (`pr-merge-reminder.py`, `post-tool-use.py`, `usage-snapshot.py`,
`post-pr-merge-project.py`) from one implementation.

**General lesson (continuing Amendments 1 and 5's):** a sweep is only as complete as the list it
started from. Amendment 5's sweep was scoped to "hooks needing `gh pr create` detection like
`pr-merge-reminder.py`," which correctly caught `post-tool-use.py` but missed two hooks whose need
was narrower (merge detection only), so their duplication didn't look like the same shape at a
glance. Generalized: when consolidating a cross-hook utility, grep for the *engine* (the parser
body itself), not just the *call pattern* that motivated the current fix — the two would have
surfaced together.

## Amendment 7 (2026-07-02) — converging `pre-tool-use-canonical-mutate-guard.py` onto a shared `split_top_level` engine (dev-env#511)

Amendment 5's "related but narrower implementation, left out of scope" section flagged that
`pre-tool-use-canonical-mutate-guard.py` had its own heredoc/quote-adjacent handling that was not
equivalent protection to `scan_top_level`, and that converging it "would need that hook to also
expose per-segment iteration with cwd-redirect detection, not just a boolean reducer — a real API
mismatch, not simple reuse." This amendment resolves that mismatch rather than accepting it as a
permanent limitation: the fix is not "make the guard call `scan_top_level`" but "extract the
segment-yielding engine `scan_top_level` was already built on, and let the guard consume segments
directly."

**Concrete impact — two false positives, verified by hand-tracing the pre-fix code (dev-env#511):**

1. **Quote-mis-split.** `git log --grep="foo && git checkout -b evil"` — the guard's own
   `_SEGMENT_SPLIT` regex (`&&|\|\||;|\n|\|`) had no quote-tracking, so it split *inside* the quoted
   `--grep` value, producing a fake segment `git checkout -b evil"` that the verb classifier flagged
   as a `checkout`. A harmless `git log` search would have been blocked in a canonical checkout.
2. **Bare heredoc body.** `git status <<EOF` / `git commit --amend` (body) / `EOF` — the guard's
   `_HEREDOC_CATSUB_RE` only recognized the `$(cat <<MARKER...)` command-substitution idiom (the
   dev-env#481 fix). A *bare* heredoc (no command-substitution wrapper) was untouched by it; after
   the regex splitter's `\n`-split, the body line `git commit --amend` became its own segment and was
   misclassified as a real invocation — same failure class as #481, just the untested
   non-command-substitution variant.

**Fix:**

1. **Extract `split_top_level(command, *, split_pipe: bool = False) -> list[str]`** from
   `scan_top_level`'s internals in `_hookio.py`. It is the exact same stack-based
   quote/subshell/heredoc-aware parser, just returning the segment list instead of reducing it to a
   boolean via `check_fn`. `scan_top_level` becomes a thin wrapper —
   `any(check_fn(seg) for seg in split_top_level(command))` — behavior-preserving for its existing
   two callers (`pr-merge-reminder.py`, `post-tool-use.py`), both of which continue to call it
   unchanged and pass their full test suites with zero modification.
2. **`split_pipe` is opt-in, defaulting `False`.** `scan_top_level`'s two existing callers never need
   pipe-awareness (`gh pr merge`/`gh pr create`/`gh issue create`/`git push` detection has no
   pipe-input idiom); `pre-tool-use-canonical-mutate-guard.py` passes `split_pipe=True` because a
   mutating git invocation can read piped stdin (e.g. `echo msg | git commit -F -`, an idiom the
   guard's original `_SEGMENT_SPLIT` regex already split on). The flag keeps the behavior change
   scoped to exactly the one caller that needs it — zero risk to the other two.
3. **The guard imports `split_top_level(cmd, split_pipe=True)`** in both `classify()` and
   `_has_override()`, replacing `_SEGMENT_SPLIT.split(cmd)`. `_HEREDOC_CATSUB_RE` and
   `_strip_heredoc_command_subs()` are deleted entirely — the shared engine's `$()`-subshell and
   heredoc opacity subsumes the narrow regex, fixing both false positives above as a side effect of
   convergence rather than as separate patches. The `cd`-whole-command-scope and
   `git -C`/`--git-dir`-single-segment-skip logic (the guard's own domain-specific classification,
   not something `split_top_level` needs to know about) is unchanged — it now just runs over
   `split_top_level`'s segment list instead of the regex splitter's.

**Coverage:** `test_hookio.py` gains 13 new `split_top_level`-specific tests (segment order/whitespace
contract, quote-tracking for `&&`/`|`, opt-in pipe-splitting including the `||`-vs-lone-`|`
distinction, bare and command-substitution heredoc opacity, the unterminated-quote fail-permissive
contract) alongside the pre-existing `scan_top_level` tests, all of which continue to pass unchanged
against the new implementation. `test_canonical_mutate_guard.py` gains 5 new tests: the two
false-positive fixes above (pure `classify()` plus one end-to-end `main()` subprocess test proving
the fix reaches the real hook process, not just the pure function), a pipe-splits-and-classifies
test, and a pipe-inside-quotes-does-not-split test. All 28 pre-existing tests in that file continue
to pass unchanged. `test_pr_merge_reminder.py` and `test_post_tool_use.py` — the two other
`scan_top_level` consumers — were re-run as a regression check and pass unchanged (neither file was
edited).

**`/review` caught an equal-and-opposite regression before merge.** The fix above closes two false
positives but, in doing so, changed a load-bearing assumption every classification helper in the
guard was written against: a segment used to always be one physical line (the old regex splitter split
on every `\n`), and `split_top_level`'s whole point is to let a segment span multiple physical lines
when a heredoc/`$(...)` span is part of it. Two of the guard's regexes were never updated for that:

- `_GIT_INVOCATION_RE = ^git(?:\.exe)?\s+(.*)$` has no `re.DOTALL`, so `.*` cannot cross the first
  embedded `\n` and `$` (not `re.MULTILINE`) requires the true end of string — on a multi-line
  segment the match fails outright. A real `git commit -m "$(cat <<'EOF' ... EOF)"` (this repo's own
  documented commit-message idiom, used throughout this very CLAUDE.md) was silently classified as
  **non-mutating** — the guard's most common trigger, silently unguarded.
- `_REDIRECT_RE.search(seg)` is unanchored and its `(?:^|\s)` alternation treats an embedded `\n`
  exactly like a space, so a commit whose heredoc *body* merely mentioned `git -C /somewhere` as
  prose was wrongly read as "this git invocation redirects to another repo" and skipped —
  `classify()`'s `continue` branch fired on a segment that was never actually redirected at all.

Both are false negatives in the direction that matters most for a collision-prevention guard: a real
mutating command slips through unblocked. Fixed with a single new helper, `_first_line(segment)`,
used by both call sites — the git invocation and its flags only ever appear on a segment's own first
physical line; anything after an embedded newline is heredoc/command-substitution body data. A
tempting alternative — add `re.DOTALL` to `_GIT_INVOCATION_RE` instead — was considered and rejected:
it fixes the match, but then `rest.split()` tokenizes the *entire* segment including heredoc body
words, re-exposing them to the `stash pop`/`apply` and `checkout --` token scans (a read-only
`git stash list` whose heredoc body happens to contain the word "apply" would then wrongly block).
`_first_line()` fixes both call sites without that tradeoff, verified by construction against a
`git stash list <<EOF\nplease apply this later\nEOF` case that must stay allowed.

Also gained, in the same fix, a small performance correction the same review pass raised: `main()`
previously called `classify(cmd)` then `_has_override(cmd)`, each independently re-running
`split_top_level` over the same command string. Both functions now accept an optional `segments`
parameter — `main()` computes it once and passes it to both; every existing direct caller (all of
`test_canonical_mutate_guard.py`) keeps calling either function with just `cmd` unchanged.

**Coverage (follow-up):** five more tests in `test_canonical_mutate_guard.py` — the two real-command
false negatives above (heredoc command-substitution and bare-heredoc commit messages), the
heredoc-body-mentions-`-C` false skip, the DOTALL-would-have-been-wrong `stash list` regression guard,
and an end-to-end `main()` subprocess proof that `git commit -m "$(cat <<'EOF' ...)"` is blocked for
real. 38 tests total in that file, all passing.

**General lesson (continuing Amendment 5's):** "a real API mismatch, not simple reuse" is a reason to
design the right shared primitive, not a reason to accept parallel implementations permanently. The
mismatch here was that `scan_top_level` exposed a reducer when a second caller needed a sequence —
the fix was to expose the sequence and make the reducer a trivial wrapper over it, the same shape as
`effective_merge_dir` remaining unshared beside `_effective_push_dir` (Amendment 5's own
scope-decision precedent) when two callers' needs *don't* converge. Here they did. A second lesson,
specific to this amendment: generalizing a data shape (single segment → possibly-multi-line segment)
is not free — every downstream consumer that pattern-matches on the old shape's implicit invariants
needs the same audit the shape change itself got. `/review`'s adversarial pass, not the hand-written
test suite, is what caught this; the test suite only exercised the *inverse* direction (heredoc body
text that must NOT trigger) because that was the bug being fixed, not the one this fix could
introduce.

## Amendment 8 (2026-07-02) — rolling `confirm_merge_via_gh` out to the five remaining marker-gated hooks (dev-env#504)

Amendment 3 added a live `gh pr view` fallback (`should_confirm_via_gh` / `confirm_merge_via_gh` in
`_hookio.py`) for when gh's merge-success marker doesn't survive to a PostToolUse hook's captured
output — but wired it into `post-pr-merge-project.py` **only**, per that PR's own stated rationale: it
is the one hook of the six with no other eventual-consistency backstop for a missed action
(dev-env#498 later found even that one hook's own confirmation inconclusive to observe — a separate,
still-open question this amendment does not resolve). dev-env#504 tracked the rollout to the other
five as an explicit follow-up rather than leaving it implied. This amendment closes that gap.

**Evidence the gap was live, not theoretical:** the identical worktree-cleanup failure
(`fatal: 'main' is already checked out`) recurred on dev-env PRs #491, #493, and #512, and — new since
#504 was filed — on a **different repo entirely**, career-playbook PR #635. Since these hooks are
global (fire "for every repo without a hook-config.json opt-in requirement," per `usage-snapshot.py`'s
own docstring), the career-playbook occurrence confirms the gap is a property of the shared hook
architecture, not something specific to dev-env's own worktree/board setup.

**Fix:** wired `should_confirm_via_gh` / `confirm_merge_via_gh` into the remaining five hooks —
`usage-snapshot.py`, `post-merge-tile-checkpoint.py`, `post-pr-merge-pull.py`,
`post-pr-merge-reclaim.py`, `pr-merge-reminder.py` — mirroring `post-pr-merge-project.py`'s existing
pattern: when the marker-based check fails but the command was still a genuine `gh pr merge` with a
non-zero exit code, fall back to a live confirmation before conceding no merge happened. Per #504's own
"Scope" section, this takes the simpler of the two named options — accepting up to six independent
(redundant) `gh pr view` calls per failed-merge event rather than building a cross-hook shared-result
cache, since each PostToolUse hook is a separate process with no natural place to share one result
short of new infrastructure that six calls to a cheap, read-only `gh` command don't justify.

Four of the five hooks (`usage-snapshot.py`, `post-merge-tile-checkpoint.py`, `post-pr-merge-pull.py`,
`post-pr-merge-reclaim.py`) gate on a simple 2-argument marker predicate
(`merge_confirmed(command, output)` / `is_successful_merge(command, output)`) that existing tests call
directly and offline. The fallback was added only in each hook's own `main()`, never inside those
tested predicates, so no existing test's behavior or coverage changed.

`pr-merge-reminder.py` needed a different shape: its `_build_messages()` helper — which computes
`merge_ok` from the marker alone — is itself directly unit-tested with ~20 synthetic `(command,
exit_code, output)` combinations, several of which have a non-zero `exit_code` and no marker (e.g. the
dev-env#494 "create fails, merge never ran" case). Naively calling `confirm_merge_via_gh` from inside
`_build_messages` would have made those existing tests shell out to a **real** `gh pr view` subprocess
call — one of them stayed accidentally safe only because its synthetic `cwd` doesn't exist on disk
(`subprocess.run(cwd=...)` raises before `gh` ever runs, caught by `confirm_merge_via_gh`'s broad
exception handler). Relying on that accident would have been fragile, and violates the repo's own
"avoids subprocess mocks by never reaching the live boundary in a pure-helper test" convention. Instead,
`_build_messages` gained a `live_confirmed: bool | None = None` parameter: `None` (the default, and
every pre-existing test call) leaves `merge_ok` exactly as the marker-only check already computed it;
`True`/`False` authoritatively overrides it. `main()` — never `_build_messages` — decides whether to
attempt the live check and calls `confirm_merge_via_gh` itself, before the `_build_messages` call. This
keeps the live subprocess boundary exactly where every sibling hook already keeps it: in `main()`,
untested by convention, never inside a function synthetic inputs can reach.

All six hooks resolve the `gh pr view` cwd via `effective_merge_dir(command, cwd)` rather than the raw
session `cwd` — a `cd <other-repo> && gh pr merge` chain must confirm against the repo the merge
actually targeted, matching the existing cd-chain-scoping convention `pr-merge-reminder.py` and
`post-pr-merge-pull.py` already use for their own repo/dir resolution (ADR-067). `post-pr-merge-pull.py`
and `post-pr-merge-reclaim.py` keep their pre-existing raw-`cwd` uses (`extract_repo`'s cd-chain
resolution; `_spawn_reclaim`'s `--protect-cwd`) untouched — those are semantically distinct from "which
directory should `gh pr view` run from," so the new `effective_merge_dir` call is additive, not a
replacement.

**Coverage:** all five hooks' existing marker-predicate tests pass unchanged — no existing test was
modified or reinterpreted, only new code paths added around them in each `main()`.
`pr-merge-reminder.py`'s suite gained three new tests exercising `live_confirmed=True` / `False` /
omitted directly — pure, no subprocess — bringing that file's suite to 45 passing tests. The live
`confirm_merge_via_gh` calls themselves remain untested across all six hooks, per this ADR's and
`post-pr-merge-project.py`'s own established convention (it shells out to `gh pr view`; the repo avoids
subprocess mocks).

**What this amendment does not resolve:** dev-env#498's question — whether even
`post-pr-merge-project.py`'s original, longer-lived fallback is provably firing, versus GitHub's native
project-automation masking the result — stands independent of this rollout and is not touched here.

**General lesson (continuing Amendments 1, 5, and 6's):** a fix scoped to "the one hook that needs it
most" is a deliberate, load-bearing decision (Amendment 3 was explicit about this), but it leaves a
tracked-but-open gap for every sibling hook until a follow-up closes it — dev-env#504 existed
specifically so that gap wasn't just implied by re-reading Amendment 3's rationale later. A second,
narrower lesson specific to this amendment: wiring a live-call fallback into a function that already has
direct synthetic-input test coverage requires checking every existing test case against the new code
path before assuming "add the fallback the same way as the reference hook" is a safe copy — one of
`pr-merge-reminder.py`'s own pre-existing tests would have started shelling out to `gh` for real had the
fallback been added inside `_build_messages` rather than gated in `main()`.

## Amendment 9 (2026-07-02) — converging the 3 remaining marker-gated hooks' command-shape check onto `scan_top_level` (dev-env#529)

Amendments 5 and 6 converged `pr-merge-reminder.py`, `post-tool-use.py`, `usage-snapshot.py`, and
`post-pr-merge-project.py` onto the shared `scan_top_level` engine for command-shape detection. Three
siblings sharing the identical `is_successful_merge(command, output)` predicate shape —
`post-merge-tile-checkpoint.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py` — were left on the
original crude `if "gh pr merge" not in command: return False` substring test, missed by both sweeps for
the same reason Amendment 6 named its own two misses: their need (merge detection only) didn't visually
match the `gh pr create`-detection shape either sweep was scoped to look for.

**Concrete consequence, surfaced during dev-env#504's rollout review ([PR #528](https://github.com/brownm09/dev-env/pull/528)):**
before #528 wired `confirm_merge_via_gh` into these three hooks, a false substring match — literal
`gh pr merge` text inside a heredoc body or a quoted argument, not a real invocation — combined with a
missing `exitCode` (defaulting to `-1`, per this ADR's own Context section noting the real payload often
omits the field entirely) and no success marker was harmless: the hook just exited 0 after
`is_successful_merge` returned `False`. After #528, that same false match now reaches
`should_confirm_via_gh(-1, output)` — `True`, since `-1 != 0` and no marker is present — and pays a real
`gh pr view` subprocess call before exiting 0.

**Symptom, live-reproduced during this very fix's own session (dev-env#529):** a Bash command that wrote
and ran a Python fixture script containing the literal text `"gh pr merge --squash --delete-branch"`
inside a heredoc (test data for this fix's own regression tests, below) false-triggered the *canonical*,
not-yet-fixed `post-merge-tile-checkpoint.py`: `should_confirm_via_gh` saw the substring-matched command,
the absent `exitCode`, and no marker in the fixture script's own stdout, so it proceeded to a live
`confirm_merge_via_gh` call — which found a real `MERGED` PR against the session's actual checked-out
branch (unrelated to the Bash command that triggered the check) and fired the tile-checkpoint reminder for
a merge that did not happen in that Bash call at all. The blast radius: a misattributed reminder, plus a
paid `gh pr view` network round-trip, for a command that never invoked `gh` — reproducing the "Concrete
consequence" paragraph above firsthand rather than only by inspection.

**Fix:** for each of the three files — added a local `_MERGE_RE` / `_check_merge_stmt` pair, identical to
the one already defined in `usage-snapshot.py` / `pr-merge-reminder.py` / `post-pr-merge-project.py`
(`(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b`, anchored via `.match()` on the lstripped token); replaced both
occurrences of `if "gh pr merge" not in command` — one inside each file's `is_successful_merge()`, one
inside `main()`'s live-confirmation fallback gate — with `if not scan_top_level(command,
_check_merge_stmt)`. Behavior-preserving for every pre-existing test case (a real `gh pr merge`
invocation, bare or `--help`-flagged, is still matched or rejected identically); the only behavior change
is that a `gh pr merge`-shaped substring inside a heredoc body, a quoted argument, or a `$()` subshell no
longer counts as an invocation. A repo-wide grep for `"gh pr merge" not in command` after the fix returns
zero matches in `claude/scripts/*.py` — the pattern survives only in this ADR's own history and in the
new tests' explanatory comments describing the pre-fix behavior.

**Coverage:** each file's existing `is_successful_merge()` test suite — marker-detection only before this
amendment, per the issue's own framing — gains three new cases mirroring the dev-env#499 false-positive
shapes `test_hookio.py`'s own `scan_top_level` suite already covers at the engine level: a heredoc body, a
double-quoted argument (the `&&`-inside-quotes shape that would otherwise carve out a second top-level
segment starting with `gh pr merge`), and a `$()` subshell. Each new case pairs the false-match command
with an output string that DOES carry a genuine success marker, isolating the command-shape check from the
marker check — the old substring test would have proceeded past a false match straight to the (passing)
marker check and returned `True`. Verified directly against the extracted pre-fix predicate logic before
writing the fix (re-running the three new command/output pairs through a standalone
`if "gh pr merge" not in command: return False` implementation) that all three genuinely reproduce the old
bug (`True`), confirming the new tests are real regressions, not vacuously-true assertions. 26 total tests
across the three files (7 + 12 + 7), up from 17 (4 + 9 + 4) pre-amendment.

**General lesson (continuing Amendments 1 and 6's):** a sweep is only as complete as the list it started
from, a third time. Amendment 6 already drew this lesson from missing `usage-snapshot.py` and
`post-pr-merge-project.py`; this amendment is the same lesson applied to a *different* narrow-need shape
(marker-gated merge detection with no `gh pr create`/`git push` sibling need) that neither Amendment 5's
nor Amendment 6's sweep enumerated. A durable mechanical proxy going forward: grep the whole
`claude/scripts/` tree for the literal string `"gh pr merge" not in command` (and its `"gh pr create"` /
`"git push"` analogs, still live in `stub-push-archive-reminder.py`'s unrelated `git push`-only check,
left untouched here as out of scope for this issue) before declaring any future `scan_top_level`
convergence sweep complete — a textual grep catches what a conceptual "which hooks need this kind of
detection" sweep can miss, as it did three times running.

## Amendment 10 (2026-07-02) — converging `stub-push-archive-reminder.py`'s command-shape check onto `scan_top_level` (dev-env#532)

Amendment 9's own "General lesson" named the exact gap this amendment closes: its durable mechanical grep
proxy explicitly listed `stub-push-archive-reminder.py`'s `git push`-only check as "left untouched here as
out of scope for this issue." This amendment is that named follow-up, not a new discovery — a repo-wide
grep for `"gh pr create" not in command` and `"gh pr merge" not in command` immediately before this fix
confirmed both were already zero matches in `claude/scripts/*.py` (the `"gh pr merge"` shape closed by
Amendment 9; the `"gh pr create"` shape appears to have never existed in that literal form —
`post-tool-use.py`'s pre-Amendment-5 bug was a differently-shaped unanchored `re.search`, not this literal
substring test, so the zero-match count isn't evidence any amendment specifically "closed" it); `"git push"
not in command` had exactly one match, this file's line 82.

**Shape difference from Amendment 9's three files:** `post-merge-tile-checkpoint.py`,
`post-pr-merge-pull.py`, and `post-pr-merge-reclaim.py` each already exposed their substring check as a
separate, pre-tested pure predicate (`is_successful_merge()`), so Amendment 9 only needed to swap the
check's implementation. `stub-push-archive-reminder.py`'s `if "git push" not in command` lived inline in
`main()` with no equivalent — per this repo's own convention that `main()`'s stdin/exit-code I/O is
untested by design (`test_stub_push_archive_reminder.py`'s own docstring already noted `main()` is out of
scope), there was nothing pure to attach regression coverage to. This amendment therefore also extracts a
new named predicate, `is_git_push_command(command)`, mirroring the role `is_successful_merge()` plays in
the three Amendment 9 hooks.

**Not new regex design — a port.** `pr-merge-reminder.py` already defines the exact predicate this file
needs: `_PUSH_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?git\s+push\b")`, `_check_push_stmt`, and a wrapper
`is_git_push_command(command)` (added under Amendment 5, for its own `git push`-triggered journal-update
reminder). `stub-push-archive-reminder.py` gains an identical, independently-defined trio — consistent
with this ADR's established scope decision (Amendment 5: "only the engine is shared, not the wrapper
functions") — rather than a newly-authored regex.

**Fix:** added the `_PUSH_RE` / `_check_push_stmt` / `is_git_push_command()` trio to
`stub-push-archive-reminder.py`; replaced the sole occurrence of `if "git push" not in command:` in
`main()` with `if not is_git_push_command(command):`. Behavior-preserving for every real invocation (a
bare or `cd`-prefixed `git push` still matches identically); the only behavior change is that `git push`
text inside a heredoc body, a quoted argument, or a `$()` subshell no longer counts as an invocation. A
repo-wide grep for `"git push" not in command` after the fix returns zero matches in
`claude/scripts/*.py` — combined with the pre-fix state confirmed above, all three `"X" not in command`
analogs Amendment 9's "General lesson" named are now closed.

**Coverage:** `test_stub_push_archive_reminder.py` gains four new cases: a bare top-level `git push`
sanity baseline (this predicate had no pre-existing positive-match test to inherit, unlike the three
Amendment 9 hooks' already-tested `is_successful_merge()`), plus the same three dev-env#499
false-positive shapes Amendment 9 covered — a heredoc body, a double-quoted `&&`-embedded argument, and a
`$()` subshell. Unlike Amendment 9's tests, these don't pair the false-match command with a
marker-bearing output to isolate the command-shape check from a second gate — `is_git_push_command()` is
a single-argument, single-purpose predicate with no downstream marker check to isolate from.

**General lesson (continuing Amendments 1, 6, and 9's):** a sweep's own written-down "out of scope" note
is a checklist, not a closed door — Amendment 9 named this file and this fix by hand; this amendment is
the mechanical act of doing what was already written down. The three-times-repeated lesson from
Amendments 1/6/9 (a sweep is only as complete as the list it started from) has a corollary: when a sweep
*does* write down what it deliberately left out, that note is the cheapest possible seed for the next
sweep — cheaper than re-deriving the gap from a fresh grep.

## Amendment 11 (2026-07-02) — automating Amendment 9's durable mechanical grep proxy into a regression test (dev-env#534)

Amendment 9's closing "General lesson" proposed a specific, concrete mitigation for the pattern all of
Amendments 1, 6, and 9 kept re-discovering — a sweep is only as complete as the list it started from:
"grep the whole `claude/scripts/` tree for the literal string `\"gh pr merge\" not in command` (and its
`\"gh pr create\"` / `\"git push\"` analogs...) before declaring any future `scan_top_level` convergence
sweep complete." That proxy was itself never more than prose in this ADR — a manual step a human or a
future session had to remember to run. Amendment 10 is direct proof the reminder alone wasn't durable
enough on its own: it required a *second* named issue (dev-env#532) and PR to actually close the gap
Amendment 9 had already written down by hand. This amendment converts the reminder from a checklist item
into `claude/scripts/tests/test_no_crude_command_substring_checks.py`, an enforced regression test.

**Why AST, not grep.** A grep-based version would face exactly the problem PR #530's own diff had to
navigate carefully by hand: the fix's explanatory comments and test docstrings *quote* the crude pattern's
literal text to describe the bug it replaced (see this ADR's own Amendment 9/10 prose above, and the
`test_post_merge_tile_checkpoint.py` / `test_post_pr_merge_pull.py` / `test_post_pr_merge_reclaim.py`
comment blocks PR #530 added), so a naive text search for `"gh pr merge" not in command` matches its own
documentation. `ast.parse` sidesteps this categorically rather than approximately: a comment is stripped
by the tokenizer before the parser ever produces a node for it, and a docstring's string constant is never
itself an operand of a `Compare` node unless it is literally live code performing the comparison. The new
test's `test_ignores_pr530_style_explanatory_comment` case proves this directly — it feeds PR #530's own
verbatim comment text through both a naive substring grep (which reports a false positive, as predicted)
and the AST detector (which correctly reports none).

**Generalized beyond the three named literals.** Amendment 10's own repo-wide grep, run immediately before
that fix, checked specifically for `"gh pr create" not in command`, `"gh pr merge" not in command`, and
`"git push" not in command` — the three literals Amendment 9 named — and correctly found zero, zero, and
one match respectively. The new test's detector instead flags *any* string-literal `in`/`not in` check
against a variable named `command`, because an AST walk costs no more to make generic than to hardcode to
three strings. Running the generalized detector against the current tree surfaced **two previously
untracked instances of the identical shape**, both in `stub-push-archive-reminder.py`:
`"engineering-journal" not in command` and `"engineering_journal" not in command` (checking whether the
raw command references the engineering-journal repo by name, not whether it invokes a specific CLI
subcommand). Neither has ever been named by any prior ADR text or issue — a grep scoped to the three
historical literals, including Amendment 10's own, would not have found them, because they were never on
anyone's list of "known" instances to check for. This is the concrete value of grepping for the *shape* of
the bug rather than its previously-observed instances, one level more thoroughly than Amendment 6's own
"grep for the engine, not the call pattern" lesson already argued for.

**The allowlist is two-sided, not a one-way exemption list.** Since converging
`stub-push-archive-reminder.py`'s two `engineering-journal` checks onto `scan_top_level` is a
hook-behavior change and out of scope for a test-only PR, the new test carries a small
`_KNOWN_EXCEPTIONS` set (matched on filename + literal text, not line number) so it passes today instead
of failing on introduction against known, pre-existing debt. That set is checked in both directions: a
live offense absent from it fails the test (the actual regression-prevention purpose), and an entry
present in it with no matching live offense *also* fails the test, so a future fix can't leave a stale
exception behind to rot silently. This is not a hypothetical concern invented for symmetry — it is exactly
what would have happened to this same test had it existed a day earlier: dev-env#532/PR #533 converged
`stub-push-archive-reminder.py`'s `"git push" not in command` check (Amendment 10, above) while this
amendment's own test was being authored, so a `("stub-push-archive-reminder.py", "git push")` exception
drafted before that merge would have gone stale within the same PR's lifetime. The two-sided check is what
makes that self-correcting rather than silently permissive.

**Follow-up, not fixed here.** The two `engineering-journal` checks are tracked via a `spawn_task` tile and
a linked follow-up issue at merge time (per [ADR-046](046-post-merge-followup-tiles.md)'s "durable
tracking still needs an issue, the tile and the issue are complementary" rule), not converged in this PR.

**Coverage:** the new test's own detector is self-tested against ten synthetic fixtures (a live check; a
fourth literal outside the ADR's original three, proving genericity; a full-line comment; a trailing
inline comment; a docstring mention; unrelated variable names `cmd`/`commands`; an unrelated membership
check on a different variable — the real `"error:" in lower` shape from this same file's own
`has_push_error()`; reverse operand order; an offense as the second link of a chained comparison, not the
first, verified empirically via `ast.dump` before the assertion was written; and the PR #530
calibration case above), plus the repo-wide gate itself (11 cases total). All existing suites
(`test_hookio.py`, `test_stub_push_archive_reminder.py`, and the item-1 syntax check over every
`claude/scripts/*.py` file) were re-run unchanged and continue to pass — no hook runtime behavior is
touched by this amendment.

**General lesson (continuing Amendments 1, 6, and 9's):** a written-down mechanical proxy is exactly as
missable as a conceptual sweep until it stops being something a session has to remember to run. Amendment
9 wrote the grep down; Amendment 10 is proof that writing it down was not, by itself, sufficient to make
it happen without a second dedicated issue. The durable form of a "remember to grep for X" note is not a
better-worded note — it is the grep, committed, enforced, and running on every future change to this
directory.

## Amendment 12 (2026-07-02) — converging `stub-push-archive-reminder.py`'s engineering-journal reference checks onto `scan_top_level` (dev-env#539)

Amendment 11's own "Follow-up, not fixed here" note named this exact gap: the two
`engineering-journal`/`engineering_journal` checks it surfaced were deliberately left as
`_KNOWN_EXCEPTIONS` debt, tracked via a `spawn_task` tile and a linked follow-up issue, because
converging them was a hook-behavior change out of scope for that test-only PR. This amendment is that
named follow-up.

**Shape difference from Amendments 9/10's `scan_top_level` uses.** Every prior `scan_top_level`
convergence in this ADR — `is_pr_merge_command`, `is_pr_create_command`, `is_git_push_command`,
`is_successful_merge`'s internal merge check — detects whether a command **invokes** a specific CLI verb
(`gh pr merge`, `git push`, ...). The check itself sits at the very start of its governing statement, so
anchoring `check_fn` with `<regex>.match(token.lstrip())` — matching only from position 0 of a top-level
segment — is sufficient: a CLI-invocation phrase embedded mid-segment (inside a heredoc body, a quoted
argument, or a `$()` subshell that a single un-split segment absorbs) never appears at that segment's own
start. `references_engineering_journal()` detects something structurally different: not an invocation,
but a **directory-argument value** — `engineering-journal` (or `engineering_journal`) as the target of a
`cd` or `git -C`, which by construction never sits at position 0 of its segment (it follows the `cd
`/`git -C ` prefix). A naive "check each top-level segment for substring containment" — the seemingly
obvious analog of the old whole-command `in` check, just scoped to `scan_top_level`'s segments — would
**not** have fixed the bug: a segment like `git commit -m "sync notes with engineering-journal"` is
exactly one top-level segment (the quoted `-m` argument has no top-level `&&`/`;` to split on), and the
literal text is still `in` that segment string even after splitting. Verified directly before writing the
fix: re-running each of the three new false-positive commands (heredoc, quoted argument, subshell)
through a standalone `any(literal in seg for seg in split_top_level(command))` implementation confirmed
all three still incorrectly match, ruling out that simpler design and confirming the fix needs an
anchored-prefix predicate, not just a re-scoped substring test. The fix instead anchors `_EJ_REF_RE` to
the same `.match()`-from-segment-start discipline the CLI-invocation checks use, but keyed on the
*prefix* that can legitimately carry a directory argument (`cd `/`git -C `) rather than on a CLI verb.

**Not a port, unlike Amendment 10.** Amendment 10 could lift `is_git_push_command`'s predicate wholesale
from `pr-merge-reminder.py`, which already needed identical `git push` detection. No sibling hook has
ever needed "does this command reference the engineering-journal repo by directory argument" detection —
`pr-merge-reminder.py`'s own `_EJ_REPO_FRAGMENT` check (`_EJ_REPO_FRAGMENT in cwd.replace("\\", "/")`) is
a same-named but unrelated shape: it substring-tests an already-resolved OS directory path (`cwd`), never
raw shell command text, so it was never subject to the heredoc/quote/subshell false-positive class in the
first place and needed no `scan_top_level` anchoring of its own. `_EJ_REF_RE` /
`_check_engineering_journal_ref` / `references_engineering_journal()` are new, purpose-built for this
file.

**Fix:** added the `_EJ_REF_RE` / `_check_engineering_journal_ref` / `references_engineering_journal()`
trio to `stub-push-archive-reminder.py`, immediately below the existing `is_git_push_command()` trio;
replaced the sole occurrence of `if "engineering-journal" not in command and "engineering_journal" not in
command:` in `main()` with `if not references_engineering_journal(command):`; updated the module
docstring's four-condition summary so condition 2 also documents the `scan_top_level` anchoring. `_EJ_REF_RE
= re.compile(r"(?:cd|git\s+-C)\s+\S*engineering[-_]journal\S*")` — no `re.IGNORECASE`, preserving the old
check's case-sensitivity exactly (Amendment 10's own framing: behavior-preserving except for the named
false-positive shapes). A repo-wide grep for `"engineering-journal" not in command` / `"engineering_journal"
not in command` after the fix returns zero matches in `claude/scripts/*.py`.

**Allowlist closure — the self-enforcing step named in the issue.** With both checks converged,
`test_no_crude_command_substring_checks.py`'s `_KNOWN_EXCEPTIONS` set (Amendment 11) no longer has a live
offense to cover; its own two-sided gate (a listed exception with no matching live offense fails the test
as "stale") means simply forgetting to remove the two entries would have failed the suite immediately,
rather than silently leaving stale debt behind. `_KNOWN_EXCEPTIONS` is now `set()` — left as an
explicitly-typed empty set rather than deleted, so the next genuinely-new crude check (should one ever
reappear) has an established, documented place to land. Confirmed: the repo-wide gate now reports "47
files ... 0 known exception(s), 0 unexpected, 0 stale, 0 duplicated" — the crude-command-substring-check
class named across Amendments 5, 6, 9, 10, and 11 has zero known instances left anywhere in
`claude/scripts/*.py`.

**Coverage:** `test_stub_push_archive_reminder.py` gains seven new cases, mirroring Amendment 10's own
four-case shape but expanded for this check's two independent axes (two accepted prefixes — `cd` and
`git -C` — and two accepted spellings — hyphenated and underscored): a `cd`-prefix positive baseline, a
`git -C`-prefix positive baseline, an underscore-spelling positive baseline, an unrelated-repo negative
sanity baseline, and the same three dev-env#499 false-positive shapes Amendment 10 covered (a heredoc
body, a double-quoted argument — embedding both spellings and an internal `&&` to also re-prove the
quote-tracking non-split guarantee in the same case — and a `$()` subshell). 17 total tests in the file,
up from 10 pre-amendment.

**General lesson (continuing Amendments 1, 6, 9, 10, and 11's):** `scan_top_level` is an engine for
finding top-level statement boundaries, not a complete false-positive fix by itself — the anchoring
strategy layered on top of it has to match the *shape* of what's being detected. A CLI-invocation check
can safely anchor to "segment start" because a genuine invocation always begins its own statement. A
directory-argument-value check cannot reuse that same anchor unmodified — the value never begins the
statement, the governing keyword (`cd`/`-C`) does — so the anchor has to move to "does the segment start
with a known argument-taking prefix, followed by a value containing the target." Reusing `scan_top_level`
(the segmentation engine) without reasoning through this distinction would have produced a predicate that
*looked* converged — it calls `scan_top_level`, it has a regex, it has tests — while remaining just as
false-positive-prone as the crude check it replaced, for exactly the shapes this ADR exists to close.

## Amendment 13 (2026-07-04) — `is_merge_help_only`: excluding non-mutating `gh pr merge` invocations from the live-confirmation fallback (dev-env#557)

**The gap.** Amendment 8 (and the sixth-hook sweep of Amendment 6) rolled a live `gh pr view`
confirmation out to every marker-gated hook: when `should_confirm_via_gh(exit_code, output)` says the
cheap marker check found nothing and the exit code signals a possible loss, the hook pays a live `gh
pr view` call rather than silently conceding no merge happened (the dev-env#489 lost-marker case).
That fallback is correct for a *genuine* unresolved merge, but `gh pr merge --help` (or `-h`) is not
one — it can *categorically never* attempt a real merge, yet it textually satisfies every
`is_pr_merge_command` / `_check_merge_stmt` predicate in this hook family (it *is* a `gh pr merge`
invocation, syntactically), prints no success marker, and typically exits non-zero or leaves the
payload's `exitCode` at its `-1` default. The fallback it triggers calls `confirm_merge_via_gh(None,
"", cwd)` with no explicit PR number, which resolves via `gh pr view` scoped to **cwd's currently
checked-out branch** — if that branch has *any* merged PR on record, the hook attributes that merge to
the `--help` invocation. Live incident: running `gh pr merge --help` purely to check flag semantics
during a lifting-logbook session moved an unrelated already-Done issue's project-board item, because
three PostToolUse siblings (`post-pr-merge-project.py`, `post-merge-tile-checkpoint.py`,
`usage-snapshot.py`) all independently hit this path. No lasting harm that time (the misattributed
issue was already Done), but the mechanism is general: `post-pr-merge-pull.py` and
`post-pr-merge-reclaim.py` share the identical shape (a silent local-main fetch / an unscheduled
disk-reclaim sweep), and the two `PreToolUse` siblings (`pre-merge-findings-gate.py`,
`pre-merge-numbering-check.py`) would evaluate or potentially block the `--help` call against an
unrelated PR's review-findings or numbering-collision state.

**Not a `scan_top_level` shape (unlike Amendments 5–12).** Every prior fix in this ADR converged a
command-*detection* check — "does this command invoke `gh pr merge`/`gh pr create`/`git push`, as
opposed to merely mentioning that text in a heredoc/quote/subshell." `is_merge_help_only` answers a
different question about a command *already known* to invoke `gh pr merge`: "of the top-level `gh pr
merge` segments this command contains, are they *all* help-only?" It still depends on
`split_top_level` for the same reason every other predicate in this file does (a heredoc/quoted/
subshell mention of `--help` must not count, and a chained `gh pr merge --help && gh pr merge 380
--squash` must not have its real second half suppressed by the harmless first), but it composes on
top of the existing `is_pr_merge_command`-style gate rather than replacing it — callers keep their own
`scan_top_level`-anchored merge-detection check first, then add `is_merge_help_only` as a second,
narrower filter.

**Fix.** Added `is_merge_help_only(command)` to `_hookio.py`, next to `should_confirm_via_gh`: finds
every top-level segment (`split_top_level`) whose own first physical line (`_first_line()` — a
second, independently-defined but identically-purposed helper of the same name already exists in
`pre-tool-use-canonical-mutate-guard.py`; not shared/imported, each module stays self-contained)
matches `gh(?:\.exe)?\s+pr\s+merge\b` (case-insensitive, anchored at the lstripped segment start —
mirroring every sibling `_check_merge_stmt`'s `.match(token.lstrip())` convention, so a `gh pr merge`
phrase inside an *earlier* quoted argument on the same segment is never mistaken for a genuine
invocation); returns `False` if there are no such segments (callers already gate on their own
merge-detection check first); otherwise returns `True` only if **every** matched segment's first line
also carries a standalone `--help`/`-h` token (`_HELP_FLAG_RE`, whitespace/start/end-bounded so
`--helpful` or a `-help-me` branch name can't false-match). Wired into all 8 files the incident named,
each as an early exit treating a help-only command exactly like "not a merge command at all":

- `post-pr-merge-project.py`, `usage-snapshot.py`, `post-pr-merge-pull.py`,
  `post-pr-merge-reclaim.py` — the guard sits right after each file's existing `if not
  scan_top_level(command, _check_merge_stmt): sys.exit(0)` line, before the `exit_code = ...` read.
- `post-merge-tile-checkpoint.py` — same placement, inside its `is_successful_merge()`-driven early
  exit's own inline `scan_top_level` fallback check.
- `pr-merge-reminder.py` — different shape: its live-confirmation attempt is a single `if (is_merge
  and not _is_successful_merge_call(output) and should_confirm_via_gh(exit_code, output)):`
  condition in `main()`; the fix adds `and not is_merge_help_only(command)` into that same
  condition, so a chained `gh pr create --fill && gh pr merge --help` (the dev-env#494 chaining
  precedent this file already handles) still gets its create reminder while the help-shaped merge
  half is correctly excluded from the live-confirmation attempt.
- `pre-merge-findings-gate.py`, `pre-merge-numbering-check.py` — `PreToolUse` siblings; the guard is
  `if is_merge_help_only(command): sys.exit(0)` immediately after each file's existing `if not
  is_pr_merge_command(command): sys.exit(0)` gate, so a `--help` command is never evaluated against
  (or blocked on) an unrelated PR's review findings or numbering-collision state.

No change to `should_confirm_via_gh`'s deliberate `-1` exit-code default (Amendment 3; still favors a
live-confirmation attempt over silently missing a real merge for every *other* shape), and no change
to any file's own `_check_merge_stmt`/`is_pr_merge_command` regex.

**Coverage.** `test_hookio.py` gains 13 new cases for `is_merge_help_only` itself (57 total, up from
44): bare `--help`/`-h`, a real merge with a PR number, a bare current-branch merge with no `--help`
anywhere, no merge invocation at all, a chained help-then-real-merge (must stay `False` — the real
merge is never suppressed), a chained two-help-invocations command (`True` — all segments qualify), a
heredoc mention of `--help` text not affecting a real merge elsewhere, the mirror case (heredoc
mentions a *non-help* `gh pr merge` while the only real segment is help-only — still `True`), a
quoted-argument mention of `gh pr merge` not counted as a second segment, `--help` not confused with a
similarly-named flag, a `cd`-prefixed `--help`, and case-insensitivity. Each of the 8 wired-in files
gains a lightweight pure-composition test (rather than a `main()`-driving subprocess test): for the 5
PostToolUse files whose new guard sits inside `main()` behind an already-tested sibling predicate
(`merge_succeeded`/`is_successful_merge`/`merge_confirmed`), the test pins that predicate returns
`False` for the exact `--help` shape `is_merge_help_only` returns `True` for (proving the two
predicates compose the way the guard depends on), plus that an unresolved *non-help* real merge
leaves `is_merge_help_only` `False` (the live-confirmation fallback stays reachable, unchanged).
`post-pr-merge-project.py`'s own `main()` requires a `.claude/hook-config.json` fixture to reach the
guard at all — building that fixture just to re-prove an already-covered predicate would have broken
the family's otherwise-uniform test shape for no proportional benefit, so it gets the same
lightweight composition test as its four PostToolUse siblings. `pr-merge-reminder.py`'s guard is
inline in `main()`, not its own function, so its test directly re-evaluates the same boolean
expression `main()` computes. The two PreToolUse files (already `is_pr_merge_command`-pure-tested)
each gain a two-line composition test plus, for `pre-merge-findings-gate.py`, a new case in its
existing `test-merge-findings-gate.sh` behavioral self-test proving the guard fires with **no**
`MERGE_GATE_TEST_JSON` seam set at all — if the guard did not fire, that shape would fall through to
a live, unstubbed `gh pr view` call. 234 tests total across the touched files, 0 failures.

**General lesson.** Not every fix in this hook family is a `scan_top_level`-detection convergence —
some incidents (like this one) are about a command *correctly* detected as "yes, this invokes `gh pr
merge`" but whose *consequences* still need excluding from a downstream fallback that assumes
detection implies mutation risk. Amendments 5–12 all answered "is this text a genuine invocation, or
just a heredoc/quote/subshell mention of one?" — this amendment instead answers "given a genuine
invocation, does its own flag set make it categorically incapable of the side effect the fallback
exists to catch?" The two questions look similar (both dispatch on segment content) but are
independent: a predicate answering the first correctly (as `is_pr_merge_command` already did here)
provides no guarantee about the second, and conflating them would have either re-introduced the
`--help` false positive (if the second question were skipped) or risked suppressing a real merge (if
the two were merged into one over-eager check instead of two independently composed, narrowly-scoped
predicates).

## Amendment 14 (2026-07-08) — recognizing gh's `-R` shorthand for `--repo` across the repo-flag-checking hooks (dev-env#616)

**The gap.** Every repo-flag check this ADR's hooks rely on — `extract_repo_from_command()`
(`post-pr-merge-project.py`), `_effective_merge_repo()` (`pr-merge-reminder.py`, dev-env#470),
`_devenv_merge_pr()` (`posttooluse-inert-advisory.py`), and `extract_repo()` (`post-pr-merge-pull.py`,
ADR-067) — matched only the long `--repo owner/repo` flag. None recognized `gh`'s documented `-R`
shorthand for the same flag, so a `-R owner/repo` merge command fell through exactly as if no repo
flag were present at all, silently resolving against the fallback (cwd's own
`.claude/hook-config.json`, or — for `pr-merge-reminder.py`/`post-pr-merge-pull.py` — cwd/cd-chain
resolution) instead of the command's actual target repo.

**Symptom.** Running `gh pr merge 611 -R brownm09/dev-env --squash` from a session whose cwd was
`lifting-logbook` (merging dev-env PR #611, closing dev-env#606) made `post-pr-merge-project.py`
report `Issue #608 moved to Done` — lifting-logbook#608, not any dev-env issue.
`extract_repo_from_command` returned `None` for the `-R`-flagged command, so `main()` fell through to
`repo = config.get("repo", "")`, resolving cwd's own (lifting-logbook's) project-board config; from
there `get_pr_body(611, repo="brownm09/lifting-logbook")` fetched a real, unrelated, already-merged
lifting-logbook PR that coincidentally shared the number 611, and its `Closes #608` moved that repo's
issue. Harmless this time only because lifting-logbook#608's board item was already Done — a different
pairing of coincidental PR numbers and issue states could have silently reverted real in-progress work
on an unrelated repo's board.

**Scope — four hooks needed the fix, two already had it.** The issue's own text named
`post-pr-merge-project.py` (the incident) and flagged `_effective_merge_repo` in
`pr-merge-reminder.py` as a likely identical-shape sibling, per `extract_repo_from_command`'s own
docstring claim to mirror its resolution order. Auditing every `--repo`-flag check in
`claude/scripts/*.py` — not just hooks with a `_REPO_FLAG_RE`-named constant, since one of the four
turned out to be an unnamed inline `re.search()` call that a name-scoped grep would have missed —
found two more instances of the identical gap:

- `posttooluse-inert-advisory.py`'s `_REPO_FLAG_RE = re.compile(r"--repo[=\s]+(\S+)")`, feeding
  `_devenv_merge_pr()`'s repo-identity check (ADR-053/ADR-055's Stop-hook safety net — the same
  misattribution risk as the incident, just surfaced as an advisory rather than a live board mutation).
- `post-pr-merge-pull.py`'s `extract_repo()`, whose `--repo` check is an inline
  `re.search(r"--repo\s+(...)", command)` with no named constant at all (ADR-067) — feeding which
  local clone gets fast-forwarded, so the same gap here silently redirects a local `git fetch`/`pull`
  to the wrong repo's clone instead of a project-board mutation.

Two siblings already handled `-R` correctly and needed no change: `post-tool-use.py`'s
`extract_repo_flag()` (dev-env#542 — a prior, narrower fix for the `gh issue/pr create`-side project-add
hook that was never swept to this merge-side family, the same "a fix scoped to one hook is a standing
invitation to check every sibling" lesson Amendments 1, 6, 9, 10, and 12 already drew) and
`stop-tile-enumeration-gate.py`'s own `_REPO_FLAG_RE` (ADR-088).

**Fix.** Extended each of the four gap sites' regex to accept `-R` as an alternate flag spelling,
minimally and in the same shape the existing regex already used per file — `(?:--repo|-R)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)`
for the three that anchored on a strict `owner/repo` character class, `(?:--repo|-R)[=\s]+(\S+)` for
`posttooluse-inert-advisory.py`'s looser form. Deliberately did **not** port `post-tool-use.py`'s more
sophisticated `extract_repo_flag()` (which also normalizes a full URL form and an `=`-joined form,
dev-env#542/#544) into the other four files — that hook's richer parsing exists to feed `gh issue/pr
create`'s cross-repo config resolution, a different call site with different inputs than these four
merge-side hooks; broadening scope to match its full behavior is unrelated to the `-R`-recognition gap
this issue reported and would be scope creep, not a fix.

**Coverage.** Each fixed file's existing pure-helper suite gained one `-R`-shorthand case, pinned
against the exact incident shape (`gh pr merge 611 -R brownm09/dev-env --squash`) resolving identically
to the equivalent `--repo` form already covered: `test_post_pr_merge_project.py`
(`test_repo_from_repo_flag_short_form`, 27 tests total, up from 26), `test_pr_merge_reminder.py`
(`test_merge_repo_short_flag_form`, 49 total, up from 48), `test_posttooluse_inert_advisory.py` (two
new assertions inside the existing `test_devenv_merge_pr_direct`, one resolving to dev-env and one to a
different repo — mirroring the pre-existing `--repo` pair exactly, 31 total unchanged in count),
and `test_post_pr_merge_pull.py` (`test_extract_repo_short_flag_form`, 15 total, up from 14). The live
`gh`/`git` calls each file's `main()` eventually feeds remain untested per this ADR's established
no-subprocess-mock convention, unchanged by this amendment.

**General lesson (continuing Amendments 1, 6, 9, 10, and 12's).** A sweep scoped to a *named* pattern
(`_REPO_FLAG_RE`) is still narrower than a sweep scoped to the pattern's *shape* — `post-pr-merge-pull.py`'s
inline, unnamed `re.search(r"--repo\s+...", command)` carried the identical bug but would have survived
a grep for the constant name alone. This is the same lesson Amendment 11 already drew for crude
substring checks ("grep for the shape of the bug rather than its previously-observed instances") and
Amendment 6 drew for the `scan_top_level` engine ("grep for the engine, not the call pattern") — applied
here a third time to a third kind of drifted duplication. A second, narrower lesson specific to this
amendment: dev-env#542's fix for `post-tool-use.py` predates this issue by roughly a month and
already solved the identical `-R`-recognition problem in one file, but nothing connected that fix to
the sibling hooks sharing the same *narrower* `--repo`-only regex shape at the time — a durable
mechanical proxy analogous to Amendment 11's AST-based crude-substring-check gate (grep
`claude/scripts/*.py` for any `--repo`-matching regex lacking a `-R`/`(?:--repo|-R)` alternate on
introduction) would have caught this class the same day dev-env#542 landed, rather than a month later
via a live misattribution incident.

## Amendment 15 (2026-07-08) — masking quoted spans before the repo-flag regex runs (dev-env#626)

**The gap.** Amendment 14's `(?<!\S)` lookbehind requires `--repo`/`-R` to start a standalone token, which
stops a mid-word match (`xx-R`) but does not make the check quote-aware: a `--subject`/`--body` value like
`"see -R other/repo for context"` still contains a legitimately space-separated `-R other/repo` substring.
The character before `-R` there is whitespace too — just whitespace *inside a quoted string*, not between
top-level tokens — so the lookbehind cannot tell the two apart. This was flagged as a non-blocking `/review`
finding on Amendment 14's own PR (#623) and filed as dev-env#626 rather than expanded in that PR, since a
real fix needs quote-aware masking, not another regex tweak, and affects the same four call sites Amendment
14 already touched.

**Not newly introduced.** As dev-env#626 itself notes, this is a pre-existing limitation of the original
`--repo`-only regex, not a regression from Amendment 14 — that amendment only marginally widened practical
exposure by adding a second, shorter trigger literal (`-R`, 2 characters) alongside `--repo` (6 characters).

**Fix — `mask_quoted_spans` in `_hookio.py`.** A new function, independent of `split_top_level`/`scan_top_level`
(deliberately not a refactor of either — see the rationale below), that returns its input with every
single-/double-quoted span, `$()` subshell, and heredoc body replaced by a same-length run of `#` (newlines
preserved). Each of the four repo-flag call sites now runs its regex against a masked copy of the relevant
string instead of the raw one:

- `post-pr-merge-project.py::extract_repo_from_command` — masks the `_MERGE_ARGS_RE`-scoped `args` before
  `_REPO_FLAG_RE.search()`.
- `pr-merge-reminder.py::_effective_merge_repo` — masks the whole `command` (this function has no args-scoping
  of its own — it searches the raw command directly).
- `posttooluse-inert-advisory.py::_devenv_merge_pr` — masks `args` before `_REPO_FLAG_RE.search()`.
- `post-pr-merge-pull.py::extract_repo` — masks the whole `command` before the first (repo-flag) `re.search()`
  call. This file's check was the most exposed of the four (no args-scoping, no named regex constant at all —
  Amendment 14's own account already called this out).

**Masking is scoped to exactly the vulnerable regex, never a fallback that legitimately matches a quoted
value.** Two of the four files have a second regex whose match is reused elsewhere and must stay on the
*unmasked* string:

- `extract_repo_from_command`'s PR-URL fallback (`_PR_URL_REPO_RE.search(args)`) stays unmasked — an existing
  test (`test_repo_from_cross_repo_url`) passes a *quoted* PR URL (`gh pr merge
  "https://github.com/brownm09/dev-env/pull/554"`) that must keep resolving; masking that string too would
  have silently broken it.
- `_devenv_merge_pr`'s `url_m = _DEVENV_PR_URL_RE.search(args)` stays unmasked — its match is reused for the
  PR-number fallback later in the same function, and a quoted URL is a legitimate shape there too.

`post-tool-use.py`'s `extract_repo_flag` was in scope for this fix, per dev-env#626's own "arguably five"
framing. This amendment deliberately does not touch it: its regex —
`(?:--repo|-R)[\s=]+(\"[^\"]+\"|'[^']+'|\S+)` — already supports a legitimately *quoted* flag value as part
of its own capture group (`--repo "owner/repo"`), a shape none of the other four files ever supported.
Blanket-masking before that regex would blind its own legitimate quoted-value case, not just the false-match
one. Its docstring already discloses the narrower residual gap ("an unusual construction where a quoted
--title/--body value itself contains literal '--repo' text before the real flag could match the wrong
occurrence") as an accepted, conservative-by-design tradeoff, further bounded because its only caller
(`_sibling_repo_config`) never acts on an extracted repo name unless a real local sibling checkout's own
`hook-config.json` independently confirms it — a mitigating backstop none of the other four files have. Fixing
it would need a differently-shaped algorithm (e.g. matching the flag's own quoted-or-bare value directly,
rather than masking-then-searching), which is a distinct piece of work, not a copy of this fix.

**Design — an independent walker, not a `split_top_level` refactor.** `mask_quoted_spans` mirrors
`split_top_level`'s state transitions (the same four states: top/single/double/subshell) and reuses the
already-shared `_find_heredoc_end`, but `split_top_level` itself is untouched — zero lines changed, zero risk
to its ~30 existing tests and multiple callers (`pr-merge-reminder.py`, `post-tool-use.py`,
`pre-tool-use-canonical-mutate-guard.py`). This was a deliberate call, not an oversight: `split_top_level` has
been hardened across Amendments 5 and 7, and Amendment 7's own postmortem — "`/review`'s adversarial pass, not
the hand-written test suite, is what caught this... because that was the bug being fixed, not the one this fix
could introduce" — is a direct caution against generalizing a heavily-tested function's internals for an
unrelated need without very deliberate scrutiny. This mirrors Amendment 5's own scope decision ("only the
engine is shared, not the wrapper functions") one level up: the true shared atom (`_find_heredoc_end`) is
reused; the two higher-level walks (segment-splitting vs. span-masking) stay independent, since forcing them
through one shared reducer would have meant either generalizing `split_top_level`'s return shape (the exact
kind of change Amendment 7 shows can hide a subtle regression) or building new plumbing neither of its two
existing callers needs.

**The tradeoff, and how it's covered.** Because the two walks are independent, nothing in the type system
guarantees they never drift apart on what counts as "inside a quote/subshell/heredoc" for some future input
shape. Rather than leave that as a prose "keep these in sync" comment, `test_hookio.py` gains
`test_mask_quoted_spans_agrees_with_split_top_level`: across four fixtures (a decoy `&&` inside double quotes,
single quotes, a `$()` subshell, and a heredoc body, each followed by a real top-level `&&`), it asserts
`split_top_level` never splits on the decoy while `mask_quoted_spans` masks it, and that the real, later `&&`
is untouched by both. This is Amendment 11's own lesson applied to a second case: "a written 'keep these in
sync' reminder is exactly as missable as the bug it guards against... the durable form is a running test."

**Coverage.** `test_hookio.py` gains 12 direct `mask_quoted_spans` tests (no-op passthrough; single- and
double-quoted spans masked; an escaped quote inside double quotes does not end the span early; `$()` subshell
content masked; a `$()` nested inside `"..."` closes as one contiguous span rather than letting the inner
subshell's own characters end the outer quote early; a bare heredoc body masked; a `$(cat <<'EOF'...)`
heredoc-in-subshell masked as one span; newlines preserved inside a masked multi-line span; an unterminated
double quote and an unterminated `$()` subshell both mask their tail without crashing, mirroring
`split_top_level`'s existing fail-permissive contract for the identical case; and a real, unquoted `--repo`
flag survives byte-for-byte alongside a masked quoted decoy in the same command) plus the cross-consistency
test above (13 new tests total; `test_hookio.py` is now at 70, up from 57 — Amendment 14 touched the other
four files, not this one, so 57 was this file's count coming into this amendment). Each of the four fixed
files' existing suite gains two new cases: the
exact dev-env#626 repro (a quoted `--subject`/`--body` decoy resolves to `None`/falls back correctly, not the
decoy's repo) and a real `--repo`/`-R` flag resolving correctly alongside an irrelevant quoted decoy in the
same command — except `posttooluse-inert-advisory.py`, whose existing `test_devenv_merge_pr_direct` already
bundles every resolution-shape case into one function (this file's own established convention, unlike the
other three's one-test-per-case style); the two new dev-env#626 cases were added as additional assertions
inside that same function rather than as new top-level functions, matching the file's precedent. All five
existing suites were re-run in full and pass unchanged — the existing quoted-PR-URL and
`--repo`-flag-precedence-over-quoted-URL tests in particular confirm the masking scope decision above is
correct, not just intended.

**Out of scope, filed as a follow-up (not fixed here).** Auditing the `_REPO_FLAG_RE` family surfaced three
more sites with a related but structurally distinct gap, none named in dev-env#626 itself:

- `pre-merge-findings-gate.py`'s `_parse_merge_target` tokenizes via naive `tail.split()` rather than a regex
  at all — a `--subject "... -R other/repo ..."` value hijacks it the same way, just via whitespace
  tokenization instead of an unanchored regex match.
- `pre-auto-merge-checkpoint-gate.py` dynamically imports `_parse_merge_target` from
  `pre-merge-findings-gate.py` (`importlib`/`exec_module`, same function object) — automatically covered by
  whatever fix lands there; not a second site.
- `stop-tile-enumeration-gate.py`'s own `_REPO_FLAG_RE` (`(?:--repo|-R)(?:=|\s+)(?P<repo>[^\s/]+/[^\s]+)`,
  ADR-088) never received Amendment 14's `(?<!\S)` lookbehind at all (it already recognized `-R` before PR
  #623, so that PR never touched it) — a strictly larger, pre-existing gap than dev-env#626 itself, on top of
  the same quote-unawareness.
- The PR-URL-regex analog in all four files fixed here (`_PR_URL_REPO_RE`/`_PR_URL_RE`/equivalents) has the
  same quoted-value blind spot for a URL-shaped decoy instead of a flag-shaped one — dev-env#626's own scope is
  specifically the "`_REPO_FLAG_RE` family."

Filed as a single consolidated follow-up issue rather than expanded here, per this ADR's own established
precedent (Amendment 11's "Follow-up, not fixed here" for the `engineering-journal` checks, later closed by
Amendment 12) — each of the four bullets above is a distinct code shape needing its own fix design, not a copy
of this amendment's masking approach.

**General lesson (continuing Amendments 5, 7, and 11's).** Sharing "the engine" does not always mean sharing
one function — Amendment 5 already drew the line at wrapper functions with genuinely divergent call shapes;
this amendment draws it one level deeper, at a heavily-tested function's *internals*, and closes the resulting
gap (two independent implementations of the same opacity rules) with an enforced consistency test rather than
either an unsafe refactor or an unenforced comment. A second, narrower lesson: auditing a named bug class
(`_REPO_FLAG_RE`) for a fix inevitably surfaces adjacent, structurally different instances of a *related* bug
class — the right response is the same "grep for the shape, note what's out of scope, file it" discipline this
ADR has used since Amendment 9, not scope creep into a single oversized PR.

## Amendment 16 (2026-07-08) — generalizing `is_merge_help_only` into `is_help_only`; fixing the identical `gh issue/pr create --help` false-positive in `post-tool-use.py` (dev-env#636)

**The gap.** Amendment 13 fixed `gh pr merge --help`'s false-positive across eight files, but the
identical shape of bug survived, unfixed, in the two functions Amendment 5 introduced for
`post-tool-use.py`'s own create-side detection: `is_issue_create_command()` / `is_pr_create_command()`.
Running `gh issue create --help` (or `gh pr create --help`) — exactly the step this repo's own CLI
Scripting Checklist prescribes ("run `<command> --help` first to confirm flag names and syntax" before
writing any `gh` automation script) — textually satisfies `is_issue_create_command`'s `_ISSUE_CREATE_RE`
match, so `main()` proceeds as though a real issue had been created. Since `--help` output contains no
GitHub URL, this reaches the de-silenced "successful create but no URL found" branch Amendment 5's own
predecessor (dev-env#377/#499) intentionally surfaces rather than swallows, and the hook emits a
blocking (exit 2) "Issue created but no GitHub URL found in output" message for a command that created
nothing.

**Live reproduction.** Confirmed firsthand during this fix's own research session: `gh issue create
--help` (chained after an unrelated command, as a normal exploratory `gh ... --help` check per the CLI
Scripting Checklist) produced exactly this message.

**Why generalize rather than duplicate `is_merge_help_only`'s logic a second time.**
`is_merge_help_only`'s existing implementation was already hardcoded to `_GH_MERGE_INVOCATION_RE` —
copying its segment-scan-plus-`all()` body into `post-tool-use.py` for a second and third invocation
regex (`_ISSUE_CREATE_RE`, `_PR_CREATE_RE`) would have been the exact kind of near-verbatim duplication
this ADR exists to prevent (Amendment 6's "grep for the engine, not the call pattern" lesson, applied
one level up: this time the engine is the *help-only* check itself, not the top-level-statement scanner
it's built on). `is_help_only(command, invocation_re)` extracts the reusable core; `is_merge_help_only`
becomes a thin wrapper (`is_help_only(command, _GH_MERGE_INVOCATION_RE)`) with its pre-existing name,
signature, and behavior completely unchanged — every one of Amendment 13's 13 `is_merge_help_only` tests
continues to pass unmodified against the refactored implementation.

**Fix.** `post-tool-use.py` gains `is_issue_create_help_only(command)` / `is_pr_create_help_only(command)`,
each a thin `is_help_only(command, <its own existing _ISSUE_CREATE_RE / _PR_CREATE_RE>)` wrapper —
reusing the exact regex `is_issue_create_command` / `is_pr_create_command` already match against, so the
help-only check can never diverge from the create-detection it's meant to override. `main()` downgrades
each create-flag *independently*:

```python
if is_issue_create and is_issue_create_help_only(command):
    is_issue_create = False
if is_pr_create and is_pr_create_help_only(command):
    is_pr_create = False
```

rather than a single combined bail-out — mirroring Amendment 4's "gate each side effect on its own
confirming signal" lesson: a real `gh pr create` chained with an unrelated help-only `gh issue create`
(or vice versa) must still be processed as the create it actually is, not suppressed because *some*
create-shaped segment in the same command happened to be a harmless flag check.

**Coverage.** `test_hookio.py` gains four new tests exercising `is_help_only` directly with a non-merge
`invocation_re` (a `gh issue create` pattern), proving the extraction is genuinely generic rather than
merge-specific, alongside the unmodified pre-existing `is_merge_help_only` suite (74 total, up from 70 —
Amendment 15's `mask_quoted_spans` work landed first and already brought this file from 57 to 70; this
amendment's four tests are the delta on top of that, not on top of Amendment 13's original 57).
`test_post_tool_use.py` gains seven pure-helper tests (bare `--help`/`-h`, a real create, no invocation
at all, and the chained-independent-downgrade case for both `is_issue_create_help_only` and
`is_pr_create_help_only`) plus three `main()` end-to-end subprocess tests: `gh issue create --help` and
`gh pr create --help` each now exit 0 silently (reproducing this fix's own live incident, inverted), and
a help-only issue-create chained with a real pr-create still reaches the exit-2 "no GitHub URL found"
path for the real create — proving the independent-downgrade fix, not just the single-command case
(87 total, up from 77).

**General lesson (continuing Amendments 1, 6, 9, 10, 12, and 14's).** The "a fix scoped to one hook is a
standing invitation to check every sibling doing the same kind of thing" lesson, drawn several times
already in this ADR for output-reading, `scan_top_level`-anchoring, and repo-flag-shorthand bugs,
applies just as directly to a further kind of drifted duplication: a fix landed for one
command-detection predicate (`is_pr_merge_command`) and never checked against the structurally
identical predicates one file over (`is_issue_create_command` / `is_pr_create_command`) despite both
living in `_hookio`-adjacent code and sharing the exact same `scan_top_level`-based detection shape
Amendment 5 itself introduced. The mechanical proxy here is narrower and cheaper than a fresh grep: any
new `is_*_command`-style top-level-statement predicate introduced into this hook family should be
paired, at introduction, with the question "does a `--help`/`-h`-only invocation of this same CLI verb
need the identical exclusion `is_merge_help_only` already established the pattern for?"

## Amendment 17 (2026-07-09) — extending dev-env#626's quote-awareness fix to the three remaining repo-flag/PR-URL parsing sites (dev-env#634)

**The gap.** Auditing the `_REPO_FLAG_RE` family for Amendment 15's own fix surfaced three more sites with a
related but structurally distinct gap, filed as dev-env#634 rather than expanded in that amendment (see
Amendment 15's own "Out of scope, filed as a follow-up" section, which named all three in advance):

1. `pre-merge-findings-gate.py`'s `_parse_merge_target` tokenizes the `gh pr merge` invocation's tail via naive
   `tail.split()` — whitespace splitting, not a regex at all, and not quote-aware in the slightest. A
   `--subject`/`--body` value like `"see -R other/repo for context"` splits into several separate whitespace
   tokens (the naive tokenizer has no concept of quoting), so the decoy `-R` and `other/repo` inside it are
   indistinguishable from a real `--repo`/`-R` flag followed by its value — the identical dev-env#626 hijack,
   reached here via whitespace tokenization instead of an unanchored regex match. Confirmed live:
   `_parse_merge_target('gh pr merge 42 --subject "see -R other/repo for context"')` returned `('42',
   'other/repo')` instead of `('42', None)`.
2. `pre-auto-merge-checkpoint-gate.py` dynamically imports `_parse_merge_target` from
   `pre-merge-findings-gate.py` (`importlib.util.spec_from_file_location` + `exec_module`, a live reference to
   the same function object, not a copy) — automatically covered by fixing site 1, not a second site.
3. `stop-tile-enumeration-gate.py`'s own `_REPO_FLAG_RE`
   (`(?:--repo|-R)(?:=|\s+)(?P<repo>[^\s/]+/[^\s]+)`, ADR-088) never received Amendment 14's `(?<!\S)`
   lookbehind at all — it already recognized `-R` before PR #623, so that PR's audit classified it as already
   correct for the narrower "does it recognize `-R`" question it was scoped to, and never touched it. A
   strictly larger, pre-existing gap than dev-env#626 itself: exposed to BOTH a mid-word match (`xx-R`) and the
   same quote-unawareness Amendment 15 fixed everywhere else.
4. The PR-URL-regex analog (`_PR_URL_REPO_RE` / `_DEVENV_PR_URL_RE` / the inline URL searches) in all four
   files Amendment 15 fixed has the identical quoted-value blind spot, just for a URL-shaped decoy instead of a
   flag-shaped one — e.g. a `--subject` value containing `"see https://github.com/other/repo/pull/1 for
   context"` could false-match the URL extraction the same way. Amendment 15's own scope was specifically the
   "`_REPO_FLAG_RE` family," so this was deliberately left unfixed there.

**Fix — site 1 (`pre-merge-findings-gate.py`).** `tokens = tail.split()` → `tokens =
mask_quoted_spans(tail).split()`. `mask_quoted_spans` replaces a quoted span's characters — including its
internal whitespace — with `#`, so the entire decoy collapses into ONE contiguous token after `.split()`; the
existing `_VALUE_FLAGS` handling then correctly consumes it whole as `--subject`'s own value (`i += 2`), never
exposing the `-R`/`other/repo` text inside it as separate tokens. This is exactly the one-line fix dev-env#634
itself proposed, verified against this file's test suite before landing: neither existing test
(`test-merge-findings-gate.sh`'s step 7) depends on a *quoted* positional `ref`, so nothing regresses.
`test-merge-findings-gate.sh` gains step 8 (the dev-env#634 repro resolving to `('42', None)`, and a real
`--repo` flag surviving alongside the same quoted decoy).

**Fix — site 2 (`pre-auto-merge-checkpoint-gate.py`).** No code change — transitively fixed by site 1's fix to
the shared `_parse_merge_target` function object. Its own test suite
(`test_pre_auto_merge_checkpoint_gate.py` + `test-auto-merge-checkpoint-gate.sh`) was re-run in full to confirm.

**Fix — site 3 (`stop-tile-enumeration-gate.py`).** `_REPO_FLAG_RE` gains the `(?<!\S)` lookbehind:
`(?<!\S)(?:--repo|-R)(?:=|\s+)(?P<repo>[^\s/]+/[^\s]+)`. `_explicit_repo` runs it against a
`mask_quoted_spans`-masked copy of its `segment_first_line` input, mirroring the four sibling sites' own
masking-scope decision: the `_PR_URL_RE` fallback stays on the *unmasked* text (this file's own PR-URL regex
is explicitly out of dev-env#634's scope — see "Out of scope" below). Previously exercised only indirectly, via
`session_merged_prs`'s A4 cross-repo tests (both bare, unquoted `--repo` values) — this amendment adds
`_explicit_repo`'s first direct tests: mid-word `-R` not matched, a quoted `-R other/repo` decoy inside
`--subject` not matched, a real `--repo` flag surviving alongside that decoy, the pre-existing `-R` shorthand
still resolving after both fixes, and the (out-of-scope) PR-URL fallback still resolving a bare quoted URL
unchanged.

**Fix — site 4 (the PR-URL-regex analog in the four Amendment 15 files).** A new function,
`mask_prose_flag_values`, in `_hookio.py`. Unlike `mask_quoted_spans`, it cannot blanket-mask every quoted span:
at least one existing, legitimate case depends on a URL living *inside* a quoted span resolving correctly — a
bare quoted positional URL argument (`gh pr merge "https://github.com/o/r/pull/N"`,
`post-pr-merge-project.py`'s own `test_repo_from_cross_repo_url`). Blanket-masking every quoted span the way
`mask_quoted_spans` does would blind that legitimate case along with the decoy — the "mirror-image" complexity
dev-env#634 itself flagged without prescribing an exact mechanism.

`mask_prose_flag_values` instead masks ONLY the value immediately following a `--subject`/`-t`/`--body`/`-b`
flag — quoted, `$()` subshell, heredoc, AND unquoted single-token values alike (the last of these was a gap in
this amendment's own first draft, caught during `/review`: an unquoted `--body https://github.com/other/repo
/pull/1` is just as much a decoy as the same URL sitting inside quoted prose, since the decoy doesn't need
surrounding words to hide in when the flag's entire value IS the decoy — confirmed live:
`extract_repo_from_command('gh pr merge 380 --body https://github.com/other/repo/pull/1 --squash')` returned
`'other/repo'` before the fix). Every other opaque span, in particular a bare quoted *positional* URL argument
never preceded by one of these flags, is left untouched.

Applied at three of the four Amendment 15 call sites (their PR-URL fallback, run after the already-fixed
repo-flag check finds no match):

- `post-pr-merge-project.py::extract_repo_from_command` — `_PR_URL_REPO_RE.search(args)` →
  `_PR_URL_REPO_RE.search(mask_prose_flag_values(args))`.
- `posttooluse-inert-advisory.py::_devenv_merge_pr` — `url_m = _DEVENV_PR_URL_RE.search(args)` → `url_m =
  _DEVENV_PR_URL_RE.search(mask_prose_flag_values(args))`. `url_m` is reused both for the `is_devenv`
  self-identification decision and the PR-number fallback later in the function — both correctly benefit from
  the fix, since a decoy dev-env URL must not self-identify a non-dev-env command as dev-env's own either.
- `post-pr-merge-pull.py::extract_repo` — the second (`m2`, URL) `re.search` now runs against
  `mask_prose_flag_values(command)` instead of the raw `command`.

`pr-merge-reminder.py` needs no change: its analogous function, `_effective_merge_repo`, has no URL-fallback
branch at all (confirmed by reading the full file) — its only regex is the repo-flag check Amendment 15
already fixed. The one PR-URL regex that does exist in this file lives in the unrelated `_create_shard_step`
(parsing a `gh pr create`'s *output*, not a `gh pr merge` *command*), outside this fix's scope.

**Design — sharing `_opaque_spans`, not re-implementing the quote/subshell/heredoc walk a second time.**
`mask_quoted_spans` is refactored to extract its span-finding state machine into a new private
`_opaque_spans(command) -> list[tuple[int, int]]`, which `mask_quoted_spans` and `mask_prose_flag_values` both
call — `mask_quoted_spans` now masks every span `_opaque_spans` returns (byte-identical output to before this
extraction; all 13 of its pre-existing tests, plus the `split_top_level` cross-consistency test, pass
unmodified), while `mask_prose_flag_values` masks only the spans whose start coincides exactly with the
position right after a `--subject`/`-t`/`--body`/`-b` flag's own separator.

This is a narrower, additive exception to Amendment 15's own "no premature parameterization" call, not a
reversal of it: that decision was about NOT building a single parameterized "mask-then-search" policy helper
when the four then-existing callers' masking scope and which-regex-stays-unmasked already differed per site.
`_opaque_spans` extracts only the pure span-*finder* underneath both `mask_quoted_spans` and
`mask_prose_flag_values` — each function still independently decides which spans to mask and what regex to run
against the result; nothing about the search/masking *policy* is shared or parameterized. Without this
extraction, `mask_prose_flag_values` would need its own hand-copy of the same four-state
quote/subshell/heredoc walk — exactly the two-independent-implementations drift risk Amendment 15's own module
comment warns about (there, in the different context of `mask_quoted_spans` vs. `split_top_level` — a case
Amendment 15 deliberately left as two independent walks, guarded by an enforced consistency test, because
`split_top_level` was already a heavily-tested function with several other callers where an unsafe refactor was
the greater risk). `mask_quoted_spans` here is a much younger function (introduced one day earlier, in this
same ADR, with a single well-scoped internal API) — extracting its own already-private helper carries none of
that risk, and a byte-identical-output-preserving refactor is verified directly by its own full pre-existing
test suite passing unchanged, rather than needing a new cross-consistency test the way `mask_quoted_spans` vs.
`split_top_level` did (there is only one span-finding implementation now, not two that could drift apart).

A real, precedented shape this design handles for free: a `--body` value built as `"$(cat <<'EOF' ...
EOF)"` (see `stop-tile-enumeration-gate.py`'s own `session_resolved_issue_numbers` docstring for where this
construction is used elsewhere in this hook family) is masked as the single opaque span `_opaque_spans` already
computes for it — `mask_prose_flag_values` needs no extra heredoc/subshell-specific logic of its own.

**Out of scope, not fixed here.**

- `stop-tile-enumeration-gate.py`'s own `_PR_URL_RE` — dev-env#634's own point 4 scoped the URL-regex analog fix
  specifically to "all four files [Amendment 15] fixed," not this file's independent `_REPO_FLAG_RE`/`_PR_URL_RE`
  pair. Left as a `mask_quoted_spans`-unmasked fallback exactly like the four Amendment 15 files' own URL
  regexes, matching the established pattern, but genuinely untouched by this amendment.
- A separate, more widespread characteristic noticed while implementing site 1: several files in this hook
  family — `pre-merge-findings-gate.py`'s own `tail = re.split(r"&&|\|\||;|\n", tail)[0]` (used to bound the
  merge invocation's tail BEFORE tokenizing it) and the sibling `_MERGE_ARGS_RE = re.compile(r"\bgh\s+pr\s+
  merge\b([^\n;|&]*)")` pattern used across several of the Amendment 15 files — scope their "args" region via a
  raw split / negated-character-class match that itself has no quote-awareness: a `--subject`/`--body` value
  containing a literal `&&`/`;`/`|`/newline character would truncate the scoped region early, potentially
  hiding a REAL trailing `--repo` flag or PR URL rather than exposing a decoy one (the opposite failure
  direction from dev-env#626/dev-env#634's own "decoy hijacks a real value" shape — this one would silently
  DROP real data instead). Not demonstrated as live-exploitable here (no reproduction attempted, no fix
  designed), and pre-existing across the whole family rather than unique to any one site fixed by this
  amendment — flagged as a follow-up worth its own investigation rather than folded into this fix, per this
  ADR's own established "grep for the shape, note what's out of scope, file it" discipline (Amendments 9, 11,
  15).
- A **third decoy class**, found by `/review` on this amendment's own PR (#647) via a subagent that executed
  the code directly: a **bare positional PR-number** inside `--subject`/`--body` prose (e.g. `--subject
  "resolves 42 items"`) is read as a real PR number by three sites' `(?<!\S)(\d+)(?=\s|$)`-shaped regexes,
  none of which are protected by either `mask_quoted_spans` or `mask_prose_flag_values` — those two helpers were
  built for flag/URL extraction, not bare-number extraction. Confirmed live in
  `posttooluse-inert-advisory.py::_devenv_merge_pr`, `post-pr-merge-project.py::extract_pr_number_from_command`,
  and `stop-tile-enumeration-gate.py::_target_pr`. Genuinely a third, distinct decoy shape (neither a
  `_REPO_FLAG_RE` variant nor a PR-URL regex), affecting a comparable number of sites to this amendment's own
  fix — filed as [dev-env#650](https://github.com/brownm09/dev-env/issues/650) rather than folded in here.

**Coverage.** `_hookio.py`'s `test_hookio.py` gains 12 new `mask_prose_flag_values` tests (no-op passthrough;
double- and single-quoted decoys masked; `-t`/`-b` short forms; the `--subject=<value>` equals form; an
unquoted value masked too, plus the concrete unquoted-URL-decoy repro and an unquoted value at end-of-string
with no crash; the critical negative case — a bare quoted positional PR-URL argument NOT masked; a real URL
surviving alongside a masked decoy; a `$(cat <<'EOF' ...)` heredoc-in-subshell `--body` value masked as one
span; a mid-word `-b` not matched) — 86 total, up from 74 (Amendment 16 already brought this file to 74).
`test-merge-findings-gate.sh` gains step 8 (site 1's two cases). `test_stop_tile_
enumeration_gate.py` gains 5 new direct `_explicit_repo` tests — 81 total, up from 76 (site 3). Each of the
three site-4 files' existing suite gains two new cases (the exact dev-env#634 URL-decoy repro, and a real
signal surviving alongside it) — `test_post_pr_merge_project.py` to 32 (up from 30),
`test_post_pr_merge_pull.py` to 20 (up from 18) — except `posttooluse-inert-advisory.py`, whose existing
`test_devenv_merge_pr_direct` already bundles every resolution-shape case into one function (this file's own
established convention, per Amendment 15's own precedent) — the two new dev-env#634 cases were added as
additional assertions inside that same function, so its total stays at 31. All previously-passing suites
(`test_pre_merge_findings_gate.py`, `test_pre_auto_merge_checkpoint_gate.py` +
`test-auto-merge-checkpoint-gate.sh`, `test_pr_merge_reminder.py`) were re-run in full and pass unchanged.

**General lesson (continuing Amendments 9, 11, and 15's).** A fix scoped to a named bug class inevitably
surfaces adjacent, structurally different instances of a *related* class once the audit widens even slightly —
Amendment 15 itself predicted this precisely (its own "Out of scope, filed as a follow-up" section named all
three of this amendment's fix sites in advance) and the right response, again, is the same "grep for the
shape, note what's out of scope, file it" discipline, not scope creep into the original PR. A second, narrower
lesson specific to this amendment: Amendment 15's "no premature parameterization" call was correctly scoped to
the masking *policy* (which spans to mask, which regex to run) — but the underlying span-*finding* mechanism
itself was always a better candidate for extraction than either of the two policies built on top of it, once a
second policy (`mask_prose_flag_values`) needed the identical opacity rules. Distinguishing "the engine" from
"the policy built on it" — Amendment 5's own original framing — is a decision worth revisiting each time a
function gains a second caller with different needs, not just settling it once and treating that as final.

## Amendment 18 (2026-07-09) — `_effective_create_repo`: extending the `--repo`/`-R` flag-first resolution to `pr-merge-reminder.py`'s `is_create` branch (dev-env#646)

**The gap.** Amendment 14 swept every `--repo`-flag check in `claude/scripts/*.py` for `-R`-shorthand
recognition, but that sweep — like dev-env#616's own issue text — was scoped to checks that *already existed*:
`_effective_merge_repo` (`pr-merge-reminder.py`'s `merge_ok` branch), `extract_repo_from_command`
(`post-pr-merge-project.py`), `_devenv_merge_pr` (`posttooluse-inert-advisory.py`), and `extract_repo`
(`post-pr-merge-pull.py`). `pr-merge-reminder.py`'s **other** branch — `is_create`, which fires on a successful
`gh pr create` — was never in scope for any of that, because it had no repo-flag resolution to fix in the first
place: it has always unconditionally reported `cwd` and told the reminded session to "Identify the project
journal path from cwd," full stop. A `-R`/`--repo`-sweep grep (Amendment 14's own "grep for the shape, not the
constant name" lesson) would not have found this gap either — there was no `_REPO_FLAG_RE`-shaped regex, or any
regex at all, backing this branch's `cwd` report to grep for.

**Live reproduction (2026-07-08, dev-env#646's own filing).** `gh pr create --repo brownm09/dev-env --title
"..." --head docs/issue-642-tile-dont-ask-anywhere ...` run from a lifting-logbook-cwd session created
dev-env#644. The hook fired immediately with `cwd: C:\Users\brown\Git\lifting-logbook` and "1. Identify the
project journal path from cwd" — blindly following it would have written PR #644's stub and open-PR shard
under `sessions/lifting-logbook/`, misattributing a dev-env PR to the wrong project's journal. Caught only
because the session independently knew better; a less careful session would not have.

**Fix.** `pr-merge-reminder.py` gains `_effective_create_repo(command, cwd)`, structurally identical to
`_effective_merge_repo`: a `--repo`/`-R` flag (via the same `_REPO_FLAG_RE`, run against a
`mask_quoted_spans`-masked copy of `command` — the dev-env#626/Amendment 15 protection applies from this
function's introduction, not as a later follow-up) takes precedence, falling back to `cwd` otherwise. The one
deliberate divergence from `_effective_merge_repo`: no delegation to a cd-chain-aware helper (there is no
`effective_create_dir` counterpart to `effective_merge_dir`) — the `is_create` branch has only ever reported
plain `cwd`, so an unflagged create command's reminder is byte-for-byte unchanged by this fix; only the
explicit-flag case is new behavior. `_build_messages`'s `is_create` branch now computes `create_repo =
_effective_create_repo(command, cwd)` and prints both `cwd:` and `repo:` lines, with "Identify the project
journal path from the repo above" replacing "from cwd" — mirroring the `merge_ok` branch's own existing
`cwd:`/`repo:` display and phrasing exactly (a plain textual diff between the two branches' message bodies now
shows only the label and instruction-line changes, not a structurally different message shape).

**`_REPO_FLAG_RE`'s comment updated for its second consumer**, and `_hookio.py`'s `mask_quoted_spans` module
comment gains a fourth incremental paragraph (following the same pattern Amendment 17 used for its own two new
sites) naming `_effective_create_repo` as a seventh `mask_quoted_spans` call site — deliberately additive
rather than rewriting the original "four call sites" sentence, matching Amendment 17's own precedent for
exactly this situation.

**Coverage.** `test_pr_merge_reminder.py` gains a new `_effective_create_repo` section mirroring
`_effective_merge_repo`'s own: the dev-env#646 repro itself (`--repo` flag from an unrelated cwd), the `-R`
shorthand, the no-flag-falls-back-to-cwd baseline, and a real flag surviving alongside a quoted
`--body`-value decoy (the `mask_quoted_spans` protection, now proven at this seventh call site too) — 56 total,
up from 52. The pre-existing `test_build_messages_single_create_success_fires` and its chained-command siblings
were left unmodified (they assert only `"gh pr create detected" in messages[0]`, not the message's literal
body) and continue to pass unchanged, confirming the new `repo:` line is additive, not a breaking format
change. `test_hookio.py` needed no new tests (the comment-only addition there does not change
`mask_quoted_spans`'s behavior) and was re-run in full to confirm — 86 total, unchanged from Amendment 17.

**Out of scope, filed as a follow-up (not fixed here).** `/review` on this amendment's own PR (#662), via
direct execution rather than just reading, found that both `_effective_merge_repo` and the new
`_effective_create_repo` resolve `--repo`/`-R` via a search over the **entire** `command` string, not scoped
to their own statement — confirmed live: `gh pr create --repo a/x --fill && gh pr merge 5 --repo b/y --squash`
resolves `_effective_merge_repo` to `a/x` (the create's own flag), not `b/y`. Whichever `--repo` flag appears
textually first in the command wins for **both** functions, regardless of which statement it actually belongs
to. Pre-existing in `_effective_merge_repo` since dev-env#470 (not introduced by this amendment) and only
manifests when a single command chains create and merge with two **different** explicit repos — the common
single-repo chained pattern already tested extensively elsewhere in this file is unaffected either way. Filed
as [dev-env#667](https://github.com/brownm09/dev-env/issues/667) rather than expanded here: a proper fix needs
to scope each function's search to its own statement's region (e.g. via `split_top_level`), a shared design
change touching both functions that is a larger, distinct unit of work than this amendment's create-path-only
scope — the same "grep for the shape, note what's out of scope, file it" discipline Amendments 15 and 17 both
already established for this exact ADR.

**General lesson (continuing Amendments 1, 6, 9, 10, 12, and 14's).** The "a fix scoped to one hook/branch is a
standing invitation to check every sibling doing the same kind of thing" lesson, drawn repeatedly in this ADR,
has a sharper edge here than usual: `is_create` and `merge_ok` are not two separate files or two separate
functions maintained by different authors — they are two branches of the *same* `_build_messages` function in
the *same* file, and the asymmetry survived four separate repo-flag-focused amendments (14, 15, and the
`is_create`-adjacent Amendment 16, none of which touched this gap) before a live misattribution surfaced it.
The mechanical proxy this suggests, sharper than "grep for `_REPO_FLAG_RE`": whenever one branch of a shared
message-building function gains a new resolution helper, ask whether every *other* branch of that same
function reports its target the same way — a within-function asymmetry is easy to miss precisely because both
branches read as "already covered" from a file-level or grep-level audit.

## Amendment 19 (2026-07-09) — masking the bare positional PR-number regex family (dev-env#650)

**The gap.** Amendment 17's own "Out of scope, not fixed here" section named a third decoy class, found by
`/review` on that amendment's own PR (#647) via a subagent that executed the code directly: a **bare
positional PR-number** inside `--subject`/`--body` prose (e.g. `--subject "resolves 42 items"`) is read as a
real PR number by three sites' `(?<!\S)(\d+)(?=\s|$)`-shaped regexes, none of which are protected by either
`mask_quoted_spans` or `mask_prose_flag_values` — both of those helpers were built for flag/URL extraction, not
bare-number extraction. Confirmed live (reproduced again independently before this fix, matching dev-env#650's
own repro):

```python
>>> _devenv_merge_pr('gh pr merge --subject "resolves 42 items"', DEVENV_CWD)
'42'   # should be None -- no real PR number in this command at all
>>> extract_pr_number_from_command('gh pr merge --subject "resolves 42 items" --squash')
42     # should be None
>>> _target_pr('gh pr view --subject "resolves 42 items"')
42     # should be None
```

Grepping `claude/scripts/*.py` for the exact `(?<!\S)(\d+)(?=\s|$)` pattern (dev-env#650's own closing
suggestion — "worth grepping the rest of `claude/scripts/*.py`... before assuming these three are exhaustive")
turned up a **fourth** site the issue itself didn't name: `stop-tile-enumeration-gate.py`'s
`_closed_issue_number` uses the exact same compiled `_POS_NUM_RE` object as `_target_pr` (same file, same
regex, same vulnerability shape) to resolve `gh issue close`'s target issue number. Confirmed live against the
pre-fix code:

```python
>>> _closed_issue_number('gh issue close --comment "see 42 for related issue" 630')
42   # should be 630 -- the decoy precedes the real positional issue number
```

No other `claude/scripts/*.py` file matches the pattern — the sweep is exhaustive as of this amendment.

**Fix.** Each of the four sites' bare-number regex now runs against a `mask_quoted_spans`-masked copy of its
own input, mirroring Amendment 15's fix for `_REPO_FLAG_RE` — `mask_quoted_spans` (not `mask_prose_flag_values`)
is the correct helper here because this decoy shape is not scoped to a `--subject`/`--body`/`-t`/`-b` flag
specifically; a bare number could just as easily hide inside a quoted `--author-email` value or a quoted
branch-name argument.

- `posttooluse-inert-advisory.py::_devenv_merge_pr` — `num_m = _MERGE_POS_NUM_RE.search(args)` →
  `_MERGE_POS_NUM_RE.search(masked_quoted_args)`, where `masked_quoted_args = mask_quoted_spans(args)` is now
  computed once and shared with the pre-existing `repo_m` check (previously two separate
  `mask_quoted_spans(args)` calls; behavior-unchanged, one fewer redundant walk over the same string — the same
  kind of micro-optimization Amendment 7 made when `main()` was calling `split_top_level` twice per command).
- `post-pr-merge-project.py::extract_pr_number_from_command` — `num = re.search(r"(?<!\S)(\d+)(?=\s|$)", args)`
  → `re.search(r"(?<!\S)(\d+)(?=\s|$)", mask_quoted_spans(args))`. The function's own `_PR_URL_RE` fallback
  (a *different*, structurally distinct decoy shape — see "Out of scope" below) is untouched.
- `stop-tile-enumeration-gate.py::_target_pr` and `::_closed_issue_number` — both `n = _POS_NUM_RE.search(tail)`
  calls become `_POS_NUM_RE.search(mask_quoted_spans(tail))`. Each function's own `_PR_URL_RE` /
  `_ISSUE_URL_RE` check stays on the unmasked text, mirroring `_explicit_repo`'s own established scope decision
  immediately above it in the same file (Amendment 17) — this hook's PR-URL/issue-URL regexes remain an
  accepted, already-documented gap, out of scope for both dev-env#634 and this amendment.

Each site's masking-scope decision needed its own read of what else runs against the same input and must stay
unmasked, per dev-env#650's own framing ("Each site needs its own care about which OTHER regex on the same
input must stay unmasked") — genuinely four small, independent fixes sharing one mechanical shape, not a single
find-replace.

**Out of scope, not fixed here.** Auditing `post-pr-merge-project.py::extract_pr_number_from_command` for this
fix surfaced a structurally distinct, previously-undocumented gap in the *same function*: its `_PR_URL_RE`
fallback (`url = _PR_URL_RE.search(args)`, checked when the — now masked — positional-number match finds
nothing) still runs on the raw, unmasked `args`. A `--subject`/`--body` value containing a decoy PR URL (not a
bare number) can still hijack the extracted PR number when no real positional number is present. Confirmed
live:

```python
>>> extract_pr_number_from_command('gh pr merge --squash --subject "see https://github.com/other/repo/pull/99 for context"')
99   # should be None -- no real PR number in this command at all
```

This is `_PR_URL_RE` (declared at this file's line 55, used only inside `extract_pr_number_from_command`), a
**different regex object** from `_PR_URL_REPO_RE` (line 67, used inside `extract_repo_from_command`) — the
latter was already fixed by Amendment 17's `mask_prose_flag_values`. It is a URL-shaped decoy, not a
bare-number one, so it is not part of dev-env#650's own scope either — the identical "grep for the shape, note
what's out of scope, file it" situation Amendment 17 itself was in when it found and deferred this very
amendment's fix. Filed as a follow-up issue rather than folded in here, for the same reason Amendment 17 gave
for deferring dev-env#650 in the first place: a masking-scope decision this specific deserves its own
verification against this file's existing quoted-URL-argument tests, not a rushed addition to an
already-in-flight PR.

**Coverage.** `posttooluse-inert-advisory.py`'s `test_devenv_merge_pr_direct` gains three new bundled
assertions (the exact dev-env#650 repro for both `--subject` and `--body`, plus a real number surviving
alongside a decoy) — matching this file's established bundled-assertions convention (Amendment 15/17's own
precedent for this function) — count stays at 31. `test_post_pr_merge_project.py` gains two new
`test_cmd_*`-pattern cases (34 total, up from 32). `test_stop_tile_enumeration_gate.py` gains seven new direct
tests — four for `_target_pr` (the bare-number-decoy repro, a real number surviving alongside a leading decoy,
the already-established unmasked-URL-fallback case, and an integration-level case proving the fix reaches
`session_merged_prs` via the auto-merge acted-on/observed correlation path — the one path that actually calls
`_target_pr` on the raw command, since a direct-marker merge resolves its number from the *output* text via
`merge_pr_number_from_output` first and never reaches `_target_pr` at all) and three for the newly-covered
`_closed_issue_number` (bare-number-decoy repro, real number surviving alongside a leading decoy, and an
integration-level `session_resolved_issue_numbers` case) — 88 total, up from 81. Every new "decoy alone, no
real number" assertion was verified against the pre-fix code first and confirmed to reproduce the bug (not a
vacuously-true assertion) — including one case caught during that verification itself: an initial "real number
survives alongside a decoy" test placed the decoy *after* the real number, which passed identically on both
pre-fix and fixed code (`re.search` returns the leftmost match, so a trailing decoy never reaches the
vulnerable code path regardless of masking) — the decoy was moved before the real number in both the direct
`_target_pr`/`_closed_issue_number` integration tests to make them genuine regression tests. All previously-
passing suites (`test_hookio.py`, `test_no_crude_command_substring_checks.py`, `test_pre_merge_findings_gate.py`
+ `test-merge-findings-gate.sh`, `test_pre_auto_merge_checkpoint_gate.py` + `test-auto-merge-checkpoint-gate.sh`,
`test_pr_merge_reminder.py`) were re-run in full and pass unchanged — `_hookio.py` itself is untouched by this
amendment (all four fixes reuse the existing `mask_quoted_spans`, no new helper needed).

**General lesson (continuing Amendments 1, 6, 9, 15, and 17's).** The fourth site (`_closed_issue_number`) is
this ADR's *fourth* instance of the specific "same regex object, same file, only one of its call sites got
fixed" gap — Amendment 6 found it for `scan_top_level`-shaped duplication, Amendment 9/10 for
`"X" not in command` substring checks, and now for a bare positional-number regex. dev-env#650's own issue text
anticipated this exact outcome by prescribing the grep before filing was even complete; the discipline held. A
second, narrower lesson, continuing Amendment 17's own closing point: a fix that resolves one filed issue can
surface the *next* one during its own implementation (here, the `_PR_URL_RE` gap in
`extract_pr_number_from_command`), the same way Amendment 17's own review surfaced dev-env#650 while fixing
dev-env#634. Treating this as a steady-state property of the hook family — each fix's audit is expected to
surface exactly one more adjacent, structurally distinct gap — rather than a surprise each time, is the
practical takeaway: budget for "note it, file it" as part of the fix, not as evidence the sweep failed.

## Amendment 20 (2026-07-09) — quote-aware args-region BOUNDARY, not just the search within it (dev-env#660)

**The gap.** Amendment 17's own "Out of scope, not fixed here" section predicted this almost exactly: several
sites in this hook family scope a `gh pr merge` invocation's "args" region via a raw split or
negated-character-class regex with **no quote-awareness of its own**, run *before* any masking happens:

1. `pre-merge-findings-gate.py::_parse_merge_target` — `tail = re.split(r"&&|\|\||;|\n", tail)[0]`, used to
   bound the tail **before** Amendment 17's `mask_quoted_spans(tail).split()` tokenization ever runs.
2. `_MERGE_ARGS_RE = re.compile(r"\bgh\s+pr\s+merge\b([^\n;|&]*)")`, independently defined in
   `post-pr-merge-project.py` (`extract_pr_number_from_command`, `extract_repo_from_command`) and
   `posttooluse-inert-advisory.py` (`_devenv_merge_pr`) — its negated character class stops at **any single**
   `&`/`|` (not just doubled `&&`/`||`), which is *easier* to trigger than site 1's pattern.
3. `pre-auto-merge-checkpoint-gate.py::_merge_tail` — a hand-copy of site 1's PRE-Amendment-17 logic, explicitly
   documented as "mirrors `_parse_merge_target`'s own tail-extraction," carrying the identical gap.

This is the **opposite failure direction** from Amendments 15/17's own decoy-hijack shape: instead of a FAKE
flag/URL inside quoted prose being mistaken for real, a **REAL** trailing flag, PR number, or URL that comes
*after* a quoted value containing a literal `&`, `;`, `|`, or `\n` is silently **dropped** from the scoped
region and never seen at all — false negative / silent data loss, not false positive.

**Confirmed live, not speculative** (Amendment 17 explicitly left this "[n]ot demonstrated as live-exploitable
here" — this amendment closes that gap):

```
>>> _parse_merge_target('gh pr merge 42 --subject "part1 && part2" --repo brownm09/dev-env')
('42', None)   # --repo silently dropped; expected ('42', 'brownm09/dev-env')
>>> extract_repo_from_command('gh pr merge 42 --subject "R&D tracking" --repo brownm09/dev-env')
None   # a bare '&' in an ORDINARY commit subject already triggers it -- no deliberate crafting needed
```

**Impact per site.**

- **`pre-merge-findings-gate.py`** (ADR-028/ADR-039 merge gate): a dropped `repo` means `gh pr view` runs
  without `--repo`, resolving against cwd's git remote instead of the command's actual explicit target — the
  findings gate can silently evaluate against the wrong PR.
- **`post-pr-merge-project.py`**: `extract_repo_from_command` returning `None` falls through to cwd's own
  `.claude/hook-config.json` repo instead of the command's explicit `--repo` — the exact dev-env#559 failure
  shape, reintroduced through a different vector. A same-numbered issue on the **wrong repo's board** could be
  moved to Done.
- **`posttooluse-inert-advisory.py`**: `_devenv_merge_pr` falls back to `_is_devenv_cwd(cwd)` when the `--repo`
  flag is invisible — producing either a missed advisory or a false one depending on cwd.
- **`pre-auto-merge-checkpoint-gate.py`**: confirmed **NOT** a live gate bypass. Truncating inside an open quote
  always leaves the naive slice with an unbalanced quote count, which `wants_auto_merge`'s own `shlex.split()`
  already rejects via its `except ValueError: return True` fail-closed fallback (dev-env PR #588) — the correct
  answer was reached, but only by accident, through a fallback built for an unrelated reason. Fixed here for
  consistency with the other three sites, not because a live gap was confirmed.

**Fix.** The same technique at all four sites, mirroring Amendment 15's own — applied one step earlier in the
pipeline, at *boundary-detection* instead of *post-boundary flag-search*: mask the **whole** relevant text with
the existing `mask_quoted_spans` **before** the boundary-finding split/regex runs, then use the match's **span
offsets** (not its matched text) against the masked string to slice the **original**, unmasked text.
`mask_quoted_spans` is length-preserving — every masked character is replaced 1:1 with `#` — so an offset that's
correct in the masked string is correct at the identical position in the original.

- **Site 1** (`pre-merge-findings-gate.py`): `boundary = len(re.split(r"&&|\|\||;|\n",
  mask_quoted_spans(tail))[0]); tail = tail[:boundary]`, inserted immediately before Amendment 17's existing
  `tokens = mask_quoted_spans(tail).split()` line, which is otherwise unchanged.
- **Sites 2** (`post-pr-merge-project.py`): a new private `_merge_args(command)` helper —
  `_MERGE_ARGS_RE.search(mask_quoted_spans(command))`, then `command[start:end]` from the match's `.span(1)` —
  replaces the direct `_MERGE_ARGS_RE.search(command)` call in both `extract_pr_number_from_command` and
  `extract_repo_from_command` (previously byte-identical lead-ins in both functions; factoring the now-more-
  complex boundary logic once avoids duplicating it a second time in the same file).
- **`posttooluse-inert-advisory.py`**: the identical mask-then-reslice pattern inlined directly in
  `_devenv_merge_pr` (its only call site, so no separate helper).
- **`pre-auto-merge-checkpoint-gate.py`**: the identical pattern in `_merge_tail`, requiring one new import
  (`mask_quoted_spans` from `_hookio`).

Everything downstream of each site's boundary fix is **completely unchanged** — the already-fixed within-
boundary searches (Amendments 15/17's `mask_quoted_spans(args)` / `mask_prose_flag_values(args)` calls) now
simply receive the correct, untruncated `args`/`tail` text instead of a prematurely truncated one. Each
existing stop-character-set is preserved exactly (`&&`/`||`/`;`/`\n` for site 1's split; single-char
`&`/`|`/`;`/`\n` for `_MERGE_ARGS_RE`) — this is a pure quote-awareness fix, not a behavior change to *which*
characters bound the region, so no currently-tested case regresses.

**Confirmed NOT affected: `pr-merge-reminder.py` and `post-pr-merge-pull.py`.** Both mask the **whole** command
directly via `mask_quoted_spans` before searching for the repo flag (`_effective_merge_repo` /
`extract_repo`), with no separate quote-unaware pre-truncation step of their own — the vulnerability is
specific to code that computes a bounded `args` substring via a raw split/regex *before* any masking happens,
which these two never do.

**Known residual limitation, left unfixed.** `mask_quoted_spans` deliberately preserves literal newlines even
inside a masked span (`test_mask_quoted_spans_preserves_newlines`) — needed by its own documented contract for
a different reason (heredoc body line-count preservation for a hypothetical future line-oriented consumer). A
`--subject`/`--body` value containing a **literal embedded newline** (a multi-line double-quoted argument, or
a heredoc body — the latter a real, precedented shape in this hook family per Amendment 17's own citation of
`stop-tile-enumeration-gate.py`'s `--body "$(cat <<'EOF' ...)"` idiom) immediately followed by more real args
would still truncate early even after this fix, since the boundary regex still stops at that preserved
(unmasked) `\n`. Deliberately left as a documented gap rather than folded into this fix: closing it would mean
deviating from `mask_quoted_spans`'s established, tested newline contract that other callers rely on, for a
narrower shape (needs an embedded newline specifically, inside a still-open span, with more real content after
it) than the confirmed live bug this amendment fixes.

**Coverage.** `test-merge-findings-gate.sh` gains step 9 (site 1: the confirmed `&&` repro resolving correctly,
the easier-to-trigger bare-`&` case, a real chained `&&` command still correctly excluded, and — added during
`/review` on this amendment's own PR #668 — a quoted `&&` decoy combined with a real trailing `&&` chain in the
SAME command, proving the boundary-finder picks the first UNMASKED separator rather than being shadowed by the
earlier masked one) — 13 total, up from 11 (four `print()` cases now behind the same two assertions). `test_
post_pr_merge_project.py` gains 6 new tests (site 2: `--repo`/PR-number after a quoted `&&`, bare `&`, and `|`
value; a real chained `&&` command still excluded; the same combined decoy+chain case) — 38 total, up from 32.
`test_posttooluse_inert_advisory.py` gains one new function bundling four assertions (three original plus the
combined decoy+chain case), following this file's own established convention of bundling every
`_devenv_merge_pr` resolution-shape case into one function (Amendment 17's own precedent) — 32 total, up from
31. `test_pre_auto_merge_checkpoint_gate.py` gains 3 new tests (site 4: `_merge_tail` now returns the full
untruncated tail and `wants_auto_merge` reaches the correct answer directly rather than via the `ValueError`
fallback; a real chained `&&` command still excluded; the combined decoy+chain case) — 33 total, up from 30.
`_hookio.py`'s own `mask_quoted_spans` docstring gains a documentation-only paragraph (no behavior change)
naming the newline-preservation caveat for these four new boundary-finding callers — `test_hookio.py` stays
unchanged at 86 (no `_hookio.py` behavior was modified, only its documentation and additional call sites of
its existing, unmodified `mask_quoted_spans`). All previously-passing suites across all four files and the
repo-wide AST-based crude-substring-check gate were re-run in full and pass unchanged.

**General lesson (continuing Amendments 9, 11, 15, and 17's).** This is the cleanest instance yet of this ADR's
recurring "a fix scoped to one part of a bug class is a standing invitation to check the rest of it" lesson —
Amendment 17 didn't just predict that a related gap existed somewhere, it named the exact two code shapes
(`_parse_merge_target`'s split, `_MERGE_ARGS_RE`'s negated class) that turned out to be vulnerable, in its own
"Out of scope, not fixed here" section, before any reproduction was attempted. The discipline of writing that
prediction down at the moment a related-but-different gap is *noticed but not chased* — rather than either
silently forgetting it or reflexively expanding the current PR's scope to chase it immediately — is what made
this amendment's investigation fast: the "is this real?" question had a documented, precise starting point
instead of a fresh audit from scratch. A second, narrower lesson: "quote-aware search within an already-bounded
region" and "quote-aware detection of the region's own boundary" are two *different* defects that can exist
independently in the same function — fixing the first (Amendment 17, for sites 1 and 2's within-`args` repo-flag/
URL searches) does not imply the second is also fixed, even though both defects are patched with the literal
same primitive (`mask_quoted_spans`) applied at a different pipeline stage. A fix landing for one doesn't
retroactively prove the other was ever audited.
