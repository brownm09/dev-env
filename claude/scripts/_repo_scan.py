#!/usr/bin/env python3
"""Shared directory-scan helper: discover primary git repos directly under a directory.

Backs the `--scan-dir` mode of prune-merged-worktrees.py, reclaim-worktree-disk.py, and
reconcile-project-board.py — extracted from three near-identical copies. Flagged as a
deferred follow-up in ADR-070 (see "Alternatives Considered" and "Consequences"); dev-env
issue #471 tracked doing it. See ADR-072 for rationale.

Mirrors how _hookutil.py / _journal_shards.py / _hookio.py were extracted for the hook
families. Imported the same way: a sibling module in scripts/ that the `pyw -3` hook
launcher (which puts the script's own directory on sys.path) and the test harness
(sys.path.insert(0, scripts_dir)) both resolve.

Usage:
    from _repo_scan import find_git_repos

    repos = find_git_repos(scan_dir)
    if repos is None:
        ...  # scan_dir itself unreadable (missing / no permission)
    elif not repos:
        ...  # scanned fine, zero repos found
    else:
        ...  # one or more primary repo paths
"""
from __future__ import annotations

import os
import sys


def find_git_repos(scan_dir: str) -> list[str] | None:
    """Return paths of primary git repos (with a .git directory) directly under scan_dir,
    or None if scan_dir itself could not be scanned (missing / no permission) — distinct
    from an empty list, which means the scan succeeded and simply found zero repos.

    A worktree's .git is a file, not a directory, so worktrees are excluded automatically.
    A top-level entry that is itself a symlink/junction to a directory is also excluded
    (follow_symlinks=False) — deliberate, so callers never double-scan a repo reachable
    through both its real path and an alias under the same scan directory."""
    repos: list[str] = []
    try:
        with os.scandir(scan_dir) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
    except OSError as exc:
        # OSError covers PermissionError, FileNotFoundError, NotADirectoryError (scan_dir
        # is a file, not a directory), and any other OS-level reason the path is unusable —
        # deliberately broad, matching the "or None if scan_dir itself could not be scanned"
        # contract above. ValueError/TypeError (a non-string argument, an embedded null byte)
        # are not caught here — those indicate a caller bug and should propagate.
        print(f"WARNING: cannot scan {scan_dir}: {exc}", file=sys.stderr)
        return None
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if os.path.isdir(os.path.join(entry.path, ".git")):
            repos.append(entry.path)
    return repos
