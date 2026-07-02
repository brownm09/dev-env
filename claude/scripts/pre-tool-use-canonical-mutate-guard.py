#!/usr/bin/env python3
"""Claude Code PreToolUse hook — blocks git-mutating Bash commands run directly
in a repo's canonical (non-worktree) checkout.

Problem: two Claude Code sessions both working directly in the same canonical
checkout (no worktree involved) can collide — one session's `git checkout`
silently thrashes HEAD out from under the other, scrambling commit attribution
or (in the near-miss case) risking a stale-branch PR reverting the other
session's already-merged work. A shared checkout also shares and can exhaust
the GitHub API rate limit. See dev-env#453 for the two motivating incidents
(2026-07-01, both in career-playbook) and the rate-limit finding.

This hook is the inverse complement of ADR-024's
`pre-tool-use-worktree-path-check.py`, which covers "session IS in a worktree,
writes escape to the canonical root." This one covers the previously-uncovered
case: "session is NOT in a worktree at all, mutates the canonical root
directly" — a case ADR-024's hook is a complete no-op for, since it only fires
on Write/Edit/NotebookEdit and only when cwd matches the worktree pattern.

Logic (cheapest checks first — no subprocess spawn unless a mutating segment
is actually found):
  1. Read stdin JSON. Fail open (exit 0) on anything unparseable, a missing or
     empty `cwd`, or a non-`Bash` tool_name.
  2. If `cwd` matches the worktree path pattern (`.../.claude/worktrees/<name>`),
     exit 0 — out of scope, ADR-024's hook already covers that surface, and any
     command (mutating or not) is fine from inside a worktree.
  3. Split the command into logical segments via the shared
     `_hookio.split_top_level(cmd, split_pipe=True)` engine (dev-env#511,
     ADR-050 Amendment 7) — a quote/subshell/heredoc-aware stack parser, not
     a plain regex split (`split_pipe=True` since a mutating git invocation
     can appear after a pipe, e.g. `echo msg | git commit -F -`). This means:
       - `&&`/`||`/`;`/`\n`/`|` characters inside a single- or double-quoted
         string (e.g. `git log --grep="foo && git checkout -b evil"`) are
         never mistaken for segment boundaries — the prior plain-regex
         splitter had no quote-tracking and would misclassify a command like
         this harmless `git log` search as a `checkout`.
       - A `$(...)` command substitution — including a heredoc fed to it,
         e.g. the `gh issue create --body "$(cat <<'EOF' ... EOF)"` idiom
         this repo's own CLAUDE.md documents extensively for
         git-commit-message heredocs — is opaque: its entire span, heredoc
         body included, stays inside whichever segment contains it and never
         produces its own spurious segment. This generalizes the former
         dev-env#481 fix (which only recognized the exact
         `$(cat <<MARKER...)` shape via a dedicated regex) to any heredoc
         inside any `$(...)`.
       - A *bare* (non-command-substitution) heredoc body is likewise
         skipped as one opaque span rather than split line-by-line on `\n`
         — closing a previously untested version of the same #481 gap: a
         body line that itself *begins* with a mutating verb (e.g.
         `git status <<EOF` / `git commit --amend` / `EOF`) would otherwise
         become its own segment and be misclassified as a real invocation.
     Two redirect shapes are still out of scope for this hook's cwd-based
     check (known, documented gap — see module-level note below), scanned
     separately after segmentation:
       - A bare `cd <path>` in ANY segment persists for the rest of the shell
         invocation (that's what `&&`-chaining a `cd` means), so the whole
         command is treated as out of scope the moment a `cd` appears anywhere
         in it — a later segment's real execution directory is then unknown,
         not `cwd`.
       - `git -C <path>` / `git --git-dir=<path>` redirects only the single
         git invocation carrying the flag — just that segment is skipped;
         other segments in the same command are still classified normally.
     A command that redirects *into* the canonical root from elsewhere
     requires deliberate, visible authorship rather than the silent
     default-cwd collision #453 documents, so deferring both shapes is
     acceptable for v1 — same class of scope-limiting judgment ADR-024 made
     for its own Bash coverage.
  4. Classify each remaining segment, anchored at the segment start (not a
     substring match — the career-playbook #442 heredoc-mention lesson: a
     mutating verb merely *mentioned* inside a heredoc body or prose must not
     trigger). If no segment is mutating, exit 0 — nothing here needs a git
     subprocess at all.
  5. If the `ALLOW_CANONICAL_MUTATE=1` override token appears as a genuine
     leading prefix on the command or on one of its split-out segments (not
     merely mentioned as a substring anywhere, e.g. inside a commit message
     argument or inside a `$(...)`/heredoc span — step 3's opacity applies
     identically here), exit 0 — a deliberate, visible human override.
  6. Resolve the git toplevel for `cwd` via `git -C <cwd> rev-parse
     --show-toplevel`. Fail open if git can't resolve one at all (not a repo,
     git missing, timeout) — that toplevel, once resolved, *is* the canonical
     root by definition (cwd already failed the worktree-pattern check above).
  7. Exit 2 with a blocking JSON `{"reason": ...}` naming the matched command,
     the canonical root, why it's dangerous, and the two remedies (isolate via
     a worktree, or override).

Mutating verbs blocked: checkout (branch switch or `-b`; a bare
`git checkout <path>` with no `--` is conservatively treated as a possible
branch argument and blocked — use `git checkout -- <path>` for file restores),
switch, commit, merge, rebase, reset, cherry-pick, revert, stash pop/apply,
branch -d/-D, and pull *except* when the same segment also contains
`--ff-only` (fast-forwarding a canonical checkout to origin/main is the
common, safe sync operation and must stay zero-friction).

Explicitly NOT blocked (must stay zero-friction): status, log, diff, show,
fetch, branch --show-current, rev-parse, ls-tree, blame, remote -v, plain
`git branch` (no -d/-D), `git stash list`/`show`, `git checkout -- <path>`,
`git pull --ff-only`, and anything non-git. Plain Read/Grep/Glob against a
canonical checkout is untouched entirely since this hook only matches Bash.

Known, documented gap (not solved, v1 deferral): a command that `cd`s or
`-C`s *into* the canonical root from elsewhere (e.g. from a worktree's Bash,
`git -C C:/Users/brown/Git/dev-env checkout -b foo`) is not caught by this
hook's cwd-based check. That requires deliberate, visible authorship rather
than the silent default-cwd collision #453 documents, so it is an acceptable
v1 deferral — extend if it recurs in practice (same incremental-hardening
precedent as ADR-024's own orphan-liveness addendum).

Stdin JSON shape (PreToolUse):
  {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "..."},
    "session_id": "...",
    "cwd": "..."
  }
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import subprocess
import sys

from _hookio import split_top_level

# Matches `.claude/worktrees/<name>` anywhere in a path — same pattern as
# ADR-024's hook. A cwd matching this is out of scope for this hook entirely;
# any command (mutating or not) is fine from inside a worktree.
_WORKTREE_RE = re.compile(
    r"[/\\]\.claude[/\\]worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)

# The sole override token. A genuine leading prefix on a command/segment (not
# a substring appearing anywhere, e.g. inside a commit message) bypasses the
# block — see the anchored check in `_has_override()` below.
OVERRIDE_TOKEN = "ALLOW_CANONICAL_MUTATE=1"

# A segment redirects THAT git invocation to another repo via `git -C <path>`
# or `git --git-dir=<path>` — out of scope for this hook's cwd-based check.
# (A bare `cd <path>` is handled separately in classify() — it persists across
# the rest of the command, not just the segment it appears in.)
_REDIRECT_RE = re.compile(r"(?:^|\s)git\s+(?:-C\s+\S|--git-dir=\S)")

# `cd <path>` at the start of a segment (after stripping leading env-var
# assignments — see `_strip_leading_env`). Compiled once; reused by both the
# cd-scan loop in classify() and anywhere else that needs to detect a cd.
_CD_RE = re.compile(r"^\s*cd(?:\s|$)")

# A git invocation at the start of a segment (after stripping leading env-var
# assignments) — captures the rest of the command (the subcommand + its
# args) for verb classification in is_mutating_segment().
_GIT_INVOCATION_RE = re.compile(r"^git(?:\.exe)?\s+(.*)$", re.IGNORECASE)

# Git-level options that can precede the actual subcommand (git's own option
# grammar, not the subcommand's) — e.g. `git -c gc.auto=0 stash pop` or
# `git --no-optional-locks stash apply`. `-c <name>=<value>` takes a
# space-separated value token; the rest are flags with no separate value
# token. `-C <path>` / `--git-dir=<path>` are deliberately NOT in this list —
# those are handled upstream by `_REDIRECT_RE`, which skips the whole segment
# rather than classifying it, so a redirected invocation never reaches here.
_GIT_LEVEL_FLAG_NO_VALUE = {"--no-optional-locks", "--no-pager", "-p", "--paginate", "--bare"}
_GIT_LEVEL_FLAG_WITH_VALUE = {"-c"}

# Leading env-var assignments (VAR=val VAR2=val2 ...) that may precede the
# actual command in a segment — stripped before verb classification so
# `ALLOW_CANONICAL_MUTATE=1 git checkout -b foo` still classifies on `git`.
_LEADING_ENV = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")


def _strip_leading_env(segment: str) -> str:
    return _LEADING_ENV.sub("", segment, count=1)


def _skip_git_level_flags(tokens: list) -> list:
    """Drop a leading run of git-level options (`git -c gc.auto=0 stash
    pop`, `git --no-optional-locks stash apply`) so the actual subcommand
    lands at index 0 for verb classification — otherwise `tokens[0]` is `-c`
    or `--no-optional-locks`, not the real verb, and the whole invocation
    silently falls through the `if verb == ...` chain to the default `False`
    (not mutating), unblocking a real stash pop/apply.
    """
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GIT_LEVEL_FLAG_WITH_VALUE:
            i += 2  # flag + its value token
            continue
        if tok in _GIT_LEVEL_FLAG_NO_VALUE:
            i += 1
            continue
        break
    return tokens[i:]


def is_mutating_segment(segment: str) -> bool:
    """True if `segment` (after stripping leading env-var assignments) is a
    git invocation whose verb mutates working-tree/branch/history state.

    Anchored at the start of the (env-stripped) segment — a mention of one of
    these verbs later in the string (e.g. inside a commit message argument)
    does not trigger; only the actual invoked git subcommand does.
    """
    stripped = _strip_leading_env(segment).strip()
    m = _GIT_INVOCATION_RE.match(stripped)
    if not m:
        return False
    rest = m.group(1).strip()
    if not rest:
        return False

    tokens = _skip_git_level_flags(rest.split())
    if not tokens:
        return False
    verb = tokens[0].lower()

    # NOTE: the mutating-verb list is spread across this branch chain with no
    # single canonical source. It is independently re-spelled in four prose
    # locations that must stay in sync whenever a verb is added, removed, or
    # its condition changes: the module docstring above ("Mutating verbs
    # blocked:"), claude/CLAUDE.md's "Never mutate git state directly..."
    # bullet, docs/adr/071-canonical-checkout-mutate-guard-hook.md's
    # "Mutating verbs blocked:" line, and docs/REFERENCE.md's ADR-071
    # pointer. Update all four when this branch chain changes.
    if verb in ("switch", "commit", "merge", "rebase", "reset", "cherry-pick", "revert"):
        return True

    if verb == "checkout":
        # `git checkout -- <path>` is a file restore, not a branch switch —
        # explicitly allowed. Anything else (bare path, branch name, `-b`) is
        # conservatively treated as a possible branch argument and blocked.
        if "--" in tokens[1:]:
            return False
        return True

    if verb == "stash":
        # Scan all remaining tokens for the subcommand, not just tokens[1] —
        # a stash-level flag can precede pop/apply, e.g. `git -c gc.auto=0
        # stash pop`, `git --no-optional-locks stash apply`, or
        # `git stash --quiet pop`, pushing the real subcommand off a fixed
        # tokens[1] position. Every other verb branch here already scans
        # tokens[1:] for its flags — make stash consistent rather than
        # trusting a fixed position (a prior version checked only tokens[1]
        # and silently let flag-prefixed stash pop/apply through unblocked).
        return any(t.lower() in ("pop", "apply") for t in tokens[1:])

    if verb == "branch":
        # Plain `git branch` (list) or `git branch <name>` (create, no switch)
        # is read-only/non-collision-prone and stays zero-friction. Only the
        # delete flags mutate in the way #453 is about.
        return any(t in ("-d", "-D", "--delete") for t in tokens[1:])

    if verb == "pull":
        # Fast-forward-only pull is the common, safe canonical-checkout sync
        # operation and must stay zero-friction; any other pull mutates.
        return "--ff-only" not in tokens[1:]

    return False


def classify(cmd: str):
    """Return the first mutating segment in `cmd`, or None if none found.

    Segments come from the shared `_hookio.split_top_level(cmd,
    split_pipe=True)` engine (dev-env#511, ADR-050 Amendment 7) — a
    quote/subshell/heredoc-aware stack parser, not a plain regex split. This
    means `&&`/`||`/`;`/`\n`/`|` characters inside a quoted string (e.g.
    `git log --grep="foo && git checkout -b evil"`) are never mistaken for
    segment boundaries, and a `$(...)` command substitution — including a
    heredoc fed to it, e.g. the `gh issue create --body "$(cat <<'EOF' ...
    EOF)"` idiom this repo's own CLAUDE.md documents for git-commit-message
    heredocs — stays opaque: its entire span, heredoc body included, never
    produces its own spurious segment. This generalizes the former
    dev-env#481 fix (which only recognized the exact `$(cat <<MARKER...)`
    shape via a dedicated regex) to any heredoc inside any `$(...)`, and
    additionally covers a *bare* (non-command-substitution) heredoc body,
    which the old regex never touched at all — a body line that itself
    *begins* with a mutating verb (e.g. `git status <<EOF` / `git commit
    --amend` / `EOF`) no longer becomes its own segment.

    Two distinct redirect shapes, both out of scope for this hook's cwd-based
    check (documented v1 gap), handled at different granularities on top of
    that segment list:

      - A bare `cd <path>` in ANY segment persists for the rest of the shell
        invocation (that's what `&&`-chaining a `cd` means) — every later
        segment actually executes in that other directory, not `cwd`. Once a
        `cd` appears anywhere in the command, the whole command is out of
        scope: exit early with None rather than flagging a later segment
        whose real execution directory this hook cannot determine. The cd
        check runs against the env-stripped segment (same helper
        `is_mutating_segment` uses) so `FOO=1 cd /tmp && git checkout -b x`
        is still recognized as a cd-redirect — an unstripped match would miss
        it and let the checkout get falsely blocked even though it targets
        `/tmp`, not the canonical root.
      - `git -C <path>` / `git --git-dir=<path>` redirects only the single
        git invocation it appears on — that segment alone is skipped; other
        segments in the same command are still classified normally.
    """
    segments = split_top_level(cmd, split_pipe=True)
    for seg in segments:
        if _CD_RE.match(_strip_leading_env(seg)):
            return None  # cd persists across the rest of the command — out of scope entirely
    for seg in segments:
        if _REDIRECT_RE.search(seg):
            continue
        if is_mutating_segment(seg):
            return seg.strip()
    return None


def _has_override(cmd: str) -> bool:
    """True if the override token is a genuine leading prefix on some
    segment of `cmd` — not merely a substring appearing anywhere (e.g. inside
    a commit message: `git commit -m "ALLOW_CANONICAL_MUTATE=1 was
    mentioned"` must NOT bypass the block). Uses the same
    `_hookio.split_top_level(cmd, split_pipe=True)` segmenting `classify()`
    uses, so the override is recognized in exactly the positions a real shell
    would treat it as an env-var assignment: at the very start of the
    command, or at the start of any top-level segment — and, symmetrically
    with `classify()`, never inside a quoted string or a `$(...)`/heredoc
    span (e.g. documentation prose showing the override syntax inside a `gh
    issue create --body "$(cat <<'EOF' ... EOF)"` argument must not bypass
    the block for an unrelated real mutating segment elsewhere in the same
    command).
    """
    for seg in split_top_level(cmd, split_pipe=True):
        stripped = seg.strip()
        if stripped == OVERRIDE_TOKEN or stripped.startswith(OVERRIDE_TOKEN + " "):
            return True
    return False


def _resolve_git_toplevel(cwd: str):
    """Return git's worktree top-level for `cwd`, or None if git can't resolve
    it (not a repo, git missing, timeout, non-zero exit) — the fail-open path.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)  # valid JSON but not an object (e.g. `[]`, `"x"`, `123`, `null`) -> fail open

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    cwd = data.get("cwd", "") or ""
    if not cwd:
        sys.exit(0)  # missing/empty cwd -> can't determine scope -> fail open

    if _WORKTREE_RE.search(cwd):
        sys.exit(0)  # inside a worktree — out of scope, ADR-024 covers this surface

    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not cmd:
        sys.exit(0)

    matched = classify(cmd)
    if matched is None:
        sys.exit(0)  # no mutating segment found

    if _has_override(cmd):
        sys.exit(0)  # explicit, visible human override (anchored prefix, not a substring mention)

    canonical_root = _resolve_git_toplevel(cwd)
    if canonical_root is None:
        sys.exit(0)  # not a git repo at all / git unavailable -> fail open

    reason = (
        f"[canonical-mutate-guard] BLOCKED: a git-mutating command was issued directly "
        f"in a canonical (non-worktree) checkout. Two Claude Code sessions sharing one "
        f"canonical checkout can collide — one session's checkout/commit/reset silently "
        f"thrashes HEAD out from under another concurrent session, scrambling commit "
        f"attribution or risking a stale-branch PR reverting the other session's already-"
        f"merged work. A shared checkout also shares (and can exhaust) the GitHub API "
        f"rate limit. See dev-env#453 for the two motivating incidents.\n"
        f"\n"
        f"  Command : {matched}\n"
        f"  Root    : {canonical_root}\n"
        f"\n"
        f"Remedies:\n"
        f"  1. Isolate into a worktree — EnterWorktree, or:\n"
        f"     git -C {canonical_root} worktree add <path> -b <branch> origin/main\n"
        f"  2. If you've confirmed no other session is active in this checkout, override:\n"
        f"     ALLOW_CANONICAL_MUTATE=1 {matched}"
    )
    # Claude Code discards stdout on a PreToolUse hook exit code 2 — only
    # stderr is surfaced to the model. Write there, matching the working
    # pattern in career-playbook's block-artifact-merge.py /
    # block-letter-violations.py.
    sys.stderr.write(json.dumps({"reason": reason}) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
