#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-worktree-path-check.py.

Two layers. The `_worktree_is_live()` unit-test layer and most of the
end-to-end layer are hermetic (no real git repos, no real worktrees) — they
build worktree fixtures from bogus (non-real-repo) `.git` files, which is
enough to exercise `_resolve_worktree_scope()`'s regex+liveness FALLBACK path
(`git worktree list` fails against a bogus `.git`, same as it always has). A
smaller set of tests (dev-env#774, below) use a REAL throwaway git repo —
needed to exercise the git-CONFIRMED path, which a bogus `.git` file cannot
reach at all:

  1. _worktree_is_live() decision table — the orphaned-worktree liveness guard
     (dev-env#328). Driven with stubbed `path_isfile` / `git_toplevel` so every
     branch is exercised deterministically.
  2. End-to-end main() via subprocess — drives the real hook over stdin and
     asserts exit codes for:
       - an Edit from an orphaned `.claude/worktrees/<name>` cwd (no `.git`) is
         BLOCKED (exit 2) with the orphan recovery message, on stderr;
       - a Write escaping to the canonical root from a live worktree is
         BLOCKED (exit 2) with the escape recovery message, on stderr
         (dev-env#469 — this call site had zero coverage before);
       - a call from a non-worktree cwd is a no-op (exit 0);
       - (dev-env#774, real-git-repo fixtures) a Write from a subdirectory of a
         repo literally named `<x>-worktrees` is allowed — gap (b): the regex
         alone misclassifies the subdirectory as a worktree name, but
         `_resolve_worktree_scope()`'s git confirmation catches the mismatch
         against git's own canonical entry and no-ops instead; an orphan
         nested in a REAL canonical repo is still correctly blocked; a REAL,
         `git worktree add`-registered worktree still gets zero-friction
         treatment; and escape-detection (this hook's core purpose) still
         fires against a REAL canonical root, not just the bogus-`.git`-file
         fixtures every other test in this file uses.

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
import os
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

# The orphan block message is rendered from this module rather than written inline in
# the hook (dev-env#862, ADR-116). Asserting against it — instead of re-hardcoding the
# command text here, as this file did with the dev-env#751-disproven
# `git worktree add --force` string — means a change to the recipe cannot leave these
# acceptance tests pinning a recipe the hook no longer emits.
import _worktree_recovery  # noqa: E402

wr = _worktree_recovery

_WT = "C:/Users/brown/Git/dev-env/.claude/worktrees/hardcore-williams-9df32f"
_CANON = "C:/Users/brown/Git/dev-env"


def _assert_orphan_recovery_recipe(reason: str, orphan: str, canonical: str) -> None:
    """Pin the orphan block reason: it names the orphan condition and carries every
    canonical recovery command, in order, for THESE paths.

    Also pins the absence of `worktree add --force` — the form dev-env#751 disproved
    and dev-env#862 found still live in this very message. git dies
    `fatal: '<path>' already exists` on a non-empty target before it ever consults
    `--force`, so re-emitting it would send a blocked session back down a dead end.
    """
    if "orphaned" not in reason:
        raise AssertionError(f"block reason missing orphan text: {reason!r}")
    cursor = -1
    for command in wr.recovery_commands(orphan, canonical):
        found = reason.find(command, cursor + 1)
        if found == -1:
            raise AssertionError(
                f"block reason missing (or misordering) recovery command {command!r}: {reason!r}"
            )
        cursor = found
    if "worktree add --force" in reason:
        raise AssertionError(
            f"block reason reintroduced the disproven --force recipe: {reason!r}"
        )


def _init_throwaway_repo(root: Path) -> None:
    """Initialize a minimal real git repo at `root` (dev-env#774) — needed for
    the tests below that exercise `_resolve_worktree_scope()`'s git-backed path,
    as opposed to every pre-existing test in this file, which uses a bogus
    (non-real-repo) `.git` file and so only ever reaches the regex+liveness
    fallback. Mirrors `test_canonical_mutate_guard.py`'s identical helper
    (duplicated rather than imported, per this codebase's convention of
    tolerating duplication through two test-file consumers before extracting a
    shared module).
    """
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=", "init", "-q", str(root)],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True, capture_output=True)


def _init_repo_with_real_worktree(canonical: Path, worktree_path: Path) -> None:
    """Real canonical repo + a REAL, actually-registered `git worktree add` at
    `worktree_path` (dev-env#774) — mirrors `test_canonical_mutate_guard.py`'s
    `_init_repo_with_live_worktree`. `git worktree add` needs an existing commit
    and a distinct branch (git allows a branch checked out in at most one
    worktree at a time, and `main` is already checked out in the canonical).
    """
    _init_throwaway_repo(canonical)
    subprocess.run(
        ["git", "-C", str(canonical), "commit", "--allow-empty", "-q", "-m", "init"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(canonical), "branch", "-M", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(canonical), "worktree", "add", "-q", "-b", "real-worktree-branch", str(worktree_path)],
        check=True, capture_output=True,
    )


def test_worktree_is_live_decision_table() -> str:
    cases = [
        # (label, git_link_is_file, git_toplevel_return, expected_live)
        ("live: .git present as a file, toplevel == worktree_root", True, _WT, True),
        ("orphan: .git link missing", False, _WT, False),
        ("orphan: git resolves up to canonical root", True, _CANON, False),
        ("orphan: git returns unrelated path", True, "C:/somewhere/else", False),
        (".git present but git exec failed (None) → don't false-block", True, None, True),
        ("live: toplevel differs only by case/sep", True, _WT.replace("/", "\\").upper(), True),
        (
            "not live: .git present but as a DIRECTORY, not a file (dev-env#760 review finding — "
            "a genuine canonical clone at a worktree-shaped path must not be treated as live)",
            False,
            _WT,
            False,
        ),
    ]
    for label, link_is_file, top, expected in cases:
        live = wpc._worktree_is_live(
            _WT,
            _WT,
            path_isfile=lambda _p, _is_file=link_is_file: _is_file,
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
        _WT, _WT, path_isfile=lambda _p: False, git_toplevel=_spy
    )
    if live is not False:
        raise AssertionError("missing .git link should yield not-live")
    if called["n"] != 0:
        raise AssertionError("git_toplevel was called despite missing .git link (no short-circuit)")
    return "missing .git link blocks without spawning git"


def test_worktree_is_live_rejects_git_directory() -> str:
    """dev-env#760 review finding: a genuine canonical checkout (a real `git clone`,
    whose `.git` is a DIRECTORY) that happens to sit at a worktree-shaped path must not
    be treated as a live worktree. `os.path.isfile` on a directory returns False, so this
    exercises the REAL default (not a stubbed lambda) against a real filesystem directory
    named `.git`, proving the fix — `os.path.exists` would have wrongly returned True here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        worktree_shaped_root = Path(tmp) / ".claude" / "worktrees" / "actually-a-clone"
        (worktree_shaped_root / ".git").mkdir(parents=True)  # .git as a DIRECTORY, not a file
        live = wpc._worktree_is_live(str(worktree_shaped_root), str(worktree_shaped_root))
        if live is not False:
            raise AssertionError("a worktree-shaped path whose .git is a directory must not be live")
    return "a real .git directory (not file) at a worktree-shaped path is correctly not-live (dev-env#760)"


def _run_hook(payload: dict, env_overrides: dict = None) -> subprocess.CompletedProcess:
    """Run the real hook over stdin. `env_overrides`, when given, merges onto
    a copy of the current environment (rather than replacing it wholesale) so
    a test can redirect e.g. WORKTREE_PATH_CHECK_JOURNAL_PATH at a disposable
    temp dir without losing PATH/other inherited variables the subprocess
    needs (to find `git`, etc.). Mirrors test_canonical_mutate_guard.py's
    identical helper.
    """
    env = None
    if env_overrides:
        env = dict(os.environ)
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
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
        _assert_orphan_recovery_recipe(reason, str(orphan), tmp)
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


def test_main_allows_write_to_engineering_journal_canonical_root() -> str:
    """dev-env#750, reopened: a Write targeting the BARE engineering-journal canonical
    root (no worktree segment anywhere in the target path) is allowed, when issued from
    a session whose own primary repo is itself an EJ worktree.

    This is the primary Stub file workflow write shape (claude/CLAUDE.md -> Engineering
    Journal -> "Never create a dedicated worktree to write a stub -- always operate
    directly on the canonical via -C") and is distinct from
    test_main_allows_write_to_sibling_worktree above: that test's write TARGET itself
    looks worktree-shaped (a different worktree under the same canonical root); this
    test's write target is the canonical root directly -- the case PR #756's carve-out
    never covered, which is why the issue was reopened.

    WORKTREE_PATH_CHECK_JOURNAL_PATH redirects the hook's journal-carveout constant at a
    disposable temp dir instead of the developer's real engineering-journal checkout --
    this test must never touch that real checkout.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical_root = Path(tmp) / "ej-canon"
        worktree_root = canonical_root / ".claude" / "worktrees" / "some-worktree"
        worktree_root.mkdir(parents=True)
        (worktree_root / ".git").write_text("not a real gitdir link")
        # Bare canonical-root target -- no worktree segment anywhere in this path.
        target_file = canonical_root / "sessions" / "dev-env" / "2026-08-15_120000.stub.md"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target_file)},
            "cwd": str(worktree_root),
        }
        proc = _run_hook(payload, env_overrides={"WORKTREE_PATH_CHECK_JOURNAL_PATH": str(canonical_root)})
        if proc.returncode != 0:
            stderr_msg = ""
            try:
                stderr_msg = json.loads(proc.stderr).get("reason", proc.stderr)
            except json.JSONDecodeError:
                stderr_msg = proc.stderr
            raise AssertionError(
                f"expected exit 0 (bare EJ-canonical-root write allowed), got {proc.returncode}. "
                f"reason={stderr_msg!r}"
            )
    return "Write to the bare engineering-journal canonical root is allowed (exit 0, dev-env#750 reopened)"


def test_main_blocks_write_to_samename_repo_outside_journal_carveout_path() -> str:
    """dev-env#750, reopened / dev-env#576/PR#584 review-finding precedent: a canonical
    root that merely happens to be NAMED the same as the configured carve-out path, but
    lives somewhere else, is NOT exempt -- only the one path
    WORKTREE_PATH_CHECK_JOURNAL_PATH names is. Pins the exact-path (not basename) match,
    mirroring test_canonical_mutate_guard.py's
    test_main_blocks_redirect_into_samename_repo_outside_carveout_path.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical_root = Path(tmp) / "engineering-journal"  # same basename, NOT the configured carve-out path
        worktree_root = canonical_root / ".claude" / "worktrees" / "some-worktree"
        worktree_root.mkdir(parents=True)
        (worktree_root / ".git").write_text("not a real gitdir link")
        escaping_path = canonical_root / "sessions" / "dev-env" / "2026-08-15_120000.stub.md"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(escaping_path)},
            "cwd": str(worktree_root),
        }
        # Deliberately configure the carve-out path to somewhere else entirely so
        # `canonical_root` (same basename, different path) is provably not it.
        other_path = Path(tmp) / "actual-carveout-target"
        proc = _run_hook(payload, env_overrides={"WORKTREE_PATH_CHECK_JOURNAL_PATH": str(other_path)})
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (same-basename repo at a different path must NOT be exempt), "
                f"got {proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        try:
            reason = json.loads(proc.stderr).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stderr was not JSON: {proc.stderr!r}")
        if "canonical repo root" not in reason or "Corrected" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "same-basename-but-wrong-path repo is correctly NOT journal-carveout-exempt (dev-env#750 reopened)"


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
        _assert_orphan_recovery_recipe(reason, str(orphan), str(Path(tmp) / "canon-repo"))
    return "Edit from orphaned sibling-directory-convention worktree blocked (exit 2, dev-env#760)"


def test_main_allows_write_to_nested_worktree_inside_sibling_directory_worktree() -> str:
    """dev-env#760 review finding: a `.claude/worktrees/<name>` (nested-convention) worktree
    created INSIDE a `<repo>-worktrees/<name>` (sibling-convention) worktree must resolve to
    ITS OWN root, not the outer sibling directory. A combined single-regex alternation with
    non-greedy `(.+?)` would let the sibling alternative match at the shallower (outer)
    position, mis-identifying worktree_root as the outer directory — which has no `.git` of
    its own at that exact path (only the inner one does) — and wrongly block every write here
    as if from an orphaned worktree. `_match_worktree` tries the nested pattern against the
    whole string first, which correctly finds the (unique) `.claude/worktrees/` segment
    regardless of the sibling directory enclosing it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        outer_sibling_worktree = Path(tmp) / "canon-repo-worktrees" / "outer-worktree"
        inner_nested_worktree = outer_sibling_worktree / ".claude" / "worktrees" / "inner-worktree"
        inner_nested_worktree.mkdir(parents=True)
        (inner_nested_worktree / ".git").write_text("not a real gitdir link")
        target_file = inner_nested_worktree / "some_file.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target_file)},
            "cwd": str(inner_nested_worktree),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            stderr_msg = ""
            try:
                stderr_msg = json.loads(proc.stderr).get("reason", proc.stderr)
            except json.JSONDecodeError:
                stderr_msg = proc.stderr
            raise AssertionError(
                f"expected exit 0 (write inside the correctly-identified inner worktree "
                f"allowed), got {proc.returncode}. reason={stderr_msg!r}"
            )
    return "Write inside a nested worktree created inside a sibling-directory worktree is allowed, root correctly resolved to the inner worktree (exit 0, dev-env#760)"


def test_main_allows_write_from_subdirectory_of_repo_literally_named_worktrees_suffix() -> str:
    """dev-env#774 gap (b): a repo whose OWN root directory name literally ends in
    `-worktrees` (e.g. `some-repo-worktrees`) must not have every Write/Edit from a
    subdirectory misclassified as targeting a non-live "worktree".

    Before this fix, `_SIBLING_WORKTREE_RE` matched the subdirectory as if it were a
    worktree name and a truncated prefix as the "canonical root" -- then blocked
    because no `.git` exists at that fabricated worktree_root. The
    `_resolve_worktree_scope()` git-confirmation now recognizes that git's own
    canonical entry (`some-repo-worktrees` itself) does not match the regex's
    truncated guess, so nothing is enforced here at all.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "some-repo-worktrees"
        subdir = repo / "src"
        subdir.mkdir(parents=True)
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(subdir / "some_file.py")},
            "cwd": str(subdir),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            stderr_msg = ""
            try:
                stderr_msg = json.loads(proc.stderr).get("reason", proc.stderr)
            except json.JSONDecodeError:
                stderr_msg = proc.stderr
            raise AssertionError(
                f"expected exit 0 (repo literally named *-worktrees must not be "
                f"misclassified), got {proc.returncode}. reason={stderr_msg!r}"
            )
    return "Write from a subdirectory of a repo literally named *-worktrees is allowed (exit 0, dev-env#774 gap (b))"


def test_main_blocks_edit_from_orphaned_worktree_nested_in_real_canonical() -> str:
    """dev-env#774: proves the new git-worktree-list-based confirmation still
    correctly BLOCKS a genuine orphan (its `.git` link missing) nested inside a
    REAL canonical repo -- git's own canonical entry (found by walking up past
    the orphan) matches the regex's guess, but the orphan's own path is not among
    the repo's registered (linked) worktrees, so it is correctly treated as not live.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical-repo"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        orphan = canonical / ".claude" / "worktrees" / "orphan-name"
        orphan.mkdir(parents=True)  # no .git of its own
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(orphan / "some_file.py")},
            "cwd": str(orphan),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (orphan nested in a real canonical still blocked), "
                f"got {proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        reason = json.loads(proc.stderr).get("reason", "")
        if "orphaned" not in reason:
            raise AssertionError(f"block reason missing orphan text: {reason!r}")
    return "Edit from an orphaned worktree nested in a REAL canonical repo is still blocked (exit 2, dev-env#774)"


def test_main_allows_write_from_real_registered_worktree() -> str:
    """dev-env#774: a genuine, `git worktree add`-registered worktree (not just a
    worktree-shaped bogus-`.git`-file directory) is still recognized as live and
    in-scope -- a write correctly targeting it is allowed. Positive control for
    the new git-worktree-list-based confirmation.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical-repo"
        worktree_root = canonical / ".claude" / "worktrees" / "real-worktree"
        _init_repo_with_real_worktree(canonical, worktree_root)
        target_file = worktree_root / "some_file.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(target_file)},
            "cwd": str(worktree_root),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}. stderr={proc.stderr!r}")
    return "Write correctly targeting a REAL registered worktree is allowed (exit 0, dev-env#774)"


def test_main_blocks_write_escaping_to_real_canonical_root() -> str:
    """dev-env#774: escape-detection (this hook's core purpose) still fires
    correctly when using a REAL, `git worktree add`-registered worktree + REAL
    canonical, not just the bogus-`.git`-file fixtures the pre-existing tests
    in this file use (all of which only ever exercise the regex+liveness
    fallback path, since a bogus `.git` file makes `git worktree list` fail).
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical-repo"
        worktree_root = canonical / ".claude" / "worktrees" / "real-worktree"
        _init_repo_with_real_worktree(canonical, worktree_root)
        escaping_path = canonical / "some_file.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(escaping_path)},
            "cwd": str(worktree_root),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
        reason = json.loads(proc.stderr).get("reason", "")
        if "canonical repo root" not in reason or "Corrected" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "Write escaping to a REAL canonical root (from a REAL registered worktree) still blocked (exit 2, dev-env#774)"


# ---------------------------------------------------------------------------
# dev-env#1031/#1033: read_cwd() + the shared _hookio.read_tool_input_field()
# helper -- extending _hookio's hardened-read migration to this hook's own
# computed field-name read, which _hookio.read_command can't cover directly.
# read_tool_input_field's own correctness (normal/missing/None/non-dict/
# non-string inputs) is exhaustively covered in test_hookio.py -- this file's
# own coverage was a local wrapper's worth of duplicate tests until
# dev-env#1033's review round hoisted the wrapper into _hookio.py as a
# genuinely shared helper (a second caller, memory-write-advisory.py, needed
# the identical generalization); not re-duplicated here.
#
# The two main()-level tests below call wpc.main() DIRECTLY rather than via
# the subprocess-based `_run_hook` helper every OTHER main()-level test in
# this file uses. /review on dev-env#1031/#1033's PR verified, by reverting
# this hook's production code and re-running these exact payloads, that a
# subprocess-based assertion on exit code ALONE cannot tell "handled
# cleanly" from "crashed, caught by this file's own `except Exception:
# sys.exit(0)` __main__ guard" apart -- both produce the identical exit 0.
# That risk does NOT apply to this file's OTHER main()-level tests above
# (they assert a SPECIFIC blocking exit 2 with a specific reason on stderr --
# a crash produces exit 0 instead, which those assertions already catch), so
# only these two, which assert a clean ALLOW, needed conversion. Calling
# wpc.main() directly bypasses that safe-exit wrapper entirely: a crash now
# propagates as an uncaught Python exception in THIS test process, which
# this file's own test runner reports as a failure. Mirrors
# test_usage_snapshot.py's `_run_main_capturing_trace` pattern and this same
# file's own `wpc = _load_module()` module-loading approach.
# ---------------------------------------------------------------------------

def _run_wpc_main(payload) -> int:
    """Call the already-loaded wpc.main() directly (bypassing its __main__
    try/except safe-exit wrapper). See the module comment above this
    function for why this matters and what it replaces."""
    import io as _io
    real_stdin = wpc.sys.stdin
    wpc.sys.stdin = _io.StringIO(json.dumps(payload))
    try:
        try:
            wpc.main()
            return 0
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
    finally:
        wpc.sys.stdin = real_stdin


def test_main_malformed_tool_input_and_cwd_does_not_crash() -> str:
    """dev-env#1028's payload shape (tool_input present-but-null) combined with
    a malformed cwd, driven through main() directly. Pre-fix, the unguarded
    `data.get("tool_input", {}).get(_PATH_FIELD[tool_name], "")` chain would
    have crashed once cwd resolution got far enough to reach it; here cwd:null
    resolves to "" via read_cwd() and fails _match_worktree() before that
    line is even reached -- still proves read_cwd()'s own malformed-input
    handling doesn't crash this hook's main() dispatch. A crash here (the
    pre-fix behavior) would propagate as an uncaught AttributeError, not a
    laundered exit 0 -- verified empirically against the pre-fix blob during
    /review on dev-env#1031/#1033.
    """
    code = _run_wpc_main({"tool_name": "Write", "tool_input": None, "cwd": None})
    if code != 0:
        raise AssertionError(f"expected exit 0, got {code}")
    return "tool_input:null + cwd:null -> clean SystemExit(0), no crash (dev-env#1031/#1033)"


def test_main_non_dict_top_level_data_does_not_crash() -> str:
    """A valid-JSON-but-non-dict top-level payload (a list here) crashed at
    `data.get("tool_name", "")` pre-fix -- one level above every read_*
    helper's own guard, the identical silent-crash class this migration
    closes across all 14 dev-env#1031 files.
    """
    code = _run_wpc_main(["not", "an", "object"])
    if code != 0:
        raise AssertionError(f"expected exit 0, got {code}")
    return "non-dict top-level JSON -> clean SystemExit(0), no crash (dev-env#1031/#1033)"


def main() -> int:
    tests = [
        ("_worktree_is_live decision table", test_worktree_is_live_decision_table),
        ("missing .git short-circuits before git", test_git_link_check_short_circuits_before_git),
        ("_worktree_is_live rejects a real .git directory (dev-env#760 review finding)", test_worktree_is_live_rejects_git_directory),
        ("main() blocks Edit from orphaned worktree", test_main_blocks_edit_from_orphaned_worktree),
        ("main() blocks Write escaping to canonical root", test_main_blocks_write_escaping_to_canonical_root),
        ("main() allows Write to sibling worktree", test_main_allows_write_to_sibling_worktree),
        ("main() allows Write to bare engineering-journal canonical root (dev-env#750 reopened)", test_main_allows_write_to_engineering_journal_canonical_root),
        ("main() blocks Write to same-basename repo outside journal carve-out path (dev-env#750 reopened)", test_main_blocks_write_to_samename_repo_outside_journal_carveout_path),
        ("main() blocks Write escaping to canonical root, sibling-directory convention (dev-env#760)", test_main_blocks_write_escaping_to_canonical_root_sibling_directory_convention),
        ("main() blocks Edit from orphaned sibling-directory worktree (dev-env#760)", test_main_blocks_edit_from_orphaned_sibling_directory_worktree),
        ("main() allows Write to nested worktree inside a sibling-directory worktree (dev-env#760 review finding)", test_main_allows_write_to_nested_worktree_inside_sibling_directory_worktree),
        ("main() no-op outside worktree", test_main_noop_outside_worktree),
        ("main() allows Write from a subdirectory of a repo literally named *-worktrees (dev-env#774 gap (b))", test_main_allows_write_from_subdirectory_of_repo_literally_named_worktrees_suffix),
        ("main() blocks Edit from an orphan nested in a REAL canonical repo (dev-env#774)", test_main_blocks_edit_from_orphaned_worktree_nested_in_real_canonical),
        ("main() allows Write from a REAL registered worktree (dev-env#774)", test_main_allows_write_from_real_registered_worktree),
        ("main() blocks Write escaping to a REAL canonical root (dev-env#774)", test_main_blocks_write_escaping_to_real_canonical_root),
        ("main(): tool_input:null + cwd:null does not crash (direct call, dev-env#1031/#1033)", test_main_malformed_tool_input_and_cwd_does_not_crash),
        ("main(): non-dict top-level JSON does not crash (direct call, dev-env#1031/#1033)", test_main_non_dict_top_level_data_does_not_crash),
    ]

    # dev-env#1031/#1033 /review finding: every main()-level test in this
    # file (both the subprocess-based ones above and the two direct-call
    # ones just added) reaches this hook's own unconditional
    # `_hookutil.record_heartbeat(...)` call. HOOK_HEARTBEAT_DIR_OVERRIDE
    # (checked by record_heartbeat itself, and inherited automatically by
    # every subprocess `_run_hook` spawns via `env=None`'s "inherit the
    # current environment" default) redirects all of them away from the
    # developer's real ~/.claude/scratch/hook-heartbeat/ for this file's
    # whole run, so running this test suite never silently blinds
    # hook-liveness-check.py's staleness detector (ADR-106).
    import tempfile
    heartbeat_tmp = tempfile.mkdtemp(prefix="dev_env_worktree_path_check_heartbeat_")
    real_override = os.environ.get("HOOK_HEARTBEAT_DIR_OVERRIDE")
    os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = heartbeat_tmp
    try:
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
    finally:
        if real_override is None:
            os.environ.pop("HOOK_HEARTBEAT_DIR_OVERRIDE", None)
        else:
            os.environ["HOOK_HEARTBEAT_DIR_OVERRIDE"] = real_override
        import shutil
        shutil.rmtree(heartbeat_tmp, ignore_errors=True)

    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
