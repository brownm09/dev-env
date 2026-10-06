#!/usr/bin/env python3
"""dev-env-doctor -- a read-only health check of the dev-env install on this machine.

Run it after `bash setup.sh` on a new machine (setup runs it for you), or any time the
tooling misbehaves:

    py -3 ~/.claude/scripts/dev-env-doctor.py [--offline] [--settings PATH]

It prints one line per check -- PASS, WARN, FAIL or INFO -- then a summary, and exits 1
when any check FAILs, 0 otherwise. It changes nothing: no fetch, no config write, no file
write (child output is captured in anonymous temp files). `--offline` skips the two
network reads: `gh auth status`'s scope check, and `git ls-remote` for today's journal
draft branch. Every child runs with prompts off and stdin closed, and a timeout kills its
whole process tree -- setup runs the doctor from agent sessions, where nobody can answer a
credential dialog.

What it checks, and why each one matters on a second machine (dev-env#1114, part of #1107):

- The ~/.claude links (the set setup.sh creates) and ~/bin exist, resolve, and point into
  the dev-env checkout, and that checkout is ~/Git/dev-env on `main` -- dev-env-sync.py
  and session-start-sync.py assume both. A dangling link FAILs: realpath() still returns
  its missing target, which would otherwise compare equal to the expected one.
- Every hook command in the live ~/.claude/settings.json names a script that exists. A
  missing script exits 2, which blocks every prompt or tool call it is wired to. The check
  asserts it extracted at least one script path, so an unreadable or unparseable settings
  file FAILs instead of passing vacuously (ADR-144).
- The tools the workflow shells out to: the hook launcher (pyw), py, git, gh, node -- plus
  gh sign-in (decided locally by `gh auth token`) and its `project` scope, the gh
  git-credential helper (ADR-047) and git identity.
- Every script in claude/scripts/ compiles on this machine's Python. A compile warning (an
  invalid escape: SyntaxWarning from 3.12, DeprecationWarning before) is a WARN, because a
  future Python makes it an error.
- The global core.hooksPath exists, and every clone under ~/Git whose effective hooks
  directory is not the global one -- those repos silently skip the ADR-005 pre-push
  guards (#1108).
- ~/Git/engineering-journal exists and contains origin/draft/<today> when another machine
  or session already pushed it (#1111).
- dev-env's gitignored .claude/hook-config.json, which the project-board hook needs.
- This machine's routine-host flag in ~/.claude/machine.json (#1110) -- informational.

The decisions live in pure `*_result` / `*_results` helpers, so the tests are fixture-only
and never read this machine's real state; `collect()` does the I/O around them, and a check
that crashes becomes a FAIL line instead of hiding the rest of the report.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

import _winsubp  # noqa: F401  -- UTF-8 decoding + no console flash for child processes (ADR-007)
import _repo_scan

# The settings.json hook walker the three settings gates share (Testing items 61-63; the
# doctor is its one runtime consumer, so item 61 names item 101 too). It lives in tests/ so the
# gates' own claude/scripts/*.py glob never mistakes it for a hook. Appended, not prepended, so
# nothing in tests/ can shadow a module the doctor imports.
sys.path.append(str(Path(__file__).resolve().parent / "tests"))
from _hook_wiring import hook_entries  # noqa: E402

HOME = Path.home()
CLAUDE_HOME = HOME / ".claude"
GIT_ROOT = HOME / "Git"
EXPECTED_DEV_ENV = GIT_ROOT / "dev-env"
JOURNAL = GIT_ROOT / "engineering-journal"

# What setup.sh links: CLAUDE_FILE_LINKS + CLAUDE_DIR_LINKS + CLAUDE_JUNCTION_LINKS into
# ~/.claude, and HOME_LINKS into the home directory. test_dev_env_doctor.py pins both against
# setup.sh's arrays.
LINKED_ITEMS = ("CLAUDE.md", "scripts", "skills", "hooks", "templates", "routines")
HOME_LINKS = ("bin",)

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"

# The script a hook command runs: its last token ending in .py, optionally double-quoted
# (a home path containing a space). Matches the `pyw -3 C:/.../foo.py` form (ADR-007).
_SCRIPT_PATH_RE = re.compile(r'(?:"([^"]+\.py)"|(\S+\.py))\s*$')

_SHA_RE = re.compile(r"[0-9a-f]{40}")


class Result(NamedTuple):
    status: str  # PASS / WARN / FAIL / INFO
    check: str
    detail: str


def same_path(a: object, b: object) -> bool:
    """Path equality that ignores slash direction, and letter case on Windows."""
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def script_path_from_command(command: str) -> str | None:
    """The script path a hook command runs, or None when it names no .py file."""
    m = _SCRIPT_PATH_RE.search(command.strip())
    if not m:
        return None
    return m.group(1) or m.group(2)


def home_prefix(script_path: str) -> str | None:
    """The part of a hook script path before `/.claude/` -- e.g. `C:/Users/brown`."""
    norm = script_path.replace("\\", "/")
    idx = norm.lower().find("/.claude/")
    return norm[:idx] if idx > 0 else None


def parse_ls_remote(stdout: str, ref: str) -> str:
    """origin's SHA for <ref> from `git ls-remote` stdout, or "" when origin has no such ref.
    Only a `<40-hex>\\t<ref>` line counts, so stray text can't pose as a branch."""
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref and _SHA_RE.fullmatch(parts[0]):
            return parts[0]
    return ""


