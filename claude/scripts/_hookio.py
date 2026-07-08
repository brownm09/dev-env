"""Shared I/O helpers for Claude Code PostToolUse Bash hooks.

Claude Code's Bash hook payload exposes a command's output under
``tool_response.stdout`` and ``tool_response.stderr`` — NOT ``output``.
Reading ``output`` yields ``""`` and silently breaks any hook that keys off
command output: the bug was found in ``post-tool-use.py`` (dev-env #377 /
ADR-049) and the same wrong read existed in four sibling hooks (dev-env #380).
Centralising the correct read here — plus the merge-success-marker detection the
``post-pr-merge-*`` hooks share — means every hook depends on one implementation
instead of re-deriving (and re-breaking) the field precedence or the marker set.

``effective_merge_dir`` (ADR-067) mirrors ``_effective_push_dir`` from
``pr-merge-reminder.py`` (ADR-065) but scans to ``gh pr merge`` instead of
``git push``.  It is shared here because both ``post-pr-merge-pull.py`` and
``pr-merge-reminder.py`` need it.

``split_top_level`` / ``scan_top_level`` (dev-env#499, ADR-050 Amendment 5; the
split/scan extraction is dev-env#511, ADR-050 Amendment 7) are a stack-based
command parser originally written for ``pr-merge-reminder.py``'s ``gh pr
merge`` / ``gh pr create`` / ``git push`` detection. ``scan_top_level`` is
shared with ``post-tool-use.py`` so it can detect a top-level ``gh issue
create`` / ``gh pr create`` too, instead of its previous unanchored
``re.search`` over the whole raw command string — which matched the pattern
anywhere, including inside a heredoc body, a quoted commit message, or a
``--text`` field value. ``split_top_level`` (the segment-yielding engine
``scan_top_level`` is now built on) is additionally shared with
``pre-tool-use-canonical-mutate-guard.py``, which needs the actual segment
list — not just a boolean — for its own per-segment cd-scope /
``-C``-redirect-skip / mutating-verb classification.

Imported the same way as ``_winsubp``: a sibling module in ``scripts/`` that the
``pyw -3`` hook launcher (which puts the script's own directory on ``sys.path``)
and the test harness (``sys.path.insert(0, scripts_dir)``) both resolve.

``is_merge_help_only`` (dev-env#557) closes a gap in the ``is_pr_merge_command``
family: ``gh pr merge --help`` textually satisfies every one of those
predicates (it *is* a ``gh pr merge`` invocation), so a hook's marker-absent /
non-zero-exit fallback pays a live ``gh pr view`` confirmation with no PR
number — which resolves against cwd's checked-out branch and can misattribute
an unrelated already-merged PR to the harmless ``--help`` call. Callers treat a
True result exactly like "not a merge command at all". Generalized (dev-env#636)
into ``is_help_only(command, invocation_re)`` so ``post-tool-use.py``'s
``gh issue create`` / ``gh pr create`` detectors — which had the identical
``--help`` false-positive — reuse the same segment-scan instead of a copy of it.

Usage:
    from _hookio import read_command_output, output_has_merge_marker
    from _hookio import effective_merge_dir, scan_top_level, split_top_level
    from _hookio import is_help_only, is_merge_help_only

See ADR-049 (root cause + canonical read) and ADR-050 (shared helper + sibling
hook fixes).  See ADR-067 for the merge-dir scoping.
"""

from __future__ import annotations

import _winsubp  # noqa: F401  -- suppress console windows + default UTF-8 decoding on Windows
import json
import os
import re
import subprocess
from collections.abc import Callable

# gh's completed-merge success line, e.g. "Squashed and merged pull request #380
# (Title)" — and the cross-repo "... pull request brownm09/dev-env#380" variant.
# Anchored on the action verb so a queued `--auto` ("Pull request #N will be
# automatically merged"), a failure line, or an incidental "Squashed and merged"
# substring in an unrelated chained command's output is NOT matched as a merge.
_MERGE_MARKER_RE = re.compile(
    r"(?:Merged|Squashed and merged|Rebased and merged)\s+pull request\s+"
    r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#(\d+)",
    re.IGNORECASE,
)


