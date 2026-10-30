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

Also fires for the PowerShell tool (dev-env#620): registered under both the
Bash and PowerShell PreToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `gh pr merge` in this environment.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "...", "description": "..."},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 — always; hook is advisory only.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import sys

import _bash_state
from _hookio import is_pr_merge_command, load_hook_input, read_command, read_cwd, SHELL_TOOLS
import _hookutil


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
    _hookutil.record_heartbeat("pre-merge-branch-check")
    data = load_hook_input(SHELL_TOOLS)
    if data is None:
        sys.exit(0)

    # dev-env#1031/#1033: read_command()/read_cwd() never raise on a
    # present-but-non-dict tool_input/cwd (dev-env#1028's payload shape) --
    # the pre-fix unguarded chains crashed here, silently caught by the
    # __main__ safe-exit guard below (which loses only this advisory
    # branch-checkpoint message -- see ADR-050 Amendment 27 for why
    # pre-merge-findings-gate.py, a blocking merge gate, was fixed first and
    # separately on fail-open severity grounds).
    command = read_command(data)
    if not is_pr_merge_command(command):
        sys.exit(0)

    cwd = read_cwd(data)
    session_id = data.get("session_id", "") or ""
    repo_root, branch, drift_warning = _bash_state.drift_warning_for(session_id, cwd)

    print(json.dumps({"systemMessage": build_message(branch, repo_root, drift_warning)}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
