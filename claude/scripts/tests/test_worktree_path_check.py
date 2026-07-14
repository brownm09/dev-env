#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-worktree-path-check.py.

Two layers, both hermetic (no real git repos, no real worktrees):

  1. _worktree_is_live() decision table — the orphaned-worktree liveness guard
     (dev-env#328). Driven with stubbed `path_exists` / `git_toplevel` so every
     branch is exercised deterministically.
  2. End-to-end main() via subprocess — drives the real hook over stdin and
     asserts exit codes for:
       - an Edit from an orphaned `.claude/worktrees/<name>` cwd (no `.git`) is
         BLOCKED (exit 2) with the orphan recovery message, on stderr;
       - a Write escaping to the canonical root from a live worktree is
         BLOCKED (exit 2) with the escape recovery message, on stderr
         (dev-env#469 — this call site had zero coverage before);
       - a call from a non-worktree cwd is a no-op (exit 0).

Both block scenarios assert the reason lands on stderr with empty stdout —
Claude Code discards a PreToolUse hook's stdout on exit code 2, so a reason
printed there is silently invisible to the model even though the block
itself still works (dev-env#469).

Usage:
    py -3 claude/scripts/tests/test_worktree_path_check.py

Exit 0 = all pass.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "pre-tool-use-worktree-path-check.py"

# The module's first line is `import _winsubp`; ensure scripts/ is importable.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("worktree_path_check", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wpc = _load_module()

_WT = "C:/Users/brown/Git/dev-env/.claude/worktrees/hardcore-williams-9df32f"
_CANON = "C:/Users/brown/Git/dev-env"


def test_worktree_is_live_decision_table() -> str:
    cases = [
        # (label, git_link_present, git_toplevel_return, expected_live)
        ("live: .git present, toplevel == worktree_root", True, _WT, True),
        ("orphan: .git link missing", False, _WT, False),
        ("orphan: git resolves up to canonical root", True, _CANON, False),
        ("orphan: git returns unrelated path", True, "C:/somewhere/else", False),
        (".git present but git exec failed (None) → don't false-block", True, None, True),
        ("live: toplevel differs only by case/sep", True, _WT.replace("/", "\\").upper(), True),
    ]
    for label, link_present, top, expected in cases:
        live = wpc._worktree_is_live(
            _WT,
            _WT,
            path_exists=lambda _p, _present=link_present: _present,
            git_toplevel=lambda _c, _top=top: _top,
        )
        if live != expected:
            raise AssertionError(f"{label}: _worktree_is_live = {live}, expected {expected}")
    return f"{len(cases)} liveness combinations classified correctly"


def test_git_link_check_short_circuits_before_git() -> str:
    """When the .git link is missing, git_toplevel must not even be consulted."""
    called = {"n": 0}

    def _spy(_cwd):
        called["n"] += 1
        return _WT

    live = wpc._worktree_is_live(
        _WT, _WT, path_exists=lambda _p: False, git_toplevel=_spy
    )
    if live is not False:
        raise AssertionError("missing .git link should yield not-live")
    if called["n"] != 0:
        raise AssertionError("git_toplevel was called despite missing .git link (no short-circuit)")
    return "missing .git link blocks without spawning git"


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_main_blocks_edit_from_orphaned_worktree() -> str:
    """Acceptance: an Edit from an orphaned worktree cwd (no .git) is blocked.

    The block reason must land on stderr, not stdout — Claude Code discards
    stdout on a PreToolUse hook exit code 2 and surfaces only stderr to the
    model. Asserting against proc.stdout here would pass even if the reason
    were silently invisible to the model (dev-env#469).
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Build <tmp>/.claude/worktrees/<name> with NO .git link — an orphan.
        orphan = Path(tmp) / ".claude" / "worktrees" / "orphan-name"
        orphan.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(orphan / "some_file.py")},
            "cwd": str(orphan),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block), got {proc.returncode}. stderr={proc.stderr!r}"
            )
        if proc.stdout.strip():
            raise AssertionError(f"expected empty stdout (reason must go to stderr), got {proc.stdout!r}")
        try:
            reason = json.loads(proc.stderr).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stderr was not JSON: {proc.stderr!r}")
        if "orphaned" not in reason or "git worktree add --force" not in reason:
            raise AssertionError(f"block reason missing orphan/recovery text: {reason!r}")
    return "Edit from orphaned worktree cwd blocked (exit 2) with recovery recipe, reason on stderr"


def test_main_blocks_write_escaping_to_canonical_root() -> str:
    """Acceptance: a Write whose absolute path targets the canonical root
    instead of the active (live) worktree is blocked — the hook's primary,
    most-documented scenario (ADR-024), and a code path the orphan test above
    does not reach (it exercises the *other* print-before-exit(2) call site
    in main()). The block reason must land on stderr, not stdout, same
    requirement as the orphan case (dev-env#469).

    The worktree must be LIVE (not orphaned) to reach this code path. A
    bogus (non-gitdir-link) `.git` file is enough: `git rev-parse
    --show-toplevel` fails against it (non-zero exit), and `_worktree_is_live`
    treats a git-resolution failure as live (see module docstring) — no real
    git repo needed, keeping this test hermetic like its siblings.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical_root = Path(tmp) / "canon-repo"
        worktree_root = canonical_root / ".claude" / "worktrees" / "some-worktree"
        worktree_root.mkdir(parents=True)
        (worktree_root / ".git").write_text("not a real gitdir link")
        escaping_path = canonical_root / "some_file.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(escaping_path)},
            "cwd": str(worktree_root),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        if proc.stdout.strip():
            raise AssertionError(f"expected empty stdout (reason must go to stderr), got {proc.stdout!r}")
        try:
            reason = json.loads(proc.stderr).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stderr was not JSON: {proc.stderr!r}")
        if "canonical repo root" not in reason or "Corrected" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "Write escaping to canonical root blocked (exit 2), reason on stderr"


def test_main_noop_outside_worktree() -> str:
    """A call whose cwd is not a Claude-managed worktree is a no-op (exit 0)."""
    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(Path(tmp) / "x.py")},
            "cwd": tmp,
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (no-op) outside worktree, got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
    return "non-worktree cwd is a no-op (exit 0)"


def test_main_allows_write_to_sibling_worktree() -> str:
    """Write targeting a sibling worktree under the same canonical root is allowed.

    Motivating case (dev-env#750): during journal compose, the session's cwd is
    inside one EJ worktree but the compose skill writes to the compose-YYYY-MM-DD
    worktree, which is a *different* worktree under the same canonical root. The
    hook was incorrectly blocking these as "escaping to canonical root" writes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical_root = Path(tmp) / "canon-repo"
        # Session is running inside worktree-A.
        worktree_a = canonical_root / ".claude" / "worktrees" / "worktree-A"
        worktree_a.mkdir(parents=True)
        (worktree_a / ".git").write_text("not a real gitdir link")
        # Write target is compose-2026-07-12, a sibling worktree.
        compose_wt = canonical_root / ".claude" / "worktrees" / "compose-2026-07-12"
        compose_wt.mkdir(parents=True)
        target_file = compose_wt / "sessions" / "meta" / "2026-07-12-journal.md"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target_file)},
            "cwd": str(worktree_a),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            stderr_msg = ""
            try:
                stderr_msg = json.loads(proc.stderr).get("reason", proc.stderr)
            except json.JSONDecodeError:
                stderr_msg = proc.stderr
            raise AssertionError(
                f"expected exit 0 (sibling worktree write allowed), got {proc.returncode}. "
                f"reason={stderr_msg!r}"
            )
    return "Write to sibling worktree under same canonical root is allowed (exit 0)"


def test_main_blocks_write_escaping_to_canonical_root_sibling_directory_convention() -> str:
    """dev-env#760: same acceptance as test_main_blocks_write_escaping_to_canonical_root,
    but cwd is the SIBLING-DIRECTORY worktree convention (`<repo>-worktrees/<name>`, a
    directory next to the canonical root, not nested inside it) rather than the nested
    `.claude/worktrees/<name>` convention. Confirms `_WORKTREE_RE`'s second alternative
    correctly extracts canonical_root/worktree_root and the escape-blocking logic
    downstream needs no shape-specific changes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        canonical_root = tmp / "canon-repo"
        worktree_root = tmp / "canon-repo-worktrees" / "some-worktree"
        worktree_root.mkdir(parents=True)
        (worktree_root / ".git").write_text("not a real gitdir link")
        escaping_path = canonical_root / "some_file.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(escaping_path)},
            "cwd": str(worktree_root),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        try:
            reason = json.loads(proc.stderr).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stderr was not JSON: {proc.stderr!r}")
        if "canonical repo root" not in reason or "Corrected" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "Write escaping to canonical root blocked (exit 2) from a sibling-directory-convention worktree (dev-env#760)"


def test_main_blocks_edit_from_orphaned_sibling_directory_worktree() -> str:
    """dev-env#760: same acceptance as test_main_blocks_edit_from_orphaned_worktree, but
    for an orphaned SIBLING-DIRECTORY-convention worktree (no `.git` link) rather than the
    nested convention — confirms the liveness guard fires identically regardless of shape.
    """
    with tempfile.TemporaryDirectory() as tmp:
        orphan = Path(tmp) / "canon-repo-worktrees" / "orphan-name"
        orphan.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(orphan / "some_file.py")},
            "cwd": str(orphan),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block), got {proc.returncode}. stderr={proc.stderr!r}"
            )
        try:
            reason = json.loads(proc.stderr).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stderr was not JSON: {proc.stderr!r}")
        if "orphaned" not in reason or "git worktree add --force" not in reason:
            raise AssertionError(f"block reason missing orphan/recovery text: {reason!r}")
    return "Edit from orphaned sibling-directory-convention worktree blocked (exit 2, dev-env#760)"


def main() -> int:
    tests = [
        ("_worktree_is_live decision table", test_worktree_is_live_decision_table),
        ("missing .git short-circuits before git", test_git_link_check_short_circuits_before_git),
        ("main() blocks Edit from orphaned worktree", test_main_blocks_edit_from_orphaned_worktree),
        ("main() blocks Write escaping to canonical root", test_main_blocks_write_escaping_to_canonical_root),
        ("main() allows Write to sibling worktree", test_main_allows_write_to_sibling_worktree),
        ("main() blocks Write escaping to canonical root, sibling-directory convention (dev-env#760)", test_main_blocks_write_escaping_to_canonical_root_sibling_directory_convention),
        ("main() blocks Edit from orphaned sibling-directory worktree (dev-env#760)", test_main_blocks_edit_from_orphaned_sibling_directory_worktree),
        ("main() no-op outside worktree", test_main_noop_outside_worktree),
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
