# ADR-105: Draft-Branch Worktree-Squat Guard and Pattern-Scoped Squatter Detection

**Date:** 2026-07-12
**Status:** Accepted
**Tags:** journal, worktrees, canonical, draft-branch, hooks, PreToolUse, prune, park, safety, ADR-058, ADR-071, ADR-093

---

## Context

The engineering-journal Stub file workflow (`claude/CLAUDE.md` -> Engineering Journal -> Stub file
workflow) deliberately keeps `draft/YYYY-MM-DD` checked out **only** in the canonical checkout
(`C:/Users/brown/Git/engineering-journal`), reached via `git -C <journal> <command>` from any
session, never a dedicated worktree. Git only allows one worktree to hold a branch at a time, so
this is what lets every concurrent session — dozens per day, across every tracked project — reach
the shared branch safely.

That exemption was being silently violated. `spawn_task`-spawned tile sessions whose own primary
repo is a different project (most often finishing real work in lifting-logbook or career-playbook,
then writing an engineering-journal stub as a side effect) have been creating their own throwaway
worktree for engineering-journal — named `stub-<issue>-<HHMMSS>`, a convention defined nowhere in
dev-env's own scripts, improvised by the agent, most likely from the general "isolate cross-repo
work in a worktree" instinct the rest of `claude/CLAUDE.md` actively teaches (`EnterWorktree`
guidance, the `git worktree add <path> -b <branch> origin/<default-branch>` cross-repo pattern) —
and checking out `draft/YYYY-MM-DD` **inside it** instead. That locks the branch to the throwaway
worktree and blocks the canonical, and every other concurrent session, from reaching it until the
worktree is parked or removed.

