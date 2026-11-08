#!/usr/bin/env python3
"""Routines must not move the shared engineering-journal checkout (dev-env#657, #346, ADR-082).

Required when changing `claude/routines/biweekly-retro/SKILL.md` or
`claude/routines/weekly-memory-audit/SKILL.md`, or adding a routine that touches engineering-journal.
Run: py -3 claude/scripts/tests/test_routine_journal_isolation.py

Two layers. A static check over every routine: nothing passes engineering-journal to
`sync-routine-worktree`, and no fenced bash block moves that checkout's branch or index. An end-to-end
run of each report routine's own extracted blocks against a throwaway bare origin, with a fake `gh`:
the shared checkout is byte-identical afterwards, and the report still reaches its own branch and PR.

Scope gaps: the static check only reads fenced bash blocks and the `REPO` parameter, so `--git-dir` or
a path built at run time goes unseen; the end-to-end layer covers the two routines that write reports.
"""
import os
import re
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

# `merge` is deliberately absent: retro-chain-backstop's documented fetch + `merge --ff-only` cannot
# switch a branch or rewrite a session's commits, and refuses on a dirty conflict.
MOVING_VERBS = (
    "checkout|switch|rebase|reset|pull|commit|add|stash|cherry-pick|revert|restore|rm|mv|am|apply"
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def fenced_bash(text):
    return re.findall(r"^[ \t]*```bash[ \t]*\r?\n(.*?)^[ \t]*```", text, re.MULTILINE | re.DOTALL)


def shared_names(text):
    """Variables a routine points at the shared checkout, e.g. `EJ`."""
    return set(re.findall(rf'^\s*(\w+)="?{re.escape(SHARED)}"?\s*(?:#.*)?$', text, re.MULTILINE))


def violations(text):
    """Every way this routine text would rebase, reset or switch the shared checkout."""
    found = []
    names = shared_names(text)
    variable = "|".join(sorted(names)) or r"(?!)"
    # The closing lookahead keeps "$EJ/.claude/worktrees/x" and "$WT" out: only the checkout itself.
    target = rf'"?(?:{re.escape(SHARED)}|\$\{{?(?:{variable})\}}?)"?(?=\s|$)'
    for value in re.findall(r"`?\bREPO`?\s*=\s*`?([^\s`,]+)", text):
        if "engineering-journal" in value or value.lstrip("$").strip("{}\"") in names:
            found.append(f"sync-routine-worktree REPO={value}")
    for block in fenced_bash(text):
        for match in re.finditer(rf"git\s+-C\s+{target}\s+(?:-c\s+\S+\s+)*({MOVING_VERBS})\b", block):
            found.append(f"git -C <shared> {match.group(1)}")
        if re.search(rf"^\s*cd\s+{target}", block, re.MULTILINE):
            found.append("cd <shared>")
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
KNOWN_BAD = {
    "REPO parameter": OLD_STEP_0,
    "REPO in prose": "Invoke `sync-routine-worktree` with `REPO=C:/Users/brown/Git/engineering-journal`.\n",
    "REPO through a variable": '```bash\nEJ="C:/Users/brown/Git/engineering-journal"\n```\nUse `REPO=$EJ`.\n',
    "cd then checkout": OLD_RETRO_BLOCK,
    "git -C checkout": OLD_AUDIT_BLOCK,
    "literal path rebase": "```bash\ngit -C C:/Users/brown/Git/engineering-journal rebase origin/main\n```\n",
    "git -C with a -c option": '```bash\nJ="C:/Users/brown/Git/engineering-journal"\ngit -C "$J" -c a=b reset --hard\n```\n',
}
KNOWN_GOOD = {
    "read-only queries": (
        "```bash\ngit -C C:/Users/brown/Git/engineering-journal fetch origin\n"
        "git -C C:/Users/brown/Git/engineering-journal ls-tree -r origin/main --name-only\n```\n"
    ),
    "worktree registration": (
        '```bash\nEJ="C:/Users/brown/Git/engineering-journal"\nWT="$EJ/.claude/worktrees/x"\n'
        'git -C "$EJ" worktree add --detach "$WT" refs/remotes/origin/main\n'
        'git -C "$WT" checkout --detach refs/remotes/origin/x\ngit -C "$WT" add sessions\n'
        'git -C "$WT" commit -m x\n```\n'
    ),
    "another repo's sync": "- `REPO` = `C:/Users/brown/Git/career-playbook`\n",
    "a routine's own root": "Invoke `sync-routine-worktree` with `REPO=$WORKTREE_ROOT`.\n",
    "prose naming the old call": "The prior `sync-routine-worktree` call could rebase engineering-journal.\n",
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
            self.assertGreater(len(fenced_bash(text)), 3, f"{name}: no fenced blocks extracted")

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
    done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8")
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

    MARKERS = ("fetch origin", "worktree add --detach", 'push origin "HEAD:refs/heads/', "gh pr create",
               'worktree remove --force "$WT"')

    def __init__(self, name, prefix, folder, report):
        self.name, self.prefix, self.folder, self.report = name, prefix, folder, report
        text = _read(os.path.join(ROUTINES, name, "SKILL.md"))
        blocks = fenced_bash(text)
        self.blocks = []
        for marker in self.MARKERS:
            matches = [b for b in blocks if marker in b]
            if len(matches) != 1:
                raise AssertionError(f"{name}: {len(matches)} blocks contain {marker!r}, expected 1")
            self.blocks.append(matches[0])
        positions = [text.index(b) for b in self.blocks]
        if positions != sorted(positions):
            raise AssertionError(f"{name}: blocks are out of order")


RETRO = Routine("biweekly-retro", "retro", "sessions/meta/retro", f"{DATE}-retro.md")
AUDIT = Routine("weekly-memory-audit", "memory-audit", "sessions/meta/memory-audit", f"{DATE}-audit.md")


class EndToEnd(unittest.TestCase):
    """A bare origin, a shared checkout mid-session on a draft branch, and a stale origin/main ref."""

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

        os.makedirs(self.bin)
        fake_gh = os.path.join(self.bin, "gh")
        with open(fake_gh, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$GH_LOG"\n')
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
                              env=env, cwd=self.base)

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

    def _stage(self, routine, report):
        """What the Write tool does: the draft lands in scratch, outside every worktree."""
        folder = f"{routine.prefix}-{DATE}"
        _write(self.scratch, f"{folder}/{routine.report}", report)
        _write(self.scratch, f"{folder}/README.md", "# readme\n")

    def _publish_flow(self, routine, report):
        fetch, create, publish, pr, remove = routine.blocks
        before = self._snapshot()
        self._run_ok(fetch)
        self._run_ok(create)
        self.assertEqual(self._snapshot(), before, "creating the worktree changed the shared checkout")
        self._stage(routine, report)
        self._run_ok(publish)
        self._run_ok(pr)
        self._run_ok(remove)
        self.assertEqual(self._snapshot(), before, "the routine changed the shared checkout")

    def _assert_no_leftovers(self, routine):
        listed = _git(self.shared, "worktree", "list", "--porcelain")
        self.assertEqual(listed.count("worktree "), 1, listed)
        self.assertEqual(_git(self.shared, "for-each-ref", f"refs/heads/{routine.prefix}*"), "")

    def _check_flow(self, routine):
        """A crashed run's worktree, then a first run, then a same-day re-run, then one with nothing new."""
        stale = os.path.join(self.shared, ".claude", "worktrees", f"{routine.prefix}-{PRIOR}")
        os.makedirs(os.path.dirname(stale))
        _git(self.shared, "worktree", "add", "-q", "--detach", _posix(stale), "HEAD")

        self._publish_flow(routine, "# first\n")
        self.assertFalse(os.path.exists(stale), "a crashed run's worktree was left behind")
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
        self._assert_no_leftovers(routine)

        self._publish_flow(routine, "# second\n")
        second = _git(self.origin, "rev-parse", f"refs/heads/{branch}").strip()
        self.assertEqual(_git(self.origin, "rev-parse", f"{second}^").strip(), first, "not a fast-forward")
        self.assertEqual(_git(self.origin, "show", f"{second}:{routine.folder}/{routine.report}"), "# second\n")

        self._publish_flow(routine, "# second\n")  # nothing new to commit must not fail the run
        self.assertEqual(_git(self.origin, "rev-parse", f"refs/heads/{branch}").strip(), second)
        self._assert_no_leftovers(routine)

    def test_retro_publishes_without_touching_the_shared_checkout(self):
        self._check_flow(RETRO)

    def test_audit_publishes_without_touching_the_shared_checkout(self):
        self._check_flow(AUDIT)

    def _check_failed_push(self, routine):
        fetch, create, publish, _pr, remove = routine.blocks
        before = self._snapshot()
        self._run_ok(fetch)
        self._run_ok(create)
        self._stage(routine, "# keep me\n")
        _git(self.shared, "remote", "set-url", "origin", _posix(os.path.join(self.base, "gone.git")))
        self.assertNotEqual(self._bash(publish).returncode, 0, "a failed push must fail the block")
        self._run_ok(remove)
        self.assertEqual(self._snapshot(), before)
        self._assert_no_leftovers(routine)
        draft = os.path.join(self.scratch, f"{routine.prefix}-{DATE}", routine.report)
        self.assertEqual(_read(draft), "# keep me\n", "the failed run's draft must survive for recovery")

    def test_retro_failed_push_keeps_the_draft(self):
        self._check_failed_push(RETRO)

    def test_audit_failed_push_keeps_the_draft(self):
        self._check_failed_push(AUDIT)

    def test_retro_reads_fresh_content(self):
        fetch, create = RETRO.blocks[:2]
        self._run_ok(fetch)
        self._run_ok(create)
        journal = f"sessions/dev-env/{DATE}-journal.md"
        worktree = os.path.join(self.shared, ".claude", "worktrees", f"retro-{DATE}")
        self.assertTrue(os.path.isfile(os.path.join(worktree, *journal.split("/"))), "stale read root")
        self.assertFalse(os.path.isfile(os.path.join(self.shared, *journal.split("/"))), "fixture is wrong")
        text = _read(os.path.join(ROUTINES, "biweekly-retro", "SKILL.md"))
        self.assertIsNone(re.search(r'\$\{?EJ\}?"?/sessions', "\n".join(fenced_bash(text))),
                          "a block still reads the shared tree")
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


if __name__ == "__main__":
    unittest.main()
