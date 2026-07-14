# ADR-071: PreToolUse Hook to Block Git-Mutating Bash Commands in a Canonical (Non-Worktree) Checkout

**Date:** 2026-07-01 (amended 2026-07-04, 2026-07-05, 2026-07-14, 2026-07-14)
**Status:** Accepted
**Tags:** hooks, worktrees, pre-tool-use, bash, powershell, git, gh-cli, concurrency, canonical-checkout, rate-limit, redirect-target, orphaned-worktree

---

## Context

Claude Code sessions frequently work directly in a repo's **canonical checkout** — the main working
tree at e.g. `C:/Users/brown/Git/dev-env` — rather than in an isolated worktree, since worktree
isolation is opt-in, not the default (`EnterWorktree`'s own description fires "ONLY when explicitly
instructed"). Nothing in the harness prevents two separate Claude Code sessions from both working
directly in that same canonical checkout concurrently.

When they do, one session's `git checkout` (branch switch, not necessarily `-b`) silently moves HEAD
and the working tree out from under the other session, mid-turn, with no intervening user action.
**[dev-env#453](https://github.com/brownm09/dev-env/issues/453)** documents two occurrences, both
2026-07-01, both in career-playbook:

1. **Attribution scrambled.** Session A committed `747be13` on
   `chore/port-durable-rules-575-576`. Concurrently, Session B (unrelated work) ran
   `git checkout feat/exec-structural-audit` in the same checkout, moving HEAD out from under
   Session A — Session A's commit landed as a child of Session B's branch instead. Session B later
   squash-merged via career-playbook#587, carrying Session A's 4-file docs change in as an
   unattributed rider; the merging PR referenced only Session B's issues, and Session A's issues
   (career-playbook#575, #576) had to be closed manually after the fact with a resolution comment
   tracing the real path.
2. **Near-miss on a destructive action.** In the same incident, Session A's own branch was now based
   on a stale `origin/main` (missing Session B's since-merged work). Had Session A opened a PR from
   that branch without first diffing against the new `origin/main`, merging it would have **reverted
   Session B's legitimate work**. Only a pause to diff against `origin/main` before opening the PR
   caught it — the diff was empty (the change was already upstream).
3. **Second, independent occurrence (career-playbook#590/#593), same day.** Session A committed and
   pushed while Session B, concurrently in the same non-worktree checkout, ran
   `git checkout -b feat/582-record-overclaim-detectors`, again silently displacing Session A's
   working tree. This time non-destructive only because Session A's commit was already pushed —
   Session A finished review and merge entirely via remote reads (`gh pr diff`,
   `git show origin/<branch>:<path>`, `gh api`) rather than touching the displaced local tree. This
   occurrence also surfaced a **second failure dimension**: two sessions sharing one checkout also
   share — and can exhaust — the GitHub GraphQL API's 5,000/hr rate-limit bucket, which hit 0
   mid-session and disabled `gh pr merge`/`gh pr comment`/`gh pr view --json` (all GraphQL-backed)
   for both sessions.

**Neither existing worktree ADR covers this.** [ADR-066](066-worktree-session-safety-rules.md)
addresses a session *already in a worktree* accidentally `cd`-ing to the main repo — a different
shape (one session in a worktree, one in main), not two sessions both directly in main with no
worktree involved at all. [ADR-024](024-worktree-path-guard-hook.md)'s
`pre-tool-use-worktree-path-check.py` fires only on `Write`/`Edit`/`NotebookEdit` and only when `cwd`
matches the worktree pattern — it is a **complete no-op** for this exact scenario, since the
colliding sessions here are never in a worktree at all. career-playbook's own worktree-staleness ADR
(ADR-010) covers a *single* worktree going stale versus `origin/main`, not two concurrent sessions.

career-playbook is not uniquely broken — it is the highest-concurrency repo today (heavy
`spawn_task`/tile use, the batch cover-letter pipeline, several parallel interview pipelines) and so
surfaced the gap first. The same exposure exists for every repo using the Claude-managed worktree
convention, since nothing about the collision depends on career-playbook specifically.

---

## Decision

Add a new `PreToolUse(Bash)` hook, `pre-tool-use-canonical-mutate-guard.py`, that hard-blocks
git-mutating Bash commands whenever `cwd` resolves to a canonical (non-worktree) checkout root. This
is the **inverse complement** of ADR-024's hook: ADR-024 covers "session IS in a worktree, writes
escape to the canonical root"; this covers "session is NOT in a worktree at all, mutates the
canonical root directly" — the case ADR-024's hook does nothing for.

**Logic:**
1. Read stdin JSON. Fail open on anything unparseable, a missing/empty `cwd`, or a non-`Bash`
   `tool_name`.
2. If `cwd` matches the worktree path pattern (`.../.claude/worktrees/<name>`), exit 0 — out of
   scope; ADR-024's hook already covers that surface, and any command from inside a worktree is
   fine.
3. Split the command into logical segments (`&&`, `||`, `;`, `\n`, `|`). A bare `cd <path>` anywhere
   in the command takes the **whole command** out of scope (a `cd` persists across the rest of the
   shell invocation, so a later segment's real execution directory is unknown, not `cwd`); a
   `git -C <path>` / `git --git-dir=<path>` flag takes only **that segment** out of scope (it
   redirects a single invocation, not the shell's directory).
4. Classify each remaining segment, anchored at the segment start (not a substring match — the
   career-playbook #442 heredoc-mention lesson) against the mutating-verb list below.
5. If no segment is mutating, exit 0. If a segment is mutating, check for the
   `ALLOW_CANONICAL_MUTATE=1` override token anywhere in the command — exit 0 if present.
6. Resolve the git toplevel for `cwd` via `git -C <cwd> rev-parse --show-toplevel` (same defensive
   subprocess pattern as ADR-024: `import _winsubp`, try/except, 10s timeout). Fail open if git
   can't resolve one at all — that toplevel, once resolved, *is* the canonical root by construction
   (cwd already failed the worktree-pattern check in step 2).
7. Exit 2 with a blocking `{"reason": ...}` JSON naming the matched command, the canonical root, why
   it's dangerous (cites dev-env#453's two incidents and the rate-limit finding), and the two
   remedies: isolate via `EnterWorktree`/`git worktree add`, or override with
   `ALLOW_CANONICAL_MUTATE=1 <command>` after confirming no other session is active in that checkout.

**Mutating verbs blocked:** `checkout` (branch switch or `-b`; a bare `git checkout <arg>` without
`--` is conservatively treated as a possible branch argument and blocked — use
`git checkout -- <path>` for file restores), `switch`, `commit`, `merge`, `rebase`, `reset`,
`cherry-pick`, `revert`, `stash pop`/`stash apply`, `branch -d`/`-D`/`--delete`, and `pull`
**except** when the same segment also contains `--ff-only` (fast-forwarding a canonical checkout to
`origin/main` is the common, safe sync operation and must stay zero-friction).

**Explicitly NOT blocked (stays zero-friction):** `status`, `log`, `diff`, `show`, `fetch`,
`branch --show-current`, `rev-parse`, `ls-tree`, `blame`, `remote -v`, plain `git branch` (list or
create-without-switch), `git stash list`/`show`, `git checkout -- <path>`, `git pull --ff-only`, and
anything non-git. Plain Read/Grep/Glob against a canonical checkout is untouched entirely, since this
hook only matches `Bash`.

**Wired** as a 5th entry into the *existing* `"matcher": "Bash"` array in `claude/settings.json`
(which already stacks four command hooks) — the established pattern, not a new one.

---

## Judgment calls

### New script, not folded into ADR-024's existing hook

Different matcher (`Bash`, not `Write`/`Edit`/`NotebookEdit`), a near-inverse trigger condition
(fires when `cwd` is *outside* a worktree, not inside), and a different failure mode (a mutating
command, not a mis-targeted file path). Folding the two into one script would conflate two distinct
decision trees for a saving of one file.

### Block (exit 2), not rewrite or warn-only

Consistent with ADR-024 and the two career-playbook precedent hooks (`block-artifact-merge.py`,
`block-letter-violations.py`): silently rewriting the command or merely warning would repeat the same
class of silent-failure risk this hook exists to close. A hard block forces the model to either
isolate into a worktree or make a deliberate, visible override choice — both of which surface in the
session transcript.

### `cd` takes the whole command out of scope; `-C`/`--git-dir` takes only its own segment

These are not the same redirect shape. `cd <path> && git checkout -b foo` really does execute the
checkout in `<path>`, not in `cwd` — the `cd` persists for every later `&&`-chained segment in the
same shell invocation, so once a `cd` appears anywhere, this hook cannot determine any later
segment's real execution directory and must treat the whole command as out of scope. `git -C <path>
checkout -b foo`, by contrast, redirects only that single git invocation — it does not change the
shell's directory, so a *different*, non-redirected mutating segment later in the same command must
still be caught. Collapsing both into one per-segment skip (the initial implementation) mis-classified
the `cd`-chained case; this ADR's decision documents the corrected two-tier scope.

### Both redirect shapes are still an unresolved v1 gap, not a false-safety claim

A command that `cd`s or `-C`s *into* the canonical root **from a worktree's Bash session** is not
caught by either the `cd`-scope or `-C`-scope logic above — those exist to prevent the hook from
mis-flagging a segment against the wrong directory, not to extend coverage to redirected commands.
That gap requires deliberate, visible authorship (typing an explicit `cd`/`-C` to another repo)
rather than the silent default-cwd collision #453 documents, so leaving it uncovered in v1 is an
acceptable scope-limiting judgment — the same class of deferral ADR-024 made for its own Bash
coverage, and extendable later if it recurs in practice.

### Bare `git checkout <path>` without `--` is conservatively blocked

Could be a branch name or a file path — the hook cannot distinguish without a working-tree lookup,
which would add a git spawn to a read-only-feeling command class. Blocking is the conservative
direction (a legitimate file-restore false-positive costs one override, while allowing a branch
switch through would defeat the hook's purpose); `git checkout -- <path>` stays the zero-friction
form.

### `pull` blocked unless `--ff-only` is present

Narrower than blocking all pulls: fast-forwarding a canonical checkout to `origin/main` is the
common, safe sync operation and would otherwise force a needless override on every session start.
Any other pull form (merge pull, pull with a rebase flag, pull from a non-default remote/branch) can
mutate history or the working tree in ways that collide, so it stays blocked.

### Universal scope (every repo), not per-repo opt-in

Matches ADR-024's own stated universal scope — the collision risk is a harness-level property (any
repo using the Claude-managed worktree convention is exposed), not a career-playbook-specific one.
career-playbook surfaced it first only because it is currently the highest-concurrency repo.

### New ADR, not an ADR-066 addendum

ADR-066 bundled three rules because they were "discovered together by one audit, share a single
theme, and each is a short prose rule" (ADR-066's own rejected-alternatives reasoning). None of those
conditions hold here: #453 was discovered independently, a day after ADR-066 shipped, and this
change ships a hook file + test file + settings wiring — the same shape ADR-024 itself flagged as
ADR-warranting, not a short prose rule. Follows ADR-024's template directly since it is the same kind
of decision.

---

## Consequences

- **Git-mutating Bash commands issued directly in a canonical checkout now fail immediately** with a
  clear message and two remedies, instead of silently thrashing HEAD out from under a concurrent
  session.
- **No-op outside canonical roots** — the hook exits 0 instantly for any command from inside a
  worktree, and fails open for any cwd that isn't a git repo at all.
- **Coverage gap remains for `cd`/`-C`/`--git-dir` redirects into the canonical root from elsewhere**
  — deliberately deferred per the judgment calls above; extend if recurrence is observed.
- **Testing.** `claude/scripts/tests/test_canonical_mutate_guard.py` — pure-function coverage of the
  full mutating-verb matrix and the read-only allowlist, the segment-split/anchor and two-tier
  redirect-scope behavior, plus subprocess end-to-end coverage of the block/allow/override/fail-open
  paths against a real throwaway git repo. `claude/scripts/tests/test_worktree_path_check.py`
  (ADR-024's hook) re-run unmodified as a regression check — the two hooks do not interfere, since
  their trigger conditions are mutually exclusive by construction (worktree cwd vs. non-worktree
  cwd). Since dev-env#511 ([ADR-050](050-shared-hookio-sibling-hook-fixes.md) Amendment 7), segment
  splitting comes from the shared `_hookio.split_top_level` engine rather than this hook's own regex
  splitter — same block/allow behavior, now with quote-tracking and general heredoc-opacity instead
  of the narrower `$(cat <<MARKER...)`-only stripping.
- **Observability.** The block reason is written as hook JSON output to **stderr** — Claude Code
  discards a `PreToolUse` hook's stdout on exit code 2 and surfaces only stderr to the model, so
  stdout would have silently hidden the reason. No separate log needed; a blocked attempt is
  visible in the same session transcript that triggered it, on the same stream career-playbook's
  `block-artifact-merge.py`/`block-letter-violations.py` already use for this reason.
- **Security.** The `ALLOW_CANONICAL_MUTATE=1` override is a deliberate, visible bypass — typed
  inline on the command the model is about to run — matching the override pattern of both
  career-playbook precedent hooks (`block-artifact-merge.py`'s `ALLOW_ARTIFACT_MERGE=1`,
  `block-letter-violations.py`'s `ALLOW_LETTER_VIOLATIONS=1`) and ADR-024's own recovery-command
  pattern. It is not a hidden bypass; anyone reading the transcript sees exactly when and why the
  guard was overridden.
- **Resilience.** The hook fails open on every ambiguous or unresolvable input (malformed JSON,
  missing/empty cwd, non-git cwd, git unavailable) — it can never wedge an unrelated Bash call, only
  ever block when it positively identifies a mutating command in a positively-resolved canonical
  root.
- **Performance.** One `git rev-parse --show-toplevel` subprocess per Bash call that both (a) has a
  non-worktree cwd and (b) contains at least one candidate-mutating segment — the common case (most
  Bash calls are non-git, or read-only git) never reaches the subprocess at all, since the segment
  classification runs first and is pure string matching.
- **Data integrity.** N/A — this hook only ever blocks or allows a command; it never rewrites,
  retries, or otherwise mutates the command itself.
- **Recovery runbook** added to [`docs/REFERENCE.md`](../REFERENCE.md) → Git Workflow Runbooks,
  covering the case this hook can't prevent (a manual terminal session, or a redirected command) —
  the reflog-reconstruction and remote-only-recovery sequence both #453 incidents actually used.
- **ADR warranted** because the hook is a new file under `claude/scripts/`, is wired in
  `claude/settings.json`, and establishes a harness-level safety invariant applicable to all repos
  using Claude-managed worktrees — the same warranting shape as ADR-024.

---

## References

- `claude/scripts/pre-tool-use-canonical-mutate-guard.py` — implementation
- `claude/scripts/tests/test_canonical_mutate_guard.py` — self-test
- `claude/settings.json` — hook wiring (5th entry in the `Bash` matcher array)
- `claude/CLAUDE.md` — Git Workflow bullet stating the invariant
- [`docs/REFERENCE.md`](../REFERENCE.md) → Git Workflow Runbooks — recovery runbook for what the hook
  can't catch
- [ADR-024](024-worktree-path-guard-hook.md) — the sibling hook this one complements (inverse
  trigger condition)
- [ADR-066](066-worktree-session-safety-rules.md) — worktree session safety rules; addresses a
  different collision shape (worktree vs. main), not two sessions both in main
- [ADR-050](050-shared-hookio-sibling-hook-fixes.md) Amendment 7 — this hook's segment splitting was
  converged onto the shared `_hookio.split_top_level` engine (dev-env#511), fixing two false
  positives the original regex-based splitter had (a quoted `&&`/`|` containing a fake mutating verb;
  a bare heredoc body line starting with one)
- `brownm09/career-playbook/.claude/hooks/block-artifact-merge.py`,
  `block-letter-violations.py` — segment-split/anchor pattern and override-token style this hook
  follows
- [dev-env#453](https://github.com/brownm09/dev-env/issues/453) — motivating issue, both incidents,
  and the rate-limit finding
- career-playbook issue #442 (referenced via the precedent hooks) — the heredoc-mention
  false-positive lesson behind the segment-anchoring design
- [Claude Code Hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks) — hook exit
  codes and JSON output format
- [dev-env#558](https://github.com/brownm09/dev-env/issues/558) — Amendment 1's motivating gap:
  `gh pr merge -d`/`--delete-branch` was invisible to the guard
- [`gh pr merge` manual](https://cli.github.com/manual/gh_pr_merge) — documents `-d`/`--delete-branch`:
  "Delete the local and remote branch after merge"

---

## Amendment 1 (2026-07-04) — `gh pr merge -d`/`--delete-branch` reaches the same harm model through a `gh` invocation (dev-env#558)

### The gap

`is_mutating_segment()` only classifies `git`/`git.exe`-prefixed invocations (see `_GIT_INVOCATION_RE`
and this file's own "Mutating verbs blocked" list above) — the command surface this hook watches for
was scoped to literal `git` verbs from the start. `gh pr merge --help` documents:

```
-d, --delete-branch          Delete the local and remote branch after merge
```

Run from the branch it's merging, `gh pr merge -d`/`--delete-branch` must locally check out the base
branch and delete the local branch to fulfil `--delete-branch` (a checked-out branch can't be deleted)
— **the exact same silent local-HEAD-thrash harm model this hook already blocks for `git
checkout`/`git branch -d`**, just reached through a `gh` invocation instead of a literal `git` verb. A
`gh pr merge -d` issued directly in a canonical (non-worktree) checkout was, before this amendment,
completely invisible to the guard: no block, no override needed, the same silent collision
dev-env#453 documents for `git checkout` — just via a different CLI binary. This repo's own documented
convention (`gh pr merge --squash --delete-branch`, used throughout `claude/CLAUDE.md`'s Git Workflow
section) is the common path that hits this gap.

A **bare** `gh pr merge` (no `-d`/`--delete-branch`) merges only remotely via the GitHub API and
touches no local state at all — same class as `git push`/`gh pr create` in dev-env#558's original
investigation (which explicitly concluded those stay unblocked, since GitHub/git already reject
double-merge/duplicate-PR loudly at the remote layer). That conclusion is unchanged by this amendment;
only the specific `-d`/`--delete-branch` flag combination is newly in scope.

### The fix

Added a new, separate classifier function, `is_mutating_gh_segment(segment)`, alongside the existing
`is_mutating_segment()` — **not folded into it**, preserving that function's specific, documented
contract as a *git*-invocation classifier. The new function:

- Matches a `gh`/`gh.exe` invocation at the start of the (env-stripped) segment's first physical line
  only, via a new `_GH_INVOCATION_RE` mirroring `_GIT_INVOCATION_RE`'s shape exactly, and reusing the
  same `_strip_leading_env()` + `_first_line()` helpers `is_mutating_segment()` already uses — so a
  heredoc/`$()` body merely *mentioning* "gh pr merge -d" as prose (e.g. this very commit's message)
  cannot trigger it, the identical career-playbook #442 anchoring guarantee.
- Returns True only when that invocation's subcommand is `pr merge` **and** its remaining arguments
  contain `-d` or `--delete-branch` as a standalone token (not a substring of a longer flag).
- Deliberately does **not** special-case a `--repo owner/repo` flag on `gh pr merge -d` — matches this
  hook's own established "block when in doubt" judgment call for a bare `git checkout <path>` (see
  Judgment calls above).

Wired into `classify()`'s existing per-segment loop: a segment matching *either*
`is_mutating_segment(seg)` **or** `is_mutating_gh_segment(seg)` is treated as the mutating match — same
severity (block, exit 2), same existing `cd`-scope-out / `-C`/`--git-dir`-redirect-scope /
`ALLOW_CANONICAL_MUTATE=1`-override machinery in `classify()`, `main()`, and `_has_override()`, all of
which already operate generically on the segment list and needed **zero** changes. `_REDIRECT_RE` (git
`-C`/`--git-dir` skip) is git-specific by construction (its pattern requires the literal word `git`) and
never matches a `gh` segment, so no interaction to reason about there either.

The `main()` block-message `reason` text previously opened with "a git-mutating command was issued
directly..." — technically inaccurate for a `gh pr merge -d` match (the invoked binary is `gh`, not
`git`, even though it mutates git state). Reworded to "a command that mutates local git state was
issued directly..." — accurate for both invocation shapes, no other change to the message's structure,
severity, or remedies.

### Why this is an amendment, not a new ADR

Same harm model (silent local-HEAD-thrash between two sessions sharing a canonical checkout), same
severity (hard block, exit 2), same file, same hook, same override/worktree-scope machinery — only a
previously-unrecognized command surface reaching that already-decided harm model through a different
CLI binary. This mirrors [ADR-050](050-shared-hookio-sibling-hook-fixes.md)'s own amendment
convention for extending an already-shipped hook's coverage (e.g. Amendment 6's "completing the sweep"
pattern) rather than re-litigating the original decision.

### Sync-location update

This hook's module docstring warns the mutating-verb list is independently re-spelled in four places
that must stay in sync (see the `NOTE:` comment inside `is_mutating_segment()`). All four were updated
in the same PR as this amendment:

1. The module docstring in `pre-tool-use-canonical-mutate-guard.py` ("Mutating verbs blocked:" section)
   — added a paragraph documenting the `gh pr merge -d`/`--delete-branch` addition and its zero-friction
   bare-`gh pr merge` counterpart.
2. `claude/CLAUDE.md`'s "Never mutate git state directly..." bullet — added the same clause plus this
   issue's cross-reference alongside dev-env#453.
3. This ADR's own "Mutating verbs blocked:" line (in the Decision section, above) — left **unedited**
   for history; this Amendment section is the addition, per this repo's established amended-ADR
   convention (see ADR-058, ADR-050).
4. `docs/REFERENCE.md`'s ADR-071 Hooks-table pointer — extended the trigger-condition cell with the
   `gh pr merge -d`/`--delete-branch` clause.

### Coverage

`claude/scripts/tests/test_canonical_mutate_guard.py` gains 10 new tests, split across the file's
existing two-layer convention:

- **Pure-function layer:** `is_mutating_gh_segment()` classifies `gh pr merge --delete-branch`, `-d`
  (either flag, any position among other flags) as mutating, and a bare `gh pr merge` /
  `gh pr merge --squash` / `gh pr merge --auto` / non-merge `gh pr`/`gh issue` commands as safe;
  `classify()` flags a `gh pr merge --delete-branch` segment among otherwise-safe segments and allows a
  delete-branch-less one; the `ALLOW_CANONICAL_MUTATE=1` override bypasses a `gh pr merge -d` match
  exactly like a git-verb match; a heredoc body merely mentioning "gh pr merge -d" as prose does not
  trigger (mirrors the existing career-playbook #442 heredoc-mention test).
- **End-to-end `main()`-via-subprocess layer:** `gh pr merge --delete-branch` and `gh pr merge -d` from
  a canonical (non-worktree) throwaway git repo both exit 2 with the reason on stderr and the matched
  command named in it; a bare `gh pr merge` / `gh pr merge --squash` from the same canonical root exit
  0; the same `gh pr merge --delete-branch` command from a worktree-pattern cwd exits 0 (out of scope,
  unchanged — ADR-024's hook covers that surface); the override token bypasses the block end-to-end.

All 38 pre-existing tests in that file continue to pass unchanged. The AST-based
`test_no_crude_command_substring_checks.py` repo-wide gate (dev-env#534/#539, ADR-050 Amendment 11) was
also re-run and passes — the new classifier uses tokenized parsing (`rest.split()` + membership checks
against `_GH_DELETE_BRANCH_FLAGS`), not a crude `"<literal>" in command` substring test.

---

## Amendment 2 (2026-07-05) — resolve a `-C`/`--git-dir`/`--work-tree` redirect target and apply the canonical-root check to it, not just cwd (dev-env#576)

### The gap

The original hook is **cwd-centric**: it blocks a mutating verb only when *cwd itself* resolves to a
canonical (non-worktree) checkout. A command that mutates a *different* canonical checkout via a
`git -C <path>` / `--git-dir=<path>` / `--work-tree=<path>` redirect was let through by **two
independent mechanisms**, both confirmed by reading the deciding code:

1. **Worktree short-circuit.** `main()` ran `if _WORKTREE_RE.search(cwd): sys.exit(0)` as its first
   substantive check, so a worktree cwd exited 0 *before the command was ever parsed*.
2. **`_REDIRECT_RE` segment-skip.** Even from a non-worktree cwd, `classify()` matched `git -C <path>`
   / `git --git-dir=<path>` and `continue`d, deliberately skipping the segment without ever resolving
   the target. (`--work-tree=` was not even in `_REDIRECT_RE`, and `git --work-tree=<path> commit`
   separately misclassified as non-mutating — the verb detector saw the leading flag, not `commit`.)

Both the original module docstring and this ADR's "Both redirect shapes are still an unresolved v1
gap" judgment call documented this as a deliberate v1 deferral, framed as *"extend if it recurs in
practice."*

### The incident (the recurrence)

**[dev-env#576](https://github.com/brownm09/dev-env/issues/576)** (2026-07-05): during engineering-journal
bookkeeping from a win11-init-tools **worktree**, `git -C C:/Users/brown/Git/engineering-journal pull`
ran un-blocked while another concurrent session was actively mutating that same shared canonical
journal checkout (its branch flipped `draft/2026-07-03` → `draft/2026-07-02` between two read-only
checks seconds apart, with a large uncommitted stub/manifest set). No damage — git's own "local
changes would be overwritten" safety aborted the pull — but the guard that exists to prevent exactly
this collision never fired. This is the "recurs in practice" trigger the v1 deferral named. (Same
command shape as **[dev-env#573](https://github.com/brownm09/dev-env/issues/573)**, a *distinct*
harness cwd-tracking bug; this fix incidentally blocks the mutating cross-repo command that triggers
#573's cwd revert.)

### The fix — make the guard target-aware

- **`_parse_git_prefix(tokens)`** replaces `_skip_git_level_flags`: it walks the same git-level options
  (`-c <v>`, `--no-optional-locks`, …) **and** the redirect flags `-C`/`--git-dir`/`--work-tree` (both
  `=` and space forms), returning `(redirect_dirs, remaining_tokens)`. Consuming the redirect flags as
  git-level options simultaneously fixes the `--work-tree=` misclassification (the real verb now lands
  at `tokens[0]`) and captures the target dir. `--git-dir=…/.git` is normalized to its parent (the
  worktree top).
- **`find_mutating_segments()`** replaces `classify()`'s single-match return with an ordered list of
  `{"segment", "redirect_dirs"}` descriptors, staying pure/offline: it captures the redirect dirs by
  string work only. Resolving them to canonical roots (a `git rev-parse` subprocess) is deliberately
  the caller's (`main()`'s) job, so the pure-function test layer never shells out. `classify()` remains
  as a thin compatibility wrapper (first mutating segment string) for the pure tests.
- **`main()`** no longer blanket-exits on a worktree cwd. A worktree cwd with **no** redirecting
  mutating segment is cleared cheaply (the common in-worktree case, still subprocess-free); a worktree
  cwd **with** such a redirect falls through. For each mutating segment: an *ambient* (no-redirect)
  segment is blockable iff cwd is canonical (the original behavior); a *redirect* segment is blockable
  iff a target resolves — via `_blockable_redirect_root()` — to a canonical (non-worktree) root that is
  not carve-out-exempt, **regardless of cwd**. The `ALLOW_CANONICAL_MUTATE=1` override and all fail-open
  paths are unchanged.

### The journal carve-out (`_REDIRECT_TARGET_ALLOWLIST`) — and reconciliation with ADR-082

The documented engineering-journal stub workflow (global `CLAUDE.md` Engineering Journal section +
[ADR-066](066-worktree-session-safety-rules.md)) **automatically** runs
`git -C <journal-canonical> checkout/commit/pull` on every PR open/merge. A naive "resolve the target,
block if canonical" would block that automated path, forcing `ALLOW_CANONICAL_MUTATE=1` onto it —
untenable (it trains reflexive override use and would require a large workflow-doc rewrite). So a
narrow, **temporary** carve-out (`_REDIRECT_TARGET_ALLOWLIST = {"engineering-journal"}`, matched by
resolved-toplevel basename) exempts that one checkout. Consequence, stated plainly: the incident's
*exact* command stays allowed by the carve-out — the general gap closes for every *other* repo (the
actual [dev-env#453](https://github.com/brownm09/dev-env/issues/453) collision surface), and the
journal's real fix is worktree isolation, tracked to **[dev-env#346](https://github.com/brownm09/dev-env/issues/346)**;
removing the carve-out is that issue's job.

**This amendment deliberately revisits a decision ADR-082 recorded as rejected.**
[ADR-082](082-journal-compose-worktree-isolation.md) → *Alternatives rejected* → "Extend ADR-071's
guard to parse into `git -C` targets" rejected the extension on two grounds: (a) `git -C` is
"deliberate, visible authorship" distinct from the silent default-cwd collision the guard catches, and
(b) parsing redirect targets "would also block compose's own legitimate, deliberate cross-repo
operations." Both are addressed rather than ignored:

- **(a)** The #576 incident is new evidence that the "deliberate authorship" distinction is
  *incomplete*: the author deliberately typed `git -C <journal> pull`, but the *collision* with a
  concurrent session mutating the same shared checkout was still silent and unintended. Deliberately
  typing the redirect does not make the shared-checkout mutation safe. That is precisely the "extend if
  it recurs in practice" condition the v1 deferral (and, implicitly, ADR-082's rejection) left open.
- **(b)** The carve-out surgically preserves exactly those legitimate journal cross-repo operations, so
  the concern that motivated ADR-082's rejection does not materialize. ADR-082's own worktree-isolation
  of *compose* is orthogonal and unaffected — it moved compose's git work into a worktree, while the
  *stub* workflow that the carve-out protects still runs `git -C <journal-canonical>` and is what #346
  will eventually migrate.

ADR-082's rejected-alternative entry is updated in the same PR to forward-reference this amendment, so
the two ADRs do not read as contradictory.

### Why an amendment, not a new ADR

Same harm model (silent shared-canonical-checkout collision between two sessions), same severity (hard
block, exit 2), same file, same hook, same override/worktree-scope machinery — only the *scope of what
counts as "the canonical root in question"* is widened from cwd to cwd-or-redirect-target. Mirrors
Amendment 1's "extend an already-shipped hook's coverage" convention rather than re-litigating the
original decision.

### Sync-location update

The four re-spelled locations the module docstring's `NOTE:` names were all updated in this PR (the
*mutating-verb list* itself is unchanged; what changed is the *target-awareness* documented alongside
it):

1. The module docstring in `pre-tool-use-canonical-mutate-guard.py` — logic steps 2/3/6 and the
   coverage note now describe redirect-target resolution and the carve-out (the `cd`-into-canonical
   case remains the sole documented v1 gap).
2. `claude/CLAUDE.md`'s "Never mutate git state directly…" bullet — added the
   `-C`/`--git-dir`/`--work-tree`-into-canonical clause and the #576 cross-reference.
3. This ADR's own "Both redirect shapes are still an unresolved v1 gap" Judgment call — left
   **unedited** for history; this Amendment section is the addition, per this repo's amended-ADR
   convention.
4. `docs/REFERENCE.md`'s ADR-071 Hooks-table trigger cell and the Git Workflow Runbook "Prevention"
   note — updated to reflect the redirect coverage and the narrowed v1 gap.

### Review hardening (dev-env#576/PR#584)

`/review` on this PR (Opus-model correctness/security and reliability/performance/maintainability
subagents, each finding independently verified end-to-end before being accepted) found the first
implementation of the fix above was itself incomplete in three ways, plus two narrower scope-precision
gaps — all fixed in the same PR before merge, since each was small, self-contained, and introduced by
this branch:

1. **Quoted redirect paths with whitespace defeated the block.** `_git_rest_tokens` tokenized with a
   naive `rest.split()` — a redirect value containing a space (e.g. `-C "C:/Program Files/repo"`, a
   common shape on the exact platform this hook targets) shattered into multiple bogus tokens, so the
   captured "target dir" was mangled and the invocation was never even recognized as mutating. Fixed by
   a new `_tokenize()` helper: `shlex.split(rest, posix=True)` with a fallback to the prior plain
   `.split()` on `ValueError` — the fallback is not just defensive, it is *required*, since `rest` is
   only ever a segment's first physical line and a real multi-line heredoc/command-substitution segment
   (e.g. `git commit -m "$(cat <<'EOF' ...)"`) truncates mid-quote right there, which `shlex` correctly
   flags as unbalanced.
2. **Relative redirect targets resolved against the wrong process's cwd.** `_resolve_git_toplevel(d)`
   was called with the raw captured value; for a relative `d` (`git -C ../other-repo`, or `--git-dir=.git`
   after normalization), the `git` subprocess it spawns inherits the *hook script's own* cwd (whatever
   directory happens to launch it), not the Bash command's actual `cwd` from the PreToolUse payload — so
   a relative redirect into a canonical root silently missed the block. Fixed by resolving a non-absolute
   redirect value against the payload `cwd` (`os.path.isabs()` guard + `os.path.join()`) before handing
   it to `_resolve_git_toplevel`; `git rev-parse --show-toplevel` canonicalizes any `..`/`.` segments in
   the joined path itself.
3. **A null byte in a redirect value crashed the hook instead of failing open.** `_resolve_git_toplevel`'s
   `except (FileNotFoundError, subprocess.TimeoutExpired, OSError)` tuple didn't include `ValueError`,
   which `subprocess.run`'s `Popen` raises on an embedded null character. This was safe when the only
   caller passed harness-provided `cwd`; it stopped being safe once this PR routed a second,
   command-string-derived value through the same function — command strings are far less constrained
   than a harness-provided cwd. Fixed by adding `ValueError` to the tuple.
4. **The journal carve-out matched on basename alone.** `_is_allowlisted_root` exempted *any* canonical
   checkout anywhere on disk whose last path segment happened to be named `engineering-journal`, not
   just the one intended shared checkout. Hardened to an exact (separator- and case-normalized) path
   match — consistent with, not a new departure from, this codebase's existing convention of hardcoding
   this exact path elsewhere (the global `CLAUDE.md` Engineering Journal section). The real path is
   overridable via a `CANONICAL_MUTATE_GUARD_JOURNAL_PATH` environment variable solely so the test suite
   can point the carve-out at a disposable temp directory instead of ever touching the developer's real
   engineering-journal checkout.
5. **No memoization across redirect resolutions.** A command with several `&&`-chained mutating segments,
   each carrying several redirect flags, could spawn one `git rev-parse` subprocess per redirect-dir
   *occurrence* rather than per distinct directory — a crafted worst case spawned 15 subprocesses for one
   command. Bounded and adversarial-only (every ordinary Bash call still triggers 0 or 1 spawn, unchanged),
   but cheap to close: a dict-based memo shared across `main()`'s per-segment loop now resolves each
   distinct directory at most once per Bash call.

None of these five change the *shape* of the decision this amendment describes above (target-aware
redirect resolution, cwd-or-target must be canonical to block, the journal carve-out, the fail-open
guarantee) — they correct the implementation to actually deliver it.

### Coverage

`claude/scripts/tests/test_canonical_mutate_guard.py` grows from 48 to 64 tests (58 at initial
implementation, +6 from the review-hardening above), split across the existing two-layer convention:

- **Pure-function layer:** `_parse_git_prefix` capture/verb-exposure across `=`/space forms; the
  `--work-tree`/`--git-dir`/`-C` mutating-classification fix; `_segment_redirect_dirs` first-line-only
  anchoring (a heredoc-body `-C` mention injects no target); the `_is_allowlisted_root` journal
  carve-out (exact-path match, plus a same-basename-wrong-path negative case); `_tokenize`'s quoted-path
  capture and its unbalanced-quote fallback; `_resolve_git_toplevel`'s null-byte fail-open; and the
  repurposed `test_dashC_redirect_captured_and_classified` (the pre-#576 test asserted `classify()`
  returned `None` for `git -C <canonical> checkout` — the exact gap — and is inverted here, justified in
  the PR body under the Test Integrity policy).
- **End-to-end `main()`-via-subprocess layer:** `git -C <canonical>` from a worktree cwd blocked (exit
  2, target root named); `git -C <engineering-journal>` from a worktree cwd allowed (carve-out, via the
  test-only env override); a same-basename repo at a different path correctly NOT exempt; `git -C
  <worktree>` allowed (target is not canonical); `git -C <other canonical>` from a canonical cwd blocked
  (the `_REDIRECT_RE`-backstop case); `git --work-tree=<canonical> commit` blocked (the misclassification
  fix); the override bypassing a redirect block; a quoted, space-bearing `-C` target blocked; and a
  relative `-C` target resolved against the command's own cwd, blocked. Test fixtures embedding a path
  in command TEXT use `Path.as_posix()` (forward slashes) — a raw Windows `str(Path)` would embed
  backslashes that `shlex.split(posix=True)` treats as escape characters outside quotes, which is exactly
  the class of bug item 1 above fixes in the hook itself and would otherwise silently corrupt the test
  fixtures the same way. The `test_no_crude_command_substring_checks.py` AST gate passes — the new
  parsing is tokenized (`_parse_git_prefix`/`_tokenize`), not a substring test.

---

## Amendment 3 (2026-07-14) — confirm worktree-cwd liveness before trusting the shape-only exemption (dev-env#749)

### The gap

`main()`'s `cwd_is_worktree = bool(_WORKTREE_RE.search(cwd))` trusted the *shape* of `cwd` alone — a
path matching `.../.claude/worktrees/<name>` — with no confirmation that the directory is an actual,
live, registered git worktree. When true, the ambient-mutation short-circuit
(`if cwd_is_worktree and not any(redirect_dirs): sys.exit(0)`) and the per-match loop's `elif
cwd_is_worktree: continue` both exited without ever asking git to resolve `cwd`.

An **orphaned** worktree directory — its `.git` link file missing or broken, e.g. left behind after an
incomplete `git worktree add`/`remove` — still textually matches the pattern. When git resolves such a
directory (`git rev-parse --show-toplevel`), it walks up the filesystem tree looking for a `.git` and
lands on the CANONICAL repo instead, since a worktree's real location (`<canonical>/.claude/worktrees/
<name>`) is nested inside the canonical's own tree. Live-confirmed on
[dev-env#630](https://github.com/brownm09/dev-env/issues/630) (a comment on that issue, not a reopen —
its own root-cause conclusion still holds): an orphaned worktree directory with no `.git` resolved `git
rev-parse --show-toplevel` straight to the canonical checkout, giving that session fully unguarded
ambient mutate access to it — the exact dev-env#453 collision this hook exists to prevent, entirely
undetected because the shape-only check exited before git was ever consulted.

`_WORKTREE_RE` has a second use site, in `_blockable_redirect_root()` (checking a `-C`/`--git-dir`/
`--work-tree` redirect target). That site is **not** buggy: it always resolves the target via
`_resolve_git_toplevel()` first, and only checks the pattern against the git-RESOLVED result — an
orphaned target is already "unmasked" to its real canonical root by the resolution step itself, before
the pattern check ever runs. No change needed there.

The sibling hook `pre-tool-use-worktree-path-check.py` (ADR-024) hit and fixed the identical bug shape
in its own cwd-facing use site five weeks earlier
([dev-env#328](https://github.com/brownm09/dev-env/issues/328), ADR-024's 2026-06-06 addendum) with a
proven `_worktree_is_live()` two-signal liveness check. This amendment ports that pattern into this
hook's own cwd-facing use site.

### The fix

Three new pieces, adapted from `pre-tool-use-worktree-path-check.py`'s own `_worktree_is_live()`/
`_normalize()` — copied rather than imported, per this codebase's convention of tolerating duplication
through two consumers before extracting a shared module (see `_worktree_topology.py`'s own docstring
and ADR-093's Maintainability section for the precedent of this convention; this is a *different*
duplicated bundle than the git-redirect-parsing helpers ADR-105 discusses reusing — do not conflate the
two):

- **`_WORKTREE_ROOT_RE`** — an anchored capture variant of `_WORKTREE_RE`, used only to extract the
  worktree-root PREFIX of a raw cwd. Kept separate from `_WORKTREE_RE` (whose other use site needs its
  unanchored `.search()` form against an already-resolved root, and is not buggy — see above).
- **`_worktree_root_from_cwd(cwd)`** — cheap, pure string extraction, no subprocess.
- **`_is_live_worktree(worktree_root, cwd, *, path_exists=..., git_toplevel=...)`** — the two-signal
  liveness check: (1) the `.git` link file must exist at `worktree_root` (catches the common orphan
  case, no subprocess); (2) git's resolved toplevel for `cwd` must equal `worktree_root` (catches the
  subtler orphan mode where the `.git` link file itself is still present but its target inside
  `<canonical>/.git/worktrees/<name>` was independently pruned away). A git-resolution failure (`None`)
  is treated as live — a transient git failure must not widen this hook's block surface. The injectable
  `path_exists=`/`git_toplevel=` keywords mirror the sibling's identical seam, for the same reason:
  deterministic pure-function test coverage without real subprocess/filesystem calls. Reuses this
  file's own existing `_resolve_git_toplevel` as the default `git_toplevel=` — it already has a more
  defensive except-tuple (`ValueError` for null-byte paths, dev-env#576/PR#584) than the sibling's copy.

Unlike the sibling hook (which BLOCKS on a non-live worktree, since any write there risks landing on
the wrong tree), a non-live result here does not itself block anything — it just disables the
shape-only exemption in `main()`. `cwd` then falls through to this hook's own normal canonical-root
resolution, exactly as an ordinary non-worktree cwd would: if the orphaned directory's git-resolved
toplevel is a real canonical root, the existing block logic correctly blocks it; if git can't resolve
it at all, the file's existing fail-open guarantee applies unchanged.

`main()` is restructured so the (now possibly subprocess-spawning) liveness check is deferred until
after `matches` is confirmed non-empty, and the override check (`_has_override`) is checked *before*
the liveness confirmation — preserving the module docstring's "no subprocess spawn unless a mutating
segment is actually found" contract now that liveness confirmation can itself spawn one. This reordering
is a pure performance win with no behavior change: `_has_override` and `cwd_is_worktree` are independent
of each other and both lead to the identical exit-0 outcome when true, so their relative check order
cannot affect the result for any input — only whether a wasted subprocess call happens before an
override short-circuits.

### Why this is an amendment, not a new ADR

Same harm model (silent shared-canonical-checkout collision, dev-env#453), same file, same hook, same
override/worktree-scope machinery, same severity (hard block, exit 2) — only a previously-unrecognized
way for `cwd` to *look* like a worktree without *being* a live one. Mirrors Amendment 1 (an
unrecognized command surface reaching the same harm through a different CLI binary) and Amendment 2 (an
unresolved redirect target) in framing: this closes a gap that let the *original* harm through, it does
not introduce a new one. A genuinely live worktree still gets zero-friction treatment (proven by the new
`test_main_allows_ambient_mutating_command_from_live_worktree` end-to-end test); every previously
fail-open cwd shape stays fail-open (proven by re-running all 9 pre-existing worktree-cwd tests
unchanged — see Coverage below).

### Sync-location update

The four re-spelled locations this hook's module docstring names (see the `NOTE:` comment inside
`is_mutating_segment()`) describe the *mutating-verb list*, which is unchanged by this amendment — no
update needed there. This amendment instead touches the docstring's own worktree-detection description
in two places, plus the standard external sync locations:

1. The module docstring in `pre-tool-use-canonical-mutate-guard.py` — logic step 2's prose and the
   "Coverage note" paragraph both now describe the liveness confirmation and cite dev-env#749.
2. `claude/CLAUDE.md`'s "Never mutate git state directly…" bullet — its existing wording already implied
   a *real* worktree; left as-is (precision touch only, not a contract change, so not worth the diff
   noise).
3. This ADR's own Decision/Judgment-calls text and Amendments 1–2 — left **unedited** for history; this
   Amendment 3 section is the addition, per this repo's established amended-ADR convention.
4. `docs/REFERENCE.md` — the Hooks table row for `pre-tool-use-canonical-mutate-guard.py`, and the
   separate "Prevention" paragraph in the Git Workflow Runbook section (which previously framed the
   `cd`-into-canonical case as the hook's "sole remaining documented v1 gap" — reworded, since this
   amendment closes a different-axis gap, not that one, and the `cd`-into-canonical gap remains
   separately true).
5. `docs/adr/INDEX.md` row 071 — `Date` cell appended with this amendment's date; tag `orphaned-worktree`
   added (the same tag ADR-024's own INDEX row already uses for its structurally identical addendum).

### Review hardening (dev-env#749/PR#757)

`/review` on this PR (two Opus-model subagents, correctness/security and reliability/performance/
maintainability, run independently) found the initial implementation above was incomplete in two ways,
both fixed in the same PR before merge:

1. **Wasted liveness-check subprocess on the all-redirect path.** `cwd_is_worktree` was computed
   unconditionally whenever `cwd_worktree_root` was non-None, but its value is only ever consulted in
   the per-match loop's `elif cwd_is_worktree` branch — unreachable for any match carrying
   `redirect_dirs`. A command whose every mutating segment is a redirect (the common
   `git -C <journal-canonical> pull` shape the Stub file workflow runs on nearly every PR open/merge)
   spawned a wasted `git rev-parse` for a `cwd_is_worktree` value the loop never reads. Fixed by gating
   the computation on `any(not m["redirect_dirs"] for m in matches)` — confirmed behavior-preserving:
   when every match is a redirect, `cwd_is_worktree` was never consulted before this fix either, so
   forcing it to `False` (via the short-circuited `any(...)`) changes no outcome, only whether the
   subprocess runs.
2. **The ambient branch's canonical-root invariant was weakened by this amendment.** Before this
   amendment, `cwd_is_worktree == False` could only mean "cwd never looked worktree-shaped at all," so
   the ambient branch's `block_root = cwd_root` was safe under the stated invariant "any resolved
   toplevel IS canonical by construction." This amendment breaks that guarantee in one respect: `cwd`
   can now be worktree-*shaped* yet still reach the ambient branch, if `_is_live_worktree()` returns
   `False` for a cwd that is, in fact, a genuinely live worktree (e.g. a resolved-toplevel vs.
   extracted-prefix mismatch from a junction, symlink, or short-path component — not reproduced in this
   codebase's own layout, but not provably impossible on every filesystem). Left unguarded, that gap
   would let the ambient branch block a legitimate in-worktree mutation against the worktree's own
   resolved root — a false-positive over-block that never existed pre-amendment, and a contradiction of
   this amendment's own "every previously fail-open cwd shape stays fail-open" framing above. Fixed by
   `_blockable_ambient_root()`, a direct mirror of `_blockable_redirect_root()`'s existing
   `not _WORKTREE_RE.search(root)` guard: a resolved `cwd_root` that is itself worktree-shaped is never
   treated as blockable. A no-op for the actual bug this amendment fixes — an orphan's resolved root is
   the canonical repo, never worktree-shaped — so the fix for dev-env#749 itself is unaffected; this
   only closes a latent, not-currently-reachable gap the fix's own restructuring introduced.

A third finding (the `_is_live_worktree` docstring overclaiming that signal 2 catches a pruned-gitdir
orphan mode, when in that state git actually errors and `git_toplevel` returns `None`, which signal 2
treats as live — harmless in practice, since the same `None` also leaves the ambient branch nothing
blockable, so every path still converges on fail-open) was fixed by correcting the docstring rather than
the logic, since the logic's fail-open behavior was already correct.

Two further findings were surfaced and deliberately **not** acted on in this PR: (a) a duplicated
worktree-path regex fragment between `_WORKTREE_RE` and `_WORKTREE_ROOT_RE`, fixed inline via a shared
`_WORKTREE_PATH_FRAGMENT` constant (a same-file, zero-risk factor, unlike the deliberate cross-file
duplication convention discussed above); (b) `_resolve_git_toplevel(cwd)` running twice for the same
cwd in the narrow "worktree-shaped, `.git` present, but not live" sub-case (once inside
`_is_live_worktree`'s signal 2, once again in the ambient branch) — filed as
[dev-env#758](https://github.com/brownm09/dev-env/issues/758) rather than fixed inline, since a clean
fix requires either threading the resolved toplevel out of `_is_live_worktree`'s bool-only return (a
signature change that would diverge it from the sibling hook's identical copy) or seeding
`toplevel_cache` with a cwd entry (a small scope widening of a cache that today only memoizes redirect
targets) — both judged to warrant their own focused review rather than folding into an already-large
review-response commit.

### Coverage

`claude/scripts/tests/test_canonical_mutate_guard.py` grows from 64 to 70 tests (+6: the 5 described
below, plus one review-hardening addition), split across the existing two-layer convention:

- **Pure-function layer:** `test_worktree_root_from_cwd_matches_and_extracts` (bare worktree root, a
  nested-subdirectory cwd, a mixed-separator/uppercase variant, and two non-worktree `None` cases);
  `test_is_live_worktree_decision_table` (mirrors `test_worktree_path_check.py`'s identical table:
  live; `.git` missing; git resolves to canonical; git resolves to an unrelated path; git exec fails →
  treat as live; toplevel differs only by case/separator → still live); `test_is_live_worktree_short_
  circuits_before_git` (a spy on `git_toplevel` proves zero calls when the `.git` link is already
  missing); `test_blockable_ambient_root_guards_against_worktree_shaped_resolution` (review hardening
  #2 above — `None` stays `None`, a canonical root passes through unchanged, a worktree-shaped resolved
  root is never treated as blockable).
- **End-to-end `main()`-via-subprocess layer:** `test_main_allows_ambient_mutating_command_from_live_
  worktree` — a REAL, `git worktree add`-registered worktree (not just a worktree-shaped directory; no
  existing test in this file exercised a genuine live worktree before this amendment) still gets
  zero-friction treatment; `test_main_blocks_orphaned_worktree_shaped_cwd_nested_in_canonical` —
  reproduces the dev-env#630 signature exactly (an orphaned worktree-shaped directory nested inside a
  real canonical repo, no `.git` of its own, real git naturally walks up to the canonical's `.git`, no
  mocking needed) and confirms the mutating command is now blocked with the canonical root named in the
  reason. Both new e2e tests were confirmed to actually exercise the fix (the orphan test fails red
  against the pre-amendment code; the live-worktree test passes both before and after, correctly proving
  a no-regression property rather than a bug-reproduction property, since the legitimate live-worktree
  path was never broken).

All 9 pre-existing worktree-cwd tests were traced and re-verified to pass unchanged, via three distinct
mechanisms rather than by accident: 2 tests use a bare non-git directory, which is now correctly found
"not live" and falls through to the existing fail-open path (same exit code, different internal route);
6 tests carry a `-C`/`--git-dir`/`--work-tree` redirect, whose control flow never depended on
`cwd_is_worktree`'s value in the first place; 1 test relies on the override token, which now
short-circuits before the liveness check even runs. The full suite (`py -3
claude/scripts/run-hook-tests.py`) passes unchanged elsewhere.

---

## Amendment 4 (2026-07-14) — extend coverage to the PowerShell tool (dev-env#620)

### The gap

This hook's own `main()` — like 10 of its 11 PreToolUse sibling hooks — hard-gated on
`data.get("tool_name") != "Bash"`, and `claude/settings.json` wired all 12 PreToolUse safety hooks
(this one included) under a `"matcher": "Bash"` array only, with **no equivalent registration for
the `"PowerShell"` tool anywhere in the file** (dev-env#620, filed after the #617 detached-HEAD
investigation surfaced it as a freestanding structural gap). PowerShell is not a misuse case in this
environment — it is a fully sanctioned, parallel way to run git/gh commands (the PowerShell tool's
own description: *"for terminal operations via PowerShell: git, npm, docker, and PS cmdlets"*; the
global CLAUDE.md environment section lists it as the primary shell). Net effect: `git checkout main`
(or any other mutating command this hook blocks for Bash) run via the PowerShell tool against a
canonical checkout **completely bypassed this guard, silently** — not a corner case, but the
identical dev-env#453 collision this ADR exists to prevent, reached through a different tool
invocation rather than a different CLI binary (Amendment 1's shape) or an unresolved redirect target
(Amendment 2's shape).

Verification (per #620's own recommendation) went one level deeper than "is the matcher wired":
mirroring the settings.json block alone would have been a **silent no-op**, since this hook's `!=
"Bash"` check would still fail-open on a real `tool_name: "PowerShell"` payload. Two further,
narrower gaps surfaced from reading `_hookio.py`'s shared `split_top_level`/`_opaque_spans` engine
(which this hook's `find_mutating_segments()` depends on) against real PowerShell syntax rather than
assuming a bash-oriented parser generalizes for free:

1. **Here-strings.** A PowerShell here-string (`@'...'@` literal, `@"..."@` expandable) is the
   functional equivalent of a bash heredoc but uses a structurally unrelated opener/closer. Without
   recognizing it, the engine's existing POSIX single/double-quote tracking still fired on the bare
   `'`/`"` inside the `@'`/`@"` opener, closing prematurely at the first embedded quote character in
   the body — traced by hand (and pinned as a regression test) to a case where this **dropped a real
   trailing command entirely** as an "unterminated quote" tail, exactly the silent-bypass direction
   that matters here.
2. **The brace-conditional idiom.** PowerShell 5.1 has no `&&`/`||` (confirmed parser errors in this
   environment — the PowerShell tool's own description says so), so its documented "run B only if A
   succeeds" idiom is `A; if ($?) { B }`. `split_top_level` had no concept of an unquoted `{` as a
   statement boundary, and every caller in this hook family (this one included) matches via
   `.match()` anchored at a segment's *start* — so `B` nested inside `{ }` was invisible to
   `is_mutating_segment()`/`is_mutating_gh_segment()` even though nothing else about the command was
   unusual. The identical bash brace-group idiom (`{ cmd; }`) had the same gap.

### The fix

- **`claude/settings.json`** — a new `"matcher": "PowerShell"` array under `PreToolUse`, mirroring
  the `"Bash"` array's 12 scripts (this hook included) and their timeouts exactly.
- **This hook's `main()`** — `data.get("tool_name") not in _SANCTIONED_TOOL_NAMES` where
  `_SANCTIONED_TOOL_NAMES = ("Bash", "PowerShell")`, replacing the `!= "Bash"` check. Applied
  identically (each with its own local check, not a shared cross-file constant — kept per-file since
  three of the ten sibling files don't otherwise import anything that would justify a new shared
  dependency) to the other 10 tool_name-gated PreToolUse hooks: `pre-commit-branch-check.py`,
  `pre-pr-create-check.py`, `pre-merge-message-check.py`, `pre-merge-branch-check.py`,
  `pre-merge-findings-gate.py`, `pre-auto-merge-checkpoint-gate.py`, `pre-merge-numbering-check.py`,
  `pre-tool-use-journal-draft-worktree-guard.py`, `pre-tool-use-journal-compose-force-guard.py`, and
  `pre-bash-drift-check.py`. `disk-space-check.py` needed no change — it already ignores `tool_name`
  entirely.
- **`_hookio.py`** — a new `_find_herestring_end()` (mirrors `_find_heredoc_end()`) recognizes
  `@'...'@`/`@"..."@` as an opaque span in both `split_top_level`'s and `_opaque_spans`'s `top` and
  `subshell` states; an unquoted `{` is added as an unconditional split trigger in `split_top_level`'s
  `top` state only (never inside a subshell, matching how every other separator is already scoped
  there) — closing the brace-conditional gap for every hook that routes through `scan_top_level`, not
  just this one. Deliberately **not** special-cased for a PowerShell `@{...}` hashtable-literal/splat
  token (over-segmentation is the accepted benign direction here, per this same file's existing
  `mask_quoted_spans` docstring reasoning) — see dev-env#761 for what this change does not attempt.
- **Two hooks with their own local, non-`scan_top_level` regex** (`pre-commit-branch-check.py`'s
  `_GIT_COMMIT_RE`, `pre-pr-create-check.py`'s `_GH_PR_CREATE_RE`) needed a parallel, independent fix:
  `{` added to each one's own anchor alternation, since neither routes through the shared engine.
- **New regression test**: `test_settings_hook_wiring.py`'s
  `test_pretooluse_bash_and_powershell_matchers_are_mirrored` asserts the two matcher arrays wire the
  identical script set — nothing before this amendment would have caught a future hook added to one
  matcher and forgotten on the other.
- **Documented, not fixed, in this amendment**: `_CD_RE`'s literal-`cd`-only anchoring (here and in
  `pre-tool-use-journal-draft-worktree-guard.py`) does not recognize PowerShell's `Set-Location`/`sl`
  — see the updated "Coverage note" above, which now states this explicitly extends (does not newly
  create) the already-accepted bare-`cd`-into-canonical-root v1 gap. `Set-Location`/`sl` recognition,
  the `shlex.split(posix=True)` vs. PowerShell-quoting mismatch in three files' `_tokenize()`, a
  PowerShell-native override-token syntax, and the equally Bash-only **PostToolUse** hook family are
  tracked in [dev-env#761](https://github.com/brownm09/dev-env/issues/761) rather than folded into
  this already-large amendment.

### Why this is an amendment, not a new ADR

Same harm model this ADR exists to prevent (silent shared-canonical-checkout collision, dev-env#453),
same file, same hook, same override/worktree-scope machinery, same severity (hard block, exit 2) —
only a previously-unrecognized *tool* surface (PowerShell, not Bash) reaching that already-decided
harm model, exactly mirroring Amendment 1's framing for a previously-unrecognized *command* surface
(`gh`, not `git`) and Amendment 3's framing for a previously-unrecognized *cwd-shape* surface (an
orphaned worktree). This amendment is filed against ADR-071 specifically because it is this hook's
own dedicated ADR and this hook is one of the twelve directly affected; the identical `tool_name` fix
and settings.json mirror applied to the other ten sibling PreToolUse hooks, and the shared
`_hookio.py` parser extension, are cross-referenced here rather than each carrying their own ADR,
since none of them individually meets the ADR-warrant bar (a settings.json wiring change plus a
one-line conditional widening, repeated) — this hook's is the one file in the affected set that
already has a dedicated ADR whose own stated scope this change directly widens.

### Sync-location update

1. The module docstring in `pre-tool-use-canonical-mutate-guard.py` — logic step 1 and the
   "Explicitly NOT blocked" / "Still deferred (v1)" paragraphs now describe PowerShell coverage and
   the `Set-Location` extension of the existing `cd` gap.
2. `claude/CLAUDE.md`'s "Never mutate git state directly…" bullet — updated in the same PR to
   describe the hook as `PreToolUse(Bash/PowerShell)` rather than `PreToolUse(Bash)`.
3. This ADR's own Decision-section text and Amendments 1–3 — left **unedited** for history; this
   Amendment 4 section is the addition, per this repo's established amended-ADR convention.
4. `docs/REFERENCE.md`'s ADR-071 Hooks-table pointer — updated in the same PR if it made the same
   Bash-exclusive claim.
5. `docs/adr/INDEX.md` row 071 — `Date` cell appended with this amendment's date; `powershell` tag
   added. (The header `Date`/`Tags` lines at the top of this file were also brought in sync with
   INDEX.md's already-current state as of this same edit — they had drifted to show only the
   2026-07-04 amendment and were missing the `redirect-target`/`orphaned-worktree` tags Amendments 2
   and 3 had already added to INDEX.md but never back-ported into this file's own header.)

### Coverage

- **`test_hookio.py`** gains 11 new tests for the shared parser extension: the PowerShell
  conditional-brace idiom and the bash brace-group equivalent are now detected via `scan_top_level`;
  an unquoted `{` inside a quoted string is confirmed NOT a split trigger; `{`-splitting still applies
  with `split_pipe=True` (this hook's own mode); both here-string forms (literal/expandable) with an
  embedded stray quote no longer hide a real trailing command (the load-bearing regression pins,
  traced by hand against the pre-fix parser to confirm each would have failed); a here-string with no
  closer runs to end-of-string like a heredoc; `mask_quoted_spans` masks both here-string forms; and
  the existing `mask_quoted_spans`/`split_top_level` cross-consistency fixture list gains a
  here-string case. All pre-existing tests in this file pass unchanged (91 -> 102).
- **`test_canonical_mutate_guard.py`** gains one new end-to-end test: a mutating command with
  `tool_name: "PowerShell"` is blocked (exit 2) identically to `tool_name: "Bash"`, while the existing
  `test_main_noop_on_non_bash_tool` (a genuinely unrelated tool, `"Write"`) continues to no-op
  unchanged (70 -> 71).
- **`test_journal_draft_worktree_guard.py`**, **`test_pre_merge_numbering_check.py`** (the latter via
  a refactored shared `_run_collision_test(tool_name)` helper so the expensive real-git-repo collision
  scenario isn't duplicated verbatim), **`test_pre_tool_use_journal_compose_force_guard.py`**, and the
  two shell-driven suites **`test-auto-merge-checkpoint-gate.sh`** / **`test-merge-findings-gate.sh`**
  each gain an equivalent end-to-end `tool_name=PowerShell` case proving their own hook's real blocking
  path (not just a no-op) fires identically for both tool names.
- **`test_pre_commit_branch_check.py`** and **`test_pre_pr_create_check.py`** each gain two pure-function
  tests confirming their own local `{`-anchor regex now detects both the PowerShell conditional-brace
  idiom and the bash brace-group equivalent.
- **`test_settings_hook_wiring.py`** gains `test_pretooluse_bash_and_powershell_matchers_are_mirrored`
  (see The fix, above).
- Three PreToolUse hooks (`pre-merge-branch-check.py`, `pre-merge-message-check.py`,
  `pre-bash-drift-check.py`) received the identical `tool_name` fix but no new test: each has an
  established, deliberate "pure-helper convention, `main()`'s stdin plumbing not covered" scope
  documented in its own test file's docstring, predating this amendment — adding `main()`-level
  coverage newly, only for this one fix, would be inconsistent with that standing per-file convention
  rather than an extension of it. All three are pure advisories (exit 0 always), never a blocking
  gate, so the risk this leaves untested is low.
- Full suite (`py -3 claude/scripts/run-hook-tests.py`): 67 files passed, 2 skipped (both pre-existing,
  documented runner/environment skips unrelated to this change), 0 failed.
