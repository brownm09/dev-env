#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-worktree-path-check.py.

Two layers, both hermetic (no real git repos, no real worktrees):

  1. _worktree_is_live() decision table — the orphaned-worktree liveness guard
     (dev-env#328). Driven with stubbed `path_exists` / `git_toplevel` so every
     branch is exercised deterministically.
  2. End-to-end main() via subprocess — drives the real hook over stdin and
     asserts exit codes for:
       - an Edit from an orphaned `.claude/worktrees/<name>` cwd (no `.git`) is
         BLOCKED (exit 2) with the orphan recovery message;
       - a call from a non-worktree cwd is a no-op (exit 0).

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
    """Acceptance: an Edit from an orphaned worktree cwd (no .git) is blocked."""
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
        try:
            reason = json.loads(proc.stdout).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stdout was not JSON: {proc.stdout!r}")
        if "orphaned" not in reason or "git worktree add --force" not in reason:
            raise AssertionError(f"block reason missing orphan/recovery text: {reason!r}")
    return "Edit from orphaned worktree cwd blocked (exit 2) with recovery recipe"


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


def main() -> int:
    tests = [
        ("_worktree_is_live decision table", test_worktree_is_live_decision_table),
        ("missing .git short-circuits before git", test_git_link_check_short_circuits_before_git),
        ("main() blocks Edit from orphaned worktree", test_main_blocks_edit_from_orphaned_worktree),
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
