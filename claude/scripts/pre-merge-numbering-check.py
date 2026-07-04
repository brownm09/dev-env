#!/usr/bin/env python3
"""Claude Code PreToolUse hook -- pre-merge numbering-collision check (dev-env#516).

CLAUDE.md's `## Testing` section and docs/adr/INDEX.md's ADR table are both
hand-numbered sequential lists that many concurrent PRs append to. Two branches
cut around the same time each pick "the next number" from their own stale
snapshot; when both merge, git accepts the second insertion as a clean 3-way
merge (it never touches the first PR's lines), silently producing a duplicate
number instead of a conflict. The bug is a *merge-time race*, not a
stale-branch problem: a branch can be perfectly self-consistent when authored
and still collide, because the collision is introduced by *other* PRs merging
first while this one sits in review (dev-env#516; see also seven ADR-INDEX.md
renumber incidents found via `git log --grep=renumber -- docs/adr/`). A "check
origin/main before opening the PR" convention does not catch this -- the only
point that is actually authoritative is immediately before merge, after a
fresh fetch. That is where this hook runs.

How it decides (mechanical, not a content judgment):
  1. Detect a top-level `gh pr merge` in the Bash command; resolve the merge
     directory via `effective_merge_dir` (ADR-067) so a `cd <repo> && gh pr
     merge` targets the right checkout.
  2. Scope to the dev-env repo only (`is_dev_env_repo` on the origin remote
     URL) -- this hook is wired globally but the two files it checks are
     dev-env-specific.
  3. `git fetch origin main`, then read three snapshots of both files: the
     merge-base, this branch's HEAD, and origin/main.
  4. A number this branch newly introduces (absent at the merge-base) that
     origin/main has *also* claimed since the branch point is a genuine
     collision -> BLOCK (exit 2) naming both colliding lines and the fix. A
     gap that this branch's own new numbers create or extend (no collision,
     just non-contiguous) is advisory only, never blocking -- it is cosmetic,
     and a legitimate item deletion can produce one. A gap that already
     existed on origin/main before this branch is never advised on, so a
     long-standing hole doesn't re-nag on every future merge.

Fails OPEN: any git/network/parse error exits 0 (with an advisory
systemMessage where relevant), so this check can never wedge a legitimate
merge on its own failure -- matching `pre-merge-findings-gate.py` and
`pre-merge-message-check.py`, its two siblings in this hook family.

Pure-helper convention (matches the rest of `claude/scripts/`): extraction,
collision-detection, gap-detection, message-formatting, and merge-command
detection (`is_pr_merge_command`, built on `_hookio.scan_top_level`) are pure
functions, unit-tested offline in `tests/test_pre_merge_numbering_check.py`
with no subprocess, network, or disk. `main()`'s git/subprocess orchestration
is additionally covered end-to-end by a handful of real-subprocess tests in
the same file (throwaway `git init` repos, no live network) -- unlike this
hook's `pre-merge-message-check.py` sibling, whose main() has no such
coverage since it never shells out to git.

Stdin JSON shape (PreToolUse): {"tool_name":"Bash","tool_input":{"command":...},"cwd":...}

Exit 2 -- block the merge (stderr shown to Claude): a genuine number collision.
Exit 0 -- allow: not a merge command, not dev-env, no collision, or any
          internal error (fail open).
"""
import _winsubp  # noqa: F401  -- suppress console windows + default UTF-8 decoding on Windows
import json
import os
import re
import subprocess
import sys

from _hookio import effective_merge_dir, is_merge_help_only, scan_top_level

_MERGE_STMT_RE = re.compile(r"gh\s+pr\s+merge\b")
_TESTING_ITEM_RE = re.compile(r'^(\d+)\.\s+\*\*')
_ADR_ROW_RE = re.compile(r'^\|\s*\[(\d+)\]\(')


