# ADR-024: PreToolUse Hook to Block Canonical-Root Writes from Worktrees

**Date:** 2026-05-23 (amended 2026-06-06)
**Status:** Accepted
**Tags:** hooks, worktrees, pre-tool-use, file-safety, write, edit, orphaned-worktree

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

## References

- `claude/scripts/pre-tool-use-worktree-path-check.py` — implementation
- `claude/scripts/tests/test_worktree_path_check.py` — self-test (addendum)
- `claude/settings.json` — hook wiring
- `brownm09/career-playbook#276` — downstream symptom tracker (original)
- `brownm09/dev-env#328` — orphaned-worktree hardening (addendum)
- `brownm09/dev-env#469` — stdout→stderr block-reason fix (both sites), `_block()` helper introduced
- Engineering-journal `sessions/career-playbook/2026-05-22_140307.stub.md` — third occurrence
- Engineering-journal `sessions/career-playbook/2026-06-06_105718.stub.md` — orphaned-worktree incident
- [Claude Code Hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks) — hook exit codes and JSON output format
