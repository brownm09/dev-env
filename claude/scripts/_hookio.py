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

``scan_top_level`` (dev-env#499, ADR-050 Amendment 5) is a stack-based command
parser originally written for ``pr-merge-reminder.py``'s ``gh pr merge`` /
``gh pr create`` / ``git push`` detection. It is shared here so
``post-tool-use.py`` can detect a top-level ``gh issue create`` / ``gh pr
create`` too, instead of its previous unanchored ``re.search`` over the whole
raw command string — which matched the pattern anywhere, including inside a
heredoc body, a quoted commit message, or a ``--text`` field value.

Imported the same way as ``_winsubp``: a sibling module in ``scripts/`` that the
``pyw -3`` hook launcher (which puts the script's own directory on ``sys.path``)
and the test harness (``sys.path.insert(0, scripts_dir)``) both resolve.

Usage:
    from _hookio import read_command_output, output_has_merge_marker
    from _hookio import effective_merge_dir, scan_top_level

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


def scan_top_level(command: str, check_fn: Callable[[str], bool]) -> bool:
    """Return True when *command* contains a top-level statement matched by
    *check_fn* — i.e. not inside a quoted string, $() subshell, or heredoc body.

    Uses a stack-based parser with four states ('top', 'single', 'double',
    'subshell') so that shell operators buried inside quoted arguments, command
    substitutions, or heredoc content are never mistaken for top-level statement
    separators.  Specifically handles:
    - Single/double quotes
    - $() subshells (including $() inside "…")
    - <<DELIM / <<'DELIM' heredoc bodies

    *check_fn* is called with each top-level statement/segment (unstripped);
    callers typically do ``token.lstrip()`` then an anchored ``.match()`` so
    that a target phrase appearing mid-segment (rather than at its start) is
    not mistaken for a genuine invocation.
    """
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
                if check_fn(command[stmt_start:i]):
                    return True
                stmt_start = i + 1
            elif c == "&" and i + 1 < n and command[i + 1] == "&":
                if check_fn(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1
            elif c == "|" and i + 1 < n and command[i + 1] == "|":
                if check_fn(command[stmt_start:i]):
                    return True
                stmt_start = i + 2
                i += 1

        i += 1

    if stack == ["top"]:
        return check_fn(command[stmt_start:])
    return False
