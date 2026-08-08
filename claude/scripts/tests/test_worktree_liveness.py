#!/usr/bin/env python3
"""Unit tests for _worktree_liveness.py and the prune/reclaim window constants.

Covers the session-liveness guard that stops prune-merged-worktrees.py /
reclaim-worktree-disk.py from severing an active Claude session (dev-env#384, ADR-051):

  1. encode_project_slug()  — the ':'/'\\'/'/'/'.' -> '-' transcript-dir encoding (pure).
  2. is_recent()            — the recency boundary, incl. unknown and future mtimes (pure).
  3. transcript_dirs_for()  — exact-slug match (alone) + unique-basename suffix fallback + miss,
                              plus the newest-across-all-matches safety property.
  4. newest_jsonl_mtime()   — recursive newest *.jsonl (incl. subagents/), ignores non-.jsonl.
  5. parse_liveness_window_seconds() — default/valid/missing/non-numeric/negative (pure).
  6. worktree_session_is_live() — live / stale / no-session verdicts (now + root injected).
  7. The per-script default windows: prune = 24h, reclaim = 6h.

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


def test_transcript_dirs_exact_match() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = r"C:\Users\brown\Git\dev-env\.claude\worktrees\foo-bar-abc123"
        slug = wl.encode_project_slug(wt)
        (root / slug).mkdir()
        # A same-basename decoy must NOT be returned when the exact slug exists.
        (root / "OTHER--claude-worktrees-foo-bar-abc123").mkdir()
        got = wl.transcript_dirs_for(wt, root)
        if got != [root / slug]:
            raise AssertionError(f"exact match returned {got}, expected [{root / slug}]")
    return "exact encoded-slug dir resolved alone (no scan when it exists)"


def test_transcript_dirs_suffix_fallback() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/zesty-mayer-9f9f9f"
        # No exact-slug dir; instead a differently-prefixed dir ending in the unique base.
        decoy = root / "TOTALLY-DIFFERENT-PREFIX--claude-worktrees-zesty-mayer-9f9f9f"
        decoy.mkdir()
        (root / "unrelated-dir").mkdir()  # must not match
        got = wl.transcript_dirs_for(wt, root)
        if got != [decoy]:
            raise AssertionError(f"suffix fallback returned {got}, expected [{decoy}]")
    return "basename-suffix fallback resolves transcript dir when exact slug is absent"


def test_transcript_dirs_missing_returns_empty() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "C--Users-brown-Git-other--claude-worktrees-someone-else-000000").mkdir()
        got = wl.transcript_dirs_for("C:/Users/brown/Git/dev-env/.claude/worktrees/no-match-111111", root)
        if got != []:
            raise AssertionError(f"expected [] for a worktree with no transcript dir, got {got}")
    return "no exact slug and no suffix match -> [] (eligible, preserves cleanup)"


def test_fallback_takes_newest_across_matches() -> str:
    # The A1 safety property: exact slug absent (encoding drift) and TWO same-basename
    # dirs match — a stale one (wrong repo) and a fresh one (the live worktree). Liveness
    # must take the NEWEST across all matches, so the live worktree is still protected.
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/twin-hopper-abc123"
        # Neither equals the exact slug (so the fallback fires); both end with the unique
        # `-worktrees-<base>` suffix. `stale` = a same-basename dir from another repo;
        # `fresh` = the real worktree's dir under a drifted *prefix* encoding.
        stale = root / "C--Users-brown-Git-OTHER--claude-worktrees-twin-hopper-abc123"
        fresh = root / "DRIFTED~PREFIX--claude-worktrees-twin-hopper-abc123"
        for d, mtime in ((stale, now - 90_000), (fresh, now - 30)):  # 25h stale vs 30s fresh
            d.mkdir()
            f = d / "s.jsonl"
            f.write_text("{}")
            os.utime(f, (mtime, mtime))
        # No exact-slug dir exists, so both are fallback matches; newest (fresh) wins.
        live = wl.worktree_session_is_live(wt, projects_root=root, window_seconds=86400, now=now)
        if not live:
            raise AssertionError("newest-across-matches must protect the live worktree even with a stale same-basename decoy")
    return "fallback takes newest mtime across all same-basename matches (over-protect, safe)"


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


def test_newest_jsonl_mtime_excludes_matching_session_id() -> str:
    # dev-env#966 / ADR-130: session-start-sync.py runs AS one of the sessions that would
    # otherwise match, so it must be able to exclude its own transcript when asking "is some
    # OTHER session live here."
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        excluded = d / "11111111-1111-1111-1111-111111111111.jsonl"
        excluded.write_text("{}")
        os.utime(excluded, (9_000.0, 9_000.0))  # newest of the two, but excluded
        other = d / "22222222-2222-2222-2222-222222222222.jsonl"
        other.write_text("{}")
        os.utime(other, (5_000.0, 5_000.0))
        got = wl.newest_jsonl_mtime(d, exclude_session_id="11111111-1111-1111-1111-111111111111")
        if got != 5_000.0:
            raise AssertionError(f"expected 5000.0 (the non-excluded file), got {got}")
    return "the excluded session_id's .jsonl is skipped even when it is the newest"


def test_newest_jsonl_mtime_excludes_nested_subagent_transcript() -> str:
    # dev-env#966 review finding: a subagent transcript's own filename stem is the
    # SUBAGENT's uuid, never the session id -- a stem-only exclusion filter misses it even
    # though the recursive rglob exists specifically to find nested subagent activity.
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        session_id = "66666666-6666-6666-6666-666666666666"
        subagent = d / session_id / "subagents" / "77777777-7777-7777-7777-777777777777.jsonl"
        subagent.parent.mkdir(parents=True)
        subagent.write_text("{}")
        os.utime(subagent, (9_000.0, 9_000.0))  # newest, but must still be excluded
        other = d / "88888888-8888-8888-8888-888888888888.jsonl"
        other.write_text("{}")
        os.utime(other, (5_000.0, 5_000.0))
        got = wl.newest_jsonl_mtime(d, exclude_session_id=session_id)
        if got != 5_000.0:
            raise AssertionError(
                f"expected 5000.0 (the non-excluded file); nested subagent transcript under "
                f"the excluded session's own directory must also be excluded, got {got}"
            )
    return "a nested <session-id>/subagents/*.jsonl is excluded too, not just a top-level stem match"


def test_newest_jsonl_mtime_exclude_none_preserves_behavior() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        f = d / "session.jsonl"
        f.write_text("{}")
        os.utime(f, (5_000.0, 5_000.0))
        got_default = wl.newest_jsonl_mtime(d)
        got_explicit_none = wl.newest_jsonl_mtime(d, exclude_session_id=None)
        if got_default != 5_000.0 or got_explicit_none != 5_000.0:
            raise AssertionError(
                f"omitted vs explicit None must both be 5000.0: {got_default}, {got_explicit_none}"
            )
    return "omitting exclude_session_id and passing None explicitly are identical (regression safety)"


def _make_session(root: Path, worktree: str, mtime: float) -> None:
    slug = wl.encode_project_slug(worktree)
    sd = root / slug
    sd.mkdir(parents=True)
    f = sd / "11111111-2222-3333-4444-555555555555.jsonl"
    f.write_text("{}")
    os.utime(f, (mtime, mtime))


def _make_session_file(root: Path, worktree: str, session_id: str, mtime: float) -> None:
    """Like `_make_session`, but with a caller-chosen `session_id` filename -- needed to test
    `exclude_session_id`, which the fixed-UUID `_make_session` helper can't exercise."""
    slug = wl.encode_project_slug(worktree)
    sd = root / slug
    sd.mkdir(parents=True, exist_ok=True)
    f = sd / f"{session_id}.jsonl"
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


