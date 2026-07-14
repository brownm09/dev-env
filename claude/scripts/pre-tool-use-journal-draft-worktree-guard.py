#!/usr/bin/env python3
"""Claude Code PreToolUse hook — blocks a Bash command that would check out
engineering-journal's shared `draft/YYYY-MM-DD` branch anywhere except the
canonical checkout.

Problem: git allows a branch checked out in only one worktree at a time. The
documented Stub file workflow (claude/CLAUDE.md -> Engineering Journal -> Stub
file workflow) relies on that constraint working IN ITS FAVOR — every
concurrent session reaches `draft/YYYY-MM-DD` through the SAME canonical
checkout via `git -C <journal> <command>`, never a dedicated worktree. When a
session instead runs `git worktree add <path> ... draft/YYYY-MM-DD` (or an
ambient/redirected `git checkout draft/YYYY-MM-DD` outside the canonical), it
locks the branch to that worktree and blocks the canonical — and every other
concurrent session — from reaching it until the worktree is parked or
removed. Confirmed live twice on 2026-07-12
(`.claude/worktrees/stub-823-120134`, `.claude/worktrees/stub-829-165612`,
each locking that day's draft branch). See dev-env#747, ADR-105.

This is the OPPOSITE-direction sibling of `pre-tool-use-canonical-mutate-guard.py`
(ADR-071): that hook blocks a mutating command when it targets a canonical
checkout; this one blocks a checkout of one specific shared branch when it
does NOT target the canonical. Kept as a separate file rather than folded
into that one, for the same reason ADR-071 itself gives for not folding into
ADR-024's hook: the two fire on opposite conditions, and `git worktree add`
is a verb that file's mutating-verb classifier has never covered (it guards
against mutating an EXISTING checkout, not against creating a new one).

Two blocked shapes:
  1. `git worktree add <path> ... <branch>` where <branch> matches
     DRAFT_BRANCH_RE, from ANY cwd — unconditional, no git resolution needed.
     No legitimate flow anywhere in this codebase creates a worktree checked
     out directly onto a draft branch: journal-compose's own isolated
     worktree (ADR-082) checks out DETACHED, never a named draft/YYYY-MM-DD
     branch.
  2. `git checkout|switch [-b|-B <branch>] <branch>` where <branch> matches
     DRAFT_BRANCH_RE, ambient or redirected via `-C`/`--git-dir`/`--work-tree`
     — blocked UNLESS the resolved git toplevel IS the engineering-journal
     canonical exactly (the one legitimate case: the documented workflow
     itself, `git -C <journal> checkout draft/YYYY-MM-DD`).

Segment extraction (`find_worktree_add_blocks`, `find_checkout_candidates`) is
pure/offline, mirroring the sibling hook's own `find_mutating_segments()`
split: string work happens here, the git subprocess that resolves a
`checkout`/`switch` candidate's actual target is deliberately `main()`'s job,
so the pure-function test layer never shells out. `find_worktree_add_blocks`
needs no such resolution at all — there is no legitimate target for that
shape, full stop.

Git-command parsing (`_parse_git_prefix`, `_tokenize`, `_normalize_redirect_dir`,
`_resolve_git_toplevel`, `_first_line`, `_git_rest_tokens`, plus the
flag-classification constants) is a deliberate, documented duplicate of the
identical helpers in `pre-tool-use-canonical-mutate-guard.py`, not a shared
import. That logic has a review-found-bug history (ADR-071 Amendment 2 alone
fixed five distinct bugs in it: quoted paths with spaces, relative-redirect
resolution against the wrong cwd, null-byte crashes, basename-vs-exact-path
carve-out matching, missing memoization) — this file copies the ALREADY-fixed,
tested implementation verbatim (including the relative-redirect-resolves-
against-command-cwd fix) rather than risk a fresh reimplementation
reintroducing one of them, and copying (unlike a shared refactor mid-fix)
carries zero risk of perturbing that hook's own extensive existing test
suite. Matches this codebase's own stated "tolerate duplication through two
consumers, extract at a third" convention (`_worktree_topology.py`'s module
docstring; ADR-093's Maintainability section) — this is the second consumer.
Extract into `_hookio.py` if/when a third caller needs the identical parsing.

Fail-open (exit 0) on anything unparseable, a missing/empty cwd, a tool_name
that is neither Bash nor PowerShell, or an unresolvable git path — matches the
sibling hook's own fail-open contract throughout.

Also fires for the PowerShell tool (dev-env#620): registered under both the
Bash and PowerShell PreToolUse matchers in settings.json, mirroring the
sibling canonical-mutate-guard hook's own PowerShell extension.

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "..."},
    "session_id": "...",
    "cwd": "..."
  }
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import shlex
import subprocess
import sys

from _hookio import is_absolute_path, split_top_level
import _hookutil

# draft/YYYY-MM-DD, or draft/YYYY-MM-DD-recovery (docs/REFERENCE.md's
# documented recovery-branch suffix for the draft/YYYY-MM-DD-recovery
# runbook). Anchored full-match — a branch merely CONTAINING this shape
# (e.g. a hypothetical `feature/draft/2026-01-01-preview`) must not match.
DRAFT_BRANCH_RE = re.compile(r"^draft/\d{4}-\d{2}-\d{2}(-recovery)?$")

# Overridable so a test can point this at a disposable temp directory instead
# of the developer's actual engineering-journal checkout — mirrors
# journal-canonical-guard.py's JOURNAL_CANONICAL_GUARD_REPO_PATH and
# pre-tool-use-canonical-mutate-guard.py's CANONICAL_MUTATE_GUARD_JOURNAL_PATH.
JOURNAL_REPO = (
    os.environ.get("JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH", "C:/Users/brown/Git/engineering-journal")
    .replace("\\", "/")
    .rstrip("/")
    .lower()
)

# The sole override token — mirrors ALLOW_CANONICAL_MUTATE=1's convention on
# the sibling hook. No currently-known legitimate case, but included for
# consistency, and because this is not one of the two specially-designated
# no-override fail-closed gates (pre-auto-merge-checkpoint-gate.py,
# pre-tool-use-journal-compose-force-guard.py), whose no-override design
# rests on much stronger, specific reasons that don't apply here.
OVERRIDE_TOKEN = "ALLOW_JOURNAL_DRAFT_WORKTREE=1"

_CD_RE = re.compile(r"^\s*cd(?:\s|$)")

# --- begin deliberate duplicate of pre-tool-use-canonical-mutate-guard.py's
# git-command-parsing helpers (see module docstring for why) ---

_GIT_REDIRECT_FLAGS = ("-C", "--git-dir", "--work-tree")
_GIT_LEVEL_FLAG_NO_VALUE = {"--no-optional-locks", "--no-pager", "-p", "--paginate", "--bare"}
_GIT_LEVEL_FLAG_WITH_VALUE = {"-c"}
_LEADING_ENV = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")
_GIT_INVOCATION_RE = re.compile(r"^git(?:\.exe)?\s+(.*)$", re.IGNORECASE)


def _strip_leading_env(segment: str) -> str:
    return _LEADING_ENV.sub("", segment, count=1)


def _first_line(segment: str) -> str:
    """segment's own first physical line — a heredoc/$() body must never be
    mistaken for invocation syntax. See the sibling hook's own docstring."""
    return segment.split("\n", 1)[0]


