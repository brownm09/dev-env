#!/usr/bin/env python3
"""Claude Code PostToolUse hook — after a successful `gh pr merge`, reclaim the
regenerable disk artifacts of now-idle worktrees.

A merged worktree's branch is merged into origin/main, so its node_modules is both
eligible for reclamation (ADR-037) and regenerable (worktree-npm-install.py / ADR-016
reinstalls on next use). Without this hook those artifacts linger until the 6-hourly
reclaim-worktree-disk routine runs — and per-worktree node_modules is the dominant C:
consumer (dev-env#364: 60 lifting-logbook worktrees × ~1-2 GB). This hook reclaims
them *at the idle event* by spawning reclaim-worktree-disk.py detached.

What this hook does NOT do — and why (dev-env#364):
  Removing the merged worktree's *directory* and *branch* cannot happen from within
  the session that lives in it: Windows holds an OS lock on a process's current
  directory (git worktree remove / rmdir fail with a sharing violation), a branch
  checked out in a worktree cannot be deleted, and `gh pr merge --delete-branch`
  aborts from a worktree on `main is already checked out`. Directory + branch removal
  therefore stays the daily, out-of-process prune-stale-worktrees routine's job. This
  hook reclaims only the heavy regenerable artifacts (a plain file delete, valid even
  for the active worktree via --protect-cwd, which skips it). The reclaim is spawned
  DETACHED so it never blocks or delays the merge.

The detached spawn uses sys.executable (pythonw.exe under the `pyw -3` hook
invocation) rather than the `py` launcher — spawning via `py.exe` would allocate a
fresh console for the grandchild (dev-env#300; same convention as disk-space-check.py).

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "..."},  # NOT "output" — ADR-049
    "cwd": "..."
  }

Exit 0 always — informational only; never blocks Claude.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import subprocess
import sys
from pathlib import Path

from _hookio import (
    confirm_merge_via_gh,
    effective_merge_dir,
    output_has_merge_marker,
    read_command_output,
    should_confirm_via_gh,
)

SCAN_DIR = "C:/Users/brown/Git"
RECLAIM_SCRIPT = Path(__file__).resolve().parent / "reclaim-worktree-disk.py"


def is_successful_merge(command: str, output: str) -> bool:
    """Pure predicate: did this Bash call complete a `gh pr merge`?

    Gated on gh's success marker alone, not the exit code: a worktree exits
    non-zero because local cleanup (`git checkout main`, branch delete) fails
    even though the remote merge succeeded (mirrors post-pr-merge-pull.py /
    issue #275), while a clean exit 0 is also true for non-merge invocations
    like `gh pr merge --help` or a queued `--auto` — an exit-0-alone gate
    misfired on exactly that shape (dev-env#485).
    """
    if "gh pr merge" not in command:
        return False
    return output_has_merge_marker(output)


def _spawn_reclaim(protect_cwd: str) -> bool:
    """Spawn reclaim-worktree-disk.py detached. Returns True if spawned.

    No --min-free-gb: the trigger is the idle event (a merge), not low space, so
    reclamation runs regardless of current free space. --protect-cwd shields the
    active worktree (this session's cwd) from being stripped mid-use.
    """
    exe = sys.executable or "pythonw.exe"
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    args = [exe, str(RECLAIM_SCRIPT), "--scan-dir", SCAN_DIR]
    if protect_cwd:
        args += ["--protect-cwd", protect_cwd]
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        return True
    except OSError:
        return False


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
    output = read_command_output(data)
    cwd = data.get("cwd", "")

    if not is_successful_merge(command, output):
        # gh's marker does not always survive to this hook's captured output
        # when gh exits abruptly right after a worktree's local-cleanup
        # failure (dev-env#489) — fall back to a live `gh pr view` confirmation
        # rather than silently skipping the disk reclaim (dev-env#504).
        if "gh pr merge" not in command:
            sys.exit(0)
        exit_code = data.get("tool_response", {}).get("exitCode", -1)
        if not should_confirm_via_gh(exit_code, output):
            sys.exit(0)
        if confirm_merge_via_gh(None, "", effective_merge_dir(command, cwd)) is None:
            sys.exit(0)

    if _spawn_reclaim(cwd):
        print(
            "[post-merge-reclaim] PR merged — reclaiming node_modules/.turbo from "
            "now-idle worktrees in the background (regenerable on next use).",
            file=sys.stderr,
        )

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an informational hook must never block Claude.
        sys.exit(0)
