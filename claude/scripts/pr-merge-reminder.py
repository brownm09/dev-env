#!/usr/bin/env python3
"""Claude Code PostToolUse hook — detects 'gh pr create', 'gh pr merge', or
'git push' (when the pushed branch has an open PR) in Bash commands and emits
journal-update reminders via stderr (exit code 2) so Claude sees them.

Matches only actual CLI invocations, not the string appearing inside commit
messages, heredocs, or other quoted arguments.

Also fires for the PowerShell tool (dev-env#763): registered under both the
Bash and PowerShell PostToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `gh pr create`/`gh pr merge`/`git push` in
this environment.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",  # or "PowerShell"
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

from _hookio import (
    confirm_merge_via_gh,
    effective_merge_dir,
    is_absolute_path,
    is_merge_help_only,
    output_has_merge_marker,
    read_command_output,
    scan_top_level,
    should_confirm_via_gh,
)
import _hookutil
from _repo_target import create_args, merge_args, repo_from_flag

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


def is_pr_merge_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr merge`."""
    return scan_top_level(command, _check_merge_stmt)


def is_pr_create_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `gh pr create`."""
    return scan_top_level(command, _check_create_stmt)


def is_git_push_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `git push`."""
    return scan_top_level(command, _check_push_stmt)


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
    if not is_absolute_path(path):
        # A relative target resolves against cwd, not against any earlier `cd` in
        # the same chain (`cd /a && cd b && git push` -> cwd/b, not /a/b).  That
        # mis-resolve is a downstream silent no-op (no such dir -> no open PR),
        # never a wrong-repo positive — a documented ADR-065 limit.
        path = os.path.normpath(os.path.join(cwd, path))
    return path


def _effective_merge_repo(command: str, cwd: str) -> str:
    """Best-effort repo label for a top-level ``gh pr merge`` in *command*.

    An explicit ``--repo``/``-R owner/repo`` flag takes precedence over cwd and
    any ``cd``-chain prefix — e.g. ``gh pr merge 110 --repo other/repo`` run
    from an unrelated cwd reports ``other/repo``, not the session directory
    (dev-env#470; ``-R`` shorthand added in dev-env#616). Falls back to
    ``effective_merge_dir(command, cwd)`` (the cd-chain / cwd resolution,
    ADR-067) when no ``--repo``/``-R`` flag is present.

    The flag search is scoped to the ``gh pr merge`` invocation's own args
    (``merge_args``) — the shared, statement-scoped resolver (ADR-111) — so a
    chained sibling ``gh pr create --repo X`` statement's flag can no longer
    cross-contaminate this one (dev-env#667/#482 Gap 1: whichever ``--repo``
    appeared textually first in the whole masked command previously won for
    both functions, regardless of which statement it belonged to).
    ``repo_from_flag`` masks the args with ``mask_quoted_spans`` internally
    (dev-env#626, ADR-050 Amendment 15), so a ``--subject``/``--body`` decoy
    can't false-match.
    """
    args = merge_args(command)
    if args is not None:
        flag_repo = repo_from_flag(args)
        if flag_repo:
            return flag_repo
    return effective_merge_dir(command, cwd)


def _effective_create_repo(command: str, cwd: str) -> str:
    """Best-effort repo label for a top-level ``gh pr create`` in *command*.

    An explicit ``--repo``/``-R owner/repo`` flag takes precedence over cwd —
    e.g. ``gh pr create --repo other/repo`` run from an unrelated cwd reports
    ``other/repo``, not the session directory (dev-env#646). Falls back to
    *cwd* when no ``--repo``/``-R`` flag is present — mirroring
    ``_effective_merge_repo``'s flag-first precedence (dev-env#470/#616), but
    unlike that function there is no cd-chain-aware dir to fall back to here:
    the ``is_create`` message branch has only ever reported cwd, so an
    unflagged create command's reminder is unchanged by this addition.

    The flag search is scoped to the ``gh pr create`` invocation's own args
    (``create_args``, ADR-111), so a chained sibling ``gh pr merge --repo Y``
    statement's flag can no longer cross-contaminate this one (dev-env#667).
    """
    args = create_args(command)
    if args is not None:
        flag_repo = repo_from_flag(args)
        if flag_repo:
            return flag_repo
    return cwd


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


def _is_successful_merge_call(output: str) -> bool:
    """Return True iff a gh pr merge call completed the remote merge.

    Gated on gh's success marker alone, not the exit code: a worktree merge
    exits non-zero on local cleanup ('main is already checked out') even when
    the remote merge succeeded (issue #275), while a clean exit 0 is also true
    for non-merge invocations like `gh pr merge --help` or a queued `--auto` —
    neither of which the caller's `is_pr_merge_command` command-shape check
    filters out. Mirrors merge_succeeded() in post-pr-merge-project.py and
    merge_confirmed() in usage-snapshot.py (dev-env#485).
    """
    return output_has_merge_marker(output)


