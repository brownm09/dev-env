# ADR-024: PreToolUse Hook to Block Canonical-Root Writes from Worktrees

**Date:** 2026-05-23
**Status:** Accepted
**Tags:** hooks, worktrees, pre-tool-use, file-safety, write, edit

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
- **ADR warranted** because the hook is a new file under `claude/scripts/`, is wired in `claude/settings.json`, and establishes a harness-level safety invariant applicable to all repos using Claude-managed worktrees.

---

## References

- `claude/scripts/pre-tool-use-worktree-path-check.py` — implementation
- `claude/settings.json` — hook wiring
- `brownm09/career-playbook#276` — downstream symptom tracker
- Engineering-journal `sessions/career-playbook/2026-05-22_140307.stub.md` — third occurrence
- [Claude Code Hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks) — hook exit codes and JSON output format
