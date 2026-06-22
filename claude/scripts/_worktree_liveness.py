#!/usr/bin/env python3
"""Detect whether a git worktree has a live Claude Code session.

Claude Code writes each session's transcript to

    <config>/projects/<slug>/<session-uuid>.jsonl

where ``<slug>`` is the session's working directory with every ``:`` ``\\`` ``/`` ``.``
replaced by ``-``. Each git worktree has a distinct cwd, hence a distinct transcript
directory, so a recent newest-``*.jsonl`` mtime there is a reliable "a Claude session is
(or just was) active in this worktree" heartbeat.

``prune-merged-worktrees.py`` and ``reclaim-worktree-disk.py`` call
``worktree_session_is_live()`` to SKIP a worktree an active session is running in —
guarding against severing a live session by removing its directory
(``git worktree remove``) or stripping its ``node_modules`` out from under a running
build. The guard is purely *additive*: it only ever adds skips, so it can never cause
more removal/reclamation than the pre-existing checks, only less.

An out-of-process routine (it runs in its own worktree) cannot see another worktree's
session via ``os.getcwd()`` / ``--protect-cwd``; the transcript mtime is the only signal
that crosses that boundary. See ADR-051.

This module is import-only (no ``_winsubp``, no subprocess, no ``main()``) so its helpers
unit-test offline. Each caller owns its own window constant — the module is policy-free
(removal severs the session ⇒ a long window; stripping ``node_modules`` is self-healing
⇒ a short window that keeps disk reclamation aggressive).
"""
import os
import time
from pathlib import Path

# Neutral fallback only. Callers pass their own window — prune uses 24h (removal severs
# the session), reclaim uses 6h (stripping node_modules is self-healing and must stay
# aggressive against ENOSPC). The policy lives with them, not here.
DEFAULT_LIVENESS_WINDOW_SECONDS = 6 * 60 * 60

# Characters Claude Code maps to '-' when encoding the per-session transcript directory
# name under ~/.claude/projects/. Both path separators are included, so the Windows and
# POSIX spellings of one worktree encode identically.
_SLUG_CHARS = (":", "\\", "/", ".")


def encode_project_slug(path: "str | os.PathLike[str]") -> str:
    """Encode a worktree path the way Claude Code names its ``projects/`` subdir.

    Replaces every ``:`` ``\\`` ``/`` ``.`` with ``-``. Verified empirically against real
    ``projects/`` dirs::

        C:\\Users\\brown\\Git\\dev-env\\.claude\\worktrees\\foo
          -> C--Users-brown-Git-dev-env--claude-worktrees-foo
    """
    s = str(path)
    for ch in _SLUG_CHARS:
        s = s.replace(ch, "-")
    return s


def default_projects_root() -> Path:
    """The ``~/.claude/projects`` directory (honors ``CLAUDE_CONFIG_DIR``)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".claude"
    return root / "projects"


def transcript_dir_for(
    worktree_path: "str | os.PathLike[str]",
    projects_root: "str | os.PathLike[str]",
) -> "Path | None":
    """Locate the transcript directory for a worktree, or ``None`` if there is none.

    Primary: the exact encoded-slug dir. Fallback: a ``projects/`` subdir whose name
    ends with ``-worktrees-<basename>`` — the worktree basename is a globally-unique
    random slug, so this re-finds the transcript even if a future Claude Code version
    tweaks the path-prefix encoding. A fallback collision (the same basename across two
    repos) only ever over-protects (an extra skip), never deletes more, so the loose
    match is safe.
    """
    root = Path(projects_root)
    exact = root / encode_project_slug(worktree_path)
    if exact.is_dir():
        return exact
    base = Path(str(worktree_path)).name
    if not base:
        return None
    suffix = f"-worktrees-{base}"
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and entry.name.endswith(suffix):
                return Path(entry.path)
    except OSError:
        pass
    return None


def newest_jsonl_mtime(transcript_dir: "str | os.PathLike[str]") -> "float | None":
    """Newest mtime (epoch secs) among ``*.jsonl`` under ``transcript_dir``, recursively.

    Recursive so a quiet top-level transcript with an active ``<uuid>/subagents/*.jsonl``
    still reads as live. Returns ``None`` when the dir is absent or holds no readable
    ``.jsonl``.
    """
    newest: "float | None" = None
    try:
        for p in Path(transcript_dir).rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    except OSError:
        return None
    return newest


def is_recent(age_seconds: "float | None", window_seconds: float) -> bool:
    """True when ``age`` is known and within the window.

    A future-dated mtime (negative age, e.g. clock skew) counts as live — the safe
    direction for a guard whose job is to avoid touching an active worktree.
    """
    return age_seconds is not None and age_seconds <= window_seconds


def worktree_session_is_live(
    worktree_path: "str | os.PathLike[str]",
    *,
    projects_root: "str | os.PathLike[str] | None" = None,
    window_seconds: float = DEFAULT_LIVENESS_WINDOW_SECONDS,
    now: "float | None" = None,
) -> bool:
    """True when ``worktree_path`` has transcript activity within ``window_seconds``.

    Fail-safe: an unresolvable/empty transcript dir yields ``False`` (treated as not
    live, i.e. eligible) — identical to the pre-guard behavior for genuinely-idle
    worktrees. Only recently-active worktrees gain protection. ``now`` / ``projects_root``
    are injectable for offline tests.
    """
    root = Path(projects_root) if projects_root is not None else default_projects_root()
    tdir = transcript_dir_for(worktree_path, root)
    if tdir is None:
        return False
    mtime = newest_jsonl_mtime(tdir)
    if mtime is None:
        return False
    current = now if now is not None else time.time()
    return is_recent(current - mtime, window_seconds)