def _build_messages(
    command: str,
    cwd: str,
    exit_code: int,
    output: str,
    is_create: bool,
    is_merge: bool,
    is_push: bool,
    live_confirmed: bool | None = None,
) -> list[str]:
    """Build the reminder message(s) for a detected create/merge/push command.

    Each message is gated on its own success condition rather than a shared
    early exit, so a chained command matching more than one of is_create /
    is_merge / is_push (e.g. `gh pr create --fill && gh pr merge --auto`)
    still gets the reminder for whichever half actually succeeded — an
    incomplete merge sub-check (no success marker: a queued --auto, or
    --help) must not suppress an otherwise-legitimate create reminder in the
    same command (dev-env#494).

    gh pr merge is confirmed via the output marker alone, not the exit code —
    a worktree exits non-zero on local cleanup despite a real merge (#275),
    while a clean exit 0 does NOT mean a merge happened (--help, a queued
    --auto).

    gh pr create and git push are gated on `exit_code == 0 or merge_ok` — NOT
    on `is_merge` alone (a static text match, true even when `&&` short-
    circuited before merge ever ran: `gh pr create --fill && gh pr merge
    --auto` with create itself failing has is_merge True and merge_ok False,
    so this gate correctly stays False, matching the pre-#494-fix behavior of
    suppressing everything). A *confirmed* merge (merge_ok True) is
    independent proof create already succeeded — merge cannot complete
    against a PR that was never opened — so it counts as evidence for create
    even when the chain's aggregate exit code is non-zero (the #275 worktree
    case, chained with a preceding create).

    *live_confirmed* — dev-env#504: gh's marker does not always survive to
    this hook's captured output when gh exits abruptly right after a
    worktree's local-cleanup failure (dev-env#489). `main()` resolves the
    live `gh pr view` fallback (a subprocess call) itself, BEFORE calling this
    function, and passes the result here — this function never shells out.
    `None` (the default) means main() never attempted the live check (marker
    already found, or exit_code/output didn't warrant it) and `merge_ok` keeps
    its marker-only value, so every existing caller/test that omits this
    parameter is unaffected. `True`/`False` means main() did attempt it and
    authoritatively overrides `merge_ok`.
    """
    merge_ok = is_merge and _is_successful_merge_call(output)
    if live_confirmed is not None:
        merge_ok = live_confirmed
    create_push_ok = exit_code == 0 or merge_ok
    messages = []

    if is_create and create_push_ok:
        shard_step = _create_shard_step(output)
        create_repo = _effective_create_repo(command, cwd)
        messages.append(
            "[journal-reminder] gh pr create detected — write the journal stub AND"
            " open-PR shard NOW:\n"
            f"  cwd: {cwd}\n"
            f"  repo: {create_repo}\n"
            "  Rationale: the stub captures session context while it's intact;\n"
            "  compaction or session corruption after this point loses it permanently.\n"
            "  1. Identify the project journal path from the repo above.\n"
            "  2. Check out or create the draft branch in engineering-journal.\n"
            "  3. Write the session block for this session — start directly with a"
            " `## Session: YYYY-MM-DD HH:MM — <topic>` heading (no header marker needed)."
            + shard_step + "\n"
            "  4. Add token comment and <!-- next-session-context --> paragraph.\n"
            "  5. git add the stub, manifest shard, and open-PR shard written above.\n"
            '  6. git commit -m "draft: YYYY-MM-DD session N" -- <those same files>'
            " && git push"
        )

    if merge_ok:
        merge_dir = _effective_merge_repo(command, cwd)
        messages.append(
            "[journal-reminder] gh pr merge detected — update the engineering journal now:\n"
            f"  cwd: {cwd}\n"
            f"  repo: {merge_dir}\n"
            "  1. Identify the project journal path from the repo above.\n"
            "  2. Check out or create the draft branch in engineering-journal.\n"
            "  3. Update this session's stub with the merge details (or write a new"
            " stub if this is a fresh session).\n"
            "  4. Add token comment and <!-- next-session-context --> paragraph.\n"
            "  5. git add the updated stub (and manifest shard, if this session's own).\n"
            '  6. git commit -m "draft: YYYY-MM-DD session N" -- <those same files>'
            " && git push"
        )

    if is_push and not (is_create or is_merge) and create_push_ok:
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
                "  5. git add the stub (and manifest shard, if this session's own).\n"
                '  6. git commit -m "draft: YYYY-MM-DD session N" -- <those same files>'
                " && git push"
            )

    return messages


def main() -> None:
    _hookutil.record_heartbeat("pr-merge-reminder")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    cwd = data.get("cwd", "<unknown>")
    exit_code = data.get("tool_response", {}).get("exitCode", 0)

    is_create = is_pr_create_command(command)
    is_merge = is_pr_merge_command(command)
    is_push = is_git_push_command(command)

    if not (is_create or is_merge or is_push):
        sys.exit(0)

    output = read_command_output(data)

    # dev-env#504: when the marker-based check fails but the command was a
    # genuine gh pr merge with a non-zero exit code, fall back to a live
    # `gh pr view` confirmation (dev-env#489) before conceding no merge
    # happened. Resolved here, not inside _build_messages, so that function's
    # existing direct-call test suite can never trigger a live subprocess.
    #
    # `gh pr merge --help` (or any other non-mutating gh pr merge invocation
    # that prints no marker) can categorically never attempt a real merge —
    # excluded here so it is treated exactly like "not a merge command at
    # all" rather than paying a live gh pr view confirmation that resolves
    # against cwd's current branch and can misattribute an unrelated
    # already-merged PR (dev-env#557).
    live_confirmed = None
    if (
        is_merge
        and not _is_successful_merge_call(output)
        and should_confirm_via_gh(exit_code, output)
        and not is_merge_help_only(command)
    ):
        live_confirmed = (
            confirm_merge_via_gh(None, "", effective_merge_dir(command, cwd)) is not None
        )

    messages = _build_messages(
        command, cwd, exit_code, output, is_create, is_merge, is_push,
        live_confirmed=live_confirmed,
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
