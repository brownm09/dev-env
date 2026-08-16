#!/usr/bin/env python3
r"""_repo_target.py — one quote-aware resolver for a gh command's target repo/PR.

Five PostToolUse/Stop hooks each independently reimplemented the same primitive:
given a ``gh pr merge`` / ``gh pr create`` / ``gh issue create`` (and, for
one, ``gh issue close``) command, work out the ``owner/repo`` it targets —
honoring an explicit
``--repo``/``-R`` flag over cwd, then a ``github.com/<owner>/<repo>/pull/<N>``
URL, then a positional number — with quote-aware masking so a ``--subject`` /
``--body`` value can never hijack the match.  The five copies had **drifted**:

  * ``post-pr-merge-project.py`` / ``pr-merge-reminder.py`` / ``post-pr-merge-pull.py``
        ``(?<!\S)(?:--repo|-R)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)``  (space only,
        strict slug — silently misses the ``--repo=owner/repo`` `=` form)
  * ``posttooluse-inert-advisory.py``
        ``(?<!\S)(?:--repo|-R)[=\s]+(\S+)``                            (= or space,
        loose ``\S+`` capture)
  * ``stop-tile-enumeration-gate.py``
        ``(?<!\S)(?:--repo|-R)(?:=|\s+)(?P<repo>[^\s/]+/[^\s]+)``      (= or space,
        semi-loose named capture)

Three regex shapes for one conceptual match is exactly the divergence that
produces silent-misrouting bugs — a merge's board-update or advisory logic
acting on the wrong repo.  This module is the single implementation the five
files converge on, the same pattern this repo already uses for ``_hookio.py`` /
``_hookutil.py`` / ``_worktree_canon.py`` / ``_journal_schema.py`` (each a pure
shared module with its own dedicated test file, imported by multiple hooks).

Correct ``--repo``/``-R`` extraction is inseparable from correct
argument-region bounding, so this module folds in the two ``_hookio`` masking
primitives rather than re-deriving them:

  * ``mask_quoted_spans`` (ADR-050 Amendment 15) — the ``--repo``/``-R`` flag
    search always runs against a ``mask_quoted_spans``-masked copy of its input,
    so a ``--subject``/``--body`` value containing a space-separated
    ``-R other/repo`` substring can never be mistaken for a real flag.  This is
    baked into ``repo_from_flag`` itself (the caller chooses only the *scope* of
    the text — the merge/create args region, the whole command, or a single
    top-level segment — never *whether* to mask).
  * ``mask_prose_flag_values`` (ADR-050 Amendment 17) — the PR-URL / issue-URL
    searches are NOT masked internally, because these primitives are also reused
    on trusted ``gh`` *output* (``iter_pr_urls``/``iter_issue_urls`` scanning a
    merge's own confirmation text), which must stay unmasked.  Every
    command-string caller masks with ``mask_prose_flag_values`` before calling;
    the choice of *when* to mask lives with the caller, not this module.

Argument-region bounding (``merge_args`` / ``create_args``) is itself
quote-aware (ADR-050 Amendment 20): the ``[^\n;|&]*`` region regex has no
quote-awareness of its own, so it runs against a length-preserving
``mask_quoted_spans`` copy first and the returned slice is taken from the
*original* (unmasked) command — so a ``--subject "part1 && part2"`` value no
longer truncates the args region before a later real ``--repo`` is seen.

cd-chain / ``-C`` redirect-directory resolution is NOT re-implemented here: it
already lives in ``_hookio.effective_merge_dir`` (ADR-067) and is imported
directly by the consumers that need it.  This module owns only the
*command-string* target extraction; directory resolution stays in ``_hookio``.

Imported the same way as ``_hookio`` / ``_winsubp``: a sibling module in
``scripts/`` that the ``pyw -3`` hook launcher (which puts the script's own
directory on ``sys.path``) and the test harness (``sys.path.insert(0,
scripts_dir)``) both resolve.  Pure functions only — no I/O, no subprocess.

Usage:
    from _repo_target import repo_from_flag, merge_args, create_args, issue_create_args
    from _repo_target import repo_from_pr_url, pr_number_from_pr_url, iter_pr_urls
    from _repo_target import iter_issue_urls, issue_number_from_issue_url
    from _repo_target import positional_number
    from _repo_target import repo_from_rest_merge_path, pr_number_from_rest_merge_path

See ADR-111 (this consolidation; ends the per-site ADR-050 amendment treadmill
for the repo-flag concern) and ADR-050 (the amendment history it supersedes for
this concern).  ``repo_from_flag`` also normalizes a full-URL / host-prefixed
``--repo`` value, and ``issue_create_args`` was added, so post-tool-use.py — a
sixth consumer — fully migrates onto this resolver (dev-env#838, folding in its
former private ``_REPO_HOST_PREFIX_RE`` / dev-env#544 normalization).
"""

