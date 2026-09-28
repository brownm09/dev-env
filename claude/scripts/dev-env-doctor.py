#!/usr/bin/env python3
"""dev-env-doctor -- a read-only health check of the dev-env install on this machine.

Run it after `bash setup.sh` on a new machine (setup runs it for you), or any time the
tooling misbehaves:

    py -3 ~/.claude/scripts/dev-env-doctor.py [--offline] [--settings PATH]

It prints one line per check -- PASS, WARN, FAIL or INFO -- then a summary, and exits 1
when any check FAILs, 0 otherwise. It changes nothing: no fetch, no config write, no file
write. `--offline` skips the two network reads (`gh auth status`, and `git ls-remote` for
today's journal draft branch).

What it checks, and why each one matters on a second machine (dev-env#1114, part of #1107):

- The ~/.claude links (the set setup.sh creates) and ~/bin resolve into the dev-env
  checkout, and that checkout is ~/Git/dev-env on `main` -- dev-env-sync.py and
  session-start-sync.py assume both.
- Every hook command in the live ~/.claude/settings.json names a script that exists. A
  missing script exits 2, which blocks every prompt or tool call it is wired to. The check
  asserts it extracted at least one command, so an unreadable or empty settings file FAILs
  instead of passing vacuously (ADR-144).
- The tools the workflow shells out to: the hook launcher (pyw), py, git, gh, node -- plus
  gh's `project` scope, the gh git-credential helper (ADR-047) and git identity.
- Every script in claude/scripts/ compiles on this machine's Python. A SyntaxWarning (an
  invalid escape in a docstring, say) is a WARN, because a future Python makes it an error.
- The global core.hooksPath, and every clone under ~/Git whose effective hooks directory is
  not the global one -- those repos silently skip the ADR-005 pre-push guards (#1108).
- ~/Git/engineering-journal exists and contains origin/draft/<today> when another machine
  or session already pushed it (#1111).
- dev-env's gitignored .claude/hook-config.json, which the project-board hook needs.
- This machine's routine-host flag in ~/.claude/machine.json (#1110) -- informational.

The decisions live in pure `*_result` / `*_results` helpers, so the tests are fixture-only
and never read this machine's real state; `collect()` does the I/O around them.
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
import warnings
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

import _winsubp  # noqa: F401  -- UTF-8 decoding + no console flash for child processes (ADR-007)
import _repo_scan

# The settings.json hook walker the three settings gates share (Testing items 61-63), so a
# change to settings.json's hook nesting is handled in one place. It lives in tests/ so the
# gates' own claude/scripts/*.py glob never mistakes it for a hook -- import it from there.
sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from _hook_wiring import hook_entries  # noqa: E402

HOME = Path.home()
CLAUDE_HOME = HOME / ".claude"
GIT_ROOT = HOME / "Git"
EXPECTED_DEV_ENV = GIT_ROOT / "dev-env"
JOURNAL = GIT_ROOT / "engineering-journal"

# What setup.sh links from the repo into ~/.claude: CLAUDE_FILE_LINKS + CLAUDE_DIR_LINKS,
# plus the routines junction. test_dev_env_doctor.py pins this against setup.sh itself.
LINKED_ITEMS = ("CLAUDE.md", "scripts", "skills", "hooks", "templates", "routines")

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"

# The script a hook command runs: its last token ending in .py, optionally double-quoted
# (a home path containing a space). Matches the `pyw -3 C:/.../foo.py` form (ADR-007).
_SCRIPT_PATH_RE = re.compile(r'(?:"([^"]+\.py)"|(\S+\.py))\s*$')


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


# --------------------------------------------------------------------------- decisions


def link_results(
    claude_home: Path,
    bin_link: Path,
    repo_root: Path,
    resolve: Callable[[str], str] = os.path.realpath,
    lexists: Callable[[str], bool] = os.path.lexists,
) -> list[Result]:
    """Every link setup.sh creates must resolve into repo_root."""
    expected = [(claude_home / name, repo_root / "claude" / name) for name in LINKED_ITEMS]
    expected.append((bin_link, repo_root / "bin"))
    wrong: list[str] = []
    for link, target in expected:
        if not lexists(str(link)):
            wrong.append(f"{link} is missing")
            continue
        real = resolve(str(link))
        if same_path(real, resolve(str(target))):
            continue
        if same_path(real, link):
            wrong.append(f"{link} is a real file or directory, not a link (re-run setup.sh; it backs the original up first)")
        else:
            wrong.append(f"{link} resolves to {real}, expected {target}")
    if wrong:
        return [Result(FAIL, "links", f"{len(wrong)} of {len(expected)} wrong: " + "; ".join(wrong))]
    return [Result(PASS, "links", f"all {len(expected)} ~/.claude links and ~/bin resolve into {repo_root}")]


def checkout_results(repo_root: Path, expected: Path, branch: str | None) -> list[Result]:
    """The checkout the links point into should be ~/Git/dev-env, on main."""
    out: list[Result] = []
    if same_path(repo_root, expected):
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
    if settings is None:
        return [Result(
            FAIL, "hook commands",
            f"cannot read {settings_path} (missing or not JSON) -- re-seed it: "
            "py -3 <dev-env>/claude/scripts/_settings_sync.py",
        )]
    entries = hook_entries(settings)
    if not entries:
        return [Result(
            FAIL, "hook commands",
            f"no hook commands found in {settings_path}; checking zero commands would pass "
            "vacuously -- re-seed it: py -3 <dev-env>/claude/scripts/_settings_sync.py",
        )]
    missing: list[str] = []
    unparsed: list[str] = []
    scripts: set[str] = set()
    launchers: set[str] = set()
    for entry in entries:
        path = script_path_from_command(entry.command)
        if path is None:
            unparsed.append(entry.command)
            continue
        scripts.add(path)
        tokens = entry.command.split()
        if tokens:
            launchers.add(tokens[0])
        if not is_file(path):
            missing.append(path)

    out: list[Result] = []
    if missing:
        unique = sorted(set(missing))
        detail = (
            f"{len(missing)} of {len(entries)} hook commands name a missing script "
            f"(every prompt or tool call they are wired to is blocked), e.g. {', '.join(unique[:3])}"
        )
        prefix = home_prefix(unique[0])
        if prefix and not same_path(prefix, home):
            detail += f" -- they point under {prefix}, but this machine's home is {home} (dev-env#1113)"
        out.append(Result(FAIL, "hook commands", detail))
    else:
        out.append(Result(
            PASS, "hook commands", f"{len(entries)} commands, {len(scripts)} distinct scripts, all present",
        ))
    if unparsed:
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
    """Every script must compile here; a SyntaxWarning is tomorrow's SyntaxError."""
    errors: list[str] = []
    syntax_warnings: list[str] = []
    count = 0
    for path in sorted(paths):
        count += 1
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path.name}: unreadable ({exc})")
            continue
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                errors.append(f"{path.name}:{exc.lineno}: {exc.msg}")
                continue
        for w in caught:
            if issubclass(w.category, SyntaxWarning):
                syntax_warnings.append(f"{path.name}:{w.lineno}: {w.message}")

    if count == 0:
        return [Result(FAIL, "python sources", "no scripts found to compile; a check over zero files would pass vacuously")]
    out: list[Result] = []
    if errors:
        out.append(Result(FAIL, "python sources", f"{len(errors)} of {count} scripts do not compile: " + "; ".join(errors[:3])))
    if syntax_warnings:
        out.append(Result(
            WARN, "python sources",
            f"{len(syntax_warnings)} SyntaxWarning(s) -- an error on a future Python: " + "; ".join(syntax_warnings[:3]),
        ))
    if not errors and not syntax_warnings:
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