def read_command_output(data: dict) -> str:
    """Return a Bash command's combined output from a PostToolUse payload.

    Joins ``tool_response.stdout`` and ``tool_response.stderr`` with a newline,
    falling back to the legacy ``output`` key for forward/backward
    compatibility. Returns ``""`` for a missing, empty, ``None``, or non-dict
    ``tool_response`` — never raises, so a hook can call it unguarded.
    """
    tr = data.get("tool_response") or {}
    if not isinstance(tr, dict):
        return ""
    parts = [p for p in (tr.get("stdout"), tr.get("stderr")) if p]
    if parts:
        return "\n".join(parts)
    return str(tr.get("output", "") or "")


def output_has_merge_marker(output: str) -> bool:
    """Return True iff the output contains gh's completed-merge success line.

    Anchored on the action verb + ``pull request #N`` (see ``_MERGE_MARKER_RE``),
    so a queued ``--auto``, a failure line, or a stray ``Squashed and merged``
    substring in unrelated output does not count as a merge.
    """
    return any(_MERGE_MARKER_RE.search(line) for line in output.splitlines())


def merge_pr_number_from_output(output: str) -> int | None:
    """Return the PR number from gh's merge success marker, or ``None``.

    Scans bottom-up so the last (most recent) marker wins when output stacks
    several lines.
    """
    for line in reversed(output.splitlines()):
        m = _MERGE_MARKER_RE.search(line)
        if m:
            return int(m.group(1))
    return None


# Locates the `gh pr merge` token so the pre-merge region can be bounded.
_BARE_MERGE_RE = re.compile(r"\bgh\s+pr\s+merge\b")
# Matches a `cd <path>` followed by `&&` or `;` — identical pattern to the push
# equivalent in pr-merge-reminder.py but scoped to merge commands here.
_MERGE_CD_CHAIN_RE = re.compile(r"""cd\s+("[^"]+"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)""")


def effective_merge_dir(command: str, cwd: str) -> str:
    """Best-effort directory a top-level ``gh pr merge`` in *command* runs in.

    When a ``cd <path> &&`` (or ``;``) prefix chains into the merge — e.g.
    ``cd /other/repo && gh pr merge --squash`` — return <path> (resolved
    against *cwd* when relative), so operations that key off the merge target
    use the repo that was actually merged, not the session cwd.  A bare
    ``gh pr merge`` returns *cwd*.

    Conservative by design: any shape it cannot parse falls back to *cwd* so
    the worst case is the pre-ADR-067 behaviour — under-corrects rather than
    mis-fires.  Mirrors ``_effective_push_dir`` in ``pr-merge-reminder.py``
    (ADR-065); shared here so both ``post-pr-merge-pull.py`` and
    ``pr-merge-reminder.py`` use one implementation.
    """
    merge = _BARE_MERGE_RE.search(command)
    region = command[: merge.start()] if merge else command
    target = None
    for m in _MERGE_CD_CHAIN_RE.finditer(region):
        target = m.group(1)  # last cd before the merge governs it
    if not target:
        return cwd
    path = target.strip("\"'")
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(cwd, path))
    return path


def should_confirm_via_gh(exit_code: int, output: str) -> bool:
    """Pure: should a caller pay a live `gh pr view` call to confirm the merge?

    Only when the cheap marker check found nothing AND the exit code signals a
    possible loss (non-zero) — an exit-0 non-merge (a queued `--auto`, or any
    other clean-exit command that merely matched `gh pr merge` textually) never
    pays the network call. See `confirm_merge_via_gh` for why the check exists
    at all (dev-env#489).
    """
    return exit_code != 0 and not output_has_merge_marker(output)


