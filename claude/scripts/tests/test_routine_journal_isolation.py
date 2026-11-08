#!/usr/bin/env python3
"""Routines must not move the shared engineering-journal checkout (dev-env#657, #346, ADR-082).

Required when changing `claude/routines/biweekly-retro/SKILL.md` or
`claude/routines/weekly-memory-audit/SKILL.md`, or adding a routine that touches engineering-journal.
Run: py -3 claude/scripts/tests/test_routine_journal_isolation.py

Two layers. A static check over every routine: nothing passes engineering-journal to
`sync-routine-worktree`, and no shell block (```bash, ```sh, ```shell or unlabelled) moves that
checkout's branch, index or files. An end-to-end run of each report routine's own extracted blocks
against a throwaway bare origin, with a fake `gh`: the shared checkout is byte-identical afterwards,
the report still reaches its own branch and PR, and the leftover sweep spares worktrees it didn't make.

Scope gaps: the static check reads only fenced shell blocks and the `REPO` parameter, and recognizes
the checkout by its literal path (`C:/`, `/c/`, `~/` or `$HOME/` forms) or a variable assigned one of
them, so a path built any other way at run time goes unseen. The end-to-end layer covers the two
routines that write reports.
"""
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTINES = os.path.normpath(os.path.join(HERE, "..", "..", "routines"))
SHARED = "C:/Users/brown/Git/engineering-journal"
SCRATCH = "C:/Users/brown/.claude/scratch"
DATE = "2026-11-14"
PRIOR = "2026-10-31"
NEWER = "2026-11-07"
TIMEOUT = 120

SHARED_PATH = r"(?:(?:[Cc]:|/c)/Users/brown|~|\$\{?HOME\}?)/Git/engineering-journal"
SHELL_LABELS = ("bash", "sh", "shell", "")
# Verbs that move a branch, the index or the working tree. `merge` is allowed only with --ff-only
# (retro-chain-backstop's documented update); `branch`, `symbolic-ref` and `worktree` only in the
# read-only or other-path forms below.
MOVING_VERBS = {
    "checkout", "switch", "rebase", "reset", "pull", "commit", "add", "stash", "cherry-pick", "revert",
    "restore", "rm", "mv", "am", "apply", "clean", "update-ref", "read-tree", "update-index",
    "checkout-index",
}
OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def shell_blocks(text, labels=SHELL_LABELS):
    """Fenced blocks labelled bash, sh, shell or nothing, paired fence by fence in document order."""
    blocks, current = [], None
    for line in text.splitlines():
        fence = re.match(r"^[ \t]*```+[ \t]*([\w+-]*)", line)
        if fence:
            if current is None:
                current = (fence.group(1).lower(), [])
            else:
                if current[0] in labels:
                    blocks.append("\n".join(current[1]) + "\n")
                current = None
            continue
        if current is not None:
            current[1].append(line)
    return blocks


def shared_names(text):
    """Variables a routine points at the shared checkout, e.g. `EJ`."""
    return set(re.findall(rf'^\s*(\w+)="?{SHARED_PATH}/?"?\s*(?:#.*)?$', text, re.MULTILINE))


def _is_target(token, names):
    token = token.rstrip("/")
    if token.endswith("/.git"):
        token = token[: -len("/.git")]
    variable = "|".join(sorted(names)) or r"(?!)"
    return re.fullmatch(rf"{SHARED_PATH}|\$\{{?(?:{variable})\}}?", token) is not None


def _commands(block):
    """Each simple command in a block, as a token list, split at ; && || | ( ) and newlines."""
    for line in block.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:  # an unclosed quote: fall back to whitespace
            tokens = line.split()
        command = []
        for token in tokens + [";"]:
            if token and set(token) <= set("();<>|&"):
                if command:
                    yield command
                command = []
            else:
                command.append(token)


