#!/usr/bin/env python3
"""Diagnose the worktree-on-`main` topology of a git repo and decide a correction.

dev-env's architecture rule has two halves: the **canonical** checkout (`~/Git/dev-env`,
whose working tree is symlinked into `~/.claude/`) must always be on `main`, and **no
non-canonical worktree** may hold `main`. Git checks a branch out in at most one worktree,
so a non-canonical worktree squatting `main` *implies* the canonical is off `main` — it
freed the ref first (e.g. a stray `gh pr checkout` run in the canonical), then `gh pr merge
--delete-branch` from another worktree checked `main` out *there* (gh deletes the merged
local branch and checks out the default branch). The squatter then blocks gh's local
post-merge checkout for every *other* worktree's merge, and the canonical can't return to
`main`, so newly-merged hooks/scripts stay silently inert in the live `~/.claude/`. The
2026-06-22 PR #391 recovery is the motivating incident — see ADR-058 and dev-env#396.

This module is **policy-free and pure** (no ``_winsubp``, no subprocess, no ``main()``) so
its helpers unit-test offline. It parses ``git worktree list --porcelain``, diagnoses the
topology, and returns a *decision*; each caller performs the git mutation itself:

  - ``prune-merged-worktrees.py`` **parks** an idle squatter off ``main`` (recreates its
    ``claude/<slug>`` branch at HEAD) instead of the older outright ``git worktree remove``.
  - ``post-pr-merge-pull.py`` parks the just-merged worktree when ``gh`` left it on ``main``.
  - ``dev-env-sync.py`` auto-returns a *clean* canonical to ``main``, else warns — naming the
    squatter and its park command, or preserving uncommitted drift.

The non-destructive **park** (recreate ``claude/<slug>`` at the worktree's current commit)
is the correction precedent: ``git checkout -b`` changes no working-tree files, so it frees
``main`` without touching even a dirty worktree's state. A caller parking a *different*
worktree must still honor the ADR-051 liveness guard first (never move a live session);
parking the caller's own just-merged session worktree (post-merge) is inherently safe.
"""
from collections import namedtuple
from pathlib import Path


def parse_worktree_porcelain(text: str) -> "list[dict]":
    """Parse ``git worktree list --porcelain`` into ``[{path, branch}, ...]``.

    ``branch`` is the short name, ``"<detached>"`` for a detached HEAD, or ``""`` when
    unspecified. Used by ``dev-env-sync.py`` and ``prune-merged-worktrees.py``;
    ``reclaim-worktree-disk.py`` keeps an equivalent local copy (not in this fix's scope).
    """
    worktrees: "list[dict]" = []
    current: "dict | None" = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = {"path": line[len("worktree "):].strip(), "branch": ""}
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):].strip()
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line == "detached" and current is not None:
            current["branch"] = "<detached>"
    if current is not None:
        worktrees.append(current)
    return worktrees


def _norm(path: "str | Path") -> str:
    """Resolved string form for robust path comparison (matches prune/reclaim)."""
    return str(Path(path).resolve())


def canonical_worktree(worktrees: "list[dict]") -> "dict | None":
    """The canonical (primary) worktree — always the first ``git worktree list`` entry."""
    return worktrees[0] if worktrees else None


def park_branch_for(path: "str | Path") -> str:
    """The branch a worktree should be parked on: ``claude/<basename>``.

    Mirrors how Claude-managed worktrees under ``.claude/worktrees/<slug>`` are named — the
    slug is the dir basename and its branch is ``claude/<slug>``. ``gh``'s ``--delete-branch``
    deletes that branch and checks out ``main``; parking recreates it at the current commit.
    """
    return "claude/" + Path(str(path)).name


def main_squatter(worktrees: "list[dict]") -> "dict | None":
    """The first NON-canonical worktree checked out on ``main``, or ``None``.

    Git checks ``main`` out in at most one worktree, so if it's the canonical there's no
    squatter. The path guard is belt-and-suspenders against a malformed list.
    """
    canonical = canonical_worktree(worktrees)
    if canonical is None:
        return None
    canonical_path = _norm(canonical["path"])
    for wt in worktrees[1:]:
        if wt["branch"] == "main" and _norm(wt["path"]) != canonical_path:
            return wt
    return None


def canonical_on_main(worktrees: "list[dict]") -> bool:
    """True when the canonical (primary) worktree is on ``main``."""
    canonical = canonical_worktree(worktrees)
    return canonical is not None and canonical["branch"] == "main"


MainTopology = namedtuple(
    "MainTopology",
    ["canonical_path", "canonical_branch", "squatter_path", "squatter_branch", "healthy"],
)


def diagnose_main_topology(worktrees: "list[dict]") -> MainTopology:
    """Diagnose the worktree-on-``main`` topology.

    ``healthy`` = the canonical is on ``main`` AND no non-canonical worktree squats ``main``.
    An empty worktree list yields a healthy verdict (nothing to correct).
    """
    canonical = canonical_worktree(worktrees)
    if canonical is None:
        return MainTopology(None, None, None, None, True)
    squatter = main_squatter(worktrees)
    healthy = canonical["branch"] == "main" and squatter is None
    return MainTopology(
        canonical_path=canonical["path"],
        canonical_branch=canonical["branch"],
        squatter_path=squatter["path"] if squatter else None,
        squatter_branch=squatter["branch"] if squatter else None,
        healthy=healthy,
    )


SyncAction = namedtuple("SyncAction", ["kind", "squatter_path", "park_branch"])


def canonical_sync_action(topo: MainTopology, canonical_clean: bool) -> SyncAction:
    """Decide what ``dev-env-sync`` should do given the diagnosed topology.

    Returns a ``SyncAction`` whose ``kind`` is one of:

      - ``"on-main"``          the canonical is on ``main`` -> nothing to do (defensive;
                               the caller normally guards this branch away).
      - ``"warn-squatter"``    a non-canonical worktree holds ``main``; the canonical can't
                               return until it is parked. ``squatter_path`` / ``park_branch``
                               give the exact ``git -C <squatter> checkout -b <park>`` recovery.
      - ``"return-canonical"`` ``main`` is free and the canonical is clean -> safe to
                               ``git checkout main`` (restores the ``~/.claude`` symlinks).
      - ``"warn-dirty"``       ``main`` is free but the canonical has uncommitted changes ->
                               do NOT auto-switch (preserve drift); warn with the manual command.
    """
    if topo.canonical_branch == "main":
        return SyncAction("on-main", None, None)
    if topo.squatter_path is not None:
        return SyncAction("warn-squatter", topo.squatter_path, park_branch_for(topo.squatter_path))
    if canonical_clean:
        return SyncAction("return-canonical", None, None)
    return SyncAction("warn-dirty", None, None)


def merge_park_target(cwd: str, canonical_path: str, cwd_branch: str) -> "str | None":
    """The branch to park ``cwd`` on after a merge, or ``None``.

    Returns ``claude/<basename(cwd)>`` when ``cwd`` is a NON-canonical worktree currently on
    ``main`` — i.e. ``gh pr merge --delete-branch`` checked ``main`` out in the worktree (only
    possible when the canonical had freed the ref). Returns ``None`` when ``cwd`` is empty, is
    the canonical itself, or is not on ``main`` (the normal case, where gh's local checkout
    failed because the canonical holds ``main`` and the worktree kept its own branch).
    """
    if not cwd or not canonical_path:
        return None
    if cwd_branch != "main":
        return None
    if _norm(cwd) == _norm(canonical_path):
        return None
    return park_branch_for(cwd)