# --------------------------------------------------------------------------- decisions


def link_results(
    claude_home: Path,
    home: Path,
    repo_root: Path,
    resolve: Callable[[str], str] = os.path.realpath,
    lexists: Callable[[str], bool] = os.path.lexists,
    exists: Callable[[str], bool] = os.path.exists,
) -> list[Result]:
    """Every link setup.sh creates must exist, resolve, and point into repo_root."""
    expected = [(claude_home / name, repo_root / "claude" / name) for name in LINKED_ITEMS]
    expected += [(home / name, repo_root / name) for name in HOME_LINKS]
    wrong: list[str] = []
    for link, target in expected:
        if not lexists(str(link)):
            wrong.append(f"{link} is missing")
            continue
        real = resolve(str(link))
        if not exists(str(link)):
            # Dangling: realpath() returns the missing target, which would compare equal.
            wrong.append(f"{link} dangles -> {real}, which does not exist")
            continue
        if same_path(real, resolve(str(target))):
            continue
        if same_path(real, link):
            wrong.append(f"{link} is a real file or directory, not a link (re-run setup.sh; it backs the original up first)")
        else:
            wrong.append(f"{link} resolves to {real}, expected {target}")
    if wrong:
        return [Result(FAIL, "links", f"{len(wrong)} of {len(expected)} wrong: " + "; ".join(wrong))]
    return [Result(PASS, "links", f"all {len(expected)} ~/.claude links and ~/bin resolve into {repo_root}")]


def checkout_results(
    repo_root: Path,
    expected: Path,
    branch: str | None,
    is_dir: Callable[[str], bool] = os.path.isdir,
    resolve: Callable[[str], str] = os.path.realpath,
) -> list[Result]:
    """The checkout the links point into should exist, be ~/Git/dev-env, and be on main."""
    if not is_dir(str(repo_root)):
        return [Result(
            FAIL, "dev-env checkout",
            f"{repo_root} does not exist -- re-run setup.sh from the clone you use",
        )]
    out: list[Result] = []
    # Both sides resolved: a junction anywhere in ~/Git's ancestry is still the same checkout.
    if same_path(resolve(str(repo_root)), resolve(str(expected))):
        out.append(Result(PASS, "dev-env checkout", str(repo_root)))
    else:
        out.append(Result(
            WARN, "dev-env checkout",
            f"links point into {repo_root}, but dev-env-sync.py and session-start-sync.py expect {expected}",
        ))
    if branch is None:
        out.append(Result(WARN, "dev-env branch", "could not read the checkout's current branch"))
    elif branch == "main":
        out.append(Result(PASS, "dev-env branch", "main"))
    else:
        out.append(Result(
            WARN, "dev-env branch",
            f"on '{branch or 'detached HEAD'}' -- the canonical checkout must stay on main, "
            "or newly merged hooks and skills stay invisible",
        ))
    return out