def gh_auth_result(returncode: int | None, output: str) -> Result:
    """gh must be signed in, with the `project` scope the board commands need."""
    if returncode is None:
        return Result(INFO, "gh auth", "not checked (--offline, or gh is not installed)")
    if returncode != 0:
        return Result(FAIL, "gh auth", "not signed in -- run: gh auth login && gh auth setup-git && gh auth refresh -s project")
    m = re.search(r"Token scopes:\s*(.+)", output)
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


def global_hooks_result(value: str | None, expected: Path, resolve: Callable[[str], str] = os.path.realpath) -> Result:
    """The global core.hooksPath is what makes the ADR-005 guards run in every repo."""
    if not value:
        return Result(FAIL, "global hooks", "core.hooksPath is unset globally, so the ADR-005 guards never run -- re-run setup.sh")
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

    `remote_sha` is origin's draft/<today> tip, "" when origin has no such branch, or None
    when origin could not be reached."""
    if not is_repo:
        return [Result(
            FAIL, "journal",
            f"{journal} is missing or not a git clone; the stub workflow writes there -- "
            f"git clone https://github.com/brownm09/engineering-journal {journal.as_posix()}",
        )]
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
        out.append(Result(
            WARN, "journal draft",
            f"origin/{draft} already exists (pushed from another machine or session) but this checkout "
            f"does not contain it. Before writing a stub: git -C {j} fetch origin, then "
            f"git -C {j} checkout {draft} (no -b). If a push is rejected: "
            f"git -C {j} pull --no-rebase --no-edit, then push -- never --force (dev-env#1111)",
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


def machine_role_result(text: str | None) -> Result:
    """Whether this machine runs the shared-state routines (#1110). Informational only."""
    if text is None:
        return Result(INFO, "routine host", "no ~/.claude/machine.json -- not a routine host (dev-env#1110)")
    try:
        data = json.loads(text)
    except ValueError:
        return Result(WARN, "routine host", "~/.claude/machine.json is not valid JSON")
    host = isinstance(data, dict) and data.get("routine_host") is True
    return Result(INFO, "routine host", f"{'yes' if host else 'no'} (~/.claude/machine.json)")


def exit_code(results: Iterable[Result]) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0


# --------------------------------------------------------------------------- I/O


def _run(args: list[str], timeout: float = 20.0) -> tuple[int | None, str]:
    """Run a command; return (returncode, stdout+stderr), or (None, "") if it could not run."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _git_out(*args: str) -> str | None:
    """stdout of a git command, stripped, or None when it failed."""
    rc, out = _run(["git", *args])
    return out.strip() if rc == 0 else None


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


def collect(offline: bool, settings_path: Path) -> list[Result]:
    results: list[Result] = [Result(
        INFO, "machine", f"{platform.node()} | home {HOME} | Python {platform.python_version()}",
    )]

    repo_root = repo_root_from_links(CLAUDE_HOME) or Path(__file__).resolve().parents[2]
    results += link_results(CLAUDE_HOME, HOME / "bin", repo_root)
    results += checkout_results(repo_root, EXPECTED_DEV_ENV, _git_out("-C", str(repo_root), "branch", "--show-current"))

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = None
    results += hook_command_results(settings if isinstance(settings, dict) else None, settings_path, HOME)
    results += compile_results((repo_root / "claude" / "scripts").glob("*.py"))

    results += tool_results()
    if offline or shutil.which("gh") is None:
        results.append(gh_auth_result(None, ""))
    else:
        rc, out = _run(["gh", "auth", "status"])
        results.append(gh_auth_result(rc, out))
    helpers = _git_out("config", "--global", "--get-all", "credential.https://github.com.helper")
    results.append(credential_helper_result(helpers.splitlines() if helpers else []))
    results.append(identity_result(
        _git_out("config", "--global", "user.name") or "", _git_out("config", "--global", "user.email") or "",
    ))

    global_value = _git_out("config", "--global", "core.hooksPath")
    results.append(global_hooks_result(global_value, CLAUDE_HOME / "hooks"))
    repos = _repo_scan.find_git_repos(str(GIT_ROOT)) if GIT_ROOT.is_dir() else None
    if repos is None:
        results.append(Result(
            WARN, "repo hooks", f"could not scan {GIT_ROOT} -- dev-env expects its clones there",
        ))
    else:
        results += repo_hooks_results(
            {repo: _effective_hooks_dir(repo) for repo in repos}, global_value or str(CLAUDE_HOME / "hooks"),
        )

    today = datetime.date.today().isoformat()
    is_repo = (JOURNAL / ".git").exists()
    remote_sha: str | None = None
    contains = False
    if is_repo and not offline:
        rc, out = _run(["git", "-C", str(JOURNAL), "ls-remote", "--heads", "origin", f"draft/{today}"])
        if rc == 0:
            remote_sha = out.split()[0] if out.strip() else ""
        if remote_sha:
            rc, _ = _run(["git", "-C", str(JOURNAL), "merge-base", "--is-ancestor", remote_sha, "HEAD"])
            contains = rc == 0
    branch = _git_out("-C", str(JOURNAL), "branch", "--show-current") if is_repo else None
    results += journal_results(JOURNAL, is_repo, branch, today, remote_sha, contains, offline)

    config = repo_root / ".claude" / "hook-config.json"
    results.append(hook_config_result(config, config.is_file()))

    machine = CLAUDE_HOME / "machine.json"
    try:
        machine_text: str | None = machine.read_text(encoding="utf-8")
    except OSError:
        machine_text = None
    results.append(machine_role_result(machine_text))
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
    parser.add_argument("--offline", action="store_true", help="skip network reads (gh auth status, git ls-remote)")
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