def _normalize_redirect_dir(flag: str, value: str) -> str:
    if flag == "--git-dir":
        trimmed = value.replace("\\", "/").rstrip("/")
        if trimmed.lower().endswith("/.git"):
            return trimmed[: -len("/.git")] or value
        if trimmed.lower() == ".git":
            return "."
        return value
    return value


def _parse_git_prefix(tokens: list):
    """Consume a leading run of git-level options, returning
    (redirect_dirs, remaining_tokens); remaining_tokens[0] is the real verb.
    Identical logic to the sibling hook's own function of the same name."""
    redirect_dirs = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in _GIT_REDIRECT_FLAGS:
            if i + 1 < n:
                redirect_dirs.append(_normalize_redirect_dir(tok, tokens[i + 1]))
                i += 2
            else:
                i += 1
            continue
        matched_eq = False
        for flag in ("--git-dir", "--work-tree"):
            if tok.startswith(flag + "="):
                redirect_dirs.append(_normalize_redirect_dir(flag, tok.split("=", 1)[1]))
                i += 1
                matched_eq = True
                break
        if matched_eq:
            continue
        if tok in _GIT_LEVEL_FLAG_WITH_VALUE:
            i += 2
            continue
        if tok in _GIT_LEVEL_FLAG_NO_VALUE:
            i += 1
            continue
        break
    return redirect_dirs, tokens[i:]


