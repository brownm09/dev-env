#!/usr/bin/env python3
"""Claude Code PostToolUse hook — after 'gh pr merge', fast-forward the local
main branch of the affected repo so the local clone stays current.

Uses `git fetch origin main:main` to update the local main ref without requiring
a checkout — except when the repo's canonical checkout is itself on `main` (e.g.
dev-env's own canonical, which must always stay on `main` per its symlink
architecture, CLAUDE.md -> Dev-Env Architecture): git refuses that fetch
('refusing to fetch into branch ... checked out'), so a plain `pull --ff-only`
is used there instead (dev-env#488).

Also fires for the PowerShell tool (dev-env#763): registered under both the
Bash and PowerShell PostToolUse matchers in settings.json, since PowerShell is
an equally sanctioned way to run `gh pr merge` in this environment.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",  # or "PowerShell"
    "tool_input": {"command": "...", "description": "..."},
    "tool_response": {"stdout": "...", "stderr": "..."},  # NOT "output" — ADR-049
    "session_id": "...",
    "cwd": "..."
  }

Output channels (via _hookout, PR5 of dev-env#717): routine pull status is a user
systemMessage (exit 0); a "parked this worktree off main" warning goes to the model
via exit-2 stderr — the model's own cwd branch just changed underneath it and it must
know. Exit-2 on PostToolUse only feeds stderr to the model as feedback; it never
blocks Claude's work.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys

import _hookout
from _hookio import (
    confirm_merge_via_gh,
    effective_merge_dir,
    is_merge_help_only,
    is_rest_merge_command,
    mask_prose_flag_values,
    output_has_merge_marker,
    output_has_rest_merge_marker,
    read_command_output,
    scan_top_level,
    should_confirm_via_gh,
)
import _hookutil
from _repo_target import merge_args, repo_from_flag, repo_from_pr_url, repo_from_rest_merge_path
from _worktree_topology import canonical_on_main, merge_park_target, parse_worktree_porcelain

# Anchored top-level match — mirrors usage-snapshot.py / pr-merge-reminder.py /
# post-pr-merge-project.py's identical _check_merge_stmt (ADR-050 Amendments 5/6).
_MERGE_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?gh\s+pr\s+merge\b")


def _check_merge_stmt(token: str) -> bool:
    return bool(_MERGE_RE.match(token.lstrip()))


# Map GitHub repo slugs to local clone paths.
# Repos with no local clone (e.g. profile-only repos) map to None.
REPO_LOCAL_PATHS: dict[str, str | None] = {
    "brownm09/dev-env":                "C:/Users/brown/Git/dev-env",
    "brownm09/engineering-journal":    "C:/Users/brown/Git/engineering-journal",
    "brownm09/engineering-playbooks":  "C:/Users/brown/Git/engineering-playbooks",
    "brownm09/lifting-logbook":        "C:/Users/brown/Git/lifting-logbook",
    "brownm09/brownm09":               None,
    "brownm09/leadership-playbooks":   None,
}


def extract_repo(command: str, cwd: str) -> str | None:
    """Return 'owner/repo' for the merged PR, or None.

    Resolution order (ADR-067; the string-parsing steps now share ``_repo_target``,
    ADR-111):
    1. ``--repo``/``-R owner/repo`` flag — explicit, highest confidence
       (``-R`` shorthand, ``=``-or-space form, standalone-token lookbehind, and
       ``mask_quoted_spans`` decoy-blinding all live in
       ``_repo_target.repo_from_flag``). Scoped to the ``gh pr merge``
       invocation's own args (``merge_args``) so a chained sibling command's
       ``--repo`` flag can no longer leak in (dev-env#482 Gap 1) — this file's
       own unscoped whole-command search was the most exposed of the sibling
       checks to that class of false match.
    2. GitHub PR URL in the command string — e.g. ``gh pr merge
       https://github.com/owner/repo/pull/N``.  Pure parse, no subprocess.
       Checked against a `mask_prose_flag_values`-masked copy of `command`
       (dev-env#634, ADR-050 Amendment 17), so a `--subject`/`--body` value
       containing a URL-shaped decoy can no longer be mistaken for the merge's
       actual target repo — while a *bare* quoted PR URL (never preceded by
       `--subject`/`--body`) is a legitimate, already-supported shape that
       masking must not blind, and is left untouched.
    3. The two-step REST merge fallback's own path (`gh api -X PUT
       repos/<owner>/<repo>/pulls/<N>/merge`, dev-env#986) — this command
       shape always names its target repo explicitly in the path, so it is
       checked before the cd-chain/git-remote fallbacks below (mirrors how a
       PR URL is preferred over inferring from cwd).
    4. ``cd <path> && gh pr merge`` chain: run git-remote on <path> so a
       cross-repo merge correctly identifies the other repo, not cwd's repo.
    5. Bare fallback: git-remote on cwd (pre-ADR-067 behaviour — still correct
       when the merge was run directly from the target repo's cwd).
    """
    args = merge_args(command)
    flag_repo = repo_from_flag(command if args is None else args)
    if flag_repo:
        return flag_repo

    # GitHub PR URL in the command (e.g. `gh pr merge https://…/pull/N`)
    url_repo = repo_from_pr_url(mask_prose_flag_values(command))
    if url_repo:
        return url_repo

    # The REST merge fallback's own path always names its repo explicitly
    # (dev-env#986) — checked before falling back to cwd/cd-chain inference.
    rest_repo = repo_from_rest_merge_path(command)
    if rest_repo:
        return rest_repo

    # cd-chain scoping: a `cd /other/repo && gh pr merge` should query that
    # repo's remote, not cwd's (the cross-repo incident from the #442 session).
    effective_dir = effective_merge_dir(command, cwd)
    try:
        result = subprocess.run(
            ["git", "-C", effective_dir, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # https://github.com/owner/repo(.git)
            m3 = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", url)
            if m3:
                return m3.group(1)
    except Exception:
        pass

    return None


def list_worktrees(local_path: str) -> list[dict]:
    """Return `git worktree list --porcelain` for *local_path*'s repo, parsed; [] on failure.

    Shared by pull_main's on-main detection and park_worktree_off_main's squatter
    detection so a merge event runs `git worktree list` once, not twice.
    """
    try:
        wt = subprocess.run(
            ["git", "-C", local_path, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    return parse_worktree_porcelain(wt.stdout) if wt.returncode == 0 else []


def pull_command(local_path: str, on_main: bool) -> list[str]:
    """Pure: the git invocation that fast-forwards local main from origin.

    `git fetch origin main:main` fails ('refusing to fetch into branch
    "refs/heads/main" checked out at ...') whenever `main` is the branch currently
    checked out at *local_path* — always true for dev-env's canonical (CLAUDE.md ->
    Dev-Env Architecture requires it stay on `main`; its tree is symlinked into
    ~/.claude/). Use a plain `pull --ff-only` there instead. Otherwise (a feature
    branch is checked out — the case the fetch-into-ref trick was written for,
    issue #275) the fetch updates main without disturbing the current checkout,
    unchanged.
    """
    if on_main:
        return ["git", "-C", local_path, "pull", "--ff-only", "origin", "main"]
    return ["git", "-C", local_path, "fetch", "origin", "main:main"]


def format_pull_message(
    repo: str,
    kind: str,
    *,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    error: str | None = None,
) -> str:
    """Pure: the status line for a local-main fast-forward outcome.

    Split out from pull_main so the user-facing message text is testable without a
    live git call — mirrors dev-env-sync.py's formatter extraction (CLAUDE.md
    Testing item 56).
    """
    if timed_out:
        return f"[post-merge-pull] {repo}: git {kind} timed out"
    if error is not None:
        return f"[post-merge-pull] {repo}: unexpected error — {error}"
    if returncode == 0:
        parts = [p for p in (stdout.strip(), stderr.strip()) if p]
        detail = "\n".join(parts) or "already up to date"
        return f"[post-merge-pull] {repo}: local main updated — {detail}"
    err = (stderr or stdout).strip()
    return f"[post-merge-pull] {repo}: git {kind} failed — {err}"


def pull_main(local_path: str, repo: str, on_main: bool) -> str:
    """Fast-forward local main from origin; return the status line (see pull_command)."""
    cmd = pull_command(local_path, on_main)
    kind = "pull" if on_main else "fetch"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return format_pull_message(
            repo, kind,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except subprocess.TimeoutExpired:
        return format_pull_message(repo, kind, timed_out=True)
    except Exception as exc:
        return format_pull_message(repo, kind, error=str(exc))


def format_park_message(park: str, *, ok: bool = False, exc: str | None = None, detail: str = "") -> str:
    """Pure: the message when this worktree was (or couldn't be) parked off main.

    `ok` — parked successfully; `exc` — the checkout itself raised; otherwise the
    checkout exited non-zero (the branch likely already exists), with git's stderr in
    `detail`. Split out so the model-visible warning text is testable without a live
    checkout (mirrors format_pull_message / CLAUDE.md Testing item 56).
    """
    if ok:
        return (
            f"[post-merge-pull] parked this worktree off main onto {park} — freed the main ref. "
            "The canonical ~/Git/dev-env is off main; dev-env-sync returns it on the next prompt "
            "if clean (else switch it back manually to refresh ~/.claude/)."
        )
    if exc is not None:
        return f"[post-merge-pull] could not park worktree off main — {exc}"
    return (
        f"[post-merge-pull] could not park worktree off main (branch {park} may already exist) "
        f"— {detail}"
    )


def park_worktree_off_main(cwd: str, worktrees: list[dict]) -> str | None:
    """If `gh pr merge --delete-branch` left this worktree squatting main, park it off.

    gh deletes the merged local branch and checks out the default branch; from a worktree
    that checkout only succeeds when the canonical had freed the main ref (it was off main),
    so the worktree grabs main and blocks every other worktree's local post-merge checkout.
    Recreate the worktree's own claude/<slug> branch at HEAD to free main again — this acts
    on the hook's OWN just-merged session worktree (cwd), so no ADR-051 liveness check is
    needed. Non-destructive: `git checkout -b` changes no working-tree files (dev-env#396,
    ADR-058).

    `merge_park_target` is fed the *merged repo's* worktree list (shared with pull_main's
    on-main detection via `list_worktrees` — one `git worktree list` call per merge event),
    so it parks only when `cwd` is genuinely a worktree of that repo on main — a `gh pr merge
    --repo X` run from an unrelated checkout never touches that other repo (correctness review).

    Returns the message to surface (the caller routes it to the model via exit-2 stderr),
    or None when no park was needed.
    """
    if not cwd:
        return None
    park = merge_park_target(cwd, worktrees)
    if not park:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "checkout", "-b", park],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as exc:
        return format_park_message(park, exc=str(exc))
    if r.returncode == 0:
        return format_park_message(park, ok=True)
    return format_park_message(park, detail=r.stderr.strip())


def is_successful_merge(command: str, output: str) -> bool:
    """Pure predicate: did this Bash call complete a `gh pr merge`?

    Gated on gh's success marker alone, not the exit code: a worktree exits
    non-zero because local cleanup (`git checkout main`, branch delete) fails
    even though the remote merge succeeded (issue #275; mirrors
    post-pr-merge-reclaim.py), while a clean exit 0 is also true for non-merge
    invocations like `gh pr merge --help` or a queued `--auto` — an
    exit-0-alone gate misfired on exactly that shape (dev-env#485). The output
    is read via the shared `read_command_output` helper — reading the legacy
    `output` field left this fallback dead because the real payload carries
    `stdout`/`stderr` (#380).

    The command-shape check itself is `scan_top_level`-anchored rather than a
    raw substring test, so `gh pr merge` text inside a heredoc body, a quoted
    argument, or a `$()` subshell no longer counts as an invocation — matching
    the pattern already used in usage-snapshot.py / pr-merge-reminder.py /
    post-pr-merge-project.py (dev-env#529, ADR-050 Amendment 9).

    Also recognizes the two-step REST merge fallback (`gh api -X PUT
    .../pulls/<N>/merge`, dev-env#986) — see usage-snapshot.py's
    merge_confirmed() for the full rationale.
    """
    if scan_top_level(command, _check_merge_stmt) and output_has_merge_marker(output):
        return True
    return is_rest_merge_command(command) and output_has_rest_merge_marker(output)


def plan_advisory(status_msg: str | None, park_msg: str | None) -> tuple[bool, str] | None:
    """Pure: whether this merge's advisory must reach the model, and its combined text.

    A park message means this worktree's branch changed underneath the model, so the
    model must see it (needs_block True -> exit-2 stderr via emit_block); routine pull
    status alone is a user systemMessage (needs_block False). Returns (needs_block, text),
    or None when there is nothing to say. Returning a bool (not a channel string) keeps
    the untested main() consumer un-typo-able: a mistaken channel string would silently
    downgrade a park warning to a toast — the exact invisibility this migration removes.
    """
    lines = [m for m in (status_msg, park_msg) if m]
    if not lines:
        return None
    return (bool(park_msg), "\n".join(lines))


def main() -> None:
    _hookutil.record_heartbeat("post-pr-merge-pull")
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
    output = read_command_output(data)
    cwd = data.get("cwd", "")

    if not is_successful_merge(command, output):
        # gh's marker does not always survive to this hook's captured output
        # when gh exits abruptly right after a worktree's local-cleanup
        # failure (dev-env#489) — fall back to a live `gh pr view` confirmation
        # rather than silently skipping the local-main fast-forward (dev-env#504).
        if not scan_top_level(command, _check_merge_stmt):
            sys.exit(0)
        # `gh pr merge --help` (or any other non-mutating gh pr merge invocation
        # that prints no marker) can categorically never attempt a real merge —
        # treat it exactly like "not a merge command at all" rather than paying
        # a live gh pr view confirmation that resolves against cwd's current
        # branch and can misattribute an unrelated already-merged PR (dev-env#557).
        if is_merge_help_only(command):
            sys.exit(0)
        exit_code = data.get("tool_response", {}).get("exitCode", -1)
        if not should_confirm_via_gh(exit_code, output):
            sys.exit(0)
        if confirm_merge_via_gh(None, "", effective_merge_dir(command, cwd)) is None:
            sys.exit(0)

    repo = extract_repo(command, cwd)
    if not repo:
        sys.exit(0)

    local_path = REPO_LOCAL_PATHS.get(repo)
    if local_path is None:
        # Repo known but no local clone (e.g. brownm09/brownm09 profile)
        sys.exit(0)

    if not os.path.isdir(local_path):
        # emit_advisory is NoReturn (exits 0 here) — nothing below this branch runs.
        _hookout.emit_advisory(
            "PostToolUse",
            f"[post-merge-pull] {repo}: local path not found ({local_path}) — skipping",
            audience="user",
        )

    worktrees = list_worktrees(local_path)
    status_msg = pull_main(local_path, repo, canonical_on_main(worktrees))
    park_msg = park_worktree_off_main(cwd, worktrees)

    planned = plan_advisory(status_msg, park_msg)
    if planned is None:
        sys.exit(0)
    needs_block, text = planned
    if needs_block:
        # The model's own cwd branch just changed underneath it — it must see this.
        _hookout.emit_block(text)
    _hookout.emit_advisory("PostToolUse", text, audience="user")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Safe-exit guard: an informational hook must never block Claude.
        sys.exit(0)
