#!/usr/bin/env python3
"""
UserPromptSubmit hook: correct a hijacked engineering-journal canonical checkout.

Every prompt, checks whether the canonical engineering-journal repo
(`C:/Users/brown/Git/engineering-journal`, no worktree, no `-C` redirect) is sitting on the
dev-env#630 hijack signature — detached HEAD, or a stray `claude/<slug>` branch belonging to
no live worktree — and if so, restores it to `main`.

Unlike dev-env's own canonical (which must ALWAYS be on `main`, per ADR-058), engineering-
journal's canonical is legitimately on `draft/YYYY-MM-DD` for most of every working day (the
documented Stub file workflow in `claude/CLAUDE.md`). So this hook does NOT treat "off main"
as broken — only the two states never legitimate for any canonical: detached, or a
`claude/*`-prefixed branch (reserved for actual Claude-managed worktrees at a different
filesystem path). See `is_hijacked_branch` in `_worktree_topology.py`.

Root cause (a scheduled-task worktree-provisioning mechanism hijacking the canonical's HEAD
before/after a routine's own session, confirmed reproduced 2 mornings running via reflog
evidence) is very likely not fixable from within dev-env — this is the interim mitigation
proposed in dev-env#630: detect and self-correct, bounding the damage window from "hours until
a human happens to notice" to "the next prompt in any session on this machine."

The correction is non-destructive: `git checkout main` never deletes the hijacked branch or
its commits — they remain reachable by name if ever needed (ADR-058's "park, don't remove"
philosophy; no "park" step is even needed here since the hijacked branch already has a name
distinct from `main`).

All advisories print to STDOUT, never stderr (dev-env#699, ADR-099). This hook always exits 0,
and per the Claude Code hooks reference, a `UserPromptSubmit` hook's exit-0 stdout is added to
Claude's context — stderr is not. A prior version routed every warning to stderr, the identical
defect ADR-098 fixed in the sibling `dev-env-sync.py`.

Exit 0 always — never block the user's prompt.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import os
import subprocess
import sys
from pathlib import Path

import _hookutil
from _worktree_topology import (
    DETACHED,
    canonical_sync_action,
    diagnose_main_topology,
    is_hijacked_branch,
    parse_worktree_porcelain,
    resolve_current_branch,
)

# Overridable via JOURNAL_CANONICAL_GUARD_REPO_PATH solely so a test can point this at a
# disposable temp directory instead of the developer's actual engineering-journal checkout —
# mirrors pre-tool-use-canonical-mutate-guard.py's CANONICAL_MUTATE_GUARD_JOURNAL_PATH.
JOURNAL_REPO = Path(
    os.environ.get("JOURNAL_CANONICAL_GUARD_REPO_PATH", str(Path.home() / "Git" / "engineering-journal"))
)


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=JOURNAL_REPO,
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


def main() -> None:
    _hookutil.record_heartbeat("journal-canonical-guard")
    try:
        sys.stdin.read()
    except Exception:
        pass

    # Guard: repo must exist at the expected path.
    if not JOURNAL_REPO.is_dir():
        sys.exit(0)

    # Cheap first read (no worktree-list/status calls) so the common healthy path — on main,
    # on draft/YYYY-MM-DD, or any other legitimate branch — stays cheap, mirroring
    # dev-env-sync.py's "extra git calls only on the rare/broken path" design.
    branch = run(["git", "symbolic-ref", "--short", "HEAD"])
    current_branch = resolve_current_branch(branch.returncode, branch.stdout)
    if not is_hijacked_branch(current_branch):
        sys.exit(0)

    wt = run(["git", "worktree", "list", "--porcelain"])
    if wt.returncode != 0:
        print(
            f"[journal-canonical-guard] WARNING: engineering-journal canonical is on "
            f"'{current_branch}' (looks hijacked, dev-env#630) and its worktree list could "
            f"not be read. Switch it back manually: git -C {JOURNAL_REPO} checkout main"
        )
        sys.exit(0)

    worktrees = parse_worktree_porcelain(wt.stdout)
    topo = diagnose_main_topology(worktrees)

    # Re-check against this FRESH read before acting. This canonical sees dozens of
    # concurrent stub-writing sessions daily (each running `checkout main && checkout -b
    # draft/YYYY-MM-DD` directly against it) — a concurrent session may have already moved it
    # onto a legitimate branch between our two reads above. Acting on the stale first read
    # would yank that session's just-established branch back to main (TOCTOU; caught in
    # design review, not observed in production).
    if not is_hijacked_branch(topo.canonical_branch):
        sys.exit(0)

    status = run(["git", "status", "--porcelain"])
    clean = status.returncode == 0 and not status.stdout.strip()
    action = canonical_sync_action(topo, clean)

    if action.kind == "warn-squatter":
        print(
            f"[journal-canonical-guard] WARNING: engineering-journal canonical is on "
            f"'{topo.canonical_branch}' (looks hijacked, dev-env#630) and worktree\n"
            f"  {action.squatter_path}\n"
            "is squatting 'main' - cannot auto-restore. Free the ref (non-destructive):\n"
            f"  git -C {action.squatter_path} checkout -b {action.park_branch}\n"
            "then the next prompt restores the canonical automatically (or run\n"
            f"  git -C {JOURNAL_REPO} checkout main\n"
            ")."
        )
    elif action.kind == "warn-dirty":
        print(
            f"[journal-canonical-guard] WARNING: engineering-journal canonical is on "
            f"'{topo.canonical_branch}' (looks hijacked, dev-env#630) with uncommitted "
            "changes - not auto-switching to preserve them. Investigate, then:\n"
            f"  git -C {JOURNAL_REPO} status\n"
            f"  git -C {JOURNAL_REPO} checkout main   # after committing/stashing"
        )
    elif action.kind == "return-canonical":
        # Final, cheap re-check immediately before the mutation itself - narrows the residual
        # TOCTOU window to a single subprocess spawn (the same order of magnitude as
        # dev-env-sync.py's own residual). This canonical is moved between main and
        # draft/YYYY-MM-DD by many concurrent sessions routinely, unlike dev-env's own
        # canonical (which is never legitimately off main at all), so the window between the
        # git-status read above and this checkout is meaningfully more likely to be hit here
        # than the equivalent window in dev-env-sync.py (review finding, PR #661).
        final = run(["git", "symbolic-ref", "--short", "HEAD"])
        if not is_hijacked_branch(resolve_current_branch(final.returncode, final.stdout)):
            sys.exit(0)  # a concurrent session already fixed it since our last read
        checkout = run(["git", "checkout", "main"])
        if checkout.returncode != 0:
            print(
                f"[journal-canonical-guard] WARNING: engineering-journal canonical is on "
                f"'{topo.canonical_branch}' (looks hijacked, dev-env#630); auto-restore to "
                f"main failed:\n{checkout.stderr.strip()}\n"
                f"Switch it back manually: git -C {JOURNAL_REPO} checkout main"
            )
        else:
            recoverable = topo.canonical_branch != DETACHED  # DETACHED is a sentinel,
            # not a real ref -- nothing nameable to offer as a checkout target.
            recovery = (
                f" Non-destructive - '{topo.canonical_branch}' still exists if needed: "
                f"git -C {JOURNAL_REPO} checkout {topo.canonical_branch}"
                if recoverable
                else ""
            )
            print(
                f"[journal-canonical-guard] Restored engineering-journal canonical to main "
                f"(was on '{topo.canonical_branch}', dev-env#630 hijack pattern)."
                f"{recovery}"
            )
    # "on-main" cannot occur here (is_hijacked_branch never matches "main").

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Honor the "Exit 0 always" docstring contract even against an unexpected subprocess
        # failure (timeout, git missing from PATH, etc.) - matches the established fail-open
        # convention for engineering-journal-guarding UserPromptSubmit hooks in this repo
        # (new-day-journal-check.py wraps every subprocess.run call the same way; review
        # finding, PR #661).
        sys.exit(0)