from __future__ import annotations

import re

from _hookio import mask_quoted_spans, strip_line_continuations

# ---------------------------------------------------------------------------
# --repo / -R flag  (canonical, ends the three-shape drift)
#
#   * (?<!\S)      standalone-token lookbehind — never a mid-word "-R" (e.g. a
#                  PR title containing "add-R support"); every prior copy had it.
#   * (?:--repo|-R) both the long form and gh's documented `-R, --repo` short
#                  form (dev-env#616 added -R to the three that lacked it).
#   * (?:=|\s+)    BOTH the `--repo=owner/repo` and `--repo owner/repo` forms —
#                  the space-only copies silently missed the `=` form (dev-env#482
#                  Gap 2's sibling; gh accepts both).
#   * host prefix  (?:https?://)?(?:www\.)?(?:github\.com/)? — gh also accepts a
#                  full URL or a bare `github.com/owner/repo` host-prefixed value
#                  (https://cli.github.com/manual/gh#--repo-string); the prefix is
#                  consumed but NOT captured, so the result stays a bare
#                  `owner/repo`.  Folds in post-tool-use.py's former private
#                  `_REPO_HOST_PREFIX_RE` so that sixth consumer fully migrates
#                  onto this resolver (dev-env#838 / dev-env#544) — and fixes the
#                  same latent mis-capture (`github.com/owner`) in the other five.
#   * strict slug  [A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+ — the GitHub-legal owner/repo
#                  shape (the loose \S+ / [^\s/]+ captures only ever differed on
#                  malformed input a real gh invocation never produces).
# ---------------------------------------------------------------------------
_REPO_FLAG_RE = re.compile(
    r"(?<!\S)(?:--repo|-R)(?:=|\s+)"
    r"(?:https?://)?(?:www\.)?(?:github\.com/)?"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)

# github.com/<owner>/<repo>/pull/<N> and .../issues/<N>.  Scheme-agnostic (a PR
# URL is always https in practice, but requiring it bought nothing and the
# copies disagreed on it) and strict-slug (the loose [^/\s]+ copy only differed
# on malformed input).  Group 1 = owner/repo, group 2 = number.
_PR_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)")
_ISSUE_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)")

# A bare positional integer token: `380` on its own, not a digit run inside a
# flag value (`--foo=12`), a URL (`/pull/12`), or a branch name (`my-branch-2`).
# Callers pass this through positional_number(), which masks first so a decoy
# number inside a quoted value ("resolves 42 items") can't hijack it.
_POS_NUM_RE = re.compile(r"(?<!\S)(\d+)(?=\s|$)")

# The argument list of a `gh pr merge` / `gh pr create` / `gh issue create`
# invocation only — up to the next shell separator — so a /pull/N URL or a --repo
# flag in a --subject value, or in a chained sibling command, cannot hijack the
# extraction.  Group 1 is the args span.  The negated class has no quote-awareness
# of its own, which is why _invocation_args searches a mask_quoted_spans copy
# (ADR-050 Amendment 20).
_MERGE_ARGS_RE = re.compile(r"\bgh\s+pr\s+merge\b([^\n;|&]*)")
_CREATE_ARGS_RE = re.compile(r"\bgh\s+pr\s+create\b([^\n;|&]*)")
_ISSUE_CREATE_ARGS_RE = re.compile(r"\bgh\s+issue\s+create\b([^\n;|&]*)")