def test_session_is_live_excludes_own_session_id() -> str:
    # dev-env#966 / ADR-130: a hook running AS the only session present must be able to
    # exclude its own transcript, or it would always read itself as live.
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/self-check-ddd444"
        my_session = "33333333-3333-3333-3333-333333333333"
        _make_session_file(root, wt, my_session, now - 5)  # 5s ago -- would read as live
        live = wl.worktree_session_is_live(
            wt, projects_root=root, window_seconds=3600, now=now, exclude_session_id=my_session,
        )
        if live:
            raise AssertionError("excluding the only session present must not read as live")
    return "excluding the sole session's own transcript -> not live (asking 'is another session here')"


def test_session_is_live_still_detects_other_session_when_excluding_self() -> str:
    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wt = "C:/Users/brown/Git/dev-env/.claude/worktrees/two-sessions-eee555"
        my_session = "44444444-4444-4444-4444-444444444444"
        other_session = "55555555-5555-5555-5555-555555555555"
        _make_session_file(root, wt, my_session, now - 5)     # my own, recent
        _make_session_file(root, wt, other_session, now - 10)  # a genuinely different session, also recent
        live = wl.worktree_session_is_live(
            wt, projects_root=root, window_seconds=3600, now=now, exclude_session_id=my_session,
        )
        if not live:
            raise AssertionError("a second, non-excluded recent session must still read as live")
    return "excluding own session_id still detects a genuinely different concurrent session"


