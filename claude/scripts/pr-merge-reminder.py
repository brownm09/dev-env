#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh pr create', 'gh pr merge', or
'git push' (when the pushed branch has an open PR) in Bash commands and emits
journal-update reminders via stderr (exit code 2) so Claude sees them.

Matches only actual CLI invocations, not the string appearing inside commit
messages, heredocs, or other quoted arguments.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "...", "exitCode": 0},
    "session_id": "...",
    "cwd": "..."
  }

Output is read via _hookio.read_command_output (stdout+stderr, legacy output fallback)
per ADR-049/ADR-050 — do NOT read tool_response.output directly.

Exit 0  — no relevant command detected; no action
Exit 2  — gh pr create, gh pr merge, or git push (open PR) detected;
          reminder(s) emitted via stderr
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable

from _hookio import output_has_merge_marker, read_command_output

# Matches the start of a statement token against `gh pr merge`, `gh pr create`,
# or `git push`.
_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")
_CREATE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+create\b")
_PUSH_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?git\s+push\b")

# Path fragment that identifies the engineering-journal repo — push events
# there are handled by stub-push-archive-reminder.py, not this script.
_EJ_REPO_FRAGMENT = "engineering-journal"

# A `cd <path> &&` (or `;`) prefix chains a later `git push` into <path>'s repo,
# not cwd's.  Captures the directory token (quoted or bare) so the open-PR lookup
# can be scoped to the repo the push actually targets — the cross-repo
# false-positive fixed in issue #442 / ADR-065.
_CD_CHAIN_RE = re.compile(r"""cd\s+("[^"]+"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)""")
# Bare `git push` locator — bounds the cd search to the region *before* the push.
# Distinct from _PUSH_RE (which optionally eats a cd prefix and would hide it).
_BARE_PUSH_RE = re.compile(r"\bgit\s+push\b")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_RE.match(token.lstrip()))


def _check_create_stmt(token: str) -> bool:
    return bool(_CREATE_RE.match(token.lstrip()))


def _check_push_stmt(token: str) -> bool:
    return bool(_PUSH_RE.match(token.lstrip()))


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


def _scan_top_level(command: str, check_fn: Callable[[str], bool]) -> bool:
    """Return True when *command* contains a top-level statement matched by
    *check_fn* — i.e. not inside a quoted string, $() subshell, or heredoc body.

    Uses a stack-based parser with four states ('top', 'single', 'double',
    'subshell') so that shell operators buried inside quoted arguments, command
    substitutions, or heredoc content are never mistaken for top-level statement
    separators.  Specifically handles:
    - Single/double quotes
    - $() subshells (including $() inside "…")
    - <<DELIM / <<'DELIM' heredoc bodies
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


def is_pr_merge_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr merge`."""
    return _scan_top_level(command, _check_merge_stmt)


