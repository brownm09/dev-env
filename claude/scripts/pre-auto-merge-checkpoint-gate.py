#!/usr/bin/env python3
"""Claude Code PreToolUse hook — mechanical pre-check gate for `gh pr merge --auto` (ADR-083).

Extends `pre-merge-findings-gate.py`'s marker-based enforcement (ADR-028/ADR-039) to two more
merge-time checkpoints ADR-011 and ADR-019 define but had no mechanical artifact for: the
ADR-warrant check and the doc-reconciliation check. `/review`'s Step 2f/Step 8 now record both as
a second PR-comment marker, `<!-- premerge-checkpoints: adr_warrant=... doc_reconciliation=... -->`,
emitted alongside the existing `<!-- review-findings: ... -->` marker in the same comment.

This hook only acts when a `gh pr merge` command carries `--auto` (not an explicit
`--auto=false/0/no`, a genuine no-op mirroring `is_mutating_gh_segment`'s `--delete-branch=false`
handling). When active, it requires the PR's single most recent comment that carries BOTH markers
together, with: the review-findings marker clean or disposed (identical condition to the sibling
gate), both premerge-checkpoints fields holding one of their two valid values
(`written`/`not-warranted`, `updated`/`not-applicable` — a third literal `missing` exists for an
unresolved gap and deliberately fails this check), and that comment's `createdAt` no older than
the PR's current head commit's `committedDate`.

Fails CLOSED (exit 2) on any gap or error — a deliberate inversion of the sibling gate's fail-open
behavior (ADR-083 Decision point 3): `--auto` removes every other in-session backstop the moment it
succeeds, so a `gh`/network blip must not become a free pass to skip all three checkpoints and get
an unattended async merge. No override token exists (ADR-083 Decision point 4) — the always-
available fallback is dropping `--auto` and running a plain `gh pr merge` after CI is green, which
this gate does not touch at all.

Reuses `pre-merge-findings-gate.py`'s `is_pr_merge_command`, `_parse_merge_target`, `_MARKER_RE`,
`_DISPOSED_RE`, and `_fetch_pr_json` (which carries the `MERGE_GATE_TEST_JSON` test seam) via
`importlib.util.spec_from_file_location` — the same dynamic-load trick
`test_pre_merge_findings_gate.py` and `test-merge-findings-gate.sh` already use, since a hyphenated
filename isn't a valid Python import name. No `sys.path.insert` is needed here (unlike those two
test harnesses, which live a directory level down in `tests/`): `pyw -3` puts this hook's own
directory on `sys.path[0]` automatically, and since this file lives alongside
`pre-merge-findings-gate.py` in the same `claude/scripts/` directory, that's already enough for
both the dynamic load and `_pmfg`'s own internal `from _hookio import ...` to resolve.

Stdin JSON shape (PreToolUse): {"tool_name":"Bash","tool_input":{"command":...},"cwd":...}

Exit 2 — block (stderr shown to Claude), on any of: gh/network error, no comment carries both
markers together, open findings with no recorded disposition, an incomplete checkpoints marker, or
a stale marker.
Exit 0 — allow: not a `gh pr merge`, no `--auto` (or an explicit falsy value), a `--help`-only
invocation, or all checks pass.
"""
import importlib.util
import json
import os
import re
import shlex
import sys

import _hookutil


def _fail_closed(msg: str) -> None:
    """Block the merge (exit 2) with an ASCII-sanitized reason on stderr.

    Used by the crash/import guards below, which can carry arbitrary exception
    text (a path, a third-party error message) that may not be cp1252-encodable;
    Claude Code pipes hook stderr through cp1252 on Windows, so a raw non-ASCII
    byte can make the whole reason vanish. Sanitizing to ASCII keeps it visible.
    Distinct from `_advisory` (defined below): that handles main()'s statically-
    ASCII block messages and is defined *after* the sibling-dependency load this
    helper must be callable *before*. Both fail CLOSED per ADR-083 -- see that
    ADR / Decision point 3 for why --auto inverts the usual fail-open calculus
    (dev-env#717/#718).
    """
    sys.stderr.write(msg.encode("ascii", "replace").decode("ascii"))
    sys.exit(2)


