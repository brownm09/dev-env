#!/usr/bin/env python3
"""
SessionStart hook: fetch, then fast-forward-or-warn, for whatever repo a session starts in.

Stale-checkout / `origin/main` drift is the #1 cross-repo friction across three biweekly
retros running (dev-env#910, dev-env#966) — a detached HEAD 20 commits behind, a branch cut
from a stale base, a "this feature is unbuilt" conclusion against a checkout that's just
behind. `CLAUDE.md` already carries several hand-written rules telling Claude to fetch first
(the CLI Scripting Checklist, the missing-file-investigation rule, the bare-local-`main`-ref
rule) — none of them is mechanical. This hook backs those rules with automation: on session
start (registered for the `startup` and `resume` sources only — see the matcher note below),
resolve the repo the session is actually in, fetch, and either fast-forward the checkout when
it is safe to do so or emit a loud advisory naming exactly how far behind it is and why it
was not auto-fixed.

Generalizes `dev-env-sync.py`'s fetch -> compare -> `pull --ff-only` mechanic (which is
hardcoded to one repo and one branch) to any repo/default-branch, resolved dynamically from
the session's own cwd. dev-env's own checkouts (canonical AND any worktree of it — see
`DEV_ENV_REPO` below) are explicitly excluded here — dev-env already has a strictly more
thorough, dev-env-specific mechanism (off-main topology auto-correction + persistent-failure
escalation, ADR-058/ADR-110) that fires unconditionally every prompt regardless of which
dev-env checkout the current session happens to be in.

**Matcher note (dev-env#966 review finding).** This hook is registered in `claude/settings.json`
with `"matcher": "startup|resume"`, NOT unmatched. `SessionStart` also fires on `clear` and
`compact`; `/compact` in particular is a routine mid-session operation (triggered by context
growth, not elapsed time) and synchronously blocking it on a remote `git fetch` was measured
as disproportionate to the staleness risk a routine compact actually represents — and widened
the false-concurrency window against this session's own just-written transcript. `startup`
and `resume` are the two sources where "is this checkout stale" is a meaningful question.

Auto-fix (`git merge --ff-only <compare_ref>`, run against the already-fetched
remote-tracking ref the eligibility decision itself was measured against — see the
"decoupled ref" fix below) is deliberately narrow: it only ever fires on a
canonical-or-sole checkout (never a linked worktree — see `is_canonical_checkout`) that is
currently on exactly its own default branch, with a true fast-forward available (no local-only
commits), a clean *tracked* working tree (untracked files do not block a fast-forward and are
not treated as dirty — see the `tree_clean` fix below), and no other session's transcript
active in this checkout or the session's own cwd in the last few minutes
(`_worktree_liveness.worktree_session_is_live`, extended with an `exclude_session_id`
parameter so this hook does not always see itself, or its own subagents, as "live"). Every
other case — a linked worktree, a detached HEAD, an off-default-branch checkout, a dirty
tracked tree, a true divergence, a concurrent session, an unmeasurable git state — is
advisory only; this hook never mutates in any case it cannot prove safe.

Fails open, unconditionally: every subprocess failure (not a git repo, fetch failure, a
rev-parse failure, a call that would exceed this firing's own time budget) exits 0 silently
or falls through to an advisory; every ineligible-to-autofix case falls through to an
advisory, never a block. This hook can never block a prompt or session start, never exits
non-zero, and its own failure never regresses a session below today's (manual-discipline-only)
baseline. It is a drift *detector*, not a gate. See ADR-130 (and its dev-env#966 review-finding
addendum, which corrected several defects the original version shipped with — the default-
branch prefix bug, the untracked-files-as-dirty bug, and others listed there — found only
after two independent review passes and live verification against real repos on this machine).

Advisories are delivered via `_hookout.emit_advisory("SessionStart", ..., audience="both")` —
`SessionStart` is one of the three events whose exit-0 stdout reaches the model
(`_hookout.STDOUT_MODEL_VISIBLE_EVENTS`), and `audience="both"` additionally surfaces a
systemMessage toast to the user, matching the issue's own "loud advisory" framing.

**Deferred, explicitly out of scope:** persistent-failure escalation across sessions (unlike
`dev-env-sync.py`'s ADR-110 mechanism — this hook fires once per session, so a permanently
blocked repo repeats the same single-line advisory rather than escalating; a candidate for a
future ADR amendment if this proves insufficient in practice), mid-session repo-hops (only the
session's own primary repo at firing time is checked), and multi-repo-in-play coverage in a
single firing (`SessionStart` only knows about one `cwd`).

Opt-out: a project may disable this hook entirely by setting
`"session_start_sync_disabled": true` in its own `.claude/hook-config.json` — read from the
checkout's *canonical* root (via `_worktree_canon.canonical_repo_root`), not a worktree's own
copy, since `.claude/` is commonly gitignored and a worktree checkout never has its own copy
of a gitignored file to read.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import _hookout
import _hookutil
import _worktree_canon
import _worktree_liveness
from _worktree_topology import (
    DETACHED,
    canonical_worktree,
    parse_worktree_porcelain,
    resolve_current_branch,
)

# Same constant dev-env-sync.py uses for its own hardcoded target -- see the module
# docstring above for why every dev-env checkout (not just the canonical) is excluded.
DEV_ENV_REPO = Path.home() / "Git" / "dev-env"

# How recently another session's transcript must have been touched, in this exact checkout,
# to count as "may be using it right now" and block auto-fix. Deliberately much shorter than
# prune/reclaim's liveness windows (which ask "was this worktree used recently enough to be
# worth keeping" over a long horizon) -- the question here is narrower: "is another session
# actively working right now," so a stale multi-hour-old transcript should not block a safe,
# ff-only sync.
CONCURRENT_SESSION_WINDOW_SECONDS = 300

# Must stay <= this hook's own "timeout" value in claude/settings.json. A shared deadline
# (not a flat per-call timeout) bounds the whole firing's total subprocess time, since ~12
# git calls each independently claiming up to PER_CALL_TIMEOUT_SECONDS could otherwise total
# far more than the harness's own budget before the harness kills the process outright --
# losing the one advisory this hook exists to produce, and risking an unsupervised mutation
# if the killed call was the merge (dev-env#966 review finding).
HOOK_TIMEOUT_SECONDS = 30
DEADLINE_SAFETY_MARGIN_SECONDS = 3
PER_CALL_TIMEOUT_SECONDS = 15
# Below this much remaining budget, don't even attempt another subprocess call -- return a
# synthetic failure the caller's existing fail-open/advisory handling already covers, rather
# than starting a call likely to be killed mid-flight.
MIN_CALL_BUDGET_SECONDS = 0.5

# Conservative ref-name validator applied before any resolved ref is interpolated into a git
# command argument (dev-env#966 review finding -- a leading-dash ref name is format-valid to
# git (`git check-ref-format refs/heads/-foo` exits 0, verified) and would let `rev-parse`/
# `merge` parse it as an option instead of a ref. Every call here uses list-form
# subprocess.run with no shell, so this is argument injection, not command injection, and the
# values are git-local-state-derived, not user input -- low severity, defense in depth.
_REF_NAME_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")


# --- pure helpers (unit-tested offline; no subprocess, no I/O) --------------------------


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def is_valid_ref_name(name: str) -> bool:
    """Conservative validity check for a ref name before it is used as a command argument.

    Stricter than git's own ref-name rules -- the only refs this hook ever needs to pass
    through are ordinary branch/remote-tracking names, so there is no reason to accept
    anything unusual. Rejects an empty string and any name starting with a character other
    than an alphanumeric/`.`/`_` (in particular, a leading `-`).
    """
    return bool(name) and bool(_REF_NAME_RE.match(name))


def resolve_default_branch(returncode: int, stdout: str) -> str:
    """`git symbolic-ref --short refs/remotes/origin/HEAD` result -> default branch name.

    The command's output RETAINS the "origin/" prefix even with `--short` -- it shortens
    "refs/remotes/origin/HEAD"'s *target* (e.g. "refs/remotes/origin/main") down to its
    unambiguous short form "origin/main", not down to a bare branch name. (dev-env#966 review
    finding: the original version of this function treated the output as already-bare,
    verified live to be wrong against real repos -- win11-init-tools and lifting-logbook both
    return "origin/main" with returncode 0 -- which broke `can_autofix`'s
    `branch == default_branch` comparison unconditionally, since `branch` itself comes from a
    plain `git symbolic-ref --short HEAD` and is never prefixed.)

    Falls back to "main" when `origin/HEAD` is unset (returncode != 0), blank, or the
    stripped-prefix result is empty. Every repo referenced anywhere in this corpus uses
    "main"; querying `gh repo view --json defaultBranchRef` to cover a currently-nonexistent
    case would add a network- and auth-dependent call to the common, cheap path this hook
    otherwise stays on.
    """
    if returncode != 0:
        return "main"
    branch = stdout.strip().removeprefix("origin/")
    return branch if branch else "main"


def is_canonical_checkout(repo_root: str, worktrees: "list[dict]") -> bool:
    """True when `repo_root` is the canonical (worktree-list entry 0) checkout, False for a
    linked worktree or when the worktree list could not be determined.

    Compares by resolved-path VALUE against `canonical_worktree()`'s own path, not by
    identity against `find_worktree_by_path`'s result. (dev-env#966 review finding:
    `find_worktree_by_path`'s own docstring explicitly disclaims the identity-comparison
    shortcut as something a caller should rely on -- "identity is an implementation detail
    of the current 'search and return the element' approach, not a promise this docstring
    makes callers depend on" -- so this compares by value instead, matching how
    `_worktree_topology`'s own `_norm`-based callers do it.)
    """
    if not worktrees:
        return False
    canonical = canonical_worktree(worktrees)
    if canonical is None:
        return False
    return _resolve_path(repo_root) == _resolve_path(canonical["path"])


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
    ahead_count: "int | None",
    tree_clean: bool,
    concurrent_session: bool,
) -> bool:
    """Eligibility gate for an automatic `git merge --ff-only`.

    True only when ALL hold: `is_canonical` (never auto-mutate a linked worktree), `branch ==
    default_branch` (excludes detached HEAD and a worktree sitting on a feature branch by
    construction -- both fall through to the off-default-branch case), `ahead_count == 0` (a
    true fast-forward -- no local-only commits to lose; `None`, meaning the count could not be
    measured, safely fails this same comparison rather than needing a separate check), `tree_clean`
    (nothing uncommitted-and-tracked to clobber), and not `concurrent_session` (no other
    session's transcript active here recently). Any single False routes to advisory-only.
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
    ahead_count: "int | None",
    tree_clean: bool,
    concurrent_session: bool,
) -> str:
    """Human-readable reason the warn message names for why auto-fix did not apply.

    Fixed precedence when more than one condition fails at once (checked in this order):
    not-canonical > off-default-branch (covers detached HEAD) > unmeasurable local-commit
    count > diverged (ahead_count > 0) > dirty tracked tree > concurrent session. Only ever
    called on a path that `can_autofix` already rejected, so the conditions are never all
    satisfied here -- but the function stays total (a defined result for any input) rather
    than assuming a specific caller contract.
    """
    if not is_canonical:
        return "checkout is a linked worktree, not the canonical/sole checkout"
    if branch != default_branch:
        if branch == DETACHED:
            return f"HEAD is detached (not on {default_branch!r})"
        return f"checked out on {branch!r}, not the default branch {default_branch!r}"
    if ahead_count is None:
        return "local commit count relative to the remote could not be measured"
    if ahead_count > 0:
        return (
            f"local has {ahead_count} commit{_plural(ahead_count)} not on the remote "
            "(diverged, not a pure fast-forward)"
        )
    if not tree_clean:
        return "tracked files have uncommitted changes"
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
    ahead_count: "int | None",
    reason: str,
    *,
    is_untracked_branch: bool,
) -> str:
    """Advisory for a stale checkout that was NOT auto-fixed, naming the gap and why.

    `is_untracked_branch` (True when `branch` has no upstream and is not detached -- i.e. a
    not-yet-pushed local/feature branch, most commonly a fresh Claude-managed worktree)
    softens the closing sentence: for that case, "N commits behind" is ordinary rebase
    distance for an intentional branch, not evidence of a stale checkout, and the original
    "files on disk may be stale... resolve manually" framing was actively misleading on what
    review found is likely this hook's *most frequently firing* case, given how many linked
    worktrees are typically active in this environment (dev-env#966 review finding).
    """
    diverged_clause = ""
    if ahead_count is not None and ahead_count > 0:
        diverged_clause = f" and {ahead_count} commit{_plural(ahead_count)} ahead of it"
    header = (
        f"[session-start-sync] {repo_name} ({branch}) is {behind_count} "
        f"commit{_plural(behind_count)} behind {compare_ref}{diverged_clause} "
        f"(local {local_sha[:8]} -> remote {remote_sha[:8]}).\n"
        f"  Not auto-fixed: {reason}.\n"
    )
    if is_untracked_branch:
        return header + (
            "  This branch has no upstream yet (compared against the repo default as a "
            "best-effort baseline) - if it's an intentional, not-yet-pushed branch, this is "
            "expected rebase distance, not staleness."
        )
    return header + "  Files on disk may be stale until this is resolved manually."


