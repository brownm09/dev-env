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

All advisories print to STDOUT, never stderr (dev-env#694, ADR-098). This hook always exits 0,
and per the Claude Code hooks reference, a `UserPromptSubmit` hook's exit-0 stdout is added to
Claude's context — stderr is not. A prior version routed warnings to stderr, which made a
fast-forward failure (e.g. a dirty working-tree file conflicting with an incoming commit)
invisible for 36+ hours and 21+ commits of drift, confirmed live during the dev-env#694
investigation: the hook fired every prompt, `git pull --ff-only` failed every time, and the
stderr warning never once reached the model or the user. Every warning now also states local/
remote short SHAs and the commit-behind count so a future occurrence is self-diagnosing without
a manual `git log`/`git fetch` comparison.

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


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _count_from(result: subprocess.CompletedProcess) -> int:
    """Parse a `git rev-list --count` result; 0 on any failure (advisory-only diagnostic)."""
    text = result.stdout.strip()
    return int(text) if result.returncode == 0 and text.isdigit() else 0


def format_sync_note(local: str, remote: str, behind: int) -> str:
    """Short diagnostic clause: short SHAs + commits-behind count.

    Appended to every fast-forward-related warning so a future occurrence is self-diagnosing
    without a manual `git log`/`git fetch` comparison (dev-env#694's suggested follow-up).
    """
    return f"local {local[:8]} is {behind} commit{_plural(behind)} behind origin/main {remote[:8]}"


def format_pull_failure_message(local: str, remote: str, behind: int, git_stderr: str) -> str:
    return (
        "[dev-env-sync] WARNING: fast-forward pull failed — "
        + format_sync_note(local, remote, behind)
        + " and could not be applied.\n"
        + git_stderr.strip()
    )


def format_diverged_message(local: str, remote: str, behind: int, ahead: int) -> str:
    """Warning for local `main` not being a fast-forward ancestor of `origin/main`.

    Covers two distinct states the caller does not distinguish: a true fork (``behind`` and
    ``ahead`` both > 0) versus local merely being ahead with nothing new on origin (``behind
    == 0``, e.g. a commit landed directly on the canonical). Calling the latter "diverged"
    would be internally contradictory ("0 commits behind ... has diverged") — review finding,
    PR #701.
    """
    if behind == 0:
        return (
            "[dev-env-sync] WARNING: local dev-env repo is ahead of origin/main "
            f"(local {local[:8]} has {ahead} commit{_plural(ahead)} not on origin/main "
            f"{remote[:8]} — did something commit directly to the canonical?).\n"
            "CLAUDE.md and symlinked tooling may be stale. Run `git -C ~/Git/dev-env "
            "status` to investigate before proceeding."
        )
    return (
        "[dev-env-sync] WARNING: local dev-env repo has diverged from origin/main "
        f"(local {local[:8]} is {ahead} commit{_plural(ahead)} ahead and {behind} "
        f"commit{_plural(behind)} behind origin/main {remote[:8]}).\n"
        "CLAUDE.md and symlinked tooling may be stale. Run `git -C ~/Git/dev-env "
        "status` to investigate before proceeding."
    )


def format_pulled_message(local: str, remote: str, behind_count: int, pulled_lines: "list[str]") -> str:
    """Success message for a completed fast-forward pull.

    ``behind_count`` is measured BEFORE the pull; ``pulled_lines`` is the ``git log --oneline``
    output measured AFTER. They should agree — a mismatch means the ref moved between the two
    measurements (most likely a concurrent session's own sync hook racing this one against the
    same shared canonical checkout), which is exactly the ambiguity that made dev-env#694 hard
    to root-cause after the fact. Surfacing it here means a future recurrence explains itself.

    A mismatch note is only printed when ``behind_count`` reflects a real measurement. At the
    call site, ``base == local`` and ``local != remote`` are already established, so a
    successful ``rev-list --count`` is always >= 1 here — ``behind_count == 0`` can only mean
    the measurement itself failed (``_count_from``'s fail-open sentinel), not a genuine "0
    commits behind". Blaming that on a concurrent process would misattribute a measurement
    failure as a race — review finding, PR #701.
    """
    count = len(pulled_lines)
    shown = pulled_lines[:5]
    trailer = f"  ... and {count - 5} more" if count > 5 else ""
    if behind_count == 0:
        note = "\n  (pre-pull commit-behind count could not be measured)"
    elif count != behind_count:
        note = (
            f"\n  (measured {behind_count} commit{_plural(behind_count)} behind before "
            "pulling — a concurrent process likely moved origin/main mid-pull)"
        )
    else:
        note = ""
    return (
        f"[dev-env-sync] Pulled {count} commit{_plural(count)} from origin/main "
        f"(local {local[:8]} -> origin/main {remote[:8]}) — CLAUDE.md and tooling are now "
        "current.\n"
        + "\n".join(f"  {line}" for line in shown)
        + (f"\n{trailer}" if trailer else "")
        + note
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
                f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}' and its worktree "
                "list could not be read — ~/.claude/ symlinks may serve stale hooks/scripts.\n"
                f"Switch it back manually: git -C {DEV_ENV_REPO} checkout main"
            )
            sys.exit(0)
        worktrees = parse_worktree_porcelain(wt.stdout)
        topo = diagnose_main_topology(worktrees)
        status = run(["git", "status", "--porcelain"])
        canonical_clean = status.returncode == 0 and not status.stdout.strip()
        action = canonical_sync_action(topo, canonical_clean)

        if action.kind == "warn-squatter":
            print(
                f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}' and worktree\n"
                f"  {action.squatter_path}\n"
                "is squatting 'main' — the canonical cannot return until that worktree is parked\n"
                "off main. Free the ref (non-destructive — changes no files):\n"
                f"  git -C {action.squatter_path} checkout -b {action.park_branch}\n"
                "then the next prompt returns the canonical to main automatically (or run\n"
                f"  git -C {DEV_ENV_REPO} checkout main\n"
                "). Until then ~/.claude/ symlinks may serve stale hooks/scripts."
            )
            sys.exit(0)

        if action.kind == "warn-dirty":
            print(
                f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}' with uncommitted\n"
                "changes — ~/.claude/ symlinks may serve stale hooks/scripts. Not auto-switching to\n"
                "preserve your drift; commit or stash, then:\n"
                f"  git -C {DEV_ENV_REPO} checkout main"
            )
            sys.exit(0)

        if action.kind == "return-canonical":
            checkout = run(["git", "checkout", "main"])
            if checkout.returncode != 0:
                print(
                    f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}'; auto-return to\n"
                    f"main failed:\n{checkout.stderr.strip()}\n"
                    "Switch it back manually so symlinked tooling is current."
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

    # How far behind local is right now, measured once up front so it's available to every
    # downstream message (success, diverged, or failed-pull) without repeating the git call in
    # three different branches, and so a failed pull still names the gap it couldn't close
    # (dev-env#694).
    behind_count = _count_from(run(["git", "rev-list", "--count", f"{local}..{remote}"]))

    if base != local:
        # Local main has commits not on origin/main — diverged.
        ahead_count = _count_from(run(["git", "rev-list", "--count", f"{remote}..{local}"]))
        print(format_diverged_message(local, remote, behind_count, ahead_count))
        sys.exit(0)

    # Fast-forward is safe — pull.
    pull = run(["git", "pull", "--ff-only", "origin", "main"])
    if pull.returncode == 0:
        # Count how many commits were pulled.
        log = run(["git", "log", "--oneline", f"{local}..HEAD"])
        lines = [line for line in log.stdout.strip().splitlines() if line]
        print(format_pulled_message(local, remote, behind_count, lines))
    else:
        print(format_pull_failure_message(local, remote, behind_count, pull.stderr))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Honor the "Exit 0 always" docstring contract even against an unexpected subprocess
        # failure (timeout, git missing from PATH, etc.) — matches the established fail-open
        # convention for UserPromptSubmit hooks touching this canonical
        # (journal-canonical-guard.py, new-day-journal-check.py; review finding, PR #661).
        # Unlike those two, this hook's whole purpose is eliminating invisible failures, so
        # print a minimal pure-ASCII notice (never a formatted/dynamic value that could itself
        # raise) rather than fail open in total silence — review finding, PR #701.
        try:
            print("[dev-env-sync] WARNING: sync check failed unexpectedly and was skipped this prompt.")
        except Exception:
            pass
        sys.exit(0)
