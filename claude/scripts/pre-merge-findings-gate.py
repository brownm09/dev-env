#!/usr/bin/env python3
"""Claude Code PreToolUse hook — All-Findings Merge Gate enforcement (ADR-028/ADR-039).

Detects `gh pr merge` in a Bash command and blocks the merge when the PR has a
`/review` comment reporting unresolved findings and the PR body does not record
their disposition. This converts ADR-028 ("all findings — blocking and
non-blocking — must be addressed before merge") from a prose rule into a
mechanical gate, so a session cannot silently merge while leaving findings
"as-is".

How it decides (mechanical, not a content judgment):
  1. Find the merge target PR (positional ref / --repo, else current branch).
  2. `gh pr view --json comments,body`.
  3. Among comments, take the LAST one carrying the machine marker the /review
     skill emits:  <!-- review-findings: blocking=N non_blocking=M -->
  4. If no such marker → exit 0 (PR not reviewed by /review; out of scope here).
     If N+M == 0 → exit 0 (clean review).
     If N+M > 0 and the PR body records a disposition (a "Review findings
     disposition" section, or a <!-- findings-disposed --> sentinel) → exit 0.
     Otherwise → exit 2 (BLOCK) with the counts and the fix-or-file instruction.

Design limitation (documented in ADR-039): the gate verifies that a conscious
disposition step happened, not that each individual finding was genuinely fixed
or filed. It removes the silent-merge autopilot; it is not a proof of closure.

Fails OPEN: any error resolving or fetching the PR exits 0 with an advisory
systemMessage, so a transient `gh`/network problem never wedges a legitimate
merge. The gate is one layer; CLAUDE.md and the reviewer remain responsible.

Merge detection is built on `_hookio.scan_top_level` (dev-env#519), the same
quote/subshell/heredoc-aware engine `pre-merge-numbering-check.py` and
`pr-merge-reminder.py` already use — not a plain unanchored `re.search` over
the whole command string, which could spuriously fire on a `gh pr merge`
mentioned only inside a heredoc body or `$()` subshell (dev-env#499) and pay
this hook's live `gh pr view` call for a command that never actually merges.

`is_pr_merge_command`, `_parse_merge_target`, `_MARKER_RE`, `_DISPOSED_RE`, and `_fetch_pr_json`
are also imported by `pre-auto-merge-checkpoint-gate.py` (ADR-083) via a dynamic
`importlib.util.spec_from_file_location` load, despite the leading underscore on three of
them — do not rename, remove, or change the signature/regex-group shape of any of these five
without updating that file too.

Stdin JSON shape (PreToolUse): {"tool_name":"Bash","tool_input":{"command":...},"cwd":...}

Exit 2 — block the merge (stderr shown to Claude).
Exit 0 — allow (clean review, no review marker, disposition present, or hook error).
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

import _hookout
from _hookio import is_merge_help_only, mask_quoted_spans, scan_top_level

_MERGE_STMT_RE = re.compile(r"gh\s+pr\s+merge\b")
_MARKER_RE = re.compile(
    r"<!--\s*review-findings:\s*blocking\s*=\s*(\d+)\s+non_blocking\s*=\s*(\d+)\s*-->"
)
# A recorded disposition in the PR body: the sentinel comment, or a heading/line
# mentioning "findings disposition" / "findings-disposed" (case-insensitive).
_DISPOSED_RE = re.compile(r"findings[\s\-]dispos", re.IGNORECASE)

# Flags to `gh pr merge` that consume the following token as their value.
_VALUE_FLAGS = {"--repo", "-R", "-b", "--body", "-t", "--subject", "--match-head-commit",
                "--author-email"}


def _check_merge_stmt(token):
    return bool(_MERGE_STMT_RE.match(token.lstrip()))


def is_pr_merge_command(command):
    """True iff *command* contains a top-level `gh pr merge` -- i.e. not one
    merely mentioned inside a quoted string, $() subshell, or heredoc body
    (dev-env#499). Mirrors `pre-merge-numbering-check.py`'s identically-named
    predicate (dev-env#519).
    """
    return scan_top_level(command, _check_merge_stmt)


def _parse_merge_target(command):
    """Return (ref, repo) parsed from the `gh pr merge ...` invocation.

    ref is the positional PR number/URL/branch, or None (→ current branch).
    repo is the --repo/-R value, or None.

    Tokenizes a `mask_quoted_spans`-masked copy of the tail (dev-env#634, ADR-050
    Amendment 17), not the raw text: a naive `.split()` over unmasked text treats
    a --subject/--body value like `"see -R other/repo for context"` as several
    separate whitespace tokens, so the decoy `-R` and `other/repo` inside it are
    indistinguishable from a real `--repo`/`-R` flag followed by its value
    (dev-env#626's own hijack, reached here via whitespace tokenization instead
    of an unanchored regex match). Masking first collapses the entire quoted
    span into a single contiguous run of `#` (no internal whitespace survives to
    split on), so it becomes exactly one token -- consumed whole as `--subject`'s
    own value by the `_VALUE_FLAGS` handling below, never mistaken for a
    fresh flag.

    The tail's own END boundary (the "stop at a shell separator" split below)
    is ALSO bounded against a `mask_quoted_spans`-masked copy first (dev-env#660,
    ADR-050 Amendment 20) -- Amendment 17 fixed the WITHIN-boundary tokenization
    hijack above but left this boundary-finding step itself searching raw,
    unmasked text. A `&&`/`||`/`;`/`\n` that only appears inside a quoted
    --subject/--body value (e.g. `--subject "part1 && part2" --repo o/r`) was
    mistaken for the real end of the invocation, silently dropping a REAL
    trailing flag like `--repo` that comes after it -- confirmed live, not
    speculative: `_parse_merge_target('gh pr merge 42 --subject "a && b" --repo
    o/r')` returned `('42', None)`, losing the repo entirely. Opposite failure
    direction from Amendment 17's decoy-hijack (a fake flag being matched) --
    here a REAL flag is silently lost. mask_quoted_spans is length-preserving,
    so the masked split's length slices the correct, UNMASKED prefix back out
    of the real tail before the (unchanged) tokenization step above runs.
    """
    m = re.search(r"gh\s+pr\s+merge\b(.*)", command, re.DOTALL)
    if not m:
        return None, None
    tail = m.group(1)
    # Stop at a shell separator so we don't swallow a chained command.
    boundary = len(re.split(r"&&|\|\||;|\n", mask_quoted_spans(tail))[0])
    tail = tail[:boundary]
    tokens = mask_quoted_spans(tail).split()
    ref, repo = None, None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--repo=") or tok.startswith("-R="):
            repo = tok.split("=", 1)[1]
            i += 1
            continue
        if tok in ("--repo", "-R") and i + 1 < len(tokens):
            repo = tokens[i + 1]
            i += 2
            continue
        if tok in _VALUE_FLAGS and i + 1 < len(tokens):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if ref is None:
            ref = tok
        i += 1
    return ref, repo


def _advisory(msg):
    print(json.dumps({"systemMessage": msg}))
    sys.exit(0)


def _fetch_pr_json(view_cmd, cwd):
    """Return the PR's JSON dict, or None on any failure (→ fail open).

    Test seam: when MERGE_GATE_TEST_JSON is set it bypasses `gh` entirely —
    the value "FAIL" simulates a gh error; any other value is read as a path to
    a JSON file. Never set in production; lets the behavioral self-test exercise
    every decision path without a cross-platform `gh` stub.
    """
    seam = os.environ.get("MERGE_GATE_TEST_JSON")
    if seam is not None:
        if seam == "FAIL":
            return None
        try:
            with open(seam, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    try:
        result = subprocess.run(
            view_cmd, capture_output=True, text=True, cwd=cwd, timeout=20
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


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
    # `gh pr merge --help` (or any other non-mutating gh pr merge invocation)
    # can categorically never attempt a real merge, so it must never be
    # evaluated against — or blocked on — an unrelated PR's review findings
    # (dev-env#557).
    if is_merge_help_only(command):
        sys.exit(0)

    cwd = data.get("cwd", "") or None
    ref, repo = _parse_merge_target(command)

    view_cmd = ["gh", "pr", "view"]
    if ref:
        view_cmd.append(ref)
    if repo:
        view_cmd += ["--repo", repo]
    view_cmd += ["--json", "comments,body,number"]

    pr = _fetch_pr_json(view_cmd, cwd)
    if pr is None:
        _advisory("[merge-gate] Could not verify review findings (gh/network error "
                  "or unparseable output). Merge allowed — manually confirm all "
                  "/review findings are fixed-or-filed per ADR-028.")

    comments = pr.get("comments", []) or []
    body = pr.get("body", "") or ""

    # Last comment carrying the /review machine marker wins (handles re-reviews).
    blocking = non_blocking = None
    for c in comments:
        mk = _MARKER_RE.search(c.get("body", "") or "")
        if mk:
            blocking, non_blocking = int(mk.group(1)), int(mk.group(2))
    if blocking is None:
        sys.exit(0)  # no /review marker — not in scope for this gate
    total = blocking + non_blocking
    if total == 0:
        sys.exit(0)  # clean review

    if _DISPOSED_RE.search(body):
        sys.exit(0)  # author recorded a disposition section

    num = pr.get("number", ref or "current branch")
    _hookout.emit_block(
        f"[merge-gate] BLOCKED: PR #{num} has an open /review with "
        f"{blocking} blocking + {non_blocking} non-blocking finding(s), and the PR "
        f"body records no disposition.\n\n"
        f"Per ADR-028, every finding must be either FIXED in this PR or FILED as a "
        f"tracked issue and linked - none may be left \"as-is\".\n\n"
        f"To proceed:\n"
        f"  1. For each finding in the review comment, fix it (commit) or file a "
        f"follow-up issue.\n"
        f"  2. Add a \"Review findings disposition\" section to the PR body listing "
        f"each finding's disposition (fixed in <sha> | filed #N), or add the "
        f"sentinel <!-- findings-disposed --> once all are genuinely closed.\n"
        f"  3. Re-run `gh pr merge`."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
