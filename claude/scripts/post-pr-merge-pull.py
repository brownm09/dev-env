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
    "tool_response": {"stdout": "...", "stderr": "..."},  # NOT "output" — ADR-049
    "session_id": "...",
    "cwd": "..."
  }

Exit 0 always — informational output only; never blocks Claude.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

from _hookio import output_has_merge_marker, read_command_output
from _worktree_topology import merge_park_target

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


def current_branch(path: str) -> str:
    """Short branch name checked out at `path`, or "" if undeterminable/detached."""
    try:
        r = subprocess.run(
            ["git", "-C", path, "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def park_worktree_off_main(cwd: str, canonical: str) -> None:
    """If `gh pr merge --delete-branch` left this worktree squatting main, park it off.

    gh deletes the merged local branch and checks out the default branch; from a worktree
    that checkout only succeeds when the canonical had freed the main ref (it was off main),
    so the worktree grabs main and blocks every other worktree's local post-merge checkout.
    Recreate the worktree's own claude/<slug> branch at HEAD to free main again — this acts
    on the hook's OWN just-merged session worktree (cwd), so no ADR-051 liveness check is
    needed. Non-destructive: `git checkout -b` changes no working-tree files (dev-env#396,
    ADR-058).
    """
    park = merge_park_target(cwd, canonical, current_branch(cwd))
    if not park:
        return
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "checkout", "-b", park],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        print(f"[post-merge-pull] could not park worktree off main — {exc}", file=sys.stderr)
        return
    if r.returncode == 0:
        print(
            f"[post-merge-pull] parked this worktree off main onto {park} — freed the main ref. "
            "The canonical ~/Git/dev-env is off main; dev-env-sync returns it on the next prompt "
            "if clean (else switch it back manually to refresh ~/.claude/).",
            file=sys.stderr,
        )
    else:
        print(
            f"[post-merge-pull] could not park worktree off main (branch {park} may already exist) "
            f"— {r.stderr.strip()}",
            file=sys.stderr,
        )


def is_successful_merge(command: str, exit_code: int, output: str) -> bool:
    """Pure predicate: did this Bash call complete a `gh pr merge`?

    `gh pr merge` from a worktree exits non-zero because local cleanup
    (`git checkout main`, branch delete) fails even though the remote merge
    succeeded, so the stdout/stderr success markers are trusted too (issue #275;
    mirrors post-pr-merge-reclaim.py). The output is read via the shared
    `read_command_output` helper — reading the legacy `output` field left this
    fallback dead because the real payload carries `stdout`/`stderr` (#380).
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
    cwd = data.get("cwd", "")

    if not is_successful_merge(command, exit_code, output):
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
    park_worktree_off_main(cwd, local_path)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an informational hook must never block Claude.
        sys.exit(0)