def is_pr_create_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr create`."""
    return _scan_top_level(command, _check_create_stmt)


def is_git_push_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `git push`."""
    return _scan_top_level(command, _check_push_stmt)


def _open_pr_for_cwd(cwd: str) -> dict | None:
    """Return the first open PR for the current branch in *cwd*, or None.

    Returns a dict with keys ``number``, ``url``, and ``title``.
    Returns None when *cwd* is the engineering-journal repo (handled by
    stub-push-archive-reminder.py) or when no open PR exists.

    Note: uses ``git branch --show-current`` (the checked-out branch), not the
    push refspec.  Correct for the common case of ``git push`` with no explicit
    refspec; may miss or mismatch on ``git push origin other:target`` patterns.
    """
    if _EJ_REPO_FRAGMENT in cwd.replace("\\", "/"):
        return None
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        branch = branch_result.stdout.strip()
        if not branch:
            return None
        pr_result = subprocess.run(
            ["gh", "pr", "list", "--head", branch,
             "--json", "number,url,title", "--state", "open", "--limit", "1"],
            capture_output=True, text=True, timeout=10, cwd=cwd,
        )
        if pr_result.returncode != 0:
            return None
        prs = json.loads(pr_result.stdout or "[]")
        return prs[0] if prs else None
    except Exception:
        return None


def _effective_push_dir(command: str, cwd: str) -> str:
    """Best-effort directory a top-level ``git push`` in *command* runs in.

    When a ``cd <path> &&`` (or ``;``) prefix chains into the push — e.g.
    ``cd /other/repo && git push`` — return <path> (resolved against *cwd* when
    relative), so the open-PR lookup is scoped to the repo the push actually
    targets rather than the session cwd.  A bare ``git push`` returns *cwd*.

    Conservative by design: any shape it cannot parse confidently (no governing
    ``cd``, the push hidden behind quoting, etc.) falls back to *cwd*, so the
    worst case is the pre-#442 behavior — never a wrong-repo positive — and a
    mis-resolved directory simply yields no open PR downstream (a silent no-op).
    """
    push = _BARE_PUSH_RE.search(command)
    region = command[: push.start()] if push else command
    target = None
    for m in _CD_CHAIN_RE.finditer(region):
        target = m.group(1)  # the last cd before the push is the one that governs it
    if not target:
        return cwd
    path = target.strip("\"'")
    if not os.path.isabs(path):
        # A relative target resolves against cwd, not against any earlier `cd` in
        # the same chain (`cd /a && cd b && git push` -> cwd/b, not /a/b).  That
        # mis-resolve is a downstream silent no-op (no such dir -> no open PR),
        # never a wrong-repo positive — a documented ADR-065 limit.
        path = os.path.normpath(os.path.join(cwd, path))
    return path


def _create_shard_step(output: str) -> str:
    """Return the shard-writing instruction lines for a gh pr create reminder.

    When *output* contains the PR URL printed by gh pr create, includes the
    parsed PR number and URL so the session doesn't have to look them up.
    """
    pr_url_match = re.search(r"https://github\.com/\S+/pull/(\d+)", output)
    if pr_url_match:
        pr_url = pr_url_match.group(0)
        pr_number = pr_url_match.group(1)
        return (
            f"\n  3a. Write the open-PR shard for PR #{pr_number}:\n"
            f'       echo \'{{"pr":{pr_number},"url":"{pr_url}",'
            '"topic":"<H2 heading from stub>","stub":"YYYY-MM-DD_HHMMSS.stub.md",'
            '"opened":"YYYY-MM-DD"}\''
            f"\n         > sessions/<project>/open-prs/{pr_number}.json\n"
            "  3b. Stage it alongside the stub: git add sessions/<project>/open-prs/"
        )
    return (
        "\n  3a. Write the open-PR shard: sessions/<project>/open-prs/<N>.json\n"
        "       Fields: pr (int), url, topic (H2 from stub), stub (filename),"
        " opened (YYYY-MM-DD)\n"
        "  3b. Stage it alongside the stub: git add sessions/<project>/open-prs/"
    )


def _is_successful_merge_call(exit_code: int, output: str) -> bool:
    """Return True iff a gh pr merge call completed the remote merge.

    Worktree merges exit non-zero on local cleanup (e.g. 'main is already
    checked out') even when the remote merge succeeded — check the success
    marker too (issue #275 behaviour, mirrors post-merge-tile-checkpoint.py).
    """
    return exit_code == 0 or output_has_merge_marker(output)


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
    cwd = data.get("cwd", "<unknown>")
    exit_code = data.get("tool_response", {}).get("exitCode", 0)

    is_create = is_pr_create_command(command)
    is_merge = is_pr_merge_command(command)
    is_push = is_git_push_command(command)

    if not (is_create or is_merge or is_push):
        sys.exit(0)

    # gh pr merge in a worktree exits non-zero on local cleanup even when the
    # remote merge succeeded; check the output marker too.
    # gh pr create and git push are only acted on when they exit cleanly.
    output = read_command_output(data)
    if is_merge:
        if not _is_successful_merge_call(exit_code, output):
            sys.exit(0)
    elif exit_code != 0:
        sys.exit(0)

    messages = []

    if is_create:
        shard_step = _create_shard_step(output)
        messages.append(
            "[journal-reminder] gh pr create detected — write the journal stub AND"
            " open-PR shard NOW:\n"
            f"  cwd: {cwd}\n"
            "  Rationale: the stub captures session context while it's intact;\n"
            "  compaction or session corruption after this point loses it permanently.\n"
            "  1. Identify the project journal path from cwd.\n"
            "  2. Check out or create the draft branch in engineering-journal.\n"
            "  3. Write a <!-- session: <slug> --> block for this session."
            + shard_step + "\n"
            "  4. Add token comment and <!-- next-session-context --> paragraph.\n"
            '  5. git commit -m "draft: YYYY-MM-DD session N" && git push'
        )

    if is_merge:
        messages.append(
            "[journal-reminder] gh pr merge detected — update the engineering journal now:\n"
            f"  cwd: {cwd}\n"
            "  1. Identify the project journal path from cwd.\n"
            "  2. Check out or create the draft branch in engineering-journal.\n"
            "  3. Append a <!-- session: <slug> --> block documenting this PR merge.\n"
            "  4. Add token comment and <!-- next-session-context --> paragraph.\n"
            '  5. git commit -m "draft: YYYY-MM-DD session N" && git push'
        )

    if is_push and not (is_create or is_merge):
        # Scope the open-PR lookup to the repo the push actually targets: a
        # `cd <other-repo> && git push` must not fire the session cwd's reminder
        # (issue #442 / ADR-065).  Engineering-journal pushes route into the
        # _open_pr_for_cwd EJ skip once their real target dir is used.  The
        # reminder fires on every qualifying push (each carries new journalable
        # content); scoping — not dedup — is what removes the #442 cross-repo noise.
        push_dir = _effective_push_dir(command, cwd)
        pr = _open_pr_for_cwd(push_dir)
        if pr:
            messages.append(
                f"[journal-reminder] git push detected for PR #{pr['number']} — "
                f"update the engineering journal NOW:\n"
                f"  PR: {pr['url']} — {pr['title']}\n"
                f"  repo: {push_dir}\n"
                "  Check whether a stub already exists for this session:\n"
                "  - If YES: update it in place (append new content to the session block).\n"
                "  - If NO: create a new stub for this push session.\n"
                "  Document what changed and why (review findings addressed,\n"
                "  approach decisions, what was pushed).\n"
                "  1. Identify the project journal path from the repo above.\n"
                "  2. Check out the draft branch in engineering-journal.\n"
                "  3. Find today's stub for this session, or create one if absent.\n"
                "  4. Add/update token comment and <!-- next-session-context --> paragraph.\n"
                '  5. git commit -m "draft: YYYY-MM-DD session N" && git push'
            )

    if not messages:
        sys.exit(0)

    print("\n\n".join(messages), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    # Never crash the user's push/PR flow — any unexpected error exits 0.
    # SystemExit (raised by the intentional sys.exit(2) reminder path) is a
    # BaseException, not Exception, so it still propagates and exit 2 is honored.
    try:
        main()
    except Exception:
        sys.exit(0)
