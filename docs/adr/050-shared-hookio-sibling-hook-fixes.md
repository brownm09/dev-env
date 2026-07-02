# ADR-050 — Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes

**Date:** 2026-06-21
**Status:** Accepted
**Amended:** 2026-07-01, 2026-07-02 (seven amendments — see Amendment sections below)
**Tags:** hooks, post-tool-use, tool_response, payload, github-project, automation, reliability, dry, usage-snapshot, pr-merge-reminder, gh-pr-view, api-fallback, message-dispatch, top-level-statement-scan, issue-create, false-positive, command-parsing, heredoc, regex, quote-tracking, canonical-mutate-guard, pre-tool-use

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

**General lesson (continuing Amendment 5's):** "a real API mismatch, not simple reuse" is a reason to
design the right shared primitive, not a reason to accept parallel implementations permanently. The
mismatch here was that `scan_top_level` exposed a reducer when a second caller needed a sequence —
the fix was to expose the sequence and make the reducer a trivial wrapper over it, the same shape as
`effective_merge_dir` remaining unshared beside `_effective_push_dir` (Amendment 5's own
scope-decision precedent) when two callers' needs *don't* converge. Here they did.
