#!/usr/bin/env python3
"""Claude Code PreToolUse hook — general cwd/branch drift check on every Bash
call, gated by elapsed time since the last recorded Bash state.

dev-env#682: ADR-085's three checkpoint hooks (pre-commit-branch-check.py,
pre-pr-create-check.py, pre-merge-branch-check.py) only compare recorded vs.
current repo/branch state at git-commit / gh-pr-create / gh-pr-merge moments.
A silent cwd/branch revert affecting any OTHER Bash command (a grep, a test
runner, a build) goes completely unflagged by those three. This hook extends
the same _bash_state.py-backed comparison to every Bash call — but only pays
the git subprocess cost when at least MIN_GAP_SECONDS have elapsed since the
last recorded call (a cheap file-mtime stat gates it otherwise), since the
observed trigger (dev-env#682: drift noticed immediately after two ~5-7
minute background Agent calls) is gap-shaped, not adjacent-call-shaped. Back-
to-back Bash calls — the overwhelming majority in any session — skip the
subprocess entirely.

Advisory only, matching ADR-085's other three checkpoints: this mechanism
cannot distinguish a legitimate EnterWorktree/cd switch from a silent
crash-induced revert (see ADR-085 Judgment calls) — never blocks.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", ...},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 — always; hook is advisory only.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import sys

import _bash_state

# Margin-of-safety default, not fit to the one observed 5-7 min incident — a
# smaller threshold only costs more `git rev-parse` calls, never more false
# positives (format_drift_warning still no-ops on "no repo change" regardless
# of how eagerly this gate fires).
MIN_GAP_SECONDS = 60.0


def should_check_drift(age_seconds: float | None, min_gap: float) -> bool:
    """Pure: True iff enough time has elapsed to justify the subprocess cost.

    ``None`` (no prior state file yet — first Bash call of the session) and
    any age at or below *min_gap* (including a negative age from a
    future/skewed mtime) both return False — the boundary belongs to the
    cheaper "skip" side, matching disk-space-check.py's classify_free_space
    convention."""
    if age_seconds is None:
        return False
    return age_seconds > min_gap


def build_message(drift_warning: str) -> str:
    return f"[bash-drift-check] {drift_warning}"


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

    session_id = data.get("session_id", "") or ""
    cwd = data.get("cwd", "") or ""
    if not session_id or not cwd:
        sys.exit(0)

    age = _bash_state.state_age_seconds(session_id)
    if not should_check_drift(age, MIN_GAP_SECONDS):
        sys.exit(0)

    _, _, warning = _bash_state.drift_warning_for(session_id, cwd)
    if warning:
        print(json.dumps({"systemMessage": build_message(warning)}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: this hook must never block or surface an error.
        sys.exit(0)