def _tokenize(rest: str) -> list:
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.split()


def _git_rest_tokens(segment: str):
    """Tokens after `git` on segment's first physical line, or None if the
    (env-stripped) segment isn't a git invocation. Identical logic to the
    sibling hook's own function of the same name."""
    stripped = _strip_leading_env(segment).strip()
    m = _GIT_INVOCATION_RE.match(_first_line(stripped))
    if not m:
        return None
    rest = m.group(1).strip()
    if not rest:
        return None
    return _tokenize(rest)


def _resolve_git_toplevel(cwd: str):
    """Return git's worktree top-level for cwd, or None if unresolvable (the
    fail-open path) — identical contract to the sibling hook's own helper."""
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


# --- end duplicated section ---


def _is_journal_canonical(root: str) -> bool:
    """True iff `root` (a resolved git toplevel) IS the engineering-journal
    canonical — the one legitimate target for a draft/YYYY-MM-DD checkout."""
    return root.replace("\\", "/").rstrip("/").lower() == JOURNAL_REPO


def _worktree_add_target(tokens_after_add: list) -> list:
    """The branch/commit-ish candidates `git worktree add` would check out.

    `--detach` checks out a detached HEAD at the given commit-ish — it holds
    no branch ref at all, so nothing here can ever be a squat regardless of
    what the other tokens look like; returns `[]` unconditionally in that
    case (review finding: a bare `--detach <draft-branch>` was previously a
    false-positive over-block).

    Otherwise, if `-b`/`-B` names a NEW branch, that new branch — not any
    commit-ish startpoint elsewhere in the command — is what actually gets
    checked out, so only ITS value is returned (`git worktree add -b myfix
    <path> draft/2026-07-12` bases a new, differently-named branch off a
    draft branch as a mere starting point; it does not lock draft/2026-07-12
    itself, and must not be blocked). Otherwise, any non-flag token is a
    candidate (typically `<path>` then `<commit-ish>`) — deliberately
    permissive since `git worktree add`'s full flag grammar is not modeled
    here; the caller checks every candidate against DRAFT_BRANCH_RE.
    """
    if "--detach" in tokens_after_add:
        return []
    for i, tok in enumerate(tokens_after_add):
        if tok in ("-b", "-B"):
            return [tokens_after_add[i + 1]] if i + 1 < len(tokens_after_add) else []
    return [t for t in tokens_after_add if not t.startswith("-")]


def find_worktree_add_blocks(cmd: str, segments: list = None) -> list:
    """Return the segment text of every top-level `git worktree add ...
    <draft-branch>` invocation — unconditionally blockable. Pure/offline: no
    git subprocess, since no legitimate target exists for this shape at all.

    Deliberately does NOT apply the `cd`-takes-the-command-out-of-scope guard
    `find_checkout_candidates` uses: that guard exists because a `checkout`/
    `switch` candidate's blockability depends on resolving a real cwd, which
    a preceding `cd` makes unknowable. This function needs no cwd at all — it
    blocks purely on the branch-name token, unconditionally, regardless of
    where the worktree would physically be created — so a preceding `cd`
    provides no cover (review finding: `cd <repo> && git worktree add <path>
    draft/YYYY-MM-DD` previously bypassed the block entirely, reproducing the
    exact incident this hook exists to prevent).
    """
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    out = []
    for seg in segments:
        tokens = _git_rest_tokens(seg)
        if not tokens:
            continue
        _redirect_dirs, tokens = _parse_git_prefix(tokens)
        if len(tokens) < 2 or tokens[0].lower() != "worktree" or tokens[1].lower() != "add":
            continue
        targets = _worktree_add_target(tokens[2:])
        if any(DRAFT_BRANCH_RE.match(t) for t in targets):
            out.append(seg.strip())
    return out


