#!/usr/bin/env python3
"""Detect whether a git worktree (or, for a third caller, a canonical checkout) has a live
Claude Code session.

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

**Third caller, different fail direction (dev-env#966 / ADR-130).**
``session-start-sync.py`` calls ``worktree_session_is_live()`` too, but *in-process* (as
one of the sessions that could match) and against a **canonical repo root**, not
necessarily a worktree path — this is why ``exclude_session_id`` exists (see
``newest_jsonl_mtime``'s docstring). The fail direction this function documents as "safe"
is caller-dependent: for prune/reclaim, ``False`` (not live) means "go ahead and clean up,"
so a missing/unmatched transcript dir erring toward ``False`` only risks under-protecting an
idle worktree — the existing behavior. For ``session-start-sync.py``, ``False`` means "no
other session — go ahead and auto-mutate a shared canonical checkout," the opposite
polarity; a canonical's basename can never satisfy ``transcript_dirs_for``'s suffix-fallback
match (``-worktrees-<basename>``), so an absent exact-slug dir there returns ``False`` with
no signal that the check was actually inconclusive rather than genuinely negative. Callers
in this in-process, gate-not-skip position should treat that ambiguity as a known limitation,
not as confirmation.

This module is import-only (no ``_winsubp``, no subprocess, no ``main()``) so its helpers
unit-test offline. It is **policy-free** — each caller passes its own window constant
(removal severs the session ⇒ a long window; stripping ``node_modules`` is self-healing
⇒ a short window that keeps disk reclamation aggressive; ``session-start-sync.py`` uses a
much shorter window still — see its own module docstring).
"""
import os
import time
from pathlib import Path

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

    Two paths differing only by ``.`` vs ``-`` in a component collide to one slug — but so
    do they in Claude Code's own ``projects/`` naming, so both sessions share one transcript
    dir and the newest-mtime check protects whichever is live (over-protect — safe).
    """
    s = str(path)
    for ch in _SLUG_CHARS:
        s = s.replace(ch, "-")
    return s


def default_projects_root() -> Path:
    """The ``~/.claude/projects`` directory.

    Honors ``CLAUDE_CONFIG_DIR``; it must point at a valid Claude Code config dir (the same
    one Claude Code writes transcripts under). If it is misconfigured, transcript dirs are
    unresolvable and worktrees read as not-live — but Claude Code itself would be equally
    broken, so the misconfiguration is not silent in practice.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".claude"
    return root / "projects"


def transcript_dirs_for(
    worktree_path: "str | os.PathLike[str]",
    projects_root: "str | os.PathLike[str]",
) -> "list[Path]":
    """All transcript directories that could belong to ``worktree_path``.

    Common case: the exact encoded-slug dir exists ⇒ ``[that dir]`` — definitive, no scan.

    Fallback (exact dir absent, e.g. a future Claude Code encoding change): every
    ``projects/`` subdir whose name ends with ``-worktrees-<basename>``. The worktree
    basename is a globally-unique random slug, so this re-finds the transcript across an
    encoding tweak. The caller takes the newest mtime **across all** returned dirs, so even
    if a same-basename dir from another repo is also matched, a live transcript for the real
    worktree still protects it (over-protect — the safe direction). The ``os.scandir`` of
    ``projects/`` (potentially hundreds of dirs) runs **only** on this rare fallback path.
    """
    root = Path(projects_root)
    exact = root / encode_project_slug(worktree_path)
    if exact.is_dir():
        return [exact]
    base = Path(str(worktree_path)).name
    if not base:
        return []
    suffix = f"-worktrees-{base}"
    matches: "list[Path]" = []
    try:
        for entry in os.scandir(root):
            # follow_symlinks=False: only ever match real directories (mirrors the sibling
            # scripts' find_git_repos), never a symlink planted under projects/.
            if entry.is_dir(follow_symlinks=False) and entry.name.endswith(suffix):
                matches.append(Path(entry.path))
    except OSError:
        pass
    return matches


