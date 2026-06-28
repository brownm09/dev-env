#!/usr/bin/env python3
"""Claude Code PostToolUse hook — after a successful `gh pr merge`, emit a
blocking reminder to spawn follow-up tiles via the spawn_task tool.

The CLAUDE.md "Capture post-merge follow-ups as tiles" rule (ADR-046) has no
automated enforcement — without this hook it is crowded out by the journal/board
cleanup sequence that runs immediately after merge. This hook provides the same
enforcement model as pr-merge-reminder.py: fires on every successful merge and
blocks until acknowledged.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Output is read via _hookio.read_command_output (stdout+stderr, legacy output
fallback) per ADR-049/ADR-050 — do NOT read tool_response.output directly.

Exit 0 — not a successful `gh pr merge` call; no action.
Exit 2 — successful merge detected; tile-checkpoint reminder emitted via stderr.
"""
import json
import sys

from _hookio import output_has_merge_marker, read_command_output


def is_successful_merge(command: str, exit_code: int, output: str) -> bool:
    """Pure predicate: did this Bash call complete a `gh pr merge`?

    Mirrors post-pr-merge-reclaim.py and post-pr-merge-pull.py:
    worktree merges exit non-zero on local cleanup even when the remote merge
    succeeded (issue #275), so the stdout success marker is also checked.
    """
    if "gh pr merge" not in command:
        return False
    return exit_code == 0 or output_has_merge_marker(output)


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
    exit_code = data.get("tool_response", {}).get("exitCode", -1)
    output = read_command_output(data)

    if not is_successful_merge(command, exit_code, output):
        sys.exit(0)

    print(
        "[tile-checkpoint] PR merged — spawn follow-up tiles now via spawn_task for "
        "any out-of-scope fixes, deferred work, or ideas surfaced during this session. "
        "Only an explicit 'skip tiles' user instruction exempts this checkpoint.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
