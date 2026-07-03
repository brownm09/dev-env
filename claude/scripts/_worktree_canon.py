#!/usr/bin/env python3
"""Shared worktree-cwd -> canonical-checkout-root resolution (dev-env#454).

`post-tool-use.py` (the PostToolUse project-board add-hook) and
`reconcile-project-board.py` (its background-session backstop, ADR-068) both independently
defined an identical `_WORKTREE_RE` regex to resolve a Claude-managed worktree cwd
(`<root>/.claude/worktrees/<name>/...`) to the canonical checkout root, where
`.claude/hook-config.json` actually lives in a project that gitignores it (dev-env's own
convention, not a universal one — some projects track it in git, e.g. lifting-logbook,
dev-env#527): `git worktree add` never checks out a gitignored file, and the harness
copies it into Claude-managed worktrees only inconsistently — dev-env#378.

The two scripts want different "no match" behavior, so this module exposes both shapes
over one shared regex + primitive rather than picking one and breaking the other:

  - `canonical_root_from_worktree(cwd)` -> `None` on no match. post-tool-use.py's
    `load_config()` uses the `None` to know a worktree-config read was never attempted,
    so it can fall through to the sibling-worktree git fallback (`canonical_root_via_git`,
    which stays in post-tool-use.py — it is not duplicated in reconcile-project-board.py).
  - `canonical_repo_root(path)` -> `path` unchanged on no match.
    reconcile-project-board.py's `default_repo_root()` always has a real path (derived
    from `__file__`) and needs it normalized whether or not it happens to be a worktree
    path; a `None` here would crash the `os.path.join` in its caller.

Not folded into `_worktree_topology.py` despite the similar subject matter: that module
already has an unrelated `canonical_worktree(worktrees)` (first entry of a *parsed*
`git worktree list --porcelain` result) — reusing the "canonical" name for a completely
different path-regex operation here would be a readability trap. `_worktree_topology.py`
is also policy-free at the *branch-topology* level (parses `git worktree list`); this
module is pure at the *path-string* level (a regex match, no git calls at all).

Pure — no I/O, no subprocess — so both functions are exercised offline by the unit tests
in `tests/test_worktree_canon.py`. Imported the same way as `_hookio` / `_worktree_topology`
/ `_hookutil`: a sibling module in `scripts/` resolved via `sys.path` (the `pyw -3` hook
launcher puts the script's own directory on `sys.path`; the test harness does the same with
`sys.path.insert(0, scripts_dir)`).

See ADR-073.
"""
from __future__ import annotations

import re

# Matches `<root>/.claude/worktrees/<name>` at the start of a path, capturing the
# canonical repo root (everything before `/.claude/`). Tolerates `/` and `\` separators.
_WORKTREE_RE = re.compile(
    r"^(.+?)[/\\]\.claude[/\\]worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)


def canonical_root_from_worktree(cwd: str) -> str | None:
    """Canonical repo root for a Claude-managed worktree cwd
    (`<root>/.claude/worktrees/<name>/...`), else None."""
    m = _WORKTREE_RE.match(cwd or "")
    return m.group(1) if m else None


def canonical_repo_root(path: str) -> str:
    """Canonical checkout root for `path`. If `path` is inside a Claude-managed
    worktree, return the canonical checkout root; otherwise return `path` unchanged
    (never None — callers path-join the result unconditionally). Built on
    `canonical_root_from_worktree` so there is exactly one regex and one match
    operation behind both contracts."""
    return canonical_root_from_worktree(path) or (path or "")