Confirmed live and reproduced twice on 2026-07-12, not merely inferred: `.claude/worktrees/
stub-823-120134` held `draft/2026-07-11` (already moot — that day's journal had fully composed into
`main` via merged PR #170 — but still holding the ref) and `.claude/worktrees/stub-829-165612` held
`draft/2026-07-12` (that day's, actively blocking every other session including the canonical) —
simultaneously. `stub-829-165612` was clean and 0 commits ahead of `origin/draft/2026-07-12`: every
commit it ever made was already safely on the remote, i.e. it carried zero unique data despite
having locked the branch for the whole day. `stub-823-120134` additionally carried 4 uncommitted,
unpushed deletions of unclear intent. Neither worktree had a transcript directory anywhere under
`~/.claude/projects/` — confirming neither was ever a session's own working directory; both were
populated via `-C`-redirected (or equivalent) git commands issued from elsewhere, which also means
ADR-051's `worktree_session_is_live()` transcript-mtime liveness signal structurally cannot apply
to this failure mode (see Judgment calls, below).

### Why nothing already caught this

- `pre-tool-use-canonical-mutate-guard.py`'s (ADR-071) mutating-verb classifier has no `worktree`
  case at all — `git worktree add` is entirely outside its scope, and its whole invariant runs in
  the opposite direction anyway (it blocks a command that mutates an *existing* canonical checkout;
  this failure mode is a command that *creates* a new, non-canonical one).
- `journal-canonical-guard.py` (ADR-093) detects the canonical hijacked *onto* a stray branch — the
  inverse direction from a worktree squatting a branch the canonical itself needs.
- No hook was wired to `EnterWorktree`/`ExitWorktree` for any repo.
- `_worktree_topology.py`'s squatter-detection helpers (`main_squatter`, `canonical_sync_action`)
  are hardcoded to the literal branch `"main"` — no generalized "some branch pattern must never be
  held by a non-canonical worktree" check existed.

### The dev-env#346 mis-citation this fix also corrects

`claude/CLAUDE.md`'s mutate-guard exemption bullet, `pre-tool-use-canonical-mutate-guard.py`'s own
`_REDIRECT_TARGET_ALLOWLIST` comment, and ADR-071 Amendment 2 all cited
[dev-env#346](https://github.com/brownm09/dev-env/issues/346) as if it tracked a general
journal-workflow-to-worktree migration that would eventually let the canonical-direct exemption be
removed. Reading the actual filed issue shows its scope is narrow: only the `biweekly-retro`
routine's own report-writing step (`git checkout -b retro/${RUN_DATE}` in the shared canonical), a
real but different problem. The canonical-direct exemption for the daily Stub file workflow is not
"pending" anything — it is permanent, load-bearing infrastructure. This ADR's fix corrects those
citations rather than leaving the overstatement to confuse a future reader into thinking closing
#346 would somehow complete a migration it was never scoped to do.

### Also live during this investigation, tracked separately

The canonical was independently found hijacked onto an unrelated branch by the already-tracked,
closed [dev-env#630](https://github.com/brownm09/dev-env/issues/630) mechanism — with fresh evidence
that it also hits regular interactive session provisioning, not just scheduled tasks as #630's
original title states, plus a live-confirmed compounding gap (`pre-tool-use-canonical-mutate-guard.py`'s
`_WORKTREE_RE` cwd check is a path-*shape* regex, not an actual `git worktree list` membership check,
so an orphaned worktree directory is treated as legitimate and gets fully unguarded mutate access to
the canonical). Recorded as a comment on #630 (not reopened — its own root-cause conclusion still
holds) and **not** part of this ADR's fix — different mechanism, tracked separately.

---

## Decision

Four changes, all in this PR:

### 1. New blocking `PreToolUse(Bash)` hook

`claude/scripts/pre-tool-use-journal-draft-worktree-guard.py`, wired in `claude/settings.json`
immediately after `pre-tool-use-canonical-mutate-guard.py` (thematic grouping — both are
git-canonical-safety guards for the same repo class). Blocks two shapes:

1. `git worktree add <path> ... <branch>` where `<branch>` matches `DRAFT_BRANCH_RE`
   (`^draft/\d{4}-\d{2}-\d{2}(-recovery)?$`), from **any** cwd, unconditionally — no legitimate
   target exists for this shape at all, so no git resolution is even needed. If `-b`/`-B` names a
   new branch, only *that* branch's name is checked (a `git worktree add -b myfix <path>
   draft/2026-07-12` bases a new, differently-named branch off a draft branch as a mere starting
   point and does not lock `draft/2026-07-12` itself — must not be blocked).
2. `git checkout|switch <branch>` (ambient or redirected via `-C`/`--git-dir`/`--work-tree`) where
   `<branch>` matches `DRAFT_BRANCH_RE` — blocked unless the resolved git toplevel **is** the
   engineering-journal canonical exactly (the one legitimate case: the documented workflow itself).
   `git checkout <branch> -- <paths>` (a file restore, not a branch switch) is explicitly exempted.

Exit 2 (blocking gate, matching `pre-tool-use-canonical-mutate-guard.py`'s convention for a failure
mode of comparable disruption), with an `ALLOW_JOURNAL_DRAFT_WORKTREE=1` override token for
consistency with that hook's `ALLOW_CANONICAL_MUTATE=1` — no currently-known legitimate case needs
it, but this is not one of the two specially-designated no-override fail-closed gates
(`pre-auto-merge-checkpoint-gate.py`, `pre-tool-use-journal-compose-force-guard.py`), whose
no-override design rests on much stronger, specific reasons that don't apply here.

### 2. Generalized `_worktree_topology.py`

A third invariant, alongside the two the module already hosts (dev-env's "canonical always `main`"
and the repo-agnostic "canonical never detached or on a stray `claude/*` branch" from ADR-093): some
branches must never be held by any non-canonical worktree at all, independent of what the canonical
itself currently holds. Two additions:

- `DRAFT_BRANCH_RE` (the same pattern the new hook uses — see Judgment calls for why it is a
  documented duplicate, not a shared import) and `non_canonical_worktrees_matching(worktrees,
  pattern)`, returning **every** non-canonical match, not just the first — more than one stale
  squatter can coexist (confirmed live: yesterday's and today's draft branches, each locked to a
  different throwaway worktree, on the same day).
- `PatternSquatAction`/`pattern_squat_action(path, branch, *, live, dirty, fully_pushed)`, deciding
  `"warn-live"` (never touch a live session — ADR-051) / `"park-and-remove"` (idle, clean, fully
  pushed relative to the branch's own `origin/<branch>` — safe to park *and* remove in one pass) /
  `"park-only"` (dirty, or not provably fully pushed — free the branch name only, leave the
  worktree's contents completely untouched for human review).

### 3. Wired into `prune-merged-worktrees.py`

A new per-worktree branch in `prune_one()`'s loop, positioned after the existing main-squatter park
block and before the `BRANCH_PREFIX` gate (a `draft/*` branch never starts with `claude/`, so the
prefix gate would otherwise skip it). The ADR-051 liveness guard already runs earlier in the same
loop for every worktree, so `live=False` is safe to assume by the time this branch is reached. A new
`_origin_ahead_count(branch, repo)` helper measures "fully pushed" against the squatted branch's own
`origin/<branch>` tip, not `origin/main` — a composed draft branch reaches `main` via a fresh squash
commit (ADR-082), never a fast-forward or a matching PR head, so the existing `is_merged()` check
would never fire for this branch shape at all.

### 4. Citation corrections

`claude/CLAUDE.md`'s Stub file workflow section gains an explicit anti-pattern warning naming this
exact failure mode. Its mutate-guard exemption bullet, `pre-tool-use-canonical-mutate-guard.py`'s
`_REDIRECT_TARGET_ALLOWLIST` comment, and ADR-093's References section are corrected to state the
carve-out is permanent by design and stop citing dev-env#346 as if closing it would remove it (see
Context, above).

---

## Judgment calls

**New dedicated hook, not an extension of `pre-tool-use-canonical-mutate-guard.py`.** The two fire
on opposite cwd conditions (that hook blocks when the target *is* canonical; this one blocks when
the target is *not*), and `git worktree add` is a verb that file's classifier has never modeled —
folding both invariants into one function computing two opposite verdicts from the same resolved
root was judged more confusing than two focused files, matching ADR-071's own precedent for not
folding its guard into ADR-024's differently-shaped hook.

**Git-command-parsing helpers are a deliberate, documented duplicate, not a shared `_hookio.py`
import.** `pre-tool-use-canonical-mutate-guard.py`'s redirect/tokenization logic
(`_parse_git_prefix`, `_tokenize`, `_normalize_redirect_dir`, `_resolve_git_toplevel`, `_first_line`)
has a real review-found-bug history — ADR-071 Amendment 2 alone fixed five distinct bugs in it
(quoted paths with spaces, relative-redirect resolution against the wrong cwd, null-byte crashes,
basename-vs-exact-path carve-out matching, missing memoization). This PR copies the
already-fixed, tested implementation verbatim (including the relative-redirect-resolves-against-
command-cwd fix, verified directly against a real target during manual testing) into the new hook
file, rather than risk a fresh reimplementation reintroducing one of those bugs. Matches this
codebase's own stated convention of tolerating duplication through two consumers before extracting
(`_worktree_topology.py`'s own module docstring; ADR-093's Maintainability section cites the
identical precedent for a different pair of files) — this is the second consumer. Extract into
`_hookio.py` if/when a third caller needs the identical parsing.

**ADR-051's transcript-mtime liveness guard does not apply to this failure mode, and the new
`prune-merged-worktrees.py` wiring does not try to use it.** `worktree_session_is_live()` keys off a
worktree *path* having its own Claude Code session transcript directory. Live verification during
this investigation found neither `stub-823-120134` nor `stub-829-165612` had one anywhere under
`~/.claude/projects/`, despite 230 other project directories existing there with the encoding scheme
confirmed correct against the engineering-journal canonical's own entry — because these worktrees
were never a session's own cwd; they were populated via `-C`-redirected git commands issued from a
different session's actual working directory. This is the same shape of gap ADR-086 already found
and solved differently for a related problem (whether an in-progress session might still write to a
draft branch before automated compose runs) — ADR-086 rejected generalizing
`worktree_session_is_live()` there too, for the same reason: no single worktree path corresponds to
"the session that might still act on this." This ADR's answer is the git-observable-state approach
`pattern_squat_action` already takes (clean/dirty, fully-pushed/not) rather than a liveness check
that would structurally never fire.

**`park-and-remove` eligibility is safe regardless of the ADR-051 liveness question, because parking
itself is non-destructive.** `park_branch_for`'s mechanism (`git checkout -b claude/<slug>` at the
worktree's current HEAD) touches zero working-tree files and deletes no commits — pushed or not. The
actual risk `pattern_squat_action` guards against is not data loss from parking (which never causes
any) but discoverability: an idle-but-dirty or not-fully-pushed worktree's content becomes harder to
find once renamed onto a `claude/<slug>` branch nobody is looking for. `"park-only"` addresses that
by leaving the worktree in place, unremoved, exactly as `stub-823-120134`'s real 4 uncommitted
deletions were left during this incident's live recovery, for a human to review on their own
schedule.

---

## Consequences

- Confirmed live twice on 2026-07-12; the new blocking hook was manually exercised against 9
  scenarios (worktree-add onto a draft branch, `-C` redirect at the real canonical, ambient checkout
  from a non-canonical worktree, `checkout main`, the override token, the `-b <newbranch>` startpoint
  false-positive-avoidance case, a file-restore `--` case, ambient checkout from the canonical
  itself, and a relative `-C` redirect resolved against the command's own cwd against a real,
  existing, non-canonical target) before the formal test suite was written, then re-verified via that
  suite.
- **Testing.** `claude/scripts/tests/test_journal_draft_worktree_guard.py` (new, 25 cases: pure
  extraction-function coverage + subprocess end-to-end coverage against real throwaway git repos,
  mirroring `test_canonical_mutate_guard.py`'s two-layer convention). `test_worktree_topology.py`
  extended (dev-env `## Testing` item 22) with `DRAFT_BRANCH_RE`,
  `non_canonical_worktrees_matching`, and all three `pattern_squat_action` outcomes.
  `test_prune_merged_worktrees.py` extended (item 26) with four draft-branch-squat integration
  cases via the file's existing `subprocess.run` mocking convention. All three suites pass.
  **`/review` on this PR found five real gaps this initial pass missed, all fixed before merge**
  (each independently confirmed via a sabotage-then-reconfirm check — break the logic, watch the
  relevant test actually fail, restore, watch it pass again — not just re-read): (1) the one test
  protecting the hook's single most important invariant (distinguishing the legitimate `-C
  <journal-canonical>` case from every squat) was tautological — it built the `-C` path via
  `str(Path(...))`, which renders Windows backslashes that `shlex.split(posix=True)` silently
  strips, so the test passed whether or not `_is_journal_canonical` was even correct; fixed by
  rendering the path with forward slashes, matching the documented production convention. (2) a
  leading `cd` fully exempted `git worktree add ... draft/YYYY-MM-DD` from the block — the
  `cd`-scope-loss guard was copied onto `find_worktree_add_blocks` even though that function needs
  no cwd resolution at all (it blocks purely on the branch-name token), so `cd <repo> && git
  worktree add ... draft/YYYY-MM-DD` silently reproduced the exact incident this PR fixes; fixed by
  removing that guard from `find_worktree_add_blocks` specifically (it correctly stays on
  `find_checkout_candidates`, which does need a resolvable cwd). (3) the park-and-remove test
  asserted only `(pruned, skipped)` counts, which are identical to park-only's — sabotaging
  `pattern_squat_action` to always return park-only still passed; fixed by recording every
  dispatched subprocess call and asserting `git worktree remove` was (or wasn't) actually invoked,
  plus two new tests for that removal failing or timing out after a successful park. (4) a trailing
  `git checkout <branch> --` with no pathspec after it still switches branches (confirmed against
  real git) but was wrongly exempted as a file restore; fixed to require a real pathspec after
  `--`. (5) `git worktree add --detach <draft-branch>` was a false-positive over-block (a detached
  checkout holds no branch ref, so it can never be a squat); fixed by exempting `--detach`
  unconditionally. `prune-merged-worktrees.py`'s new draft-squat branch was also refactored to
  actually call `non_canonical_worktrees_matching` (precomputed once before the loop, mirroring
  `main_squatter`) instead of leaving it as an unused import with the check re-implemented inline —
  and the new hook's `_resolve_checkout_target` gained the `toplevel_cache` memoization the sibling
  hook's equivalent function has (dev-env#576/PR#584), which the initial pass had silently dropped
  when copying the parsing helpers. Full methodology and findings: `/review`'s posted comment on
  [PR #748](https://github.com/brownm09/dev-env/pull/748).
- **Observability.** The new hook's block reason follows the established stderr-only,
  exit-2-discards-stdout convention (`pre-tool-use-canonical-mutate-guard.py`'s own docstring
  explains why: Claude Code discards a PreToolUse hook's stdout on exit 2). `prune-merged-worktrees.py`'s
  new branch prints the same `[dry-run] would ...` / action-taken shape the existing main-squatter
  park block already uses.
- **Security.** N/A — no auth/secrets/PII surface; only local git state on the developer's own
  machine, matching every sibling hook in this family.
- **Resilience / failure modes.** Fails open throughout: unresolvable git paths, malformed JSON,
  missing cwd, non-Bash `tool_name`, and a `cd` anywhere in the command (which takes the rest of the
  invocation out of scope, mirroring the sibling hook's identical contract) all degrade to "allow,"
  never a crash. `prune-merged-worktrees.py`'s `park-and-remove` path degrades to `park-only`
  disposition (worktree left in place, flagged skipped with a reason) if the `worktree remove` step
  itself fails or times out after a successful park — the branch is still freed either way.
- **Performance.** The new hook adds one more `PreToolUse(Bash)` process spawn machine-wide per Bash
  call, mirroring the existing cost of `pre-tool-use-canonical-mutate-guard.py`; the git-subprocess
  resolution for a `checkout`/`switch` candidate only runs when a draft-branch-shaped token is
  actually present in the command (the common case pays only the pure string-parsing cost).
  `prune-merged-worktrees.py`'s new branch adds one `fetch` + one `rev-list --count` only for a
  worktree whose branch already matched `DRAFT_BRANCH_RE` — negligible given how rarely that pattern
  appears outside engineering-journal.
- **Data integrity.** N/A schema-wise; every correction in this PR (the new hook's block, the park
  mechanism) is non-destructive by construction — see Judgment calls above.
- **Maintainability.** `_worktree_topology.py` now hosts three invariants instead of two; its module
  docstring is updated accordingly, matching the precedent ADR-093 set when it added the second one.
  The git-command-parsing duplication between the two `PreToolUse` hooks is a known, documented,
  deliberately-accepted trade-off (see Judgment calls) with an explicit extraction trigger (a third
  consumer), not an oversight.

---

## Alternatives rejected

- **Fold the new detection into `journal-canonical-guard.py`.** Rejected — that hook's cheap-path
  design deliberately avoids reading `git worktree list` at all when the canonical's own branch is
  already healthy (its docstring: "Cheap first read... so the common healthy path stays cheap").
  Detecting a draft-branch squatter fundamentally requires reading the worktree list unconditionally,
  regardless of the canonical's own branch state — folding it in would lose that cheap-path property
  for every prompt, every session, even when nothing is wrong.
- **Extend `pre-tool-use-canonical-mutate-guard.py` to also cover `git worktree add`.** Considered,
  since it already has the redirect-resolution machinery this fix needs. Rejected — see Judgment
  calls: the two invariants are inverse-direction, and the existing hook's five-bug history argues
  for not risking a refactor of working, tested code when a documented, low-risk copy achieves the
  same protection.
- **Rely solely on ADR-051's transcript-liveness guard, generalized to detect these squatters.**
  Rejected — structurally cannot work for this failure mode (see Judgment calls): the squatting
  worktrees were never a session's own cwd, so no transcript ever exists to check.
- **Broaden dev-env#346 to cover this bug instead of filing a new issue.** Rejected after reading
  #346's actual filed content — its scope is specifically `biweekly-retro`'s report-writing step, a
  real, different, still-open problem. Broadening it would make its own title inaccurate and conflate
  two independently-fixable issues; filing [dev-env#747](https://github.com/brownm09/dev-env/issues/747)
  correctly scoped, cross-referencing both #346 and #630, better fits this org's own
  search-before-filing/extend-don't-duplicate convention.

---

## References

- `claude/scripts/pre-tool-use-journal-draft-worktree-guard.py` — the new hook
- `claude/scripts/_worktree_topology.py` — `DRAFT_BRANCH_RE`, `non_canonical_worktrees_matching`,
  `PatternSquatAction`/`pattern_squat_action` (new); `park_branch_for` (reused unchanged)
- `claude/scripts/prune-merged-worktrees.py` — the new per-worktree draft-squat branch,
  `_origin_ahead_count`
- `claude/scripts/tests/test_journal_draft_worktree_guard.py`,
  `claude/scripts/tests/test_worktree_topology.py`,
  `claude/scripts/tests/test_prune_merged_worktrees.py` — new/extended coverage
- `claude/settings.json` — `PreToolUse`/`Bash` registration, alongside
  `pre-tool-use-canonical-mutate-guard.py`
- [dev-env#747](https://github.com/brownm09/dev-env/issues/747) — motivating issue, full live
  evidence, and the fix this ADR implements
- [dev-env#346](https://github.com/brownm09/dev-env/issues/346) — narrower, unrelated
  `biweekly-retro`-specific issue this ADR's fix stops mis-citing
- [dev-env#630](https://github.com/brownm09/dev-env/issues/630) — the inverse-direction sibling bug
  (canonical hijacked onto a stray branch), commented with fresh evidence, not folded into this fix
- [ADR-058](058-worktree-squatting-main-detection-correction.md) — the `main`-squat park precedent
  this ADR generalizes to an arbitrary branch pattern; shared module origin
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the inverse-direction sibling hook this
  one's git-parsing logic is a documented duplicate of; its Amendment 2 bug-fix history is why this
  ADR chose duplication over a fresh reimplementation
- [ADR-082](082-journal-compose-worktree-isolation.md) — `journal-compose`'s own prior, narrower solve
  of an adjacent version of this problem (a detached compose worktree, never a named draft branch);
  why `is_merged()` can never fire for a composed draft branch (motivating `_origin_ahead_count`)
- [ADR-051](051-worktree-liveness-guard.md) / [ADR-086](086-journal-compose-liveness-guard.md) — why
  the transcript-mtime liveness pattern doesn't transfer to this failure mode
- [ADR-093](093-journal-canonical-hijack-guard.md) — the closest structural precedent (a standalone,
  engineering-journal-specific corrective mechanism reusing `_worktree_topology.py`'s shared
  primitives under a narrower invariant); its own References section corrected the dev-env#346
  mis-citation as part of this PR
