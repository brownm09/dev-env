#!/usr/bin/env python3
"""Claude Code PostToolUse hook — after a successful `gh pr merge`, emit a
blocking reminder to spawn follow-up tiles via the spawn_task tool.

The CLAUDE.md "Capture post-merge follow-ups as tiles" rule (ADR-046) has no
automated enforcement — without this hook it is crowded out by the journal/board
cleanup sequence that runs immediately after merge. This hook provides the same
enforcement model as pr-merge-reminder.py: fires on every successful merge and
blocks until acknowledged.

Also fires for the PowerShell tool (dev-env#763): registered under both the
Bash and PowerShell PostToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `gh pr merge` in this environment.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Output is read via _hookio.read_command_output (stdout+stderr, legacy output
fallback) per ADR-049/ADR-050 — do NOT read tool_response.output directly.

Exit 0 — not a successful `gh pr merge` call (including a genuinely-failed
         merge the live `gh pr view` fallback also could not confirm); no action.
Exit 2 — successful merge detected; tile-checkpoint reminder emitted via
         _hookout.emit_block (exit-2 stderr, ASCII-sanitized per ADR-103).
"""
import json
import re
import sys

import _hookout
from _hookio import (
    confirm_merge_via_gh,
    effective_merge_dir,
    is_merge_help_only,
    is_rest_merge_command,
    output_has_merge_marker,
    output_has_rest_merge_marker,
    read_command,
    read_command_output,
    read_cwd,
    read_exit_code,
    scan_top_level,
    should_confirm_via_gh,
)
import _hookutil

# Anchored top-level match — mirrors usage-snapshot.py / pr-merge-reminder.py /
# post-pr-merge-project.py's identical _check_merge_stmt (ADR-050 Amendments 5/6).
_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_RE.match(token.lstrip()))


def is_successful_merge(command: str, output: str) -> bool:
    """Pure predicate: did this Bash call complete a `gh pr merge`?

    Gated on gh's success marker alone, not the exit code. Mirrors
    post-pr-merge-reclaim.py and post-pr-merge-pull.py: worktree merges exit
    non-zero on local cleanup even when the remote merge succeeded (issue
    #275), while a clean exit 0 is also true for non-merge invocations like
    `gh pr merge --help` or a queued `--auto` — an exit-0-alone gate misfired
    on exactly that shape (dev-env#485).

    The command-shape check itself is `scan_top_level`-anchored rather than a
    raw substring test, so `gh pr merge` text inside a heredoc body, a quoted
    argument, or a `$()` subshell no longer counts as an invocation — matching
    the pattern already used in usage-snapshot.py / pr-merge-reminder.py /
    post-pr-merge-project.py (dev-env#529, ADR-050 Amendment 9).

    Also recognizes the two-step REST merge fallback (`gh api -X PUT
    .../pulls/<N>/merge`, dev-env#986) — see usage-snapshot.py's
    merge_confirmed() for the full rationale.
    """
    if scan_top_level(command, _check_merge_stmt) and output_has_merge_marker(output):
        return True
    return is_rest_merge_command(command) and output_has_rest_merge_marker(output)


def main() -> None:
    _hookutil.record_heartbeat("post-merge-tile-checkpoint")
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
    # __main__ safe-exit guard below (which loses only this reminder, an
    # advisory side effect with other backstops -- see ADR-050 Amendment 27
    # for why pre-merge-findings-gate.py, a blocking merge gate, was fixed
    # first and separately on fail-open severity grounds).
    command = read_command(data)
    output = read_command_output(data)
    cwd = read_cwd(data)

    if not is_successful_merge(command, output):
        # gh's marker does not always survive to this hook's captured output
        # when gh exits abruptly right after a worktree's local-cleanup
        # failure (dev-env#489) — fall back to a live `gh pr view` confirmation
        # rather than silently dropping the tile-checkpoint reminder (dev-env#504).
        if not scan_top_level(command, _check_merge_stmt):
            sys.exit(0)
        # `gh pr merge --help` (or any other non-mutating gh pr merge invocation
        # that prints no marker) can categorically never attempt a real merge —
        # treat it exactly like "not a merge command at all" rather than paying
        # a live gh pr view confirmation that resolves against cwd's current
        # branch and can misattribute an unrelated already-merged PR (dev-env#557).
        if is_merge_help_only(command):
            sys.exit(0)
        exit_code = read_exit_code(data, default=-1)
        if not should_confirm_via_gh(exit_code, output):
            sys.exit(0)
        if confirm_merge_via_gh(None, "", effective_merge_dir(command, cwd)) is None:
            sys.exit(0)

    _hookout.emit_block(
        "[tile-checkpoint] PR merged - spawn follow-up tiles now via spawn_task for "
        "any out-of-scope fixes, deferred work, or ideas surfaced during this session. "
        "This includes the next not-yet-started unit of a multi-PR initiative (ADR-059) "
        "if one exists -- do not convert it into a scheduling/permission question back "
        "to the user ('let me know if you want me to start it now'); tile it the same "
        "as any other follow-up. Only an explicit 'skip tiles' user instruction exempts "
        "this checkpoint."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