def hook_command_results(
    settings: dict | None,
    settings_path: Path,
    home: Path,
    is_file: Callable[[str], bool] = os.path.isfile,
    which: Callable[[str], str | None] = shutil.which,
) -> list[Result]:
    """Every wired hook command must name an existing script and an available launcher."""
    reseed = "re-seed it: py -3 <dev-env>/claude/scripts/_settings_sync.py"
    if settings is None:
        return [Result(FAIL, "hook commands", f"cannot read {settings_path} (missing or not JSON) -- {reseed}")]
    entries = hook_entries(settings)
    if not entries:
        return [Result(
            FAIL, "hook commands",
            f"no hook commands found in {settings_path}; checking zero commands would pass vacuously -- {reseed}",
        )]
    missing: list[str] = []
    unparsed: list[str] = []
    scripts: set[str] = set()
    launchers: set[str] = set()
    checked = 0
    for entry in entries:
        tokens = entry.command.split()
        if tokens:
            launchers.add(tokens[0])
        path = script_path_from_command(entry.command)
        if path is None:
            unparsed.append(entry.command)
            continue
        checked += 1
        scripts.add(path)
        if not is_file(path):
            missing.append(path)

    out: list[Result] = []
    if not scripts:
        # Entries alone aren't enough: a check that read no script path checked nothing.
        out.append(Result(
            FAIL, "hook commands",
            f"none of the {len(entries)} hook commands names a .py script this check can read, so it "
            f"checked nothing -- a vacuous pass otherwise; e.g. {entries[0].command[:80]}",
        ))
    elif missing:
        unique = sorted(set(missing))
        detail = (
            f"{len(missing)} of {checked} checked hook commands name a missing script "
            f"(every prompt or tool call they are wired to is blocked), e.g. {', '.join(unique[:3])}"
        )
        prefix = home_prefix(unique[0])
        if prefix and not same_path(prefix, home):
            detail += f" -- they point under {prefix}, but this machine's home is {home} (dev-env#1113)"
        out.append(Result(FAIL, "hook commands", detail))
    else:
        out.append(Result(
            PASS, "hook commands", f"{checked} commands checked, {len(scripts)} distinct scripts, all present",
        ))
    if unparsed and scripts:
        out.append(Result(
            WARN, "hook commands",
            f"{len(unparsed)} command(s) name no .py script, so were not checked, e.g. {unparsed[0][:80]}",
        ))
    for launcher in sorted(launchers):
        if which(launcher) is None:
            out.append(Result(
                FAIL, "hook launcher",
                f"'{launcher}' is not on PATH, and every hook command starts with it (ADR-007)",
            ))
    return out


def compile_results(paths: Iterable[Path]) -> list[Result]:
    """Every script must compile here; a compile warning is tomorrow's SyntaxError."""
    errors: list[str] = []
    compile_warnings: list[str] = []
    count = 0
    for path in sorted(paths):
        count += 1
        try:
            # Bytes, as the interpreter reads them: a UTF-8 BOM or coding cookie is honored.
            source = path.read_bytes()
        except OSError as exc:
            errors.append(f"{path.name}: unreadable ({exc})")
            continue
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                errors.append(f"{path.name}:{exc.lineno}: {exc.msg}")
                continue
            except ValueError as exc:  # a NUL byte, on Python 3.11 and earlier
                errors.append(f"{path.name}: {exc}")
                continue
        for w in caught:
            # An invalid escape warns as SyntaxWarning from Python 3.12, DeprecationWarning before.
            if issubclass(w.category, (SyntaxWarning, DeprecationWarning)):
                compile_warnings.append(f"{path.name}:{w.lineno}: {w.message}")

    if count == 0:
        return [Result(FAIL, "python sources", "no scripts found to compile; a check over zero files would pass vacuously")]
    out: list[Result] = []
    if errors:
        out.append(Result(FAIL, "python sources", f"{len(errors)} of {count} scripts do not compile: " + "; ".join(errors[:3])))
    if compile_warnings:
        out.append(Result(
            WARN, "python sources",
            f"{len(compile_warnings)} compile warning(s) -- an error on a future Python: " + "; ".join(compile_warnings[:3]),
        ))
    if not errors and not compile_warnings:
        out.append(Result(PASS, "python sources", f"all {count} scripts compile cleanly on Python {platform.python_version()}"))
    return out


