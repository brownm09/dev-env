#!/usr/bin/env python3
"""
UserPromptSubmit hook: detect when the active project has no engineering-journal home.

Fires once per session. If the current git repo has no corresponding sessions/<repo>/
directory in engineering-journal, emits a one-line advisory pointing to /journal-onboard.

Exit 0 always — never blocks the user's prompt.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import subprocess
import sys
from pathlib import Path

JOURNAL_SESSIONS = Path.home() / "Git" / "engineering-journal" / "sessions"
SCRATCH = Path.home() / ".claude" / "scratch"


def get_repo_name(cwd: str) -> str | None:
    """Return the git repo name for cwd, handling worktrees correctly."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        git_common_dir = result.stdout.strip()
        # --git-common-dir returns a path like /path/to/repo/.git
        # Parent of that is the repo root; its name is the repo name.
        git_path = Path(git_common_dir)
        if not git_path.is_absolute():
            git_path = Path(cwd) / git_path
        repo_root = git_path.parent.resolve()
        return repo_root.name
    except Exception:
        return None


def main() -> None:
    raw = ""
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        pass
    hook_data = json.loads(raw) if raw else {}
    session_id = hook_data.get("session_id", "")
    cwd = hook_data.get("cwd", "")

    # Fire once per session regardless of outcome
    if session_id:
        flag_path = SCRATCH / f"journal_onboard_{session_id}.flag"
        if flag_path.exists():
            sys.exit(0)
        try:
            SCRATCH.mkdir(parents=True, exist_ok=True)
            flag_path.touch()
        except Exception:
            pass

    if not cwd:
        sys.exit(0)

    repo_name = get_repo_name(cwd)
    if not repo_name:
        sys.exit(0)

    # Skip engineering-journal itself
    if repo_name == "engineering-journal":
        sys.exit(0)

    journal_home = JOURNAL_SESSIONS / repo_name
    if not journal_home.exists():
        print(
            f"[journal-onboard] No journal home found for `{repo_name}`.\n"
            f"Run `/journal-onboard` to scaffold `sessions/{repo_name}/` in engineering-journal."
        )

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
