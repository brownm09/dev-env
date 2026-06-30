#!/usr/bin/env python3
"""Shared utilities for the Stop / UserPromptSubmit hook family.

Per-session sentinel helpers (used by posttooluse-inert-advisory.py and
reconcile-open-prs.py) and transcript locate (used by token-tracker.py and
posttooluse-inert-advisory.py) — extracted from the near-verbatim copies that
existed in each script before this module.

Mirrors how _hookio.py was extracted for PostToolUse Bash hooks (ADR-050).
See ADR-064 for rationale.

Imported the same way as _hookio: a sibling module in scripts/ that the
`pyw -3` hook launcher (which puts the script's own directory on sys.path)
and the test harness (sys.path.insert(0, scripts_dir)) both resolve.

Usage:
    import _hookutil

    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)
    path = _hookutil.sentinel_path(SENTINEL_PREFIX, session_id)
    tpath = _hookutil.find_transcript(session_id)
"""
from __future__ import annotations

import time
from pathlib import Path

SCRATCH = Path.home() / ".claude" / "scratch"
PROJECTS = Path.home() / ".claude" / "projects"
MAX_AGE_DAYS = 30


def cleanup_stale_sentinels(prefix: str, scratch: Path | None = None) -> None:
    """Remove per-session sentinel files whose names start with *prefix* and whose
    mtime is older than MAX_AGE_DAYS.  Swallows all I/O errors — the cleanup is
    best-effort and must never block a hook.  *scratch* overrides SCRATCH (used by
    tests to isolate against the real ~/.claude/scratch directory)."""
    root = scratch if scratch is not None else SCRATCH
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    try:
        flags = list(root.glob(f"{prefix}*.flag"))
    except Exception:
        return
    # Guard each file independently — a single stat()/unlink() failure (race,
    # permission) must not abort cleanup of the remaining sentinels.
    for f in flags:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except Exception:
            continue


def sentinel_path(prefix: str, session_id: str, scratch: Path | None = None) -> Path:
    """Return ``scratch / f"{prefix}{session_id}.flag"``.

    *scratch* overrides SCRATCH (used by tests).  The existence of this file is the
    per-session "already ran" signal; callers create it with ``.write_text("")`` and
    check it with ``.exists()``."""
    root = scratch if scratch is not None else SCRATCH
    return root / f"{prefix}{session_id}.flag"


def find_transcript(session_id: str, projects: Path | None = None) -> Path | None:
    """Return the JSONL transcript for *session_id*, or ``None`` when not found.

    Searches all project directories under ``projects`` (defaults to
    ``~/.claude/projects/``).  *projects* is injectable for offline tests.
    """
    root = projects if projects is not None else PROJECTS
    # Session ids are unique, so the first match is the answer — next() short-circuits
    # instead of materializing every JSONL under ~/.claude/projects/.
    return next(root.glob(f"**/{session_id}.jsonl"), None)
