# ADR-050 — Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes

**Date:** 2026-06-21
**Status:** Accepted
**Amended:** 2026-07-01, 2026-07-02, 2026-07-04, 2026-07-08, 2026-07-09, 2026-07-11, 2026-08-15, 2026-08-16, 2026-08-20 (twenty-six amendments — see Amendment sections below)
**Tags:** hooks, post-tool-use, tool_response, payload, github-project, automation, reliability, dry, usage-snapshot, pr-merge-reminder, gh-pr-view, api-fallback, message-dispatch, top-level-statement-scan, issue-create, false-positive, command-parsing, heredoc, regex, quote-tracking, canonical-mutate-guard, pre-tool-use, ast, regression-test, allowlist, gh-pr-merge-help, misattribution, live-confirmation-fallback, repo-flag-shorthand, quote-masking, gh-create-help, prose-flag-masking, pr-create, bare-number-masking, args-boundary, truncation, rest-merge-fallback, gh-api, graphql-rate-limit, stop-hook-parity, stop-tile-enumeration-gate, convergence, observability, trace-logging, forensic-debugging, silent-failure, defense-in-depth, null-payload, dispatch

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
there `get_pr_body(611, repo="merickvaughn/lifting-logbook")` fetched a real, unrelated, already-merged
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

## Amendment 21 (2026-07-09) — masking the PR-number extraction's own URL fallback (dev-env#664)

**The gap.** Amendment 19's own "Out of scope, not fixed here" section named this gap in advance: masking
`extract_pr_number_from_command`'s positional-number search (dev-env#650) left its own `_PR_URL_RE` fallback —
checked only when the positional-number match finds nothing — running on the raw, unmasked `args`. A
`--subject`/`--body` value containing a URL-shaped decoy could hijack the extracted PR number when the command
named no real positional number. Confirmed live (post dev-env#650/dev-env#660 fixes, reproduced again
independently before this fix):

```python
>>> extract_pr_number_from_command('gh pr merge --squash --subject "see https://github.com/other/repo/pull/99 for context"')
99   # should be None -- no real PR number in this command at all
```

This is `_PR_URL_RE` (declared at this file's module scope, used both by `extract_pr_number`'s output scan and by
this fallback), a **different regex object** from `_PR_URL_REPO_RE` (used inside `extract_repo_from_command`) —
the latter was already fixed by Amendment 17's `mask_prose_flag_values`. `extract_pr_number_from_command`'s
return value is the primary PR-number source `main()` uses to resolve the merged PR before fetching its body and
moving the linked issue to Done — a false number from this decoy could fetch and act on the wrong PR's body,
potentially moving an unrelated issue to Done on this repo's board.

**Fix.** One line: `url = _PR_URL_RE.search(args)` → `url = _PR_URL_RE.search(mask_prose_flag_values(args))` in
`extract_pr_number_from_command`, mirroring `extract_repo_from_command`'s own `_PR_URL_REPO_RE` fix immediately
below it in the same file (Amendment 17) — `mask_prose_flag_values`, not blanket `mask_quoted_spans`, is the
correct helper for the identical reason Amendment 17 established: a bare quoted positional PR-URL argument
(`gh pr merge "https://github.com/o/r/pull/380"`, never preceded by `--subject`/`--body`, `test_cmd_url`) is a
legitimate, already-tested shape that `mask_prose_flag_values` leaves untouched, unlike blanket `mask_quoted_spans`,
which would blind it too. `mask_prose_flag_values` was already imported in this file (Amendment 17's own
`extract_repo_from_command` fix) — no new import needed. Both the `_PR_URL_RE` module-scope comment and
`extract_pr_number_from_command`'s own docstring are updated to document the new masking scope, matching this
file's established per-regex documentation convention.

**Coverage.** `test_post_pr_merge_project.py` gains 3 new `test_cmd_*`-pattern cases — 43 total, up from 40: the
exact dev-env#664 repro (decoy alone -> `None`), a real positional number surviving alongside the same decoy (the
existing masked positional-number search already handled this case, unaffected by this fix), and — the case that
actually exercises the fixed `_PR_URL_RE` fallback branch itself, since the previous two both short-circuit on the
earlier positional-number match — a real *bare URL* (no positional number at all) surviving alongside the same
quoted decoy. Each new "decoy alone" assertion was verified against the pre-fix code first (reproducing `99`
instead of `None`) before landing, per this ADR's established regression-test discipline (Amendments 15, 17, 19).
All previously-passing suites were re-run in full and pass unchanged: `test_hookio.py` (86, `mask_prose_flag_values`'s
own source is untouched by this amendment), the repo-wide AST-based crude-substring-check gate (13), and — as an
extra sanity check, since neither file was touched — `test_posttooluse_inert_advisory.py` (32) and
`test_stop_tile_enumeration_gate.py` (112).

