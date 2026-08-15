# ADR-024: PreToolUse Hook to Block Canonical-Root Writes from Worktrees

**Date:** 2026-05-23 (amended 2026-06-06, 2026-07-14, 2026-07-14, 2026-07-14, 2026-08-15)
**Status:** Accepted
**Tags:** hooks, worktrees, pre-tool-use, file-safety, write, edit, orphaned-worktree, worktree-membership

---

## Context

Claude Code sessions launched inside a worktree (`<repo>/.claude/worktrees/<name>/`) receive `cwd` pointing at the worktree directory. However, when the model constructs an absolute `file_path` starting at the canonical repo root (e.g., `C:/Users/brown/Git/dev-env/docs/foo.md`), the Write/Edit/NotebookEdit tools resolve that path against the host filesystem — landing on the **main working tree**, not the worktree.

The failure is silent: no error fires, the session continues, and the file appears on the wrong tree. Recovery is mechanical (`cp` into the worktree, `rm` orphans) but costs tokens and risks orphans being missed entirely.

This failure was documented three times in career-playbook sessions (most recent: PR #275, stub `2026-05-22_140307.stub.md`). Tracked downstream at `brownm09/career-playbook#276`.

The failure is harness-level — any repo using Claude-managed worktrees is vulnerable. A per-project CLAUDE.md heuristic only protects one repo and relies on the model reading and obeying it mid-session, which is precisely what failed three times.

---

## Decision

Add a new `PreToolUse` hook (`pre-tool-use-worktree-path-check.py`) that fires on `Write`, `Edit`, and `NotebookEdit` tool calls.

**Logic:**
1. If `cwd` does not match the pattern `.../.claude/worktrees/<name>`, exit 0 (no-op).
2. Extract `canonical_root` (everything before `/.claude/`) and `worktree_root` (`canonical_root + /.claude/worktrees/<name>`).
3. Read `file_path` (Write/Edit) or `notebook_path` (NotebookEdit) from tool input.
4. If the path is relative → exit 0.
5. If the path starts with `canonical_root` but **not** `worktree_root` → exit 2 with a blocking `{"reason": "..."}` message naming the attempted path, the active worktree root, and the corrected path.
6. Otherwise → exit 0.

**Wired** in `claude/settings.json` under `hooks.PreToolUse` with three separate matcher entries (`Write`, `Edit`, `NotebookEdit`), each invoking the same script.

---

## Judgment calls

### Block (exit 2), not rewrite

Silently rewriting the path would be the same class of silent failure in the opposite direction: the model issues one path, something else executes. Blocking forces the model to re-issue with the correct path, which is the behavior we want to reinforce and that makes the fix visible in the session transcript.

### Scope: Write / Edit / NotebookEdit only, not Bash

Bash commands can write files via redirects, `cp`, `mv`, here-docs, and other mechanisms that are harder to parse reliably from a command string. The three file tools have a well-defined path field in structured tool input. Bash is deferred — extend only if a recurrence happens through that surface.

### Three separate matcher entries, not one unmatched entry

Using explicit matchers (`"matcher": "Write"`, etc.) limits the hook to only those three tool call types. An unmatched entry would fire on every PreToolUse event (including Bash, Glob, Grep, etc.), adding overhead for no benefit.

### No path rewriting in the error message when `os.path.relpath` raises ValueError

On Windows, `os.path.relpath` raises `ValueError` when source and target are on different drives. The hook falls back to a descriptive placeholder rather than crashing, preserving the blocking behavior.

---

## Consequences

- **Write/Edit/NotebookEdit calls with wrong absolute paths now fail immediately** with a clear message instead of silently landing on the main working tree.
- **No-op outside worktrees** — the hook exits 0 instantly when `cwd` does not match the worktree pattern.
- **Coverage gap remains for Bash** — commands like `cp`, `tee`, or here-doc redirects that write to the canonical root are not intercepted. This is acceptable for now given the complexity; extend if recurrence is observed.
- **Bypass for intentional canonical edits from a worktree:** The hook blocks `Write`, `Edit`, and `NotebookEdit` — not `Bash`. When a worktree session legitimately needs to modify a canonical repo file (e.g., editing `settings.json` on a config branch checked out in the main working tree), use `Bash` with `node -e` or a targeted `sed`/`py -3` invocation. This is the correct pattern — the hook is designed to surface accidental path mistakes, not to prevent deliberate file operations through a different tool surface.
- **Block reason is written to stderr, not stdout** — Claude Code discards a `PreToolUse` hook's stdout on exit code 2, so a reason printed to stdout is silently invisible to the model even though the block itself still works. Both `main()` block sites emit through a shared `_block()` helper to keep this from drifting (dev-env#469 — the hook originally printed to stdout at both sites for over a month before this was caught and fixed).
- **ADR warranted** because the hook is a new file under `claude/scripts/`, is wired in `claude/settings.json`, and establishes a harness-level safety invariant applicable to all repos using Claude-managed worktrees.

---

## Addendum (2026-06-06): orphaned-worktree liveness guard (dev-env#328)

### Problem the original decision missed

The original logic keys entirely on the cwd **path string**. It extracts
`worktree_root` from the path and checks whether `file_path` is lexically inside
it — but never verifies the worktree directory is a *live, registered* git
worktree.

An **orphaned worktree** — a `.claude/worktrees/<name>/` directory that still
exists on disk but has lost its `.git` link file and is no longer in
`git worktree list` — defeats this. Git, finding no `.git` at the directory,
walks **up** the tree and resolves every command to the **canonical** repo's
`.git`. The harness still treats the directory as the session's worktree (sets
cwd there, force-resets cwd to it after each command), so the failure is silent:

