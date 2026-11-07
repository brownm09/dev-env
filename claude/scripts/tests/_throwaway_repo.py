"""Throwaway git repo for running a worktree-maintenance script for real (dev-env#1310).

Builds, under a temp directory and with no network:

    <root>/github.com/owner/repo.git   bare origin; the path satisfies detect_gh_repo()
    <root>/repo                        primary checkout on main, pushed to origin
    <root>/repo/.claude/worktrees/wt   clean worktree on claude/merged, at main's commit,
                                       so it is merged by ancestry and `gh` is never called

The worktree holds a gitignored node_modules/ so reclaim-worktree-disk.py can see it as
clean and eligible. Pass --repo-path repo.primary on every run: a script that wrongly goes
live then only touches this directory, not the real dev-env repo it defaults to.
"""
import os
import subprocess
import sys
from pathlib import Path


# Set when these tests run from inside a git hook, and they would point the script's git
# commands at the real repo instead of the throwaway one.
_GIT_ENV_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_VARS}


class ThrowawayRepo:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.origin = self.root / "github.com" / "owner" / "repo.git"
        self.primary = self.root / "repo"
        self.worktree = self.primary / ".claude" / "worktrees" / "wt"
        self.branch = "claude/merged"
        self.node_modules = self.worktree / "node_modules"
        self.fetch_head = self.primary / ".git" / "FETCH_HEAD"

        # The machine's global core.hooksPath hooks must not run against this repo.
        no_hooks = self.root / "no-hooks"
        no_hooks.mkdir()
        self._git_prefix = [
            "git", "-c", f"core.hooksPath={no_hooks}",
            "-c", "user.name=test", "-c", "user.email=test@example.invalid",
        ]

        self.origin.parent.mkdir(parents=True)
        self.primary.mkdir()
        self._git(self.root, "init", "--bare", "-b", "main", str(self.origin))
        self._git(self.primary, "init", "-b", "main")
        (self.primary / ".gitignore").write_text("node_modules/\n.claude/\n", encoding="utf-8")
        self._git(self.primary, "add", ".gitignore")
        self._git(self.primary, "commit", "-m", "init")
        self._git(self.primary, "remote", "add", "origin", self.origin.as_posix())
        self._git(self.primary, "push", "origin", "main")
        self._git(self.primary, "worktree", "add", str(self.worktree), "-b", self.branch)
        self.node_modules.mkdir()
        (self.node_modules / "dep.js").write_text("x", encoding="utf-8")

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(
            [*self._git_prefix, *args], cwd=cwd, env=_env(), check=True,
            capture_output=True, text=True, timeout=60,
        )

    def branch_exists(self) -> bool:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}"],
            cwd=self.primary, env=_env(), capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        """Run script against this repo. cwd is the root, so no worktree is the current one."""
        return subprocess.run(
            [sys.executable, str(script), *args, "--repo-path", str(self.primary)],
            cwd=self.root, env={**_env(), "PYTHONIOENCODING": "utf-8"}, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=120,
        )