def _check_merge_stmt(token):
    return bool(_MERGE_STMT_RE.match(token.lstrip()))


def is_pr_merge_command(command):
    """True iff *command* contains a top-level `gh pr merge` -- i.e. not one
    merely mentioned inside a quoted string, $() subshell, or heredoc body
    (dev-env#499). Mirrors `pr-merge-reminder.py`'s identically-named
    predicate. Unlike this hook's two older PreToolUse-merge siblings
    (`pre-merge-message-check.py`, `pre-merge-findings-gate.py`), which still
    use an unanchored regex, this one is built on the shared engine those two
    predate -- dev-env#519 tracks migrating them for consistency.
    """
    return scan_top_level(command, _check_merge_stmt)


def extract_section(text, heading):
    """Return the body of `## {heading}` up to (not incl.) the next `## ` line.

    Empty string if the heading is absent. Matching is exact ("## Testing"),
    so a differently-cased or differently-worded heading is treated as absent
    rather than guessed at.
    """
    lines = text.splitlines()
    start = None
    target = f"## {heading}"
    for i, line in enumerate(lines):
        if line.strip() == target:
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start:end])


def extract_testing_numbers(claude_md_text):
    """CLAUDE.md's `## Testing` section -> {item number: item's first line}.

    Only matches an unindented `N. **` line -- a top-level numbered list item
    in this file's established format. Indented lines (code blocks, nested
    prose inside an item) never match, so they cannot be mistaken for a
    sibling item.
    """
    section = extract_section(claude_md_text, "Testing")
    items = {}
    for line in section.splitlines():
        m = _TESTING_ITEM_RE.match(line)
        if m:
            items[int(m.group(1))] = line.strip()
    return items


def extract_adr_numbers(index_md_text):
    """docs/adr/INDEX.md's ADR table -> {ADR number: table row}.

    Matches `| [NNN](...` rows only -- the header (`| # | Title | ... |`) and
    separator (`|---|...`) rows have no `[`, so neither is mistaken for an
    ADR entry.
    """
    items = {}
    for line in index_md_text.splitlines():
        m = _ADR_ROW_RE.match(line)
        if m:
            items[int(m.group(1))] = line.strip()
    return items


def is_dev_env_repo(remote_url):
    """True if *remote_url* (origin, any protocol) points at brownm09/dev-env."""
    if not remote_url:
        return False
    return bool(re.search(r'[:/]brownm09/dev-env(\.git)?/?\s*$', remote_url.strip(), re.IGNORECASE))


def find_new_collisions(base_numbers, branch_numbers, main_numbers):
    """{number: (branch_line, main_line)} for numbers this branch newly claims
    (absent at the merge-base) that origin/main has also claimed.

    A number present in *base_numbers* is excluded even if its text differs
    between branch and main -- that is an edit to an existing item, not a
    fresh claim, and is never a collision.
    """
    branch_new = set(branch_numbers) - set(base_numbers)
    colliding = branch_new & set(main_numbers)
    return {n: (branch_numbers[n], main_numbers[n]) for n in colliding}


def find_gaps(numbers):
    """Missing numbers in [1, max(numbers)]. Empty input -> no gaps."""
    if not numbers:
        return []
    hi = max(numbers)
    return [n for n in range(1, hi + 1) if n not in numbers]


def find_new_gaps(main_numbers, branch_new):
    """Gaps this branch's own new numbers create or extend -- excludes any
    gap that already existed in *main_numbers* on its own.

    Without this exclusion, a long-standing legitimate gap already on
    origin/main (e.g. a retired item) would re-advise on every future merge
    forever, training the operator to ignore the advisory. Only a gap that
    is new *because of* this branch's additions is worth surfacing here.
    """
    pre_existing = set(find_gaps(set(main_numbers)))
    projected = set(find_gaps(set(main_numbers) | set(branch_new)))
    return sorted(projected - pre_existing)


