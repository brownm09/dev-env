# ADR-050 — Shared `_hookio.read_command_output` + Sibling PostToolUse Hook Fixes

**Date:** 2026-06-21
**Status:** Accepted
**Amended:** 2026-07-01, 2026-07-02 (ten amendments — see Amendment sections below)
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