- `git status`/`branch`/`stash`/`checkout` operate on the canonical checkout —
  `git stash -u` stashed an unrelated branch's WIP; `git checkout -b` moved the
  canonical checkout onto a new branch.
- `Write`/`Edit` (absolute paths) landed files in the disconnected directory,
  invisible to git.

For this case the original hook computes `worktree_root` from the path, sees the
target is "inside" it, and **passes** — missing the exact case it most needs to
catch. Observed in a career-playbook session on 2026-06-06; recovery took several
careful steps (restore canonical branch, `git stash pop`, `git worktree add
--force`, move stranded files back).

### Extended decision

Before the path-scoping check, `main()` now asserts the worktree is **live** via
`_worktree_is_live(worktree_root, cwd)`:

1. `<worktree_root>/.git` must exist (the worktree link file). Missing → **not
   live** (the documented orphan signature; caught without spawning git).
2. `git -C <cwd> rev-parse --show-toplevel` must equal `worktree_root`. A
   resolution to the canonical root (or anywhere else) → **not live** (subtle
   mis-resolution).
3. If git cannot run at all (returns `None`) but the `.git` link is present →
   treated as **live** — a transient git failure must not block every write when
   the link file clearly exists.

Not-live → exit 2 with a `{"reason": ...}` message naming the worktree and cwd
and giving the recovery recipe `git worktree add --force <worktree_root> <branch>`.

### Judgment calls (addendum)

- **Check placed before path-scoping.** The orphan risk applies to *any* write
  from the dead cwd — relative paths and in-worktree absolute paths included, not
  just canonical-root absolute paths — so the liveness gate runs first and covers
  all three.
- **Fail-closed on a genuine orphan, fail-open on transient git failure.** A
  block is recoverable (clear message + recipe); a silent wrong-tree write is
  not. But blocking every write when git is momentarily unavailable yet the
  `.git` link plainly exists would be a worse failure mode, so step 3 fails open.
- **Both signals retained despite the per-write git spawn.** The `.git`-existence
  check (signal 1) catches the documented incident — an orphan whose link file is
  gone — with no subprocess. Signal 2 (`git rev-parse --show-toplevel`) is *not*
  merely belt-and-suspenders: it catches a distinct, real orphan mode where the
  `.git` file still exists but its `gitdir:` target was removed (e.g. a later
  `git worktree prune` deleted `<canonical>/.git/worktrees/<name>` while the
  checkout dir remained), so git silently resolves up to the canonical repo even
  though signal 1 passes. The cost — one fast, windowless `git rev-parse` per
  file write *in a worktree only* (~10–30 ms, `CREATE_NO_WINDOW`) — is acceptable
  for closing that second mode; memoizing liveness per session was considered and
  rejected to keep the hook stateless.
