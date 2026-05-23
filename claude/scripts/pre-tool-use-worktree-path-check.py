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
  3. Read file_path (Write/Edit) or notebook_path (NotebookEdit) from tool input.
  4. If the path is absolute and starts with canonical_root but NOT with
     worktree_root → exit 2 with a blocking message naming both paths and the
     corrected worktree-relative path.
  5. Otherwise pass (exit 0).

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
import json
import os
import re
import sys

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


def main() -> None:
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
    print(json.dumps({"reason": reason}))
    sys.exit(2)


if __name__ == "__main__":
    main()