def repo_from_flag(text: str) -> str | None:
    """Return the ``owner/repo`` value of a standalone ``--repo``/``-R`` flag in
    *text*, or ``None``.

    Handles the ``--repo owner/repo`` and ``--repo=owner/repo`` forms (and the
    ``-R`` shorthand of each), plus a full-URL or bare host-prefixed value
    (``--repo https://github.com/owner/repo`` / ``--repo github.com/owner/repo``)
    that gh also accepts — the ``https://``/``www.``/``github.com/`` prefix is
    stripped so the return is always a bare ``owner/repo`` (dev-env#838, folding
    in post-tool-use.py's former private normalization — dev-env#544).  *text* is
    masked with ``mask_quoted_spans`` internally before the search, so a
    ``--subject``/``--body`` value containing a space-separated ``-R other/repo``
    substring can never be mistaken for a real flag (ADR-050 Amendment 15).

    The caller chooses the *scope* of *text* — the merge/create args region
    (``merge_args`` / ``create_args`` / ``issue_create_args``, statement-scoped so
    a chained sibling command's flag can't leak in — dev-env#667/#482), the whole
    command, or a single top-level segment — but never whether to mask; that
    invariant, kept re-derived across ADR-050 Amendments 15/17/18, lives here now.
    """
    m = _REPO_FLAG_RE.search(mask_quoted_spans(text))
    return m.group(1) if m else None


def _invocation_args(command: str, invocation_re: re.Pattern) -> str | None:
    """Return the real (unmasked) argument text of the first top-level
    ``invocation_re`` match in *command*, or ``None``.

    Bounded against a ``mask_quoted_spans``-masked copy of *command* so a bare
    ``&``/``|``/``;`` inside a quoted ``--subject``/``--body`` value does not
    truncate the region early (ADR-050 Amendment 20).  ``mask_quoted_spans`` is
    length-preserving, so the match's span offsets apply unchanged to the
    original — the returned slice is genuine (unmasked) argument text, so a
    downstream ``repo_from_flag`` / ``mask_prose_flag_values`` call still sees
    real quote/flag-value content to mask in its own turn.

    Shell backslash-newline line-continuations are stripped first
    (``strip_line_continuations``, dev-env#831) so a multi-line invocation with a
    ``--repo``/PR-number on a continued line is not truncated at the join.
    """
    command = strip_line_continuations(command)  # join shell line-continuations (dev-env#831)
    m = invocation_re.search(mask_quoted_spans(command))
    if not m:
        return None
    start, end = m.span(1)
    return command[start:end]


def merge_args(command: str) -> str | None:
    """The ``gh pr merge`` invocation's own argument text in *command*, or
    ``None`` (quote-aware statement-bounded — see ``_invocation_args``)."""
    return _invocation_args(command, _MERGE_ARGS_RE)


def create_args(command: str) -> str | None:
    """The ``gh pr create`` invocation's own argument text in *command*, or
    ``None`` (quote-aware statement-bounded — see ``_invocation_args``)."""
    return _invocation_args(command, _CREATE_ARGS_RE)


def issue_create_args(command: str) -> str | None:
    """The ``gh issue create`` invocation's own argument text in *command*, or
    ``None`` (quote-aware statement-bounded — see ``_invocation_args``).

    The ``gh issue create`` counterpart of ``create_args``, added so
    post-tool-use.py's cross-repo ``--repo`` extraction — which fires for both
    ``gh issue create`` and ``gh pr create`` — resolves both invocations through
    this one resolver instead of its own private regex (dev-env#838)."""
    return _invocation_args(command, _ISSUE_CREATE_ARGS_RE)


