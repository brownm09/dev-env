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

dev-env#510 extends the consumer set to the two PreToolUse worktree guards, whose
byte-identical (or trivially-variant) copies of these regexes this module now
single-sources. `pre-tool-use-worktree-path-check.py` consumes `match_worktree()`
directly (it needs both the canonical-root `group(1)` and the worktree-root `group(0)`
of one match); `pre-tool-use-canonical-mutate-guard.py` consumes `worktree_root_from_path()`
(the full worktree root of a cwd) and `is_worktree_path()` (a boolean shape check on an
already git-resolved toplevel). All three are built on the one `match_worktree()`
matcher below, so the worktree-path convention lives in exactly one place across all
four consumers — see ADR-073.

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


def match_worktree(path: str):
    """The `re.Match` for `path` against the nested convention (tried first) then
    the sibling convention, or None. The single matcher every worktree-path
    operation in this module — and its consumer hooks (dev-env#510) — is built
    on, so there is exactly one pair of regexes and one match ordering behind all
    of them:

      - `canonical_root_from_worktree()` reads its `group(1)` — the canonical-root
        PREFIX (everything before the marker segment).
      - `worktree_root_from_path()` reads its `group(0)` — the full worktree root
        (the PREFIX plus the `.claude/worktrees/<name>` / `<repo>-worktrees/<name>`
        marker segment).
      - `is_worktree_path()` just tests it for None.

    Nested is tried first so a nested worktree created inside a sibling-convention
    worktree resolves to its own (deeper) root rather than the outer sibling
    stealing the match at a shallower position — see `_NESTED_WORKTREE_RE`'s
    comment for the full reasoning."""
    path = path or ""
    m = _NESTED_WORKTREE_RE.match(path)
    return m if m else _SIBLING_WORKTREE_RE.match(path)


def canonical_root_from_worktree(cwd: str) -> str | None:
    """Canonical repo root for a Claude-managed worktree cwd
    (`<root>/.claude/worktrees/<name>/...` or `<root>-worktrees/<name>/...`), else None.
    The nested convention is tried first — see `_NESTED_WORKTREE_RE`'s comment for why."""
    m = match_worktree(cwd)
    return m.group(1) if m else None


def worktree_root_from_path(path: str) -> str | None:
    """The full worktree ROOT of `path` — everything up through and including the
    `.claude/worktrees/<name>` or `<repo>-worktrees/<name>` marker segment — or
    None if `path` isn't anchored inside a worktree. This is `match_worktree`'s
    whole-match `group(0)`, i.e. `canonical_root_from_worktree`'s PREFIX plus the
    marker segment.

    Consumed by `pre-tool-use-canonical-mutate-guard.py`'s `_worktree_root_from_cwd`
    (the worktree-root string it hands its `.git`-liveness check) — dev-env#510.
    That hook only ever passes an absolute cwd, for which this returns the same
    root its former local `_NESTED_WORKTREE_ROOT_RE`/`_SIBLING_WORKTREE_ROOT_RE`
    did: the two share the nested pattern exactly, and the sibling patterns agree
    for any path with a genuine leading component before the marker segment — which
    a Windows absolute path (drive-letter `C:/…` or UNC `\\…`) always has. The one
    divergence is a path whose *sibling* marker is the very FIRST component (a bare
    relative `dev-env-worktrees/foo`, or one at the Unix filesystem root
    `/dev-env-worktrees/foo`): the former regex required a component before
    `<repo>-worktrees` and returned None, this returns the root. Neither occurs as
    a resolved toplevel on this system — see `test_worktree_root_from_path_*`."""
    m = match_worktree(path)
    return m.group(0) if m else None


def is_worktree_path(path: str) -> bool:
    """True if `path` is anchored inside a Claude-managed worktree (either
    convention). A boolean-only shape check for callers that need to know
    *whether* a path is worktree-shaped, not extract a root from it — consumed by
    `pre-tool-use-canonical-mutate-guard.py`'s `_is_confirmed_worktree_root`
    fail-open backstop (dev-env#510).

    Equivalent, for the absolute paths that hook ever passes (a git-resolved
    `--show-toplevel`), to that hook's former unanchored `_WORKTREE_RE.search`:
    an anchored `match_worktree` and an unanchored search agree on *whether* a
    marker segment is present whenever the path has a genuine leading component
    before the marker, which a Windows absolute path (drive-letter `C:/…` or UNC
    `\\…`) always does. They differ only when the marker is the very FIRST path
    component — a bare relative path (`dev-env-worktrees/foo` → this True, search
    False) or one at the Unix filesystem root (`/.claude/worktrees/foo` → this
    False, search True) — neither of which occurs as a resolved toplevel on this
    system (git emits `C:/…`) — see `test_is_worktree_path_*`."""
    return match_worktree(path) is not None


def canonical_repo_root(path: str) -> str:
    """Canonical checkout root for `path`. If `path` is inside a Claude-managed
    worktree, return the canonical checkout root; otherwise return `path` unchanged
    (never None — callers path-join the result unconditionally). Built on
    `canonical_root_from_worktree` so there is exactly one regex and one match
    operation behind both contracts."""
    return canonical_root_from_worktree(path) or (path or "")
