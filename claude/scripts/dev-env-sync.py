#!/usr/bin/env python3
"""
UserPromptSubmit hook: keep the local dev-env repo in sync with origin/main.

Runs a fast-forward pull at session start so that CLAUDE.md and other
symlinked tooling always reflect the latest merged changes. Silent on
success; emits a warning if the repo has diverged and needs manual attention.

When the canonical worktree is off `main` (so `~/.claude/` symlinks would serve
that branch's stale files) — including a *detached* HEAD, routed into the same path via
`resolve_current_branch` (dev-env#619) — diagnoses the worktree-on-main topology and either:
  - auto-returns a *clean* canonical to `main`, then continues the fast-forward pull;
  - warns, naming a non-canonical worktree squatting `main` plus its park command,
    when one is blocking the canonical's return (dev-env#396, ADR-058); or
  - warns without switching when the canonical has uncommitted drift (preserved).

All advisories print to STDOUT, never stderr (dev-env#694, ADR-098). This hook always exits 0,
and per the Claude Code hooks reference, a `UserPromptSubmit` hook's exit-0 stdout is added to
Claude's context — stderr is not. A prior version routed warnings to stderr, which made a
fast-forward failure (e.g. a dirty working-tree file conflicting with an incoming commit)
invisible for 36+ hours and 21+ commits of drift, confirmed live during the dev-env#694
investigation: the hook fired every prompt, `git pull --ff-only` failed every time, and the
stderr warning never once reached the model or the user. Every warning now also states local/
remote short SHAs and the commit-behind count so a future occurrence is self-diagnosing without
a manual `git log`/`git fetch` comparison.

When a fast-forward pull keeps failing across prompts/sessions (a dirty tracked file conflicting
with an incoming commit — dev-env#697, #795), the failure is tracked in a single repo-level
scratch state file and, once it has persisted across several prompts or a couple of hours,
escalated to a distinct, louder advisory naming the commits-behind count, the blocking file
path(s), and how long it has been failing — so a genuinely stuck canonical can't silently drift
many commits behind (leaving every merged dev-env fix inert, since `~/.claude/` is junctioned to
this checkout's working tree) the way it has twice before (dev-env#797, ADR-110).

Exit 0 always — never block the user's prompt.
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import _hookout
import _hookutil
from _worktree_topology import (
    canonical_sync_action,
    diagnose_main_topology,
    parse_worktree_porcelain,
    resolve_current_branch,
)

DEV_ENV_REPO = Path.home() / "Git" / "dev-env"
SCRATCH = Path.home() / ".claude" / "scratch"

# Persistent fast-forward-failure escalation (dev-env#797). A single, repo-level (NOT
# per-session) scratch state file records how long / how many consecutive prompts the
# canonical's fast-forward pull has been failing, so a genuinely stuck canonical (a dirty
# tracked file conflicting with an incoming commit — dev-env#697, #795) escalates to a
# distinct, louder advisory instead of the same-severity per-prompt warning that let it drift
# 21 commits / ~41h unnoticed. Per-session state would reset every session and defeat the
# multi-session persistence detection that is the whole point of the issue. See ADR-110.
FAILURE_STATE_PREFIX = "dev_env_sync_ff_failure"
ESCALATE_AFTER_CONSECUTIVE_FAILURES = 3
ESCALATE_AFTER_HOURS = 2.0


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=DEV_ENV_REPO,
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _count_from(result: subprocess.CompletedProcess) -> int:
    """Parse a `git rev-list --count` result; 0 on any failure (advisory-only diagnostic)."""
    text = result.stdout.strip()
    return int(text) if result.returncode == 0 and text.isdigit() else 0


def format_sync_note(local: str, remote: str, behind: int) -> str:
    """Short diagnostic clause: short SHAs + commits-behind count.

    Appended to every fast-forward-related warning so a future occurrence is self-diagnosing
    without a manual `git log`/`git fetch` comparison (dev-env#694's suggested follow-up).
    """
    return f"local {local[:8]} is {behind} commit{_plural(behind)} behind origin/main {remote[:8]}"


def format_pull_failure_message(local: str, remote: str, behind: int, git_stderr: str) -> str:
    # git's stderr is echoed verbatim, but it can carry non-cp1252 bytes (a non-Western
    # LC_MESSAGES locale, or a U+FFFD from _winsubp's errors="replace" on invalid UTF-8) that
    # would raise UnicodeEncodeError at print() on Claude Code's Windows hook-output pipe and
    # lose the whole advisory. ascii_sanitize guarantees a cp1252-safe rendering (review
    # finding, PR #800; same root cause as ADR-098's cp1252 fix).
    return (
        "[dev-env-sync] WARNING: fast-forward pull failed — "
        + format_sync_note(local, remote, behind)
        + " and could not be applied.\n"
        + _hookout.ascii_sanitize(git_stderr).strip()
    )


def format_diverged_message(local: str, remote: str, behind: int, ahead: int) -> str:
    """Warning for local `main` not being a fast-forward ancestor of `origin/main`.

    Covers two distinct states the caller does not distinguish: a true fork (``behind`` and
    ``ahead`` both > 0) versus local merely being ahead with nothing new on origin (``behind
    == 0``, e.g. a commit landed directly on the canonical). Calling the latter "diverged"
    would be internally contradictory ("0 commits behind ... has diverged") — review finding,
    PR #701.
    """
    if behind == 0:
        return (
            "[dev-env-sync] WARNING: local dev-env repo is ahead of origin/main "
            f"(local {local[:8]} has {ahead} commit{_plural(ahead)} not on origin/main "
            f"{remote[:8]} — did something commit directly to the canonical?).\n"
            "CLAUDE.md and symlinked tooling may be stale. Run `git -C ~/Git/dev-env "
            "status` to investigate before proceeding."
        )
    return (
        "[dev-env-sync] WARNING: local dev-env repo has diverged from origin/main "
        f"(local {local[:8]} is {ahead} commit{_plural(ahead)} ahead and {behind} "
        f"commit{_plural(behind)} behind origin/main {remote[:8]}).\n"
        "CLAUDE.md and symlinked tooling may be stale. Run `git -C ~/Git/dev-env "
        "status` to investigate before proceeding."
    )


def format_pulled_message(local: str, remote: str, behind_count: int, pulled_lines: "list[str]") -> str:
    """Success message for a completed fast-forward pull.

    ``behind_count`` is measured BEFORE the pull; ``pulled_lines`` is the ``git log --oneline``
    output measured AFTER. They should agree — a mismatch means the ref moved between the two
    measurements (most likely a concurrent session's own sync hook racing this one against the
    same shared canonical checkout), which is exactly the ambiguity that made dev-env#694 hard
    to root-cause after the fact. Surfacing it here means a future recurrence explains itself.

    A mismatch note is only printed when ``behind_count`` reflects a real measurement. At the
    call site, ``base == local`` and ``local != remote`` are already established, so a
    successful ``rev-list --count`` is always >= 1 here — ``behind_count == 0`` can only mean
    the measurement itself failed (``_count_from``'s fail-open sentinel), not a genuine "0
    commits behind". Blaming that on a concurrent process would misattribute a measurement
    failure as a race — review finding, PR #701.
    """
    count = len(pulled_lines)
    shown = pulled_lines[:5]
    trailer = f"  ... and {count - 5} more" if count > 5 else ""
    if behind_count == 0:
        note = "\n  (pre-pull commit-behind count could not be measured)"
    elif count != behind_count:
        note = (
            f"\n  (measured {behind_count} commit{_plural(behind_count)} behind before "
            "pulling — a concurrent process likely moved origin/main mid-pull)"
        )
    else:
        note = ""
    return (
        f"[dev-env-sync] Pulled {count} commit{_plural(count)} from origin/main "
        f"(local {local[:8]} -> origin/main {remote[:8]}) — CLAUDE.md and tooling are now "
        "current.\n"
        + "\n".join(f"  {line}" for line in shown)
        + (f"\n{trailer}" if trailer else "")
        + note
    )


# --- persistent fast-forward-failure escalation (dev-env#797) --------------------
# Pure helpers (unit-tested offline) plus best-effort scratch-state I/O. The read path mirrors
# _bash_state.read_state (None on malformed/missing, scratch= injection); the atomic tmp-file +
# os.replace write mirrors _hookutil.record_heartbeat (NOT _bash_state, whose write_state is a
# direct non-atomic write_text). State is a single repo-level file (not per-session) so a
# failure that spans sessions accumulates rather than resetting; see the module-level constants
# above and ADR-110.


def parse_blocking_files(git_stderr: str) -> "list[str]":
    """Extract the file paths git names as blocking a `--ff-only` pull, from its stderr.

    `git pull --ff-only` aborts with either of two shapes, both listing the offending paths
    one per line, tab-indented, between a header and a "Please ..." / "Aborting" line:

        error: Your local changes to the following files would be overwritten by merge:
        \tclaude/skills/sources.md
        Please commit your changes or stash them before you merge.
        Aborting

        error: The following untracked working tree files would be overwritten by merge:
        \tsomefile
        Please move or remove them before you merge.
        Aborting

    Returning every tab-indented line captures the path list from either variant while
    ignoring the surrounding prose (never tab-indented). Returns `[]` when git's failure
    names no files (a different failure mode) — the escalated message degrades gracefully.
    """
    files = []
    for line in git_stderr.splitlines():
        if line.startswith("\t"):
            stripped = line.strip()
            if stripped:
                files.append(stripped)
    return files


def format_duration(seconds: float) -> str:
    """Human-readable elapsed-time clause for the escalated failure message.

    ``< 60s`` -> "under a minute" (an escalation triggered by the consecutive-count arm
    rather than elapsed time can fire within seconds, and "0m" would read as broken);
    otherwise "Xh Ym" / "Xh" / "Ym". A negative input (a skewed/future first-failure
    timestamp) is clamped to 0 so the message never shows a nonsensical negative duration.
    """
    seconds = max(0.0, seconds)
    if seconds < 60:
        return "under a minute"
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def record_failure(prev: "dict | None", now: float) -> dict:
    """Return the updated failure-state dict after one more consecutive ff-pull failure.

    Pure — the caller persists the result via ``write_failure_state``. A fresh run (``prev``
    is ``None``, or its ``first_failure_at`` is missing/non-numeric) starts the clock at
    ``now`` with a count of 1; a genuine ongoing run preserves the original
    ``first_failure_at`` and increments the count. ``last_failure_at`` always advances to
    ``now``. A valid timestamp with a corrupt count keeps the (trustworthy) timestamp but
    conservatively restarts the count at 1 — the time-based escalation arm still fires off
    the real ``first_failure_at`` regardless.
    """
    first = None
    count = 0
    if isinstance(prev, dict):
        raw_first = prev.get("first_failure_at")
        if isinstance(raw_first, (int, float)) and not isinstance(raw_first, bool):
            first = float(raw_first)
        raw_count = prev.get("consecutive_count")
        if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0:
            count = raw_count
    if first is None:
        first = now
        count = 0  # no trustworthy start time -> treat as a fresh run
    return {
        "first_failure_at": first,
        "consecutive_count": count + 1,
        "last_failure_at": now,
    }


def should_escalate(
    state: dict,
    now: float,
    max_failures: int = ESCALATE_AFTER_CONSECUTIVE_FAILURES,
    max_hours: float = ESCALATE_AFTER_HOURS,
) -> bool:
    """Whether a persistent ff-pull failure warrants the escalated (louder) advisory.

    Escalate when the failure has recurred on ``>= max_failures`` consecutive prompts OR has
    persisted ``>= max_hours`` hours (OR semantics, per dev-env#797). The time arm is the
    robust one under concurrency: multiple sessions' hooks racing the shared state file can
    lose a count increment, but ``first_failure_at`` is set once and elapsed time accumulates
    regardless, so escalation still fires on schedule.
    """
    count = state.get("consecutive_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= max_failures:
        return True
    first = state.get("first_failure_at")
    if isinstance(first, (int, float)) and not isinstance(first, bool) and (now - first) >= max_hours * 3600:
        return True
    return False


def format_escalated_pull_failure_message(
    local: str,
    remote: str,
    behind: int,
    blocking_files: "list[str]",
    consecutive_count: int,
    seconds_failing: float,
    git_stderr: str,
) -> str:
    """Distinct, louder advisory for a *persistent* fast-forward-pull failure (dev-env#797).

    Unlike the one-off ``format_pull_failure_message``, this leads with the persistence
    (consecutive-prompt count + duration), states the STALE-tooling blast radius explicitly
    (``~/.claude/`` is junctioned to this checkout's working tree), lists the blocking file
    path(s) prominently, and points at the remediation precedent — then still echoes git's
    own diagnostic. Plain ASCII so it survives Claude Code's cp1252 hook-output pipe on
    Windows (ADR-098); delivered on stdout at exit 0, the model-visible channel for a
    UserPromptSubmit hook.
    """
    if blocking_files:
        files_block = "\n".join(f"    {f}" for f in blocking_files)
    else:
        files_block = "    (none named by git; see its diagnostic below)"
    # behind == 0 on this path can only mean the `git rev-list --count` measurement itself
    # failed (_count_from's fail-open sentinel) — a genuine count is always >= 1 here, since
    # `local != remote` and `base == local` are already established at the call site. Rendering
    # a literal "0 commits behind ... serving STALE tooling" would be self-contradictory, the
    # exact case PR #701 fixed in the two sibling formatters (review finding, PR #800).
    if behind == 0:
        behind_clause = "behind origin/main by an unmeasured number of commits"
    else:
        behind_clause = f"{behind} commit{_plural(behind)} behind origin/main"
    # "consecutive failing pulls" (not "prompts"): consecutive_count counts fast-forward-pull
    # failures, which is not literally every prompt — intervening fetch-failure / off-main
    # prompts don't reach the pull (review finding, PR #800). git's stderr is ascii_sanitized
    # before echoing for the same cp1252 reason as format_pull_failure_message above.
    return (
        "[dev-env-sync] PERSISTENT FAILURE: the canonical dev-env checkout has failed to "
        f"fast-forward on {consecutive_count} consecutive failing pull{_plural(consecutive_count)} "
        f"(failing for {format_duration(seconds_failing)}).\n"
        f"  It is {behind_clause} "
        f"(local {local[:8]} -> remote {remote[:8]}), so ~/.claude/ CLAUDE.md, hooks, and "
        "scripts are serving STALE tooling on this machine until this is resolved.\n"
        "  Blocked by uncommitted local change(s) to:\n"
        f"{files_block}\n"
        "  Resolve the listed file(s) so auto-fast-forward can resume - commit or stash them "
        "(dev-env#697 / #795 are the precedent for legitimate uncommitted sources.md / "
        "SKILL.md content). The next prompt then fast-forwards automatically.\n"
        "  Git's own diagnostic:\n"
        + _hookout.ascii_sanitize(git_stderr).strip()
    )


def build_failure_response(
    prev_state: "dict | None",
    now: float,
    local: str,
    remote: str,
    behind_count: int,
    git_stderr: str,
) -> "tuple[dict, str]":
    """Pure core of the ff-pull-failure branch: return ``(new_state, message)``.

    Given the prior on-disk state (``None`` on a first failure) and this failure's context,
    records one more failure, decides escalated-vs-plain, and returns the state to persist plus
    the advisory to print. ``main()`` does the read / write / print I/O around it. Extracted so
    the escalate-vs-plain decision — the feature's load-bearing logic — is unit-testable without
    git (review finding, PR #800); ``main()``'s remaining glue (``read_failure_state`` ->
    ``build_failure_response`` -> ``write_failure_state`` + ``print``) is trivial one-liners.
    """
    state = record_failure(prev_state, now)
    if should_escalate(state, now):
        message = format_escalated_pull_failure_message(
            local,
            remote,
            behind_count,
            parse_blocking_files(git_stderr),
            state["consecutive_count"],
            now - state["first_failure_at"],
            git_stderr,
        )
    else:
        message = format_pull_failure_message(local, remote, behind_count, git_stderr)
    return state, message


def failure_state_path(scratch: "Path | None" = None) -> Path:
    """Path to the single repo-level ff-failure state file. *scratch* overrides SCRATCH (tests)."""
    root = scratch if scratch is not None else SCRATCH
    return root / f"{FAILURE_STATE_PREFIX}.json"


def read_failure_state(scratch: "Path | None" = None) -> "dict | None":
    """Best-effort read of the persisted ff-failure state.

    Returns ``None`` on a missing / unreadable file (``OSError``), non-UTF-8 bytes
    (``UnicodeDecodeError``), or malformed / non-dict JSON (``json.JSONDecodeError``) — a first
    failure, a cleared file, or an externally-corrupted one is not an error; the caller starts
    a fresh run. Both decode failures are ``ValueError`` subclasses, so one
    ``except (OSError, ValueError)`` covers all three. (``_bash_state.read_state`` has the same
    shape but a narrower ``OSError``-only catch that a non-UTF-8 file would escape — review
    finding, PR #800.) *scratch* overrides SCRATCH (tests).
    """
    try:
        raw = failure_state_path(scratch).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_failure_state(state: dict, scratch: "Path | None" = None) -> None:
    """Best-effort atomic write of the ff-failure state (tmp file + os.replace).

    The atomic swap keeps a concurrent session's racing read from ever seeing a torn file,
    and a per-PID tmp name keeps two racing writers from clobbering each other's tmp (mirrors
    ``_hookutil.record_heartbeat``). Swallows all I/O errors — the state is an advisory
    side-channel, never a hard dependency. *scratch* overrides SCRATCH (tests).
    """
    root = scratch if scratch is not None else SCRATCH
    try:
        root.mkdir(parents=True, exist_ok=True)
        target = failure_state_path(root)
        tmp = root / f"{FAILURE_STATE_PREFIX}.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def clear_failure_state(scratch: "Path | None" = None) -> None:
    """Delete the ff-failure state file (a resolved pull -> fresh start on the next failure).

    Called on every up-to-date / successful-pull outcome. Best-effort; a missing file is a
    no-op. *scratch* overrides SCRATCH (tests).
    """
    try:
        failure_state_path(scratch).unlink(missing_ok=True)
    except OSError:
        pass


def main() -> None:
    _hookutil.record_heartbeat("dev-env-sync")
    try:
        sys.stdin.read()
    except Exception:
        pass

    # Best-effort backstop sweep of any stale ff-failure state file (dev-env#797). The state
    # file self-clears on the next up-to-date/successful pull, so the .json sweep only matters
    # for a machine abandoned mid-failure and never prompted again for 30+ days. The second
    # sweep reaps an orphaned atomic-write tmp (dev_env_sync_ff_failure.json.<pid>.tmp) left by
    # a rare os.replace failure (a Windows sharing violation when a concurrent session is
    # mid-read), which the .json glob cannot match; 30 days is safely longer than any in-flight
    # write, so a concurrent writer's live tmp is never swept (review finding, PR #800).
    _hookutil.cleanup_stale_sentinels(FAILURE_STATE_PREFIX, ext=".json")
    _hookutil.cleanup_stale_sentinels(FAILURE_STATE_PREFIX, ext=".tmp")

    # Guard: repo must exist at the expected path.
    if not DEV_ENV_REPO.is_dir():
        sys.exit(0)

    # The canonical must stay on main — ~/.claude/ symlinks serve its working tree, so a
    # feature branch there hides newly-merged hooks/scripts. When it's off main we diagnose
    # the worktree topology below and auto-correct (clean canonical -> back to main) or warn
    # precisely (squatter holding main, or dirty drift to preserve); see ADR-058.
    #
    # A non-zero returncode means detached HEAD, not "command failed" — resolve_current_branch
    # routes it to the "<detached>" sentinel so it falls into the SAME diagnostic block below
    # instead of exiting silently here. diagnose_main_topology/canonical_sync_action already
    # handle "<detached>" correctly (main_squatter's bare/detached guard); the gap was purely
    # that nothing used to route a detached canonical into them (dev-env#619).
    branch = run(["git", "symbolic-ref", "--short", "HEAD"])
    current_branch = resolve_current_branch(branch.returncode, branch.stdout)
    if current_branch != "main":
        # Off main is a determinate not-actively-ff-pull-failing state (a different problem, or
        # a topological one that pauses the pull) — end any in-progress ff-failure run so a
        # later, unrelated failure doesn't inherit a stale first_failure_at/count and escalate
        # with a bogus multi-hour duration (review finding, PR #800). A genuinely still-dirty
        # file re-accumulates a fresh run once the canonical is back on main and the pull fails
        # again; a conservative under-report is the right bias for an escalation signal.
        clear_failure_state()
        # The canonical is off main — diagnose the worktree topology so we can either
        # auto-return a clean canonical to main (restoring the ~/.claude symlinks) or warn
        # precisely. A non-canonical worktree may be squatting main (gh's --delete-branch
        # checked it out there), which blocks the canonical's return entirely (dev-env#396,
        # ADR-058). This is the rare/broken path, so the extra git calls are cheap.
        wt = run(["git", "worktree", "list", "--porcelain"])
        if wt.returncode != 0:
            # Topology undeterminable — don't auto-correct on incomplete data (a silent []
            # would misdiagnose as "dirty drift", review finding). Emit the plain off-main
            # warning and let the user switch back manually.
            print(
                f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}' and its worktree "
                "list could not be read - ~/.claude/ symlinks may serve stale hooks/scripts.\n"
                f"Switch it back manually: git -C {DEV_ENV_REPO} checkout main"
            )
            sys.exit(0)
        worktrees = parse_worktree_porcelain(wt.stdout)
        topo = diagnose_main_topology(worktrees)
        status = run(["git", "status", "--porcelain"])
        canonical_clean = status.returncode == 0 and not status.stdout.strip()
        action = canonical_sync_action(topo, canonical_clean)

        if action.kind == "warn-squatter":
            print(
                f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}' and worktree\n"
                f"  {action.squatter_path}\n"
                "is squatting 'main' - the canonical cannot return until that worktree is parked\n"
                "off main. Free the ref (non-destructive - changes no files):\n"
                f"  git -C {action.squatter_path} checkout -b {action.park_branch}\n"
                "then the next prompt returns the canonical to main automatically (or run\n"
                f"  git -C {DEV_ENV_REPO} checkout main\n"
                "). Until then ~/.claude/ symlinks may serve stale hooks/scripts."
            )
            sys.exit(0)

        if action.kind == "warn-dirty":
            print(
                f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}' with uncommitted\n"
                "changes - ~/.claude/ symlinks may serve stale hooks/scripts. Not auto-switching to\n"
                "preserve your drift; commit or stash, then:\n"
                f"  git -C {DEV_ENV_REPO} checkout main"
            )
            sys.exit(0)

        if action.kind == "return-canonical":
            checkout = run(["git", "checkout", "main"])
            if checkout.returncode != 0:
                print(
                    f"[dev-env-sync] WARNING: Canonical worktree is on '{current_branch}'; auto-return to\n"
                    f"main failed:\n{checkout.stderr.strip()}\n"
                    "Switch it back manually so symlinked tooling is current."
                )
                sys.exit(0)
            print(
                f"[dev-env-sync] Returned canonical worktree to main (was on '{current_branch}') - "
                "symlinked tooling restored."
            )
            current_branch = "main"
        # "on-main" cannot occur on this path (current_branch != "main"); fall through to pull.

    # Fetch quietly so the local remote-tracking ref is current.
    fetch = run(["git", "fetch", "origin", "main", "--quiet"])
    if fetch.returncode != 0:
        # Network issue — don't block, don't spam on every turn.
        sys.exit(0)

    # Compare local main to origin/main.
    rev_local = run(["git", "rev-parse", "refs/heads/main"])
    rev_remote = run(["git", "rev-parse", "origin/main"])
    if rev_local.returncode != 0 or rev_remote.returncode != 0:
        sys.exit(0)

    local = rev_local.stdout.strip()
    remote = rev_remote.stdout.strip()

    if local == remote:
        # Already up-to-date — clear any persisted failure run (dev-env#797).
        clear_failure_state()
        sys.exit(0)

    # Check if local main is an ancestor of origin/main (fast-forward possible).
    merge_base = run(["git", "merge-base", "refs/heads/main", "origin/main"])
    if merge_base.returncode != 0:
        sys.exit(0)

    base = merge_base.stdout.strip()

    # How far behind local is right now, measured once up front so it's available to every
    # downstream message (success, diverged, or failed-pull) without repeating the git call in
    # three different branches, and so a failed pull still names the gap it couldn't close
    # (dev-env#694).
    behind_count = _count_from(run(["git", "rev-list", "--count", f"{local}..{remote}"]))

    if base != local:
        # Local main has commits not on origin/main — diverged. A determinate non-ff-failure
        # state, so end any in-progress ff-failure run (review finding, PR #800).
        clear_failure_state()
        ahead_count = _count_from(run(["git", "rev-list", "--count", f"{remote}..{local}"]))
        print(format_diverged_message(local, remote, behind_count, ahead_count))
        sys.exit(0)

    # Fast-forward is safe — pull.
    pull = run(["git", "pull", "--ff-only", "origin", "main"])
    if pull.returncode == 0:
        # Count how many commits were pulled.
        log = run(["git", "log", "--oneline", f"{local}..HEAD"])
        lines = [line for line in log.stdout.strip().splitlines() if line]
        print(format_pulled_message(local, remote, behind_count, lines))
        clear_failure_state()  # resolved — reset the persistence counter (dev-env#797)
    else:
        # A failed fast-forward is almost always a dirty tracked file conflicting with an
        # incoming commit — a condition that NEVER self-heals (unlike a transient concurrent
        # pull, which resolves to up-to-date on the next prompt and clears the state above).
        # Track it across prompts/sessions and escalate a persistent one to a distinct, louder
        # advisory, instead of the same-severity per-prompt warning that let the canonical drift
        # 21 commits / ~41h unnoticed (dev-env#697, #795, #797, ADR-110). The escalate-vs-plain
        # decision lives in the pure build_failure_response helper; main() only does the
        # read/write/print glue around it.
        now = time.time()
        state, message = build_failure_response(
            read_failure_state(), now, local, remote, behind_count, pull.stderr
        )
        write_failure_state(state)
        print(message)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Honor the "Exit 0 always" docstring contract even against an unexpected subprocess
        # failure (timeout, git missing from PATH, etc.) — matches the established fail-open
        # convention for UserPromptSubmit hooks touching this canonical
        # (journal-canonical-guard.py, new-day-journal-check.py; review finding, PR #661).
        # Unlike those two, this hook's whole purpose is eliminating invisible failures, so
        # print a minimal pure-ASCII notice (never a formatted/dynamic value that could itself
        # raise) rather than fail open in total silence — review finding, PR #701.
        try:
            print("[dev-env-sync] WARNING: sync check failed unexpectedly and was skipped this prompt.")
        except Exception:
            pass
        sys.exit(0)
