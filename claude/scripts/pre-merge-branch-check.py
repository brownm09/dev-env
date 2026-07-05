#!/usr/bin/env python3
"""Claude Code PreToolUse hook — detects 'gh pr merge' in Bash commands and
emits a systemMessage showing the current branch as a visible checkpoint.

Does NOT block the merge (exit 0). Mirrors pre-commit-branch-check.py's
pattern for `git commit`: `gh pr merge` with no explicit PR number also
infers its target from the current checkout, which is exactly the state
that can go stale after a session's tracked cwd silently reverts (e.g. after
an intermittent Git Bash crash) — see dev-env#573.

Also appends a drift warning when the repo/branch recorded by
post-tool-use-cwd-track.py after the session's last Bash call differs from
the repo/branch at merge time. Advisory only; never blocks.

Merge detection reuses `_hookio.scan_top_level` (dev-env#519), the same
quote/subshell/heredoc-aware engine pre-merge-message-check.py and
pre-merge-numbering-check.py already use — not a plain unanchored
`re.search`, which could spuriously fire on a `gh pr merge` mentioned only
inside a heredoc body or `$()` subshell (dev-env#499).

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 — always; hook is advisory only.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import subprocess
import sys

import _bash_state
from _hookio import scan_top_level

_MERGE_STMT_RE = re.compile(r"gh\s+pr\s+merge\b")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_STMT_RE.match(token.lstrip()))


def is_pr_merge_command(command: str) -> bool:
    """True iff *command* contains a top-level `gh pr merge` — i.e. not one
    merely mentioned inside a quoted string, $() subshell, or heredoc body
    (dev-env#499). Mirrors pre-merge-message-check.py's /
    pre-merge-numbering-check.py's identically-named predicate (dev-env#519).
    """
    return scan_top_level(command, _check_merge_stmt)


def current_branch(cwd: str) -> str | None:
    """Return the current branch, or None for a detached HEAD / git failure.

    None (rather than a display placeholder) keeps this apples-to-apples with
    post-tool-use-cwd-track.py's recorded state for the drift comparison.
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=cwd or None, timeout=5,
        )
        branch = result.stdout.strip()
        return branch if result.returncode == 0 and branch else None
    except Exception:
        return None


def current_repo_root(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=cwd or None, timeout=5,
        )
        root = result.stdout.strip()
        return root if result.returncode == 0 and root else None
    except Exception:
        return None


def build_message(branch: str | None, repo_root: str | None, drift_warning: str | None) -> str:
    display_branch = branch if branch is not None else "<detached HEAD or unknown>"
    display_repo = repo_root if repo_root is not None else "<unknown>"
    message = (
        f"[merge-branch-check] merging from: {display_branch} (repo: {display_repo}) — "
        "confirm this is the PR/branch you intend to merge; pass an explicit PR "
        "number to `gh pr merge` if there's any doubt (dev-env#573)."
    )
    if drift_warning:
        message += "\n" + drift_warning
    return message


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not is_pr_merge_command(command):
        sys.exit(0)

    cwd = data.get("cwd", "")
    session_id = data.get("session_id", "") or ""
    branch = current_branch(cwd)
    repo_root = current_repo_root(cwd)

    drift_warning = None
    if session_id:
        recorded = _bash_state.read_state(session_id)
        drift_warning = _bash_state.format_drift_warning(recorded, repo_root, branch, cwd)

    print(json.dumps({"systemMessage": build_message(branch, repo_root, drift_warning)}))
    sys.exit(0)


if __name__ == "__main__":
    main()
