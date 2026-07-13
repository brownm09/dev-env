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

This module hosts **three distinct invariants** now. The dev-env-specific one above (a
canonical that must *always* be ``main``) drives ``main_squatter``/``canonical_sync_action``'s
"healthy = on main" framing. A second, repo-agnostic one — a canonical must never be
*detached* or checked out onto a Claude-managed worktree's own ``claude/<slug>`` branch,
regardless of what *other* branch is otherwise legitimate for that repo — drives
``resolve_current_branch``/``is_hijacked_branch`` below (dev-env#619, dev-env#630, ADR-093).
The two compose: a caller with a narrower "must always be main" invariant (``dev-env-sync.py``)
uses the first directly; a caller with a broader "may legitimately be on many branches, just
never a hijacked one" invariant (``journal-canonical-guard.py``) uses the second to *gate*
before reusing ``diagnose_main_topology``/``canonical_sync_action`` for the "is it safe to
auto-correct" sub-decision only.

A third invariant is repo-agnostic and branch-*pattern*-scoped rather than tied to a single
literal name: some branches (engineering-journal's ``draft/YYYY-MM-DD``) must never be held by
any non-canonical worktree at all, independent of what the canonical itself currently holds.
Unlike ``main`` — which ``main_squatter`` only flags once the canonical has already freed the
ref, since git's one-worktree-per-branch rule makes the two mutually exclusive — a
``draft/YYYY-MM-DD`` branch can be squatted by a second worktree while the canonical sits on
``main`` (early in the day) or already on that exact draft branch; either way, a second
worktree holding the literal branch name is a bug, not a state to compare the canonical
against. Drives ``non_canonical_worktrees_matching``/``pattern_squat_action`` below
(dev-env#747, ADR-105).

This module is **policy-free and pure** (no ``_winsubp``, no subprocess, no ``main()``) so
its helpers unit-test offline. It parses ``git worktree list --porcelain``, diagnoses the
topology, and returns a *decision*; each caller performs the git mutation itself:

  - ``prune-merged-worktrees.py`` **parks** an idle squatter off ``main`` (recreates its
    ``claude/<slug>`` branch at HEAD) instead of the older outright ``git worktree remove``.
  - ``post-pr-merge-pull.py`` parks the just-merged worktree when ``gh`` left it on ``main``.
  - ``dev-env-sync.py`` auto-returns a *clean* canonical to ``main``, else warns — naming the
    squatter and its park command, or preserving uncommitted drift.
  - ``journal-canonical-guard.py`` auto-returns a *hijacked* engineering-journal canonical
    (detached, or on a stray ``claude/*`` branch) to ``main``, else warns — the same
    return-canonical/warn-squatter/warn-dirty decision, gated by a narrower predicate since
    that repo's canonical is legitimately on many other branches (e.g. ``draft/YYYY-MM-DD``).

The non-destructive **park** (recreate ``claude/<slug>`` at the worktree's current commit)
is the correction precedent: ``git checkout -b`` changes no working-tree files, so it frees
``main`` without touching even a dirty worktree's state. A caller parking a *different*
worktree must still honor the ADR-051 liveness guard first (never move a live session);
parking the caller's own just-merged session worktree (post-merge) is inherently safe.
"""
import re
from collections import namedtuple
from pathlib import Path

# The sentinel for a detached-HEAD worktree/canonical, shared by every producer and consumer
# in this module (and by journal-canonical-guard.py) so the spelling can't drift between them
# (review finding, PR #661: previously a bare "<detached>" string literal repeated at each site).
DETACHED = "<detached>"


def parse_worktree_porcelain(text: str) -> "list[dict]":
    """Parse ``git worktree list --porcelain`` into ``[{path, branch}, ...]``.

    ``branch`` is the short name, ``DETACHED`` for a detached HEAD, or ``""`` when
    unspecified. Used by ``dev-env-sync.py``, ``prune-merged-worktrees.py``, and
    ``reclaim-worktree-disk.py``.
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
            current["branch"] = DETACHED
    if current is not None:
        worktrees.append(current)
    return worktrees


def _norm(path: "str | Path") -> str:
    """Resolved string form for robust path comparison (matches prune/reclaim).

    A falsy path returns ``""`` rather than resolving to the *current working directory*
    (which ``Path("").resolve()`` does) — so an empty path can never compare-equal to a real
    worktree and trick a caller into acting on cwd. Callers still guard empties up front; this
    is defence in depth for any future caller.
    """
    if not path:
        return ""
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
    squatter. A non-canonical worktree on ``main`` is an *anomalous* squatter only when the
    canonical is a normal checkout sitting on a real, non-``main`` branch — that is what frees
    the ``main`` ref. A **bare or detached** canonical cannot hold a working-tree checkout of
    ``main`` at all, so a secondary worktree on ``main`` there is *legitimate* (``main`` must
    live somewhere) — never flag it, or prune/dev-env-sync would mis-park a bare/detached-
    primary repo's real ``main`` worktree (correctness review, PR #398). The path guard is
    belt-and-suspenders against a malformed list.
    """
    canonical = canonical_worktree(worktrees)
    if canonical is None:
        return None
    cbranch = canonical["branch"]
    if not cbranch or cbranch == "main" or cbranch == DETACHED:
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


def merge_park_target(cwd: str, worktrees: "list[dict]") -> "str | None":
    """The branch to park ``cwd`` on after a merge, or ``None``.

    ``worktrees`` is the **merged repo's** parsed ``git worktree list``. Returns
    ``claude/<basename(cwd)>`` only when ``cwd`` is a NON-canonical worktree **of that repo**
    currently on ``main`` — i.e. ``gh pr merge --delete-branch`` checked ``main`` out in the
    session's worktree (only possible when the canonical had freed the ref). The branch is read
    from the authoritative worktree list, not a separate ``symbolic-ref`` call.

    Returns ``None`` when ``cwd`` is empty, is the canonical itself, is **not one of the repo's
    worktrees** (e.g. ``gh pr merge --repo X`` was run from an unrelated checkout — never park
    that other repo; correctness review, PR #398), or is not on ``main`` (the normal case, where
    gh's local checkout failed because the canonical holds ``main`` and the worktree kept its own
    branch).
    """
    if not cwd or not worktrees:
        return None
    canonical = canonical_worktree(worktrees)
    norm_cwd = _norm(cwd)
    if canonical is not None and _norm(canonical["path"]) == norm_cwd:
        return None  # merge ran from the canonical itself
    for wt in worktrees:
        if _norm(wt["path"]) == norm_cwd:
            # cwd is a worktree of the merged repo — park it only if it grabbed main.
            return park_branch_for(cwd) if wt["branch"] == "main" else None
    return None  # cwd is not a worktree of the merged repo


def resolve_current_branch(symbolic_ref_returncode: int, symbolic_ref_stdout: str) -> str:
    """Resolve the working branch name from ``git symbolic-ref --short HEAD``'s result.

    A non-zero return code means detached HEAD (no symbolic ref to resolve) — routed to the
    ``DETACHED`` sentinel so callers feed it into the same diagnostic path as a wrong-branch
    canonical, rather than silently exiting (dev-env#619). ``dev-env-sync.py`` used to call
    ``sys.exit(0)`` directly on a non-zero return code, so a detached canonical never reached
    ``diagnose_main_topology``/``canonical_sync_action`` at all — even though both already
    handle ``DETACHED`` correctly (see ``main_squatter``'s bare/detached guard above), since
    nothing routed a detached HEAD into them.
    """
    if symbolic_ref_returncode != 0:
        return DETACHED
    return symbolic_ref_stdout.strip()


def is_hijacked_branch(branch: "str | None") -> bool:
    """True when ``branch`` matches the dev-env#630 hijack signature.

    Unlike dev-env's canonical (always ``main``), other repos' canonicals may legitimately
    sit on other named branches (e.g. engineering-journal's ``draft/YYYY-MM-DD``). This only
    flags states never legitimate for ANY canonical: detached, or checked out onto a
    Claude-managed worktree's own ``claude/<slug>`` branch — reserved for actual worktrees
    living at a different filesystem path, never the canonical itself (git allows a branch
    checked out in at most one worktree, so the canonical and a live worktree can never both
    hold the same ``claude/<slug>`` branch at once).

    Accepts ``None`` (e.g. ``diagnose_main_topology([])``'s ``canonical_branch`` field) without
    raising — mirrors ``main_squatter``'s own falsy-branch guard.
    """
    return bool(branch) and (branch == DETACHED or branch.startswith("claude/"))


# engineering-journal's Stub file workflow branch-naming convention (claude/CLAUDE.md ->
# Engineering Journal -> Stub file workflow): draft/YYYY-MM-DD, or draft/YYYY-MM-DD-recovery
# (docs/REFERENCE.md's documented recovery-branch suffix). Anchored full-match. Shared with
# pre-tool-use-journal-draft-worktree-guard.py's own identically-defined constant — that file
# documents why it is a deliberate duplicate rather than an import (see its module docstring);
# this module's copy is the "source of truth" shape the other one must stay byte-identical to.
DRAFT_BRANCH_RE = re.compile(r"^draft/\d{4}-\d{2}-\d{2}(-recovery)?$")


def non_canonical_worktrees_matching(worktrees: "list[dict]", pattern: "re.Pattern") -> "list[dict]":
    """Every non-canonical worktree whose branch matches ``pattern``.

    Generalizes ``main_squatter``'s "a non-canonical worktree holding a branch it shouldn't"
    check to an arbitrary pattern, for branches whose invariant is "never legitimately held
    anywhere but the canonical" regardless of what the canonical itself currently holds (see
    this module's own docstring for why that differs from ``main``'s mutual-exclusion shape).
    Returns every match, not just the first — more than one stale squatter can coexist
    (confirmed live: yesterday's AND today's draft branches, each locked to a different
    throwaway worktree, dev-env#747).
    """
    canonical = canonical_worktree(worktrees)
    canonical_path = _norm(canonical["path"]) if canonical else ""
    return [
        wt for wt in worktrees
        if _norm(wt["path"]) != canonical_path and pattern.match(wt.get("branch") or "")
    ]


PatternSquatAction = namedtuple("PatternSquatAction", ["kind", "path", "branch", "park_branch"])


def pattern_squat_action(path: str, branch: str, *, live: bool, dirty: bool, fully_pushed: bool) -> PatternSquatAction:
    """Decide what to do about one non-canonical worktree holding a squatted branch.

    ``kind`` is one of:
      - ``"warn-live"``       a live Claude session (ADR-051) owns this worktree — never touch it.
      - ``"park-and-remove"`` idle, clean, and fully pushed (0 commits ahead of the squatted
                              branch's own ``origin/<branch>``) — safe to park (free the branch
                              name, non-destructive) AND remove the worktree in the same pass.
                              Measured against the squatted branch's own origin, NOT
                              ``origin/main`` via the generic ``is_merged()`` check the rest of
                              ``prune-merged-worktrees.py`` uses — a composed draft branch's
                              content reaches ``main`` via a fresh squash commit (ADR-082), never
                              a fast-forward or matching PR head, so ``is_merged()`` would never
                              fire for this branch shape and a merely-parked worktree would
                              linger forever.
      - ``"park-only"``      idle but dirty, or not provably fully pushed — free the branch name
                              only; leave the worktree and its contents completely untouched for
                              human review (mirrors the ``stub-823-120134`` disposition,
                              dev-env#747).

    Caller supplies ``live``/``dirty``/``fully_pushed`` as pre-computed booleans — this module
    stays pure/subprocess-free, mirroring ``canonical_sync_action(topo, canonical_clean)``'s own
    pattern.
    """
    if live:
        return PatternSquatAction("warn-live", path, branch, None)
    park = park_branch_for(path)
    if not dirty and fully_pushed:
        return PatternSquatAction("park-and-remove", path, branch, park)
    return PatternSquatAction("park-only", path, branch, park)