def tool_results(which: Callable[[str], str | None] = shutil.which) -> list[Result]:
    """The CLIs the workflow shells out to. (pyw is covered by the hook-launcher check.)"""
    required = {
        "py": "the Python launcher, which runs every dev-env script by hand",
        "git": "every repo operation",
        "gh": "issues, PRs and the project board",
    }
    out: list[Result] = []
    missing = [f"{name} ({why})" for name, why in required.items() if which(name) is None]
    if missing:
        out.append(Result(FAIL, "tools", "not on PATH: " + "; ".join(missing)))
    else:
        out.append(Result(PASS, "tools", "py, git and gh are on PATH"))
    if which("node") is None:
        out.append(Result(
            WARN, "tools",
            "node is not on PATH; the workflow parses JSON with `node -e` (jq is not available) -- "
            "install nvm for Windows, then: nvm install 20.11.1 && nvm use 20.11.1",
        ))
    return out


def gh_auth_result(
    token_rc: int | None,
    status_rc: int | None,
    status_out: str,
    status_timed_out: bool = False,
) -> Result:
    """gh must be signed in -- decided by `gh auth token`, which is local and works offline --
    and hold the `project` scope, which only `gh auth status` (a network call) can report.
    Offline, `gh auth status` exits 1 claiming the token is invalid, so its failure alone
    never means "not signed in"."""
    login = "gh auth login && gh auth setup-git && gh auth refresh -s project"
    if token_rc is None:
        return Result(INFO, "gh auth", "not checked (gh is not installed, or did not answer)")
    if token_rc != 0:
        return Result(FAIL, "gh auth", f"not signed in -- run: {login}")
    if status_rc is None:
        why = "gh auth status timed out" if status_timed_out else "--offline"
        return Result(INFO, "gh auth", f"signed in; the 'project' scope was not checked ({why})")
    if status_rc != 0:
        return Result(
            WARN, "gh auth",
            "signed in, but GitHub could not be reached, or did not accept the token -- online, if "
            f"this persists, sign in again: {login}",
        )
    m = re.search(r"Token scopes:\s*(.+)", status_out)
    if not m:
        return Result(WARN, "gh auth", "signed in, but the token scopes could not be read; board commands need the 'project' scope")
    scopes = set(re.findall(r"'([^']+)'", m.group(1)))
    if "project" not in scopes:
        return Result(WARN, "gh auth", "signed in without the 'project' scope; board commands will fail -- run: gh auth refresh -s project")
    return Result(PASS, "gh auth", "signed in, with the 'project' scope")


def credential_helper_result(values: list[str]) -> Result:
    """git's GitHub credential helper should be gh, not the Credential Manager GUI (ADR-047)."""
    if any("gh" in v and "auth git-credential" in v for v in values):
        return Result(PASS, "git credentials", "github.com uses the gh credential helper")
    return Result(
        WARN, "git credentials",
        "github.com does not use the gh credential helper, so remote git operations can hang on the "
        "Git Credential Manager GUI -- run: gh auth setup-git (ADR-047)",
    )


def identity_result(name: str, email: str) -> Result:
    if name and email:
        return Result(PASS, "git identity", f"{name} <{email}>")
    return Result(WARN, "git identity", "user.name or user.email is unset -- git config --global user.name/user.email")


def global_hooks_result(
    value: str | None,
    expected: Path,
    resolve: Callable[[str], str] = os.path.realpath,
    is_dir: Callable[[str], bool] = os.path.isdir,
) -> Result:
    """The global core.hooksPath is what makes the ADR-005 guards run in every repo. `value` is
    read with `--type=path`, so a `~` in it is already expanded, as git itself expands it."""
    if not value:
        return Result(FAIL, "global hooks", "core.hooksPath is unset globally, so the ADR-005 guards never run -- re-run setup.sh")
    if not is_dir(value):
        return Result(
            FAIL, "global hooks",
            f"core.hooksPath is {value}, which does not exist (a dangling link?), so git runs no hooks at all -- re-run setup.sh",
        )
    if same_path(resolve(value), resolve(str(expected))):
        return Result(PASS, "global hooks", f"core.hooksPath -> {value}")
    return Result(WARN, "global hooks", f"core.hooksPath is {value}, not {expected}")