def repo_from_pr_url(text: str) -> str | None:
    """``owner/repo`` from the first ``github.com/<owner>/<repo>/pull/<N>`` URL in
    *text*, or ``None``.  *text* is used as-is — the caller decides whether to
    mask it first (four sites mask ``--subject``/``--body`` decoys via
    ``mask_prose_flag_values``; the tile gate keeps a bare quoted URL matchable),
    so this never masks internally."""
    m = _PR_URL_RE.search(text)
    return m.group(1) if m else None


def pr_number_from_pr_url(text: str) -> int | None:
    """The ``N`` from the first ``github.com/<owner>/<repo>/pull/<N>`` URL in
    *text*, or ``None``.  *text* is used as-is (see ``repo_from_pr_url``)."""
    m = _PR_URL_RE.search(text)
    return int(m.group(2)) if m else None


def iter_pr_urls(text: str) -> list[tuple[str, int]]:
    """Every ``github.com/<owner>/<repo>/pull/<N>`` URL in *text*, in order, as
    ``(owner/repo, number)`` pairs.  *text* is used as-is (see
    ``repo_from_pr_url``) — typically gh's own trusted output, where every URL is
    a genuine PR reference."""
    return [(m.group(1), int(m.group(2))) for m in _PR_URL_RE.finditer(text)]


def issue_number_from_issue_url(text: str) -> int | None:
    """The ``N`` from the first ``github.com/<owner>/<repo>/issues/<N>`` URL in
    *text*, or ``None``.  *text* is used as-is (see ``repo_from_pr_url``)."""
    m = _ISSUE_URL_RE.search(text)
    return int(m.group(2)) if m else None


def iter_issue_urls(text: str) -> list[tuple[str, int]]:
    """Every ``github.com/<owner>/<repo>/issues/<N>`` URL in *text*, in order, as
    ``(owner/repo, number)`` pairs.  *text* is used as-is (see
    ``repo_from_pr_url``)."""
    return [(m.group(1), int(m.group(2))) for m in _ISSUE_URL_RE.finditer(text)]


def positional_number(text: str) -> int | None:
    """The first bare positional integer token in *text*, or ``None``.

    *text* is masked with ``mask_quoted_spans`` internally before the search, so
    a bare number inside a quoted value ("resolves 42 items") can't be mistaken
    for a real positional argument (ADR-050 Amendment 19) — the same
    quoted-value blind spot ``repo_from_flag`` closes for the flag, for a
    bare-digit token instead.  A digit run inside a flag value (``--foo=12``), a
    URL (``/pull/12``), or a branch name (``my-branch-2``) is not a standalone
    token and is correctly ignored.
    """
    m = _POS_NUM_RE.search(mask_quoted_spans(text))
    return int(m.group(1)) if m else None


# github.com REST API path: repos/<owner>/<repo>/pulls/<N>/merge -- the
# two-step merge fallback's PUT target (dev-env#986, ADR-050 Amendment 23).
# Distinct from _PR_URL_RE (a github.com/.../pull/N *web* URL): this is the
# `gh api` REST *path* argument -- no host prefix, "pulls" not "pull". See
# _hookio.py's "REST merge fallback detection" module comment for the full
# rationale (the companion `is_rest_merge_command` / `output_has_rest_merge_marker`
# command-shape/output-marker primitives live there; this module owns only the
# command-string target extraction, per this file's own docstring).
_REST_MERGE_PATH_RE = re.compile(
    r"repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pulls/(\d+)/merge\b"
)


def repo_from_rest_merge_path(command: str) -> str | None:
    """``owner/repo`` from a `gh api .../repos/<owner>/<repo>/pulls/<N>/merge`
    REST path in *command*, or ``None``. *command* is used as-is — the same
    convention as `repo_from_pr_url` (dev-env#986)."""
    m = _REST_MERGE_PATH_RE.search(command)
    return m.group(1) if m else None


def pr_number_from_rest_merge_path(command: str) -> int | None:
    """The ``N`` from a `gh api .../repos/<owner>/<repo>/pulls/<N>/merge`
    REST path in *command*, or ``None``. The REST response body carries no
    PR number, so the command's own path is the only source (dev-env#986)."""
    m = _REST_MERGE_PATH_RE.search(command)
    return int(m.group(2)) if m else None
