#!/usr/bin/env python3
"""Claude Code PostToolUse hook — after 'gh pr merge', fast-forward the local
main branch of the affected repo so the local clone stays current.

Uses `git fetch origin main:main` to update the local main ref without requiring
a checkout — except when the repo's canonical checkout is itself on `main` (e.g.
dev-env's own canonical, which must always stay on `main` per its symlink
architecture, CLAUDE.md -> Dev-Env Architecture): git refuses that fetch
('refusing to fetch into branch ... checked out'), so a plain `pull --ff-only`
is used there instead (dev-env#488).

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

from _hookio import effective_merge_dir, output_has_merge_marker, read_command_output
from _worktree_topology import canonical_on_main, merge_park_target, parse_worktree_porcelain

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
    """Return 'owner/repo' for the merged PR, or None.

    Resolution order (ADR-067):
    1. ``--repo owner/repo`` flag — explicit, highest confidence.
    2. GitHub PR URL in the command string — e.g. ``gh pr merge
       https://github.com/owner/repo/pull/N``.  Pure parse, no subprocess.
    3. ``cd <path> && gh pr merge`` chain: run git-remote on <path> so a
       cross-repo merge correctly identifies the other repo, not cwd's repo.
    4. Bare fallback: git-remote on cwd (pre-ADR-067 behaviour — still correct
       when the merge was run directly from the target repo's cwd).
    """
    m = re.search(r"--repo\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", command)
    if m:
        return m.group(1)

    # GitHub PR URL in the command (e.g. `gh pr merge https://…/pull/N`)
    m2 = re.search(
        r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)/pull/\d+",
        command,
    )
    if m2:
        return m2.group(1)

    # cd-chain scoping: a `cd /other/repo && gh pr merge` should query that
    # repo's remote, not cwd's (the cross-repo incident from the #442 session).
    effective_dir = effective_merge_dir(command, cwd)
    try:
        result = subprocess.run(
            ["git", "-C", effective_dir, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # https://github.com/owner/repo(.git)
            m3 = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", url)
            if m3:
                return m3.group(1)
    except Exception:
        pass

    return None


def list_worktrees(local_path: str) -> list[dict]:
    """Return `git worktree list --porcelain` for *local_path*'s repo, parsed; [] on failure.

    Shared by pull_main's on-main detection and park_worktree_off_main's squatter
    detection so a merge event runs `git worktree list` once, not twice.
    """
    try:
        wt = subprocess.run(
            ["git", "-C", local_path, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    return parse_worktree_porcelain(wt.stdout) if wt.returncode == 0 else []


def pull_command(local_path: str, on_main: bool) -> list[str]:
    """Pure: the git invocation that fast-forwards local main from origin.

    `git fetch origin main:main` fails ('refusing to fetch into branch
    "refs/heads/main" checked out at ...') whenever `main` is the branch currently
    checked out at *local_path* — always true for dev-env's canonical (CLAUDE.md ->
    Dev-Env Architecture requires it stay on `main`; its tree is symlinked into
    ~/.claude/). Use a plain `pull --ff-only` there instead. Otherwise (a feature
    branch is checked out — the case the fetch-into-ref trick was written for,
    issue #275) the fetch updates main without disturbing the current checkout,
    unchanged.
    """
    if on_main:
        return ["git", "-C", local_path, "pull", "--ff-only", "origin", "main"]
    return ["git", "-C", local_path, "fetch", "origin", "main:main"]


def pull_main(local_path: str, repo: str, on_main: bool) -> None:
    """Fast-forward local main from origin (see pull_command for command choice)."""
    cmd = pull_command(local_path, on_main)
    kind = "pull" if on_main else "fetch"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            parts = [p for p in (result.stdout.strip(), result.stderr.strip()) if p]
            detail = "\n".join(parts) or "already up to date"
            print(
                f"[post-merge-pull] {repo}: local main updated — {detail}",
                file=sys.stderr,
            )
        else:
            err = (result.stderr or result.stdout).strip()
            print(
                f"[post-merge-pull] {repo}: git {kind} failed — {err}",
                file=sys.stderr,
            )
    except subprocess.TimeoutExpired:
        print(
            f"[post-merge-pull] {repo}: git {kind} timed out",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"[post-merge-pull] {repo}: unexpected error — {exc}",
            file=sys.stderr,
        )


def park_worktree_off_main(cwd: str, worktrees: list[dict]) -> None:
    """If `gh pr merge --delete-branch` left this worktree squatting main, park it off.

    gh deletes the merged local branch and checks out the default branch; from a worktree
    that checkout only succeeds when the canonical had freed the main ref (it was off main),
    so the worktree grabs main and blocks every other worktree's local post-merge checkout.
    Recreate the worktree's own claude/<slug> branch at HEAD to free main again — this acts
    on the hook's OWN just-merged session worktree (cwd), so no ADR-051 liveness check is
    needed. Non-destructive: `git checkout -b` changes no working-tree files (dev-env#396,
    ADR-058).

    `merge_park_target` is fed the *merged repo's* worktree list (shared with pull_main's
    on-main detection via `list_worktrees` — one `git worktree list` call per merge event),
    so it parks only when `cwd` is genuinely a worktree of that repo on main — a `gh pr merge
    --repo X` run from an unrelated checkout never touches that other repo (correctness review).
    """
    if not cwd:
        return
    park = merge_park_target(cwd, worktrees)
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


def is_successful_merge(command: str, output: str) -> bool:
    """Pure predicate: did this Bash call complete a `gh pr merge`?

    Gated on gh's success marker alone, not the exit code: a worktree exits
    non-zero because local cleanup (`git checkout main`, branch delete) fails
    even though the remote merge succeeded (issue #275; mirrors
    post-pr-merge-reclaim.py), while a clean exit 0 is also true for non-merge
    invocations like `gh pr merge --help` or a queued `--auto` — an
    exit-0-alone gate misfired on exactly that shape (dev-env#485). The output
    is read via the shared `read_command_output` helper — reading the legacy
    `output` field left this fallback dead because the real payload carries
    `stdout`/`stderr` (#380).
    """
    if "gh pr merge" not in command:
        return False
    return output_has_merge_marker(output)


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

    worktrees = list_worktrees(local_path)
    pull_main(local_path, repo, canonical_on_main(worktrees))
    park_worktree_off_main(cwd, worktrees)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an informational hook must never block Claude.
        sys.exit(0)