def repo_hooks_results(
    effective: dict[str, str | None],
    global_hooks: str,
    resolve: Callable[[str], str] = os.path.realpath,
) -> list[Result]:
    """Clones whose effective hooks dir is not the global one skip the ADR-005 guards.

    `effective` maps each clone's path to its effective hooks directory (from
    `git rev-parse --git-path hooks`), or to None when git could not read the clone."""
    if not effective:
        return [Result(INFO, "repo hooks", "no clones found to check")]
    overridden: list[tuple[str, str]] = []
    unreadable: list[str] = []
    ok = 0
    for repo, hooks in sorted(effective.items()):
        if hooks is None:
            unreadable.append(repo)
        elif same_path(resolve(hooks), resolve(global_hooks)):
            ok += 1
        else:
            overridden.append((repo, hooks))
    out: list[Result] = []
    if ok:
        out.append(Result(PASS, "repo hooks", f"{ok} clone(s) use the global hooks"))
    for repo, hooks in overridden:
        out.append(Result(
            WARN, "repo hooks",
            f"{Path(repo).name}: effective hooks dir is {hooks}, so the global pre-push guards "
            "(ADR-005) never run there (dev-env#1108)",
        ))
    if unreadable:
        out.append(Result(
            WARN, "repo hooks",
            f"git could not read {len(unreadable)} clone(s): {', '.join(Path(r).name for r in unreadable)} "
            "-- often 'dubious ownership'; see git config --global --add safe.directory <path>",
        ))
    return out


def journal_results(
    journal: Path,
    is_repo: bool,
    branch: str | None,
    today: str,
    remote_sha: str | None,
    contains_remote: bool,
    offline: bool,
) -> list[Result]:
    """The journal clone must exist; once another machine or session has pushed today's
    draft branch, a stub written anywhere else collides with it (#1111).

    `branch` is the checked-out branch, "" when HEAD is detached, or None when git could not
    read it. `remote_sha` is origin's draft/<today> tip, "" when origin has no such branch,
    or None when origin could not be reached."""
    if not is_repo:
        return [Result(
            FAIL, "journal",
            f"{journal} is missing or not a git clone; the stub workflow writes there -- "
            f"git clone https://github.com/brownm09/engineering-journal {journal.as_posix()}",
        )]
    if branch is None:
        out = [Result(
            WARN, "journal branch",
            "git could not read the checkout's branch -- often 'dubious ownership'; see "
            "git config --global --add safe.directory <path>",
        )]
    else:
        out = [Result(INFO, "journal branch", branch or "detached HEAD")]
    draft = f"draft/{today}"
    if offline:
        out.append(Result(INFO, "journal draft", f"did not check origin/{draft} (--offline)"))
    elif remote_sha is None:
        out.append(Result(WARN, "journal draft", f"could not reach origin to look for {draft}"))
    elif remote_sha == "":
        out.append(Result(INFO, "journal draft", f"no origin/{draft} yet -- the day's first session creates it"))
    elif contains_remote:
        out.append(Result(PASS, "journal draft", f"this checkout contains origin/{draft}"))
    else:
        j = journal.as_posix()
        # Explicit remote and branch throughout: this machine's own draft/<today>, cut from
        # main before origin's existed, has no upstream, so a bare `pull` has nothing to pull.
        out.append(Result(
            WARN, "journal draft",
            f"origin/{draft} already exists (pushed from another machine or session) but this checkout "
            f"does not contain it. Before writing a stub: git -C {j} fetch origin, then "
            f"git -C {j} checkout {draft} (no -b), then git -C {j} pull --no-rebase --no-edit origin {draft}. "
            f"If a push is rejected, pull the same way, then git -C {j} push -u origin {draft} "
            "-- never --force (dev-env#1111)",
        ))
    return out


def hook_config_result(path: Path, exists: bool) -> Result:
    """dev-env's board config is gitignored, so a fresh clone never has it."""
    if exists:
        return Result(PASS, "board config", str(path))
    return Result(
        WARN, "board config",
        f"{path} is missing; the project-board hook (post-tool-use.py) cannot add issues or PRs "
        "to the board without it -- copy it from your other machine (board and field IDs, no secrets)",
    )