# Load this gate's dependencies inside a fail-CLOSED guard. Previously the
# module-level `from _hookio import ...` and the dynamic exec_module of
# pre-merge-findings-gate.py were UNGUARDED: any import-time failure (a broken
# sibling, a missing attribute) raised before main(), the process exited 1, and
# Claude Code reads any non-2 exit as "hook allowed the tool" -- so a broken
# gate silently waved `gh pr merge --auto` through ungated, the exact fail-open
# dev-env#717/#718 close. This runs at import time on every Bash call, so a
# broken sibling now blocks all Bash *loudly* (a dev-time state CI catches
# first) rather than disabling the gate *silently*.
try:
    import _winsubp  # noqa: F401  -- suppress console windows on Windows; also future-proofs
    # the _winsubp patch if the _pmfg reuse below ever changes shape. Kept INSIDE this
    # fail-closed guard (review of PR #722) so a _winsubp import failure also blocks (exit 2)
    # rather than failing open -- it was the one sibling import previously left outside it.
    from _hookio import is_merge_help_only, mask_quoted_spans

    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _PMFG_PATH = os.path.join(_SCRIPT_DIR, "pre-merge-findings-gate.py")
    _pmfg_spec = importlib.util.spec_from_file_location("pre_merge_findings_gate", _PMFG_PATH)
    _pmfg = importlib.util.module_from_spec(_pmfg_spec)
    _pmfg_spec.loader.exec_module(_pmfg)

    is_pr_merge_command = _pmfg.is_pr_merge_command
    _parse_merge_target = _pmfg._parse_merge_target
    _MARKER_RE = _pmfg._MARKER_RE
    _DISPOSED_RE = _pmfg._DISPOSED_RE
    _fetch_pr_json = _pmfg._fetch_pr_json  # carries the MERGE_GATE_TEST_JSON test seam
except Exception as exc:  # noqa: BLE001 -- fail CLOSED on ANY dependency-load failure (#717/#718)
    _fail_closed(
        f"[auto-merge-gate] BLOCKED: the --auto checkpoint gate could not load its "
        f"dependencies ({type(exc).__name__}: {exc}). Failing closed per ADR-083: --auto "
        f"removes every other in-session backstop, so a gate that cannot load must block "
        f"rather than wave the merge through. To proceed: drop --auto and run a plain "
        f"`gh pr merge` after CI is green (this gate does not touch plain merges).\n"
    )

# Falsy values for --auto=<value>, mirroring is_mutating_gh_segment's --delete-branch=false
# handling in pre-tool-use-canonical-mutate-guard.py.
_FALSY_VALUES = {"false", "0", "no"}

_CHECKPOINTS_RE = re.compile(
    r"<!--\s*premerge-checkpoints:\s*adr_warrant\s*=\s*(\S+?)\s+doc_reconciliation\s*=\s*(\S+?)\s*-->"
)
_VALID_ADR_WARRANT = {"written", "not-warranted"}
_VALID_DOC_RECONCILIATION = {"updated", "not-applicable"}

# `gh pr view --json commits` wraps a paginated GraphQL connection (confirmed live: the
# underlying `commits(first: N)` field exposes `totalCount` like any other connection) with no
# documented page size and no `--paginate` equivalent on `gh pr view`. For a PR with more commits
# than that page's size, `commits[-1]` would silently be the last commit of a truncated page, not
# the PR's true HEAD commit -- the freshness check would then compare against the wrong date with
# no error signal. This threshold is a defensive, conservative guess (a common GraphQL default
# page size), not a confirmed exact limit; treating "at or above it" as "cannot safely trust
# commits[-1]" costs nothing in the common case (a handful of commits) and fails closed instead of
# silently trusting a possibly-wrong date in the tail case. Confirmed as a real gap during review
# (dev-env PR #588).
_COMMITS_PAGE_SIZE_SUSPECT = 100


def _merge_tail(command):
    """The `gh pr merge` statement's own tail, up to the next shell separator.

    Mirrors _parse_merge_target's own tail-extraction so --auto detection is scoped to the same
    statement pre-merge-findings-gate.py resolves its ref/repo from, not the whole (possibly
    chained) command string.

    Bounded against a mask_quoted_spans-masked copy first (dev-env#660, ADR-050 Amendment 20),
    mirroring the identical fix now applied to _parse_merge_target itself: a &&/||/;/\n that only
    appears inside a quoted --subject/--body value (e.g. `--subject "part1 && part2" --auto`) was
    mistaken for the real end of the invocation, truncating away a real --auto that comes after
    it. Confirmed NOT a live gate bypass -- truncating inside an open quote always leaves the
    naive slice with an unbalanced quote count, which wants_auto_merge's own shlex.split() already
    rejects via its `except ValueError: return True` fail-closed fallback (dev-env PR #588) -- so
    this was reached (correctly, if accidentally) via that fallback rather than via a correct
    parse. Fixed here for consistency with the fix now applied to the other three sibling sites in
    the same amendment.
    """
    m = re.search(r"gh\s+pr\s+merge\b(.*)", command, re.DOTALL)
    if not m:
        return ""
    tail = m.group(1)
    boundary = len(re.split(r"&&|\|\||;|\n", mask_quoted_spans(tail))[0])
    return tail[:boundary]