def format_block_message(findings):
    """{label: {number: (branch_line, main_line)}} -> the exit-2 stderr message."""
    lines = ["[numbering-check] BLOCKED: this merge would create a duplicate number.", ""]
    for label, collisions in findings.items():
        lines.append(f"{label}:")
        for n in sorted(collisions):
            branch_line, main_line = collisions[n]
            lines.append(f"  #{n} claimed by both:")
            lines.append(f"    this branch:  {branch_line}")
            lines.append(f"    origin/main:  {main_line}")
        lines.append("")
    lines.append(
        "Fix: git fetch origin main && git rebase origin/main -- then renumber the "
        "colliding item(s) above to the next free number in that file, commit, and "
        "re-run `gh pr merge`."
    )
    return "\n".join(lines)


# (repo-relative path, extractor, human label) -- the two known numbered-list
# hot spots (dev-env#516). Defined after the extractors so it can reference
# them directly.
_CHECKED_FILES = (
    ("CLAUDE.md", extract_testing_numbers, "CLAUDE.md Testing section"),
    ("docs/adr/INDEX.md", extract_adr_numbers, "docs/adr/INDEX.md ADR table"),
)


def _advisory(msg):
    print(json.dumps({"systemMessage": msg}))
    sys.exit(0)


def _run_git(args, cwd):
    """Run a git command in *cwd*; return stdout, or None on any failure."""
    try:
        result = subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=cwd, timeout=20)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def main():
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
    # evaluated for -- or blocked on -- an unrelated numbering-collision state
    # (dev-env#557).
    if is_merge_help_only(command):
        sys.exit(0)

    cwd = data.get("cwd") or os.getcwd()
    repo_dir = effective_merge_dir(command, cwd)

    remote_url = _run_git(["remote", "get-url", "origin"], repo_dir)
    if not is_dev_env_repo(remote_url):
        sys.exit(0)

    if _run_git(["fetch", "origin", "+main:refs/remotes/origin/main"], repo_dir) is None:
        _advisory(
            "[numbering-check] Could not fetch origin/main (offline?). Merge allowed -- "
            "manually confirm CLAUDE.md's Testing section and docs/adr/INDEX.md don't "
            "collide with origin/main before merging."
        )

    merge_base = _run_git(["merge-base", "HEAD", "origin/main"], repo_dir)
    if merge_base is None:
        sys.exit(0)  # can't determine ancestry -- fail open
    merge_base = merge_base.strip()

    findings = {}
    gap_notes = []
    for path, extractor, label in _CHECKED_FILES:
        base_text = _run_git(["show", f"{merge_base}:{path}"], repo_dir) or ""
        branch_text = _run_git(["show", f"HEAD:{path}"], repo_dir) or ""
        main_text = _run_git(["show", f"origin/main:{path}"], repo_dir)
        if main_text is None:
            # A failed read must never be treated as "main claims no
            # numbers" -- that would silently defeat the one check this
            # file exists to make. Skip visibly instead of passing silently.
            gap_notes.append(f"{label}: could not read origin/main -- collision check skipped for this file")
            continue

        base_numbers = extractor(base_text)
        branch_numbers = extractor(branch_text)
        main_numbers = extractor(main_text)

        collisions = find_new_collisions(base_numbers, branch_numbers, main_numbers)
        if collisions:
            findings[label] = collisions
            continue

        branch_new = set(branch_numbers) - set(base_numbers)
        gaps = find_new_gaps(main_numbers, branch_new)
        if gaps:
            gap_notes.append(f"{label}: gap at {', '.join(str(n) for n in gaps)}")

    if findings:
        sys.stderr.write(format_block_message(findings))
        sys.stderr.write("\n")
        sys.exit(2)

    if gap_notes:
        _advisory(
            "[numbering-check] No collision, but a sequencing gap was found: "
            + "; ".join(gap_notes) + ". Not blocking -- fix whenever convenient."
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
