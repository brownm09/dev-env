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

`canonical_root_from_worktree()` also recognizes a second, sibling-directory worktree
convention, `<repo>-worktrees/<name>` (e.g. `dev-env-worktrees/adr-096-correction`) — in
active use in this environment (manually via `git worktree add`, not `EnterWorktree`)
alongside the nested `.claude/worktrees/<name>` shape (dev-env#760). Unlike the
fully-ambiguous bare `<repo>-<suffix>` sibling shape (`dev-env-188` — see
`test_sibling_worktree_not_matched_by_regex`, still deliberately out of scope for this pure
regex, with `post-tool-use.py`'s `canonical_root_via_git` as its git-based fallback), the
`-worktrees/<name>` shape carries an unambiguous marker segment, so a regex extension is
reliable here the same way it is for the nested convention. Implemented as two SEPARATE
regexes (`_NESTED_WORKTREE_RE` tried first, `_SIBLING_WORKTREE_RE` as fallback) rather than
one combined alternation — review finding, dev-env#760: a single regex with non-greedy
`(.+?)` lets the sibling alternative match at a shallower position than a genuine nested
worktree occurring deeper in the same path (e.g. a nested worktree created inside a
sibling-convention one), mis-extracting the outer directory. See `_NESTED_WORKTREE_RE`'s
own comment for the full reasoning.

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

# Matches `<root>/.claude/worktrees/<name>` at the start of a path, capturing the canonical
# repo root (everything before the matched segment). Tolerates `/` and `\` separators.
# Tried BEFORE `_SIBLING_WORKTREE_RE` (see `canonical_root_from_worktree`) — review finding,
# dev-env#760: a single combined alternation with non-greedy `(.+?)` lets the sibling
# alternative "win" at a shallower position than a genuine nested worktree occurring deeper
# in the same path (e.g. a `.claude/worktrees/<name>` worktree created inside a
# `<repo>-worktrees/<name>` sibling worktree), mis-extracting the outer sibling directory as
# the root instead of the actual, deeper worktree. Checking this pattern against the WHOLE
# string first sidesteps that: since a real path normally contains at most one
# `.claude/worktrees/` segment, matching it directly finds the correct (only) occurrence
# regardless of what a `-worktrees` segment earlier in the same path might otherwise steal.
_NESTED_WORKTREE_RE = re.compile(
    r"^(.+?)[/\\]\.claude[/\\]worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)

# Matches `<root>-worktrees/<name>` at the start of a path — the sibling-directory
# convention (dev-env#760), e.g. `dev-env-worktrees/adr-096-correction`. Only consulted when
# `_NESTED_WORKTREE_RE` above doesn't match, so a nested worktree's own occurrence always
# takes precedence over an enclosing sibling directory's. A bare `<repo>-<suffix>` with no
# `-worktrees` marker (e.g. `dev-env-188`) still does not match — see the module docstring.
# The trailing `[^/\\]` in the capture group requires at least one non-separator character
# immediately before the literal `-worktrees` (review finding, dev-env#760: without it, a
# directory literally named `-worktrees` with no repo-name prefix at all would also match —
# `pre-tool-use-canonical-mutate-guard.py`'s equivalent fragment already required this;
# this pattern now agrees with it).
_SIBLING_WORKTREE_RE = re.compile(
    r"^(.+?[^/\\])-worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)


def canonical_root_from_worktree(cwd: str) -> str | None:
    """Canonical repo root for a Claude-managed worktree cwd
    (`<root>/.claude/worktrees/<name>/...` or `<root>-worktrees/<name>/...`), else None.
    The nested convention is tried first — see `_NESTED_WORKTREE_RE`'s comment for why."""
    cwd = cwd or ""
    m = _NESTED_WORKTREE_RE.match(cwd)
    if m:
        return m.group(1)
    m = _SIBLING_WORKTREE_RE.match(cwd)
    return m.group(1) if m else None


def canonical_repo_root(path: str) -> str:
    """Canonical checkout root for `path`. If `path` is inside a Claude-managed
    worktree, return the canonical checkout root; otherwise return `path` unchanged
    (never None — callers path-join the result unconditionally). Built on
    `canonical_root_from_worktree` so there is exactly one regex and one match
    operation behind both contracts."""
    return canonical_root_from_worktree(path) or (path or "")
