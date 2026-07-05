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
import _winsubp  # noqa: F401  -- suppress console windows on Windows; also future-proofs the
                                 # _winsubp patch in case the _pmfg reuse below ever changes shape
import importlib.util
import json
import os
import re
import sys

from _hookio import is_merge_help_only

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

# A standalone --auto flag token, bare or with an explicit value: --auto, --auto=true, --auto=false.
# Bounded by whitespace/start/end so it can't match inside --disable-auto (a distinct, real gh pr
# merge flag that *turns off* a pending auto-merge -- always safe, never in scope here) or a
# hypothetical --auto-something.
_AUTO_FLAG_RE = re.compile(r"(?:^|\s)--auto(?:=(\S+))?(?=\s|$)")
# Falsy values for --auto=<value>, mirroring is_mutating_gh_segment's --delete-branch=false
# handling in pre-tool-use-canonical-mutate-guard.py verbatim, plus quote-stripping (--auto='false')
# that precedent doesn't do.
_FALSY_VALUES = {"false", "0", "no"}

_CHECKPOINTS_RE = re.compile(
    r"<!--\s*premerge-checkpoints:\s*adr_warrant\s*=\s*(\S+?)\s+doc_reconciliation\s*=\s*(\S+?)\s*-->"
)
_VALID_ADR_WARRANT = {"written", "not-warranted"}
_VALID_DOC_RECONCILIATION = {"updated", "not-applicable"}


def _merge_tail(command):
    """The `gh pr merge` statement's own tail, up to the next shell separator.

    Mirrors _parse_merge_target's own tail-extraction so --auto detection is scoped to the same
    statement pre-merge-findings-gate.py resolves its ref/repo from, not the whole (possibly
    chained) command string.
    """
    m = re.search(r"gh\s+pr\s+merge\b(.*)", command, re.DOTALL)
    if not m:
        return ""
    return re.split(r"&&|\|\||;|\n", m.group(1))[0]


def wants_auto_merge(command):
    """True iff the gh pr merge statement requests --auto (not an explicit falsy value)."""
    m = _AUTO_FLAG_RE.search(_merge_tail(command))
    if not m:
        return False
    value = m.group(1)
    if value is None:
        return True  # bare --auto
    return value.strip().strip("'\"").lower() not in _FALSY_VALUES


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
        mk = _MARKER_RE.search(body)
        ck = _CHECKPOINTS_RE.search(body)
        if mk and ck:
            best = (c, mk, ck)
    return best


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
    main()