def format_unmeasured_drift_warning(repo_name: str, branch: str, compare_ref: str) -> str:
    """Advisory for the case where local and remote SHAs differ but the commit-behind count
    itself could not be measured (a `git rev-list` failure). Warns rather than the silent
    exit the original version took on this path -- a failed measurement on a possibly
    genuinely-stale checkout must not read the same as "confirmed up to date"
    (dev-env#966 review finding)."""
    return (
        f"[session-start-sync] {repo_name} ({branch}) differs from {compare_ref} but the "
        "commit-behind count could not be measured (git rev-list failed) - checkout may be "
        "stale; verify manually."
    )


def format_autofix_success(
    repo_name: str,
    default_branch: str,
    local_sha: str,
    new_head_sha: str,
    behind_count: int,
    *,
    expected_sha: "str | None" = None,
) -> str:
    """Success message for a completed automatic fast-forward merge.

    `new_head_sha` must be measured AFTER the merge (a fresh `git rev-parse HEAD`), not the
    pre-merge `compare_ref` measurement -- the original version reported the pre-merge value
    as if it were the confirmed post-merge result (dev-env#966 review finding). `expected_sha`
    (the pre-merge measurement) is compared against `new_head_sha`; a mismatch means the ref
    moved between measurement and merge (most likely a concurrent process) and is surfaced
    explicitly rather than silently misreported, matching `dev-env-sync.py`'s own established
    mismatch-note convention (PR #701).
    """
    note = ""
    if expected_sha is not None and expected_sha != new_head_sha:
        note = (
            f"\n  (expected to land on {expected_sha[:8]} but HEAD is now {new_head_sha[:8]} "
            "- a concurrent process likely moved the ref during the merge)"
        )
    return (
        f"[session-start-sync] Fast-forwarded {repo_name} {default_branch} "
        f"(local {local_sha[:8]} -> {new_head_sha[:8]}, {behind_count} "
        f"commit{_plural(behind_count)}) - was stale at session start."
        + note
    )


