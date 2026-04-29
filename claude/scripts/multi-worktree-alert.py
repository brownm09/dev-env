#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook — emits a systemMessage listing all
active worktrees when the current repo has two or more, marking the current
one with asterisks.

Fires on every user prompt so the user always knows which worktree they are
working in during a multi-worktree session. Exits silently when only one
worktree is active.

Stdin JSON shape (UserPromptSubmit):
  {
    "hook_event_name": "UserPromptSubmit",
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 always — advisory only, never blocks.
"""
import json
import subprocess
import sys
from pathlib import Path


def parse_worktree_list(output: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output into a list of dicts.

    Each dict has keys:
      path   (str) — absolute path from the 'worktree' line
      branch (str) — short branch name, or '<detached>' for detached-HEAD worktrees
    """
    worktrees: list[dict] = []
    current: dict | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = {"path": line[len("worktree "):].strip(), "branch": ""}
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):].strip()
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached" and current is not None:
            current["branch"] = "<detached>"
    if current is not None:
        worktrees.append(current)
    return worktrees


def find_current_worktree(worktrees: list[dict], cwd: str) -> dict | None:
    """Return the worktree whose path matches cwd or is a parent of cwd.

    Sorts by path length descending so the most-specific (deepest) match wins
    when cwd is a subdirectory inside a worktree.
    """
    cwd_path = Path(cwd).resolve()
    for wt in sorted(worktrees, key=lambda w: len(w["path"]), reverse=True):
        try:
            cwd_path.relative_to(Path(wt["path"]).resolve())
            return wt
        except ValueError:
            continue
    return None


def main() -> None:
    raw = sys.stdin.read().strip()
    cwd = ""
    if raw:
        try:
            cwd = json.loads(raw).get("cwd", "")
        except json.JSONDecodeError:
            pass

    if not cwd:
        sys.exit(0)

    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        sys.exit(0)

    if result.returncode != 0:
        sys.exit(0)

    worktrees = parse_worktree_list(result.stdout)
    if len(worktrees) < 2:
        sys.exit(0)

    current = find_current_worktree(worktrees, cwd)

    labeled = []
    for wt in worktrees:
        name = wt["branch"] or "<unknown>"
        labeled.append(f"*{name}*" if wt is current else name)

    location = "current unknown" if current is None else f"on *{current['branch'] or '<unknown>'}*"
    msg = f"[worktree-alert] {len(worktrees)} active worktrees ({location}): {', '.join(labeled)}"
    print(json.dumps({"systemMessage": msg}))
    sys.exit(0)


if __name__ == "__main__":
    main()
