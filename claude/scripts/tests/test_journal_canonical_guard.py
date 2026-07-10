#!/usr/bin/env python3
"""End-to-end tests for journal-canonical-guard.py's advisory stream routing.

dev-env#699: every warning path in this `UserPromptSubmit` hook (worktree-list-unreadable,
warn-squatter, warn-dirty, auto-return-checkout-failed) routed to **stderr** on a hook that
always exits 0. Per the Claude Code hooks reference (quoted in ADR-091 and ADR-098), only
`UserPromptSubmit`'s exit-0 **stdout** is added to Claude's context — stderr is not. This is
the identical defect ADR-098 fixed in the sibling `dev-env-sync.py`; this file proves the
mirrored fix here the same way: driving the REAL script end-to-end via subprocess (using the
`JOURNAL_CANONICAL_GUARD_REPO_PATH` test seam the original PR #661 author already built in,
pointed at a disposable throwaway git repo) and asserting the warning text lands on stdout
with stderr empty.

No pure-function layer: unlike `dev-env-sync.py` (which gained new pure formatter helpers as
part of its own stdout fix, ADR-098), this fix is purely the stream-routing change — no new
logic. ADR-093 (this hook's own design ADR) already decided against a dedicated test file for
its worktree-topology *decision* correctness ("zero local pure logic ... covered by
test_worktree_topology.py"), and that reasoning is unaffected by this change. This file tests
a different, orthogonal axis ADR-093 did not evaluate — which STREAM a message reaches, not
whether the topology decision is correct — so it does not contradict that precedent.

Two of the four warning paths are deliberately NOT exercised here, matching this repo's
established convention for hard-to-construct git-failure-injection paths (dev-env `##
Testing` items 22/26/30: "exercised end-to-end by --dry-run / a throwaway-repo run in the PR,
not here"):
  - worktree-list-unreadable (`git worktree list --porcelain` failing on a valid repo is not
    reliably constructible across platforms)
  - auto-return-checkout-failed (forcing `git checkout main` to fail without also disturbing
    the earlier `git status --porcelain` call used to decide clean-vs-dirty is fragile)

Usage:
    py -3 claude/scripts/tests/test_journal_canonical_guard.py

Exit 0 = all pass.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "journal-canonical-guard.py"


def _run_hook(env_overrides: dict) -> subprocess.CompletedProcess:
    """Run the real hook as a subprocess with an empty stdin (the hook reads and discards it).

    `env_overrides` merges onto a copy of the current environment (never replaces it wholesale)
    so the subprocess can still find `git`/`python` on PATH.
    """
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _init_throwaway_repo(root: Path) -> None:
    """Initialize a minimal real git repo at `root` with one empty commit on a branch
    literally named `main` (regardless of the machine's `init.defaultBranch` default).

    `-c init.templateDir= -c core.hooksPath=` neutralizes any global template directory /
    hooks path the developer's machine has configured, mirroring
    `test_canonical_mutate_guard.py`'s identical fixture helper.
    """
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=", "init", "-q", str(root)],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "--allow-empty", "-q", "-m", "init"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(root), "branch", "-M", "main"], check=True, capture_output=True)


def test_noop_when_repo_path_missing() -> str:
    """The guard: JOURNAL_REPO must exist as a directory. A missing path is a silent no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "does-not-exist"
        proc = _run_hook({"JOURNAL_CANONICAL_GUARD_REPO_PATH": str(missing)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}")
        if proc.stdout.strip() or proc.stderr.strip():
            raise AssertionError(f"expected no output at all, got stdout={proc.stdout!r} stderr={proc.stderr!r}")
    return "missing JOURNAL_REPO -> exit 0, no output"


def test_noop_when_canonical_already_on_main() -> str:
    """The common healthy path: canonical on `main` -> immediate exit, no output, no warning."""
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "engineering-journal"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        proc = _run_hook({"JOURNAL_CANONICAL_GUARD_REPO_PATH": str(canonical)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}")
        if proc.stdout.strip() or proc.stderr.strip():
            raise AssertionError(f"expected no output on the healthy path, got stdout={proc.stdout!r} stderr={proc.stderr!r}")
    return "canonical on main -> exit 0, no output"


def test_noop_when_on_legitimate_non_hijacked_branch() -> str:
    """engineering-journal's canonical is legitimately on draft/YYYY-MM-DD most of every day
    (claude/CLAUDE.md's documented Stub file workflow) — this must NOT be treated as hijacked.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "engineering-journal"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        subprocess.run(
            ["git", "-C", str(canonical), "checkout", "-q", "-b", "draft/2026-07-10"],
            check=True, capture_output=True,
        )
        proc = _run_hook({"JOURNAL_CANONICAL_GUARD_REPO_PATH": str(canonical)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}")
        if proc.stdout.strip() or proc.stderr.strip():
            raise AssertionError(f"expected no output for a legitimate non-hijacked branch, got stdout={proc.stdout!r} stderr={proc.stderr!r}")
    return "canonical on draft/2026-07-10 (legitimate) -> exit 0, no output, untouched"


def test_warn_dirty_lands_on_stdout_not_stderr() -> str:
    """THE core regression proof: a hijacked + dirty canonical prints its WARNING to stdout,
    with stderr empty. Before the dev-env#699 fix, this exact message was on stderr and
    therefore invisible to Claude on this always-exit-0 UserPromptSubmit hook.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "engineering-journal"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        subprocess.run(
            ["git", "-C", str(canonical), "checkout", "-q", "-b", "claude/hijacked-branch"],
            check=True, capture_output=True,
        )
        (canonical / "dirty.txt").write_text("uncommitted content", encoding="utf-8")
        proc = _run_hook({"JOURNAL_CANONICAL_GUARD_REPO_PATH": str(canonical)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0 (never block), got {proc.returncode}. stderr={proc.stderr!r}")
        if proc.stderr.strip():
            raise AssertionError(f"expected EMPTY stderr (this is the dev-env#699 regression), got {proc.stderr!r}")
        if "WARNING" not in proc.stdout or "uncommitted" not in proc.stdout:
            raise AssertionError(f"expected the warn-dirty WARNING on stdout, got stdout={proc.stdout!r}")
    return "hijacked + dirty -> WARNING on stdout, stderr empty"


def test_warn_squatter_lands_on_stdout_not_stderr() -> str:
    """A second warning site: hijacked canonical blocked by a non-canonical worktree squatting
    `main`. Same stdout/stderr proof as the warn-dirty case above, different message.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "engineering-journal"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        subprocess.run(
            ["git", "-C", str(canonical), "checkout", "-q", "-b", "claude/hijacked-branch"],
            check=True, capture_output=True,
        )
        squatter = Path(tmp) / "squatter-worktree"
        subprocess.run(
            ["git", "-C", str(canonical), "worktree", "add", "-q", str(squatter), "main"],
            check=True, capture_output=True,
        )
        proc = _run_hook({"JOURNAL_CANONICAL_GUARD_REPO_PATH": str(canonical)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0 (never block), got {proc.returncode}. stderr={proc.stderr!r}")
        if proc.stderr.strip():
            raise AssertionError(f"expected EMPTY stderr (this is the dev-env#699 regression), got {proc.stderr!r}")
        if "WARNING" not in proc.stdout or "squatting" not in proc.stdout:
            raise AssertionError(f"expected the warn-squatter WARNING on stdout, got stdout={proc.stdout!r}")
        if str(squatter) not in proc.stdout and squatter.name not in proc.stdout:
            raise AssertionError(f"expected the squatter path named in the warning, got stdout={proc.stdout!r}")
    return "hijacked + squatter -> WARNING on stdout (naming the squatter), stderr empty"


def test_auto_return_success_message_stays_on_stdout() -> str:
    """Regression pin for the already-working success path: hijacked + clean + no squatter
    auto-corrects to main and prints its confirmation to stdout (unchanged by this fix, but
    this file edits adjacent lines, so pin it stays correct).
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "engineering-journal"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        subprocess.run(
            ["git", "-C", str(canonical), "checkout", "-q", "-b", "claude/hijacked-branch"],
            check=True, capture_output=True,
        )
        proc = _run_hook({"JOURNAL_CANONICAL_GUARD_REPO_PATH": str(canonical)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}. stderr={proc.stderr!r}")
        if proc.stderr.strip():
            raise AssertionError(f"expected empty stderr on the success path, got {proc.stderr!r}")
        if "Restored engineering-journal canonical to main" not in proc.stdout:
            raise AssertionError(f"expected the restored-to-main confirmation on stdout, got stdout={proc.stdout!r}")
        branch = subprocess.run(
            ["git", "-C", str(canonical), "symbolic-ref", "--short", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        if branch != "main":
            raise AssertionError(f"expected the canonical to actually be back on main, got {branch!r}")
    return "hijacked + clean -> auto-restored to main, confirmation on stdout"


def main() -> int:
    tests = [
        ("noop when repo path missing", test_noop_when_repo_path_missing),
        ("noop when canonical already on main", test_noop_when_canonical_already_on_main),
        ("noop when on legitimate non-hijacked branch (draft/YYYY-MM-DD)", test_noop_when_on_legitimate_non_hijacked_branch),
        ("warn-dirty lands on stdout, not stderr", test_warn_dirty_lands_on_stdout_not_stderr),
        ("warn-squatter lands on stdout, not stderr", test_warn_squatter_lands_on_stdout_not_stderr),
        ("auto-return success message stays on stdout", test_auto_return_success_message_stays_on_stdout),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            for line in str(detail).splitlines():
                print(f"      {line}")
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
