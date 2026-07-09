#!/usr/bin/env python3
"""
UserPromptSubmit hook: keep the local dev-env repo in sync with origin/main.

Runs a fast-forward pull at session start so that CLAUDE.md and other
symlinked tooling always reflect the latest merged changes. Silent on
success; emits a warning if the repo has diverged and needs manual attention.

When the canonical worktree is off `main` (so `~/.claude/` symlinks would serve
that branch's stale files) — including a *detached* HEAD, routed into the same path via
`resolve_current_branch` (dev-env#619) — diagnoses the worktree-on-main topology and either:
  - auto-returns a *clean* canonical to `main`, then continues the fast-forward pull;
  - warns, naming a non-canonical worktree squatting `main` plus its park command,
    when one is blocking the canonical's return (dev-env#396, ADR-058); or
  - warns without switching when the canonical has uncommitted drift (preserved).

Exit 0 always — never block the user's prompt.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import subprocess
import sys
from pathlib import Path

from _worktree_topology import (
    canonical_sync_action,
    diagnose_main_topology,
    parse_worktree_porcelain,
    resolve_current_branch,
)

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

    # The canonical must stay on main — ~/.claude/ symlinks serve its working tree, so a
    # feature branch there hides newly-merged hooks/scripts. When it's off main we diagnose
    # the worktree topology below and auto-correct (clean canonical -> back to main) or warn
    # precisely (squatter holding main, or dirty drift to preserve); see ADR-058.
    #
    # A non-zero returncode means detached HEAD, not "command failed" — resolve_current_branch
    # routes it to the "<detached>" sentinel so it falls into the SAME diagnostic block below
    # instead of exiting silently here. diagnose_main_topology/canonical_sync_action already
    # handle "<detached>" correctly (main_squatter's bare/detached guard); the gap was purely
    # that nothing used to route a detached canonical into them (dev-env#619).
    branch = run(["git", "symbolic-ref", "--short", "HEAD"])
    current_branch = resolve_current_branch(branch.returncode, branch.stdout)
    if current_branch != "main":
        # The canonical is off main — diagnose the worktree topology so we can either
        # auto-return a clean canonical to main (restoring the ~/.claude symlinks) or warn
        # precisely. A non-canonical worktree may be squatting main (gh's --delete-branch
        # checked it out there), which blocks the canonical's return entirely (dev-env#396,
        # ADR-058). This is the rare/broken path, so the extra git calls are cheap.
        wt = run(["git", "worktree", "list", "--porcelain"])
        if wt.returncode != 0:
            # Topology undeterminable — don't auto-correct on incomplete data (a silent []
            # would misdiagnose as "dirty drift", review finding). Emit the plain off-main
            # warning and let the user switch back manually.
            print(
                f"[dev-env-sync] ⚠️  Canonical worktree is on '{current_branch}' and its worktree "
                "list could not be read — ~/.claude/ symlinks may serve stale hooks/scripts.\n"
                f"Switch it back manually: git -C {DEV_ENV_REPO} checkout main",
                file=sys.stderr,
            )
            sys.exit(0)
        worktrees = parse_worktree_porcelain(wt.stdout)
        topo = diagnose_main_topology(worktrees)
        status = run(["git", "status", "--porcelain"])
        canonical_clean = status.returncode == 0 and not status.stdout.strip()
        action = canonical_sync_action(topo, canonical_clean)

        if action.kind == "warn-squatter":
            print(
                f"[dev-env-sync] ⚠️  Canonical worktree is on '{current_branch}' and worktree\n"
                f"  {action.squatter_path}\n"
                "is squatting 'main' — the canonical cannot return until that worktree is parked\n"
                "off main. Free the ref (non-destructive — changes no files):\n"
                f"  git -C {action.squatter_path} checkout -b {action.park_branch}\n"
                "then the next prompt returns the canonical to main automatically (or run\n"
                f"  git -C {DEV_ENV_REPO} checkout main\n"
                "). Until then ~/.claude/ symlinks may serve stale hooks/scripts.",
                file=sys.stderr,
            )
            sys.exit(0)

        if action.kind == "warn-dirty":
            print(
                f"[dev-env-sync] ⚠️  Canonical worktree is on '{current_branch}' with uncommitted\n"
                "changes — ~/.claude/ symlinks may serve stale hooks/scripts. Not auto-switching to\n"
                "preserve your drift; commit or stash, then:\n"
                f"  git -C {DEV_ENV_REPO} checkout main",
                file=sys.stderr,
            )
            sys.exit(0)

        if action.kind == "return-canonical":
            checkout = run(["git", "checkout", "main"])
            if checkout.returncode != 0:
                print(
                    f"[dev-env-sync] ⚠️  Canonical worktree is on '{current_branch}'; auto-return to\n"
                    f"main failed:\n{checkout.stderr.strip()}\n"
                    "Switch it back manually so symlinked tooling is current.",
                    file=sys.stderr,
                )
                sys.exit(0)
            print(
                f"[dev-env-sync] Returned canonical worktree to main (was on '{current_branch}') — "
                "symlinked tooling restored."
            )
            current_branch = "main"
        # "on-main" cannot occur on this path (current_branch != "main"); fall through to pull.

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
            "status` to investigate before proceeding.",
            file=sys.stderr,
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
            + pull.stderr.strip(),
            file=sys.stderr,
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
