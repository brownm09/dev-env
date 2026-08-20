#!/usr/bin/env python3
"""Claude Code PreToolUse hook — detects 'git commit' in Bash commands and
emits a systemMessage showing the current branch as a visible checkpoint.

Does NOT block the commit (exit 0). The message appears in the Claude Code UI
so the user can catch wrong-branch commits before they land.

Also appends a drift warning (dev-env#573) when the repo/branch recorded by
post-tool-use-cwd-track.py after the session's last Bash call differs from
the repo/branch at commit time — a signal that the session's tracked cwd may
have silently reverted (e.g. after an intermittent Git Bash crash) between
that call and this one. Advisory only; never blocks.

Also fires for the PowerShell tool (dev-env#620): registered under both the
Bash and PowerShell PreToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `git commit` in this environment.

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
from _hookio import read_command, read_cwd
import _hookutil

# Matches `git commit` as an actual command invocation, not inside a string or
# after --message / -m (where "commit" would be a flag argument value). The
# trailing `{` alternative (dev-env#620) catches PowerShell's documented
# `A; if ($?) { git commit ... }` conditional-chain idiom (no && in PS 5.1) —
# and the equivalent bash brace-group `{ git commit ...; }` — both of which
# otherwise put "git commit" right after an unrecognized anchor character.
_GIT_COMMIT_RE = re.compile(
    r"(?:^|&&|\|\||;|\n|\{)\s*(?:cd\s+\S+\s+&&\s+)?git\s+commit\b"
)


def is_git_commit_command(command: str) -> bool:
    return bool(_GIT_COMMIT_RE.search(command))


def build_message(branch: str | None, drift_warning: str | None) -> str:
    display_branch = branch if branch is not None else "<detached HEAD or unknown>"
    message = f"[branch-check] committing to: {display_branch}"
    if drift_warning:
        message += "\n" + drift_warning
    return message


def main() -> None:
    _hookutil.record_heartbeat("pre-commit-branch-check")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        # A valid-JSON-but-non-dict top-level payload (a list, string, number,
        # or null) would otherwise crash the very next line (dev-env#1031/
        # #1033, mirroring usage-snapshot.py's dev-env#1028 post-review fix).
        sys.exit(0)

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    # dev-env#1031/#1033: read_command()/read_cwd() never raise on a
    # present-but-non-dict tool_input/cwd (dev-env#1028's payload shape) --
    # the pre-fix unguarded chains crashed here, silently caught by the
    # __main__ safe-exit guard below (which loses only this advisory
    # branch-checkpoint message -- see ADR-050 Amendment 27 for why
    # pre-merge-findings-gate.py, a blocking merge gate, was fixed first and
    # separately on fail-open severity grounds).
    command = read_command(data)
    if not is_git_commit_command(command):
        sys.exit(0)

    cwd = read_cwd(data)
    session_id = data.get("session_id", "") or ""
    _, branch, drift_warning = _bash_state.drift_warning_for(session_id, cwd)

    print(json.dumps({"systemMessage": build_message(branch, drift_warning)}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