def wants_auto_merge(command):
    """True iff the gh pr merge statement requests --auto (not an explicit falsy value).

    Tokenizes the merge tail with shlex rather than a raw whitespace-bounded regex, so a
    quoted flag (`gh pr merge "--auto"` -- the shell strips the quotes before `gh` ever sees
    argv, so this is a real --auto request) is correctly recognized, and so `--auto` merely
    appearing as prose inside an unrelated flag's value (e.g. `--body "please --auto merge
    this"`) is correctly NOT recognized. Both were confirmed live as real gaps in an earlier,
    plain-regex version of this function during review (dev-env PR #588) -- the regex had no
    quote-awareness, unlike shlex or this codebase's own `_hookio.split_top_level` engine used
    elsewhere for the same class of problem. shlex also strips quotes from a token's value as
    part of tokenizing, so `--auto='false'` already arrives as `--auto=false` with no separate
    manual strip needed.

    A tail that fails to tokenize at all (e.g. an unterminated quote) is treated as wanting
    --auto defensively: safer to over-apply the stricter gate to an unparseable command than
    to silently let a real --auto slip through ungated.
    """
    try:
        tokens = shlex.split(_merge_tail(command))
    except ValueError:
        return True
    for tok in tokens:
        if tok == "--auto":
            return True
        if tok.startswith("--auto="):
            return tok.split("=", 1)[1].strip().lower() not in _FALSY_VALUES
    return False


def _advisory(msg):
    """Block the merge (exit 2). Unlike the sibling gate's _advisory, this ALWAYS fails closed --
    see the module docstring / ADR-083 Decision point 3 for why the fail-open/fail-closed calculus
    inverts for --auto specifically.
    """
    sys.stderr.write(msg)
    sys.exit(2)


def _is_stale(comment_created_at, head_committed_at):
    """True iff the qualifying comment predates the PR's current head commit.

    Both timestamps are ISO-8601 UTC strings with a 'Z' suffix (gh's own format for
    `createdAt`/`committedDate`, confirmed live against dev-env PR #572 during ADR-083's design) --
    same-format, zero-padded, fixed-width strings compare correctly with plain string comparison,
    no datetime parsing needed.
    """
    return head_committed_at > comment_created_at


def _last_match(pattern, text):
    """Like pattern.search(text) but returns the LAST match in text, not the first.

    A comment can quote an earlier, stale marker for context (e.g. "the old review said
    <!-- review-findings: blocking=5 ... -->, now fixed: <!-- review-findings: blocking=0
    ... -->") before its own real, current marker later in the same body. `.search()` binds
    to the first occurrence, which would silently pick up the quoted-for-context stale value
    instead of the comment's actual final one. Confirmed as a real (if inherited from the
    sibling gate's identical `.search()`-per-comment pattern) gap during review (dev-env PR
    #588); fixed here specifically because this hook's fail-closed, no-override design raises
    the stakes of the same latent pattern well above the sibling's fail-open one.
    """
    last = None
    for m in pattern.finditer(text):
        last = m
    return last


def _qualifying_comment(comments):
    """The single most recent comment carrying BOTH markers together, or None.

    Deliberately not two independent last-comment searches: a fresh clean review paired with a
    stale/absent second marker from an earlier, now-outdated review must not pass. Searches each
    marker independently within a comment's body (order between the two markers within a comment
    does not matter -- only that both appear in the same comment), and takes the last comment
    (by array order, i.e. most recent) where both are found.
    """
    best = None
    for c in comments:
        body = c.get("body", "") or ""
        mk = _last_match(_MARKER_RE, body)
        ck = _last_match(_CHECKPOINTS_RE, body)
        if mk and ck:
            best = (c, mk, ck)
    return best


