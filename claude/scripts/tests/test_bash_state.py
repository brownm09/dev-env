#!/usr/bin/env python3
"""Unit tests for _bash_state.py — per-session repo/branch drift tracking.

_bash_state.py is the shared module backing post-tool-use-cwd-track.py's
per-Bash-call state recording and pre-commit-branch-check.py /
pre-pr-create-check.py / pre-merge-branch-check.py's drift-warning check.
See dev-env#573.

Exercises the pure helpers offline (tmp dirs, injected scratch paths — no
real ~/.claude/scratch). `current_repo_state()` shells out to a single
combined `git rev-parse --show-toplevel --abbrev-ref HEAD` call (used by all
four consuming files instead of each defining its own git-wrapping
functions) and is not covered here (pure-helper convention, matches
_hookutil.py's test suite and this module's own write_state/read_state).

Usage:
    py -3 claude/scripts/tests/test_bash_state.py

Exit 0 = all pass.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "claude" / "scripts"))

import _bash_state


def test_state_path_returns_correct_path() -> str:
    with tempfile.TemporaryDirectory() as root:
        p = _bash_state.state_path("abc123", scratch=Path(root))
        assert p.name == "bash_state_abc123.json", f"unexpected name {p.name!r}"
        assert p.parent == Path(root), f"unexpected parent {p.parent}"
    return "state_path returns scratch / f'bash_state_{session_id}.json'"


def test_state_path_default_scratch() -> str:
    p = _bash_state.state_path("abc123")
    assert p.parent == _bash_state.SCRATCH, f"default parent should be SCRATCH, got {p.parent}"
    return "default scratch is SCRATCH (~/.claude/scratch)"


def test_state_age_seconds_missing_file_returns_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        got = _bash_state.state_age_seconds("nonexistent", scratch=Path(root))
        assert got is None, f"expected None, got {got}"
    return "state_age_seconds returns None when no state file exists yet"


def test_state_age_seconds_reports_elapsed_time() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.write_state("s", "C:/repo", "main", "C:/repo", scratch=scratch)
        past = time.time() - 90
        os.utime(_bash_state.state_path("s", scratch=scratch), (past, past))
        got = _bash_state.state_age_seconds("s", scratch=scratch)
        assert got is not None, "expected a numeric age"
        assert 85 <= got <= 95, f"expected age ~90s, got {got}"
    return "state_age_seconds reports elapsed time since the file's mtime"


def test_state_age_seconds_future_mtime_returns_negative() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.write_state("s", "C:/repo", "main", "C:/repo", scratch=scratch)
        future = time.time() + 120
        os.utime(_bash_state.state_path("s", scratch=scratch), (future, future))
        got = _bash_state.state_age_seconds("s", scratch=scratch)
        assert got is not None and got < 0, f"expected a negative age, got {got}"
    return "state_age_seconds returns a negative float for a future/skewed mtime rather than raising"


def test_state_age_seconds_no_crash_on_unstattable_path() -> str:
    with tempfile.TemporaryDirectory() as root:
        # A file (not a directory) at the scratch path makes the join+stat fail.
        blocked = Path(root) / "blocked"
        blocked.write_text("occupied", encoding="utf-8")
        got = _bash_state.state_age_seconds("s", scratch=blocked)
        assert got is None, f"expected None, got {got}"
    return "state_age_seconds returns None (not a raise) when the scratch path can't be stat'd"


def test_write_then_read_round_trip() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.write_state("sess1", "C:/repo", "feat/x", "C:/repo/sub", scratch=scratch)
        got = _bash_state.read_state("sess1", scratch=scratch)
        assert got == {"repo_root": "C:/repo", "branch": "feat/x", "cwd": "C:/repo/sub"}, got
    return "write_state then read_state round-trips the exact recorded dict"


def test_read_state_missing_file_returns_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        got = _bash_state.read_state("nonexistent", scratch=Path(root))
        assert got is None, f"expected None, got {got}"
    return "read_state returns None when no state file exists yet"


def test_read_state_malformed_json_returns_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.state_path("bad", scratch=scratch).write_text("{not json", encoding="utf-8")
        got = _bash_state.read_state("bad", scratch=scratch)
        assert got is None, f"expected None for malformed JSON, got {got}"
    return "read_state returns None on malformed JSON rather than raising"


def test_read_state_non_dict_json_returns_none() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.state_path("arr", scratch=scratch).write_text("[1, 2, 3]", encoding="utf-8")
        got = _bash_state.read_state("arr", scratch=scratch)
        assert got is None, f"expected None for a JSON array, got {got}"
    return "read_state returns None when the JSON parses but isn't an object"


def test_read_state_non_utf8_returns_none() -> str:
    # dev-env#801: a non-UTF-8 state file makes read_text(encoding="utf-8") raise
    # UnicodeDecodeError (a ValueError), which the pre-fix OSError-only catch let
    # escape, contradicting the docstring ("unreadable file"). Same review-finding
    # shape PR #800 fixed in dev-env-sync.py's read_failure_state. -> None (fresh run).
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.state_path("nonutf8", scratch=scratch).write_bytes(b"\xff\xfe\x00\x9d")
        got = _bash_state.read_state("nonutf8", scratch=scratch)
        assert got is None, f"expected None for non-UTF-8 bytes, got {got}"
    return "read_state returns None on a non-UTF-8 file (UnicodeDecodeError caught)"


def test_cleanup_removes_stale_keeps_fresh() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        old = scratch / "bash_state_old-session.json"
        fresh = scratch / "bash_state_fresh-session.json"
        old.write_text("{}", encoding="utf-8")
        fresh.write_text("{}", encoding="utf-8")
        past = time.time() - (_bash_state.MAX_AGE_DAYS + 1) * 86400
        os.utime(old, (past, past))
        _bash_state.cleanup_stale_state(scratch=scratch)
        assert not old.exists(), "stale state file should have been removed"
        assert fresh.exists(), "fresh state file should be kept"
    return "cleanup_stale_state removes files older than MAX_AGE_DAYS, keeps fresh ones"


def test_cleanup_ignores_non_matching_files() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        other = scratch / "some-other-file.json"
        other.write_text("{}", encoding="utf-8")
        past = time.time() - (_bash_state.MAX_AGE_DAYS + 1) * 86400
        os.utime(other, (past, past))
        _bash_state.cleanup_stale_state(scratch=scratch)
        assert other.exists(), "a file not matching the bash_state_*.json glob must not be removed"
    return "cleanup_stale_state only removes files matching bash_state_*.json"


def test_cleanup_no_crash_on_missing_dir() -> str:
    with tempfile.TemporaryDirectory() as root:
        nonexistent = Path(root) / "no-such-dir"
        _bash_state.cleanup_stale_state(scratch=nonexistent)
    return "cleanup_stale_state does not raise when the scratch dir is absent"


def test_write_state_no_crash_on_unwritable_scratch() -> str:
    with tempfile.TemporaryDirectory() as root:
        # A file (not a directory) at the scratch path makes mkdir/write fail.
        blocked = Path(root) / "blocked"
        blocked.write_text("occupied", encoding="utf-8")
        _bash_state.write_state("s", "r", "b", "c", scratch=blocked)
    return "write_state swallows OSError when scratch cannot be created/written"


def test_drift_warning_none_when_no_recorded_state() -> str:
    got = _bash_state.format_drift_warning(None, "C:/repo", "main", "C:/repo")
    assert got is None, f"expected None, got {got!r}"
    return "format_drift_warning returns None when there is no prior recorded state"


def test_drift_warning_none_when_current_read_fully_failed() -> str:
    # A transient git failure/timeout at the checkpoint yields (None, None) for
    # the current values. Recorded state is real, so a naive tuple inequality
    # would fire — but there is nothing to meaningfully compare against, and
    # firing here would show the same cwd on both the "was" and "now" lines.
    recorded = {"repo_root": "C:/repo", "branch": "main", "cwd": "C:/repo"}
    got = _bash_state.format_drift_warning(recorded, None, None, "C:/repo")
    assert got is None, f"expected None when current git read fully failed, got {got!r}"
    return "format_drift_warning suppresses the warning when both current values are None (git read failed, not a real comparison)"


def test_drift_warning_none_when_unchanged() -> str:
    recorded = {"repo_root": "C:/repo", "branch": "main", "cwd": "C:/repo"}
    got = _bash_state.format_drift_warning(recorded, "C:/repo", "main", "C:/repo/sub")
    assert got is None, f"expected None for unchanged (repo_root, branch), got {got!r}"
    return "format_drift_warning ignores a same-repo cwd change (only repo_root+branch matter)"


def test_drift_warning_fires_on_repo_change() -> str:
    recorded = {"repo_root": "C:/repo/.claude/worktrees/x", "branch": "feat/x", "cwd": "C:/repo/.claude/worktrees/x"}
    got = _bash_state.format_drift_warning(recorded, "C:/repo", "main", "C:/repo")
    assert got is not None, "expected a warning when repo_root differs"
    assert "cwd-drift" in got
    assert "feat/x" in got and "C:/repo/.claude/worktrees/x" in got
    assert "main" in got and got.count("C:/repo") >= 1
    return "format_drift_warning fires and names both states when repo_root differs (worktree -> canonical case)"


def test_drift_warning_fires_on_branch_only_change() -> str:
    recorded = {"repo_root": "C:/journal", "branch": "draft/2026-07-05", "cwd": "C:/journal"}
    got = _bash_state.format_drift_warning(recorded, "C:/journal", "draft/2026-07-04", "C:/journal")
    assert got is not None, "expected a warning when only branch differs"
    assert "draft/2026-07-05" in got and "draft/2026-07-04" in got
    return "format_drift_warning fires on a same-repo, branch-only reversion"


def test_drift_warning_handles_missing_fields_gracefully() -> str:
    recorded = {"repo_root": None, "branch": None, "cwd": "C:/somewhere"}
    got = _bash_state.format_drift_warning(recorded, "C:/repo", "main", "C:/repo")
    assert got is not None
    assert "<unknown>" in got
    return "format_drift_warning renders '<unknown>' for a recorded non-git state instead of raising"


def test_drift_warning_for_empty_session_id() -> str:
    # REPO_ROOT (this repo's checkout) is a real git repo, so current_repo_state
    # resolves a real (repo_root, branch) regardless of which branch happens to
    # be checked out when the test runs.
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        repo_root, branch, warning = _bash_state.drift_warning_for(
            "", str(REPO_ROOT), scratch=scratch
        )
        assert repo_root is not None, "expected a real repo_root for a real git cwd"
        assert warning is None, f"expected no warning for an empty session_id, got {warning!r}"
    return "drift_warning_for resolves repo_root/branch even with an empty session_id, but never warns"


def test_drift_warning_for_no_drift() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        repo_root, branch, _ = _bash_state.drift_warning_for("", str(REPO_ROOT), scratch=scratch)
        _bash_state.write_state("sess-nodrift", repo_root, branch, str(REPO_ROOT), scratch=scratch)
        got_repo, got_branch, warning = _bash_state.drift_warning_for(
            "sess-nodrift", str(REPO_ROOT), scratch=scratch
        )
        assert warning is None, f"expected no warning when recorded matches current, got {warning!r}"
        assert got_repo == repo_root and got_branch == branch
    return "drift_warning_for returns no warning when the recorded state matches current"


def test_drift_warning_for_fires_on_drift() -> str:
    with tempfile.TemporaryDirectory() as root:
        scratch = Path(root)
        _bash_state.write_state(
            "sess-drift", "C:/some/other/repo", "some-other-branch",
            "C:/some/other/repo", scratch=scratch,
        )
        _repo_root, _branch, warning = _bash_state.drift_warning_for(
            "sess-drift", str(REPO_ROOT), scratch=scratch
        )
        assert warning is not None, "expected a warning when recorded differs from current"
        assert "cwd-drift" in warning
    return "drift_warning_for fires a warning when the recorded state differs from current"


def _fake_repo(root: Path, head: str) -> Path:
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")
    return root


def test_repo_state_from_files_branch_detached_and_subdir() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(Path(tmp), "ref: refs/heads/feat/x")
        (repo / "a" / "b").mkdir(parents=True)
        want_root = repo.resolve().as_posix()
        assert _bash_state.repo_state_from_files(str(repo)) == (want_root, "feat/x")
        assert _bash_state.repo_state_from_files(str(repo / "a" / "b")) == (want_root, "feat/x")
        (repo / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
        assert _bash_state.repo_state_from_files(str(repo)) == (want_root, None)
    return "branch read from HEAD, found from a subdirectory, and a detached SHA gives branch None"


def test_repo_state_from_files_worktree_gitdir_file() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        admin = base / "main" / ".git" / "worktrees" / "wt"
        admin.mkdir(parents=True)
        (admin / "HEAD").write_text("ref: refs/heads/wt-branch\n", encoding="utf-8")
        wt = base / "wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {admin.as_posix()}\n", encoding="utf-8")
        assert _bash_state.repo_state_from_files(str(wt)) == (wt.resolve().as_posix(), "wt-branch")
        (wt / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n", encoding="utf-8")
        assert _bash_state.repo_state_from_files(str(wt)) == (wt.resolve().as_posix(), "wt-branch")
    return "a worktree's .git file is followed, absolute or relative"


def test_repo_state_from_files_defers_to_git_when_unsure() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _fake_repo(repo, "ref: refs/remotes/origin/main")
        assert _bash_state.repo_state_from_files(str(repo)) is None, "non-branch symbolic ref"
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        assert _bash_state.repo_state_from_files(str(repo / ".git")) is None, "cwd inside .git"
        assert _bash_state.repo_state_from_files(str(repo / "removed-worktree")) is None, "deleted cwd"
        (repo / ".git" / "HEAD").unlink()
        assert _bash_state.repo_state_from_files(str(repo)) is None, "unreadable HEAD"
        assert _bash_state.repo_state_from_files("") is None, "empty cwd"
        old = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = str(repo / ".git")
        try:
            assert _bash_state.repo_state_from_files(str(repo)) is None, "GIT_DIR set"
        finally:
            if old is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = old
    return "a non-branch ref, a cwd inside .git, a deleted cwd, a missing HEAD, an empty cwd and GIT_DIR all defer to git"


def test_repo_state_from_files_matches_git() -> str:
    # write_state and the drift checks compare these values as exact strings, so the
    # file read must give byte-identical output to the git call it replaces.
    import subprocess  # noqa: PLC0415

    for cwd in (REPO_ROOT, REPO_ROOT / "claude" / "scripts"):
        git = subprocess.run(["git", "rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD"],
                             capture_output=True, text=True, cwd=str(cwd), timeout=30)
        lines = git.stdout.splitlines()
        want = (lines[0].strip(), None if lines[1].strip() == "HEAD" else lines[1].strip())
        got = _bash_state.repo_state_from_files(str(cwd))
        assert got == want, f"{cwd}: files {got!r} != git {want!r}"
    return "the file read equals `git rev-parse --show-toplevel --abbrev-ref HEAD` on this checkout"


def test_read_branch_files_then_git() -> str:
    calls = []

    class _Result:
        returncode, stdout = 0, "from-git\n"

    def run(args):
        calls.append(args)
        return _Result()

    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(Path(tmp), "ref: refs/heads/main")
        assert _bash_state.read_branch(repo, run) == "main" and not calls
        (repo / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
        assert _bash_state.read_branch(repo, run) == _bash_state.DETACHED and not calls
        (repo / ".git" / "HEAD").write_text("ref: refs/tags/v1\n", encoding="utf-8")
        assert _bash_state.read_branch(repo, run) == "from-git"
        assert calls == [["git", "symbolic-ref", "--short", "HEAD"]], calls
    return "read_branch answers from HEAD (branch or DETACHED) and runs git only when the file cannot"


def main() -> int:
    tests = [
        ("repo_state_from_files: branch, detached, subdir", test_repo_state_from_files_branch_detached_and_subdir),
        ("repo_state_from_files: worktree .git file", test_repo_state_from_files_worktree_gitdir_file),
        ("repo_state_from_files: defers to git when unsure", test_repo_state_from_files_defers_to_git_when_unsure),
        ("repo_state_from_files: matches git byte for byte", test_repo_state_from_files_matches_git),
        ("read_branch: files first, git fallback", test_read_branch_files_then_git),
        ("state_path: correct path with override", test_state_path_returns_correct_path),
        ("state_path: default SCRATCH parent", test_state_path_default_scratch),
        ("state_age_seconds: missing file -> None", test_state_age_seconds_missing_file_returns_none),
        ("state_age_seconds: reports elapsed time", test_state_age_seconds_reports_elapsed_time),
        ("state_age_seconds: future mtime -> negative", test_state_age_seconds_future_mtime_returns_negative),
        ("state_age_seconds: no crash on unstattable path", test_state_age_seconds_no_crash_on_unstattable_path),
        ("write/read: round-trip", test_write_then_read_round_trip),
        ("read: missing file -> None", test_read_state_missing_file_returns_none),
        ("read: malformed JSON -> None", test_read_state_malformed_json_returns_none),
        ("read: non-dict JSON -> None", test_read_state_non_dict_json_returns_none),
        ("read: non-UTF-8 file -> None", test_read_state_non_utf8_returns_none),
        ("write: no crash on unwritable scratch", test_write_state_no_crash_on_unwritable_scratch),
        ("cleanup: removes stale, keeps fresh", test_cleanup_removes_stale_keeps_fresh),
        ("cleanup: ignores non-matching files", test_cleanup_ignores_non_matching_files),
        ("cleanup: no crash on missing dir", test_cleanup_no_crash_on_missing_dir),
        ("drift: None when no recorded state", test_drift_warning_none_when_no_recorded_state),
        ("drift: None when current read fully failed", test_drift_warning_none_when_current_read_fully_failed),
        ("drift: None when unchanged", test_drift_warning_none_when_unchanged),
        ("drift: fires on repo_root change", test_drift_warning_fires_on_repo_change),
        ("drift: fires on branch-only change", test_drift_warning_fires_on_branch_only_change),
        ("drift: handles missing fields gracefully", test_drift_warning_handles_missing_fields_gracefully),
        ("drift_warning_for: empty session_id -> no warning", test_drift_warning_for_empty_session_id),
        ("drift_warning_for: no warning when unchanged", test_drift_warning_for_no_drift),
        ("drift_warning_for: fires on drift", test_drift_warning_for_fires_on_drift),
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