def test_parse_liveness_window_seconds() -> str:
    DEF = 999  # sentinel default so "absent -> default" is unambiguous
    # Absent flag -> default.
    if wl.parse_liveness_window_seconds(["--scan-dir", "X"], DEF) != DEF:
        raise AssertionError("absent flag must return the default")
    # Valid minutes -> seconds.
    if wl.parse_liveness_window_seconds(["--liveness-window-min", "30"], DEF) != 1800:
        raise AssertionError("30 min must parse to 1800s")
    # Zero is allowed (explicit near-disable).
    if wl.parse_liveness_window_seconds(["--liveness-window-min", "0"], DEF) != 0:
        raise AssertionError("0 must be accepted as 0s")
    # Missing argument, non-numeric, and negative each raise ValueError.
    for bad, label in (
        (["--liveness-window-min"], "missing argument"),
        (["--liveness-window-min", "abc"], "non-numeric"),
        (["--liveness-window-min", "-5"], "negative (would silently disable the guard)"),
    ):
        try:
            wl.parse_liveness_window_seconds(bad, DEF)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {label}: {bad}")
    return "parser: absent->default, 30->1800, 0 ok; missing/non-numeric/negative raise"


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
        ("transcript_dirs_for exact match", test_transcript_dirs_exact_match),
        ("transcript_dirs_for suffix fallback", test_transcript_dirs_suffix_fallback),
        ("transcript_dirs_for missing -> []", test_transcript_dirs_missing_returns_empty),
        ("fallback takes newest across matches", test_fallback_takes_newest_across_matches),
        ("newest_jsonl_mtime recurses + ignores non-jsonl", test_newest_jsonl_mtime_recurses_and_ignores_nonjsonl),
        ("newest_jsonl_mtime empty -> None", test_newest_jsonl_mtime_empty_returns_none),
        ("newest_jsonl_mtime excludes matching session_id", test_newest_jsonl_mtime_excludes_matching_session_id),
        ("newest_jsonl_mtime excludes nested subagent transcript", test_newest_jsonl_mtime_excludes_nested_subagent_transcript),
        ("newest_jsonl_mtime exclude=None preserves behavior", test_newest_jsonl_mtime_exclude_none_preserves_behavior),
        ("session live when recent", test_session_is_live_when_recent),
        ("session stale outside window", test_session_is_stale_outside_window),
        ("session not live without transcript", test_session_not_live_without_transcript),
        ("session_is_live excludes own session_id", test_session_is_live_excludes_own_session_id),
        ("session_is_live still detects other session when excluding self", test_session_is_live_still_detects_other_session_when_excluding_self),
        ("parse --liveness-window-min (default/valid/error)", test_parse_liveness_window_seconds),
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
