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
  3. Strip every `$(cat <<[-]['"]MARKER['"] ... MARKER)` span from the command
     before any segment splitting happens — a heredoc fed to `cat` and used as
     a command-substitution argument (the `gh issue create --body "$(cat
     <<'EOF' ... EOF)"` idiom this repo's own CLAUDE.md documents extensively
     for git-commit-message heredocs). The body between the opening heredoc
     line and the closing MARKER line is pure string data being assembled for
     an argument value, never something the shell itself parses and executes
     — so a body line that happens to *start* with a mutating verb (e.g. a
     markdown code-fence example inside an issue/PR body) must never surface
     as a candidate segment at all. This closes a gap the career-playbook #442
     mid-line-mention lesson in step 5 below does not cover: #442's
     anchor-at-segment-start fix means a verb merely mentioned *mid-line*
     doesn't trigger, but a heredoc body line that itself *begins* with the
     verb is (after the `\n`-split in step 4) indistinguishable from a
     genuinely invoked command — see dev-env#481 for the reproduction.
  4. Split the (now heredoc-command-sub-stripped) command into logical
     segments (`&&`, `||`, `;`, `\n`, `|` — same splitter career-playbook's
     hooks use). Two redirect shapes are out of scope for this hook's
     cwd-based check (known, documented gap — see module-level note below):
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
  5. Classify each remaining segment, anchored at the segment start (not a
     substring match — the career-playbook #442 heredoc-mention lesson: a
     mutating verb merely *mentioned* inside a heredoc body or prose must not
     trigger). If no segment is mutating, exit 0 — nothing here needs a git
     subprocess at all.
  6. If the `ALLOW_CANONICAL_MUTATE=1` override token appears as a genuine
     leading prefix on the command or on one of its `&&`/`||`/`;`/`|`-split
     segments (not merely mentioned as a substring anywhere, e.g. inside a
     commit message argument or a heredoc-command-substitution body line —
     step 3's stripping applies identically here), exit 0 — a deliberate,
     visible human override.
  7. Resolve the git toplevel for `cwd` via `git -C <cwd> rev-parse
     --show-toplevel`. Fail open if git can't resolve one at all (not a repo,
     git missing, timeout) — that toplevel, once resolved, *is* the canonical
     root by definition (cwd already failed the worktree-pattern check above).
  8. Exit 2 with a blocking JSON `{"reason": ...}` naming the matched command,
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

# The sole override token. A genuine leading prefix on a command/segment (not
# a substring appearing anywhere, e.g. inside a commit message) bypasses the
# block — see the anchored check in `_has_override()` below.
OVERRIDE_TOKEN = "ALLOW_CANONICAL_MUTATE=1"

# Logical-segment splitter — a real command is one of these segments, never a
# substring buried in a heredoc body or prose (career-playbook #442 lesson).
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\n|\|")

# A segment redirects THAT git invocation to another repo via `git -C <path>`
# or `git --git-dir=<path>` — out of scope for this hook's cwd-based check.
# (A bare `cd <path>` is handled separately in classify() — it persists across
# the rest of the command, not just the segment it appears in.)
_REDIRECT_RE = re.compile(r"(?:^|\s)git\s+(?:-C\s+\S|--git-dir=\S)")

# A `$(cat <<[-]['"]MARKER['"] ... \nMARKER)` span -- a heredoc fed to `cat`
# and used as a command-substitution argument (the `gh issue create --body
# "$(cat <<'EOF' ... EOF)"` idiom career-playbook's own CLAUDE.md documents
# extensively for git-commit-message heredocs). dev-env#481: a body line that
# itself STARTS with a mutating verb (e.g. a markdown code-fence example
# inside an issue/PR body) is, after classify()'s `\n`-split, indistinguishable
# from a genuinely invoked command -- the segment-anchor fix for
# career-playbook#442 (a verb merely MENTIONED mid-line) does not cover a body
# line that begins with the verb. `_strip_heredoc_command_subs()` below
# removes the whole span (opening `$(cat <<MARKER` line through the closing
# MARKER line) before segment classification runs, so none of its body lines
# ever become a candidate segment at all. Scoped deliberately narrow (`cat`
# only, not any command before `<<`) to match the one idiom actually observed
# in this codebase's own heredoc conventions, rather than attempting a general
# heredoc parser -- consistent with this hook's stated non-goal of being a
# full shell parser (see the `cd`/`-C` deferrals above).
_HEREDOC_CATSUB_RE = re.compile(
    r"\$\(\s*cat\s+<<-?(['\"]?)(\w+)\1[^\n]*\n"
    r".*?"
    r"^[ \t]*\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

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


def _strip_heredoc_command_subs(cmd: str) -> str:
    """Remove every `$(cat <<[-]['"]MARKER['"] ... MARKER)` span from `cmd`,
    replacing each whole match with a single space so a real segment boundary
    immediately before/after the span is never fused with adjacent text.

    Called at the top of both classify() and _has_override() -- each must be
    independently correct against a heredoc-as-command-substitution body,
    since both split `cmd` into segments the same way and both are exercised
    directly (not just through main()) by the test suite. Without this,
    _has_override() has the identical false-positive shape as classify(): a
    heredoc body line documenting the override syntax (e.g.
    "ALLOW_CANONICAL_MUTATE=1 git checkout -b foo" as example prose) would be
    read as a genuine leading-prefix override and silently bypass the block
    for an unrelated real mutating segment elsewhere in the same command.
    """
    return _HEREDOC_CATSUB_RE.sub(" ", cmd)


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

    Two distinct redirect shapes, both out of scope for this hook's cwd-based
    check (documented v1 gap), handled at different granularities:

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

    Before any of the above, `_strip_heredoc_command_subs()` removes every
    `$(cat <<'MARKER' ... MARKER)` span entirely (dev-env#481) — otherwise a
    heredoc body line that happens to *start* with a mutating verb (e.g. a
    markdown code-fence example inside a `gh issue create --body` argument)
    becomes its own segment after the `\n`-split below and is indistinguishable
    from a real invocation. This also means a `cd`-looking or mutating-looking
    line inside such a heredoc body can no longer skew the cd-scan or the
    classification loop below — both now only ever see segments from outside
    the stripped spans.
    """
    cmd = _strip_heredoc_command_subs(cmd)
    segments = _SEGMENT_SPLIT.split(cmd)
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
    mentioned"` must NOT bypass the block). Reuses the same segment-split and
    leading-env-strip helpers `classify()` uses, so the override is
    recognized in exactly the positions a real shell would treat it as an
    env-var assignment: at the very start of the command, or at the start of
    any `&&`/`||`/`;`/`|`-separated segment.

    Also strips heredoc-command-substitution spans first (dev-env#481, same
    helper `classify()` uses) — otherwise a heredoc body line that happens to
    *start* with the override token (e.g. documentation prose showing the
    override syntax inside a `gh issue create --body` argument) would read as
    a genuine leading-prefix override and silently bypass the block for an
    unrelated real mutating segment elsewhere in the same command.
    """
    cmd = _strip_heredoc_command_subs(cmd)
    for seg in _SEGMENT_SPLIT.split(cmd):
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
