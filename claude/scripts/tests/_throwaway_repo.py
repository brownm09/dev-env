"""Throwaway git repo for running a worktree-maintenance script for real (dev-env#1310).

Builds, under a temp directory and with no network:

    <root>/github.com/owner/repo.git   bare origin; the path satisfies detect_gh_repo()
    <root>/repo                        primary checkout on the default branch (main unless
                                       the caller names another), pushed to origin
    <root>/repo/.claude/worktrees/wt   clean worktree on claude/merged, at that branch's
                                       commit, so it is merged by ancestry and `gh` is never
                                       called

refs/remotes/origin/HEAD is left unset, as `git remote add` leaves it. set_origin_head() sets it.

The worktree holds a gitignored node_modules/ so reclaim-worktree-disk.py can see it as
clean and eligible. run_script() always passes --repo-path, but that flag is parsed by the
code under test, so a test must call assert_targets_repo() on a --dry-run's output before
any live run: a script that ignored --repo-path would otherwise act on the real dev-env repo.
"""
import contextlib
import io
import os
import subprocess
import unittest.mock
import sys
import time
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
    def __init__(self, root: Path, default_branch: str = "main") -> None:
        self.root = root.resolve()
        self.default_branch = default_branch
        self.origin = self.root / "github.com" / "owner" / "repo.git"
        self.primary = self.root / "repo"
        self.worktree = self.primary / ".claude" / "worktrees" / "wt"
        self.branch = "claude/merged"
        self.node_modules = self.worktree / "node_modules"
        self.fetch_head = self.primary / ".git" / "FETCH_HEAD"
        self.admin_dir = self.primary / ".git" / "worktrees" / "wt"
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
        self._git(self.root, "init", "--bare", "-b", default_branch, str(self.origin))
        self._git(self.primary, "init", "-b", default_branch)
        self._git(self.primary, "config", "core.hooksPath", str(no_hooks))
        self._git(self.primary, "config", "user.name", "test")
        self._git(self.primary, "config", "user.email", "test@example.invalid")
        (self.primary / ".gitignore").write_text("node_modules/\n.claude/\n", encoding="utf-8")
        self._git(self.primary, "add", ".gitignore")
        self._git(self.primary, "commit", "-m", "init")
        self._git(self.primary, "remote", "add", "origin", self.origin.as_posix())
        self._git(self.primary, "push", "origin", default_branch)
        self._git(self.primary, "worktree", "add", str(self.worktree), "-b", self.branch)
        self.node_modules.mkdir()
        (self.node_modules / "dep.js").write_text("x", encoding="utf-8")

    def _git(self, cwd: Path, *args: str) -> None:
        r = subprocess.run(
            ["git", *args], cwd=cwd, env=self._env, capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")

    def set_origin_head(self, branch: str) -> None:
        self._git(self.primary, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")

    def add_worktree_on_default_branch(self) -> Path:
        """Check the default branch out in a second worktree; the primary must have left it."""
        path = self.primary / ".claude" / "worktrees" / "on-default"
        self._git(self.primary, "worktree", "add", str(path), self.default_branch)
        return path

    def git(self, *args: str) -> None:
        self._git(self.primary, *args)

    def age_git_state(self, seconds: float = 7 * 24 * 60 * 60, admin_dir: "Path | None" = None) -> float:
        """Date a worktree's git state back, as if idle since (default: the first worktree's).
        Returns the new mtime."""
        stamp = time.time() - seconds
        admin_dir = admin_dir or self.admin_dir
        for path in (admin_dir, *admin_dir.iterdir()):
            os.utime(path, (stamp, stamp))
        return stamp

    def current_branch(self, worktree: Path) -> str:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=worktree, env=self._env, capture_output=True, text=True, timeout=60,
        )
        return r.stdout.strip()

    def branch_exists(self, branch: str = "") -> bool:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch or self.branch}"],
            cwd=self.primary, env=self._env, capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0

    def assert_targets_repo(self, result: subprocess.CompletedProcess) -> None:
        """Stop the test unless a --dry-run's output shows it acted on this throwaway repo."""
        if f"({self.primary})" not in result.stdout:
            raise AssertionError(
                f"run did not target {self.primary}; refusing to continue to a live run\n{result.stdout}"
            )

    def add_worktree(self, name: str, branch: str) -> Path:
        """Another clean, merged worktree like the first one, with its own node_modules."""
        path = self.primary / ".claude" / "worktrees" / name
        self._git(self.primary, "worktree", "add", str(path), "-b", branch)
        (path / "node_modules").mkdir()
        (path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
        return path

    def age_all_git_state(self, seconds: float = 7 * 24 * 60 * 60) -> None:
        """age_git_state() for every worktree, past the recent-activity guard."""
        for admin in (self.primary / ".git" / "worktrees").iterdir():
            self.age_git_state(seconds, admin)

    def run_main_failing_on(self, module, fail_on: str, *args: str,
                            only_for: "tuple[str, ...]" = (),
                            strict_cp1252: bool = False) -> "tuple[str, str, int]":
        """Run module.main() in this process with every git call naming branch fail_on raising
        OSError: as a whole argument, or as the end of a `<base>..<branch>` range. With
        only_for, just the git subcommand starting with those words fails. Pass fail_on="" for
        no failure. With strict_cp1252, stdout and stderr are cp1252 streams that raise on a
        character they can't encode, like a Windows console or pipe without PYTHONIOENCODING
        (dev-env#1358). Returns (stdout, stderr, exit code).

        A --dry-run of the same command, with nothing failing, must first show it targets this
        repo: args are parsed by the code under test. A module's default last-run state file is
        moved under this repo, so no call can read or write the real one.
        """
        real_run = subprocess.run

        def run(cmd, *a, **k):
            names_branch = any(part == fail_on or str(part).endswith(f"..{fail_on}") for part in cmd)
            if fail_on and names_branch and list(cmd[1:1 + len(only_for)]) == list(only_for):
                raise OSError(f"injected failure for {fail_on}")
            return real_run(cmd, *a, **k)

        state_file = self.root / "last-run-state.json"
        if not hasattr(module, "DEFAULT_STATE_FILE"):
            state_file = None

        def stream(strict: bool):
            if strict:
                return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
            return io.StringIO()

        def text(s) -> str:
            if isinstance(s, io.StringIO):
                return s.getvalue()
            s.flush()
            return s.buffer.getvalue().decode("cp1252")

        def main(*argv: str, inject: bool) -> "tuple[str, str, int]":
            out, err, code = stream(inject and strict_cp1252), stream(inject and strict_cp1252), 0
            with unittest.mock.patch.object(module, "DEFAULT_STATE_FILE", state_file) if state_file \
                    else contextlib.nullcontext(), \
                    unittest.mock.patch.dict(os.environ, self._script_env, clear=True), \
                    unittest.mock.patch.object(sys, "argv", ["script", *argv]), \
                    unittest.mock.patch("subprocess.run", side_effect=run if inject else real_run), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    module.main()
                except SystemExit as exc:
                    code = exc.code or 0
            return text(out), text(err), code

        dry_out, _, _ = main(*args, "--dry-run", inject=False)
        # A --scan-dir run past its baseline prints no per-repo header, only the directory.
        if f"repos under {self.root}\n" not in dry_out:
            self.assert_targets_repo(subprocess.CompletedProcess([], 0, dry_out, ""))
        return main(*args, inject=True)

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        """Run script against this repo. cwd is the root, so no worktree is the current one."""
        return subprocess.run(
            [sys.executable, str(script), *args, "--repo-path", str(self.primary)],
            cwd=self.root, env=self._script_env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