def confirm_merge_via_gh(pr_number: int | None, repo: str, cwd: str) -> int | None:
    """Best-effort live confirmation that a `gh pr merge` actually merged.

    Returns the merged PR's number when `gh pr view` confirms ``state ==
    "MERGED"``, else ``None``. Fallback only — costs a network round-trip, so
    callers should gate on `should_confirm_via_gh` first.

    gh prints its completed-merge success line before attempting the local
    branch checkout/delete that can fail with "main is already checked out"
    (confirmed by reading gh's own source: the `infof` success print in
    `mergeRun` happens before `deleteLocalBranch` runs) — but that line does not
    reliably survive to a PostToolUse hook's captured stdout/stderr when gh
    exits abruptly right after the failing git subprocess (dev-env#489, live
    reproduction: merging dev-env PR #493 lost the marker on the identical
    failure shape). This asks GitHub directly rather than trusting local output.

    *pr_number*, when already known (e.g. from `extract_pr_number_from_command`),
    is passed as an explicit argument together with *repo* for a cwd-independent
    lookup. When absent (the bare `gh pr merge --squash --delete-branch` form
    names no PR), `gh pr view` with no argument infers the PR from *cwd*'s
    checked-out branch, and the returned number comes from that same call.
    """
    args = ["gh", "pr", "view"]
    if pr_number is not None:
        args.append(str(pr_number))
        if repo:
            args += ["--repo", repo]
    args += ["--json", "state,number"]
    try:
        # text=True decodes as UTF-8 (not the Windows cp1252 default) via the
        # _winsubp patch imported above — dev-env#503.
        result = subprocess.run(args, capture_output=True, text=True, timeout=15, cwd=cwd)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if data.get("state") != "MERGED":
            return None
        return data.get("number", pr_number)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Top-level statement scanning (dev-env#499, ADR-050 Amendment 5)
#
# Originally written for pr-merge-reminder.py's gh pr merge / gh pr create /
# git push detection; shared here so post-tool-use.py's gh issue create / gh
# pr create detection gets the same top-level-statement-only guarantee instead
# of an unanchored re.search over the whole raw command string.
#
# split_top_level() (dev-env#511, ADR-050 Amendment 7) extracts the segment-
# yielding core so pre-tool-use-canonical-mutate-guard.py can converge onto
# the same quote/subshell/heredoc-aware engine instead of its own narrower
# regex-based splitter — that hook needs the actual segment list (not just a
# boolean) to find cd-redirects, skip git -C/--git-dir segments, and report
# which segment matched. scan_top_level() is now a thin boolean-reducer
# wrapper over split_top_level(); its own two callers are unaffected.
# ---------------------------------------------------------------------------


def _find_heredoc_end(cmd: str, start: int) -> int:
    """start = index of first '<' in '<<…'. Returns index just past the heredoc body.

    Handles <<DELIM, <<'DELIM', <<"DELIM", and <<-DELIM (tab-stripping) forms.
    """
    n = len(cmd)
    i = start + 2  # skip '<<'
    strip_tabs = False
    if i < n and cmd[i] == "-":
        strip_tabs = True
        i += 1
    # Read delimiter — may be wrapped in ' or "
    quote: str | None = None
    if i < n and cmd[i] in ("'", '"'):
        quote = cmd[i]
        i += 1
    stop_chars = "\n\r" + (quote or "")
    delim_start = i
    while i < n and cmd[i] not in stop_chars:
        i += 1
    delimiter = cmd[delim_start:i]
    if quote and i < n and cmd[i] == quote:
        i += 1  # skip closing quote
    # Skip to end of the <<… declaration line
    while i < n and cmd[i] not in ("\n", "\r"):
        i += 1
    if i < n:
        i += 1  # skip newline
    # Scan lines until we find the terminator
    while i < n:
        line_start = i
        if strip_tabs:
            while i < n and cmd[i] == "\t":
                i += 1
            line_start = i
        while i < n and cmd[i] not in ("\n", "\r"):
            i += 1
        if cmd[line_start:i] == delimiter:
            if i < n:
                i += 1  # skip terminator's newline
            return i
        if i < n:
            i += 1  # skip newline
    return i


def split_top_level(command: str, *, split_pipe: bool = False) -> list[str]:
    """Split *command* into its top-level statements/segments — i.e. not
    inside a quoted string, $() subshell, or heredoc body.

    Uses a stack-based parser with four states ('top', 'single', 'double',
    'subshell') so that shell operators buried inside quoted arguments, command
    substitutions, or heredoc content are never mistaken for top-level statement
    separators.  Specifically handles:
    - Single/double quotes
    - $() subshells (including $() inside "…")
    - <<DELIM / <<'DELIM' heredoc bodies (both bare and inside a subshell)

    Always splits on ``;``, ``\\n``, ``&&``, and ``||``. A lone ``|`` (not part
    of ``||``) is also a split point when *split_pipe* is True — off by
    default so `scan_top_level`'s existing callers (`pr-merge-reminder.py`,
    `post-tool-use.py`, neither of which needs pipe-awareness) see zero
    behavior change from this function's introduction.
    `pre-tool-use-canonical-mutate-guard.py` passes `split_pipe=True`, since a
    mutating git invocation can appear after a pipe (e.g.
    `echo msg | git commit -F -`).

    Segments are returned unstripped, in original order — callers typically
    do ``segment.lstrip()`` then an anchored ``.match()`` so that a target
    phrase appearing mid-segment (rather than at its start) is not mistaken
    for a genuine invocation.

    If *command* ends with an unterminated quote/subshell/heredoc, the
    trailing (malformed) segment is dropped rather than returned — mirrors
    `scan_top_level`'s pre-existing fail-permissive behavior for an
    unparseable tail (a caller scanning for a match sees no match from that
    segment either way).
    """
    segments: list[str] = []
    n = len(command)
    i = 0
    stmt_start = 0
    # Stack entries: 'top' | 'single' | 'double' | 'subshell'
    stack = ["top"]

    while i < n:
        c = command[i]
        state = stack[-1]

        if state == "single":
            if c == "'":
                stack.pop()

        elif state == "double":
            if c == "\\" and i + 1 < n:
                i += 1  # skip escaped char
            elif c == '"':
                stack.pop()
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                # $() inside "…" — track subshell so its content is opaque
                stack.append("subshell")
                i += 1  # skip '('

        elif state == "subshell":
            if c == ")":
                stack.pop()
            elif c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "(":
                stack.append("subshell")
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                # heredoc inside subshell — skip body entirely
                i = _find_heredoc_end(command, i)
                continue

        else:  # state == 'top'
            if c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                i = _find_heredoc_end(command, i)
                continue
            elif c in (";", "\n"):
                segments.append(command[stmt_start:i])
                stmt_start = i + 1
            elif c == "&" and i + 1 < n and command[i + 1] == "&":
                segments.append(command[stmt_start:i])
                stmt_start = i + 2
                i += 1
            elif c == "|":
                if i + 1 < n and command[i + 1] == "|":
                    segments.append(command[stmt_start:i])
                    stmt_start = i + 2
                    i += 1
                elif split_pipe:
                    segments.append(command[stmt_start:i])
                    stmt_start = i + 1

        i += 1

    if stack == ["top"]:
        segments.append(command[stmt_start:])
    return segments


def scan_top_level(command: str, check_fn: Callable[[str], bool]) -> bool:
    """Return True when *command* contains a top-level statement matched by
    *check_fn* — i.e. not inside a quoted string, $() subshell, or heredoc body.

    Thin wrapper over `split_top_level` (pipe-unaware — see that function's
    docstring for why `split_pipe` defaults off here). *check_fn* is called
    with each top-level statement/segment (unstripped); callers typically do
    ``token.lstrip()`` then an anchored ``.match()`` so that a target phrase
    appearing mid-segment (rather than at its start) is not mistaken for a
    genuine invocation.
    """
    return any(check_fn(seg) for seg in split_top_level(command))


# ---------------------------------------------------------------------------
# mask_quoted_spans  (dev-env#626, ADR-050 Amendment 15)
#
# The _REPO_FLAG_RE family (post-pr-merge-project.py, pr-merge-reminder.py,
# posttooluse-inert-advisory.py, post-pr-merge-pull.py) searches for a
# standalone --repo/-R token directly in raw command text. The (?<!\S)
# lookbehind added in ADR-050 Amendment 14 stops a mid-word match but not a
# legitimately space-separated "-R other/repo" substring sitting INSIDE a
# quoted --subject/--body value (dev-env#626) -- the lookbehind only checks
# that the preceding character is whitespace, which a quoted value's own
# internal spacing satisfies just as well as a real top-level flag boundary.
#
# mask_quoted_spans blinds exactly the spans a repo-flag regex must never
# match: single-/double-quoted text, $() subshell contents (tracked with the
# same nested-state rules as split_top_level, since a subshell can itself
# contain further quotes that would otherwise close an enclosing double quote
# early), and heredoc bodies.
#
# Deliberately an INDEPENDENT walker, not a refactor of split_top_level's
# internals -- it mirrors that function's state transitions (same four
# states) and reuses the already-shared _find_heredoc_end, but split_top_level
# itself is untouched. split_top_level has ~30 existing tests and several
# callers across this hook family, hardened over Amendments 5 and 7; Amendment
# 7's own postmortem ("/review's adversarial pass, not the hand-written test
# suite, is what caught this") is a direct caution against generalizing its
# internals for an unrelated need without very deliberate scrutiny. This
# mirrors Amendment 5's own scope decision ("only the engine is shared, not
# the wrapper functions") one level up: the true shared atom
# (_find_heredoc_end) is reused; the two higher-level walks stay independent
# so a future change to one's splitting/masking semantics can never silently
# perturb the other's heavily-tested behavior. Because the two are
# independent, nothing statically guarantees they never drift apart on what
# counts as "inside a quote/subshell/heredoc" -- test_hookio.py's
# test_mask_quoted_spans_agrees_with_split_top_level enforces that agreement
# directly rather than leaving it as a prose reminder to keep in sync (ADR-050
# Amendment 11's own lesson: a written "keep these in sync" note is exactly
# as missable as the bug it guards against).
#
# Each caller must mask ONLY the exact string fed to its own vulnerable
# repo-flag regex, never a fallback regex (a PR-URL or PR-number match) whose
# result is reused elsewhere -- some of those legitimately match a quoted
# value today (see mask_quoted_spans's own docstring). This invariant is
# hand-applied at four call sites with two different masking scopes (a
# _MERGE_ARGS_RE-derived `args` region vs. the whole `command`), rather than
# a single shared "mask-then-search" helper -- considered and rejected for
# this PR: the four sites' masking scope and which-regex-stays-unmasked
# already differ per site, so a shared helper would need immediate
# parameterization no current caller asks for, the same premature-convergence
# risk Amendment 5's scope decision warns against. Each site's regression
# test suite (the "*_survives_alongside_quoted_decoy" cases) is the durable,
# enforced check that this hand-wiring is correct today, in place of a
# structural guarantee.
# ---------------------------------------------------------------------------


def mask_quoted_spans(command: str) -> str:
    """Return *command* with every single-/double-quoted span, $() subshell,
    and heredoc body replaced by a same-length run of '#' (newlines
    preserved), so a regex search over the result can never match text that
    only appears inside a quoted value, command substitution, or heredoc.

    A repo-flag regex (or similar) run against this masked text instead of
    the raw command can no longer mistake a --subject/--body value like
    ``"see -R other/repo for context"`` for a genuine standalone --repo/-R
    flag (dev-env#626) -- the value's entire quoted span is blanked before
    the regex ever sees it. Text outside any opaque span is returned
    byte-for-byte unchanged, so a match against the masked string captures
    the identical substring/offsets a match against the original would have,
    whenever the match is genuine (i.e. not itself inside a masked span,
    which by construction can never match anything but '#'/newline runs).

    '#' is used as the placeholder. The four callers' own repo-flag regexes
    (not defined in this file -- see each hook's own _REPO_FLAG_RE) mostly
    capture a strict owner/repo shape ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+), which
    '#' cannot satisfy; a masked span can therefore only remove a real match
    there, never manufacture one. One caller's regex is looser
    (posttooluse-inert-advisory.py's `(\\S+)` capture group, since it only
    needs to compare the result against a known repo string, not validate
    slug shape) and CAN capture a run of '#' from a masked span -- but the
    anchor itself (a literal `--repo`/`-R` token) still can't be synthesized
    from '#' characters, so masking still only flips a match from
    present-but-wrong-value to a value that fails the caller's equality
    check, never from absent to falsely present. Newlines are preserved
    unmasked so a heredoc body's line count survives
    (matters only if a caller later applies a line-oriented helper to the
    masked result; no current caller does, but it costs nothing and mirrors
    _find_heredoc_end's own care with heredoc line structure).

    Callers must mask ONLY the exact string fed to the vulnerable repo-flag
    regex, never a string whose match is reused for something else (e.g. a
    PR-URL or PR-number fallback) -- some of those legitimately match a
    quoted value today (see each hook's own call site for the precise scope).

    See split_top_level's docstring for the shared quote/subshell/heredoc
    opacity rules this function's state machine mirrors (kept as an
    independent walk rather than a shared implementation -- see the module
    comment above this function).
    """
    n = len(command)
    opaque: list[tuple[int, int]] = []
    i = 0
    stack = ["top"]
    span_start = None  # index where 'top' was left, or None while in 'top'

    while i < n:
        c = command[i]
        state = stack[-1]

        if state == "top":
            if c == "'":
                stack.append("single")
                span_start = i
            elif c == '"':
                stack.append("double")
                span_start = i
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                span_start = i
                i += 1
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                end = _find_heredoc_end(command, i)
                opaque.append((i, end))
                i = end
                continue
            # else: real top-level text -- leave unmasked

        elif state == "single":
            if c == "'":
                stack.pop()
                if stack[-1] == "top":
                    opaque.append((span_start, i + 1))
                    span_start = None

        elif state == "double":
            if c == "\\" and i + 1 < n:
                i += 1
            elif c == '"':
                stack.pop()
                if stack[-1] == "top":
                    opaque.append((span_start, i + 1))
                    span_start = None
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1

        else:  # state == "subshell"
            if c == ")":
                stack.pop()
                if stack[-1] == "top":
                    opaque.append((span_start, i + 1))
                    span_start = None
            elif c == "'":
                stack.append("single")
            elif c == '"':
                stack.append("double")
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                stack.append("subshell")
                i += 1
            elif c == "(":
                stack.append("subshell")
            elif c == "<" and i + 1 < n and command[i + 1] == "<":
                i = _find_heredoc_end(command, i)
                continue

        i += 1

    if stack != ["top"]:
        # Unterminated quote/subshell -- mask the tail too. Mirrors
        # split_top_level's fail-permissive contract for this same case
        # (drops the trailing malformed segment): a caller's regex must see
        # no match from this content either way.
        opaque.append((span_start, n))

    out = list(command)
    for start, end in opaque:
        for idx in range(start, end):
            if out[idx] not in ("\n", "\r"):
                out[idx] = "#"
    return "".join(out)


# ---------------------------------------------------------------------------
# is_help_only / is_merge_help_only  (dev-env#557, generalized dev-env#636)
#
# `gh pr merge --help` textually satisfies every `is_pr_merge_command` /
# `_check_merge_stmt` predicate in this hook family — it *is* a `gh pr merge`
# invocation, syntactically. Since --help prints no success marker,
# `should_confirm_via_gh` returns True (no marker + non-zero/-1 exit),
# triggering a live `gh pr view` confirmation with no explicit PR number —
# which resolves against cwd's checked-out branch and can misattribute an
# unrelated already-merged PR to the --help invocation (confirmed incident:
# dev-env#557). --help/-h can *categorically never* attempt a real merge, so
# a command consisting only of --help/-h invocations of a given kind should
# short-circuit before any of that marker/exit-code logic runs at all.
#
# `post-tool-use.py`'s `is_issue_create_command` / `is_pr_create_command` have
# the identical shape of bug: `gh issue create --help` (run per this repo's own
# CLI Scripting Checklist, which prescribes checking `--help` before writing gh
# automation) textually matches, so the hook proceeds as if a real issue had
# been created (dev-env#636). `is_help_only` extracts the reusable core so a
# second/third caller doesn't need a near-verbatim copy of the same
# segment-scan + all() logic; `is_merge_help_only` becomes a thin wrapper over
# it with its pre-existing signature/behavior unchanged.
# ---------------------------------------------------------------------------

_GH_MERGE_INVOCATION_RE = re.compile(r"gh(?:\.exe)?\s+pr\s+merge\b", re.IGNORECASE)

# A standalone --help/-h flag token: bounded by whitespace/start/end so it
# can't be mistaken for part of a longer token (e.g. `--helpful`, a branch
# named `-help-me`) or for the "--help" substring inside a longer flag value.
# Case-insensitive to match _GH_MERGE_INVOCATION_RE's leniency above.
_HELP_FLAG_RE = re.compile(r"(?:^|\s)(?:--help|-h)(?:\s|$)", re.IGNORECASE)


def _first_line(segment: str) -> str:
    """Return *segment*'s own first physical line.

    A segment's `gh pr merge` invocation and its flags only ever appear on
    this line — `split_top_level` can return a segment spanning multiple
    physical lines when a heredoc/`$()` span is part of it (that's the point
    of its opacity: the span stays inside the segment rather than being split
    out), and everything after that first line is heredoc/command-
    substitution BODY data, never additional invocation syntax. A separate,
    independently-defined but identically-purposed helper of the same name
    already exists in `pre-tool-use-canonical-mutate-guard.py` for the same
    reason: a heredoc body that merely *mentions* "--help" (or a mutating
    verb, in that file's case) as prose must not be mistaken for a real flag
    on the invocation. Not shared/imported across the two files — each one's
    own module stays self-contained.
    """
    return segment.split("\n", 1)[0].split("\r", 1)[0]


def is_help_only(command: str, invocation_re: re.Pattern) -> bool:
    """True iff every top-level segment of *command* matched by
    *invocation_re* is a --help/-h invocation.

    Callers already gate on their own "is this the command at all" check
    first (e.g. `is_pr_merge_command`, `is_issue_create_command`), so the
    "no matching segments at all" case returns False here too — this
    predicate only ever needs to answer "given that at least one segment
    matched, were ALL of them --help-only?".

    A chained ``<cmd> --help && <cmd> 380 --squash`` therefore correctly
    returns False: a real invocation elsewhere in the same command must
    never be suppressed just because an earlier segment was a harmless flag
    check.

    Each segment's --help/-h check is scoped to its own first physical line
    (`_first_line()`) so a heredoc/`$()` body that merely *mentions*
    "--help" as prose cannot be mistaken for a real flag on this segment's
    own invocation. *invocation_re* itself is matched at the (lstripped)
    start of that first line — mirroring every sibling `_check_*_stmt`'s
    ``.match(token.lstrip())`` convention — so a phrase appearing mid-segment
    (e.g. inside an earlier quoted argument on the same segment) is never
    mistaken for a genuine invocation.
    """
    matching_segments = [
        seg for seg in split_top_level(command)
        if invocation_re.match(_first_line(seg).lstrip())
    ]
    if not matching_segments:
        return False
    return all(_HELP_FLAG_RE.search(_first_line(seg)) for seg in matching_segments)


def is_merge_help_only(command: str) -> bool:
    """True iff every top-level `gh pr merge` segment in *command* is a
    --help/-h invocation. Thin wrapper over `is_help_only` (see its docstring
    for the full rationale) preserving this function's pre-existing name and
    signature; see dev-env#557 (this module's header docstring) for the
    motivating incident."""
    return is_help_only(command, _GH_MERGE_INVOCATION_RE)
