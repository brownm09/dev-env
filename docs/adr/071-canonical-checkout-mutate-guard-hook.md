# ADR-071: PreToolUse Hook to Block Git-Mutating Bash Commands in a Canonical (Non-Worktree) Checkout

**Date:** 2026-07-01
**Status:** Accepted
**Tags:** hooks, worktrees, pre-tool-use, bash, git, concurrency, canonical-checkout, rate-limit

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