def newest_jsonl_mtime(
    transcript_dir: "str | os.PathLike[str]",
    *,
    exclude_session_id: "str | None" = None,
) -> "float | None":
    """Newest mtime (epoch secs) among ``*.jsonl`` under ``transcript_dir``, recursively.

    Recursive so a quiet top-level transcript with an active ``<uuid>/subagents/*.jsonl``
    still reads as live. Returns ``None`` when the dir is absent or holds no readable
    ``.jsonl``.

    ``exclude_session_id``, when given, skips any ``*.jsonl`` whose filename stem (the session
    UUID -- see the module docstring's transcript-path convention) equals it, OR that lives
    under a directory component named exactly ``exclude_session_id`` (a subagent transcript,
    ``<session-uuid>/subagents/<subagent-uuid>.jsonl`` -- its own stem is the *subagent's* id,
    never the session id, so a stem-only match misses it even though the recursive ``rglob``
    above exists specifically to find it; a session's own subagent activity must exclude the
    same as the session's own top-level transcript, or a hook re-firing mid-session after
    spawning a subagent would read its own subagent as a foreign concurrent session --
    dev-env#966 review finding). Lets a caller running *as* one of the sessions that would
    otherwise match (e.g. a hook asking "is some OTHER session live in my own checkout")
    exclude its own transcript (and its own subagents') instead of always reading itself as
    live (dev-env#966 / ADR-130). The existing callers (prune-merged-worktrees.py,
    reclaim-worktree-disk.py) run out-of-process, so this gap never mattered to them; the
    default ``None`` preserves their behavior exactly.
    """
    newest: "float | None" = None
    try:
        for p in Path(transcript_dir).rglob("*.jsonl"):
            if exclude_session_id is not None and (
                p.stem == exclude_session_id or exclude_session_id in p.parts
            ):
                continue
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


def parse_liveness_window_seconds(argv: "list[str]", default: int) -> int:
    """Parse ``--liveness-window-min N`` (minutes) from ``argv`` into seconds.

    Returns ``default`` when the flag is absent. Raises ``ValueError`` on a missing,
    non-numeric, or **negative** argument — a negative window would make ``is_recent()``
    ``False`` for every real (positive-age) transcript, silently disabling the guard and
    re-exposing the very bug this protects against. ``0`` is allowed (an explicit "protect
    only a now/future transcript", i.e. effectively off). Pure helper: callers translate the
    ``ValueError`` to ``sys.exit`` so the message is consistent across both scripts.
    """
    for i, arg in enumerate(argv):
        if arg == "--liveness-window-min":
            if i + 1 >= len(argv):
                raise ValueError("--liveness-window-min requires an argument")
            try:
                minutes = float(argv[i + 1])
            except ValueError:
                raise ValueError("--liveness-window-min requires a numeric argument")
            if minutes < 0:
                raise ValueError("--liveness-window-min must be >= 0")
            return int(minutes * 60)
    return default


def worktree_session_is_live(
    worktree_path: "str | os.PathLike[str]",
    *,
    window_seconds: float,
    projects_root: "str | os.PathLike[str] | None" = None,
    now: "float | None" = None,
    exclude_session_id: "str | None" = None,
) -> bool:
    """True when ``worktree_path`` has transcript activity within ``window_seconds``.

    Takes the newest ``.jsonl`` mtime across every candidate transcript dir (see
    ``transcript_dirs_for``). Fail-safe: no candidate dir / no readable ``.jsonl`` yields
    ``False`` (treated as not live, i.e. eligible) — identical to the pre-guard behavior for
    genuinely-idle worktrees, so cleanup of abandoned worktrees keeps working. Only
    recently-active worktrees gain protection. ``window_seconds`` is required (each caller
    owns its policy); ``now`` / ``projects_root`` are injectable for offline tests.

    ``exclude_session_id`` is threaded through to ``newest_jsonl_mtime`` on every candidate
    dir — see that function's docstring. Defaults to ``None`` (no exclusion), preserving prior
    behavior for the existing prune/reclaim callers exactly.
    """
    root = Path(projects_root) if projects_root is not None else default_projects_root()
    newest: "float | None" = None
    for tdir in transcript_dirs_for(worktree_path, root):
        mtime = newest_jsonl_mtime(tdir, exclude_session_id=exclude_session_id)
        if mtime is not None and (newest is None or mtime > newest):
            newest = mtime
    if newest is None:
        return False
    current = now if now is not None else time.time()
    return is_recent(current - newest, window_seconds)
