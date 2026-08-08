#!/usr/bin/env python3
"""
SessionStart hook: fetch, then fast-forward-or-warn, for whatever repo a session starts in.

Stale-checkout / `origin/main` drift is the #1 cross-repo friction across three biweekly
retros running (dev-env#910, dev-env#966) — a detached HEAD 20 commits behind, a branch cut
from a stale base, a "this feature is unbuilt" conclusion against a checkout that's just
behind. `CLAUDE.md` already carries several hand-written rules telling Claude to fetch first
(the CLI Scripting Checklist, the missing-file-investigation rule, the bare-local-`main`-ref
rule) — none of them is mechanical. This hook backs those rules with automation: on every
session start (startup, resume, clear, or compact — this hook is registered with no matcher),
resolve the repo the session is actually in, fetch, and either fast-forward the checkout when
it is safe to do so or emit a loud advisory naming exactly how far behind it is and why it
was not auto-fixed.

Generalizes `dev-env-sync.py`'s fetch -> compare -> `pull --ff-only` mechanic (which is
hardcoded to one repo and one branch) to any repo/default-branch, resolved dynamically from
the session's own cwd. dev-env's own canonical is explicitly excluded here (see
`DEV_ENV_REPO` below) — it already has a strictly more thorough, dev-env-specific mechanism
(off-main topology auto-correction + persistent-failure escalation, ADR-058/ADR-110); running
both against the same repo on every dev-env session would double the network/git cost for no
added coverage.

Auto-fix (`git pull --ff-only`) is deliberately narrow: it only ever fires on a
canonical-or-sole checkout (never a linked worktree — see `is_canonical_checkout`) that is
currently on exactly its own default branch, with a true fast-forward available (no local-only
commits), a clean working tree, and no other session's transcript active in that checkout in
the last few minutes (`_worktree_liveness.worktree_session_is_live`, extended here with an
`exclude_session_id` parameter so this hook does not always see itself as "live"). Every other
case — a linked worktree, a detached HEAD, an off-default-branch checkout, a dirty tree, a
true divergence, a concurrent session — is advisory only; this hook never mutates in any case
it cannot prove safe.

Fails open, unconditionally: every subprocess failure (not a git repo, fetch failure, a
rev-parse failure) exits 0 silently; every ineligible-to-autofix case falls through to an
advisory, never a block. This hook can never block a prompt or session start, never exits
non-zero, and its own failure never regresses a session below today's (manual-discipline-only)
baseline. It is a drift *detector*, not a gate. See ADR-130.

Advisories are delivered via `_hookout.emit_advisory("SessionStart", ..., audience="both")` —
`SessionStart` is one of the three events whose exit-0 stdout reaches the model
(`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`), and `audience="both"` additionally surfaces a
systemMessage toast to the user, matching the issue's own "loud advisory" framing.

Opt-out: a project may disable this hook entirely by setting
`"session_start_sync_disabled": true` in its own `.claude/hook-config.json`.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import subprocess
import sys
from pathlib import Path

import _hookout
import _hookutil
import _worktree_liveness
from _worktree_topology import (
    DETACHED,
    canonical_worktree,
    find_worktree_by_path,
    parse_worktree_porcelain,
    resolve_current_branch,
)

# Same constant dev-env-sync.py uses for its own hardcoded target -- see the module
# docstring above for why that repo is excluded from this generic hook's coverage.
DEV_ENV_REPO = Path.home() / "Git" / "dev-env"

# How recently another session's transcript must have been touched, in this exact checkout,
# to count as "may be using it right now" and block auto-fix. Deliberately much shorter than
# prune/reclaim's liveness windows (which ask "was this worktree used recently enough to be
# worth keeping" over a long horizon) -- the question here is narrower: "is another session
# actively working right now," so a stale multi-hour-old transcript should not block a safe,
# ff-only sync.
CONCURRENT_SESSION_WINDOW_SECONDS = 300


# --- pure helpers (unit-tested offline; no subprocess, no I/O) --------------------------


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def resolve_default_branch(returncode: int, stdout: str) -> str:
    """`git symbolic-ref --short refs/remotes/origin/HEAD` result -> default branch name.

    Falls back to "main" when `origin/HEAD` is unset (returncode != 0) or the output is
    otherwise empty. Every repo referenced anywhere in this corpus uses "main"; querying
    `gh repo view --json defaultBranchRef` to cover a currently-nonexistent case would add a
    network- and auth-dependent call to the common, cheap path this hook otherwise stays on.
    """
    if returncode != 0:
        return "main"
    branch = stdout.strip()
    return branch if branch else "main"


def is_canonical_checkout(repo_root: str, worktrees: "list[dict]") -> bool:
    """True when `repo_root` is the canonical (worktree-list entry 0) checkout, False for a
    linked worktree or when the worktree list could not be determined.

    Uses identity comparison between `find_worktree_by_path`'s result and
    `canonical_worktree`'s result -- the shortcut `find_worktree_by_path`'s own docstring
    documents for exactly this "compare against a specific entry" case, rather than
    re-deriving path normalization here.
    """
    if not worktrees:
        return False
    canonical = canonical_worktree(worktrees)
    match = find_worktree_by_path(worktrees, repo_root)
    return match is not None and match is canonical


def resolve_compare_ref(branch: str, upstream_ref: "str | None", default_branch: str) -> str:
    """The ref to compare HEAD against: `branch`'s own upstream if it has one, else a
    best-effort `f"origin/{default_branch}"` fallback.

    The fallback is what generalizes dev-env-sync.py's single-branch check to "detached HEAD
    20 commits behind" / "no upstream configured" incidents (career-playbook, win11-init-tools)
    -- a detached HEAD or a purely local branch has no upstream by definition, so without this
    fallback those checkouts would never be compared against anything at all.
    """
    if upstream_ref:
        return upstream_ref
    return f"origin/{default_branch}"


def can_autofix(
    *,
    is_canonical: bool,
    branch: str,
    default_branch: str,
    ahead_count: int,
    tree_clean: bool,
    concurrent_session: bool,
) -> bool:
    """Eligibility gate for an automatic `git pull --ff-only`.

    True only when ALL hold: `is_canonical` (never auto-mutate a linked worktree), `branch ==
    default_branch` (excludes detached HEAD and a worktree sitting on a feature branch by
    construction -- both fall through to the off-default-branch case), `ahead_count == 0` (a
    true fast-forward -- no local-only commits to lose), `tree_clean` (nothing uncommitted to
    clobber), and not `concurrent_session` (no other session's transcript active here
    recently). Any single False routes to advisory-only.
    """
    return (
        is_canonical
        and branch == default_branch
        and ahead_count == 0
        and tree_clean
        and not concurrent_session
    )


def classify_block_reason(
    *,
    is_canonical: bool,
    branch: str,
    default_branch: str,
    ahead_count: int,
    tree_clean: bool,
    concurrent_session: bool,
) -> str:
    """Human-readable reason the warn message names for why auto-fix did not apply.

    Fixed precedence when more than one condition fails at once (checked in this order):
    not-canonical > off-default-branch (covers detached HEAD) > diverged (ahead_count > 0) >
    dirty tree > concurrent session. Only ever called on a path that `can_autofix` already
    rejected, so the conditions are never all satisfied here -- but the function stays total
    (a defined result for any input) rather than assuming a specific caller contract.
    """
    if not is_canonical:
        return "checkout is a linked worktree, not the canonical/sole checkout"
    if branch != default_branch:
        if branch == DETACHED:
            return f"HEAD is detached (not on {default_branch!r})"
        return f"checked out on {branch!r}, not the default branch {default_branch!r}"
    if ahead_count > 0:
        return (
            f"local has {ahead_count} commit{_plural(ahead_count)} not on the remote "
            "(diverged, not a pure fast-forward)"
        )
    if not tree_clean:
        return "working tree has uncommitted changes"
    if concurrent_session:
        return "another session appears to be active in this checkout"
    return "eligible"  # unreachable via can_autofix's own gate, but keep this total, not partial


def format_stale_warning(
    repo_name: str,
    branch: str,
    compare_ref: str,
    local_sha: str,
    remote_sha: str,
    behind_count: int,
    ahead_count: int,
    reason: str,
) -> str:
    """Advisory for a stale checkout that was NOT auto-fixed, naming the gap and why."""
    diverged_clause = ""
    if ahead_count > 0:
        diverged_clause = f" and {ahead_count} commit{_plural(ahead_count)} ahead of it"
    return (
        f"[session-start-sync] {repo_name} ({branch}) is {behind_count} "
        f"commit{_plural(behind_count)} behind {compare_ref}{diverged_clause} "
        f"(local {local_sha[:8]} -> remote {remote_sha[:8]}).\n"
        f"  Not auto-fixed: {reason}.\n"
        "  Files on disk may be stale until this is resolved manually."
    )


def format_autofix_success(
    repo_name: str, default_branch: str, local_sha: str, remote_sha: str, behind_count: int
) -> str:
    """Success message for a completed automatic fast-forward pull."""
    return (
        f"[session-start-sync] Fast-forwarded {repo_name} {default_branch} "
        f"(local {local_sha[:8]} -> {remote_sha[:8]}, {behind_count} "
        f"commit{_plural(behind_count)}) - was stale at session start."
    )


def format_autofix_failure(repo_name: str, default_branch: str, git_stderr: str) -> str:
    """Race-case message: eligibility looked safe but the pull itself failed (most likely a
    concurrent change landed between the eligibility check and the pull). Falls through to a
    warning rather than being silently swallowed -- the checkout may still be stale."""
    return (
        f"[session-start-sync] {repo_name} {default_branch} looked fast-forward-safe but "
        "the pull failed (likely a concurrent change) - checkout may still be stale.\n"
        + _hookout.ascii_sanitize(git_stderr).strip()
    )


def load_disable_flag(hook_config: dict) -> bool:
    """Reads `"session_start_sync_disabled"` (bool) from a parsed hook-config.json dict.

    Defensive: a missing key, a non-dict `hook_config`, or a non-bool value all resolve to
    `False` (enabled) -- matches `idle-refresher.py`'s `load_threshold_minutes` defensive-read
    template of "any malformed config degrades to the default, never a crash."
    """
    if not isinstance(hook_config, dict):
        return False
    return hook_config.get("session_start_sync_disabled") is True


def _count_from(result: "subprocess.CompletedProcess") -> int:
    """Parse a `git rev-list --count` result; 0 on any failure (advisory-only diagnostic)."""
    text = result.stdout.strip()
    return int(text) if result.returncode == 0 and text.isdigit() else 0


def _resolve_path(path: str) -> str:
    """Resolved string form for robust path comparison.

    Matches `_worktree_topology`'s own internal `_norm` convention (not imported directly --
    that name is private/unexported); kept as a one-line helper so the dev-env-canonical
    exclusion check in `main()` compares like-for-like resolved paths rather than raw strings.
    """
    return str(Path(path).resolve())


def _read_hook_config_json(repo_root: str) -> dict:
    """Best-effort read of `<repo_root>/.claude/hook-config.json`. Any failure -> `{}`
    (enabled, since `load_disable_flag({})` is `False`) -- a missing or malformed config must
    never block this hook."""
    try:
        path = Path(repo_root) / ".claude" / "hook-config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --- main() orchestration (not unit-tested -- pure-helper convention; main()'s own
# subprocess sequencing is deliberately uncovered here, matching dev-env-sync.py /
# pre-bash-drift-check.py's established test convention) ---------------------------------


def run(args: "list[str]", cwd: str, **kwargs) -> "subprocess.CompletedProcess":
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


def main() -> None:
    _hookutil.record_heartbeat("session-start-sync")

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id")

    toplevel = run(["git", "rev-parse", "--show-toplevel"], cwd)
    if toplevel.returncode != 0:
        sys.exit(0)  # not a git repo -- nothing to sync
    repo_root = toplevel.stdout.strip()

    if _resolve_path(repo_root) == _resolve_path(str(DEV_ENV_REPO)):
        # dev-env's own canonical already has a strictly more thorough, dev-env-specific
        # mechanism (dev-env-sync.py) -- see the module docstring for why this is the one
        # explicit repo exclusion.
        sys.exit(0)

    hook_config = _read_hook_config_json(repo_root)
    if load_disable_flag(hook_config):
        sys.exit(0)

    # Fetches ALL remote-tracking branches in one round-trip, not just the default branch --
    # this is what also fixes a "wrong-branch pull scattered N files"-class incident, not just
    # default-branch staleness, for the same single network call.
    fetch = run(["git", "fetch", "origin", "--quiet"], repo_root)
    if fetch.returncode != 0:
        sys.exit(0)  # network/auth issue -- don't block, don't spam

    wt = run(["git", "worktree", "list", "--porcelain"], repo_root)
    worktrees = parse_worktree_porcelain(wt.stdout) if wt.returncode == 0 else []
    is_canonical = is_canonical_checkout(repo_root, worktrees)

    default_ref = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo_root)
    default_branch = resolve_default_branch(default_ref.returncode, default_ref.stdout)

    branch_ref = run(["git", "symbolic-ref", "--short", "HEAD"], repo_root)
    branch = resolve_current_branch(branch_ref.returncode, branch_ref.stdout)

    upstream = run(["git", "rev-parse", "--abbrev-ref", "@{u}"], repo_root)
    upstream_ref = upstream.stdout.strip() if upstream.returncode == 0 else None
    compare_ref = resolve_compare_ref(branch, upstream_ref, default_branch)

    local_rev = run(["git", "rev-parse", "HEAD"], repo_root)
    remote_rev = run(["git", "rev-parse", compare_ref], repo_root)
    if local_rev.returncode != 0 or remote_rev.returncode != 0:
        sys.exit(0)
    local_sha = local_rev.stdout.strip()
    remote_sha = remote_rev.stdout.strip()
    if local_sha == remote_sha:
        sys.exit(0)  # already up to date -- don't spam the healthy path

    behind_count = _count_from(run(["git", "rev-list", "--count", f"{local_sha}..{remote_sha}"], repo_root))
    if behind_count == 0:
        sys.exit(0)  # strictly ahead of compare_ref, not stale
    ahead_count = _count_from(run(["git", "rev-list", "--count", f"{remote_sha}..{local_sha}"], repo_root))

    status = run(["git", "status", "--porcelain"], repo_root)
    tree_clean = status.returncode == 0 and not status.stdout.strip()

    # Only pay the transcript-directory-scan cost when every other eligibility condition
    # already holds -- a path that is going to warn regardless never needs it.
    provisionally_eligible = (
        is_canonical and branch == default_branch and ahead_count == 0 and tree_clean
    )
    concurrent_session = False
    if provisionally_eligible:
        concurrent_session = _worktree_liveness.worktree_session_is_live(
            repo_root,
            window_seconds=CONCURRENT_SESSION_WINDOW_SECONDS,
            exclude_session_id=session_id,
        )

    repo_name = Path(repo_root).name

    if can_autofix(
        is_canonical=is_canonical,
        branch=branch,
        default_branch=default_branch,
        ahead_count=ahead_count,
        tree_clean=tree_clean,
        concurrent_session=concurrent_session,
    ):
        pull = run(["git", "pull", "--ff-only", "origin", default_branch], repo_root)
        if pull.returncode == 0:
            _hookout.emit_advisory(
                "SessionStart",
                format_autofix_success(repo_name, default_branch, local_sha, remote_sha, behind_count),
                audience="both",
            )
        else:
            # Race: eligibility looked safe but the pull itself failed (most likely a
            # concurrent change landed in between) -- fall through to a warning rather than
            # silently swallowing the failure.
            _hookout.emit_advisory(
                "SessionStart",
                format_autofix_failure(repo_name, default_branch, pull.stderr),
                audience="both",
            )
        return  # unreachable -- emit_advisory always exits; kept for explicit control flow

    reason = classify_block_reason(
        is_canonical=is_canonical,
        branch=branch,
        default_branch=default_branch,
        ahead_count=ahead_count,
        tree_clean=tree_clean,
        concurrent_session=concurrent_session,
    )
    _hookout.emit_advisory(
        "SessionStart",
        format_stale_warning(
            repo_name, branch, compare_ref, local_sha, remote_sha, behind_count, ahead_count, reason
        ),
        audience="both",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail open unconditionally -- this hook must never block a session start, matching
        # the module docstring's "drift detector, not a gate" contract. sys.exit(0) here is
        # the last-resort backstop against an unanticipated crash (e.g. a subprocess timeout);
        # every anticipated failure mode inside main() already exits 0 or falls through to an
        # advisory on its own.
        sys.exit(0)
