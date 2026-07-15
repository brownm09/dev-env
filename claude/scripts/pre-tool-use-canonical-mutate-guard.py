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

Also fires for the PowerShell tool (dev-env#620): registered under both the
Bash and PowerShell PreToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run mutating git/gh commands in this environment
— everything below applies identically to a PowerShell-invoked command.

Logic (cheapest checks first — no subprocess spawn unless a mutating segment
is actually found):
  1. Read stdin JSON. Fail open (exit 0) on anything unparseable, a missing or
     empty `cwd`, or a tool_name that is neither `Bash` nor `PowerShell`.
  2. Note whether `cwd` matches a worktree path pattern — either
     `.../.claude/worktrees/<name>` or the sibling-directory
     `.../<repo>-worktrees/<name>` (dev-env#760) — AND is confirmed a *live*,
     registered worktree — not merely a path that looks like one (dev-env#749: an
     orphaned worktree directory, its `.git` link missing or broken, still
     matches the pattern textually, so shape alone is not trusted; see
     `_is_live_worktree()`). A live worktree cwd is out of scope for an
     *ambient* mutation (a mutating verb targeting the worktree itself is fine —
     ADR-024's hook covers that surface), but is NOT an unconditional early
     exit: a command that redirects a mutating verb at a *canonical* checkout
     via `-C`/`--git-dir`/`--work-tree` (dev-env#576) is still evaluated below.
     A live worktree cwd whose command carries no such redirect exits 0
     cheaply, preserving the zero-friction in-worktree path. A cwd that only
     LOOKS worktree-shaped but isn't live falls through to the same
     canonical-root resolution an ordinary non-worktree cwd gets.
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
     Two redirect shapes are handled specially, scanned per-segment after
     segmentation:
       - A bare `cd <path>` in a segment persists for the rest of the shell
         invocation (that's what `&&`-chaining a `cd` means), so every segment
         from that point on is out of scope — its real execution directory is
         unknown, not `cwd`. A mutating segment BEFORE the first `cd` is still
         evaluated normally (order-sensitive; dev-env#762 review — fixed a
         regression where ANY `cd` anywhere, including one after a mutating
         segment, cleared the whole command). (A `cd` *into* a canonical root
         is still a v1 gap — see the module-level coverage note below.)
       - `git -C <path>` / `git --git-dir=<path>` / `git --work-tree=<path>`
         redirect a single git invocation at another repo. As of dev-env#576
         these are no longer merely skipped: `_parse_git_prefix()` captures the
         target dir (from the segment's first physical line only — see
         `_first_line()` — so a heredoc body that merely *mentions* `-C` cannot
         inject one), and step 6 resolves it and applies the same canonical-root
         check to the *target* as to cwd. A redirect at a worktree, at the
         carve-out-exempt engineering-journal checkout
         (`_REDIRECT_TARGET_ALLOWLIST`), or at a path git can't resolve is not
         blocked.
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
  6. For an *ambient* (no-redirect) mutating segment, resolve the git toplevel
     for `cwd` via `git -C <cwd> rev-parse --show-toplevel`; once resolved it
     *is* the canonical root by construction (cwd is not a worktree here). For a
     *redirect* mutating segment, resolve each captured target dir the same way
     and block iff it lands on a canonical (non-worktree, non-carve-out) root.
     Fail open if git can't resolve a path at all (not a repo, git missing,
     timeout).
  7. Exit 2 with a blocking JSON `{"reason": ...}` naming the matched command,
     the canonical root, why it's dangerous, and the two remedies (isolate via
     a worktree, or override).

Mutating verbs blocked: checkout (branch switch or `-b`; a bare
`git checkout <path>` with no `--` is conservatively treated as a possible
branch argument and blocked — use `git checkout -- <path>` for file restores),
switch, commit, merge, rebase, reset, cherry-pick, revert, stash pop/apply,
branch -d/-D, and pull *except* when the same segment also contains
`--ff-only` (fast-forwarding a canonical checkout to origin/main is the
common, safe sync operation and must stay zero-friction). Also blocked (added
dev-env#558, ADR-071 Amendment 1): `gh pr merge` carrying `-d`/`--delete-branch`
— that flag makes `gh` check out the base branch and delete the local branch
locally, the exact same silent-HEAD-thrash harm model reached through a `gh`
invocation instead of a `git` verb.

Explicitly NOT blocked (must stay zero-friction): status, log, diff, show,
fetch, branch --show-current, rev-parse, ls-tree, blame, remote -v, plain
`git branch` (no -d/-D), `git stash list`/`show`, `git checkout -- <path>`,
`git pull --ff-only`, a bare `gh pr merge` or `gh pr merge --squash` (no
delete-branch flag — merges only remotely via the GitHub API, touches no
local state), and anything non-git/non-gh. Plain Read/Grep/Glob against a
canonical checkout is untouched entirely since this hook only matches
Bash/PowerShell.

Coverage note: a `git -C`/`--git-dir`/`--work-tree` redirect *into* a canonical
root from elsewhere (e.g. from a worktree's Bash,
`git -C C:/Users/brown/Git/dev-env checkout -b foo`) IS now caught (dev-env#576,
ADR-071 Amendment 2), except when the target resolves to the carve-out-exempt
engineering-journal checkout (see `_REDIRECT_TARGET_ALLOWLIST`). Still deferred
(v1): a bare `cd <path>` *into* a canonical root — a `cd` takes the whole
command out of scope because a later segment's real execution directory is then
unknown, so `cd C:/Users/brown/Git/dev-env && git checkout -b foo` from
elsewhere is not caught. That still requires deliberate, visible authorship
rather than the silent default-cwd collision #453 documents, so leaving it
uncovered remains an acceptable deferral — extend if it recurs in practice
(same incremental-hardening precedent as ADR-024's own orphan-liveness
addendum). This deferral extends symmetrically to PowerShell's `Set-Location`/
`sl` (dev-env#620, ADR-071 Amendment 4): `_CD_RE` recognizes only the literal
`cd` token, so `Set-Location C:/Users/brown/Git/dev-env; git checkout -b foo`
is not recognized as a cd-redirect either — but it reaches the SAME already-
accepted gap, not a new one, since `git checkout -b foo` there would then be
evaluated as an *ambient* mutation against cwd (the worktree it actually ran
from), not the true post-Set-Location target. See dev-env#620's follow-up
issue for the remaining PowerShell-specific parsing gaps this PR does not
close.

A worktree-shaped cwd is now confirmed LIVE before being trusted (dev-env#749,
ADR-071 Amendment 3): a `.claude/worktrees/<name>` or `<repo>-worktrees/<name>`
path whose `.git` link is missing/broken, or whose git-resolved toplevel
doesn't match itself, is no longer exempted — it falls through to the same
canonical-root resolution an ordinary cwd gets, so an orphaned worktree
directory that git resolves up to a real canonical checkout is correctly
blocked rather than silently trusted. See `_is_live_worktree()`. Recognizing
the sibling-directory convention alongside the nested one is dev-env#760;
liveness confirmation itself needed no change to support it, since
`_is_live_worktree()` operates on whatever `worktree_root` string it is given,
regardless of shape.

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

# Shared fragment for the two worktree-path regexes below (dev-env#749 review
# finding — was independently spelled twice; factored out so the two can't
# silently drift if the worktree-location convention ever changes). Recognizes
# EITHER the nested `.claude/worktrees/<name>` convention (`EnterWorktree`) OR
# the sibling-directory `<repo>-worktrees/<name>` convention (manual
# `git worktree add`, e.g. `dev-env-worktrees/adr-096-correction`) — dev-env#760.
# A bare `<repo>-<suffix>` sibling with no `-worktrees` marker (e.g.
# `dev-env-188`) is still not covered; that shape is ambiguous from the path
# string alone and has no liveness-check anchor to extract a worktree root from.
_WORKTREE_PATH_FRAGMENT = r"(?:\.claude[/\\]worktrees|[^/\\]+-worktrees)[/\\][^/\\]+"

# Matches `.claude/worktrees/<name>` or `<repo>-worktrees/<name>` anywhere in a
# path — same pattern as ADR-024's hook. A cwd matching this is out of scope
# for this hook entirely; any command (mutating or not) is fine from inside a
# worktree.
_WORKTREE_RE = re.compile(
    r"[/\\]" + _WORKTREE_PATH_FRAGMENT,
    re.IGNORECASE,
)

# Anchored capture variants used only to extract the worktree-root PREFIX of a raw,
# client-supplied cwd for the liveness check in _is_live_worktree() below (via
# _worktree_root_from_cwd()). Kept separate from _WORKTREE_RE: that regex's other use site
# (_blockable_redirect_root/_blockable_ambient_root) checks an already git-RESOLVED root,
# which is safe as-is and needs no liveness confirmation — see _is_live_worktree's docstring
# for why only the cwd-facing use site was vulnerable to trusting shape alone.
#
# Split into two ordered patterns (nested tried before sibling) rather than one combined
# alternation — review finding, dev-env#760: a single regex with non-greedy `(.+?)` lets the
# sibling alternative "win" at a shallower position than a genuine nested worktree occurring
# deeper in the same path (e.g. a `.claude/worktrees/<name>` worktree created inside a
# `<repo>-worktrees/<name>` sibling worktree), mis-extracting the outer sibling directory as
# the root instead of the actual, deeper worktree. Checking the nested pattern against the
# whole string first sidesteps that: a real path normally contains at most one
# `.claude/worktrees/` segment, so matching it directly finds the correct (only) occurrence
# regardless of what a `-worktrees` segment earlier in the same path might otherwise steal.
# `_WORKTREE_RE`'s `.search()` use sites need no equivalent split: they only answer a
# boolean "is this resolved root worktree-shaped," which is unaffected by which alternative
# happens to match first.
_NESTED_WORKTREE_ROOT_RE = re.compile(
    r"^(.+?[/\\]\.claude[/\\]worktrees[/\\][^/\\]+)",
    re.IGNORECASE,
)
_SIBLING_WORKTREE_ROOT_RE = re.compile(
    r"^(.+?[/\\][^/\\]+-worktrees[/\\][^/\\]+)",
    re.IGNORECASE,
)

# The sole override token. A genuine leading prefix on a command/segment (not
# a substring appearing anywhere, e.g. inside a commit message) bypasses the
# block — see the anchored check in `_has_override()` below.
OVERRIDE_TOKEN = "ALLOW_CANONICAL_MUTATE=1"

# Git-level redirect flags that retarget a git invocation at ANOTHER repo:
#   -C <dir>              run as if git were started in <dir>
#   --git-dir[=| ]<dir>   use <dir> as the git dir (its worktree top is the
#                         parent when <dir> ends in a `.git` segment)
#   --work-tree[=| ]<dir> use <dir> as the working tree
# `_parse_git_prefix()` both skips these as git-level options AND captures the
# dir, so main() can resolve it and apply the canonical-root check to the
# *target*, not just cwd — the dev-env#576 extension of the original cwd-only
# check. Both the `=` and space value forms are handled (`-C` has only the space
# form; git does not accept `-C=<dir>`). A bare `cd <path>` is still handled
# separately in find_mutating_segments() — it persists across the rest of the
# command, not just the segment it appears in.
_GIT_REDIRECT_FLAGS = ("-C", "--git-dir", "--work-tree")

# PERMANENT carve-out (dev-env#576, corrected dev-env#747): a redirect target
# whose resolved canonical toplevel exactly matches one of these (separator-
# and case-normalized) paths is NOT blocked, even though it is a canonical
# (non-worktree) checkout. The engineering-journal canonical checkout is the
# shared tree the documented stub workflow mutates via
# `git -C <journal> checkout/commit/pull` on every PR open/merge (global
# CLAUDE.md Engineering Journal section + ADR-066); blocking that automated
# path would force ALLOW_CANONICAL_MUTATE=1 onto it — untenable.
# This carve-out is permanent by design, not a stopgap: the Stub file
# workflow's whole premise is one shared canonical every concurrent session
# reaches via `-C` instead of a per-session worktree (see claude/CLAUDE.md ->
# Engineering Journal -> Stub file workflow). dev-env#346 is a narrower,
# unrelated case (only the biweekly-retro routine's own report-writing step)
# and does not remove this carve-out — a prior version of this comment cited
# #346 as if it would; see dev-env#747, the correctly-scoped issue for the
# actual worktree-locking bug (a worktree squatting draft/YYYY-MM-DD instead
# of the canonical mutating it — the inverse of what THIS hook guards
# against), and ADR-105's pre-tool-use-journal-draft-worktree-guard.py, the
# new hook that closes that gap.
#
# Matched by exact absolute path, not a bare basename (review finding on
# dev-env#576/PR#584): a basename-only match would exempt ANY canonical
# checkout anywhere on disk that happens to be named "engineering-journal",
# not just this one. The global CLAUDE.md already hardcodes this exact path
# for this single-machine tool ("Repo path: C:/Users/brown/Git/engineering-
# journal"), so hardcoding it here too is consistent with, not a new
# departure from, the rest of this codebase's conventions.
#
# The real path is overridable via CANONICAL_MUTATE_GUARD_JOURNAL_PATH solely
# so the end-to-end test suite can point this at a disposable temp directory
# instead of the developer's actual engineering-journal checkout — a test
# must never create or resolve toplevel-detection against the real one.
_REDIRECT_TARGET_ALLOWLIST = frozenset({
    os.environ.get("CANONICAL_MUTATE_GUARD_JOURNAL_PATH", "C:/Users/brown/Git/engineering-journal")
    .replace("\\", "/")
    .rstrip("/")
    .lower()
})

# `cd <path>` at the start of a segment (after stripping leading env-var
# assignments — see `_strip_leading_env`). Compiled once; reused by both the
# cd-scan loop in classify() and anywhere else that needs to detect a cd.
_CD_RE = re.compile(r"^\s*cd(?:\s|$)")

# A git invocation at the start of a segment (after stripping leading env-var
# assignments) — captures the rest of the command (the subcommand + its
# args) for verb classification in is_mutating_segment().
_GIT_INVOCATION_RE = re.compile(r"^git(?:\.exe)?\s+(.*)$", re.IGNORECASE)

# A `gh` invocation at the start of a segment (after stripping leading env-var
# assignments) — captures the rest of the command for classification in
# is_mutating_gh_segment(). Mirrors _GIT_INVOCATION_RE's shape exactly; `gh`
# is a distinct binary from `git`, so this is a separate regex rather than a
# shared one.
_GH_INVOCATION_RE = re.compile(r"^gh(?:\.exe)?\s+(.*)$", re.IGNORECASE)

# Standalone flag tokens on a `gh pr merge` invocation that make it mutate
# LOCAL state (checks out the base branch, deletes the local branch) rather
# than merging purely via the GitHub API. See is_mutating_gh_segment().
_GH_DELETE_BRANCH_FLAGS = {"-d", "--delete-branch"}

# Git-level options that can precede the actual subcommand (git's own option
# grammar, not the subcommand's) — e.g. `git -c gc.auto=0 stash pop` or
# `git --no-optional-locks stash apply`. `-c <name>=<value>` takes a
# space-separated value token; the rest are flags with no separate value token.
# The redirect flags `-C`/`--git-dir`/`--work-tree` (see `_GIT_REDIRECT_FLAGS`)
# are handled in `_parse_git_prefix()` alongside these — it both skips them (so
# the real verb still lands at tokens[0]) and captures their target dir for
# main()'s canonical-root check (dev-env#576). They are kept in a separate
# constant because, unlike these, their value is not discarded.
_GIT_LEVEL_FLAG_NO_VALUE = {"--no-optional-locks", "--no-pager", "-p", "--paginate", "--bare"}
_GIT_LEVEL_FLAG_WITH_VALUE = {"-c"}

# Leading env-var assignments (VAR=val VAR2=val2 ...) that may precede the
# actual command in a segment — stripped before verb classification so
# `ALLOW_CANONICAL_MUTATE=1 git checkout -b foo` still classifies on `git`.
_LEADING_ENV = re.compile(r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*")


def _strip_leading_env(segment: str) -> str:
    return _LEADING_ENV.sub("", segment, count=1)


def _normalize_redirect_dir(flag: str, value: str) -> str:
    """Normalize a redirect flag's value to a directory suitable for
    `git -C <dir> rev-parse --show-toplevel`.

    `-C`/`--work-tree` already name a working-tree directory. `--git-dir`
    points at the git dir itself; when it ends in a `.git` segment the
    working-tree top is its parent (`/repo/.git` -> `/repo`), and a bare `.git`
    means the git dir sits in cwd so the worktree top is cwd (`.`). Any other
    `--git-dir` shape is passed through unchanged and left to rev-parse.
    """
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
    `(redirect_dirs, remaining_tokens)`.

    `remaining_tokens[0]` is the real subcommand verb (or the list is empty).
    `redirect_dirs` holds every `-C`/`--git-dir`/`--work-tree` directory value
    seen (in `=` or space form), each normalized via `_normalize_redirect_dir`.

    Supersedes the older `_skip_git_level_flags`: it walks the same
    `-c <v>` / `--no-optional-locks` / `--no-pager` / `-p` / `--paginate` /
    `--bare` options so the actual subcommand still lands at index 0 for verb
    classification — PLUS the redirect flags, which the old code left for a
    separate `_REDIRECT_RE` segment-skip that never resolved the target
    (dev-env#576).
    """
    redirect_dirs = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        # redirect flags, space form: `-C <dir>`, `--git-dir <dir>`, `--work-tree <dir>`
        if tok in _GIT_REDIRECT_FLAGS:
            if i + 1 < n:
                redirect_dirs.append(_normalize_redirect_dir(tok, tokens[i + 1]))
                i += 2
            else:
                i += 1  # dangling flag with no value — nothing to capture
            continue
        # redirect flags, `=` form: `--git-dir=<dir>`, `--work-tree=<dir>`
        # (`-C=<dir>` is not valid git syntax, so only the long flags have it)
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
            i += 2  # flag + its value token
            continue
        if tok in _GIT_LEVEL_FLAG_NO_VALUE:
            i += 1
            continue
        break
    return redirect_dirs, tokens[i:]


def _tokenize(rest: str) -> list:
    """Split `rest` into shell-style tokens, quote-aware — so a redirect value
    containing whitespace (e.g. `-C "C:/Program Files/repo"`) is captured as
    one token instead of shattering across several (review finding on
    dev-env#576/PR#584: the prior plain `.split()` let a quoted, space-bearing
    `-C`/`--git-dir`/`--work-tree` target silently defeat the new block).

    Falls back to a plain whitespace split on unbalanced quoting rather than
    raising: `rest` is only ever a segment's first PHYSICAL line
    (`_first_line()`), so a real multi-line heredoc/command-substitution span
    (e.g. `git commit -m "$(cat <<'EOF' ...)"`) truncates mid-quote right here
    — `shlex.split` correctly raises `ValueError` on that truncated line, and
    falling back to `.split()` preserves the existing, tested "heredoc commit
    still classifies as mutating" behavior instead of crashing the hook.
    """
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        return rest.split()


def _git_rest_tokens(segment: str):
    """Tokens after `git` on the segment's first physical line, or None if the
    (env-stripped) segment isn't a git invocation.

    Shared by `is_mutating_segment()` and `_segment_redirect_dirs()` so both see
    identical tokenization — and both restricted to the first physical line via
    `_first_line()`, so a heredoc/`$(...)` body can neither inject a spurious
    mutating verb nor a spurious `-C`/`--git-dir`/`--work-tree` redirect.
    """
    stripped = _strip_leading_env(segment).strip()
    m = _GIT_INVOCATION_RE.match(_first_line(stripped))
    if not m:
        return None
    rest = m.group(1).strip()
    if not rest:
        return None
    return _tokenize(rest)


def _first_line(segment: str) -> str:
    """Return `segment`'s own first physical line.

    A segment's git invocation and its flags only ever appear on this line —
    `split_top_level` (dev-env#511) can return a segment spanning multiple
    physical lines when a heredoc/`$(...)` span is part of it (that's the
    point of its opacity: the span stays inside the segment rather than being
    split out), and everything after that first line is heredoc/command-
    substitution BODY data, never additional invocation syntax.

    Used via `_git_rest_tokens()` by both `is_mutating_segment()` and
    `_segment_redirect_dirs()` below, so neither can be tricked by body text
    that happens to look like a mutating verb/flag or a
    `-C`/`--git-dir`/`--work-tree` redirect: `_GIT_INVOCATION_RE` is
    `$`-anchored with no `re.DOTALL`, so without this it fails to match a
    multi-line segment at all (a real `git commit -m "$(cat <<'EOF' ...)"` —
    this repo's own documented commit-message idiom — was silently classified
    as non-mutating). Capturing the redirect dir from only the first line
    (rather than an unanchored search over the whole segment) is likewise what
    stops a commit whose heredoc body merely *mentions* "git -C /somewhere" as
    prose from being wrongly read as redirected to another repo. Adding
    `re.DOTALL` to `_GIT_INVOCATION_RE` instead would trade the first false
    negative for a different one — heredoc/subshell body words leaking into the
    stash pop/apply and checkout `--` token scans below (e.g. a read-only
    `git stash list` heredoc body that happens to say "please apply this later"
    would wrongly block on "apply").
    """
    return segment.split("\n", 1)[0]


def is_mutating_segment(segment: str) -> bool:
    """True if `segment` (after stripping leading env-var assignments) is a
    git invocation whose verb mutates working-tree/branch/history state.

    Anchored at the start of the (env-stripped) segment — a mention of one of
    these verbs later in the string (e.g. inside a commit message argument)
    does not trigger; only the actual invoked git subcommand does. Only the
    segment's first physical line is examined — see `_first_line()`.
    """
    tokens = _git_rest_tokens(segment)
    if not tokens:
        return False
    # Consume git-level options (incl. -C/--git-dir/--work-tree redirects) so
    # the real verb lands at tokens[0]. This is also what lets a redirected
    # invocation like `git -C <path> checkout` be recognized as mutating at all
    # (dev-env#576) and fixes the prior `git --work-tree=<path> commit`
    # misclassification, where the leading flag was mistaken for the verb.
    _redirect_dirs, tokens = _parse_git_prefix(tokens)
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


def is_mutating_gh_segment(segment: str) -> bool:
    """True if `segment` is a `gh pr merge` invocation carrying -d/--delete-branch.

    `gh pr merge --help` documents `-d, --delete-branch: Delete the local and
    remote branch after merge` — run from the branch it's merging, this must
    check out the base branch and delete the local branch locally (a checked-
    out branch can't be deleted), which is the exact same silent
    local-HEAD-thrash harm model `is_mutating_segment()` already blocks for
    `git checkout`/`git branch -d`, just reached through a `gh` invocation
    instead of a literal `git` verb (dev-env#558). A bare `gh pr merge` (no
    delete-branch flag) merges only remotely via the GitHub API and touches no
    local state at all, so it must stay unblocked — mirrors the zero-friction
    treatment `is_mutating_segment()` gives plain `git branch <name>` (create,
    no switch) and `git checkout -- <path>` (no branch/HEAD movement).

    Anchored at the start of the (env-stripped) segment's first physical line
    only (`_strip_leading_env()` + `_first_line()`, exactly like
    `is_mutating_segment()`) — a heredoc/`$()` body merely *mentioning*
    "gh pr merge -d" as prose (e.g. a commit message describing this fix) must
    not trigger, the same career-playbook #442 lesson `is_mutating_segment()`
    already observes. Deliberately does NOT special-case a `--repo owner/repo`
    flag on `gh pr merge -d` — matches this hook's own established
    "block when in doubt" judgment call for a bare `git checkout <path>`.
    """
    stripped = _strip_leading_env(segment).strip()
    m = _GH_INVOCATION_RE.match(_first_line(stripped))
    if not m:
        return False
    rest = m.group(1).strip()
    if not rest:
        return False

    tokens = rest.split()
    if len(tokens) < 2 or tokens[0].lower() != "pr" or tokens[1].lower() != "merge":
        return False

    # Cobra boolean flags also accept an explicit-value form (`--delete-branch=true`),
    # not just the bare `-d`/`--delete-branch` most invocations actually use — a token
    # equality check alone would miss it (review finding on dev-env#558/PR #560). An
    # explicit `=false`/`=0`/`=no` is a genuine opt-out (no local mutation at all) and
    # must NOT be treated as mutating -- only the prefix match, not the flag's value,
    # was the gap.
    for t in tokens[2:]:
        if t in _GH_DELETE_BRANCH_FLAGS:
            return True
        if t.startswith("--delete-branch="):
            value = t.split("=", 1)[1].strip().lower()
            if value not in ("false", "0", "no"):
                return True
    return False


def _segment_redirect_dirs(segment: str) -> list:
    """The `-C`/`--git-dir`/`--work-tree` directory hints on a git segment's
    first physical line (`_first_line()`, via `_git_rest_tokens()`, so a
    heredoc/`$(...)` body mention can't inject a spurious redirect). Returns
    `[]` for a non-git segment or a git segment with no redirect flag.
    """
    tokens = _git_rest_tokens(segment)
    if not tokens:
        return []
    dirs, _rest = _parse_git_prefix(tokens)
    return dirs


def find_mutating_segments(cmd: str, segments: list = None) -> list:
    """Return an ordered list of mutating-segment descriptors, or `[]`.

    Segments come from the shared `_hookio.split_top_level(cmd,
    split_pipe=True)` engine (dev-env#511, ADR-050 Amendment 7) — see that
    function's own docstring for exactly how it splits (quote/subshell/
    heredoc-aware, not a plain regex) and the module docstring's step 3 above
    for the consequences specific to this hook. *segments*, when already
    computed by the caller (`main()` passes the same list it hands
    `_has_override()`, avoiding a redundant re-parse of `cmd` per Bash call),
    is used as-is; otherwise it is computed here.

    Each descriptor is `{"segment": <stripped str>, "redirect_dirs": [<dir>...]}`:
      - `redirect_dirs == []`  -> the segment mutates the *ambient* repo (cwd's);
        `main()` blocks it iff cwd is a canonical (non-worktree) root — the
        original pre-#576 behavior.
      - `redirect_dirs != []`  -> the segment redirects at another repo via
        `-C`/`--git-dir`/`--work-tree`; `main()` resolves each dir and blocks
        iff any resolves to a canonical (non-worktree) root that isn't
        carve-out-exempt (dev-env#576).

    Pure/offline by construction: extracting the dirs is string work; the git
    subprocess that resolves them to canonical roots is deliberately the
    CALLER's (`main()`'s) job, so the pure-function test layer never shells
    out. A bare `cd <path>` in a segment persists across the REST of the
    shell invocation (that's what `&&`-chaining a `cd` means), so every
    segment from that point on has an unknown real execution directory and is
    excluded — order-sensitively: a mutating segment appearing BEFORE the
    first `cd` still executed in the known, original `cwd`, and is still
    evaluated normally. The cd check runs against the env-stripped segment
    (same helper `is_mutating_segment` uses) so `FOO=1 cd /tmp && git
    checkout -b x` is still recognized as a cd-redirect.

    Order-sensitivity fix (dev-env#762 review): earlier scanned ALL segments
    for a `cd` first and returned `[]` for the WHOLE command the instant any
    segment matched, regardless of position — so a mutating segment that came
    BEFORE a later, unrelated `cd` was incorrectly cleared too. Confirmed as a
    real, exploitable regression introduced by this PR's own `{`-as-split-
    trigger addition to `_hookio.split_top_level`: `git checkout main; if ($?)
    { cd C:/repo }` (the PowerShell conditional-chain idiom this PR adds
    support for) now segments `cd C:/repo }` as its own top-level segment,
    which matched the old whole-command escape and silently un-blocked the
    preceding `git checkout main` — the exact dev-env#453 collision this hook
    exists to prevent. (The identical order-insensitivity already existed for
    a plain `git checkout main; cd C:/repo` semicolon chain even before this
    PR; fixing it order-sensitively closes both shapes at once rather than
    special-casing the newly-exposed one.)
    """
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    out = []
    for seg in segments:
        if _CD_RE.match(_strip_leading_env(seg)):
            break  # cd persists across the REST of the command -- only prior segments are known-safe to evaluate
        if is_mutating_segment(seg) or is_mutating_gh_segment(seg):
            out.append({"segment": seg.strip(), "redirect_dirs": _segment_redirect_dirs(seg)})
    return out


def classify(cmd: str, segments: list = None):
    """First mutating segment string in `cmd`, or None if none found.

    Compatibility wrapper over `find_mutating_segments()` for tests and direct
    callers that only need the matched segment text. cwd-agnostic and offline:
    "mutating" here means "contains a mutating git/gh verb", NOT "blockable" —
    whether a match is actually blocked depends on cwd- and redirect-target
    resolution, which is `main()`'s job (`main()` calls `find_mutating_segments`
    directly so it can inspect each segment's redirect dirs).
    """
    matches = find_mutating_segments(cmd, segments)
    return matches[0]["segment"] if matches else None


def _has_override(cmd: str, segments: list = None) -> bool:
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

    *segments*, when already computed by the caller, is used as-is (see
    `classify()`'s matching parameter) — avoids re-parsing `cmd` a second
    time in `main()`, which always calls this right after `classify()` on the
    same command. Tests and other direct callers can keep passing just `cmd`.
    """
    if segments is None:
        segments = split_top_level(cmd, split_pipe=True)
    for seg in segments:
        stripped = seg.strip()
        if stripped == OVERRIDE_TOKEN or stripped.startswith(OVERRIDE_TOKEN + " "):
            return True
    return False


def _resolve_git_toplevel(cwd: str):
    """Return git's worktree top-level for `cwd`, or None if git can't resolve
    it (not a repo, git missing, timeout, non-zero exit, or an illegal path) —
    the fail-open path.

    `cwd` here is either the payload's own `cwd` or a resolved redirect
    target dir (dev-env#576) — the latter is command-string-derived, so it is
    far less constrained than a harness-provided cwd. A value containing a
    null byte makes `subprocess.run`'s `Popen` raise `ValueError: embedded
    null character` rather than a `FileNotFoundError`/`OSError`; that case is
    caught here too so an unusual (if unrealistic) redirect value fails open
    like every other unresolvable path instead of crashing the hook (review
    finding on dev-env#576/PR#584 — this contract was safe when the only
    caller was the harness-provided cwd, but is not automatically safe now
    that a second, command-derived caller exists).
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


def _memoized_toplevel(path: str, cache: dict):
    """`_resolve_git_toplevel(path)`, memoized in `cache` (path -> resolved
    toplevel-or-None).

    Lets `main()` share a single resolution per distinct path across every
    caller that needs it for the same command — in particular,
    `_is_live_worktree()`'s own `git_toplevel()` call (passed in as
    `lambda path: _memoized_toplevel(path, toplevel_cache)`) and the ambient
    branch's later resolution of the identical `cwd` both land in the same
    `cache` dict, so the narrow case where cwd is worktree-shaped with a
    `.git` link present but git resolves it to a different root (not live)
    spawns `git rev-parse` at most once instead of once per caller — the two
    calls necessarily agreed anyway, since neither cwd nor repo state changes
    between them (dev-env#758). Also used directly for redirect-dir
    resolution via `_blockable_redirect_root()`, unifying what used to be two
    separate memoization schemes (that function's own `toplevel_cache`
    handling and `main()`'s separate `cwd_root`/`cwd_root_resolved` pair) into
    one.
    """
    if path not in cache:
        cache[path] = _resolve_git_toplevel(path)
    return cache[path]


def _normalize_path(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _worktree_root_from_cwd(cwd: str):
    """Return the worktree-root PREFIX of `cwd` (up through and including the
    `.claude/worktrees/<name>` or `<repo>-worktrees/<name>` segment), or None if `cwd`
    isn't anchored inside one. Cheap, pure string work — no subprocess. Nested is tried
    before sibling — see `_NESTED_WORKTREE_ROOT_RE`'s comment for why.
    """
    m = _NESTED_WORKTREE_ROOT_RE.match(cwd)
    if m:
        return m.group(1)
    m = _SIBLING_WORKTREE_ROOT_RE.match(cwd)
    return m.group(1) if m else None


def _is_live_worktree(
    worktree_root: str,
    cwd: str,
    *,
    path_isfile=os.path.isfile,
    git_toplevel=_resolve_git_toplevel,
) -> bool:
    """True if `worktree_root` is a live, registered git worktree — not an
    orphaned directory that merely LOOKS like one (dev-env#749).

    An orphaned worktree directory (its `.git` link file missing or broken,
    e.g. left behind after an incomplete `git worktree add`/`remove`) still
    textually matches `_NESTED_WORKTREE_ROOT_RE` or `_SIBLING_WORKTREE_ROOT_RE`.
    When git is asked to resolve such a directory, it walks up the filesystem
    tree looking for a `.git` and lands
    on the CANONICAL repo instead — live-confirmed on dev-env#630: an orphaned
    worktree with no `.git` resolved `git rev-parse --show-toplevel` straight
    to the canonical checkout, giving that session fully unguarded ambient
    mutate access to it.

    Adapted from `pre-tool-use-worktree-path-check.py`'s own
    `_worktree_is_live()`/`_normalize()` (ADR-024's dev-env#328 addendum,
    which fixed the identical bug shape in that sibling hook five weeks
    earlier) — copied rather than imported, per this codebase's convention of
    tolerating duplication through two consumers before extracting a shared
    module (see `_worktree_topology.py`'s own docstring and ADR-093's
    Maintainability section). `git_toplevel` defaults to this file's own
    `_resolve_git_toplevel`, which already has a more defensive except-tuple
    (adds `ValueError` for null-byte paths, dev-env#576/PR#584) than the
    sibling's copy.

    Two signals, cheapest first:
      1. The `.git` link file must exist at worktree_root, as a FILE (a real
         worktree's `.git` is always a `gitdir: ...` pointer file, never a
         directory). An orphaned dir has lost it entirely — caught without
         spawning git. `os.path.isfile`, not `os.path.exists` (review finding,
         dev-env#760): a genuine canonical checkout (a real clone, `.git` a
         directory) that merely happens to sit at a worktree-shaped path would
         otherwise pass this signal and be wrongly treated as a live worktree —
         `exists` can't tell a `.git` file from a `.git` directory, only
         `isfile` can.
      2. git's resolved top-level for `cwd` must equal worktree_root. Mainly
         catches a resolution mismatch (e.g. cwd resolving up to the canonical
         root, or to an unrelated repo). NOTE (review finding, dev-env#749):
         this does NOT reliably catch the subtler orphan mode where the `.git`
         link file is present but its target inside
         `<canonical>/.git/worktrees/<name>` was independently pruned — git
         typically errors in that state rather than walking up, so
         `git_toplevel` returns None and signal 2 falls through to the
         transient-failure branch below (treated as live). That's harmless
         here: the same None also makes main()'s own `_resolve_git_toplevel(cwd)`
         resolve to nothing blockable, so every path still converges on
         fail-open — but it means this pruned-gitdir orphan mode is NOT
         actually detected by this function, despite earlier revisions of this
         docstring claiming it was.

    If git cannot run at all (returns None) but the `.git` link is present,
    treat the worktree as live — a transient git failure must not widen this
    hook's block surface to a directory it otherwise has no reason to
    distrust.

    Unlike the sibling hook (which BLOCKS on a non-live worktree, since any
    write there risks landing on the wrong tree), a non-live result here just
    disables the shape-only exemption in main() — cwd then falls through to
    this hook's own normal canonical-root resolution, guarded by
    `_blockable_ambient_root()` below, consistent with the file's existing
    fail-open philosophy.

    Keep this function in sync with the sibling's `_worktree_is_live()` if its
    liveness logic ever changes — the two are deliberately parallel copies
    (see the duplication-convention note above), just under different names.
    """
    if not path_isfile(os.path.join(worktree_root, ".git")):
        return False
    top = git_toplevel(cwd)
    if top is None:
        return True
    return _normalize_path(top) == _normalize_path(worktree_root)


def _is_allowlisted_root(root: str) -> bool:
    """True if `root` (a resolved canonical toplevel) is on the temporary
    redirect-target carve-out — currently only the engineering-journal
    canonical checkout. See `_REDIRECT_TARGET_ALLOWLIST`.

    Matches the whole normalized path (`/` separators, no trailing slash,
    lowercased), not just the last path segment — a basename-only match would
    exempt any canonical checkout anywhere on disk that happens to share that
    directory name (review finding on dev-env#576/PR#584).
    """
    normalized = root.replace("\\", "/").rstrip("/").lower()
    return normalized in _REDIRECT_TARGET_ALLOWLIST


def _blockable_redirect_root(redirect_dirs: list, cwd: str, toplevel_cache: dict):
    """Resolve each `-C`/`--git-dir`/`--work-tree` dir hint and return the first
    that lands on a canonical (non-worktree) root NOT on the carve-out, or None.

    A dir that resolves to a worktree (target is itself isolated — fine), to a
    carve-out-exempt root (the journal), or that git can't resolve at all
    (fail-open) is skipped. This is the git-subprocess half that
    `find_mutating_segments()` deliberately leaves to the caller.

    A non-absolute `d` is resolved against `cwd` (the command's OWN cwd, from
    the PreToolUse payload) before being handed to `_resolve_git_toplevel` —
    without this, `git -C <relative-dir> ...` would run against the hook
    SCRIPT's own process cwd (whatever directory it happens to be launched
    from), which has no relationship to the Bash command's actual working
    directory, silently missing a relative redirect into a canonical root
    (review finding on dev-env#576/PR#584). `git rev-parse --show-toplevel`
    canonicalizes any `..`/`.` segments in the joined path itself, so no
    extra normalization is needed here.

    `toplevel_cache` memoizes resolved-path -> toplevel across the whole
    command (one dict per Bash call, shared by every call from the same
    `main()` invocation) so a command repeating the same redirect target
    across several `&&`-chained segments spawns at most one `git rev-parse`
    per distinct resolved path rather than one per occurrence (review finding
    on dev-env#576/PR#584 — a crafted worst case spawned 15 subprocesses for
    one command before this memoization). Delegated to the shared
    `_memoized_toplevel()` helper (dev-env#758), the same one `main()` uses
    for cwd's own resolution — one memoization scheme, one cache dict, for
    every path this hook ever resolves in a single command.
    """
    for d in redirect_dirs:
        resolved = d if is_absolute_path(d) else os.path.join(cwd, d)
        root = _memoized_toplevel(resolved, toplevel_cache)
        if root and not _WORKTREE_RE.search(root) and not _is_allowlisted_root(root):
            return root
    return None


def _blockable_ambient_root(cwd_root):
    """Return `cwd_root` if it's non-None and NOT itself worktree-shaped, else None.

    Defense-in-depth mirror of `_blockable_redirect_root`'s `not
    _WORKTREE_RE.search(root)` guard (review finding, dev-env#749). The
    ambient branch's original invariant — "cwd already failed the worktree
    pattern, so any resolved toplevel IS canonical by construction" — assumed
    `cwd_is_worktree` could only be False when cwd was never worktree-shaped
    at all. That's no longer strictly guaranteed: cwd CAN be worktree-shaped
    (`_worktree_root_from_cwd(cwd)` is not None) yet still reach the ambient
    branch, if `_is_live_worktree()` returns False for a cwd that is, in
    fact, a genuinely live worktree — e.g. a resolved-toplevel vs.
    extracted-prefix mismatch from a junction, symlink, or short-path
    component. Not reproduced in this codebase's own layout (verified during
    review), but not provably impossible on every filesystem. Guarding the
    resolved root itself, exactly like the redirect branch already does,
    closes that latent gap without weakening the orphan-blocking fix this PR
    exists to make: an orphan's resolved root is the CANONICAL repo, never
    worktree-shaped, so this guard is a no-op for the actual bug being fixed.
    """
    if cwd_root and not _WORKTREE_RE.search(cwd_root):
        return cwd_root
    return None


def _emit_block(matched: str, root: str) -> None:
    """Write the blocking `{"reason": ...}` JSON to stderr and exit 2.

    stderr, not stdout: Claude Code discards a PreToolUse hook's stdout on exit
    code 2 and surfaces only stderr to the model — matching the working pattern
    in career-playbook's block-artifact-merge.py / block-letter-violations.py.
    """
    reason = (
        f"[canonical-mutate-guard] BLOCKED: a command that mutates local git state in a "
        f"canonical (non-worktree) checkout was issued. Two Claude Code sessions sharing one "
        f"canonical checkout can collide — one session's checkout/commit/reset silently "
        f"thrashes HEAD out from under another concurrent session, scrambling commit "
        f"attribution or risking a stale-branch PR reverting the other session's already-"
        f"merged work. A shared checkout also shares (and can exhaust) the GitHub API "
        f"rate limit. See dev-env#453 for the two motivating incidents; dev-env#576 for the "
        f"-C/--git-dir/--work-tree redirect-into-canonical extension (the Root below may be a "
        f"checkout reached via a redirect flag, not cwd).\n"
        f"\n"
        f"  Command : {matched}\n"
        f"  Root    : {root}\n"
        f"\n"
        f"Remedies:\n"
        f"  1. Isolate into a worktree — EnterWorktree, or a worktree of the target repo:\n"
        f"     git -C {root} worktree add <path> -b <branch> origin/main\n"
        f"  2. If you've confirmed no other session is active in this checkout, override:\n"
        f"     ALLOW_CANONICAL_MUTATE=1 {matched}"
    )
    sys.stderr.write(json.dumps({"reason": reason}) + "\n")
    sys.exit(2)


def main() -> None:
    _hookutil.record_heartbeat("pre-tool-use-canonical-mutate-guard")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        sys.exit(0)  # valid JSON but not an object (e.g. `[]`, `"x"`, `123`, `null`) -> fail open

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    cwd = data.get("cwd", "") or ""
    if not cwd:
        sys.exit(0)  # missing/empty cwd -> can't determine scope -> fail open

    # Cheap, pure shape check only (no subprocess) — dev-env#749: this no
    # longer determines cwd_is_worktree by itself. A path that merely LOOKS
    # like a worktree (including an orphaned worktree directory whose `.git`
    # link is missing or broken) is not trustworthy on shape alone; see
    # _is_live_worktree() for the liveness confirmation, deferred below until
    # there's an actual mutating segment to evaluate it against.
    cwd_worktree_root = _worktree_root_from_cwd(cwd)

    cmd = (data.get("tool_input") or {}).get("command", "") or ""
    if not cmd:
        sys.exit(0)

    # Parsed once and reused by find_mutating_segments() and _has_override()
    # below — main() is the per-Bash-invocation hot path and both would
    # otherwise re-run the same O(n) parse over the same string.
    segments = split_top_level(cmd, split_pipe=True)

    matches = find_mutating_segments(cmd, segments)
    if not matches:
        sys.exit(0)  # no mutating segment at all

    # Checked before the liveness confirmation below (dev-env#749) so an
    # overridden command never pays for a liveness-check subprocess it
    # doesn't need — pure performance ordering, no behavior change: this
    # check and cwd_is_worktree are independent and both lead to the same
    # exit-0 outcome, so their relative order can't affect any result.
    if _has_override(cmd, segments):
        sys.exit(0)  # explicit, visible human override (anchored prefix, not a substring mention)

    # NOTE (dev-env#576): a worktree cwd is NOT an unconditional early exit. A
    # command issued from a worktree that redirects a mutating git verb at a
    # *canonical* checkout via -C/--git-dir/--work-tree (the incident:
    # `git -C <journal> pull` from a project worktree) must still be
    # evaluated. A worktree cwd with NO such redirect is cleared cheaply
    # below, preserving the pre-#576 zero-friction path for ordinary
    # in-worktree work. As of dev-env#749, "worktree cwd" additionally
    # requires liveness confirmation — the (now possibly subprocess-spawning)
    # check is deferred to this point, once a real mutating segment is known
    # to exist and the override token has already been ruled out, preserving
    # the module docstring's "no subprocess spawn unless a mutating segment is
    # actually found" contract. Further gated on at least one match being
    # AMBIENT (review finding, dev-env#749): cwd_is_worktree is only ever
    # consulted below when a match has no redirect_dirs — when every match is
    # a redirect (e.g. the common `git -C <journal> pull` shape), computing
    # liveness would spawn a `git rev-parse` whose result is never read.
    #
    # Shared across every toplevel resolution this command needs (see
    # _memoized_toplevel()). Both the liveness check just below and the
    # ambient branch's own cwd resolution further down key off this same
    # dict, so a worktree-shaped-but-not-live cwd (`.git` link present, but
    # git resolves it to a different root — _is_live_worktree()'s signal 2)
    # resolves cwd's toplevel via `git rev-parse` at most once instead of
    # once per caller (dev-env#758) — the two calls necessarily agreed
    # anyway, since neither cwd nor repo state changes between them.
    # _is_live_worktree()'s own bool-only return stays untouched — it's a
    # deliberately-kept-in-sync copy of pre-tool-use-worktree-path-check.py's
    # _worktree_is_live() (ADR-071 Amendment 3), so the cache is threaded
    # through its existing `git_toplevel` injection seam instead.
    toplevel_cache = {}

    cwd_is_worktree = (
        any(not m["redirect_dirs"] for m in matches)
        and cwd_worktree_root is not None
        and _is_live_worktree(
            cwd_worktree_root, cwd,
            git_toplevel=lambda path: _memoized_toplevel(path, toplevel_cache),
        )
    )

    # A worktree cwd with no redirecting mutating segment mutates only the
    # worktree itself — fine.
    if cwd_is_worktree and not any(m["redirect_dirs"] for m in matches):
        sys.exit(0)

    for m in matches:
        if m["redirect_dirs"]:
            # Redirect mutation: blockable iff a target resolves to a canonical
            # (non-worktree, non-carve-out) root — regardless of cwd.
            block_root = _blockable_redirect_root(m["redirect_dirs"], cwd, toplevel_cache)
        elif cwd_is_worktree:
            # Ambient mutation inside a worktree targets the worktree itself — fine.
            continue
        else:
            # Ambient mutation with a non-worktree (or non-live-worktree) cwd:
            # blockable iff cwd resolves to a root that is itself NOT
            # worktree-shaped (_blockable_ambient_root guards this — review
            # finding, dev-env#749 — since cwd_is_worktree being False no
            # longer guarantees cwd was never worktree-shaped at all, only
            # that it wasn't confirmed LIVE).
            # None -> not blockable -> fail open
            block_root = _blockable_ambient_root(_memoized_toplevel(cwd, toplevel_cache))

        if block_root is None:
            continue  # this match isn't blockable — keep scanning later segments
        _emit_block(m["segment"], block_root)  # writes stderr JSON + exit 2

    sys.exit(0)  # no blockable match found


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
