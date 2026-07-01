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
  3. Split the command into logical segments (`&&`, `||`, `;`, `\n`, `|` — same
     splitter career-playbook's hooks use). Two redirect shapes are out of
     scope for this hook's cwd-based check (known, documented gap — see
     module-level note below):
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
  5. If the command contains the `ALLOW_CANONICAL_MUTATE=1` override token,
     exit 0 — a deliberate, visible human override.
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

# Matches `.claude/worktrees/<name>` anywhere in a path — same pattern as
# ADR-024's hook. A cwd matching this is out of scope for this hook entirely;
# any command (mutating or not) is fine from inside a worktree.
_WORKTREE_RE = re.compile(
    r"[/\\]\.claude[/\\]worktrees[/\\][^/\\]+",
    re.IGNORECASE,
)

OVERRIDE_TOKENS = ("ALLOW_CANONICAL_MUTATE=1", "canonical-mutate-approved")

# Logical-segment splitter — a real command is one of these segments, never a
# substring buried in a heredoc body or prose (career-playbook #442 lesson).
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\n|\|")

# A segment redirects THAT git invocation to another repo via `git -C <path>`
# or `git --git-dir=<path>` — out of scope for this hook's cwd-based check.
# (A bare `cd <path>` is handled separately in classify() — it persists across
# the rest of the command, not just the segment it appears in.)
_REDIRECT_RE = re.compile(r"(?:^|\s)git\s+(?:-C\s+\S|--git-dir=\S)")

# Leading env-var assignments (VAR=val VAR2=val2 ...) that may precede the
# actual command in a segment — stripped before verb classification so
# `ALLOW_CANONICAL_MUTATE=1 git checkout -b foo` still classifies on `git`.
_LEADING_ENV = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")


def _strip_leading_env(segment: str) -> str:
    return _LEADING_ENV.sub("", segment, count=1)


def is_mutating_segment(segment: str) -> bool:
    """True if `segment` (after stripping leading env-var assignments) is a
    git invocation whose verb mutates working-tree/branch/history state.

    Anchored at the start of the (env-stripped) segment — a mention of one of
    these verbs later in the string (e.g. inside a commit message argument)
    does not trigger; only the actual invoked git subcommand does.
    """
    stripped = _strip_leading_env(segment).strip()
    m = re.match(r"^git(?:\.exe)?\s+(.*)$", stripped, re.IGNORECASE)
    if not m:
        return False
    rest = m.group(1).strip()
    if not rest:
        return False

    tokens = rest.split()
    verb = tokens[0].lower()

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
        sub = tokens[1].lower() if len(tokens) > 1 else ""
        return sub in ("pop", "apply")

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

    Two distinct redirect shapes, both out of scope for this hook's cwd-based
    check (documented v1 gap), handled at different granularities:

      - A bare `cd <path>` in ANY segment persists for the rest of the shell
        invocation (that's what `&&`-chaining a `cd` means) — every later
        segment actually executes in that other directory, not `cwd`. Once a
        `cd` appears anywhere in the command, the whole command is out of
        scope: exit early with None rather than flagging a later segment
        whose real execution directory this hook cannot determine.
      - `git -C <path>` / `git --git-dir=<path>` redirects only the single
        git invocation it appears on — that segment alone is skipped; other
        segments in the same command are still classified normally.
    """
    for seg in _SEGMENT_SPLIT.split(cmd):
        if re.match(r"^\s*cd(?:\s|$)", seg):
            return None  # cd persists across the rest of the command — out of scope entirely
    for seg in _SEGMENT_SPLIT.split(cmd):
        if _REDIRECT_RE.search(seg):
            continue
        if is_mutating_segment(seg):
            return seg.strip()
    return None


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

    if any(tok in cmd for tok in OVERRIDE_TOKENS):
        sys.exit(0)  # explicit, visible human override

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
    print(json.dumps({"reason": reason}))
    sys.exit(2)


if __name__ == "__main__":
    main()