def _git_violation(tokens, names):
    i, targeted = 1, False
    while i < len(tokens) and tokens[i].startswith("-"):
        option, _, inline = tokens[i].partition("=")
        if option in OPTIONS_WITH_VALUE and not inline:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
        else:
            value = inline
            i += 1
        if option in ("-C", "--git-dir", "--work-tree") and _is_target(value, names):
            targeted = True
    if not targeted or i >= len(tokens):
        return None
    verb, args = tokens[i], tokens[i + 1:]
    positional = [a for a in args if not a.startswith("-")]
    if verb in MOVING_VERBS:
        return verb
    if verb == "merge" and "--ff-only" not in args:
        return "merge without --ff-only"
    if verb == "branch" and positional:
        return "branch"
    if verb == "symbolic-ref" and len(positional) >= 2:
        return "symbolic-ref"
    if verb == "worktree" and positional[:1] in (["remove"], ["move"]) and any(_is_target(a, names) for a in positional):
        return f"worktree {positional[0]} <shared>"
    return None


def violations(text):
    """Every way this routine text would rebase, reset, switch or clean the shared checkout."""
    found = []
    names = shared_names(text)
    for value in re.findall(r"`?\bREPO`?\s*=\s*`?([^\s`,]+)", text):
        if "engineering-journal" in value or value.lstrip("$").strip("{}\"") in names:
            found.append(f"sync-routine-worktree REPO={value}")
    for block in shell_blocks(text):
        for tokens in _commands(block):
            if tokens[0] in ("cd", "pushd") and len(tokens) > 1 and _is_target(tokens[1], names):
                found.append(f"{tokens[0]} <shared>")
            elif tokens[0] == "git":
                verb = _git_violation(tokens, names)
                if verb:
                    found.append(f"git <shared> {verb}")
    return found


def routine_files():
    return sorted(
        os.path.join(ROUTINES, name, "SKILL.md")
        for name in os.listdir(ROUTINES)
        if os.path.isfile(os.path.join(ROUTINES, name, "SKILL.md"))
    )