**Out of scope, filed as a follow-up.** Auditing the wider hook family for the same shape surfaced a separate,
previously undiscovered-as-its-own-issue gap: `stop-tile-enumeration-gate.py` has its own independent `_PR_URL_RE`
and `_ISSUE_URL_RE` regex objects, used unmasked in `_target_pr`, `_explicit_repo`, and `_closed_issue_number` —
explicitly named and deliberately deferred twice already (Amendment 17's and this amendment's own predecessor,
Amendment 19) without ever getting a dedicated tracking issue. Confirmed live to be a **more severe** variant than
this amendment's own fix: because `_target_pr` and `_closed_issue_number` check their URL regex *before* their
masked positional-number fallback (the reverse order from `extract_pr_number_from_command`), a decoy wins even
when a real positional number is also present in the same command, not only when no real number exists at all.
Filed as [dev-env#685](https://github.com/brownm09/dev-env/issues/685) rather than folded in here — a third,
independent file with its own regex objects and its own test suite is a materially larger scope than this
amendment's single-line fix, the same proportionality judgment Amendment 17 made when it deferred dev-env#650 and
this very gap in the first place.

**General lesson (continuing Amendments 9, 11, 15, 17, and 19's).** Amendment 19 predicted this exact fix down to
the code shape, in its own "Out of scope, not fixed here" section, before any reproduction was attempted here —
the same discipline that made Amendment 20's investigation fast applied again. What's new this time: the
"Out of scope" audit this amendment's own fix triggered surfaced a gap that TWO prior amendments had each already
individually noticed and deferred (Amendments 17 and 19), yet neither converted into a standalone tracking issue
— a documented deferral in an ADR's prose is not the same as a filed issue a future session's search will actually
find. `gh issue list --search` against this exact gap's shape returned nothing before this amendment filed
dev-env#685, despite two separate amendments describing it in detail. The practical takeaway: "note it, file it"
means an actual issue number, not just a prose paragraph in an ADR amendment that happens to be the third
occurrence of the same accepted gap.

## Amendment 22 (2026-07-11) — restoring `ntpath.isabs`'s pre-3.13 semantics in the cd-chain / redirect path resolvers (dev-env#732)

**The gap — a new class for this ADR.** Every prior amendment fixed a *command-parsing* false-positive (a
quoted / heredoc / subshell decoy mistaken for a real token). This one is a *Python-version* compatibility bug
in the shared resolvers themselves. `effective_merge_dir` (this file), `_effective_push_dir`
(`pr-merge-reminder.py`, ADR-065), and `_blockable_redirect_root` (`pre-tool-use-canonical-mutate-guard.py`,
Amendment 7 / ADR-071) each decide whether a `cd <path> && …` / `git -C <path> …` target is absolute via
`os.path.isabs(path)`, joining it onto *cwd* only when relative.
[Python 3.13 changed `ntpath.isabs`](https://docs.python.org/3/whatsnew/3.13.html#os-path) so a
rooted-but-driveless path (`/Git/dev-env`, `\Git\dev-env`) returns `False` where ≤3.12 returned `True`. So on
3.13+ a forward-slash `cd` target wrongly takes the relative branch —
`normpath(join(cwd, "/Git/dev-env"))` → `\Git\dev-env` — silently mis-scoping the ADR-065/067 merge/push
hooks and, more seriously, **fail-opening the canonical-mutate guard**: a `git -C /repo checkout` redirect
into a canonical checkout resolves to a wrong/nonexistent directory, its `git rev-parse` fails, the redirect
is skipped, and the mutating command is no longer blocked.

**Latent, not live — surfaced by CI.** Production `py -3` is 3.12, where the code is correct; the bug bites
the moment the runtime moves to 3.13+. It was caught by
[PR #730](https://github.com/brownm09/dev-env/pull/730)'s new `windows-latest` CI (whose `py -3` is 3.13+):
`test_hookio.py` (5 cases) and `test_pr_merge_reminder.py` (7 cases) failed with
`expected /Git/dev-env, got '\Git\dev-env'`. PR #730 pinned CI to the production runtime (3.12) so it stayed
faithful and green; this amendment is the forward-compat fix (surfaced while adding CI in PR4 of the
hook-reliability initiative #717).

**Fix.** A new shared pure helper in `_hookio.py` — `is_absolute_path(path)` =
`path.startswith(("/", "\\")) or os.path.isabs(path)` — restores the ≤3.12 semantics on every version by
short-circuiting on the leading separator (a drive-absolute / UNC target still flows to `os.path.isabs`, which
handles it unchanged on both). All three resolvers swap `os.path.isabs(...)` for it (`pr-merge-reminder.py`
and the canonical-mutate guard import it; `effective_merge_dir` is in the same file). Behaviour-identical on
3.12 (both branches agree for a `/`-rooted path), correct on 3.13+. One helper, three call sites — the same
"share the fix, don't re-derive it" discipline as the rest of this ADR.

**Two failure shapes, two fix surfaces.** The 12 CI failures had two independent causes. The absolute-path
cases (`cd /Git/dev-env` → assert `== "/Git/dev-env"`) failed because the *function* routed the target
through `normpath`; the helper fixes them with no test edit. The relative-path cases
(`assert os.path.isabs(out)` where `out` is a driveless `\base\sub\repo`) failed because the *assertion
itself* called 3.13's `isabs` on a driveless path — fixed by swapping those two assertions to the
version-agnostic `is_absolute_path(out)`.

**Verified not sensitive (audited, unchanged).** The issue's "re-verify every other `os.path.isabs` /
`os.path.normpath` user" step cleared the two remaining `isabs` sites: `post-tool-use.py`'s
`_canonical_root_from_common_dir` (input is `git rev-parse --git-common-dir`, which emits drive-absolute or
relative on Windows, never driveless-rooted — its own `test_post_tool_use.py:442` already passes on 3.13) and
`pre-tool-use-worktree-path-check.py`'s `file_path` guard (input is always a drive-absolute Windows path from
the Write/Edit tool contract). The remaining `os.path.normpath`-only sites are separator normalizers, not
absoluteness decisions, and are unaffected by the `isabs` change.

**Coverage.** `test_hookio.py` gains 5 `is_absolute_path` cases (forward-slash / backslash / UNC rooted,
drive-absolute, relative/empty) including a **3.13 simulation** — it patches `os.path.isabs` to return `False`
(as 3.13 does for a driveless path) and asserts the `startswith` short-circuit still classifies rooted targets
absolute, the only way to pin the 3.13 behaviour on a 3.12 interpreter, where the `startswith` and `isabs`
branches are otherwise indistinguishable for `/x`. The two relative-path assertions in `test_hookio.py` /
`test_pr_merge_reminder.py` are made version-agnostic. `test_canonical_mutate_guard.py` (64) is re-run
unchanged — its git-shelling redirect path stays integration-covered and the pure resolution semantic is now
pinned by `is_absolute_path`'s own tests. Full suites green on the local 3.12 runtime: `test_hookio.py` (91),
`test_pr_merge_reminder.py` (56), `test_canonical_mutate_guard.py` (64); the 3.13 fix is validated by PR #730's
`windows-latest` CI.

**General lesson.** `os.path.isabs`/`normpath`/`splitdrive` semantics can shift across CPython minor versions,
and a Windows-only `ntpath` change is invisible on a POSIX CI or a pinned-3.12 runtime. When a shared helper's
correctness rests on a stdlib path predicate, prefer an explicit, version-independent condition at the exact
site where the version behaviour matters over trusting the stdlib default — and pin it with a test that
*simulates* the other version rather than trusting the interpreter you happen to run on.

## Amendment 23 (2026-08-15) — recognizing the two-step REST merge fallback across the five PostToolUse merge-consequence hooks (dev-env#986)

**The gap.** Every hook in this family detects "did this Bash call complete a `gh pr merge`?" purely by
matching the command text against `gh pr merge` (`scan_top_level` + a `_check_merge_stmt`/`_MERGE_RE`
variant) and checking the output for gh's own `"Squashed and merged pull request #N"` success line
(`output_has_merge_marker`). None of the five hooks this ADR already converged onto that shared
detection — `usage-snapshot.py`, `post-pr-merge-project.py`, `post-pr-merge-pull.py`,
`post-pr-merge-reclaim.py`, `post-merge-tile-checkpoint.py` — recognized the documented two-step REST
fallback (`gh api -X PUT repos/{owner}/{repo}/pulls/{N}/merge`), used when `gh pr merge` itself is
unavailable — e.g. during a GitHub GraphQL rate-limit outage, since the REST merge endpoint is a plain
REST call and unaffected by a GraphQL-specific outage. A merge that goes through the REST path silently
skipped the usage snapshot, the linked issue's Done move, the local-`main` fast-forward, the worktree
disk reclaim, and the tile-enumeration reminder — with no error surfaced, discovered live while merging
PR [#984](https://github.com/brownm09/dev-env/pull/984) during exactly such an outage.

**Not a new class for this ADR — the Stop-hook side already had it.** `stop-tile-enumeration-gate.py` (a
`Stop` hook, not a PostToolUse hook, so outside this ADR's original five) already recognized this exact
REST shape for its own purpose (session-merged-PR enumeration) via its own
`_GH_API_STMT_RE`/`_PULLS_MERGE_PATH_RE`/`_MERGED_TRUE_RE` trio, matched against the whole raw command
string. This amendment brings the five PostToolUse hooks to parity using the same underlying signals —
a `gh api` statement targeting a `.../pulls/<N>/merge` path, and a `"merged":true` field in the response
body — generalized into this file's own shared primitives rather than duplicated a sixth and seventh
time.

**Deliberately still NOT fully converged with `stop-tile-enumeration-gate.py`'s own trio** — a maintainability
review finding, considered and consciously not acted on. That hook's `has_api` check requires no
method-flag precision (any `gh api` verb suffices); the primitive below requires a same-segment PUT
flag. Converging them would either weaken this module's precision (a real correctness regression for
these five hooks, which trigger state-changing side effects — a Done move, a fast-forward, a disk
reclaim — where a false positive matters far more than it does for the Stop hook's passive "add a PR
number to an already-merged set") or silently narrow `stop-tile-enumeration-gate.py`'s own established,
tested, unrelated-to-this-PR behavior. Neither is in scope here; converging them properly (auditing
whether that hook's own precision needs tightening too, on its own merits) is filed as a follow-up
rather than rushed as a side effect of this fix.

**Two round-trips through review — first-draft bugs, caught and fixed before merge.** The first
implementation of `is_rest_merge_command` required BOTH the `gh api` verb and the `.../pulls/<N>/merge`
path to appear within the *same* `scan_top_level` segment, reasoning that this was strictly *safer* than
`stop-tile-enumeration-gate.py`'s whole-command path search (matching every other `_check_*_stmt`
convention in this file). Two independent review passes (`/review`'s parallel correctness and
reliability subagents, both live-verifying against the actual code) found this was strictly *narrower*
instead — and reachably so, not a theoretical edge case:

- `gh api`'s own documented `{owner}`/`{repo}` URL templating (https://cli.github.com/manual/gh_api) —
  the exact form this repo's own runbooks, and this very amendment's first draft, wrote the command in
  — means a genuine, *unquoted* invocation contains a bare `{`, which `split_top_level` treats as an
  unconditional top-level statement separator (the PowerShell `if ($?) { B }` / bash brace-group
  carve-out from Amendment 4's own history). `gh api -X PUT repos/{owner}/{repo}/pulls/42/merge` splits
  into three segments, none of which carries both the verb and the path — so the documented, expected
  form of this exact command went undetected by every one of the five hooks this fix exists for.
- The same false negative hit a backslash-line-continued invocation, for the identical reason
  (`split_top_level` splits on bare `\n` by design, per its own dev-env#836 docstring, and deliberately
  does not join continuations).
- The combined `_REST_MERGE_PATH_RE` regex in `_repo_target.py` compounded the placeholder gap: even
  once detection was fixed, the *strict-slug* combined capture couldn't parse `{owner}/{repo}` at all —
  so a quoted placeholder REST merge (where detection alone worked even before this fix, since quoting
  keeps the invocation in one segment) was confirmed successful, yet `resolve_command_pr_number` still
  returned `None` right alongside the unparseable repo, and `post-pr-merge-project.py`'s Done move
  silently skipped despite the hook believing the merge was confirmed.
- `is_rest_merge_command` was also verb-agnostic: `GET /repos/{owner}/{repo}/pulls/{pull_number}/merge`
  is GitHub's own documented, genuinely read-only "Check if a pull request has been merged" endpoint,
  sharing the identical path shape, and `gh api`'s default verb is GET — so a harmless status check
  satisfied the predicate with no method check at all.
- Most seriously, the new `_repo_target.py` extractors (`repo_from_rest_merge_path`,
  `pr_number_from_rest_merge_path`) were called *unconditionally* on the raw, unmasked, unbounded
  command from `post-pr-merge-project.py`'s `resolve_command_repo`/`resolve_command_pr_number` and
  `post-pr-merge-pull.py`'s `extract_repo` — with no `is_rest_merge_command` gate, unlike every other
  masked/bounded extractor this ADR's amendment history built (15/17/19/20/21). This was a **regression
  on the pre-existing `gh pr merge` path**, not merely a gap in the new one: a `--subject`/`--body` value
  shaped like a REST merge path (`gh pr merge 42 --squash --subject "fix: handle
  repos/other/repo/pulls/9/merge path"`) could hijack repo/PR-number resolution on an *ordinary*
  `gh pr merge` command that named no repo of its own — silently skipping a legitimate same-repo Done
  move (the resolved repo no longer matches cwd's config) or, worse, moving the wrong issue to Done.
- `post-pr-merge-project.py`'s widened top-level gate (needed so a REST command reaches the marker
  check at all — see below) also, as an unintended side effect, let a REST command that *failed* its
  own marker check fall into the live `gh pr view` confirmation fallback — directly contradicting this
  amendment's own first-draft "Scope decision" paragraph, which claimed that fallback stayed ungated for
  the REST shape. Two compounding problems: that fallback is GraphQL-backed, so it is a
  guaranteed-failing subprocess during the exact rate-limit outage that motivates the REST path in the
  first place; and when the REST path named no resolvable PR number (the placeholder case above),
  `confirm_merge_via_gh` falls back to inferring the PR from cwd's checked-out branch — the precise
  dev-env#557 misattribution class (an unrelated already-merged PR's issue wrongly moved to Done).
- `merge_succeeded`'s own first branch (`output_has_merge_marker(output)`) carried no command-shape
  condition at all — harmless before this amendment, since reaching that check already required
  `main()`'s (then-narrower) gate to confirm a genuine `gh pr merge` shape first. Once the gate widened
  to admit REST-shaped commands too, a REST command whose *combined output* happened to carry an
  unrelated chained command's own `"Squashed and merged pull request #N"` text could be wrongly
  confirmed via that branch — the four sibling hooks' own OR-extension already paired this marker check
  with the command-shape check; `post-pr-merge-project.py`'s own version had simply omitted it.
- `effective_merge_dir`'s cd-chain boundary is anchored on a literal `gh pr merge` token (ADR-067); for
  a REST-only command it finds none and falls back to treating the *entire* command as the cd-chain
  search region — so a `cd` occurring *after* the REST merge (`gh api ... && cd other-repo && npm
  test`) would be wrongly read as governing the merge, resolving `post-pr-merge-project.py`'s config to
  the wrong repo.

**The corrected design.** `_GH_API_PUT_FLAG_RE` now requires a same-top-level-segment PUT method flag
(`-X PUT` / `--method PUT` / `--method=PUT` / `-XPUT`, closing the GET false-positive) alongside the
`gh api` verb check — this half stays segment-scoped, the precision the first draft was reaching for.
`_PULLS_MERGE_PATH_RE`'s own path search is *not* segment-scoped — it runs against the whole raw
command, matching `stop-tile-enumeration-gate.py`'s existing, accepted behavior for this exact reason
(closing both the placeholder and line-continuation false negatives without re-deriving a
placeholder-aware un-splitter `stop-tile-enumeration-gate.py` never needed). This reopens a narrower
decoy surface at the primitive level in principle (two independent REST-merge invocations chained in
one command could resolve to whichever's path text a naive `.search()` finds first) — accepted as a
documented residual gap, since two genuinely distinct REST PR merges chained in a single Bash call is a
vanishingly unrealistic shape, unlike `{owner}`/`{repo}`, which is the documented, expected form of this
command. The realistic decoy surface — a `--subject`/`--body` value on an *ordinary* `gh pr merge`
command — is closed structurally, not by the path regex's own scoping: every extractor call site
(`resolve_command_repo`, `resolve_command_pr_number`, `post-pr-merge-pull.py`'s `extract_repo`) is now
gated behind `is_rest_merge_command(command)` first, so the extractors never run at all unless a genuine
top-level `gh api ... PUT` invocation is actually present — a `--subject` value is never itself a
top-level segment starting with `gh api`, regardless of what REST-path-shaped text it contains.
`_repo_target._REST_MERGE_REPO_RE` and `_REST_MERGE_PR_NUMBER_RE` are now independent regexes: the
repo half stays strict-slug (correctly returning `None`, not a literal `{owner}/{repo}` string, for an
unresolved placeholder — this module has no way to know what gh would have resolved it to without a
`git remote`/network round-trip it doesn't make), while the PR-number half (`repos/\S+?/pulls/(\d+)/merge\b`)
resolves independently of whether the repo half parses, since the PR number carries no such templating.
`post-pr-merge-project.py`'s `main()` re-adds the sibling hooks' own `if not
scan_top_level(command, _check_merge_stmt): sys.exit(0)` re-check inside its `not merge_succeeded(...)`
branch, so a REST command that fails its own marker check exits immediately rather than reaching the
GraphQL-backed live-confirmation fallback — restoring this amendment's original scope-decision claim to
actual fact rather than aspiration. `merge_succeeded`'s first branch now requires
`scan_top_level(command, _check_merge_stmt) and output_has_merge_marker(output)`, matching the four
siblings exactly. `main()` skips `effective_merge_dir` entirely for a REST-only command (no established
cd-chain convention exists for that shape) and uses `cwd` directly instead, rather than risk resolving
against a `cd` that occurs after the merge.

**Scope decision — direct marker only, not the live-confirmation fallback (now actually enforced, not
just claimed).** Every hook in this family has a secondary safety net: when the direct marker check
fails, a live `gh pr view` call (`should_confirm_via_gh`/`confirm_merge_via_gh`) confirms the merge
another way. That fallback is gated on the *original* `gh pr merge` command shape
(`scan_top_level(command, _check_merge_stmt)`) in all five hooks (see the corrected-design paragraph
above for why `post-pr-merge-project.py` needed its own re-check added) and is deliberately left ungated
for the REST shape: a REST call that fails to print `"merged":true` clearly (a malformed or truncated
response) falls through to the same silent exit an unrecognized command already gets today. A review
finding argued this residual gap is under-justified rather than wrong-in-principle — `gh api`'s exit
code (unlike `gh pr merge`'s) has none of the ambiguity that keeps this family from trusting it
elsewhere (no `--help`/queued-`--auto` variant, no local-git cleanup step that can fail despite a
successful remote action), so `is_rest_merge_command(command) and exit_code == 0` could plausibly close
the gap at zero marginal network cost. Declined for this PR as a scope-expansion beyond the issue's own
stated ask (direct-marker recognition specifically); left as a candidate for a future amendment rather
than folded in here.

**Fix — two new primitives, hand-wired per caller (this ADR's own established pattern).**
`_hookio.py` gains `is_rest_merge_command(command)` and `output_has_rest_merge_marker(output)` (see the
corrected-design paragraph above for the exact matching rules). `_repo_target.py` gains
`repo_from_rest_merge_path(command)` and `pr_number_from_rest_merge_path(command)`. Each of the five
hooks' own existing boolean "was this a successful merge" function
(`merge_confirmed`/`is_successful_merge`/`merge_succeeded`) gets a two-line OR extension using these
primitives, matching this ADR's `_check_merge_stmt` duplication convention rather than introducing a new
cross-file combinator/policy function (see Amendment 15's own "no premature parameterization" reasoning,
which this amendment follows rather than revisits). `post-pr-merge-project.py`'s `main()` gate widens to
`scan_top_level(command, _check_merge_stmt) or is_rest_merge_command(command)`; its `merge_succeeded`
gains a `command` parameter (was `output`-only) for the same reason the other four hooks' predicates
already take both; two new pure combinators, `resolve_command_repo(command)` and
`resolve_command_pr_number(command, output)`, fold the (gated) REST-path extraction in behind the
existing `extract_repo_from_command`/`extract_pr_number_from_command` resolution — split out of `main()`
for independent testability, mirroring `post-pr-merge-pull.py`'s existing `format_pull_message`/
`plan_advisory` extraction pattern in this same file family. `post-pr-merge-pull.py`'s own `extract_repo`
gains a (gated) REST-path step ahead of its cd-chain/git-remote subprocess fallback, since the REST path
always names its target repo explicitly — the same precedence a PR URL already gets over inferring from
cwd.

**The PreToolUse-side sibling gap remains open.** [dev-env#900](https://github.com/brownm09/dev-env/issues/900)
covers the four *pre-merge* gates (`pre-merge-findings-gate.py`, `pre-merge-branch-check.py`,
`pre-merge-numbering-check.py`, `pre-merge-message-check.py`), which match on command text alone before
the command has run (so they have no output to check against). This amendment's `is_rest_merge_command`
primitive is reusable there too, should that fix land later — it takes only `command`, no `output`.

**Two more hooks in the same PostToolUse family, not extended — filed, not silently deferred.**
`pr-merge-reminder.py` (the journal-stub-on-merge reminder — a `claude/CLAUDE.md`-mandated session
boundary) and `posttooluse-inert-advisory.py` (the ADR-053 inert-hook safety net) key on the identical
`gh pr merge` command shape and were not named in dev-env#986's own issue text, which scoped this fix to
exactly five hooks. Extending two more hooks — each with their own test suite — was judged a genuine
scope expansion rather than folded in silently; filed as a follow-up issue and tiled rather than left as
an unremarked gap this amendment's own "General lesson" below would otherwise contradict.

**Coverage.** New primitive-level cases in `test_hookio.py` (`is_rest_merge_command`,
`output_has_rest_merge_marker` — positive, negative, quoted-decoy, heredoc-decoy, the unquoted and
quoted `{owner}`/`{repo}` placeholder forms, a line-continued invocation, the GET-method-not-matched
negative case, all four method-flag spellings, and a same-command-different-segment method-flag
non-leak case) and `test_repo_target.py` (`repo_from_rest_merge_path`, `pr_number_from_rest_merge_path`
— including the placeholder-form split-resolution case and a `/pulls/N/merge` vs `/pull/N` web-URL
non-confusion case). Each of the five hooks' own test file gains a positive REST-merge case, a negative
(no `"merged":true`) case, and — where the hook has its own command-shape decoy test suite — a
quoted-string decoy case; `test_post_pr_merge_project.py` and `test_post_pr_merge_pull.py` additionally
gain decoy-hijack-prevention cases (a REST-path-shaped `--subject` value on an ordinary `gh pr merge`
command must not hijack repo/PR-number resolution) and a case pinning `merge_succeeded`'s corrected
command-shape-gated first branch. Manually verified end-to-end against the real hooks and a real,
currently-open PR (this very PR, #990): the unquoted `{owner}`/`{repo}` placeholder form now resolves
`post-pr-merge-project.py` all the way through to fetching the real PR's body and parsing its `Closes`
reference; the `--subject`-decoy command correctly resolves via the *real* `gh pr merge` path rather
than the decoy; and a failed REST merge now exits in under 200ms (confirming it never reaches the
network-bound live-confirmation fallback) rather than paying a multi-second `gh pr view` round-trip.

**General lesson.** A detection primitive shared across a hook family (this ADR's whole premise) still
needs periodic re-auditing against *new* command shapes that accomplish the same underlying action a
different way — `gh pr merge`'s REST fallback is not a hypothetical, and the Stop-hook side of this same
family had already independently discovered and handled it before the PostToolUse side did. When one
hook in a family gains a new detection case, checking whether its siblings need the identical extension
is cheaper than waiting for each to be discovered separately through its own silent-failure incident.
A second, sharper lesson from this amendment's own first draft: a predicate that is *stricter* than an
existing sibling's is not automatically *safer* — the sibling's looser whole-command search existed for
a reason (tolerating a documented CLI templating feature and a common multi-line shell idiom), and
tightening scope without checking why the looser version was looser can silently reintroduce exactly the
false negatives the stricter version was trying to prevent. Adversarial review (independent verification
against the live code, not just re-reading the diff) is what caught this before merge rather than after
a second silent-failure incident.

**Addendum (2026-08-16, dev-env#991) — the two filed-not-extended hooks now extended too.**
`pr-merge-reminder.py` and `posttooluse-inert-advisory.py` — the two hooks this amendment's own text
above explicitly named as "filed, not silently deferred" — now recognize the REST merge fallback as
well, using the identical `is_rest_merge_command`/`output_has_rest_merge_marker` primitives this
amendment introduced. No new primitive was needed; each fix is a small OR-extension to the hook's own
existing detection logic, following this amendment's established pattern:

- `pr-merge-reminder.py`: `main()`'s top-level gate widens to admit the REST shape (none of
  `is_create`/`is_merge`/`is_push` match a REST-only command on their own, so without this the reminder
  never reaches `_build_messages` at all); `_build_messages`'s `merge_ok` computation gains the REST
  OR-branch alongside the existing `is_merge`/marker check — deliberately **not** wired into the
  `live_confirmed` live-`gh pr view` fallback, which stays scoped to the original `gh pr merge` shape
  only, matching this amendment's own scope decision for the five hooks; and `_effective_merge_repo`
  gains the same cwd-instead-of-`effective_merge_dir` guard `post-pr-merge-project.py` already uses for
  this shape (no established cd-chain convention exists for a REST-only command, so trusting
  `effective_merge_dir`'s whole-command cd-chain search risks reading a `cd` occurring AFTER the REST
  call as governing it). A follow-up `/review` finding also caught that the push-suppression check
  (`is_push and not (is_create or is_merge)`) needed the identical `is_rest_merge_command(command)`
  addition — without it, a chained `gh api -X PUT .../pulls/<N>/merge && git push` fired both the new
  REST merge reminder AND a duplicate push reminder for the same event, since `is_merge` (the original
  `gh pr merge` text-shape flag the suppression check relies on) stays `False` for the REST shape.
- `posttooluse-inert-advisory.py`: `detect_board_actions` gains a REST branch gated on
  `is_rest_merge_command(command) AND output_has_rest_merge_marker(output)` — a stronger
  positive-confirmation requirement than the `gh pr merge` branch's own absence-of-hard-failure-text
  check, justified because `gh api`'s JSON response body is ordinary stdout (unlike gh's own "Squashed
  and merged" success line, which lives on stderr and does not reliably survive to this hook's
  transcript-derived `output` — the reason the `gh pr merge` branch cannot use a marker-based check to
  begin with). `_devenv_merge_pr` gains its own REST branch, checked when `merge_args(command)` returns
  `None` (no `gh pr merge` shape is present) and itself gated on `is_rest_merge_command` — the same
  decoy-hijack guard this amendment's corrected design established for the five hooks' extractor call
  sites, here confirmed directly: a REST-path-shaped decoy inside an ordinary `gh pr merge` command's
  `--subject` value never reaches the REST branch at all, since `merge_args` is non-`None` for that
  command and the function takes its normal (unaffected) path instead.

Both hooks' own test suites gained the same case shapes this amendment's "Coverage" paragraph
established for the five hooks: a positive REST-merge case, a negative (no `"merged":true`) case, a
GET-method-not-matched case, an `{owner}`/`{repo}` placeholder case, a chained-with-push
no-duplicate-reminder case (the `/review`-found fix above), and — for `posttooluse-inert-advisory.py`,
which does its own repo/PR-number resolution — a decoy-hijack-prevention case pinning that a
REST-path-shaped `--subject` value on an ordinary `gh pr merge` command cannot hijack
`_devenv_merge_pr`'s resolution. See [dev-env#991](https://github.com/brownm09/dev-env/issues/991).

## Amendment 24 (2026-08-16) — converging `stop-tile-enumeration-gate.py`'s own REST-merge trio onto the shared primitives (dev-env#992)

**The follow-up, actioned.** Amendment 23 left `stop-tile-enumeration-gate.py`'s own
`_GH_API_STMT_RE`/`_PULLS_MERGE_PATH_RE`/`_MERGED_TRUE_RE` trio deliberately unconverged with this
module's `is_rest_merge_command`/`output_has_rest_merge_marker`, reasoning that the Stop hook's `has_api`
check (any `gh api` verb, no method-flag requirement) might be a deliberate, lower-stakes design choice
rather than an oversight, and that auditing it properly was out of scope for a PR about the five
PostToolUse hooks. [dev-env#992](https://github.com/brownm09/dev-env/issues/992) is that audit.

**Finding: the looser check was never actually exploitable, so tightening it costs nothing.**
GitHub's read-only "check if a pull request has been merged" endpoint
(`GET /repos/{owner}/{repo}/pulls/{pull_number}/merge`) returns `204 No Content` when merged and `404`
when not — it never returns the `{"sha":...,"merged":true,"message":...}` body a *successful PUT merge*
response uniquely carries. `gh api`'s default verb is GET, so the scenario Amendment 23's PUT-flag
requirement exists to close for the five PostToolUse hooks (a harmless status-check GET satisfying a
verb-only predicate) was, for THIS hook, already structurally impossible to trigger via that GET
endpoint — `_MERGED_TRUE_RE` would never match a genuine GET response body regardless of the verb check.
The only way `has_api` + `_MERGED_TRUE_RE` could have false-positived was a chained command where an
unrelated top-level `gh api` GET/DELETE/etc. call coincidentally shared the transcript with SOME other
source of literal `"merged":true` text and a `/pulls/N/merge` path — already an accepted, documented
residual gap of the whole-command path search (`_PULLS_MERGE_PATH_RE`'s own comment, both here and in
this file), not a gap the method-flag check would have closed by itself. In short: this was not a
deliberate lower-stakes design tradeoff, it was slack nobody had exploited yet — indistinguishable from
oversight until traced this precisely, and worth closing so the drift doesn't get rediscovered as
"unexplained duplication" a third time.

**The fix.** `stop-tile-enumeration-gate.py` now imports `is_rest_merge_command` and
`output_has_rest_merge_marker` from `_hookio` and calls them directly in `session_merged_prs`:
`if is_rest_merge_command(command) and output_has_rest_merge_marker(output):`. Its own
`_GH_API_STMT_RE` and `_MERGED_TRUE_RE` are deleted. `_PULLS_MERGE_PATH_RE` stays — it is NOT a candidate
for the same convergence, because `_hookio`'s own `_PULLS_MERGE_PATH_RE` is deliberately non-capturing
(nothing in that module reads the PR number out of it), while this hook needs the captured `(\d+)` to
know which PR to add to its merged-PR set. `is_rest_merge_command` already performs its own internal
(non-capturing) path search as part of confirming a genuine invocation; the local capturing regex now
runs a second time, purely for extraction, only after that gate has already passed — a small, accepted
duplication of *search*, not of *policy*, mirroring how several other callers in this file family keep a
capturing local regex alongside a shared non-capturing gate rather than force one signature to serve
both jobs.

**Verification.** `test_gh_api_merge_detected` (`claude/scripts/tests/test_stop_tile_enumeration_gate.py`)
already used `-X PUT` in its fixture, so the tightened check changes no existing test's expected outcome
— `test_hookio.py` (122 tests) passes unchanged. A new regression test,
`test_gh_api_get_no_put_flag_not_merged`, was added to pin the negative case at this file's own
integration level: a `gh api .../pulls/N/merge` call with no method flag must not add a PR to
`session_merged_prs`'s merged set, even against a synthetic `"merged":true` output (the real GET
endpoint never returns that body). Before this PR, only `_hookio`'s own primitive-level test
(`test_is_rest_merge_command_get_method_not_matched`) covered this case — no test in
`stop-tile-enumeration-gate.py`'s own suite pinned it at the caller, confirming the looser check was
never load-bearing for any *previously* pinned behavior, while closing the gap for future changes to
this file. Full suite: 153 tests (up from 152), all passing.

See [docs/TESTING.md](../TESTING.md) item 48 for the corresponding test-index update.

## Amendment 25 (2026-08-16) — tracing `usage-snapshot.py`'s merge-confirmation decision itself (dev-env#474 follow-up)

**The still-open question Amendment 1/3/8 left unanswered.** Amendment 1 fixed dev-env#474's original
symptom (an exit-code gate that dropped the snapshot on every worktree merge); Amendment 3 added the
live `gh pr view` fallback for when gh's own success marker doesn't survive to `tool_response`
(dev-env#489); Amendment 8 rolled that fallback out to `usage-snapshot.py` and its four siblings
(dev-env#504). All three landed, tested, and correct as of 2026-07-02. But two live reproductions
*after* all three landed — merging PR #954 on 2026-08-07 and PR #988 on 2026-08-16, both ordinary
worktree-holds-`main` merges — still saw no `### Usage Snapshot (post-merge)` block appear. dev-env#489's
own investigation (and its sibling dev-env#496) had already established, live, that this class of
failure requires a human to be *present and instrumented* at the exact moment of a real worktree-merge
failure to diagnose — there was no way, after the fact, to tell whether the marker was lost (expected,
per dev-env#489's buffering-race hypothesis) and the `gh pr view` fallback then also failed to confirm,
or whether some earlier branch (the `--help`-only guard, `should_confirm_via_gh`'s cost gate) incorrectly
short-circuited before the fallback was ever attempted. Neither PR #954 nor PR #988 had that live
instrumentation in place, so both reproductions dead-ended exactly like dev-env#496 had already warned
they would.

**The fix is observability, not new detection logic.** The decision logic in `main()` — `merge_confirmed`
→ `scan_top_level` shape check → `is_merge_help_only` guard → `should_confirm_via_gh` cost gate → the
live `confirm_merge_via_gh` fallback — was already correct and already covered by
`test_merge_confirmed_*`/`test_help_command_*`. What was missing was a way to know, after the fact,
*which* of those branches fired for a given real invocation. `resolve_merge(command, output, exit_code,
cwd)` is a straight extraction of that existing branching into one pure function returning a `reason` of
`"marker"` / `"rest_marker"` / `"not_merge_shape"` / `"help_only"` / `"no_confirm_needed"` /
`"gh_view_confirmed"` / `"gh_view_unconfirmed"` — a pure refactor, not a behavior change (the existing
`test_merge_confirmed_*` suite and the `is_merge_help_only` composition tests all pass unmodified against
it). `main()` now appends the resolution — plus `cwd`, `exit_code`, and a timestamp — as one best-effort
JSON line to `C:/Users/brown/.claude/scratch/usage-snapshot-merge-trace.log` (`_log_merge_trace`, wrapped
in a bare `except Exception: pass`, matching `session-mode-prompt.py`'s own untested `_log` — an
observability aid must never become a new way to break the hook) for **every** merge-shaped command,
confirmed or not, so the next occurrence answers dev-env#489/#496's open question directly from the trace
file instead of requiring another live-instrumented reproduction.

**Scope.** Only `usage-snapshot.py` gained this trace in this amendment. `pr-merge-reminder.py` was also
silent for PR #954 per the dev-env#474 comment thread, and the identical trace mechanism would answer the
same question for it and the other four PostToolUse merge-consequence hooks — filed as a follow-up rather
than bundled here, since `usage-snapshot.py` is the one this ADR and dev-env#474 are specifically about,
and a single hook's trace file is enough to determine whether the underlying mechanism (not just this one
hook's wiring of it) is the actual gap. See `docs/TESTING.md` item 8 for the extended test coverage.

**Post-review fix.** `/review` on the PR implementing this amendment (#998) flagged the trace log as
unbounded — every merge-shaped invocation appends forever with no rotation or cap, unlike other
per-session-scoped scratch artifacts in this hook family. `_log_merge_trace` now caps to the 500 most
recent entries (a read-modify-write instead of a pure append, still wrapped in the same
`except Exception: pass` never-raise contract). 500 is deliberately generous — merges are infrequent, so
the cap exists to bound history over a period of years, not to actively trim in normal operation. The
same review also flagged that `resolve_merge()`'s `"marker"` vs `"rest_marker"` reason label is a
best-effort re-classification, not a strict decomposition of `merge_confirmed()`'s own `or` — documented
in `resolve_merge`'s own docstring rather than changed, since the dual-marker case it describes (one
command's output satisfying both merge mechanisms at once) is unrealistic.

## Amendment 26 (2026-08-20) — hardening `main()`'s own dispatch, the blind spot Amendment 25's trace couldn't see (dev-env#1028)

**The question Amendment 25 still couldn't fully answer.** Amendment 25 built `resolve_merge()` and
`_log_merge_trace()` specifically so a future "no snapshot appeared" reproduction would answer *which
branch fired* from the trace file instead of requiring another live-instrumented catch. dev-env#1028
(2026-08-20, career-playbook PR #1356, the same worktree-holds-`main` local-abort shape as
dev-env#489/#496/#954/#988) is the first real test of that promise since Amendment 25 landed — and the
trace file had **zero entries** for the invocation, not merely an unhelpful one. That is a stronger,
more specific failure than anything Amendment 25 was built to diagnose: it means whatever happened, it
happened *before* `resolve_merge()` — the one function the trace was built around — was ever called.

**What was investigated and ruled out.** Traced `resolve_merge()` end-to-end (every branch:
`merge_confirmed`/`scan_top_level`/`is_merge_help_only`/`should_confirm_via_gh`/`effective_merge_dir`/
`confirm_merge_via_gh`) against the issue's exact command (`gh pr merge "<url>" --squash
--delete-branch` — the URL-argument form, not the bare form every existing fixture used) and exact
stderr text. Every branch correctly classifies this shape and reaches the
`gh_view_confirmed`/`gh_view_unconfirmed` fallback, and `main()`'s trace-write call (`if
resolution["is_merge_shaped"]: _log_merge_trace(...)`) is unconditional on `is_merge_shaped`, evaluated
*before* the `confirmed`-gated exit. **The literal hypothesis in dev-env#1028's own title — this stderr
text causing an early return inside `resolve_merge()` — does not hold**, confirmed by both the existing
`test_resolve_merge_gh_view_*` fixtures (already using near-identical "already checked out" text) and a
fresh regression fixture against the URL-argument command form specifically.

**What was found instead, by direct inspection, not reproduction.** `main()`'s own dispatch — two lines
*before* `resolve_merge()` is ever called — never received Amendment 25's (or `read_command_output`'s
pre-existing) defensive treatment:

```python
command = data.get("tool_input", {}).get("command", "")       # usage-snapshot.py:713 (pre-fix)
exit_code = data.get("tool_response", {}).get("exitCode", -1) # usage-snapshot.py:716 (pre-fix)
```

`data.get("key", {})` substitutes the `{}` default only when *the key is absent* — a key present with
value `None` (or any other non-dict) passes straight through, and the chained `.get(...)` throws
`AttributeError`, caught only by the outermost `except Exception: sys.exit(0)` around all of `main()`
(this hook's Hook-Safety-mandated safe-exit guard) — with nothing written anywhere. `read_command_output(data)`,
called on the very next line (`:714`), already defends against exactly this shape — its own docstring
states *"Returns "" for a missing, empty, None, or non-dict tool_response — never raises, so a hook can
call it unguarded"* — but that guarantee was never extended to the `command`/`exit_code` extraction
sitting right beside it. A repo-wide grep found the identical unguarded shape, unchanged, in six sibling
hooks (see Follow-up below) — the same "a fix applied to one call site doesn't get audited against every
other call site with the same shape" pattern this ADR has already named more than once (Amendments 6, 9).

**Honest scope of the claim.** This is not presented as a confirmed reproduction of dev-env#1028's exact
trigger — the raw PostToolUse JSON payload from that career-playbook session isn't available to inspect,
so whether `tool_response`/`tool_input` genuinely arrived non-dict for that specific invocation is not
provable after the fact (the same evidentiary limit Amendment 25's own "Context" section describes for
dev-env#489/#496). What is provable by inspection alone: this is a real, reproducible defect, inconsistent
with this exact module's own established convention two lines away, and it *would* produce precisely the
observed symptom (silent, total, unrecoverable absence from the trace) for any payload shape where it
fires.

**The fix has two parts, matching the issue's own two-option ask.** dev-env#1028 asked to either (a)
confirm and fix a specific early-return, or (b) at minimum guarantee the trace can never again be silently
indistinguishable from "the hook didn't run." Since (a)'s specific hypothesis didn't hold, this amendment
delivers a generalized version of (a) — the found bug's actual fix — plus (b) as unconditional
defense-in-depth on top:

1. **New `_hookio.py` helpers, `read_command(data)` and `read_exit_code(data, default=-1)`**, extending
   `read_command_output`'s exact contract (same "never raises" guarantee, same defensive shape) to the two
   extraction points that lacked it. `usage-snapshot.py` now calls these instead of the raw chained
   `.get()`s. `default` is a parameter, not hardcoded, because sibling hooks disagree on it today
   (`post-tool-use.py`/`pr-merge-reminder.py` default `exitCode` to `0`; `usage-snapshot.py` and three
   others default to `-1`) — see Follow-up below.
2. **`resolve_merge()`'s own call site in `main()` is wrapped in a `try`/`except`**, independent of whether
   the concrete bug above is the only thing that could ever throw there — `split_top_level`'s ~450-line
   heredoc/here-string/brace-group parser (Amendments 5/7 and onward) is complex enough that a
   not-yet-identified edge case is plausible, and this amendment doesn't attempt to audit it exhaustively.
   On exception, a new, cheap, exception-resistant substring check (`_plausibly_merge_shaped` — deliberately
   NOT `scan_top_level`, since that may be what just raised) decides whether to still emit a trace line,
   with a new `reason: "classify_error"` value. This value is synthesized by `main()`, not returned by
   `resolve_merge()` itself — `resolve_merge()`'s own docstring enum is unchanged, since documenting it
   there would misdescribe what that function returns.

**Coverage.** `test_hookio.py` gains direct unit tests for `read_command`/`read_exit_code` (normal payload,
missing key, `None` value, non-dict value, non-int-coercible `exitCode`, `default` override) — 130 tests
total (up from 122). `test_usage_snapshot.py` gains what no prior amendment's test suite had: genuine
end-to-end tests that pipe a full JSON payload through `usage_snapshot.main()` itself (`sys.stdin`
monkeypatched to a fake payload, `_log_merge_trace` monkeypatched to capture calls instead of touching the
real file, `SystemExit` caught) rather than calling `resolve_merge()` directly with hand-built strings, as
every existing test in that file does. Scoped to the three scenarios that never reach the live `gh pr view`
call, matching this file's own no-subprocess-mock convention (Amendment 3/8's "the repo avoids subprocess
mocks", restated in this file's own docstring): a `gh pr merge --help` command with a `null` `tool_response`
(the regression pin for the concrete bug — `is_merge_help_only` resolves without ever needing
`confirm_fn`); a forced `resolve_merge()` exception pinning the new `classify_error` fallback itself; and
the same forced exception against a non-merge command, pinning that the fallback does *not* fire (and
therefore does not spam the trace log) for the overwhelming majority of non-merge Bash/PowerShell calls
this global hook sees on every tool call. The dev-env#1028 exact-text scenario itself (URL-argument command
+ "already checked out" stderr, needing the `gh_view_confirmed` fallback) is pinned the same way every other
`gh_view_*` case in this file already is: a direct `resolve_merge()` call with an injected `confirm_fn`, per
the file's own established idiom — not forced through `main()`'s stdin dispatch, which would need either a
real network call or a departure from that idiom. `test_usage_snapshot.py` total: 48 tests (up from 39).

**Follow-up, not bundled here.** The identical unguarded `data.get("tool_input"/"tool_response",
{}).get(...)` pattern this amendment fixes in `usage-snapshot.py` also exists, unchanged, in six sibling
hooks: `post-merge-tile-checkpoint.py`, `post-pr-merge-project.py`, `post-pr-merge-pull.py`,
`post-pr-merge-reclaim.py`, `post-tool-use.py`, `pr-merge-reminder.py` (confirmed via a repo-wide grep, not
by inspecting each file individually). Migrating all six — several safety-critical (board-Done moves,
canonical fast-forward, worktree disk reclaim) — is out of scope for dev-env#1028's own narrow report;
tracked as a follow-up issue + tile, filed after this amendment's PR merges, now a small mechanical
migration since `read_command`/`read_exit_code` and their tests already exist.

**General lesson (continuing Amendments 1, 6, and 9's, and directly extending Amendment 25's own).**
Amendment 25 built the trace mechanism to answer "which branch of `resolve_merge()` fired" — an implicit
assumption that `resolve_merge()` is always *reached*. A fourth live "no snapshot" occurrence, this time
with a trace mechanism already in place, is what surfaced that the assumption itself was the gap: an
observability tool built to explain a decision is blind to any failure that occurs before the tool's own
instrumentation point. The durable-mechanical-grep discipline Amendment 9 named ("grep the whole
`claude/scripts/` tree... before declaring any... sweep complete") applies here too, one layer removed:
`read_command_output`'s own docstring already stated its defensive guarantee in writing, in this exact
file, for weeks before this amendment — the gap wasn't unknown-unknown, it was one
`grep 'data.get("tool_response", {}).get('` away from being caught the same PR that hardened
`read_command_output` first landed.
**Post-review fix.** `/review` on the PR implementing this amendment (#1030) ran two independent passes (correctness/security; reliability/performance/maintainability) — both executed the real `main()` against this amendment's own diagnosed root-cause payload (`tool_input: None`, merge marker present in `tool_response`) rather than reasoning about it statically, and both independently found it still produced **zero trace entries** — the amendment as originally written prevented the crash but not the symptom it exists to fix. A destroyed `command` (`read_command()` correctly returns `""` for a malformed `tool_input`) is trivially `not_merge_shape` (`scan_top_level("")` is always `False`), so `resolve_merge("")` always returns `is_merge_shaped: False` and the ordinary trace-write guard (`if resolution["is_merge_shaped"]:`) never fires — reproducing dev-env#1028's exact symptom via a different mechanism than the original crash. Five findings, all fixed in the same PR before merge:

1. **The load-bearing one, independently confirmed by both review passes via execution.** `main()` now detects a present-but-non-dict `tool_input` directly, *before* ever calling `resolve_merge()` (which cannot help here — the command text it would classify no longer exists), and traces it under a new `reason: "malformed_payload"`. Since `tool_response` (unlike `tool_input`) is intact in this scenario, `output_has_merge_marker(output)` / `output_has_rest_merge_marker(output)` remain a reliable independent signal of whether a merge actually happened — used as `confirmed`. When confirmed, `main()` skips `resolve_merge()` entirely and falls through to the snapshot logic below exactly like any other confirmed merge, rather than merely logging and bailing.
2. **`cwd` had the identical unguarded-default gap**, but its crash landed *downstream* (`encode_cwd`/`find_session_jsonl`) rather than at the read site — reachable *after* a `confirmed: true` trace entry had already been written, producing a worse artifact than no record at all (a permanent claim that a merge was confirmed, with no snapshot to show for it). Fixed with a new `read_cwd()` helper in `_hookio.py`, mirroring `read_command`'s contract.
3. **The `classify_error` trace entry discarded the exception itself**, recording only that *something* threw. `except Exception:` is now `except Exception as exc:`, and the entry gains an `"error": f"{type(exc).__name__}: {exc}"` field — matching the shape `pre-auto-merge-checkpoint-gate.py`'s own catch-all handlers already use elsewhere in this repo.
4. **Both `read_command`/`read_exit_code`, and the pre-existing `read_command_output`, still crashed on a non-dict top-level `data`** (a list, string, or `null` — still valid JSON) — one level above the `tool_input`/`tool_response` guard, the identical silent-crash class this whole fix exists to close. All three now guard `isinstance(data, dict)` first. `main()` gained the same guard immediately after `json.loads()` succeeds, since `data.get("tool_name")` and the `cwd` read are not funneled through any of the three helpers.
5. **`_plausibly_merge_shaped`'s unbounded substring test** (`"gh" in lowered and "pr" in lowered`) matched ordinary words merely containing those letters (`git merge origin/print-highlights` contains both "gh" — inside "highlights" — and "pr" — inside "print" — as substrings, though neither is a real invocation). Given `_log_merge_trace` is a 500-entry ring buffer this hook writes to on *every* Bash/PowerShell call, an unbounded flood in exactly the failure-correlated scenario this fallback exists for (`resolve_merge()` throwing on a common command shape) risks evicting the genuine merge entries the log exists to preserve. Tightened to a word-bounded regex (`\bgh\b.*\bpr\b.*\bmerge\b|/pulls/\d+/merge\b`); the now-provably-unreachable `try/except` around the old `command.lower()` call (given `read_command`'s "always `str`" contract) is dropped rather than kept as dead defensive code.

**Also corrected, not code changes:** `read_exit_code`'s `default` parameter is now **required** (no default value) rather than defaulting to `-1` — review flagged that a convenience default would let a future drop-in migration at one of the `0`-default sibling sites (`post-tool-use.py`, `pr-merge-reminder.py`) silently flip an absent `exitCode` from `0` to `-1`, reintroducing the exact dev-env#557 misattribution bug via a migration that looks like a no-op. And the module-level docstring paragraph introducing `read_command`/`read_cwd`/`read_exit_code` was tightened — review found it read as if "several hooks" were migrated, when only `usage-snapshot.py` was; it now states the count precisely (fourteen more call sites confirmed via repo-wide grep, not migrated here) and cross-references the Follow-up section below.

**Follow-up scope corrected.** The original Follow-up paragraph named six sibling hooks. Review's own repo-wide grep — re-verified independently, anchored to an actual assignment statement rather than any text match — found **fourteen**, not six: the original six (`post-merge-tile-checkpoint.py`, `post-pr-merge-project.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `post-tool-use.py`, `pr-merge-reminder.py`) plus eight more (`pre-auto-merge-checkpoint-gate.py`, `pre-commit-branch-check.py`, `pre-merge-branch-check.py`, `pre-merge-findings-gate.py`, `pre-merge-message-check.py`, `pre-merge-numbering-check.py`, `pre-pr-create-check.py`, `pre-tool-use-worktree-path-check.py` — the last reading a different field, `_PATH_FIELD[tool_name]`, via the identical unguarded chain shape). Independently verified: `pre-merge-findings-gate.py`'s own `__main__` guard is `except Exception: sys.exit(0)` — a malformed `tool_input` on a `gh pr merge` command silently disables the ADR-028/ADR-039 findings gate itself, a fail-open blocking-merge-gate outcome materially worse than this amendment's own lost usage snapshot. The tracking issue filed after this PR merges lists all fourteen, ranks `pre-merge-findings-gate.py` first on fail-open grounds, and — per review's own suggestion — proposes a grep-based regression test (mirroring this repo's existing `test_no_crude_command_substring_checks.py`/`check-remote-read-hygiene.sh` pattern) asserting no `claude/scripts/*.py` file contains the unguarded chain shape, so the class becomes mechanically unrepresentable rather than merely documented.

**Coverage (post-review).** `test_hookio.py`: 5 new tests (non-dict-`data` guards on all four `read_*` helpers, `read_cwd`'s own coverage, and `read_exit_code`'s `default`-is-now-required contract) — 135 total (up from 130). `test_usage_snapshot.py`: 5 new tests, all executing the real `main()` end-to-end — the two `malformed_payload` scenarios (marker-confirmed and unconfirmed), `cwd: null` no longer crashing after a `confirmed: true` trace, non-dict top-level JSON, and the `_plausibly_merge_shaped` word-boundary regression case — plus an update to the existing `classify_error` test asserting the new `"error"` field — 53 total (up from 48). Every fix in this section was verified failing against the pre-fix code before being marked resolved, the same discipline the base amendment's own regression tests already followed.

**General lesson (extending this amendment's own, one level further).** The base amendment's lesson was that an observability tool built to explain a decision is blind to any failure before its own instrumentation point. Review found the fix for *that* gap had a gap of the identical shape, one step removed: preventing a crash is not the same as preserving the information the crash was destroying, and the two must be verified separately — by execution, not by re-reading the diff — before either is presented as closing the loop.

## Amendment 27 (2026-08-20) — closing dev-env#1031 Part 1: `pre-merge-findings-gate.py`'s own fail-open crash (dev-env#1032)

**Context.** Amendment 26's Follow-up section, corrected post-review, named fourteen sibling call sites carrying
the identical unguarded `data.get("tool_input"/"tool_response", {}).get(...)` chain `usage-snapshot.py` was fixed
onto `read_command`/`read_cwd`/`read_exit_code`. Of the fourteen, review singled out `pre-merge-findings-gate.py`
as materially worse than the rest: it is a **blocking merge gate** (ADR-028/ADR-039 — every `/review` finding
must be fixed-or-filed before merge), and its `__main__` guard is the same `except Exception: sys.exit(0)`
safe-exit convention every hook in this family uses. A malformed `tool_input` there does not just lose a side
effect (a board move, a fast-forward pull, a disk reclaim) the way it does for the other thirteen — it crashes
the gate's own dispatch *before* it ever evaluates whether findings are disposed, and the safe-exit guard
silently converts that crash into "gate passed." The tracking issue (dev-env#1031) filed after Amendment 26's PR
merged ranked this file first on exactly those fail-open grounds, ahead of the other thirteen as a batch
(dev-env#1033) — this amendment closes Part 1.

**The fix.** `main()` now: (1) guards `isinstance(data, dict)` immediately after `json.loads()` succeeds, mirroring
`usage-snapshot.py`'s own dev-env#1028 post-review fix; (2) reads `command = read_command(data)` instead of the
raw chained `.get()`; (3) reads `cwd = read_cwd(data) or None` instead of `data.get("cwd", "") or None` — not
itself part of the flagged grep pattern (a single, non-chained `.get()` never raises at the assignment site), but
hardened anyway since a non-string `cwd` still crashes *downstream* (`_fetch_pr_json`'s `subprocess.run(...,
cwd=cwd)`) exactly the way Amendment 26's post-review pass found for `usage-snapshot.py`'s own `cwd` gap.

**Honest scope — what this fix does and does NOT close.** Migrating `command` onto `read_command()` makes the
malformed-`tool_input` exit path *deterministic and testable* instead of an accident of exception propagation —
but, unlike `usage-snapshot.py`'s Amendment 26 post-review fix, it does **not** recover merge-intent detection
for the destroyed command. `read_command()` returns `""` for a malformed `tool_input`; `is_pr_merge_command("")`
correctly (if unhelpfully) classifies that as "not a merge" and exits 0 — the **same fail-open outcome** as the
pre-fix crash, reached via explicit business logic instead of an uncaught exception. The reason this file can't
match `usage-snapshot.py`'s fix shape: `usage-snapshot.py` is a **PostToolUse** hook, so even when `tool_input` is
destroyed, `tool_response.stdout`/`stderr` (the command's actual output) survives intact and gives
`output_has_merge_marker`/`output_has_rest_merge_marker` an independent post-execution signal to confirm a merge
happened. `pre-merge-findings-gate.py` is a **PreToolUse** hook — the command has not run yet, so there is no
`tool_response` and no alternative signal once the command text itself is gone. Considered and rejected: failing
CLOSED (blocking the tool call) on any malformed-`tool_input` Bash/PowerShell invocation. Without readable command
text there is no way to scope a block to merge commands specifically, so failing closed here would block *every*
Bash/PowerShell call on the same rare payload glitch, regardless of what command it carried — a materially worse,
broader regression than the narrow, now-explicitly-documented gap this leaves open (see the inline comment at the
`command = read_command(data)` call site for the same reasoning, kept in the code as well as here so a future
reader hits it without needing this ADR). This residual gap is structurally identical for
`pre-auto-merge-checkpoint-gate.py` (dev-env#1033, Part 2) — the only other PreToolUse gate among the fourteen
that extracts `command` before deciding whether a merge is even in play.

**Coverage.** `test-merge-findings-gate.sh` gains two new end-to-end cases run directly against `main()`'s stdin
dispatch (no `MERGE_GATE_TEST_JSON` seam — if the hook did not exit before `_fetch_pr_json`, either case would
attempt a real network call rather than cleanly returning 0): `tool_input: null` (the dev-env#1028 payload shape)
and a non-dict top-level JSON payload (a bare list). Both assert exit 0 with no crash/hang. `read_command`'s and
`read_cwd`'s own correctness (normal/missing/`None`/non-dict/non-string inputs) is already exhaustively covered in
`test_hookio.py` (Amendment 26) and is not re-tested per-caller here — this file's own coverage is scoped to
proving its `main()` dispatch reaches the helpers and doesn't crash, per `test_pre_merge_findings_gate.py`'s own
documented pure-helper-only convention (which explicitly excludes `main()`'s stdin plumbing, covered instead by
the shell test).

**Follow-up, not bundled here.** The remaining thirteen files (all fail-open by original design — informational/
advisory hooks whose crash loses a recoverable side effect, not a security gate) migrate in dev-env#1033, along
with the mechanical AST-based regression test (mirroring `test_no_crude_command_substring_checks.py`) asserting no
`claude/scripts/*.py` file contains the unguarded chain shape as a live assignment statement.

## Amendment 28 (2026-08-20) — closing dev-env#1031 Part 2: the remaining sibling hooks, plus the mechanical regression test (dev-env#1033)

**Context.** Amendment 27 closed Part 1 (`pre-merge-findings-gate.py` alone, on fail-open-blocking-gate
severity grounds). This amendment closes Part 2: the remaining thirteen files named in Amendment 26's
corrected Follow-up section — `post-merge-tile-checkpoint.py`, `post-pr-merge-project.py`,
`post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, `post-tool-use.py`, `pr-merge-reminder.py`,
`pre-auto-merge-checkpoint-gate.py`, `pre-commit-branch-check.py`, `pre-merge-branch-check.py`,
`pre-merge-message-check.py`, `pre-merge-numbering-check.py`, `pre-pr-create-check.py`, and
`pre-tool-use-worktree-path-check.py` — all now migrated onto `_hookio.read_command`/`read_cwd`/
`read_exit_code`. Every one of these thirteen fails open by original design (an advisory/informational
hook whose crash loses a recoverable side effect — a board move, a fast-forward pull, a disk reclaim, a
reminder), unlike Part 1's blocking merge gate — the severity gap Amendment 27 documented at length is
why this batch waited for a separate, lower-priority PR rather than being bundled into Part 1.

**The migration, file by file.** Same mechanical pattern throughout: replace the unguarded
`data.get("tool_input"/"tool_response", {}).get(...)` chain with `read_command(data)`/`read_exit_code(data,
default=...)`, replace `data.get("cwd", ...)` with `read_cwd(data)` (hardening even where the pre-fix line
itself couldn't crash — a non-chained `.get()` never raises at the assignment, but a non-string `cwd` still
crashes *downstream*, the exact class Amendment 26's post-review pass found for `usage-snapshot.py`'s own
`cwd` gap), and add an `isinstance(data, dict)` top-level guard immediately after `json.loads()` succeeds.
Three deviations from that uniform pattern, each with its own reasoning recorded inline at the call site
(not just here, so a future reader hits the reasoning without needing this ADR):

1. **`read_exit_code`'s `default` differs per file, verified against each file's own pre-fix literal, not
   copy-pasted.** `-1` for `post-merge-tile-checkpoint.py`, `post-pr-merge-project.py`,
   `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`; `0` for `post-tool-use.py` and
   `pr-merge-reminder.py` — the exact two files Amendment 26's own `read_exit_code` docstring already
   named as the `0`-default outliers, confirmed unchanged here. Getting this wrong would silently
   reintroduce the dev-env#557 misattribution bug a convenience default was specifically designed to
   prevent (see `read_exit_code`'s own docstring).
2. **`pre-tool-use-worktree-path-check.py` reads a computed field name** (`_PATH_FIELD[tool_name]` —
   `file_path` for Write/Edit, `notebook_path` for NotebookEdit), not literally `"command"`. Rather than
   widening `_hookio.read_command`'s signature for its only caller, or introducing a second,
   differently-named shared helper for a need only one file has, this file gets a small local
   `_read_tool_input_field(data, field)` wrapper mirroring `read_command`'s exact contract
   (`isinstance(data, dict)` → `isinstance(tool_input, dict)` → `isinstance(value, str)`, else `""`) —
   the same premature-parameterization caution `_hookio.py`'s own module comment on `mask_quoted_spans`
   already states for a heavily-tested shared primitive. `cwd` in this same file also migrates to
   `read_cwd()`, since a non-string `cwd` would otherwise crash downstream in `_match_worktree`/
   `_resolve_worktree_scope`'s regex/path operations, not at the read site.
3. **`pr-merge-reminder.py`'s `cwd` keeps its own `read_cwd(data) or "<unknown>"` form**, not the bare
   `read_cwd(data)` every other file gets. Its pre-fix default was the literal string `"<unknown>"` (not
   `""`), and that value is displayed verbatim in this hook's own reminder text (`f"  cwd: {cwd}\n"`) —
   falling back to `""` instead would silently change what a user reading the reminder sees for a
   missing/malformed `cwd`. The only behavioral divergence from the pre-fix code is a vanishingly rare,
   unreachable-in-production edge case (an explicitly-empty-string `cwd`, which Claude Code's hook
   contract never actually sends), documented inline rather than left as a silent behavior change.

**The one deliberate asymmetry (first revision — REVERSED post-review; see "post-review finding 3" below
for why): `pre-auto-merge-checkpoint-gate.py` does NOT get the `isinstance(data,
dict)` top-level guard.** Every other file in this batch (and Part 1's `pre-merge-findings-gate.py`) gets
this guard because they all fail OPEN on any uncaught exception — the guard changes nothing observable for
them (a non-dict `data` already produced a caught crash → exit 0; the guard just makes that path explicit
and testable instead of an accident of exception propagation). `pre-auto-merge-checkpoint-gate.py` is
different: its own `__main__` fails CLOSED on any uncaught exception (ADR-083 Decision point 3 — `--auto`
removes every other in-session backstop, so an unanticipated crash must block, not wave the merge through).
Adding the same guard there would *flip* a non-dict-top-level-payload scenario from fail-closed (block) to
fail-open (allow) — the wrong direction for a fail-closed gate. This migration therefore deliberately
leaves that one case to crash naturally into the file's own `_fail_closed(...)` handler, preserving ADR-083's
stricter posture for what is, in any case, a maximally out-of-contract payload shape (Claude Code's hook
contract always sends a JSON object at the top level — this is categorically less plausible than `tool_input`
specifically arriving malformed, dev-env#1028's actual confirmed shape). The `command = read_command(data)`
migration in this same file *does* apply uniformly, and carries its own residual-gap reasoning structurally
identical to Amendment 27's for `pre-merge-findings-gate.py`: this is the other PreToolUse merge gate among
the fourteen, so a malformed `tool_input` on a genuine `gh pr merge --auto` command still exits 0 (fails
open) rather than being confirmed-and-blocked — no `tool_response` exists yet to fall back on, and failing
closed for every malformed-`tool_input` Bash/PowerShell call (not just `--auto` merges) was rejected for the
same reason Amendment 27 rejected it: it would block every unrelated command on the same rare payload
glitch, a materially worse regression than the narrow gap it leaves open.

**Mechanical regression test (`test_sibling_hooks_hardened_io.py`), per `/review`'s own suggestion on PR
#1030 — first revision.** An AST-based scan, mirroring `test_no_crude_command_substring_checks.py`'s
detector/allowlist/self-test shape, asserting no `claude/scripts/*.py` file contains the unguarded chain as
a live expression. `_KNOWN_EXCEPTIONS` empty; confirmed as a genuine live check, not a tautology: run
against this PR's own branch *before* rebasing onto Part 1's merged fix, it correctly flagged
`pre-merge-findings-gate.py:206` as a live offense — proof the detector finds a real, currently-unfixed
instance of the shape, not just an absence it was written to expect.

**Malformed-payload smoke-test coverage — first revision.** Twelve of the thirteen files (excluding
`pre-tool-use-worktree-path-check.py`, own coverage in `test_worktree_path_check.py`) driven via
`subprocess.run([sys.executable, hook_path], ...)` with `tool_input: null` + `cwd: null` and a non-dict
top-level payload, asserting the resulting exit code.

**`/review` on this PR found both of the above had real, live gaps — corrected in the same PR before merge:**

1. **[correctness, both review passes] The AST detector required a bare `ast.Name` receiver and matched
   only the exact `X.get(key, {}).get(...)` chain shape.** Verified by direct execution against the live
   implementation: the two-statement form (`ti = data.get("tool_input", {})` on one line, `ti.get(...)` on a
   later line, no `isinstance` guard between them — the DOMINANT house style for this read elsewhere in
   `claude/scripts/`), the subscript outer form (`data.get("tool_input", {})["command"]`), a `dict()`
   default instead of `{}`, a non-`Name` base (`self.data.get(...)`, `payload[0].get(...)`,
   `json.loads(raw).get(...)`), and the bare `(X.get(key) or {}).get(...)` inline `BoolOp` form were all
   **MISS** — yet every one raises the identical `AttributeError`/`TypeError` on the exact dev-env#1028
   `tool_input: null` payload. Fixed with two independent detector arms (see `find_inline_offenses` /
   `find_two_statement_offenses` in the test file's own module docstring for the full design) —
   `_KNOWN_EXCEPTIONS` is now keyed on `(filename, field, LINE NUMBER)`, not just `(filename, field)`, since
   two distinct call sites in one file can now legitimately reuse the same field.
2. **[correctness, both review passes] The subprocess-based smoke tests did not discriminate pre-fix from
   post-fix behavior at all.** Verified empirically: running the PRE-FIX blobs from `3b7f9d1` (Part 1's
   merge commit) against the exact payloads the tests sent produced the IDENTICAL exit codes the tests
   asserted — because every hook's own `__main__` guard (`except Exception: sys.exit(0)`, or
   `_fail_closed()` → exit 2) launders a crash into the same exit code a correct, deliberate early-return
   also produces; an exit code observed from OUTSIDE the process cannot tell "handled cleanly" from
   "crashed, caught by the safe-exit guard" apart. Fixed by calling each hook's `main()` DIRECTLY (loading
   the module via `importlib.util.spec_from_file_location`, bypassing `__main__` entirely, mirroring
   `test_usage_snapshot.py`'s own `_run_main_capturing_trace` pattern) — a pre-fix crash now propagates as
   an uncaught Python exception IN the test process, a genuine failure. Re-verified after the fix by
   temporarily reverting one file (`pre-merge-message-check.py`) to its pre-fix blob and confirming the
   smoke test correctly failed with the expected `AttributeError`, then restoring it.
3. **[correctness, one review pass] `pre-auto-merge-checkpoint-gate.py`'s deliberate NON-guard was itself
   inconsistent, over-broad in its blast radius.** The first revision reasoned a non-dict top-level `data`
   was "more implausible" than a malformed `tool_input` and should therefore stay fail-CLOSED (crashing into
   `_fail_closed(...)`, exit 2) while `tool_input: null` was made fail-open (exit 0). Review found
   PLAUSIBILITY isn't the axis that matters — CONSEQUENCE is, and it's identical for both shapes: once
   either `data` or `tool_input` is unreadable, this hook has no way to tell whether the command it's
   looking at was ever a `gh pr merge --auto` in the first place (no `tool_response` exists yet at
   PreToolUse time). Crashing into `_fail_closed(...)` for the non-dict-`data` case blocked EVERY
   Bash/PowerShell call on a rare payload glitch — an ordinary `git status`, an `npm test` — with a "the
   --auto checkpoint gate crashed while evaluating this merge" message and remediation advice ("drop --auto
   and run a plain `gh pr merge`") nonsensical for whatever command was actually run. That is exactly the
   over-broad blast radius this same migration already rejects for the `tool_input` case — no principled
   reason to treat the two non-dict shapes in opposite directions. Fixed by adding the `isinstance(data,
   dict)` guard here too, bringing this file in line with its eleven siblings; its fail-CLOSED posture for
   every OTHER unanticipated exception (ADR-083) is untouched.
4. **[reliability, one review pass] Every migrated hook's `main()` calls `_hookutil.record_heartbeat(...)`
   unconditionally, and the direct-call redesign (finding 2 above) now runs that write IN the test
   process, against the developer's REAL `~/.claude/scratch/hook-heartbeat/`, on every test run.** A
   heartbeat write is exactly what `hook-liveness-check.py` (ADR-106) reads to judge whether a wired hook
   has gone silently quiet — post-tool-use.py's own months-long silent death (dev-env#377) is the motivating
   incident behind that mechanism — so running this suite would blind that detector for up to its 7-day
   cadence, for a growing set of hooks, on every future run. Fixed with a `HOOK_HEARTBEAT_DIR_OVERRIDE`
   environment-variable override added to `_hookutil.record_heartbeat` itself (checked at CALL time, not
   import time, so it applies to an already-loaded module reused across many direct calls in one test
   process, or propagates automatically to a subprocess child via `env=None`'s inherit-environment
   default); both `test_sibling_hooks_hardened_io.py` and `test_worktree_path_check.py` now set it for
   their whole run.
5. **[correctness, one review pass] The AST detector's own gap (finding 1) was not hypothetical — a
   repo-wide re-scan with the corrected detector found FIVE MORE files (six live sites) beyond the
   thirteen dev-env#1031 originally scoped, all using the `(data.get("tool_input") or {}).get(...)` variant
   the original assignment-anchored grep never matched:** `memory-write-advisory.py` (two sites — reads
   BOTH `file_path` and `content`, motivating `read_tool_input_field(data, field)`, a general form of
   `read_command` hoisted into `_hookio.py` in this same PR, with `read_command` becoming a thin wrapper
   over it — a second, independent caller is exactly the threshold `_hookio.py`'s own module comment on
   premature parameterization asks for before generalizing a shared primitive), `pre-tool-use-canonical-mutate-guard.py`
   (the SAFETY-CRITICAL canonical-mutate guard — ADR-071), `pre-tool-use-journal-compose-force-guard.py`
   (also fail-CLOSED, ADR-096 — already had its own `isinstance(data, dict)` top-level guard, so only its
   narrower `tool_input`-specific gap needed closing, not the finding-3 guard addition),
   `pre-tool-use-journal-draft-worktree-guard.py`, and `stub-push-archive-reminder.py` (needed the
   top-level guard added too, like `memory-write-advisory.py`). Given the identical bug class, an
   already-proven-safe fix pattern (fourteen precedents by this point), and that leaving them unfixed would
   have made this migration's OWN "no live offense remains" claim false, these five files were fixed in the
   same PR rather than deferred — judged to be within the ~75% scope-growth guard (5 additional files
   against the 13 already in flight, roughly 38%) and directly motivated by strengthening this PR's own
   regression test, not an unrelated tangent. `pre-tool-use-canonical-mutate-guard.py` and
   `pre-tool-use-journal-draft-worktree-guard.py` each already had their own `isinstance(data, dict)`
   top-level guard (like the journal-compose-force-guard case above), so only their `cmd =
   (data.get("tool_input") or {}).get("command", "") or ""` line needed migrating.
6. **[correctness, one review pass, deferred rather than fixed] `read_exit_code`'s `int(...)` coercion is a
   real, undocumented-until-now semantic change for the two `default=0` files specifically.** Pre-fix, a
   `tool_response` present as a dict with a present-but-non-int-coercible `exitCode` (e.g. `null`) returned
   the RAW value unchanged (`.get("exitCode", default)`'s default only substitutes on a MISSING key); `read_exit_code`
   coerces it to `default` instead. For the four `-1`-default files this is harmless (both `None` and `-1`
   equally satisfy a `!= 0` check downstream, so the boolean OUTCOME is unchanged); for `post-tool-use.py`
   and `pr-merge-reminder.py` specifically (`default=0`), a malformed-but-present `exitCode` now reads as
   "confirmed success" where it previously read as "not confirmed" — `post-tool-use.py`'s `if exit_code !=
   0: sys.exit(0)` gate no longer skips; `pr-merge-reminder.py`'s `should_confirm_via_gh()`
   dev-env#489/#504 live-confirmation fallback no longer fires. Narrower and less confirmed than the
   dev-env#1028 top-level shape (no observed incident for this exact sub-field malformation, only the same
   class of risk) — accepted as a documented, deliberately-scoped, pinned trade-off rather than building a
   bespoke dual-default helper for an unconfirmed edge case; see each file's own inline comment and the
   dedicated regression test pinning the current (accepted) behavior.

**Two findings filed as follow-ups rather than fixed in-PR** (a genuinely separate, larger design change —
introducing new shared `_hookio.py` control-flow abstractions — deserves its own careful review, not to be
bolted onto an already-large migration PR):
- `post-merge-tile-checkpoint.py`, `post-pr-merge-pull.py`, `post-pr-merge-reclaim.py`, and a near-variant
  in `post-pr-merge-project.py` share a byte-identical 12-line merge-confirmation preamble — the same shape
  that already produced Amendments 23 and 24 as multi-file sweeps. Filed:
  [dev-env#1036](https://github.com/brownm09/dev-env/issues/1036) — hoist into `_hookio.py` as
  `resolve_confirmed_merge(data) -> tuple[str, str, str] | None`.
- The 4-line payload-prologue + its rationale comment is now copy-pasted (in slightly-adapted form) across
  eleven files. Filed: [dev-env#1037](https://github.com/brownm09/dev-env/issues/1037) — hoist into
  `_hookio.py` as `read_bash_payload(raw) -> dict | None`.

**Coverage (post-review).** `test_sibling_hooks_hardened_io.py`: 23 tests (17 detector self-tests across
both arms, 2 diff-helper tests, the repo-wide gate, 5 smoke tests covering the malformed-payload matrix
across all 12 files it drives directly). `test_worktree_path_check.py`: 2 new tests (up from 16 to 18, net
of removing the now-redundant local-wrapper unit test once `read_tool_input_field` became a shared
`_hookio.py` helper with its own `test_hookio.py` coverage) — the same `tool_input:null`/non-dict-data
smoke-test pair, converted to the direct-call design. `test_hookio.py`: 5 new tests for
`read_tool_input_field` (140 total, up from 135). `test_hookutil.py`: 3 new tests for
`HOOK_HEARTBEAT_DIR_OVERRIDE` (52 total, up from 49). `test_post_tool_use.py`: 1 new test pinning the
`exit_code` coercion trade-off (finding 6) (92 total, up from 91). `test_pr_merge_reminder.py`: 1 new test,
same pin, plus the direct `should_confirm_via_gh` consequence (65 total, up from 64). The five additional
production files (finding 5) each re-ran their own existing test suite clean, with no regression for
well-formed input: `test_memory_write_advisory.py` (11), `test_canonical_mutate_guard.py` (87),
`test_pre_tool_use_journal_compose_force_guard.py` (59), `test_journal_draft_worktree_guard.py` (27),
`test_stub_push_archive_reminder.py` (33). Full suite: `py -3
claude/scripts/run-hook-tests.py` — see the PR body for the exact run and counts.

**General lesson.** Every finding here traces back to the same root: a test (or a migration decision) that
LOOKS like it verifies something can pass for reasons entirely unrelated to the thing it claims to verify —
a detector that matches the wrong AST shape, an exit code that a crash and a fix both produce, an asymmetry
justified by plausibility instead of consequence. None of these were caught by the test suite passing; all
were caught by an adversarial review pass that executed the code (or reasoned about what SPECIFICALLY makes
an assertion discriminating) rather than trusting that "tests added, tests pass" was sufficient. Directly
extends Amendment 26's own closing lesson ("preventing a crash is not the same as preserving the information
the crash was destroying") one level further: a regression test that cannot fail against the bug it exists
to catch is not a regression test, whatever its pass/fail output says.

**Closes dev-env#1031.** Both Part 1 (dev-env#1032, PR #1034) and Part 2 (dev-env#1033, this PR) are merged;
the top-level tracking issue closes with this PR.

