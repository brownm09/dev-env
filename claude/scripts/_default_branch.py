#!/usr/bin/env python3
"""Find a repo's default branch on origin and fetch it, without raising on a hung remote.

Shared by prune-merged-worktrees.py and reclaim-worktree-disk.py, which both compare worktrees
against the default branch and both sweep many repos in --scan-dir mode. A repo whose default
branch isn't main used to fail its fetch on every run (dev-env#1088), and a fetch that hung past
its timeout ended the whole sweep instead of being reported like a failed one (dev-env#1087).

Each script calls fetch_default_branch() once per repo, so the answer is held for that repo's
pass and no separate cache is needed.
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import subprocess
from typing import NamedTuple

# Used for the merge checks only when discovery fails, which is what the scripts always assumed.
FALLBACK_BRANCH = "main"
FAILED = "failed"
TIMED_OUT = "timed out"

_ORIGIN_HEAD_PREFIX = "refs/remotes/origin/"
_LS_REMOTE_PREFIX = "ref: refs/heads/"


class FetchResult(NamedTuple):
    branch: str  # the default branch, or FALLBACK_BRANCH when it could not be found
    problem: "str | None"  # None, FAILED or TIMED_OUT
    detail: str  # what went wrong, for the caller's warning


def _git(args: "list[str]", repo: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)


def _discover(repo: str) -> "tuple[str | None, str]":
    """Return (default branch or None, stderr of the failed lookup). May raise TimeoutExpired.

    refs/remotes/origin/HEAD is local and cheap, but `git clone` is the only thing that sets it,
    so unset is a normal case: ask the remote. `git ls-remote --symref` is used rather than
    `git remote show` because its output is meant for parsing and isn't translated.
    """
    r = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], repo)
    ref = r.stdout.strip()
    if r.returncode == 0 and ref.startswith(_ORIGIN_HEAD_PREFIX):
        return ref[len(_ORIGIN_HEAD_PREFIX):], ""

    r = _git(["ls-remote", "--symref", "origin", "HEAD"], repo)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if line.startswith(_LS_REMOTE_PREFIX) and line.endswith("\tHEAD"):
                return line[len(_LS_REMOTE_PREFIX):-len("\tHEAD")], ""
    return None, r.stderr.strip()


def fetch_default_branch(repo: str) -> FetchResult:
    """Fetch origin's default branch. Never raises TimeoutExpired, so one hung remote is one result."""
    try:
        branch, error = _discover(repo)
    except subprocess.TimeoutExpired:
        return FetchResult(FALLBACK_BRANCH, TIMED_OUT, "looking up the default branch timed out")
    if branch is None:
        return FetchResult(FALLBACK_BRANCH, FAILED, f"could not find the default branch: {error}")

    try:
        r = _git(["fetch", "origin", branch], repo)
    except subprocess.TimeoutExpired:
        return FetchResult(branch, TIMED_OUT, "git fetch timed out")
    if r.returncode != 0:
        return FetchResult(branch, FAILED, r.stderr.strip())
    return FetchResult(branch, None, "")