# Verbatim pieces of the sequence dev-env#657 removed. These are the known-bad references.
OLD_STEP_0 = (
    "Read `~/.claude/skills/sync-routine-worktree/SKILL.md` and execute its Behavior section\n\n"
    "- `REPO` = `C:/Users/brown/Git/engineering-journal`\n- `PREFIX` = `biweekly-retro`\n"
)
OLD_RETRO_BLOCK = (
    'EJ="C:/Users/brown/Git/engineering-journal"\n\n```bash\ncd "$EJ"\n'
    'git checkout -b "retro/${RUN_DATE}" origin/main 2>/dev/null || git checkout "retro/${RUN_DATE}"\n```\n'
)
OLD_AUDIT_BLOCK = (
    '```bash\nEJ="C:/Users/brown/Git/engineering-journal"\n'
    'git -C "$EJ" checkout -b "memory-audit/${RUN_DATE}" origin/main 2>/dev/null\n```\n'
)
_EJ = 'EJ="C:/Users/brown/Git/engineering-journal"\n'
KNOWN_BAD = {
    "REPO parameter": OLD_STEP_0,
    "REPO in prose": "Invoke `sync-routine-worktree` with `REPO=C:/Users/brown/Git/engineering-journal`.\n",
    "REPO through a variable": '```bash\nEJ="C:/Users/brown/Git/engineering-journal"\n```\nUse `REPO=$EJ`.\n',
    "cd then checkout": OLD_RETRO_BLOCK,
    "git -C checkout": OLD_AUDIT_BLOCK,
    "literal path rebase": "```bash\ngit -C C:/Users/brown/Git/engineering-journal rebase origin/main\n```\n",
    "git -C with a -c option": '```bash\nJ="C:/Users/brown/Git/engineering-journal"\ngit -C "$J" -c a=b reset --hard\n```\n',
    "clean": f'```bash\n{_EJ}git -C "$EJ" clean -fdx\n```\n',
    "branch -f": f'```bash\n{_EJ}git -C "$EJ" branch -f draft/x origin/main\n```\n',
    "update-ref": f'```bash\n{_EJ}git -C "$EJ" update-ref refs/heads/draft/x HEAD~1\n```\n',
    "merge without --ff-only": f'```bash\n{_EJ}git -C "$EJ" merge origin/main\n```\n',
    "--no-pager before -C": f'```bash\n{_EJ}git --no-pager -C "$EJ" checkout x\n```\n',
    "--no-pager after -C": f'```bash\n{_EJ}git -C "$EJ" --no-pager checkout x\n```\n',
    "--git-dir": f'```bash\n{_EJ}git --git-dir="$EJ/.git" reset --hard\n```\n',
    "trailing slash": f'```bash\n{_EJ}git -C "$EJ/" checkout x\n```\n',
    "cd then semicolon": f'```bash\n{_EJ}cd "$EJ"; git checkout -b x\n```\n',
    "cd in a subshell": f'```bash\n{_EJ}(cd "$EJ" && git checkout -b x)\n```\n',
    "cd after &&": f'```bash\n{_EJ}true && cd "$EJ" && git reset --hard\n```\n',
    "pushd": f'```bash\n{_EJ}pushd "$EJ"\n```\n',
    "/c/ path": "```bash\ngit -C /c/Users/brown/Git/engineering-journal reset --hard\n```\n",
    "~/ path": "```bash\ngit -C ~/Git/engineering-journal checkout main\n```\n",
    "$HOME variable": '```bash\nJ="$HOME/Git/engineering-journal"\ngit -C "$J" stash\n```\n',
    "sh fence": "```sh\ngit -C C:/Users/brown/Git/engineering-journal reset --hard\n```\n",
    "unlabelled fence": "```\ngit -C C:/Users/brown/Git/engineering-journal reset --hard\n```\n",
    "worktree remove of the checkout": f'```bash\n{_EJ}git -C "$EJ" worktree remove --force "$EJ"\n```\n',
}
KNOWN_GOOD = {
    "read-only queries": (
        "```bash\ngit -C C:/Users/brown/Git/engineering-journal fetch --prune origin\n"
        "git -C C:/Users/brown/Git/engineering-journal ls-tree -r origin/main --name-only\n"
        "git -C C:/Users/brown/Git/engineering-journal cat-file -e refs/remotes/origin/main:x\n```\n"
    ),
    "worktree registration": (
        '```bash\nEJ="C:/Users/brown/Git/engineering-journal"\nWT="$EJ/.claude/worktrees/x"\n'
        'git -C "$EJ" worktree add --detach "$WT" refs/remotes/origin/main\n'
        'git -C "$EJ" worktree remove --force "$OLD" || { rm -rf "$OLD"; git -C "$EJ" worktree prune; }\n'
        'git -C "$WT" checkout --detach refs/remotes/origin/x\ngit -C "$WT" add sessions\n'
        'git -C "$WT" commit -m x\n```\n'
    ),
    "a fast-forward of its own branch": (
        "```bash\ngit -C C:/Users/brown/Git/engineering-journal merge --ff-only --quiet '@{upstream}'\n```\n"
    ),
    "read-only branch and symbolic-ref": (
        f'```bash\n{_EJ}git -C "$EJ" branch --show-current\ngit -C "$EJ" symbolic-ref HEAD\n```\n'
    ),
    "another repo's sync": "- `REPO` = `C:/Users/brown/Git/career-playbook`\n",
    "a routine's own root": "Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`.\n",
    "prose naming the old call": "The prior `sync-routine-worktree` call could rebase engineering-journal.\n",
    "a markdown fence then a shell fence": (
        "```markdown\ngit -C C:/Users/brown/Git/engineering-journal reset --hard\n```\n\n"
        "```\nroutine complete: 3 issues filed\n```\n"
    ),
}