def find_checkout_candidates(cmd: str, segments: list = None) -> list:
    """Return [{"segment": str, "redirect_dirs": [str, ...]}, ...] for every
    top-level `checkout`/`switch` segment that would check out a
    DRAFT_BRANCH_RE-matching branch. Pure/offline — the caller (`main()`)
    resolves each candidate's actual target and decides whether it's
    blockable (whether it lands on the journal canonical or not).

    A bare `cd <path>` in a segment persists across the REST of the shell
    invocation, so every segment from that point on has an unknown real
    execution directory and is excluded — order-sensitively: a candidate
    BEFORE the first `cd` still executed in the known cwd and is still
    returned. (dev-env#762 review: an earlier version scanned ALL segments
    for a `cd` upfront and returned `[]` for the WHOLE command the instant any
    segment matched, regardless of position — so a real candidate before a
    later, unrelated `cd` was incorrectly cleared too. Confirmed exploitable
    via this PR's own `{`-as-split-trigger addition to `_hookio.split_top_level`:
    `git checkout draft/2026-07-14; if ($?) { cd C:/elsewhere }` segments the
    braced `cd` on its own, newly reaching the old whole-command escape and
    silently un-blocking the preceding draft-branch checkout — mirrors the
    identical fix in the sibling `pre-tool-use-canonical-mutate-guard.py`.)
    """
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    out = []
    for seg in segments:
        if _CD_RE.match(_strip_leading_env(seg)):
            break  # cd persists across the REST of the command -- only prior segments are known-safe to evaluate
        tokens = _git_rest_tokens(seg)
        if not tokens:
            continue
        redirect_dirs, tokens = _parse_git_prefix(tokens)
        if not tokens:
            continue
        verb = tokens[0].lower()
        if verb not in ("checkout", "switch"):
            continue
        rest = tokens[1:]
        if verb == "checkout" and "--" in rest:
            # `checkout <tree-ish> -- <paths>` is a file restore, not a branch switch --
            # but only when a real pathspec actually follows `--`. A TRAILING `--` with
            # nothing after it (`checkout draft/2026-07-12 --`) still switches branches
            # (verified against real git behavior) and must not be exempted (review
            # finding: this previously let a trailing-`--` squat through unblocked).
            dash_index = rest.index("--")
            if dash_index < len(rest) - 1:
                continue
        if not any(DRAFT_BRANCH_RE.match(t) for t in rest):
            continue
        out.append({"segment": seg.strip(), "redirect_dirs": redirect_dirs})
    return out


def _resolve_checkout_target(candidate: dict, cwd: str, toplevel_cache: dict):
    """Resolve one find_checkout_candidates() candidate to a git toplevel, or
    None if unresolvable (fail-open). A relative redirect dir is resolved
    against `cwd` (the command's own cwd from the PreToolUse payload, not the
    hook script's own process cwd) before being handed to
    `_resolve_git_toplevel` — same fix as the sibling hook's
    `_blockable_redirect_root` (review finding on dev-env#576/PR#584).

    `toplevel_cache` memoizes resolved-path -> toplevel across the whole
    command (one dict per Bash call, shared across every candidate) so a
    command with several draft-branch checkout segments repeating the same
    target spawns at most one `git rev-parse` per distinct resolved path —
    mirrors the sibling hook's own `toplevel_cache` (added there as a
    dev-env#576/PR#584 review fix for a crafted worst case that spawned 15
    subprocesses for one command; review finding that this hook had silently
    dropped that hardening when the parsing helpers were copied).
    """
    redirect_dirs = candidate["redirect_dirs"]
    targets = redirect_dirs if redirect_dirs else [cwd]
    for d in targets:
        resolved = d if is_absolute_path(d) else os.path.join(cwd, d)
        if resolved not in toplevel_cache:
            toplevel_cache[resolved] = _resolve_git_toplevel(resolved)
        root = toplevel_cache[resolved]
        if root is not None:
            return root
    return None


