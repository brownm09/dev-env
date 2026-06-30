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

Imported the same way as ``_winsubp``: a sibling module in ``scripts/`` that the
``pyw -3`` hook launcher (which puts the script's own directory on ``sys.path``)
and the test harness (``sys.path.insert(0, scripts_dir)``) both resolve.

Usage:
    from _hookio import read_command_output, output_has_merge_marker
    from _hookio import effective_merge_dir

See ADR-049 (root cause + canonical read) and ADR-050 (shared helper + sibling
hook fixes).  See ADR-067 for the merge-dir scoping.
"""

from __future__ import annotations

import os
import re

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
