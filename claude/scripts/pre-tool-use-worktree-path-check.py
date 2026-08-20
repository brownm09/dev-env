#!/usr/bin/env python3
"""Claude Code PreToolUse hook — blocks Write/Edit/NotebookEdit calls whose
file_path targets the canonical repo root when the session is running inside a
Claude-managed worktree.

Problem: Absolute paths like `C:/Users/brown/Git/dev-env/foo.py` resolve to
the main working tree, not the active worktree. Files land in the wrong place
silently. This hook intercepts those calls before the write happens.

Recognizes two worktree-path conventions (dev-env#760): the nested
`.claude/worktrees/<name>` shape (`EnterWorktree`) and the sibling-directory
`<repo>-worktrees/<name>` shape (manual `git worktree add`, e.g.
`dev-env-worktrees/adr-096-correction`) — see `_match_worktree()` below, which
tries the nested convention first and the sibling convention as a fallback (a
review finding: trying both via one combined alternation lets the sibling
shape steal a match at a shallower position than a nested worktree occurring
deeper in the same path, e.g. one created inside a sibling-convention
worktree). A bare `<repo>-<suffix>` sibling with no `-worktrees` marker (e.g.
`dev-env-188`) is not covered; that shape is ambiguous from the path string
alone.

`_match_worktree()`'s regex is a cheap PRE-FILTER only, not the final word
(dev-env#774): a repo whose OWN root directory name literally ends in
`-worktrees` (e.g. `some-repo-worktrees`) makes the sibling pattern (now
single-sourced in `_worktree_canon.match_worktree()`, dev-env#510) mistake
an ordinary subdirectory for a worktree name and a truncated prefix for the
canonical root. Once the regex finds a *candidate* match, `_resolve_worktree_scope()`
confirms (or corrects) it against `git worktree list --porcelain` ground truth
before anything is enforced — see that function's own docstring for the full
decision table, and gap (b) in dev-env#774 for the original report.

Logic:
  1. If cwd does not match either worktree-path convention, pass immediately —
     no subprocess needed.
  2. Extract a CANDIDATE canonical_root (repo root) and worktree_root from cwd,
     then confirm/correct them via `_resolve_worktree_scope()`. If git confirms
     cwd was never really inside a worktree structure at all (the candidate
     canonical_root doesn't match git's own), pass immediately (dev-env#774
     gap (b)) — nothing here to enforce.
  3. Liveness guard (ADR-024 addendum, dev-env#328; git-membership-confirmed as
     of dev-env#774): assert the worktree is a *live*, registered worktree, not
     an orphan or removed entry. An orphaned worktree dir silently resolves
     every git command up the tree to the canonical repo's `.git`, so writes
     land on the wrong tree or in a disconnected directory invisible to git.
     Not live → exit 2 with the recovery recipe, which is rendered from
     `_worktree_recovery.RECOVERY_STEPS` rather than written inline here
     (dev-env#862, ADR-116): this message is the only recovery instruction a
     blocked session ever sees, and its former inline copy silently kept the
     `git worktree add --force` recipe that dev-env#751 had already disproven.
  4. Read file_path (Write/Edit) or notebook_path (NotebookEdit) from tool input.
  5. If the path is absolute and starts with canonical_root but NOT with
     worktree_root → exit 2 with a blocking message naming both paths and the
     corrected worktree-relative path.
  6. Otherwise pass (exit 0).

Blocking: exits 2 with JSON {"reason": "..."} — the harness refuses the tool
call and shows the reason to Claude so it can re-issue with the correct path.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Write" | "Edit" | "NotebookEdit",
    "tool_input": {"file_path": "..." | "notebook_path": "..."},
    "session_id": "...",
    "cwd": "..."
  }
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import subprocess
import sys

from _hookio import read_cwd
import _hookutil
import _journal_canon
import _worktree_canon
import _worktree_recovery
from _worktree_topology import find_worktree_by_path, parse_worktree_porcelain

# The nested/sibling worktree-path regexes and this matcher's nested-first
# ordering are single-sourced in `_worktree_canon.match_worktree()` (dev-env#510)
# — the same convention `pre-tool-use-canonical-mutate-guard.py` also consumes, so
# a future change to the worktree-location convention (`.claude/worktrees/<name>`
# nested / `<repo>-worktrees/<name>` sibling, dev-env#760) touches one module, not
# three. `main()`'s two call sites read both `group(1)` (the canonical root) and
# `group(0)` (the worktree root) off the returned match, exactly as they did off
# the byte-identical local copies this replaces.
def _match_worktree(path: str):
    """Match `path` against the nested convention first, then the sibling
    convention — delegates to the shared `_worktree_canon.match_worktree()`."""
    return _worktree_canon.match_worktree(path)

# Maps tool name → the field in tool_input that holds the file path.
_PATH_FIELD = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}


def _read_tool_input_field(data: dict, field: str) -> str:
    """Return tool_input[field] from a PreToolUse payload, or "" on any
    malformed shape -- never raises (dev-env#1031/#1033).

    Mirrors `_hookio.read_command`'s exact "never raises" contract
    (isinstance(data, dict) -> isinstance(tool_input, dict) -> isinstance(
    value, str), else "") but for a COMPUTED field name rather than the
    literal "command" every other sibling hook in this migration reads --
    this hook reads file_path (Write/Edit) or notebook_path (NotebookEdit),
    selected via `_PATH_FIELD[tool_name]`. Kept as a small local wrapper
    rather than widening `read_command`'s own signature or adding a second,
    differently-named helper to `_hookio.py`: this is the only caller today,
    and `_hookio.py`'s own module comment on `mask_quoted_spans` warns
    against generalizing a heavily-tested shared primitive for a need only
    one caller has -- if a second caller ever needs this, hoist it then.
    """
    if not isinstance(data, dict):
        return ""
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        return ""
    val = ti.get(field, "")
    return val if isinstance(val, str) else ""


def _normalize(path: str) -> str:
    """General-purpose path-identity comparison used by this file's own worktree-path
    checks (`worktree_norm`, `file_norm`, `_worktree_is_live`, `_resolve_worktree_scope`)
    as well as `_JOURNAL_ROOT` below. Delegates to `_journal_canon.normalize_journal_path()`
    — algorithmically identical — rather than carrying its own copy, so this file and the
    other engineering-journal carve-out hooks can't drift apart (dev-env#982 review). Kept
    as a locally-named thin wrapper, not inlined away, since this file's five call sites
    reference it by this name and four of them have nothing to do with the journal."""
    return _journal_canon.normalize_journal_path(path)


# PERMANENT carve-out (dev-env#750, reopened): mirrors pre-tool-use-canonical-mutate-
# guard.py's _REDIRECT_TARGET_ALLOWLIST (ADR-071) for the same repo -- see main()'s
# call site below for the full rationale. Overridable via WORKTREE_PATH_CHECK_JOURNAL_PATH
# solely so tests can point this at a disposable temp dir instead of the developer's
# real engineering-journal checkout.
# Env-var-override resolution AND normalization now single-sourced in _journal_canon.py
# (dev-env#982, ADR-133) — three other hooks duplicated this identical pattern.
# Construction goes through this file's own local `_normalize()` (used for other,
# non-journal comparisons too), which itself delegates to
# `_journal_canon.normalize_journal_path` rather than carrying a second copy of the
# algorithm (dev-env#982 review).
_JOURNAL_ROOT = _normalize(
    _journal_canon.resolve_journal_path("WORKTREE_PATH_CHECK_JOURNAL_PATH")
)


def _resolve_git_toplevel(cwd: str):
    """Return git's worktree top-level for `cwd`, or None if git can't resolve it.

    For a live worktree this is the worktree root. For an *orphaned* worktree dir
    (no `.git` link file), git walks up the tree and returns the canonical repo
    root instead — that mismatch is the orphan signature the liveness guard keys
    on. Any execution failure (git missing, timeout, non-zero exit) returns None.
    `ValueError` also fails open here (dev-env#774 review finding): `subprocess.run(...,
    text=True)` decodes git's stdout in the process locale encoding, and a path
    containing bytes undecodable there raises `UnicodeDecodeError` (a `ValueError`
    subclass) — without this, that decode error would propagate uncaught to this
    hook's outer `except Exception: sys.exit(0)`, silently disabling enforcement
    entirely instead of falling back to the fail-open contract this function exists
    to provide.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


def _worktree_is_live(
    worktree_root: str,
    cwd: str,
    *,
    path_isfile=os.path.isfile,
    git_toplevel=_resolve_git_toplevel,
) -> bool:
    """True if `worktree_root` is a live registered worktree, not an orphan.

    Two signals, cheapest first:
      1. The `.git` link file must exist AT the worktree root, as a FILE (a real
         worktree's `.git` is always a `gitdir: ...` pointer file, never a
         directory). An orphaned dir has lost it entirely — the documented
         incident (dev-env#328), caught without spawning git. `os.path.isfile`,
         not `os.path.exists` (review finding, dev-env#760): a genuine canonical
         checkout (a real clone, `.git` a directory) that merely happens to sit
         at a worktree-shaped path would otherwise pass this signal and be
         wrongly treated as a live worktree — `exists` can't tell a `.git` file
         from a `.git` directory, only `isfile` can.
      2. git's resolved top-level for `cwd` must equal `worktree_root`. This
         catches the subtle case where git mis-resolves up to the canonical repo.

    If git cannot run (returns None) but the `.git` link is present, treat the
    worktree as live — a transient git failure must not block every write when
    the link file clearly exists.
    """
    if not path_isfile(os.path.join(worktree_root, ".git")):
        return False
    top = git_toplevel(cwd)
    if top is None:
        return True
    return _normalize(top) == _normalize(worktree_root)


def _resolve_worktrees(cwd: str):
    """Run `git -C <cwd> worktree list --porcelain` and parse it via the shared
    `_worktree_topology.parse_worktree_porcelain`, or None if git can't resolve
    it at all (no `.git` found anywhere up the tree from `cwd`, git missing,
    timeout, non-zero exit, or a decode/parse failure).

    `ValueError` fails open here for the same reason `_resolve_git_toplevel` does
    (dev-env#774 review finding): a `UnicodeDecodeError` from decoding git's stdout
    is a `ValueError` subclass, and letting it propagate uncaught would silently
    disable this hook's enforcement entirely (escape to the outer
    `except Exception: sys.exit(0)`) instead of falling back to the regex +
    `_worktree_is_live` path `_resolve_worktree_scope` provides for exactly this
    "git can't answer" case. The parse call is inside the same `try` for the same
    reason, even though `parse_worktree_porcelain` is pure string splitting with no
    known raise path today — a future change to it should not have to remember to
    keep this call site's fail-open contract intact.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return parse_worktree_porcelain(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _resolve_worktree_scope(regex_canonical_root: str, regex_worktree_root: str, cwd: str):
    """Confirm (or correct) the regex-derived `(canonical_root, worktree_root)`
    candidate against `git worktree list --porcelain` ground truth, and
    determine liveness. Returns `(canonical_root, worktree_root, is_live)`:

      - `canonical_root is None` means git confirms `cwd` was never really
        inside a worktree structure at all — the regex match was a path-shape
        false positive (dev-env#774 gap (b): a repo whose OWN root directory
        name literally ends in `-worktrees`, e.g. `some-repo-worktrees`, has
        every subdirectory misclassified by `_match_worktree()` as if it were
        a worktree name). The caller should no-op (exit 0) in this case.
      - Otherwise `canonical_root`/`worktree_root` are the CONFIRMED roots —
        from git when available, else the regex's own guess — and `is_live`
        says whether the worktree is genuinely registered (False = orphaned
        or removed, block).

    Falls back to the regex-derived roots + the pre-existing `_worktree_is_live`
    liveness check when git itself can't answer at all (fail open — e.g. the
    sibling-directory orphan case, which by construction is never nested inside
    any repo for git to walk up to, so `git worktree list` fails outright) — a
    backstop, not the primary signal, per dev-env#774's "replace (or backstop)"
    framing. This preserves every pre-existing test's behavior, since all of
    them build their worktree fixtures from bogus (non-real-repo) `.git` files
    rather than actual git repos, so `_resolve_worktrees` fails for all of them
    and they exercise this fallback path unchanged.

    When git DOES answer: `regex_worktree_root` not found among the confirmed
    repo's LINKED (non-canonical) worktrees is treated as `is_live=False`
    (orphaned/removed) directly, without a further `_worktree_is_live` call —
    git's own authoritative membership list is stronger proof of "not currently
    registered" than the `.git`-isfile/`rev-parse` heuristic it replaces here.

    `regex_worktree_root` is checked against the FULL confirmed list (canonical
    entry included) BEFORE the canonical-root textual comparison, not after
    (review finding, dev-env#774): a direct match there is git's strongest
    possible confirmation that `cwd` really is inside a live, registered
    worktree, and is trusted even when the canonical-root guess would have
    disagreed — e.g. a symlink/junction/8.3-short-path component making git's
    own canonicalized path differ in FORM (not substance) from the regex's raw
    cwd-derived substring. Checking the canonical guess first, as an earlier
    version of this function did, would have exited 0 (gap (b)'s "never really
    a worktree" outcome) for that case even though `cwd` genuinely is inside a
    live worktree — silently dropping BOTH the orphan-block and the
    escape-block, this hook's core purpose, under a path-form quirk this PR
    introduced no exposure to before (the pre-#774 regex-only code never asked
    git to independently agree on a form at all). The canonical-comparison
    heuristic remains as the fallback for the one case a direct worktree-root
    match can't resolve on its own: distinguishing a genuine orphan (canonical
    confirmed correct, this specific worktree path just isn't registered) from
    gap (b) (the canonical guess itself was wrong).
    """
    worktrees = _resolve_worktrees(cwd)
    if not worktrees:
        return regex_canonical_root, regex_worktree_root, _worktree_is_live(regex_worktree_root, cwd)

    canonical_entry = worktrees[0]
    matched = find_worktree_by_path(worktrees, regex_worktree_root, normalize=_normalize)
    if matched is not None:
        # regex_worktree_root is confirmed a registered worktree of THIS
        # repository -- trust it regardless of whether the canonical-root
        # guess textually agreed (see the path-form note above).
        return canonical_entry["path"], matched["path"], True

    if _normalize(canonical_entry["path"]) != _normalize(regex_canonical_root):
        # regex_worktree_root wasn't found anywhere in the list, AND the
        # canonical guess disagrees too -- cwd was never really inside a
        # worktree structure at all (dev-env#774 gap (b)).
        return None, None, None

    # canonical_root confirmed correct, but this specific worktree path isn't
    # among the repo's registered (linked) worktrees -- orphaned or removed.
    return canonical_entry["path"], regex_worktree_root, False


def _block(reason: str) -> None:
    """Emit a blocking {"reason": ...} payload and exit 2.

    Claude Code discards stdout on a PreToolUse hook exit code 2 — only
    stderr is surfaced to the model. Write there, matching the working
    pattern in career-playbook's block-artifact-merge.py /
    block-letter-violations.py. Centralized so main()'s two independent
    block sites can't drift out of sync on this again (dev-env#469).
    """
    sys.stderr.write(json.dumps({"reason": reason}) + "\n")
    sys.exit(2)


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-worktree-path-check")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        # A valid-JSON-but-non-dict top-level payload (a list, string, number,
        # or null) would otherwise crash the very next line (dev-env#1031/
        # #1033, mirroring usage-snapshot.py's dev-env#1028 post-review fix).
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in _PATH_FIELD:
        sys.exit(0)

    # dev-env#1031/#1033: read_cwd() never raises on a present-but-non-dict
    # cwd (dev-env#1028's payload shape) -- the pre-fix unguarded read didn't
    # crash at THIS line (a single, non-chained `.get()`), but a non-string
    # cwd would crash downstream in `_match_worktree`/`_resolve_worktree_scope`
    # (regex/path ops expecting a str). Silently caught by the __main__
    # safe-exit guard below (which loses only this write-scope guard for that
    # one call -- see ADR-050 Amendment 27 for why pre-merge-findings-gate.py,
    # a blocking merge gate, was fixed first and separately on fail-open
    # severity grounds).
    cwd = read_cwd(data)
    m = _match_worktree(cwd)
    if not m:
        sys.exit(0)  # doesn't even look worktree-shaped — nothing to enforce, no subprocess needed

    # Confirm (or correct) the regex's candidate against `git worktree list`
    # ground truth (dev-env#774) rather than trusting it outright — closes the
    # gap where a repo literally named `<x>-worktrees` has every subdirectory
    # misclassified as a worktree name by the regex alone. See
    # _resolve_worktree_scope()'s own docstring for the full decision table.
    canonical_root, worktree_root, is_live = _resolve_worktree_scope(m.group(1), m.group(0), cwd)
    if canonical_root is None:
        sys.exit(0)  # dev-env#774 gap (b): git confirms cwd was never really inside a worktree structure

    # Liveness guard (dev-env#328): an orphaned worktree dir silently resolves
    # git up to the canonical repo, so *any* write from here — relative paths and
    # in-worktree absolute paths included — risks landing on the wrong tree or in
    # a disconnected directory. Check before the path-scoping below so it covers
    # all three cases, not just canonical-root absolute paths.
    if not is_live:
        reason = (
            f"[worktree-path-guard] BLOCKED: {tool_name} issued from an orphaned / "
            f"disconnected worktree. The worktree directory exists but is not a live "
            f"registered worktree (its `.git` link is missing or git resolves to the "
            f"canonical repo), so git silently operates on the CANONICAL repo and writes "
            f"land on the wrong tree or in a directory invisible to git.\n"
            f"\n"
            f"  Worktree : {worktree_root}\n"
            f"  cwd      : {cwd}\n"
            f"\n"
            # Rendered from the single-sourced recipe in `_worktree_recovery`, which the
            # docs/REFERENCE.md runbook is pinned against (ADR-116). Until dev-env#862
            # this message carried its own copy, and it kept the `worktree add --force`
            # recipe dev-env#751 had already disproven -- on the one surface a blocked
            # session actually reads.
            + _worktree_recovery.recovery_recipe(worktree_root, canonical_root)
        )
        _block(reason)

    # dev-env#1031/#1033: _read_tool_input_field() never raises on a
    # present-but-non-dict tool_input (dev-env#1028's payload shape) -- the
    # pre-fix `data.get("tool_input", {}).get(_PATH_FIELD[tool_name], "")`
    # chain crashed here. Not `_hookio.read_command`: this reads a COMPUTED
    # field name (file_path or notebook_path via `_PATH_FIELD[tool_name]`),
    # not literally "command" -- see `_read_tool_input_field`'s own docstring
    # for why this stays a small local wrapper rather than a `_hookio.py`
    # change.
    file_path = _read_tool_input_field(data, _PATH_FIELD[tool_name])
    if not file_path or not os.path.isabs(file_path):
        sys.exit(0)  # relative paths are fine

    canonical_norm = _normalize(canonical_root)
    worktree_norm = _normalize(worktree_root)
    file_norm = _normalize(file_path)

    # Engineering-journal canonical-root carve-out (dev-env#750, reopened): the Stub file
    # workflow's own documented pattern is to write directly to the EJ canonical via `-C`
    # on every PR open/merge, even from a session whose own primary repo is itself an EJ
    # worktree (claude/CLAUDE.md -> Engineering Journal -> Stub file workflow -> "Never
    # create a dedicated worktree to write a stub"). PR #756 only exempted a WRITE TARGET
    # that itself looked worktree-shaped (the sibling-worktree carve-out below); it left
    # the far more common bare-canonical-root write (no worktree segment in the target
    # path at all -- the actual Stub-file-workflow write shape) still falling through to
    # the final block. Matched by exact resolved canonical root, not basename, for the
    # same reason given in the mutate-guard's own carve-out (dev-env#576/PR#584).
    if canonical_norm == _JOURNAL_ROOT:
        sys.exit(0)

    # Must start with canonical root (with separator) to be in-scope.
    if not (file_norm == canonical_norm or file_norm.startswith(canonical_norm + os.sep)):
        sys.exit(0)

    # Already inside the worktree — correct.
    if file_norm == worktree_norm or file_norm.startswith(worktree_norm + os.sep):
        sys.exit(0)

    # Allow writes targeting another worktree under the same canonical root.
    # Those land in that worktree's own tree, not the shared canonical working tree.
    # Motivating case: a compose session writes to compose-YYYY-MM-DD while the
    # session's own cwd is a different worktree of the same repo (dev-env#750).
    target_m = _match_worktree(file_norm)
    if target_m and _normalize(target_m.group(1)) == canonical_norm:
        sys.exit(0)

    # Path targets the canonical root but not the active worktree — block.
    try:
        rel = os.path.relpath(file_path, canonical_root)
        corrected = os.path.join(worktree_root, rel)
    except ValueError:
        corrected = "<could not compute — use worktree_root + relative path>"

    reason = (
        f"[worktree-path-guard] BLOCKED: {tool_name} targets the canonical repo root, "
        f"not the active worktree. Files written here will land on the main working tree "
        f"and will not be visible from the worktree.\n"
        f"\n"
        f"  Attempted : {file_path}\n"
        f"  Worktree  : {worktree_root}\n"
        f"  Corrected : {corrected}\n"
        f"\n"
        f"Re-issue with the corrected path."
    )
    _block(reason)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
