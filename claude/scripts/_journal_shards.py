#!/usr/bin/env python3
"""Shared reader for the journal's open-PR tracking files (ADR-056 / ADR-057).

ADR-056 reshaped open-PR tracking from a single shared `open-prs.jsonl` into per-PR
shards `sessions/<project>/open-prs/<N>.json`, with the legacy single file still read
during the back-compat transition. Two hooks consume that tracking and must agree on how
the shards are enumerated and parsed:

  - ``reconcile-open-prs.py`` walks the shards to ``unlink`` the ones whose PRs merged or
    closed — it needs the **file paths**.
  - ``post-compact.py`` reads the shards to remind Claude to ``/review`` an open PR — it
    needs the **parsed entries**, deduped by PR number.

Before this module each hook carried its own copy of "glob ``open-prs/*.json``, sort
numerically by PR number, tolerate malformed JSON, then fold in the legacy file". Two
copies of transition-period logic drift — and already did once: the shard sort key
differed between the two (lexical vs. numeric) until it was reconciled in PR #394's
review. Centralising the read here means both hooks resolve the shard set identically, and
gives **one** place to delete the legacy branch when the back-compat window closes (pairs
with the engineering-journal#128 data migration).

Imported the same way as ``_winsubp`` / ``_hookio`` / ``_worktree_liveness``: a sibling
module in ``scripts/`` that the ``pyw -3`` hook launcher (which puts the script's own
directory on ``sys.path``) and the test harness (``sys.path.insert(0, scripts_dir)``) both
resolve.

This module is import-only — no ``_winsubp``, no subprocess, no ``main()`` — so its helpers
unit-test offline (``tests/test_journal_shards.py``). See ADR-056 (the sharding) and
ADR-057 (this extraction).
"""

from __future__ import annotations

import json
from pathlib import Path


def shard_pr_number(path: Path) -> int | None:
    """Parse the PR number from an ``open-prs/<N>.json`` shard filename.

    Returns ``None`` for any non-numeric stem so stray files (e.g. an ``index.json``) are
    ignored rather than mistaken for a PR shard. A PR shard is identified **by its numeric
    filename** — that is the ADR-056 key (``open-prs/<N>.json``).
    """
    try:
        return int(path.stem)
    except (ValueError, TypeError):
        return None


def iter_pr_shards(shard_dir: Path) -> list[tuple[Path, dict]]:
    """Enumerate and parse the per-PR shards under ``shard_dir``, numerically sorted.

    Returns a list of ``(path, entry)`` pairs — the path so a caller can ``unlink`` the
    shard (``reconcile-open-prs.py``) and the parsed object so another can read it
    (``post-compact.py``). The list is materialised (not a lazy generator) before return,
    so a caller may safely ``unlink`` shards while iterating the result.

    Conservative on every malformed input — a shard is **included only** when it is a
    numeric-named ``*.json`` that parses to a JSON object:

      - non-numeric stems (``index.json``, ``bad.json``) are skipped (not a PR shard);
      - shards sort by PR number ascending (PR 2 before PR 10), not lexically;
      - unparseable JSON, a non-UTF-8 file (``UnicodeDecodeError``), or any ``OSError``
        reading the file is skipped, left for a human;
      - a parsed **non-object** value (a JSON list/scalar) is skipped — without this a
        downstream ``entry.get(...)`` would raise into a context-dropping guard (see ADR-057).

    A missing / non-directory ``shard_dir`` yields ``[]``, so callers may invoke it
    unconditionally.
    """
    if not shard_dir.is_dir():
        return []

    # Filter to numeric-named shards first, then sort by PR number — so the sort is over
    # real PR numbers (ints), never a lexical compare of filenames ('10' < '2').
    numbered: list[tuple[int, Path]] = []
    for path in shard_dir.glob("*.json"):
        n = shard_pr_number(path)
        if n is None:
            continue  # not a PR shard (non-numeric stem) — ignore
        numbered.append((n, path))
    numbered.sort(key=lambda np: np[0])

    result: list[tuple[Path, dict]] = []
    for _n, path in numbered:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue  # leave unparseable shards for a human
        if not isinstance(entry, dict):
            continue  # a non-object shard can't be a tracking entry — skip defensively
        result.append((path, entry))
    return result


def read_legacy_entries(path: Path) -> list[dict]:
    """Read the legacy single-file ``open-prs.jsonl`` — one JSON object per line.

    Returns the parsed objects in file order. Tolerant of everything ``iter_pr_shards`` is:
    blank lines, unparseable lines, and non-object lines are skipped; a missing,
    unreadable, or non-UTF-8 file yields ``[]`` (so callers need not guard
    ``path.exists()`` first). This is the pre-ADR-056 format that drains to empty as its
    PRs merge.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    entries: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries
