#!/usr/bin/env python3
"""
UserPromptSubmit hook: keep the local dev-env repo in sync with origin/main.

Runs a fast-forward pull at session start so that CLAUDE.md and other
symlinked tooling always reflect the latest merged changes. Silent on
success; emits a warning if the repo has diverged and needs manual attention.

Only acts when the repo has `main` checked out — exits silently when a
feature branch is active so dev-env PR sessions are not spammed with
false-positive divergence warnings.

Exit 0 always — never block the user's prompt.
"""

import subprocess
import sys
from pathlib import Path

DEV_ENV_REPO = Path.home() / "Git" / "dev-env"


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=DEV_ENV_REPO,
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


def main() -> None:
    try:
        sys.stdin.read()
    except Exception:
        pass

    # Guard: repo must exist at the expected path.
    if not DEV_ENV_REPO.is_dir():
        sys.exit(0)

    # Only act when main is checked out — feature branches are intentional.
    branch = run(["git", "symbolic-ref", "--short", "HEAD"])
    if branch.returncode != 0 or branch.stdout.strip() != "main":
        sys.exit(0)

    # Fetch quietly so the local remote-tracking ref is current.
    fetch = run(["git", "fetch", "origin", "main", "--quiet"])
    if fetch.returncode != 0:
        # Network issue — don't block, don't spam on every turn.
        sys.exit(0)

    # Compare local main to origin/main.
    rev_local = run(["git", "rev-parse", "refs/heads/main"])
    rev_remote = run(["git", "rev-parse", "origin/main"])
    if rev_local.returncode != 0 or rev_remote.returncode != 0:
        sys.exit(0)

    local = rev_local.stdout.strip()
    remote = rev_remote.stdout.strip()

    if local == remote:
        # Already up-to-date.
        sys.exit(0)

    # Check if local main is an ancestor of origin/main (fast-forward possible).
    merge_base = run(["git", "merge-base", "refs/heads/main", "origin/main"])
    if merge_base.returncode != 0:
        sys.exit(0)

    base = merge_base.stdout.strip()

    if base != local:
        # Local main has commits not on origin/main — diverged.
        print(
            "[dev-env-sync] WARNING: local dev-env repo has diverged from origin/main.\n"
            "CLAUDE.md and symlinked tooling may be stale. Run `git -C ~/Git/dev-env "
            "status` to investigate before proceeding."
        )
        sys.exit(0)

    # Fast-forward is safe — pull.
    pull = run(["git", "pull", "--ff-only", "origin", "main"])
    if pull.returncode == 0:
        # Count how many commits were pulled.
        log = run(["git", "log", "--oneline", f"{local}..HEAD"])
        lines = [line for line in log.stdout.strip().splitlines() if line]
        count = len(lines)
        summary = f"{count} commit{'s' if count != 1 else ''}"
        shown = lines[:5]
        trailer = f"  ... and {count - 5} more" if count > 5 else ""
        print(
            f"[dev-env-sync] Pulled {summary} from origin/main — CLAUDE.md and tooling are now current.\n"
            + "\n".join(f"  {line}" for line in shown)
            + (f"\n{trailer}" if trailer else "")
        )
    else:
        print(
            "[dev-env-sync] WARNING: fast-forward pull failed.\n"
            + pull.stderr.strip()
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