class StaticCheck(unittest.TestCase):
    def test_every_routine_is_clean(self):
        files = routine_files()
        # Extraction is non-empty: a moved folder must not pass by reading nothing.
        self.assertGreaterEqual(len(files), 8, ROUTINES)
        for path in files:
            with self.subTest(routine=os.path.basename(os.path.dirname(path))):
                self.assertEqual(violations(_read(path)), [])

    def test_report_routines_are_really_being_read(self):
        for name in ("biweekly-retro", "weekly-memory-audit"):
            text = _read(os.path.join(ROUTINES, name, "SKILL.md"))
            self.assertIn("EJ", shared_names(text), f"{name}: the shared-checkout variable moved")
            commands = [c for b in shell_blocks(text) for c in _commands(b)]
            self.assertGreater(sum(c[0] == "git" for c in commands), 5, f"{name}: no git commands extracted")

    def test_known_bad_is_caught(self):
        for label, text in KNOWN_BAD.items():
            with self.subTest(label):
                self.assertTrue(violations(text), f"{label} passed the check")

    def test_known_good_passes(self):
        for label, text in KNOWN_GOOD.items():
            with self.subTest(label):
                self.assertEqual(violations(text), [])


def _git_bash():
    """A Git for Windows bash, not the WSL launcher. Fails loudly: a skipped end-to-end gate is no gate."""
    candidates = []
    found = shutil.which("bash")
    if found and not any(part in found.lower() for part in ("system32", "windowsapps")):
        candidates.append(found)
    git = shutil.which("git")
    if git:
        here = os.path.dirname(os.path.realpath(git))
        for hops in ("", "..", os.path.join("..", ".."), os.path.join("..", "..", "..")):
            candidates.append(os.path.normpath(os.path.join(here, hops, "bin", "bash.exe")))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise AssertionError(f"no Git for Windows bash found (tried {candidates})")


def _git(cwd, *args, check=True):
    done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=TIMEOUT)
    if check and done.returncode != 0:
        raise AssertionError(f"git {args} failed: {done.stderr}")
    return done.stdout


