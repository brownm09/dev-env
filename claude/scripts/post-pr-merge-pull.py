#!/usr/bin/env python3
"""Claude Code PostToolUse hook — after 'gh pr merge', fast-forward the local
main branch of the affected repo so the local clone stays current.

Uses `git fetch origin main:main` which updates the local main ref even when
a feature branch is currently checked out.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"output": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 always — informational output only; never blocks Claude.
"""
import json
import os
import re
import subprocess
import sys

# Map GitHub repo slugs to local clone paths.
# Repos with no local clone (e.g. profile-only repos) map to None.
REPO_LOCAL_PATHS: dict[str, str | None] = {
    "brownm09/dev-env":                "C:/Users/brown/Git/dev-env",
    "brownm09/engineering-journal":    "C:/Users/brown/Git/engineering-journal",
    "brownm09/engineering-playbooks":  "C:/Users/brown/Git/engineering-playbooks",
    "brownm09/lifting-logbook":        "C:/Users/brown/Git/lifting-logbook",
    "brownm09/brownm09":               None,
    "brownm09/leadership-playbooks":   None,
}


def extract_repo(command: str, cwd: str) -> str | None:
    """Return 'owner/repo' from --repo flag, or infer from cwd via git remote."""
    m = re.search(r"--repo\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", command)
    if m:
        return m.group(1)

    # Fall back: ask git for the remote URL in cwd
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # https://github.com/owner/repo(.git)
            m2 = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", url)
            if m2:
                return m2.group(1)
    except Exception:
        pass

    return None


def pull_main(local_path: str, repo: str) -> None:
    """Fast-forward local main from origin without requiring a checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", local_path, "fetch", "origin", "main:main"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            detail = result.stderr.strip() or "already up to date"
            print(
                f"[post-merge-pull] {repo}: local main updated — {detail}",
                file=sys.stderr,
            )
        else:
            err = (result.stderr or result.stdout).strip()
            print(
                f"[post-merge-pull] {repo}: git fetch failed — {err}",
                file=sys.stderr,
            )
    except subprocess.TimeoutExpired:
        print(
            f"[post-merge-pull] {repo}: git fetch timed out",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"[post-merge-pull] {repo}: unexpected error — {exc}",
            file=sys.stderr,
        )


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
    cwd = data.get("cwd", "")

    if "gh pr merge" not in command:
        sys.exit(0)

    # Only pull when the merge actually succeeded
    if exit_code != 0:
        sys.exit(0)

    repo = extract_repo(command, cwd)
    if not repo:
        sys.exit(0)

    local_path = REPO_LOCAL_PATHS.get(repo)
    if local_path is None:
        # Repo known but no local clone (e.g. brownm09/brownm09 profile)
        sys.exit(0)

    if not os.path.isdir(local_path):
        print(
            f"[post-merge-pull] {repo}: local path not found ({local_path}) — skipping",
            file=sys.stderr,
        )
        sys.exit(0)

    pull_main(local_path, repo)
    sys.exit(0)


if __name__ == "__main__":
    main()