def _has_override(cmd: str, segments: list = None) -> bool:
    """True if OVERRIDE_TOKEN is a genuine leading prefix on some top-level
    segment of `cmd` — not merely a substring appearing anywhere. Mirrors the
    sibling hook's own `_has_override`."""
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    for seg in segments:
        stripped = seg.strip()
        if stripped == OVERRIDE_TOKEN or stripped.startswith(OVERRIDE_TOKEN + " "):
            return True
    return False


def _emit_block(matched: str, root) -> None:
    """Write the blocking `{"reason": ...}` JSON to stderr and exit 2 — same
    stderr-only convention as the sibling hook (Claude Code discards a
    PreToolUse hook's stdout on exit 2)."""
    target_line = f"\n  Target  : {root}" if root else ""
    reason = (
        f"[journal-draft-worktree-guard] BLOCKED: this command would check out a "
        f"draft/YYYY-MM-DD engineering-journal branch outside the canonical checkout. "
        f"git allows a branch in only one worktree at a time -- doing this locks the "
        f"branch to a throwaway worktree and blocks the canonical (and every other "
        f"concurrent stub-writing session) from reaching it until the throwaway worktree "
        f"is parked or removed. Confirmed recurring: dev-env#747 (live incidents "
        f".claude/worktrees/stub-823-120134, stub-829-165612, 2026-07-12).\n"
        f"\n"
        f"  Command : {matched}{target_line}\n"
        f"\n"
        f"The Stub file workflow is a deliberate, narrow exception to \"isolate "
        f"cross-repo work in a worktree\" -- run every step directly against the "
        f"canonical instead:\n"
        f"  git -C C:/Users/brown/Git/engineering-journal checkout main && "
        f"git -C C:/Users/brown/Git/engineering-journal pull\n"
        f"  git -C C:/Users/brown/Git/engineering-journal checkout -b draft/YYYY-MM-DD"
        f"      # first session of the day\n"
        f"  git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD && "
        f"git -C C:/Users/brown/Git/engineering-journal pull   # subsequent sessions\n"
        f"\n"
        f"See claude/CLAUDE.md -> Engineering Journal -> Stub file workflow, and ADR-105.\n"
        f"\n"
        f"If you've confirmed this really is a legitimate exception, override:\n"
        f"  {OVERRIDE_TOKEN} {matched}"
    )
    sys.stderr.write(json.dumps({"reason": reason}) + "\n")
    sys.exit(2)


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-journal-draft-worktree-guard")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    cwd = data.get("cwd", "") or ""
    if not cwd:
        sys.exit(0)

    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not cmd:
        sys.exit(0)

    # Parsed once and reused by every extractor + the override check below —
    # mirrors the sibling hook's own main() (avoids re-parsing `cmd` per call).
    segments = split_top_level(cmd, split_pipe=True)

    wt_blocks = find_worktree_add_blocks(cmd, segments)
    checkout_candidates = find_checkout_candidates(cmd, segments)
    if not wt_blocks and not checkout_candidates:
        sys.exit(0)

    if _has_override(cmd, segments):
        sys.exit(0)

    if wt_blocks:
        _emit_block(wt_blocks[0], None)  # writes stderr JSON + exit 2

    # Shared across every _resolve_checkout_target() call for this command so a
    # redirect target repeated across multiple candidates resolves via `git
    # rev-parse` at most once (dev-env#576/PR#584 review finding, ported here).
    toplevel_cache = {}
    for candidate in checkout_candidates:
        root = _resolve_checkout_target(candidate, cwd, toplevel_cache)
        if root is None:
            continue  # unresolvable target -> fail open, keep scanning later candidates
        if _is_journal_canonical(root):
            continue  # the one legitimate case
        _emit_block(candidate["segment"], root)  # writes stderr JSON + exit 2

    sys.exit(0)  # no blockable match found


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
