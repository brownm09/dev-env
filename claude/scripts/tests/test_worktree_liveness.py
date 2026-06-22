#!/usr/bin/env python3
"""Unit tests for _worktree_liveness.py and the prune/reclaim window constants.

Covers the session-liveness guard that stops prune-merged-worktrees.py /
reclaim-worktree-disk.py from severing an active Claude session (dev-env#384, ADR-051):

  1. encode_project_slug()  — the ':'/'\\'/'/'/'.' -> '-' transcript-dir encoding (pure).
  2. is_recent()            — the recency boundary, incl. unknown and future mtimes (pure).
  3. transcript_dir_for()   — exact-slug match + unique-basename suffix fallback + miss.
  4. newest_jsonl_mtime()   — recursive newest *.jsonl (incl. subagents/), ignores non-.jsonl.
  5. worktree_session_is_live() — live / stale / no-session verdicts (now + root injected).
  6. The per-script default windows: prune = 24h, reclaim = 6h.

Filesystem helpers are exercised against tmp dirs with os.utime-stamped mtimes (matching
test_reclaim_worktree_disk.py) — no live session, no network, fully offline. The git-driven
prune/reclaim loops are exercised by --dry-run in the PR, not here.

Usage:
    py -3 claude/scripts/tests/test_worktree_liveness.py

Exit 0 = all pass.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]

# _worktree_liveness has a valid module name; ensure scripts/ is importable for it and for
# the _winsubp the hyphenated script modules pull in.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _worktree_liveness as wl  # noqa: E402


def _load_script(stem: str):
    """Load a hyphenated-filename script module by path (it guards main() by __main__)."""
    path = SCRIPTS_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_encode_project_slug() -> str:
    cases = [
        # Windows spelling (backslashes) — the verified real dir name.
        (
            r"C:\Users\brown\Git\dev-env\.claude\worktrees\agitated-stonebraker-2156f6",
            "C--Users-brown-Git-dev-env--claude-worktrees-agitated-stonebraker-2156f6",
        ),
        # POSIX-ish spelling of the same worktree must encode identically.
        (
            "C:/Users/brown/Git/dev-env/.claude/worktrees/pensive-taussig-448bc7",
            "C--Users-brown-Git-dev-env--claude-worktrees-pensive-taussig-448bc7",
        ),
        # A dotted repo name: every '.' becomes '-'.
        ("C:/Users/brown/Git/my.app", "C--Users-brown-Git-my-app"),
    ]
    for path, expected in cases:
        got = wl.encode_project_slug(path)
        if got != expected:
            raise AssertionError(f"encode_project_slug({path!r}) = {got!r}, expected {expected!r}")
    return f"{len(cases)} paths encoded; Windows and POSIX spellings agree"


def test_is_recent_boundaries() -> str:
    cases = [
        # (age_seconds, window, expected)
        (None, 3600, False),   # unknown mtime -> not live (eligible)
        (0, 3600, True),       # just now
        (3600, 3600, True),    # exactly at the boundary is still live
        (3601, 3600, False),   # one second past -> stale
        (-5, 3600, True),      # future-dated mtime (clock skew) -> live (safe direction)
    ]
    for age, window, expected in cases:
        got = wl.is_recent(age, window)
        if got != expected:
            raise AssertionError(f"is_recent({age}, {window}) = {got}, expected {expected}")
    return f"{len(cases)} recency boundaries correct (incl. None and future mtime)"


def test_transcript_dir_exact_match() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = r"C:\Users\brown\Git\dev-env\.claude\worktrees\foo-bar-abc123"
        slug = wl.encode_project_slug(wt)
        (root / slug).mkdir()
        got = wl.transcript_dir_for(wt, root)
        if got != root / slug:
            raise AssertionError(f"exact match returned {got}, expected {root / slug}")
    return "exact encoded-slug directory resolved"


def test_transcript_dir_suffix_fallback() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/zesty-mayer-9f9f9f"
        # No exact-slug dir; instead a differently-prefixed dir ending in the unique base.
        decoy = root / "TOTALLY-DIFFERENT-PREFIX--claude-worktrees-zesty-mayer-9f9f9f"
        decoy.mkdir()
        (root / "unrelated-dir").mkdir()  # must not match
        got = wl.transcript_dir_for(wt, root)
        if got != decoy:
            raise AssertionError(f"suffix fallback returned {got}, expected {decoy}")
    return "basename-suffix fallback resolves transcript dir when exact slug is absent"


def test_transcript_dir_missing_returns_none() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "C--Users-brown-Git-other--claude-worktrees-someone-else-000000").mkdir()
        got = wl.transcript_dir_for("C:/Users/brown/Git/dev-env/.claude/worktrees/no-match-111111", root)
        if got is not None:
            raise AssertionError(f"expected None for a worktree with no transcript dir, got {got}")
    return "no exact slug and no suffix match -> None (eligible, preserves cleanup)"


def test_newest_jsonl_mtime_recurses_and_ignores_nonjsonl() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        top = d / "session.jsonl"
        top.write_text("{}")
        os.utime(top, (1_000.0, 1_000.0))  # old
        nested = d / "uuid" / "subagents" / "child.jsonl"
        nested.parent.mkdir(parents=True)
        nested.write_text("{}")
        os.utime(nested, (5_000.0, 5_000.0))  # newest — a busy subagent
        decoy = d / "uuid" / "notes.txt"
        decoy.write_text("x")
        os.utime(decoy, (9_999.0, 9_999.0))  # newer but not .jsonl -> ignored
        got = wl.newest_jsonl_mtime(d)
        if got != 5_000.0:
            raise AssertionError(f"newest_jsonl_mtime = {got}, expected 5000.0 (nested subagent .jsonl)")
    return "newest *.jsonl found recursively (subagents/); non-.jsonl ignored"


def test_newest_jsonl_mtime_empty_returns_none() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        got = wl.newest_jsonl_mtime(Path(tmp))
        if got is not None:
            raise AssertionError(f"expected None for a dir with no .jsonl, got {got}")
    return "directory with no .jsonl -> None"


def _make_session(root: Path, worktree: str, mtime: float) -> None:
    slug = wl.encode_project_slug(worktree)
    sd = root / slug
    sd.mkdir(parents=True)
    f = sd / "11111111-2222-3333-4444-555555555555.jsonl"
    f.write_text("{}")
    os.utime(f, (mtime, mtime))


def test_session_is_live_when_recent() -> str:
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/live-one-aaa111"
        _make_session(root, wt, now - 5)  # 5s ago
        if not wl.worktree_session_is_live(wt, projects_root=root, window_seconds=3600, now=now):
            raise AssertionError("a 5s-old transcript must read as live within a 1h window")
    return "recent transcript (5s) within window -> live"


def test_session_is_stale_outside_window() -> str:
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/stale-one-bbb222"
        _make_session(root, wt, now - 7200)  # 2h ago
        if wl.worktree_session_is_live(wt, projects_root=root, window_seconds=3600, now=now):
            raise AssertionError("a 2h-old transcript must be stale for a 1h window")
    return "stale transcript (2h) outside 1h window -> not live"


def test_session_not_live_without_transcript() -> str:
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/never-ran-ccc333"
        got = wl.worktree_session_is_live(wt, projects_root=root, window_seconds=3600, now=now)
        if got:
            raise AssertionError("a worktree with no transcript dir must not read as live")
    return "no transcript dir -> not live (eligible; fail-safe preserves cleanup)"


def test_script_default_windows() -> str:
    prune = _load_script("prune-merged-worktrees")
    reclaim = _load_script("reclaim-worktree-disk")
    if prune.LIVENESS_WINDOW_SECONDS != 24 * 60 * 60:
        raise AssertionError(
            f"prune window = {prune.LIVENESS_WINDOW_SECONDS}, expected {24 * 60 * 60} (24h)"
        )
    if reclaim.LIVENESS_WINDOW_SECONDS != 6 * 60 * 60:
        raise AssertionError(
            f"reclaim window = {reclaim.LIVENESS_WINDOW_SECONDS}, expected {6 * 60 * 60} (6h)"
        )
    return "prune default = 24h, reclaim default = 6h (blast-radius-scaled)"


def main() -> int:
    tests = [
        ("encode_project_slug encoding", test_encode_project_slug),
        ("is_recent boundaries", test_is_recent_boundaries),
        ("transcript_dir_for exact match", test_transcript_dir_exact_match),
        ("transcript_dir_for suffix fallback", test_transcript_dir_suffix_fallback),
        ("transcript_dir_for missing -> None", test_transcript_dir_missing_returns_none),
        ("newest_jsonl_mtime recurses + ignores non-jsonl", test_newest_jsonl_mtime_recurses_and_ignores_nonjsonl),
        ("newest_jsonl_mtime empty -> None", test_newest_jsonl_mtime_empty_returns_none),
        ("session live when recent", test_session_is_live_when_recent),
        ("session stale outside window", test_session_is_stale_outside_window),
        ("session not live without transcript", test_session_not_live_without_transcript),
        ("prune=24h / reclaim=6h default windows", test_script_default_windows),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
