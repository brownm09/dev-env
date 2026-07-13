#!/usr/bin/env python3
"""Claude Code PreToolUse hook — blocks Write/Edit/NotebookEdit calls whose
file_path targets the canonical repo root when the session is running inside a
Claude-managed worktree.

Problem: Absolute paths like `C:/Users/brown/Git/dev-env/foo.py` resolve to
the main working tree, not the active worktree. Files land in the wrong place
silently. This hook intercepts those calls before the write happens.

Logic:
  1. If cwd does not contain `/.claude/worktrees/<name>/`, pass immediately.
  2. Extract canonical_root (repo root) and worktree_root from cwd.
  3. Liveness guard (ADR-024 addendum, dev-env#328): assert the worktree is a
     *live* registered worktree, not an orphan whose `.git` link is gone. An
     orphaned worktree dir silently resolves every git command up the tree to
     the canonical repo's `.git`, so writes land on the wrong tree or in a
     disconnected directory invisible to git. If `<worktree_root>/.git` is
     missing, or `git -C <cwd> rev-parse --show-toplevel` resolves to anything
     other than worktree_root → exit 2 with the recovery recipe.
  4. Read file_path (Write/Edit) or notebook_path (NotebookEdit) from tool input.
  5. If the path is absolute and starts with canonical_root but NOT with
     worktree_root → exit 2 with a blocking message naming both paths and the
     corrected worktree-relative path.
  6. Otherwise pass (exit 0).

Blocking: exits 2 with JSON {"reason": "..."} — the harness refuses the tool
call and shows the reason to Claude so it can re-issue with the correct path.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Write" | "Edit" | "NotebookEdit",
    "tool_input": {"file_path": "..." | "notebook_path": "..."},
    "session_id": "...",
    "cwd": "..."
  }
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

import _hookutil

# Matches `.claude/worktrees/<name>` anywhere in a path, capturing the repo
# root (everything before `/.claude/`).
_WORKTREE_RE = re.compile(
    r"^(.+?)[/\\]\.claude[/\\]worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)

# Maps tool name → the field in tool_input that holds the file path.
_PATH_FIELD = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}


def _normalize(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _resolve_git_toplevel(cwd: str):
    """Return git's worktree top-level for `cwd`, or None if git can't resolve it.

    For a live worktree this is the worktree root. For an *orphaned* worktree dir
    (no `.git` link file), git walks up the tree and returns the canonical repo
    root instead — that mismatch is the orphan signature the liveness guard keys
    on. Any execution failure (git missing, timeout, non-zero exit) returns None.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


def _worktree_is_live(
    worktree_root: str,
    cwd: str,
    *,
    path_exists=os.path.exists,
    git_toplevel=_resolve_git_toplevel,
) -> bool:
    """True if `worktree_root` is a live registered worktree, not an orphan.

    Two signals, cheapest first:
      1. The `.git` link file must exist at the worktree root. An orphaned dir
         has lost it — this is the documented incident (dev-env#328), caught
         without spawning git.
      2. git's resolved top-level for `cwd` must equal `worktree_root`. This
         catches the subtle case where git mis-resolves up to the canonical repo.

    If git cannot run (returns None) but the `.git` link is present, treat the
    worktree as live — a transient git failure must not block every write when
    the link file clearly exists.
    """
    if not path_exists(os.path.join(worktree_root, ".git")):
        return False
    top = git_toplevel(cwd)
    if top is None:
        return True
    return _normalize(top) == _normalize(worktree_root)


def _block(reason: str) -> None:
    """Emit a blocking {"reason": ...} payload and exit 2.

    Claude Code discards stdout on a PreToolUse hook exit code 2 — only
    stderr is surfaced to the model. Write there, matching the working
    pattern in career-playbook's block-artifact-merge.py /
    block-letter-violations.py. Centralized so main()'s two independent
    block sites can't drift out of sync on this again (dev-env#469).
    """
    sys.stderr.write(json.dumps({"reason": reason}) + "\n")
    sys.exit(2)


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-worktree-path-check")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in _PATH_FIELD:
        sys.exit(0)

    cwd = data.get("cwd", "")
    m = _WORKTREE_RE.match(cwd)
    if not m:
        sys.exit(0)  # not in a worktree — nothing to enforce

    canonical_root = m.group(1)   # e.g. C:/Users/brown/Git/dev-env
    worktree_root = m.group(0)    # e.g. C:/Users/brown/Git/dev-env/.claude/worktrees/dreamy-feistel-e004d7

    # Liveness guard (dev-env#328): an orphaned worktree dir silently resolves
    # git up to the canonical repo, so *any* write from here — relative paths and
    # in-worktree absolute paths included — risks landing on the wrong tree or in
    # a disconnected directory. Check before the path-scoping below so it covers
    # all three cases, not just canonical-root absolute paths.
    if not _worktree_is_live(worktree_root, cwd):
        reason = (
            f"[worktree-path-guard] BLOCKED: {tool_name} issued from an orphaned / "
            f"disconnected worktree. The worktree directory exists but is not a live "
            f"registered worktree (its `.git` link is missing or git resolves to the "
            f"canonical repo), so git silently operates on the CANONICAL repo and writes "
            f"land on the wrong tree or in a directory invisible to git.\n"
            f"\n"
            f"  Worktree : {worktree_root}\n"
            f"  cwd      : {cwd}\n"
            f"\n"
            f"Recover by re-creating the path as a real worktree, then retry:\n"
            f"  git worktree add --force {worktree_root} <branch>\n"
            f"(<branch> is typically claude/<worktree-name>; confirm with `git branch -a`.)"
        )
        _block(reason)

    file_path = data.get("tool_input", {}).get(_PATH_FIELD[tool_name], "")
    if not file_path or not os.path.isabs(file_path):
        sys.exit(0)  # relative paths are fine

    canonical_norm = _normalize(canonical_root)
    worktree_norm = _normalize(worktree_root)
    file_norm = _normalize(file_path)

    # Must start with canonical root (with separator) to be in-scope.
    if not (file_norm == canonical_norm or file_norm.startswith(canonical_norm + os.sep)):
        sys.exit(0)

    # Already inside the worktree — correct.
    if file_norm == worktree_norm or file_norm.startswith(worktree_norm + os.sep):
        sys.exit(0)

    # Path targets the canonical root but not the active worktree — block.
    try:
        rel = os.path.relpath(file_path, canonical_root)
        corrected = os.path.join(worktree_root, rel)
    except ValueError:
        corrected = "<could not compute — use worktree_root + relative path>"

    reason = (
        f"[worktree-path-guard] BLOCKED: {tool_name} targets the canonical repo root, "
        f"not the active worktree. Files written here will land on the main working tree "
        f"and will not be visible from the worktree.\n"
        f"\n"
        f"  Attempted : {file_path}\n"
        f"  Worktree  : {worktree_root}\n"
        f"  Corrected : {corrected}\n"
        f"\n"
        f"Re-issue with the corrected path."
    )
    _block(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