def format_autofix_failure(repo_name: str, default_branch: str, git_stderr: str) -> str:
    """Race-case message: eligibility looked safe but the merge itself failed (most likely a
    concurrent change landed between the eligibility check and the merge, or the firing's own
    time budget ran out first). Falls through to a warning rather than being silently
    swallowed -- the checkout may still be stale."""
    sanitized = _hookout.ascii_sanitize(git_stderr).strip()
    detail = sanitized if sanitized else "(git produced no diagnostic output)"
    return (
        f"[session-start-sync] {repo_name} {default_branch} looked fast-forward-safe but "
        "the merge failed (likely a concurrent change) - checkout may still be stale.\n"
        + detail
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


def _parse_left_right_counts(result: "subprocess.CompletedProcess") -> "tuple[int | None, int | None]":
    """Parse a `git rev-list --left-right --count A...B` result into `(ahead, behind)`.

    Output shape is `"left_count\\tright_count"` -- for `A...B` with A=local, B=remote, left
    counts commits reachable from A not B (local-only, i.e. ahead), right counts the reverse
    (remote-only, i.e. behind). Verified live before use:
    `git rev-list --left-right --count HEAD...origin/main` -> `"1\\t0"` shape confirmed.

    Returns `(None, None)` on any failure or unexpected shape -- never guesses. `None`, not
    `0`, is the deliberate failure value: both counts are load-bearing (`ahead_count` gates
    `can_autofix`; `behind_count == 0` gates a silent exit), and collapsing "could not
    measure" into "measured zero" previously favored the unsafe outcome in both directions --
    falsely eligible to auto-mutate, and silently exiting on a possibly-stale checkout
    (dev-env#966 review finding). Replaces two separate `git rev-list --count` calls (and the
    prior `_count_from` helper) with one combined call, also reducing this firing's total
    subprocess-timeout exposure by one call.
    """
    if result.returncode != 0:
        return None, None
    parts = result.stdout.strip().split("\t")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None, None
    return int(parts[0]), int(parts[1])


def _resolve_path(path: str) -> str:
    """Resolved string form for robust path comparison.

    Matches `_worktree_topology`'s own internal `_norm` convention exactly, INCLUDING its
    falsy-path guard: an empty path must never resolve to the current working directory and
    accidentally compare-equal to a real worktree/canonical path. (dev-env#966 review finding:
    the original version of this function omitted the guard despite its docstring claiming to
    match `_norm` -- `_norm` is not imported directly since it is a private, unexported name.)
    """
    if not path:
        return ""
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


def run(args: "list[str]", cwd: str, deadline: float) -> "subprocess.CompletedProcess":
    """Run a git subprocess, bounded by both a per-call ceiling and a shared firing-wide
    deadline (a `time.monotonic()` timestamp computed once in `main()` and threaded through
    every call, rather than each of the ~12 calls independently claiming up to
    `PER_CALL_TIMEOUT_SECONDS` -- worst case previously ~12 x 15s against this hook's own
    30s harness timeout; dev-env#966 review finding). Once less than
    `MIN_CALL_BUDGET_SECONDS` remains, refuses to even attempt the call, returning a synthetic
    failure the caller's existing fail-open/advisory handling already covers, rather than
    starting a call the harness may kill mid-flight -- most important for the mutating merge
    at the end of `main()`'s sequence, where a killed subprocess could leave an unsupervised
    mutation in progress or (on Windows) an orphaned `.git/index.lock`.
    """
    remaining = deadline - time.monotonic()
    if remaining <= MIN_CALL_BUDGET_SECONDS:
        return subprocess.CompletedProcess(args=args, returncode=124, stdout="", stderr="")
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=min(PER_CALL_TIMEOUT_SECONDS, remaining),
    )