def machine_role_result(raw: bytes | None) -> Result:
    """Whether this machine runs the shared-state routines (#1110). Informational only."""
    if raw is None:
        return Result(INFO, "routine host", "no ~/.claude/machine.json -- not a routine host (dev-env#1110)")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return Result(
            WARN, "routine host",
            "~/.claude/machine.json is not UTF-8 (PowerShell 5.1 writes UTF-16 by default) -- re-save it as UTF-8",
        )
    except ValueError:
        return Result(WARN, "routine host", "~/.claude/machine.json is not valid JSON")
    host = isinstance(data, dict) and data.get("routine_host") is True
    return Result(INFO, "routine host", f"{'yes' if host else 'no'} (~/.claude/machine.json)")


def guarded(check: str, fn: Callable[[], list[Result]]) -> list[Result]:
    """Run one check; if it crashes, report that as its FAIL line instead of losing the report."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- one broken check must not hide the others
        return [Result(FAIL, check, f"the check itself crashed: {type(exc).__name__}: {exc}")]


def exit_code(results: Iterable[Result]) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0


# --------------------------------------------------------------------------- I/O


class RunResult(NamedTuple):
    returncode: int | None  # None: could not start, or timed out
    stdout: str
    stderr: str
    timed_out: bool = False


# Never let a check wait on a prompt: git's own terminal prompt, the Git Credential Manager's
# dialog, and gh's prompts are all off.
_NO_PROMPT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GH_PROMPT_DISABLED": "1"}


def _run(args: list[str], timeout: float = 20.0) -> RunResult:
    """Run a command with prompts off and stdin closed, keeping stdout and stderr apart.

    Output goes to temp files, not pipes: on Windows, `subprocess.run(timeout=...)` still waits
    for a pipe's EOF after killing the child, and a grandchild (git-remote-https, a credential
    helper) that inherited it holds it open -- so with pipes the timeout doesn't bound the wait.
    On timeout the whole process tree is killed."""
    env = {**os.environ, **_NO_PROMPT_ENV}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env)
        except OSError:
            return RunResult(None, "", "")
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return RunResult(None, "", "", timed_out=True)
        out.seek(0)
        err.seek(0)
        return RunResult(
            returncode, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace"),
        )


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _git_out(*args: str) -> str | None:
    """stdout of a git command, stripped, or None when it failed. stderr never becomes part
    of the value: git writes warnings there even when it succeeds."""
    result = _run(["git", *args])
    return result.stdout.strip() if result.returncode == 0 else None


def repo_root_from_links(claude_home: Path) -> Path | None:
    """The dev-env checkout ~/.claude/scripts points into, if it is a dev-env layout."""
    scripts = claude_home / "scripts"
    if not os.path.lexists(scripts):
        return None
    real = Path(os.path.realpath(scripts))
    if real.name != "scripts" or real.parent.name != "claude":
        return None
    return real.parent.parent


def _effective_hooks_dir(repo: str) -> str | None:
    out = _git_out("-C", repo, "rev-parse", "--git-path", "hooks")
    if out is None:
        return None
    path = Path(out)
    return str(path if path.is_absolute() else Path(repo) / path)


def _check_hooks(settings_path: Path) -> list[Result]:
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = None
    return hook_command_results(settings if isinstance(settings, dict) else None, settings_path, HOME)


def _check_gh(offline: bool) -> list[Result]:
    if shutil.which("gh") is None:
        return [gh_auth_result(None, None, "")]
    token = _run(["gh", "auth", "token"])  # prints the token: only its exit status is read
    status = None if offline else _run(["gh", "auth", "status"])
    return [gh_auth_result(
        token.returncode,
        None if status is None else status.returncode,
        "" if status is None else status.stdout + status.stderr,
        bool(status and status.timed_out),
    )]


def _check_git_config() -> list[Result]:
    helpers = _git_out("config", "--global", "--get-all", "credential.https://github.com.helper")
    return [
        credential_helper_result(helpers.splitlines() if helpers else []),
        identity_result(
            _git_out("config", "--global", "user.name") or "", _git_out("config", "--global", "user.email") or "",
        ),
    ]


def _check_hooks_paths() -> list[Result]:
    global_value = _git_out("config", "--global", "--type=path", "core.hooksPath")
    out = [global_hooks_result(global_value, CLAUDE_HOME / "hooks")]
    if not GIT_ROOT.is_dir():
        out.append(Result(WARN, "repo hooks", f"could not scan {GIT_ROOT} -- dev-env expects its clones there"))
        return out
    repos = _repo_scan.find_git_repos(str(GIT_ROOT))
    out += repo_hooks_results(
        {repo: _effective_hooks_dir(repo) for repo in repos}, global_value or str(CLAUDE_HOME / "hooks"),
    )
    return out


def _check_journal(offline: bool) -> list[Result]:
    today = datetime.date.today().isoformat()
    is_repo = (JOURNAL / ".git").exists()
    remote_sha: str | None = None
    contains = False
    if is_repo and not offline:
        ls = _run([
            "git", "-c", "credential.interactive=never", "-C", str(JOURNAL),
            "ls-remote", "--heads", "origin", f"draft/{today}",
        ])
        if ls.returncode == 0:
            remote_sha = parse_ls_remote(ls.stdout, f"refs/heads/draft/{today}")
        if remote_sha:
            merged = _run(["git", "-C", str(JOURNAL), "merge-base", "--is-ancestor", remote_sha, "HEAD"])
            contains = merged.returncode == 0
    branch = _git_out("-C", str(JOURNAL), "branch", "--show-current") if is_repo else None
    return journal_results(JOURNAL, is_repo, branch, today, remote_sha, contains, offline)


def _check_machine() -> list[Result]:
    try:
        raw: bytes | None = (CLAUDE_HOME / "machine.json").read_bytes()
    except OSError:
        raw = None
    return [machine_role_result(raw)]


def collect(offline: bool, settings_path: Path) -> list[Result]:
    results: list[Result] = [Result(
        INFO, "machine", f"{platform.node()} | home {HOME} | Python {platform.python_version()}",
    )]

    # The checkout ~/.claude/scripts points into -- unless that one is gone, in which case the
    # links check reports them dangling and every other check uses this script's own checkout.
    linked = repo_root_from_links(CLAUDE_HOME)
    repo_root = linked if linked is not None and linked.is_dir() else Path(__file__).resolve().parents[2]

    results += guarded("links", lambda: link_results(CLAUDE_HOME, HOME, repo_root))
    results += guarded("dev-env checkout", lambda: checkout_results(
        repo_root, EXPECTED_DEV_ENV, _git_out("-C", str(repo_root), "branch", "--show-current"),
    ))
    results += guarded("hook commands", lambda: _check_hooks(settings_path))
    results += guarded("python sources", lambda: compile_results((repo_root / "claude" / "scripts").glob("*.py")))
    results += guarded("tools", tool_results)
    results += guarded("gh auth", lambda: _check_gh(offline))
    results += guarded("git config", _check_git_config)
    results += guarded("hooks paths", _check_hooks_paths)
    results += guarded("journal", lambda: _check_journal(offline))
    config = repo_root / ".claude" / "hook-config.json"
    results += guarded("board config", lambda: [hook_config_result(config, config.is_file())])
    results += guarded("routine host", _check_machine)
    return results


def print_report(results: list[Result]) -> None:
    for r in results:
        print(f"{r.status:<4}  {r.check}: {r.detail}")
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, WARN, FAIL, INFO)}
    print()
    print(f"{counts[PASS]} PASS, {counts[WARN]} WARN, {counts[FAIL]} FAIL, {counts[INFO]} INFO")
    if counts[FAIL]:
        print("Result: FAIL -- fix the FAIL lines above, then re-run this check.")
    else:
        print("Result: OK" + (" (with warnings)" if counts[WARN] else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only health check of this machine's dev-env install.")
    parser.add_argument(
        "--offline", action="store_true",
        help="skip network reads (gh auth status's scope check, git ls-remote for today's draft)",
    )
    parser.add_argument(
        "--settings", type=Path, default=CLAUDE_HOME / "settings.json",
        help="settings file to check (default: ~/.claude/settings.json)",
    )
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    results = collect(args.offline, args.settings)
    print_report(results)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