def main() -> None:
    _hookutil.record_heartbeat("pre-auto-merge-checkpoint-gate")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    command = data.get("tool_input", {}).get("command", "")

    if not is_pr_merge_command(command):
        sys.exit(0)
    if not wants_auto_merge(command):
        sys.exit(0)  # plain merge, or an explicit --auto=false/0/no -- the sibling gate still applies
    if is_merge_help_only(command):
        sys.exit(0)

    cwd = data.get("cwd", "") or None
    ref, repo = _parse_merge_target(command)

    view_cmd = ["gh", "pr", "view"]
    if ref:
        view_cmd.append(ref)
    if repo:
        view_cmd += ["--repo", repo]
    view_cmd += ["--json", "comments,body,number,commits"]

    pr = _fetch_pr_json(view_cmd, cwd)
    if pr is None:
        _advisory(
            "[auto-merge-gate] BLOCKED: could not verify premerge checkpoints (gh/network error "
            "or unparseable output).\n\n"
            "Unlike the plain-merge gate, this gate fails CLOSED: --auto removes every other "
            "in-session backstop once it succeeds, so a transient gh/network problem must not "
            "become a free pass (ADR-031 2026-07-04 addendum; ADR-083).\n\n"
            "To proceed: retry once gh/network is healthy, or drop --auto and run a plain "
            "`gh pr merge` after CI is green (unaffected by this gate).\n"
        )

    comments = pr.get("comments", []) or []
    body = pr.get("body", "") or ""
    commits = pr.get("commits", []) or []
    num = pr.get("number", ref or "current branch")

    found = _qualifying_comment(comments)
    if found is None:
        _advisory(
            f"[auto-merge-gate] BLOCKED: PR #{num} has no single comment carrying both the "
            f"`review-findings` and `premerge-checkpoints` markers together.\n\n"
            f"To proceed:\n"
            f"  1. Run `/review <PR-URL> --post-comment` (emits both markers together), or\n"
            f"  2. Drop --auto and run a plain `gh pr merge` after CI is green.\n"
        )

    comment, mk, ck = found
    blocking, non_blocking = int(mk.group(1)), int(mk.group(2))
    adr_warrant, doc_reconciliation = ck.group(1), ck.group(2)

    if blocking + non_blocking > 0 and not _DISPOSED_RE.search(body):
        _advisory(
            f"[auto-merge-gate] BLOCKED: PR #{num}'s qualifying review comment has {blocking} "
            f"blocking + {non_blocking} non-blocking finding(s), and the PR body records no "
            f"disposition.\n\n"
            f"To proceed: add a \"Review findings disposition\" section (or the "
            f"<!-- findings-disposed --> sentinel) to the PR body per ADR-028, or drop --auto.\n"
        )

    if adr_warrant not in _VALID_ADR_WARRANT or doc_reconciliation not in _VALID_DOC_RECONCILIATION:
        _advisory(
            f"[auto-merge-gate] BLOCKED: PR #{num}'s premerge-checkpoints marker is incomplete "
            f"(adr_warrant={adr_warrant!r}, doc_reconciliation={doc_reconciliation!r}).\n\n"
            f"To proceed: resolve the gap (write the missing ADR, or add the missing "
            f"README/REFERENCE.md update) and re-run `/review <PR-URL> --post-comment`, or drop "
            f"--auto.\n"
        )

    if not commits:
        _advisory(
            f"[auto-merge-gate] BLOCKED: could not read PR #{num}'s head commit to check marker "
            f"freshness.\n\nTo proceed: drop --auto and run a plain `gh pr merge`.\n"
        )
    if len(commits) >= _COMMITS_PAGE_SIZE_SUSPECT:
        _advisory(
            f"[auto-merge-gate] BLOCKED: PR #{num} has {len(commits)} commits in the fetched "
            f"page -- at or above a page size where `commits[-1]` can no longer be trusted as "
            f"the true head commit (gh pr view's commits field is a paginated connection with "
            f"no documented limit).\n\n"
            f"To proceed: drop --auto and run a plain `gh pr merge`.\n"
        )
    head_committed_at = commits[-1].get("committedDate")
    comment_created_at = comment.get("createdAt")
    if not head_committed_at or not comment_created_at:
        _advisory(
            f"[auto-merge-gate] BLOCKED: missing timestamp data for PR #{num}'s freshness "
            f"check.\n\nTo proceed: drop --auto and run a plain `gh pr merge`.\n"
        )
    if _is_stale(comment_created_at, head_committed_at):
        _advisory(
            f"[auto-merge-gate] BLOCKED: PR #{num}'s qualifying review comment "
            f"({comment_created_at}) predates its current head commit ({head_committed_at}) -- "
            f"the review is stale.\n\n"
            f"To proceed: re-run `/review <PR-URL> --post-comment` against the current head, or "
            f"drop --auto.\n"
        )

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise  # preserve main()'s own exit 0 (allow) / exit 2 (block) verdicts -- never reclassify
    except Exception as exc:  # noqa: BLE001 -- fail CLOSED on ANY unexpected crash (#717/#718)
        _fail_closed(
            f"[auto-merge-gate] BLOCKED: the --auto checkpoint gate crashed while "
            f"evaluating this merge ({type(exc).__name__}: {exc}). Failing closed per "
            f"ADR-083. To proceed: drop --auto and run a plain `gh pr merge` after CI is "
            f"green, or re-run once the gate is fixed.\n"
        )