def main() -> None:
    _hookutil.record_heartbeat("session-start-sync")
    deadline = time.monotonic() + HOOK_TIMEOUT_SECONDS - DEADLINE_SAFETY_MARGIN_SECONDS

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    session_id = payload.get("session_id")

    toplevel = run(["git", "rev-parse", "--show-toplevel"], cwd, deadline)
    if toplevel.returncode != 0:
        sys.exit(0)  # not a git repo -- nothing to sync
    repo_root = toplevel.stdout.strip()
    if not repo_root:
        sys.exit(0)

    # config_root is the CANONICAL root for this checkout (identity for a plain repo; the
    # canonical for a Claude-managed worktree) -- used only for the dev-env exclusion and the
    # hook-config read below, never for the actual git operations, which must run against the
    # session's real checkout (repo_root). A worktree's own `.claude/` commonly doesn't exist
    # at all (gitignored, and `git worktree add` never checks out a gitignored file), so
    # reading hook-config from repo_root directly silently missed every worktree session's
    # opt-out; and the dev-env exclusion previously matched only the canonical, so a dev-env
    # worktree session paid this hook's fetch AND dev-env-sync.py's fetch against the same
    # remote (dev-env#966 review finding).
    config_root = _worktree_canon.canonical_repo_root(repo_root)

    if _resolve_path(config_root) == _resolve_path(str(DEV_ENV_REPO)):
        # Every dev-env checkout (canonical or worktree) already gets dev-env-sync.py's
        # strictly more thorough, unconditional-every-prompt coverage -- see the module
        # docstring for why this is the one explicit repo exclusion.
        sys.exit(0)

    hook_config = _read_hook_config_json(config_root)
    if load_disable_flag(hook_config):
        sys.exit(0)

    # Fetches ALL remote-tracking branches in one round-trip, not just the default branch --
    # this is what also fixes a "wrong-branch pull scattered N files"-class incident, not just
    # default-branch staleness, for the same single network call.
    fetch = run(["git", "fetch", "origin", "--quiet"], repo_root, deadline)
    if fetch.returncode != 0:
        sys.exit(0)  # network/auth issue -- don't block, don't spam

    wt = run(["git", "worktree", "list", "--porcelain"], repo_root, deadline)
    worktrees = parse_worktree_porcelain(wt.stdout) if wt.returncode == 0 else []
    is_canonical = is_canonical_checkout(repo_root, worktrees)

    default_ref = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo_root, deadline)
    default_branch = resolve_default_branch(default_ref.returncode, default_ref.stdout)

    branch_ref = run(["git", "symbolic-ref", "--short", "HEAD"], repo_root, deadline)
    branch = resolve_current_branch(branch_ref.returncode, branch_ref.stdout)

    upstream = run(["git", "rev-parse", "--abbrev-ref", "@{u}"], repo_root, deadline)
    upstream_ref = upstream.stdout.strip() if upstream.returncode == 0 else None
    compare_ref = resolve_compare_ref(branch, upstream_ref, default_branch)
    if not is_valid_ref_name(compare_ref):
        sys.exit(0)  # malformed ref from local git state -- don't risk using it in a command

    revs = run(["git", "rev-parse", "HEAD", compare_ref], repo_root, deadline)
    if revs.returncode != 0:
        sys.exit(0)
    rev_lines = revs.stdout.strip().splitlines()
    if len(rev_lines) != 2:
        sys.exit(0)  # unexpected output shape -- fail safe
    local_sha, remote_sha = rev_lines
    if local_sha == remote_sha:
        sys.exit(0)  # already up to date -- don't spam the healthy path

    counts = run(["git", "rev-list", "--left-right", "--count", f"{local_sha}...{remote_sha}"], repo_root, deadline)
    ahead_count, behind_count = _parse_left_right_counts(counts)
    repo_name = Path(repo_root).name

    if behind_count is None:
        _hookout.emit_advisory(
            "SessionStart",
            format_unmeasured_drift_warning(repo_name, branch, compare_ref),
            audience="both",
        )
    if behind_count == 0:
        sys.exit(0)  # strictly ahead of compare_ref (or an unmeasurable ahead side), not stale

    status = run(["git", "status", "--porcelain", "--untracked-files=no"], repo_root, deadline)
    # Untracked files cannot block a fast-forward (except the one case `--ff-only` itself
    # refuses and reports, already covered by format_autofix_failure), so they must not read
    # as "dirty" here -- the prior version's plain `--porcelain` counted them, which made
    # auto-fix permanently unreachable in exactly the repos ADR-130 was motivated by
    # (career-playbook, lifting-logbook both carry long-lived untracked entries; dev-env#966
    # review finding, verified live).
    tree_clean = status.returncode == 0 and not status.stdout.strip()

    # Only pay the transcript-directory-scan cost when every other eligibility condition
    # already holds, and only when there's a session_id to exclude -- without one, "is some
    # OTHER session here" can't be answered (a missing id would match nothing, or worse,
    # match this session's own transcript once one exists), so default to "no concurrent
    # session detected" rather than let an unparseable payload silently disable auto-fix
    # (dev-env#966 review finding).
    provisionally_eligible = can_autofix(
        is_canonical=is_canonical,
        branch=branch,
        default_branch=default_branch,
        ahead_count=ahead_count,
        tree_clean=tree_clean,
        concurrent_session=False,
    )
    concurrent_session = False
    if provisionally_eligible and session_id:
        concurrent_session = _worktree_liveness.worktree_session_is_live(
            repo_root,
            window_seconds=CONCURRENT_SESSION_WINDOW_SECONDS,
            exclude_session_id=session_id,
        )
        # Claude Code slugs a session's transcript directory from its actual cwd, which can
        # differ from repo_root when a session started in a subdirectory -- check both rather
        # than silently missing a live session whose transcript never matches repo_root at all
        # (dev-env#966 review finding).
        if not concurrent_session and _resolve_path(cwd) != _resolve_path(repo_root):
            concurrent_session = _worktree_liveness.worktree_session_is_live(
                cwd,
                window_seconds=CONCURRENT_SESSION_WINDOW_SECONDS,
                exclude_session_id=session_id,
            )

    if can_autofix(
        is_canonical=is_canonical,
        branch=branch,
        default_branch=default_branch,
        ahead_count=ahead_count,
        tree_clean=tree_clean,
        concurrent_session=concurrent_session,
    ):
        # Merge the exact ref eligibility was measured against (already fetched above -- no
        # second network round-trip) rather than a hardcoded `origin/<default_branch>`, which
        # would silently diverge from the measurement whenever the branch's real upstream
        # isn't literally that ref (a fork remote, a differently-named tracking branch;
        # dev-env#966 review finding).
        merge = run(["git", "merge", "--ff-only", compare_ref], repo_root, deadline)
        if merge.returncode == 0:
            post_head = run(["git", "rev-parse", "HEAD"], repo_root, deadline)
            new_head_sha = post_head.stdout.strip() if post_head.returncode == 0 else remote_sha
            _hookout.emit_advisory(
                "SessionStart",
                format_autofix_success(
                    repo_name, default_branch, local_sha, new_head_sha, behind_count,
                    expected_sha=remote_sha,
                ),
                audience="both",
            )
        else:
            # Race: eligibility looked safe but the merge itself failed (most likely a
            # concurrent change landed in between, or the deadline ran out) -- fall through to
            # a warning rather than silently swallowing the failure.
            _hookout.emit_advisory(
                "SessionStart",
                format_autofix_failure(repo_name, default_branch, merge.stderr),
                audience="both",
            )

    reason = classify_block_reason(
        is_canonical=is_canonical,
        branch=branch,
        default_branch=default_branch,
        ahead_count=ahead_count,
        tree_clean=tree_clean,
        concurrent_session=concurrent_session,
    )
    is_untracked_branch = upstream_ref is None and branch != DETACHED
    _hookout.emit_advisory(
        "SessionStart",
        format_stale_warning(
            repo_name, branch, compare_ref, local_sha, remote_sha, behind_count, ahead_count,
            reason, is_untracked_branch=is_untracked_branch,
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