- **Extend the hook, not a new SessionStart guard.** Option A (this) converts the
  silent failure into a hard block at the moment of risk and is testable in the
  same hermetic style; Options B/C (issue #328) were not pursued.
- **`import _winsubp`.** The hook now spawns `git` under `pythonw`, so it adopts
  the console-flash-suppression module per ADR-007.

### Consequences (addendum)

- **Performance:** one `git rev-parse --show-toplevel` per file write *in a
  worktree only*, short-circuited (no subprocess) when the `.git` link is already
  missing. No-op outside worktrees is unchanged.
- **Coverage:** still limited to `Write`/`Edit`/`NotebookEdit`; Bash writes from
  an orphan remain uncovered (same deferral as the original decision).
- Covered by `claude/scripts/tests/test_worktree_path_check.py` (hermetic unit +
  subprocess integration tests).

---

---

## Addendum (2026-07-14): sibling-worktree carve-out (dev-env#750)

### Problem

Step 5 of the original logic blocks a write when `file_path` starts with `canonical_root` but not `worktree_root`. This correctly blocks writes landing on the shared canonical working tree, but also — incorrectly — blocks writes targeting *another worktree* under the same canonical root (a sibling-worktree write).

**Motivating incident:** During the 2026-07-12 journal compose, the compose session ran with its cwd inside an `engineering-journal/.claude/worktrees/<session-branch>/` worktree. Writing to `engineering-journal/.claude/worktrees/compose-2026-07-12/sessions/...` was blocked even though the compose worktree is its own isolated tree, not the shared canonical working tree. The workaround was `shutil.copy2` via scratch files — fragile and token-costly. Root cause filed as dev-env#750.

### Extended decision

Insert a new step between original steps 5 and 6:

**5a. If the target path is itself inside another worktree under the same canonical root → exit 0 (no-op).**

Implementation: `_WORKTREE_RE.match(file_norm)` matches if the target path contains `/.claude/worktrees/<name>`; compare `_normalize(target_m.group(1))` (the target's canonical root) against `canonical_norm` (the session's canonical root). A match means the write goes to a different worktree's own isolated tree — allow it.

### Judgment calls (addendum)

**General carve-out, not compose-specific regex.** A `compose-YYYY-MM-DD` pattern solves the immediate case but creates a maintenance surface. The general rule — any write targeting a worktree under the same canonical root is safe — is semantically correct and forward-compatible.

**Same-canonical-root only.** The `canonical_norm` comparison ensures the carve-out only applies to sibling worktrees of the *same* repo. A write from an EJ worktree to a dev-env worktree path is not reachable by step 5a — the dev-env path doesn't start with the EJ canonical root, so the earlier check at step 4 exits 0 first.

**Liveness is not checked on the target.** The hook checks liveness only for the session's own worktree (step 3). The hook's purpose is to prevent accidental writes landing on the *shared canonical working tree*, not to audit any other worktree's state.

### Consequences (addendum)

- `test_main_allows_write_to_sibling_worktree` added to `claude/scripts/tests/test_worktree_path_check.py`.
- No performance impact: one additional regex match on an already-normalized path, behind the two cheaper pass-throughs above it.

---

## Addendum (2026-07-14): sibling-directory worktree convention, `<repo>-worktrees/<name>` (dev-env#760)

### Problem

`_WORKTREE_RE` matched only the nested `.claude/worktrees/<name>` shape. This environment also uses a
second convention reached via manual `git worktree add` (never `EnterWorktree`): a sibling directory
named `<repo>-worktrees/` next to the canonical checkout, holding named worktrees as its own
subdirectories — e.g. `dev-env-worktrees/adr-096-correction`, confirmed live via `git worktree list`
alongside `dev-env-worktrees/fix-758-double-resolve`. A genuine live worktree at this second shape was
invisible to this hook's own regex, exactly the same gap [ADR-071 Amendment 5](071-canonical-checkout-mutate-guard-hook.md#amendment-5-2026-07-14--recognize-the-sibling-directory-worktree-convention-repo-worktreesname-dev-env760)
found and fixed in the sibling `pre-tool-use-canonical-mutate-guard.py` hook, filed together as
dev-env#760.

For *this* hook specifically, a cwd shaped like the sibling-directory convention that failed to match
meant step 1 ("if cwd does not match ... pass immediately") exited 0 unconditionally — the hook was a
silent no-op for any Write/Edit/NotebookEdit issued from inside such a worktree, including the exact
canonical-root-escape scenario this hook exists to catch (step 5 was never reached at all).

### Extended decision

`_WORKTREE_RE` becomes an alternation: `.claude/worktrees` (nested) OR `[^/\\]+-worktrees`
(sibling-directory, requiring at least one character before the `-worktrees` marker). `canonical_root`
(`m.group(1)`) and `worktree_root` (`m.group(0)`) are extracted identically regardless of which
alternative matched — every step downstream (the liveness guard, the path-scoping checks, the
dev-env#750 sibling-worktree carve-out) operates on those two strings alone and needed no shape-specific
changes.

A bare `<repo>-<suffix>` sibling with no `-worktrees` marker (e.g. `dev-env-188`) remains unmatched,
deliberately — the same still-ambiguous shape `_worktree_canon.py`'s own tested contract already leaves
out of scope; see ADR-071 Amendment 5 for the full reasoning, shared verbatim across both hooks.

### Judgment calls (addendum)

**Same regex text as `_worktree_canon.py` and `pre-tool-use-canonical-mutate-guard.py`'s
`_WORKTREE_PATH_FRAGMENT`.** All three files independently define this pattern (dev-env#510 tracks
consolidating them onto a shared module); this addendum keeps the three spellings in sync rather than
letting only one of them learn the new convention, per dev-env#760's own explicit ask to touch all three
call sites together.

**No new liveness-check logic.** The orphaned-worktree liveness guard (this ADR's first addendum) and
the sibling-worktree carve-out (this ADR's second addendum, dev-env#750) both already operate on
`worktree_root`/`canonical_root` as opaque strings — recognizing a second path shape needed no changes
to either.

### Consequences (addendum)

- `test_main_blocks_write_escaping_to_canonical_root_sibling_directory_convention` and
  `test_main_blocks_edit_from_orphaned_sibling_directory_worktree` added to
  `claude/scripts/tests/test_worktree_path_check.py`, mirroring the original decision's and the first
  addendum's coverage for the new shape.
- No performance impact: same single regex match, now covering one more alternative.

### Review hardening (dev-env#760/PR#764)

`/review` found this hook shared two of the three gaps described in [ADR-071 Amendment
5](071-canonical-checkout-mutate-guard-hook.md#amendment-5-2026-07-14--recognize-the-sibling-directory-worktree-convention-repo-worktreesname-dev-env760)'s
own "Review hardening" section — full reasoning there, since both hooks' logic and fix are identical;
summarized here for this hook's own record:

1. **Non-greedy capture let the sibling alternative steal a shallower match than a nested worktree
   deeper in the same path.** A `.claude/worktrees/<name>` worktree created inside a
   `<repo>-worktrees/<name>` sibling worktree resolved `worktree_root` to the OUTER sibling directory,
   which has no `.git` of its own at that exact path — so this hook's liveness guard wrongly blocked
   every write from the genuinely live inner worktree with an "orphaned worktree" message. Fixed by
   splitting the single combined-alternation `_WORKTREE_RE` into `_NESTED_WORKTREE_RE` (tried first) and
   `_SIBLING_WORKTREE_RE` (fallback only), via a shared `_match_worktree()` helper used at both this
   hook's call sites (cwd, and the dev-env#750 sibling-worktree-target check).
2. **`os.path.exists` on the `.git` link doesn't distinguish a worktree's `.git` FILE from a canonical
   checkout's `.git` DIRECTORY.** Renamed the `_worktree_is_live()` parameter from `path_exists=` to
   `path_isfile=` (default `os.path.isfile`) so a genuine canonical clone that happens to sit at a
   worktree-shaped path is correctly rejected rather than treated as live.
3. **Sibling-pattern divergence from `pre-tool-use-canonical-mutate-guard.py`'s equivalent fragment** —
   this hook's `_SIBLING_WORKTREE_RE` accepted a bare, unprefixed `-worktrees` directory that the
   mutate-guard's fragment already rejected. Reconciled by requiring at least one non-separator character
   immediately before the literal `-worktrees`, matching the mutate-guard exactly.

`claude/scripts/tests/test_worktree_path_check.py` grows from 8 to 10 tests for these three fixes:
`test_worktree_is_live_rejects_git_directory` (a real `.git` *directory*, not a stubbed lambda, is
correctly not-live) and `test_main_allows_write_to_nested_worktree_inside_sibling_directory_worktree` (a
real end-to-end reproduction of fix #1, using bogus-but-file `.git` links matching this file's existing
hermetic test style). The bare-`-worktrees`-rejection fix (#3) is covered by
`test_worktree_canon.py`'s `test_sibling_convention_bare_worktrees_dir_not_matched` at the pure-function
level, since both files share the identical corrected pattern.

---

## Addendum (2026-07-14): confirm the regex candidate against `git worktree list`, not path shape alone (dev-env#774 gap (b))

### Problem

`_match_worktree(cwd)`'s regex is trusted outright: whatever `(canonical_root, worktree_root)` it
extracts is used directly, with no independent confirmation that `cwd` is actually inside a real worktree
structure at all. A repo whose OWN root directory name literally ends in `-worktrees` (e.g.
`some-repo-worktrees`) breaks this: `_SIBLING_WORKTREE_RE` cannot distinguish "the `<repo>-worktrees/`
sibling-directory *container* convention" from "a repo whose own root directory name happens to literally
end in `-worktrees`." For the latter, a cwd like `some-repo-worktrees/src` matches the sibling pattern as
if `src` were a worktree name and `some-repo` (a truncated, likely nonexistent path) were the canonical
root — every Write/Edit/NotebookEdit from that subdirectory is then misclassified as targeting a non-live
"worktree" (no `.git` exists at the fabricated `worktree_root`) and blocked with a misleading "orphaned
worktree" message, while a write from the repo root itself (no subdirectory component for the regex's
trailing `[^/\\]+` to capture) passes through untouched — an inconsistent and confusing failure mode not
currently triggered anywhere in this environment (no repo here is named `*-worktrees`), but with no way to
disambiguate from the path string alone.

Found alongside [ADR-071 Amendment
6](071-canonical-checkout-mutate-guard-hook.md#amendment-6-2026-07-14--confirm-resolved-root-worktree-membership-via-git-worktree-list-not-path-shape-dev-env774-gap-a)'s
gap (a) during `/review` on PR #764 (dev-env#760's own sibling-directory convention fix), filed together
as [dev-env#774](https://github.com/brownm09/dev-env/issues/774).

### Extended decision

`_match_worktree(cwd)`'s regex match becomes a cheap PRE-FILTER only, not the final word. A new
`_resolve_worktree_scope(regex_canonical_root, regex_worktree_root, cwd)` confirms (or corrects) the
candidate against `git -C <cwd> worktree list --porcelain` (a new `_resolve_worktrees()`, parsed via the
shared `_worktree_topology.parse_worktree_porcelain` — the same reuse ADR-071 Amendment 6 makes of that
module):

- If git's own canonical (first) entry does NOT match the regex's `canonical_root` guess (compared via
  this hook's existing `_normalize()`), `cwd` was never really inside a worktree structure at all — the
  regex match was a path-shape false positive. Returns a sentinel (`canonical_root is None`) telling
  `main()` to no-op (exit 0) — this is the gap (b) fix itself: a repo literally named `some-repo-worktrees`
  now correctly falls through here, since git's canonical entry is `some-repo-worktrees` (the real repo
  root) while the regex guessed the truncated `some-repo`.
- If the canonical guess IS confirmed, the shared `find_worktree_by_path()` primitive (ADR-071 Amendment
  6) looks for `regex_worktree_root` among the repo's LINKED (non-canonical) entries. Found → confirmed
  live, using git's own path strings as the authoritative `canonical_root`/`worktree_root` going forward
  (rather than the regex-extracted substrings) for every downstream check. Not found → treated as
  `is_live = False` directly (orphaned or removed) — git's own membership list is stronger proof of "not
  currently registered" than the pre-existing `.git`-isfile + `rev-parse` heuristic it replaces here.
- When git can't answer AT ALL (the sibling-directory orphan case is the clean example: by construction
  it's never nested inside any repo for git to walk up to, so `git worktree list` fails outright, same as
  it always has), falls back to the regex-derived roots plus the pre-existing `_worktree_is_live()` check —
  a backstop, not the primary signal, per dev-env#774's own "replace (or backstop)" framing. This is also
  why every test already in this file before this addendum continues to pass unchanged: all of them build
  worktree fixtures from a bogus (non-real-repo) `.git` file rather than an actual git repo, so
  `_resolve_worktrees()` fails for every one of them and they all exercise this same fallback path,
  regardless of this addendum landing.

### Judgment calls (addendum)

**Pre-filter regex retained, not replaced outright.** This hook fires on every single Write/Edit/
NotebookEdit call — far more frequent than `pre-tool-use-canonical-mutate-guard.py`'s Bash-only trigger.
Running `git worktree list` unconditionally on every call (even when cwd doesn't look remotely
worktree-shaped) would add a subprocess spawn to the overwhelmingly common non-worktree case. The regex
stays as a free, cheap gate — a subprocess is spawned only once a candidate match already exists, exactly
mirroring this hook's own pre-existing performance posture (it already spawned one `git rev-parse` per
matched call for the liveness check; this addendum swaps THAT one subprocess for `git worktree list`
instead of adding a second one — same per-call subprocess count as before).

**git's own path strings supersede the regex's once confirmed.** Rather than keep using the
regex-extracted `canonical_root`/`worktree_root` substrings once git has answered, the confirmed entries'
own `path` fields become authoritative for every downstream check (the path-scoping logic, the
dev-env#750 sibling-worktree carve-out). This incidentally also closes any latent case-normalization
mismatch between the regex's string extraction and git's own canonicalized paths, though no such mismatch
was observed in practice.

**Orphan detection via absence-from-list, not a second liveness call.** Once git has successfully
answered for a given `cwd`, `regex_worktree_root` simply not appearing among the confirmed repo's linked
worktrees is treated as sufficient proof of non-liveness directly, without an additional
`_worktree_is_live()` call — that function remains valuable, but purely as this addendum's own fallback
path (git unavailable), not as a second, redundant check layered on top of a successful git answer.

### Consequences (addendum)

- `test_main_allows_write_from_subdirectory_of_repo_literally_named_worktrees_suffix` (the direct gap (b)
  reproduction — a real repo named `some-repo-worktrees`, write from a subdirectory, now correctly
  allowed) added to `claude/scripts/tests/test_worktree_path_check.py`, along with three more REAL-git-repo
  tests proving the git-confirmed path doesn't regress the hook's core purposes: an orphan nested in a
  real canonical is still blocked, a REAL `git worktree add`-registered worktree still gets zero-friction
  treatment, and canonical-root escape-detection still fires against a real canonical (not just the
  bogus-`.git`-file fixtures every pre-existing test in this file uses). Grows from 10 to 14 tests.
- New shared helpers `_worktree_topology.find_worktree_by_path()` (this ADR's sibling, ADR-071 Amendment
  6, introduces it) and this file's own `_resolve_worktrees()`/`_resolve_worktree_scope()`.
- No change to the Bash-coverage gap (still deferred, per the original Decision) or to the dev-env#750
  sibling-worktree carve-out's own logic — both operate downstream of `canonical_root`/`worktree_root` as
  opaque strings, unaffected by where those strings now come from.
- Performance: for the live-worktree hot path, unchanged subprocess count per matched call (one
  `git worktree list` in place of the one `git rev-parse` this hook already spawned) — the common
  non-worktree-shaped-cwd case still spawns nothing, gated by the same regex pre-filter as before. This
  claim is precise for that hot path but not for every path: the orphan-reject case goes from zero
  subprocesses (the old `.git`-isfile check short-circuited before any git call) to one (`git worktree
  list` now runs unconditionally once the regex matches, before any liveness signal is checked) — see
  Review hardening below, corrected during `/review`.

### Review hardening (dev-env#774/PR#783)

`/review` on this PR (two Opus-model subagents, correctness/security and reliability/performance/
maintainability, run independently) found three issues in the initial implementation above, all fixed in
the same PR before merge:

1. **A `UnicodeDecodeError` from `_resolve_worktrees`/`_resolve_git_toplevel` could silently disable
   enforcement entirely — a genuine correctness regression, not just an inconsistency.** Both functions'
   except-tuples were missing `ValueError` (a `UnicodeDecodeError` superclass) — present on the sibling
   hook's equivalent `_resolve_worktree_list` (ADR-071 Amendment 6) from the start, since that PR's own
   `_resolve_git_toplevel` already carried it from dev-env#576/PR#584's null-byte-path fix. `subprocess.run(...,
   text=True)` decodes git's stdout in the process locale encoding; a worktree/branch path containing bytes
   undecodable there raises `UnicodeDecodeError`. Uncaught, that error unwinds to this hook's outer
   `except Exception: sys.exit(0)` — the hook exits 0 with **no enforcement at all**, never reaching the
   intended regex + `_worktree_is_live` fallback. This is a real regression relative to the pre-#774 code:
   the OLD orphan check (`.git`-isfile) cannot decode-fail, so an orphaned-worktree write was blocked
   regardless of any path's encoding; the NEW code runs `git worktree list` first, unconditionally, moving
   orphan detection behind a subprocess whose decode error silently drops the block. Fixed by adding
   `ValueError` to both except-tuples, matching the sibling hook.
2. **The performance claim in Consequences was imprecise for the orphan-reject path** — corrected above
   rather than in a separate bullet, since it's a documentation-precision fix, not a behavior change.
3. **`_resolve_worktree_scope`'s canonical-mismatch check ran before, not after, a direct worktree-root
   membership check — a latent path-form-divergence gap.** The original ordering treated ANY textual
   disagreement between git's canonical entry and the regex's `regex_canonical_root` guess as proof "cwd
   was never inside a worktree" (the gap (b) outcome). But the identical disagreement is also produced by
   benign path-form divergence — a symlink/junction/8.3-short-path component making git's canonicalized
   path differ in FORM (not substance) from the regex's raw cwd-derived substring — on a **genuine**
   worktree or orphan, silently dropping both the orphan-block and the escape-block (this hook's core
   purpose) for that cwd. Not reproduced on the dev machine (both git commands emit identical forms there)
   and fail-open in direction, but a real, if narrow, regression this PR introduced exposure to (the
   pre-#774 regex-only code never asked git to independently agree on a path form at all). Fixed by
   checking `regex_worktree_root` against the FULL confirmed list (canonical entry included) FIRST: a
   direct match there is git's strongest possible confirmation of a live, registered worktree, and is now
   trusted even when the canonical-root guess would have disagreed on form. The canonical-comparison
   heuristic remains, narrowed to the one case a direct worktree-root match can't resolve on its own:
   distinguishing a genuine orphan (canonical confirmed correct, this specific path just isn't registered)
   from gap (b) (the canonical guess itself was wrong).

A fourth, shared finding — `parse_worktree_porcelain(result.stdout)` sitting outside the `try` in both
hooks' new resolvers — is documented once, in [ADR-071 Amendment
6](071-canonical-checkout-mutate-guard-hook.md#review-hardening-dev-env774pr783)'s own Review hardening
section, since the fix (move the parse call inside the same `try`) is identical in both files.

`claude/scripts/tests/test_worktree_path_check.py`'s existing 14 tests were re-run against all three fixes
and pass unchanged — none of them were coupled to the specific ordering or except-tuple gaps corrected here,
so this is a robustness hardening pass with no test-count change.

---

## Addendum (2026-07-22): the block message's recovery recipe was wrong, and is now single-sourced (dev-env#862)

### Problem

The 2026-06-06 addendum above specifies that a non-live worktree exits 2 "with a `{"reason": ...}` message
naming the worktree and cwd and giving the recovery recipe `git worktree add --force <worktree_root>
<branch>`. That recipe does not work.

[dev-env#751](https://github.com/brownm09/dev-env/issues/751) established this in July and corrected the
`docs/REFERENCE.md` runbook — but not this hook's inline message, which is the surface that actually
matters: the guard blocks `Write`/`Edit`/`NotebookEdit` from the orphaned cwd, so the blocked session has
nothing else to act on. The disproven recipe stayed live here for another six weeks and was hit again on
2026-07-22 (career-playbook #823 / PR #826).

### Extended decision

The recipe moves out of this hook entirely into `claude/scripts/_worktree_recovery.py`, and the hook
renders it. The corrected sequence leads with `git worktree repair` (non-destructive — it preserves
uncommitted work, which the old re-create-the-worktree recipe silently discarded), then `prune` → plain
`add`, and empties the directory *in place* only if `add` reports `already exists`. `--force` is dropped:
git dies on a non-empty target before it ever consults the flag.

Full rationale, the throwaway-fixture evidence table (including that `worktree repair` **exits 1 while
succeeding**), and the parity gate that keeps the hook message and the runbook from drifting again:
**[ADR-116](116-single-source-worktree-recovery-recipe.md)**. The recipe text quoted in the 2026-06-06
addendum is left in place as a historical record of what was believed then — ADR-116 supersedes it.

Nothing else about this hook changes: the liveness signals, the block placement before path-scoping, the
exit-2-on-stderr channel (dev-env#469), and the fail-**open**-on-crash direction are all untouched.

---

## Addendum (2026-08-15): engineering-journal canonical-root carve-out (dev-env#750, reopened)

### Problem

The 2026-07-14 sibling-worktree carve-out above (step 5a) only exempts a write whose TARGET path
itself matches the worktree-shaped regex — i.e., the target is *another worktree* under the same
canonical root. It does not exempt a write whose target is the **bare canonical root itself**, with
no worktree segment anywhere in the path.

That bare-canonical-root shape is not an edge case — it is the primary, documented write pattern of
the engineering-journal (EJ) Stub file workflow (`claude/CLAUDE.md` → Engineering Journal → "Never
create a dedicated worktree to write a stub — always operate directly on the canonical via `-C`"):
every Write/Edit to a stub, manifest shard, open-PR shard, or tile shard targets
`C:/Users/brown/Git/engineering-journal/sessions/<project>/<file>` directly, with no worktree
segment in the path. When the session's own primary repo happens to be an EJ worktree (a
`spawn_task` tile working in a different project still writes its journal stub via `-C` per the
Stub file workflow's own cross-repo rule), this hook still blocks that write as "escaping to
canonical root" — the exact scenario dev-env#750 originally reported and that PR #756 (the sibling-
worktree addendum above) did not actually fix, since it only covers a differently-shaped target.

Reproduced again 2026-08-15 from worktree `elegant-matsumoto-22c80c`, prompting the issue's
reopening.

### Extended decision

Insert a new step immediately after the canonical/worktree/file path normalization, before step 5
(the escape-scoping check):

**4a. If the session's own resolved `canonical_root` exactly matches the configured
engineering-journal path → exit 0 (no-op), regardless of the write target.**

Implementation: a module-level `_JOURNAL_ROOT` constant, computed once via this file's own
`_normalize()` helper against `os.environ.get("WORKTREE_PATH_CHECK_JOURNAL_PATH",
"C:/Users/brown/Git/engineering-journal")`. `main()` compares `canonical_norm == _JOURNAL_ROOT`
right after computing `canonical_norm`/`worktree_norm`/`file_norm`, before the step-5 in-scope
check.

This directly mirrors `pre-tool-use-canonical-mutate-guard.py`'s existing, permanent
`_REDIRECT_TARGET_ALLOWLIST` carve-out for the same repo — that hook already exempts a Bash-level
`git -C <journal>` redirect targeting this exact path, for this exact reason (ADR-071). This
addendum brings the file-tool hook into parity with the Bash-tool hook.

### Judgment calls (addendum)

**Additive to, not a replacement for, the sibling-worktree carve-out.** The 2026-07-14 addendum's
step 5a still matters for a session whose canonical root is a *different* repo entirely (e.g. a
dev-env worktree writing into an EJ sibling worktree's own isolated tree) — a case this new,
narrower, exact-canonical-root check does not cover, since it only fires when the *session's own*
canonical root is the journal. Both carve-outs coexist; neither subsumes the other.

**Matched by exact resolved canonical root, not basename** (same reasoning as
`_REDIRECT_TARGET_ALLOWLIST`'s dev-env#576/PR#584 review-finding hardening): a canonical checkout
that merely happens to be named `engineering-journal` at some other path must not be wrongly
exempted. Pinned by `test_main_blocks_write_to_samename_repo_outside_journal_carveout_path`.

**Reuse this file's own `_normalize()` rather than duplicate the mutate-guard's separate
normalization.** `_REDIRECT_TARGET_ALLOWLIST` normalizes via `.replace("\\","/").rstrip("/").lower()`
directly; this hook already has `_normalize()` (`os.path.normcase(os.path.normpath(...))`) computing
`canonical_norm`/`worktree_norm`/`file_norm` throughout `main()`, so `_JOURNAL_ROOT` uses the same
helper for internal consistency rather than introducing a second normalization scheme in the same
file.

**No basename-level exemption, no shared cross-file module.** At least four other hooks already
compare a resolved path against the EJ canonical path this same way — each with its own
module-level constant and its own env-var override, rather than a shared module:
`pre-tool-use-canonical-mutate-guard.py`'s `_REDIRECT_TARGET_ALLOWLIST` /
`CANONICAL_MUTATE_GUARD_JOURNAL_PATH`, `journal-canonical-guard.py`'s `JOURNAL_REPO` /
`JOURNAL_CANONICAL_GUARD_REPO_PATH`, and `pre-tool-use-journal-draft-worktree-guard.py`'s
`JOURNAL_REPO` / `JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH`. This addendum's `_JOURNAL_ROOT` /
`WORKTREE_PATH_CHECK_JOURNAL_PATH` follows the same pattern rather than introducing a shared module
this codebase has consistently declined to extract for this (see ADR-105's "Judgment calls" for the
same duplicate-over-shared-module reasoning applied to a different pair of hooks' git-parsing
logic). This keeps each hook's test suite able to redirect its own carve-out at a disposable temp
dir independently of the others.

### Consequences (addendum)

- `test_main_allows_write_to_engineering_journal_canonical_root` and
  `test_main_blocks_write_to_samename_repo_outside_journal_carveout_path` added to
  `claude/scripts/tests/test_worktree_path_check.py` — 14 tests grows to 16.
- No performance impact: one additional exact-string comparison against an already-normalized path,
  computed once at import time.
- `docs/REFERENCE.md`'s hook-table row and `README.md`'s two hook-description mentions extended
  with a "(for case d)" / equivalent clause alongside the existing sibling-worktree mention.

---

## References

- `claude/scripts/pre-tool-use-worktree-path-check.py` — implementation
- `claude/scripts/_worktree_recovery.py` — the single-sourced recovery recipe (ADR-116, dev-env#862)
- `claude/scripts/tests/test_worktree_recovery.py` — recipe unit tests + runbook-parity gate (Testing item 78)
- `claude/scripts/tests/test_worktree_path_check.py` — self-test (addendum)
- `claude/settings.json` — hook wiring
- `brownm09/career-playbook#276` — downstream symptom tracker (original)
- `brownm09/dev-env#328` — orphaned-worktree hardening (addendum)
- `brownm09/dev-env#469` — stdout→stderr block-reason fix (both sites), `_block()` helper introduced
- `brownm09/dev-env#750` — sibling-worktree carve-out (2026-07-14 addendum); reopened 2026-08-15
  when the sibling-worktree carve-out proved not to cover the bare-canonical-root write shape,
  fixed by the 2026-08-15 addendum above
- `brownm09/dev-env#760` — sibling-directory worktree convention recognition (this addendum)
- `brownm09/dev-env#774` — git-worktree-list-based confirmation, gap (b) (this addendum)
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) — the sibling `pre-tool-use-canonical-
  mutate-guard.py` hook's own permanent `_REDIRECT_TARGET_ALLOWLIST` engineering-journal carve-out
  (dev-env#576, corrected dev-env#747), the precedent the 2026-08-15 addendum above mirrors
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) Amendment 5 — the same gap and fix in the
  sibling `pre-tool-use-canonical-mutate-guard.py` hook, fixed in the same PR
- [ADR-071](071-canonical-checkout-mutate-guard-hook.md) Amendment 6 — the sibling hook's own gap (a) fix,
  fixed in the same PR as this addendum, and the origin of the shared `find_worktree_by_path()` primitive
  this addendum builds on
- `brownm09/dev-env#510` (open) — tracks consolidating the duplicated `_WORKTREE_RE`-family regex copies
  onto a shared module; worth coordinating with, since `find_worktree_by_path()`/`_worktree_topology.py`
  are a natural home for that consolidation, though this addendum does not attempt it
- Engineering-journal `sessions/career-playbook/2026-05-22_140307.stub.md` — third occurrence
- Engineering-journal `sessions/career-playbook/2026-06-06_105718.stub.md` — orphaned-worktree incident
- Engineering-journal `sessions/meta/2026-07-13_070949.stub.md` — compose-session incident (dev-env#750)
- [Claude Code Hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks) — hook exit codes and JSON output format
