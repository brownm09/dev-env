# ADR-085: Bash Repo/Branch Drift Detection at Commit / PR-Create / PR-Merge Checkpoints

**Date:** 2026-07-05
**Status:** Accepted
**Tags:** hooks, pre-tool-use, post-tool-use, bash, git, gh-cli, drift-detection, silent-failure, windows, shared-module

---

## Context

[dev-env#573](https://github.com/brownm09/dev-env/issues/573) reports that during a long
lifting-logbook session, the Bash tool's tracked working directory — and, separately, a
different repo's checked-out branch — silently reverted to a stale/default state multiple
times, with **no error surfaced**. Once, this caused real damage: `gh pr create` (called
with no explicit `--head`) picked the wrong implicit head branch, opening a PR containing
none of the intended work, which had to be closed and recreated.

The issue's own investigation (see its second comment) points to an untracked
`bash.exe.stackdump` file found in the affected repo during the incident — Git Bash (MSYS2)
crashing intermittently, plausibly correlated with a concurrent disk-full-to-0-bytes incident
in the same session — with each freshly-spawned `bash.exe` after a crash silently reverting to
the harness's default working directory/branch, with zero signal to the session.

**What this ADR does not claim to fix.** The crash itself, and however Claude Code's own
harness tracks/restores Bash working-directory state across a persistent-shell restart, are
outside this repo's reach:

- MSYS2's own FAQ documents the "unable to remap"-class `bash.exe.stackdump` crash as caused by
  ASLR/address-rebasing/DLL-version ("magic number") mismatches, not low disk/memory. A separate
  known crash class ([msys2/MSYS2-packages#825](https://github.com/msys2/MSYS2-packages/issues/825))
  is a `STATUS_STACK_OVERFLOW` from certain command-substitution patterns.
- There is an **open, unfixed upstream Claude Code issue for this exact symptom**:
  [anthropics/claude-code#37920](https://github.com/anthropics/claude-code/issues/37920) —
  auto-closed by a stale-bot with no fix landed. One reporter found 6 identical-signature
  stackdumps across ordinary sessions with no resource pressure mentioned, suggesting these
  crashes may occur at some baseline rate on Windows regardless of disk/memory pressure.
- The harness's own cwd-tracking has **documented reliability gaps independent of the crash
  theory**: [anthropics/claude-code#11067](https://github.com/anthropics/claude-code/issues/11067)
  shows the official "Shell cwd was reset to `<dir>`" self-correction message can repeat
  without actually fixing subsequent command execution directory.
- Circumstantial local evidence: the `bash.exe.stackdump` file present in dev-env's own working
  directory during this investigation was itself truncated — only raw stack-trace frames, no
  exception-type header or register dump a fully-written dump normally includes. Consistent with
  (not proof of) a write cut short by disk exhaustion.

None of this is dev-env's to fix. What dev-env **can** do is make the *symptom* — a
consequential command silently running against the wrong repo/branch — loudly visible at the
exact moments it caused real damage.

A newer `CwdChanged` hook event exists in Claude Code, but its payload schema and its behavior
for *involuntary* changes (as opposed to a deliberate `cd`) are undocumented and unconfirmed,
and it is not wired in dev-env today. This decision does not build on it — the mechanism below
needs no dependency on an unverified event contract.

dev-env already has a disk-pressure preflight safety net
(`claude/scripts/disk-space-check.py`, a `UserPromptSubmit` hook warning at 20 GB free and
auto-reclaiming at 10 GB on `C:`) that partially answers "should the harness do something
proactive under resource pressure" for disk specifically. It has one structural limitation
worth naming: `UserPromptSubmit` fires once per user prompt, not per tool call, so exhaustion
occurring *within* a single long agentic turn can outrun it. Tightening that to a
`PreToolUse(Bash)` check is cheap (`shutil.disk_usage()` is a syscall, not a subprocess spawn)
but is independent of this ADR's mechanism — a candidate fast-follow, not built here.

---

## Decision

Track a lightweight per-session "last known repo + branch" marker after every Bash call, and
compare it against the current state at the three moments where an incorrect repo/branch
causes real, hard-to-undo damage: `git commit`, `gh pr create`, `gh pr merge`. Surface a loud,
**advisory-only** warning on a mismatch — never block.

**Advisory-only, not blocking, is a deliberate choice, not a default.** The mechanism
structurally cannot distinguish a legitimate `EnterWorktree`/`cd` switch from a silent
crash-induced revert — both simply look like "the repo/branch changed since the last Bash
call." `EnterWorktree`/`ExitWorktree` are user-callable tools with no hook integration in
Claude Code today (confirmed by a full audit of `claude/settings.json` and every existing
hook), so there is no signal available to tell the two apart. Blocking on every mismatch would
misfire on every ordinary worktree-then-commit sequence — a genuinely common, correct pattern.
This was confirmed with the user directly during planning; see the Judgment calls section.

**New shared module — `claude/scripts/_bash_state.py`:**
- `current_repo_state(cwd)` — a single `git rev-parse --show-toplevel --abbrev-ref HEAD` call
  returning `(repo_root, branch)`; both `None` on a non-git cwd or any subprocess failure/timeout,
  `branch` alone `None` on a detached HEAD. Shared by all four consuming files (see Judgment
  calls — this consolidates what briefly existed as three near-duplicate copies).
- `state_path(session_id, scratch=None)` — `~/.claude/scratch/bash_state_<session_id>.json`,
  following `_hookutil.py`'s existing `scratch=` injectable-override convention.
- `write_state(session_id, repo_root, branch, cwd, scratch=None)` — best-effort JSON write;
  swallows all I/O errors (advisory side-channel, never a hard dependency).
- `read_state(session_id, scratch=None)` — best-effort JSON read; `None` on missing/corrupt
  file or non-dict JSON (fail-open — a session's first Bash call, or a cleared scratch dir, is
  not an error).
- `cleanup_stale_state(scratch=None)` — removes state files older than `MAX_AGE_DAYS` (30),
  mirroring `_hookutil.cleanup_stale_sentinels`. Called from `post-tool-use-cwd-track.py` after
  every write, since that hook is the only place a state file is ever created.
- `format_drift_warning(recorded, current_repo_root, current_branch, current_cwd)` — **pure**
  function. Returns `None` when `recorded` is `None` (no prior state yet), when *both* current
  values are `None` (the checkpoint's own git read failed/timed out — see Judgment calls), or
  when `(repo_root, branch)` is unchanged; otherwise a formatted multi-line warning naming both
  states.

**Comparing on `(repo_root, branch)` rather than raw `cwd` is the key precision choice.** It
does not fire for ordinary same-repo subdirectory navigation (routine and extremely common —
`git rev-parse --show-toplevel` differs from a raw `cwd` string comparison specifically to
absorb this), only for a genuinely different repo/worktree or a different branch of the same
repo. This covers both failure sub-modes the issue describes: a worktree silently replaced by
the canonical root (different `repo_root`), and the same repo with its branch silently
reverted (same `repo_root`, different `branch`).

**New hook — `claude/scripts/post-tool-use-cwd-track.py`** (`PostToolUse`, `Bash` matcher):
after every Bash call, `_bash_state.current_repo_state(cwd)` then `_bash_state.write_state(...)`
and `_bash_state.cleanup_stale_state()`. Always exits 0; a cwd that isn't a git repo, or a `git`
call that fails/times out, simply records `None` rather than raising.

**Extended `claude/scripts/pre-commit-branch-check.py`** (`git commit`): appends the drift
warning (if any) to its existing branch-display `systemMessage`. Also fixed a latent
inconsistency introduced by this same change: an early version of `current_branch()` returned a
display placeholder (`"<unknown>"`/`"<detached HEAD>"`) on failure rather than `None` — comparing
that placeholder string against the writer's `None` would have manufactured a spurious drift
warning on every detached-HEAD commit. Caught during development, before the function was later
extracted into `_bash_state.current_repo_state()` entirely (see Judgment calls);
`build_message()` maps `None` to a display placeholder only at print time.

**Extended `claude/scripts/pre-pr-create-check.py`** (`gh pr create`): adds a
"Current branch: ... (repo: ...)" display line — this hook previously showed no branch/repo
context at all — plus an explicit reminder to pass `--head <branch>` (the issue's own suggested
defense-in-depth measure), plus the drift-warning append. Inserted between the pre-existing
numbered checklist and the (conditionally-numbered) baseline-advisory/doc-reconciliation lines,
rather than into the numbered sequence itself, to avoid colliding with `_baseline_advisory()`'s
hardcoded "4." numbering.

**New hook — `claude/scripts/pre-merge-branch-check.py`** (`gh pr merge`): mirrors
`pre-commit-branch-check.py`'s pattern exactly, reusing the same `_hookio.scan_top_level`-based
`is_pr_merge_command()` predicate already established in `pre-merge-message-check.py` /
`pre-merge-numbering-check.py` (dev-env#519) rather than a new regex. A separate file, not
folded into `pre-merge-message-check.py` — see Judgment calls.

**Wired** into the existing `"matcher": "Bash"` arrays in `claude/settings.json`:
`pre-merge-branch-check.py` as a 7th `PreToolUse` entry (grouped next to
`pre-merge-message-check.py`), `post-tool-use-cwd-track.py` as a 10th `PostToolUse` entry.

---

## Judgment calls

### Advisory-only, confirmed with the user, not assumed

The three-way design choice (never block / block with an override token / block only for
PR-create+merge) was put to the user directly rather than decided unilaterally, since it trades
real safety against real workflow friction in a way the code alone can't resolve. The user chose
advisory-only. This document treats that as the settled answer, not a default that quietly
slipped through — a future change to blocking would need its own review of this same tradeoff.

### `(repo_root, branch)` comparison key, not raw `cwd`

An earlier design compared raw `cwd` strings directly. That would fire on every ordinary `cd`
into a subdirectory of the same repo — ubiquitous and never itself a problem — making the
warning noisy enough to be ignored (defeating its purpose) long before it ever caught a real
incident. Keying on `git rev-parse --show-toplevel` + `git branch --show-current` instead
means the comparison only reacts to a change that could plausibly matter: a different
repo/worktree, or a different branch of the same repo.

### No attempt to suppress legitimate `EnterWorktree`/`cd` cases

`EnterWorktree`/`ExitWorktree` are not hookable (confirmed during this investigation — no entry
in `claude/settings.json`, no script in `claude/hooks/` or `claude/scripts/` referencing either
tool name), so there is no way to special-case "a deliberate worktree switch just happened."
Rather than build brittle heuristics to approximate that signal, the design accepts that the
warning will also fire after a legitimate switch — harmless, since it's advisory-only and the
message is still true and mildly useful even then (it's the same "verify branch before
committing" discipline `claude/CLAUDE.md` already asks for manually, now automatic).

### Three separate checkpoints, not one blanket per-Bash-call hook

`git commit`, `gh pr create`, and `gh pr merge` are the three commands whose whole job is to
read the *current* repo/branch state as an implicit default — exactly where a silent revert
causes real, hard-to-undo damage (a bad commit, a wrong-branch PR, a wrong-branch merge). The
*comparison* work (reading current state and checking it against the record) is confined to
these three checkpoints rather than running on every Bash call, since only these commands act on
stale state in a way that matters. The *write* side (`post-tool-use-cwd-track.py`) still has to
run on every Bash call regardless — any command, including a bare `cd`, can change cwd, so the
record can't be predicated on command content the way the read side is. That write-side cost is
real, not negligible (see the "combining the two git calls" Judgment call below), which is why it
gets its own optimization rather than being waved away as free.

### `pre-merge-branch-check.py` is a new file, not folded into `pre-merge-message-check.py`

`pre-merge-message-check.py`'s entire existing contract is a *blocking* check (`sys.stderr.write`
+ exit 2) against a manually-maintained user message queue — a different mechanism, a different
output channel, and a different exit-code contract than the advisory `systemMessage`/exit-0
pattern this ADR's other two hooks use. Bolting a second, unrelated concern onto that file would
require either restructuring its exit semantics or awkwardly splicing two unrelated response
styles into one script. A small new file, mirroring `pre-commit-branch-check.py`'s already-
established shape and reusing the already-shared `_hookio.scan_top_level` engine for merge
detection, is both cheaper and more consistent with this repo's existing one-hook-per-concern
convention (e.g. ADR-024 and ADR-071 are separate files despite both being worktree/canonical-
root guards).

### `current_branch()`/`current_repo_root()` extracted into `_bash_state.py`, reversing an earlier "duplicate, don't share" call

The first version of this PR kept each of `pre-commit-branch-check.py`, `pre-pr-create-check.py`,
and `pre-merge-branch-check.py` defining its own small `current_branch()`/`current_repo_root()`
pair, reasoning by analogy to `_first_line()`'s existing independent-copies precedent in
`_hookio.py` / `pre-tool-use-canonical-mutate-guard.py`. A `/review` pass overturned that
reasoning with a concrete point rather than a hypothetical one: this PR's own history already
demonstrated the coupling risk (see the `pre-commit-branch-check.py` paragraph above) — the
duplication had already caused one bug during development, not just a theoretical one `_first_line`'s
precedent didn't need to worry about. Given the risk was no longer speculative, the three copies
(plus a fourth, separately-shaped pair in `post-tool-use-cwd-track.py`) were consolidated into one
`current_repo_state()` in `_bash_state.py`, which all four files now import. This also incidentally
halves `post-tool-use-cwd-track.py`'s subprocess count (see the next Judgment call).

### Combining the two `git` calls into one, for the every-Bash-call caller

`post-tool-use-cwd-track.py` runs on every single Bash tool call, unlike the three checkpoint
hooks, which only run at a low-frequency commit/PR-create/PR-merge moment. A `/review` pass flagged
that this hook was paying for two sequential subprocess spawns (`git rev-parse --show-toplevel`,
then `git branch --show-current`) on every call — the one place in this hook family where the
spawn count is not incidental. `current_repo_state()` combines both into one
`git rev-parse --show-toplevel --abbrev-ref HEAD` invocation (toplevel on the first output line,
abbreviated ref — literally `"HEAD"` when detached — on the second), halving the per-call cost.
The three checkpoint hooks get this same combined call for free by importing the same function,
even though their own call frequency didn't strictly require the optimization.

### Suppressing the drift warning when the current git read itself fails

A `/review` pass found that `format_drift_warning` could fire a *self-contradictory* warning: if
the checkpoint's own `current_repo_state()` call transiently failed or timed out, both current
values become `None`, and the naive tuple comparison `(real, real) != (None, None)` read that as
drift — producing a message that claimed the repo/branch "changed" while showing the *same* cwd on
both the "was" and "now" lines. This is exactly the noisy-false-positive failure mode the
`(repo_root, branch)` comparison key was designed to avoid (see the comparison-key Judgment call
above), just reached via a different path (a failed *current* read, not a stale *recorded* one).
Fixed by an early-return: both current values `None` means "current git state unknown," which
can't be meaningfully compared against anything — genuine drift (worktree→canonical,
branch-only-reversion) always leaves at least one current value populated, so this guard never
suppresses a real detection.

---

## Consequences

- **A session that silently drifted onto the wrong repo/branch now gets a loud, hard-to-miss
  warning** at the exact moment (`git commit` / `gh pr create` / `gh pr merge`) where acting on
  that wrong state would otherwise cause real damage — directly answering the "no error,
  warning, or indication" complaint in dev-env#573.
- **Does not fix the root cause.** The Git Bash crash (if that is indeed the trigger) can still
  happen; the harness's own cwd-tracking behavior across a shell restart is unchanged. This is a
  symptom-visibility mitigation, not a cure — documented explicitly in the issue update so it is
  never mistaken for a fix.
- **Does not catch every case.** A revert that never reaches one of the three checkpoints (e.g.
  a session that only reads files or runs non-mutating commands after the revert) goes
  unflagged by design — though by the same token, no irreversible damage occurs in that case
  either, since nothing consequential ran.
- **Testing.** `claude/scripts/tests/test_bash_state.py` — pure-function coverage of
  `state_path`/`write_state`/`read_state` (including malformed-JSON and unwritable-scratch
  fail-open paths), `cleanup_stale_state` (stale-removed/fresh-kept/non-matching-ignored/
  missing-dir-no-crash), and `format_drift_warning`'s six cases (no-drift / current-read-failed /
  repo-changed / branch-only-changed / no-prior-state / missing-field). `test_pre_commit_branch_check.py`,
  `test_pre_pr_create_check.py`, and `test_pre_merge_branch_check.py` cover each hook's own
  message-building function and (for the merge hook) the `is_pr_merge_command` detector,
  following this repo's pure-helper-only testing convention — `current_repo_state()`'s `git`
  subprocess call is not covered (matches every sibling hook in this family; see Judgment calls
  for why it now lives in one place instead of four).
- **Observability.** All four scripts print/append `systemMessage` JSON on stdout (the standard
  advisory-hook channel in this repo) and always exit 0 — nothing here can block a tool call or
  hide a reason behind a discarded stream.
- **Security.** N/A — no new secret handling, no new authz surface; the state file contains only
  a repo path, a branch name, and a cwd, all already visible in the session transcript.
- **Resilience / failure modes.** Every I/O and subprocess call in the new/changed code is
  wrapped and fails open (missing state file, corrupt JSON, non-git cwd, git timeout/failure all
  degrade to "no comparison possible" rather than raising) — matches this repo's established
  advisory-hook safe-exit convention.
- **Performance.** The write side adds one combined subprocess spawn (`git rev-parse
  --show-toplevel --abbrev-ref HEAD`) per Bash call — half the cost of the two separate calls an
  earlier version of this PR shipped, per a `/review` finding (see Judgment calls). The read side
  adds no new subprocess calls beyond what each hook already paid for its own branch display.
- **Data integrity.** N/A — the state file is a disposable, per-session advisory cache, not a
  system of record; losing or corrupting it degrades gracefully to "no drift comparison this
  call," never to incorrect data being trusted downstream. It is also now bounded: `/review`
  flagged that it was the only per-session file in this codebase with no expiration, so
  `cleanup_stale_state` (30-day `MAX_AGE_DAYS`, matching `_hookutil.py`) now backs it.
- **ADR warranted** because this introduces two new hook scripts and one new shared module, all
  wired into `claude/settings.json`, and establishes a workflow rule (`claude/CLAUDE.md`'s
  extended branch-verification bullet) that other sessions rely on — the same warranting shape
  as ADR-024/ADR-071.

---

## References

- `claude/scripts/_bash_state.py` — shared state module
- `claude/scripts/post-tool-use-cwd-track.py` — state-recording hook
- `claude/scripts/pre-commit-branch-check.py`, `pre-pr-create-check.py`,
  `pre-merge-branch-check.py` — the three checkpoint hooks
- `claude/scripts/tests/test_bash_state.py`, `test_pre_commit_branch_check.py`,
  `test_pre_pr_create_check.py`, `test_pre_merge_branch_check.py` — test coverage
- `claude/settings.json` — hook wiring
- `claude/CLAUDE.md` — extended "Verify branch before editing and before every commit" bullet
- [dev-env#573](https://github.com/brownm09/dev-env/issues/573) — motivating issue
- [anthropics/claude-code#37920](https://github.com/anthropics/claude-code/issues/37920) —
  open, unfixed upstream issue for the same `bash.exe.stackdump` symptom
- [anthropics/claude-code#11067](https://github.com/anthropics/claude-code/issues/11067) —
  documents the harness's own cwd-tracking reliability gap independent of the crash theory
- [MSYS2 FAQ](https://www.msys2.org/docs/faq/) — documented causes of the "unable to remap"
  stackdump class (ASLR/DLL-version conflicts, not disk/memory pressure)
- [msys2/MSYS2-packages#825](https://github.com/msys2/MSYS2-packages/issues/825) — a separate
  known `STATUS_STACK_OVERFLOW` stackdump class from command-substitution patterns
- [ADR-024](024-worktree-path-guard-hook.md), [ADR-051](051-worktree-liveness-guard.md) — the
  closest existing analogs; both solve adjacent-but-different problems (orphaned-worktree
  liveness, session-liveness-via-transcript-mtime) and neither tracks shell/process continuity
  or cwd/branch drift
- [ADR-064](064-shared-hookutil-sentinel-transcript-locate.md) — the `scratch=` injectable
  per-session marker-file convention `_bash_state.py` follows
- [ADR-045](045-pre-install-freespace-gate.md) — existing disk-pressure preflight-gate prior
  art (a different mechanism, for a different resource, referenced for context only)
- `claude/scripts/disk-space-check.py` — the existing `UserPromptSubmit`-scoped disk-pressure
  safety net referenced in Context
- [Claude Code Hooks documentation](https://code.claude.com/docs/en/hooks) — `PreToolUse`/
  `PostToolUse` payload schema (`cwd`, `session_id`), and the `CwdChanged` event this decision
  does not depend on