def _write(root, relative, content):
    path = os.path.join(root, *relative.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _force_rmtree(path):
    def retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _posix(path):
    return path.replace("\\", "/")


class Routine:
    """One report routine's blocks, as they would be pasted into separate Bash calls."""

    def __init__(self, name, prefix, folder, report):
        self.name, self.prefix, self.folder, self.report = name, prefix, folder, report
        markers = {
            "fetch": "fetch --prune origin",
            "create": "worktree add --detach",
            "clear": f"rm -f {SCRATCH}/{prefix}-",
            "readme": f"{folder}/README.md",
            "publish": 'push origin "HEAD:refs/heads/',
            "pr": "gh pr create",
            "remove": 'worktree remove --force "$WT"',
        }
        text = _read(os.path.join(ROUTINES, name, "SKILL.md"))
        blocks = shell_blocks(text)
        self.blocks, positions = {}, {}
        for key, marker in markers.items():
            matches = [b for b in blocks if marker in b]
            if len(matches) != 1:
                raise AssertionError(f"{name}: {len(matches)} blocks contain {marker!r}, expected 1")
            self.blocks[key] = matches[0]
            positions[key] = text.index(matches[0])
        self.order = sorted(self.blocks, key=positions.get)
        for first, then in (("fetch", "create"), ("create", "publish"), ("clear", "publish"),
                            ("publish", "pr"), ("pr", "remove")):
            if positions[first] > positions[then]:
                raise AssertionError(f"{name}: the {first} block comes after the {then} block")


class EndToEnd(unittest.TestCase):
    """A bare origin, a shared checkout mid-session on a draft branch, and a stale origin/main ref."""

    @classmethod
    def setUpClass(cls):
        # Built here, not at import, so a broken marker can't hide the static layer's results.
        cls.retro = Routine("biweekly-retro", "retro", "sessions/meta/retro", f"{DATE}-retro.md")
        cls.audit = Routine("weekly-memory-audit", "memory-audit", "sessions/meta/memory-audit",
                            f"{DATE}-audit.md")

    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="routine-iso-"))
        self.addCleanup(_force_rmtree, self.base)
        self.origin = os.path.join(self.base, "origin.git")
        self.seed = os.path.join(self.base, "seed")
        self.shared = os.path.join(self.base, "ej")
        self.scratch = os.path.join(self.base, "scratch")
        self.bin = os.path.join(self.base, "bin")
        self.gh_log = os.path.join(self.base, "gh.log")
        os.makedirs(self.scratch)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], check=True, capture_output=True)

        os.makedirs(self.seed)
        _git(self.seed, "init", "-q", "-b", "main")
        self._isolate(self.seed)
        _git(self.seed, "remote", "add", "origin", _posix(self.origin))
        _write(self.seed, ".gitignore", ".claude/worktrees/\n")
        _write(self.seed, "sessions/meta/README.md", "# meta\n")
        _write(self.seed, f"sessions/meta/retro/{PRIOR}-retro.md", "# old\n")
        _git(self.seed, "add", "-A")
        _git(self.seed, "commit", "-q", "-m", "seed")
        _git(self.seed, "push", "-q", "origin", "HEAD:refs/heads/main")

        subprocess.run(["git", "clone", "-q", _posix(self.origin), self.shared], check=True, capture_output=True)
        self._isolate(self.shared)
        _git(self.shared, "checkout", "-q", "-b", f"draft/{DATE}")
        _write(self.shared, f"sessions/dev-env/{DATE}_090000.stub.md", "committed stub\n")
        _write(self.shared, f"sessions/dev-env/{DATE}_100000.stub.md", "staged by another session\n")
        _git(self.shared, "add", f"sessions/dev-env/{DATE}_090000.stub.md")
        _git(self.shared, "commit", "-q", "-m", "stub")
        _git(self.shared, "add", f"sessions/dev-env/{DATE}_100000.stub.md")
        _write(self.shared, f"sessions/dev-env/{DATE}_090000.stub.md", "committed stub, then edited\n")
        _write(self.shared, f"sessions/dev-env/{DATE}_110000.stub.md", "untracked, being written\n")

        # main moves on after the shared checkout last fetched: the routine has to pick this up.
        _write(self.seed, f"sessions/dev-env/{DATE}-journal.md", "composed after the last fetch\n")
        _write(self.seed, f"sessions/meta/retro/{NEWER}-retro.md", "# newer\n")
        _git(self.seed, "add", "-A")
        _git(self.seed, "commit", "-q", "-m", "a composed journal")
        _git(self.seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        self.main_tip = _git(self.seed, "rev-parse", "HEAD").strip()

        # A fake gh that, like the real one, refuses a second PR for the same branch.
        os.makedirs(self.bin)
        fake_gh = os.path.join(self.bin, "gh")
        with open(fake_gh, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(
                '#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$GH_LOG"\n'
                'if [ "$1 $2" = "pr create" ]; then\n'
                '  if [ -e "$GH_LOG.created" ]; then echo "a pull request already exists" >&2; exit 1; fi\n'
                '  : > "$GH_LOG.created"; echo "https://example.invalid/pull/1"\n'
                'elif [ "$1 $2" = "pr view" ]; then\n'
                '  [ -e "$GH_LOG.created" ] || exit 1; echo "https://example.invalid/pull/1"\n'
                'fi\n'
            )
        os.chmod(fake_gh, 0o755)

    def _isolate(self, repo):
        """Keep this machine's global hooks and signing out of the fixture; a worktree inherits these."""
        no_hooks = os.path.join(self.base, "no-hooks")
        os.makedirs(no_hooks, exist_ok=True)
        for key, value in (("core.hooksPath", _posix(no_hooks)), ("commit.gpgsign", "false"),
                           ("user.name", "test"), ("user.email", "test@example.invalid")):
            _git(repo, "config", key, value)

    def _fill(self, block):
        text = block.replace(SHARED, _posix(self.shared)).replace(SCRATCH, _posix(self.scratch))
        text = text.replace("YYYY-MM-DD", DATE)
        self.assertNotIn(SHARED, text, "the real checkout path survived substitution")
        self.assertNotIn(SCRATCH, text, "the real scratch path survived substitution")
        return text

    def _bash(self, block):
        env = dict(os.environ, GH_LOG=_posix(self.gh_log), PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        path = os.path.join(self.base, "block.sh")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self._fill(block))
        return subprocess.run([_git_bash(), _posix(path)], capture_output=True, text=True, encoding="utf-8",
                              env=env, cwd=self.base, timeout=TIMEOUT)

    def _run_ok(self, block):
        done = self._bash(block)
        self.assertEqual(done.returncode, 0, f"{block}\n{done.stdout}\n{done.stderr}")
        return done.stdout

    def _snapshot(self):
        """Everything another session could lose: branch, HEAD, index, status, and the dirty files' bytes."""
        files = {}
        for stamp in ("090000", "100000", "110000"):
            path = os.path.join(self.shared, "sessions", "dev-env", f"{DATE}_{stamp}.stub.md")
            if os.path.exists(path):  # a branch switch can take a session's stub off disk
                with open(path, "rb") as fh:
                    files[stamp] = fh.read()
        return {
            "branch": _git(self.shared, "symbolic-ref", "HEAD"),
            "head": _git(self.shared, "rev-parse", "HEAD"),
            "index": _git(self.shared, "ls-files", "--stage"),
            "status": _git(self.shared, "status", "--porcelain"),
            "files": files,
        }

    def _stage_dir(self, routine):
        return os.path.join(self.scratch, f"{routine.prefix}-{DATE}")

    def _stage(self, routine, report):
        """What the Write tool does: the draft lands in scratch, outside every worktree."""
        folder = f"{routine.prefix}-{DATE}"
        _write(self.scratch, f"{folder}/{routine.report}", report)
        _write(self.scratch, f"{folder}/README.md", "# readme\n")

    def _publish_flow(self, routine, report):
        """Every block in document order, with the Write tool's drafting step just before publish."""
        before = self._snapshot()
        _write(self.scratch, f"{routine.prefix}-{DATE}/left-by-a-failed-run.md", "stale\n")
        url = None
        for key in routine.order:
            block = routine.blocks[key]
            if key == "readme":
                # The fixture's main has no README in the report folder, so the routine must write one.
                self.assertNotEqual(self._bash(block).returncode, 0, "the README check found no README")
                continue
            if key == "publish":
                self._stage(routine, report)
            output = self._run_ok(block)
            if key == "clear":
                self.assertFalse(os.listdir(self._stage_dir(routine)), "the clear block missed the stage folder")
            if key == "create":
                self.assertEqual(self._snapshot(), before, "creating the worktree changed the shared checkout")
            if key == "pr":
                url = output.strip()
        self.assertEqual(url, "https://example.invalid/pull/1", "the PR block printed no URL")
        self.assertEqual(self._snapshot(), before, "the routine changed the shared checkout")

    def _worktrees(self):
        listed = _git(self.shared, "worktree", "list", "--porcelain")
        return sorted(line[len("worktree "):] for line in listed.splitlines() if line.startswith("worktree "))

    def _assert_no_leftovers(self, routine, kept=()):
        expected = sorted([_posix(self.shared), *[_posix(k) for k in kept]])
        self.assertEqual(self._worktrees(), expected)
        # A trailing "/" matches the whole namespace; a "*" in a for-each-ref pattern doesn't cross "/".
        self.assertEqual(_git(self.shared, "for-each-ref", f"refs/heads/{routine.prefix}/"), "")

    def _plant_worktrees(self, routine):
        """What the sweep finds: a crashed run's, a locked one, a session's, and an unregistered dir."""
        root = os.path.join(self.shared, ".claude", "worktrees")
        crashed = os.path.join(root, f"{routine.prefix}-{PRIOR}")
        locked = os.path.join(root, f"{routine.prefix}-2026-10-24")
        session = os.path.join(root, f"{routine.prefix}-action-fixes")
        loose = os.path.join(root, f"{routine.prefix}-2026-10-17")
        for path in (crashed, locked, session):
            _git(self.shared, "worktree", "add", "-q", "--detach", _posix(path), "HEAD")
        _git(self.shared, "worktree", "lock", _posix(locked))
        _write(session, "work-in-progress.txt", "a session's uncommitted work\n")
        _write(loose, "x.txt", "left behind\n")
        return crashed, loose, (locked, session)

    def _check_flow(self, routine):
        """Planted worktrees, then a first run, then a same-day re-run, then one with nothing new."""
        crashed, loose, kept = self._plant_worktrees(routine)
        self._publish_flow(routine, "# first\n")
        self.assertFalse(os.path.exists(crashed), "a crashed run's worktree was left behind")
        self.assertFalse(os.path.exists(loose), "an unregistered dated directory was left behind")
        self.assertTrue(os.path.isfile(os.path.join(kept[0], "sessions", "meta", "README.md")),
                        "the sweep removed a locked worktree")
        self.assertTrue(os.path.isfile(os.path.join(kept[1], "work-in-progress.txt")),
                        "the sweep removed a worktree it didn't make")
        branch = f"{routine.prefix}/{DATE}"
        first = _git(self.origin, "rev-parse", f"refs/heads/{branch}").strip()
        self.assertEqual(_git(self.origin, "rev-parse", f"{first}^").strip(), self.main_tip,
                         "the report must sit directly on the freshly fetched origin/main")
        changed = _git(self.origin, "diff", "--name-only", self.main_tip, first).split()
        self.assertEqual(sorted(changed), sorted([f"{routine.folder}/{routine.report}", f"{routine.folder}/README.md"]),
                         "another session's stubs reached the report branch")
        self.assertEqual(_git(self.origin, "show", f"{first}:{routine.folder}/{routine.report}"), "# first\n")
        with open(self.gh_log, encoding="utf-8") as fh:
            gh_args = fh.read().split("\n")
        self.assertEqual(gh_args[:2], ["pr", "create"])
        for flag, value in (("--repo", "brownm09/engineering-journal"), ("--base", "main"), ("--head", branch)):
            self.assertEqual(gh_args[gh_args.index(flag) + 1], value)
        self._assert_no_leftovers(routine, kept)

        self._publish_flow(routine, "# second\n")
        second = _git(self.origin, "rev-parse", f"refs/heads/{branch}").strip()
        self.assertEqual(_git(self.origin, "rev-parse", f"{second}^").strip(), first, "not a fast-forward")
        self.assertEqual(_git(self.origin, "show", f"{second}:{routine.folder}/{routine.report}"), "# second\n")

        self._publish_flow(routine, "# second\n")  # nothing new to commit must not fail the run
        self.assertEqual(_git(self.origin, "rev-parse", f"refs/heads/{branch}").strip(), second)
        self._assert_no_leftovers(routine, kept)

    def test_retro_publishes_without_touching_the_shared_checkout(self):
        self._check_flow(self.retro)

    def test_audit_publishes_without_touching_the_shared_checkout(self):
        self._check_flow(self.audit)

    def _check_merged_branch_is_not_continued(self, routine):
        """The morning's PR merged and its branch was deleted: a re-run starts again from main."""
        self._publish_flow(routine, "# morning\n")
        branch = f"{routine.prefix}/{DATE}"
        _git(self.origin, "update-ref", "-d", f"refs/heads/{branch}")
        self._publish_flow(routine, "# evening\n")
        tip = _git(self.origin, "rev-parse", f"refs/heads/{branch}").strip()
        self.assertEqual(_git(self.origin, "rev-parse", f"{tip}^").strip(), self.main_tip,
                         "the re-run continued a branch that no longer exists")

    def test_retro_does_not_continue_a_deleted_branch(self):
        self._check_merged_branch_is_not_continued(self.retro)

    def test_audit_does_not_continue_a_deleted_branch(self):
        self._check_merged_branch_is_not_continued(self.audit)

    def _check_readme_found(self, routine):
        """The README check sees a README that main already has, so the draft doesn't repeat it."""
        _write(self.seed, f"{routine.folder}/README.md", "# readme\n")
        _git(self.seed, "add", "-A")
        _git(self.seed, "commit", "-q", "-m", "readme")
        _git(self.seed, "push", "-q", "origin", "HEAD:refs/heads/main")
        for key in routine.order[: routine.order.index("readme")]:
            if key != "clear":
                self._run_ok(routine.blocks[key])
        self._run_ok(routine.blocks["readme"])

    def test_retro_readme_check_reads_the_right_place(self):
        self._check_readme_found(self.retro)

    def test_audit_readme_check_reads_the_right_place(self):
        self._check_readme_found(self.audit)

    def _check_failed_push(self, routine):
        blocks = routine.blocks
        before = self._snapshot()
        self._run_ok(blocks["fetch"])
        self._run_ok(blocks["create"])
        self._stage(routine, "# keep me\n")
        _git(self.shared, "remote", "set-url", "origin", _posix(os.path.join(self.base, "gone.git")))
        self.assertNotEqual(self._bash(blocks["publish"]).returncode, 0, "a failed push must fail the block")
        self._run_ok(blocks["remove"])
        self.assertEqual(self._snapshot(), before)
        self._assert_no_leftovers(routine)
        draft = os.path.join(self._stage_dir(routine), routine.report)
        self.assertEqual(_read(draft), "# keep me\n", "the failed run's draft must survive for recovery")

    def test_retro_failed_push_keeps_the_draft(self):
        self._check_failed_push(self.retro)

    def test_audit_failed_push_keeps_the_draft(self):
        self._check_failed_push(self.audit)

    def test_audit_drafts_before_it_creates_the_worktree(self):
        """A failed worktree create must still leave a draft for the failure notice to point at."""
        order = self.audit.order
        self.assertLess(order.index("clear"), order.index("create"))
        self.assertLess(order.index("readme"), order.index("create"))

    def test_retro_reads_fresh_content(self):
        self._run_ok(self.retro.blocks["fetch"])
        self._run_ok(self.retro.blocks["create"])
        journal = f"sessions/dev-env/{DATE}-journal.md"
        worktree = os.path.join(self.shared, ".claude", "worktrees", f"retro-{DATE}")
        self.assertTrue(os.path.isfile(os.path.join(worktree, *journal.split("/"))), "stale read root")
        self.assertFalse(os.path.isfile(os.path.join(self.shared, *journal.split("/"))), "fixture is wrong")
        text = _read(os.path.join(ROUTINES, "biweekly-retro", "SKILL.md"))
        blocks = shell_blocks(text)
        self.assertIsNone(re.search(r'\$\{?EJ\}?"?/sessions', "\n".join(blocks)),
                          "a block still reads the shared tree")
        # Each command block that reads $WT or the window must set them itself: shell state doesn't
        # carry over. Unlabelled fences here are notification templates, not commands.
        for block in shell_blocks(text, labels=("bash",)):
            for name in ("WT", "WINDOW_START", "RUN_DATE"):
                if re.search(rf"\$\{{?{name}\b", block):
                    with self.subTest(name=name, block=block[:60]):
                        self.assertRegex(block, rf"(?m)^{name}=")
        lines = [ln for ln in text.splitlines() if ln.startswith("PREVIOUS_RETRO=")]
        self.assertEqual(len(lines), 1)
        self.assertEqual(self._run_ok(lines[0] + '\necho "$PREVIOUS_RETRO"').strip(), NEWER,
                         "the previous retro must come from origin/main, not the shared tree")

    def test_old_sequence_is_detected_by_this_harness(self):
        """Rule 4: the invariant these cases assert does go red on the sequence dev-env#657 removed."""
        # Its checkout refuses over this one tracked edit, which is the "fails safe" #346 describes.
        _git(self.shared, "checkout", "--", f"sessions/dev-env/{DATE}_090000.stub.md")
        before = self._snapshot()
        self._run_ok(
            f'EJ="{SHARED}"\nRUN_DATE=YYYY-MM-DD\n'
            'git -C "$EJ" fetch origin\ngit -C "$EJ" checkout -b "retro/${RUN_DATE}" origin/main\n'
        )
        self.assertNotEqual(self._snapshot(), before)

    def test_leftover_check_sees_a_local_report_branch(self):
        """Rule 4 for _assert_no_leftovers: a local <prefix>/<date> branch must turn it red."""
        _git(self.shared, "branch", f"retro/{DATE}", "HEAD")
        with self.assertRaises(AssertionError):
            self._assert_no_leftovers(self.retro)


if __name__ == "__main__":
    unittest.main()
