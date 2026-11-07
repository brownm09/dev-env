"""Throwaway git repo for running a worktree-maintenance script for real (dev-env#1310).

Builds, under a temp directory and with no network:

    <root>/github.com/owner/repo.git   bare origin; the path satisfies detect_gh_repo()
    <root>/repo                        primary checkout on main, pushed to origin
    <root>/repo/.claude/worktrees/wt   clean worktree on claude/merged, at main's commit,
                                       so it is merged by ancestry and `gh` is never called

The worktree holds a gitignored node_modules/ so reclaim-worktree-disk.py can see it as
clean and eligible. run_script() always passes --repo-path, but that flag is parsed by the
code under test, so a test must call assert_targets_repo() on a --dry-run's output before
any live run: a script that ignored --repo-path would otherwise act on the real dev-env repo.
"""
import os
import subprocess
import sys
from pathlib import Path


def _env() -> dict:
    """os.environ without git's repo-local variables.

    Set when these tests run from inside a git hook, they would point the script's git
    commands at the real repo instead of the throwaway one.
    """
    env = dict(os.environ)
    local_vars = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.split()
    for name in local_vars:
        env.pop(name, None)
    return env


class ThrowawayRepo:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.origin = self.root / "github.com" / "owner" / "repo.git"
        self.primary = self.root / "repo"
        self.worktree = self.primary / ".claude" / "worktrees" / "wt"
        self.branch = "claude/merged"
        self.node_modules = self.worktree / "node_modules"
        self.fetch_head = self.primary / ".git" / "FETCH_HEAD"
        self._env = _env()
        # An empty config dir: the liveness guard must not match a real transcript whose
        # worktree happens to share this fixture's short name.
        self._script_env = {
            **self._env,
            "PYTHONIOENCODING": "utf-8",
            "CLAUDE_CONFIG_DIR": str(self.root / "claude-config"),
        }

        self.origin.parent.mkdir(parents=True)
        self.primary.mkdir()
        (self.root / "claude-config").mkdir()
        # Local config, so the script's own git commands also skip the machine's global hooks.
        no_hooks = self.root / "no-hooks"
        no_hooks.mkdir()
        self._git(self.root, "init", "--bare", "-b", "main", str(self.origin))
        self._git(self.primary, "init", "-b", "main")
        self._git(self.primary, "config", "core.hooksPath", str(no_hooks))
        self._git(self.primary, "config", "user.name", "test")
        self._git(self.primary, "config", "user.email", "test@example.invalid")
        (self.primary / ".gitignore").write_text("node_modules/\n.claude/\n", encoding="utf-8")
        self._git(self.primary, "add", ".gitignore")
        self._git(self.primary, "commit", "-m", "init")
        self._git(self.primary, "remote", "add", "origin", self.origin.as_posix())
        self._git(self.primary, "push", "origin", "main")
        self._git(self.primary, "worktree", "add", str(self.worktree), "-b", self.branch)
        self.node_modules.mkdir()
        (self.node_modules / "dep.js").write_text("x", encoding="utf-8")

    def _git(self, cwd: Path, *args: str) -> None:
        r = subprocess.run(
            ["git", *args], cwd=cwd, env=self._env, capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")

    def branch_exists(self) -> bool:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}"],
            cwd=self.primary, env=self._env, capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0

    def assert_targets_repo(self, result: subprocess.CompletedProcess) -> None:
        """Stop the test unless a --dry-run's output shows it acted on this throwaway repo."""
        if f"({self.primary})" not in result.stdout:
            raise AssertionError(
                f"run did not target {self.primary}; refusing to continue to a live run\n{result.stdout}"
            )

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        """Run script against this repo. cwd is the root, so no worktree is the current one."""
        return subprocess.run(
            [sys.executable, str(script), *args, "--repo-path", str(self.primary)],
            cwd=self.root, env=self._script_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
