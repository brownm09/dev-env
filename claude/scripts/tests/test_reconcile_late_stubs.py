#!/usr/bin/env python3
"""End-to-end tests for reconcile-late-stubs.py (dev-env#1203).

Runs the script's real main() against a throwaway bare origin and clone. Only `gh` is
faked: the fake answers `gh pr list --head <head>` from a per-test table, and every git
command runs for real, so what is asserted is the tree actually pushed to origin.

The property that matters is the one `/journal-compose` depends on: it finds stubs with
`sessions/*/<DATE>_*.stub.md`, so a moved stub must carry the target branch's date
(`compose_stubs` applies that same glob). The second is the lookup: a day whose draft
conflicted merges from compose/<D>, so the merge has to be found there too.

Each test runs in its own process, so a run starts up to 8 of them at once (dev-env#1269).

Usage:
    py -3 claude/scripts/tests/test_reconcile_late_stubs.py
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "reconcile_late_stubs", SCRIPTS / "reconcile-late-stubs.py"
)
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)

REAL_RUN = subprocess.run
BEFORE_MERGE = "2026-11-03T20:00:00Z"
MERGED_AT = "2026-11-04T12:00:00Z"
AFTER_MERGE = "2026-11-04T18:00:00Z"
TODAY = "2026-11-05"

STUB = "# p · 2026-11-03 · Session 1 — late\n"
OTHER_STUB = "# p · 2026-11-04 · a different session\n"


def manifest(stub_field: str) -> str:
    return json.dumps({
        "stub": stub_field, "topic": "late — work", "tokens": {"input": 0, "output": 0, "cost": 0},
        "prs_opened": [], "prs_closed": [],
    }) + "\n"


# What a test's repos start as. main() builds each once and every test copies one, since
# building them runs many real git commands.
MAIN_ONLY = "main-only"
LATE_STUB_AND_TARGET = "late-stub-and-target"
templates: Path

# Each test runs in its own process, because it patches the script's module globals. A real
# main() costs about 24 git processes and those are the bulk of the time, so this is what
# keeps the file well inside the runner's limit when other sessions are using the machine.
WORKERS = min(8, os.cpu_count() or 1)


class Journal:
    """A bare origin plus a clone, on `main` with one composed day."""

    def __init__(self, root: Path, start: str | None = MAIN_ONLY):
        self.prs: dict[str, list[dict]] = {}
        self.gh_error = False
        self.delete_fails = False
        self.origin = root / "origin.git"
        self.clone = root / "clone"
        if start is None:
            self.create()
            return
        shutil.copytree(templates / start / "origin.git", self.origin)
        shutil.copytree(templates / start / "clone", self.clone)
        self.git("remote", "set-url", "origin", str(self.origin))

    def create(self) -> None:
        no_hooks = self.clone.parent / "no-hooks"
        no_hooks.mkdir(parents=True)
        # An empty --template leaves out the sample hooks, so there is less to copy per test.
        REAL_RUN(["git", "init", "--template=", "--bare", "-b", "main", str(self.origin)], capture_output=True, check=True)
        REAL_RUN(["git", "clone", "--template=", str(self.origin), str(self.clone)], capture_output=True, check=True)
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "core.autocrlf", "false")
        # The machine's global pre-push hook must not run against the fixture.
        self.git("config", "core.hooksPath", str(no_hooks))
        # Throwaway repos need no durable writes or background upkeep, on either side of a push.
        for key, value in (("core.fsync", "none"), ("gc.auto", "0"),
                           ("maintenance.auto", "false"), ("receive.autogc", "false")):
            self.git("config", key, value)
            self.origin_git("config", key, value)
        self.git("checkout", "-b", "main")
        self.commit({"sessions/p/2026-11-02-composed.md": "composed\n"}, BEFORE_MERGE)
        self.git("push", "origin", "main")

    def origin_git(self, *args: str) -> str:
        """Read origin itself: what was pushed, with no fetch in between."""
        return self.git(f"--git-dir={self.origin}", *args)

    def git(self, *args: str, env: dict | None = None) -> str:
        result = REAL_RUN(
            ["git", *args], cwd=self.clone, capture_output=True,
            env={**os.environ, **(env or {})},
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {args}: {result.stderr.decode('utf-8', 'replace')}")
        return result.stdout.decode("utf-8")

    def commit(self, files: dict[str, str], when: str) -> None:
        for rel_path, content in files.items():
            path = self.clone / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        self.git("add", "-A")
        self.git(
            "commit", "-m", f"commit at {when}",
            env={"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when},
        )

    def branch(self, name: str, start: str = "origin/main") -> None:
        # Only this clone pushes to origin, so its remote-tracking refs are already current.
        self.git("checkout", "-b", name, start)

    def push(self, name: str) -> None:
        self.git("push", "origin", name)

    def compose_day(self, date: str) -> None:
        """Land a composed journal for `date` on main, as a merged PR would."""
        self.git("checkout", "main")
        self.commit({f"sessions/p/{date}-composed.md": "composed\n"}, MERGED_AT)
        self.git("push", "origin", "main")

    def source_with_late_stub(self, stub_field: str = "sessions/p/2026-11-03_120000.stub.md") -> None:
        """draft/2026-11-03 holds a composed-early stub and a stub committed after the merge."""
        self.branch("draft/2026-11-03")
        self.commit({"sessions/p/2026-11-03_080000.stub.md": "early, already composed\n"}, BEFORE_MERGE)
        self.commit({
            "sessions/p/2026-11-03_120000.stub.md": STUB,
            "sessions/p/2026-11-03_120000.manifest.jsonl": manifest(stub_field),
        }, AFTER_MERGE)
        self.push("draft/2026-11-03")
        self.compose_day("2026-11-03")

    def target(self, files: dict[str, str] | None = None) -> None:
        self.branch("draft/2026-11-04")
        self.commit(files or {"sessions/p/2026-11-04_090000.stub.md": OTHER_STUB}, BEFORE_MERGE)
        self.push("draft/2026-11-04")

    def source_with_composed_pair(self, stub_field: str = "sessions/p/2026-11-03_120000.stub.md") -> None:
        """draft/2026-11-03 holds a stub and manifest committed before the merge. The
        branch stays checked out so a test can add a late commit."""
        self.branch("draft/2026-11-03")
        self.commit({
            "sessions/p/2026-11-03_120000.stub.md": STUB,
            "sessions/p/2026-11-03_120000.manifest.jsonl": manifest(stub_field),
        }, BEFORE_MERGE)
        self.push("draft/2026-11-03")

    def fake_run(self, cmd, *args, **kwargs):
        if self.delete_fails and "--delete" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="remote hung up")
        if cmd[0] != "gh":
            return REAL_RUN(cmd, *args, **kwargs)
        if self.gh_error:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="API rate limit exceeded")
        head = cmd[cmd.index("--head") + 1]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(self.prs.get(head, [])), stderr="")

    def reconcile(self, source: str = "draft/2026-11-03") -> int:
        """Run main(); returns its exit code (0 when it returns normally)."""
        with mock.patch.object(reconcile, "JOURNAL_REPO", self.clone), \
                mock.patch.object(reconcile, "SCRATCH", self.clone.parent / "scratch"), \
                mock.patch.object(reconcile, "TODAY", TODAY), \
                mock.patch.object(sys, "argv", ["reconcile-late-stubs.py", source]), \
                mock.patch.object(reconcile.subprocess, "run", self.fake_run):
            try:
                reconcile.main()
            except SystemExit as e:
                return int(e.code or 0)
        return 0

    def tree(self, branch: str) -> list[str]:
        out = self.origin_git("ls-tree", "-r", "--name-only", f"refs/heads/{branch}", "sessions/")
        return out.splitlines()

    def show(self, branch: str, rel_path: str) -> str:
        return self.origin_git("show", f"refs/heads/{branch}:{rel_path}")

    def compose_stubs(self, branch: str) -> list[str]:
        """The stubs /journal-compose would find: sessions/*/<DATE>_*.stub.md."""
        date = branch.removeprefix("draft/")
        return [
            p for p in self.tree(branch)
            if p.count("/") == 2 and p.split("/")[-1].startswith(f"{date}_") and p.endswith(".stub.md")
        ]

    def remote_has(self, branch: str) -> bool:
        return bool(self.origin_git("for-each-ref", f"refs/heads/{branch}").strip())


def merged(number: int = 310, at: str = MERGED_AT) -> dict:
    return {"number": number, "mergedAt": at, "url": f"https://example.test/pull/{number}"}


def build_templates() -> None:
    Journal(templates / MAIN_ONLY, start=None)
    late = Journal(templates / LATE_STUB_AND_TARGET)
    late.source_with_late_stub()
    late.target()


def with_journal(test, start: str = MAIN_ONLY):
    def run_test():
        # A file another process still holds at cleanup says nothing about the test.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            test(Journal(Path(tmp), start))
    run_test.__name__ = test.__name__
    return run_test


def with_late_stub_and_target(test):
    """Starts from Journal.source_with_late_stub() followed by Journal.target()."""
    return with_journal(test, LATE_STUB_AND_TARGET)


@with_late_stub_and_target
def test_moved_stub_takes_target_date_and_compose_finds_it(j):
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.compose_stubs("draft/2026-11-04")
    tree = j.tree("draft/2026-11-04")
    assert "sessions/p/2026-11-03_120000.stub.md" not in tree
    assert "sessions/p/2026-11-03_120000.manifest.jsonl" not in tree
    assert j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.stub.md").replace("\r\n", "\n") == STUB
    entry = json.loads(j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.manifest.jsonl"))
    assert entry["stub"] == "sessions/p/2026-11-04_120000.stub.md", entry["stub"]
    assert not j.remote_has("draft/2026-11-03"), "source branch should be deleted"


@with_late_stub_and_target
def test_manifest_moves_with_stub_and_names_its_new_path(j):
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    entry = json.loads(j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.manifest.jsonl"))
    assert entry["stub"] == "sessions/p/2026-11-04_120000.stub.md", entry["stub"]
    assert entry["topic"] == "late — work", "other fields, non-ASCII included, must survive"


@with_journal
def test_bare_filename_stub_field_stays_bare(j):
    j.source_with_late_stub(stub_field="2026-11-03_120000.stub.md")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    entry = json.loads(j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.manifest.jsonl"))
    assert entry["stub"] == "2026-11-04_120000.stub.md", entry["stub"]


@with_late_stub_and_target
def test_day_merged_from_compose_branch_is_found(j):
    j.prs["compose/2026-11-03"] = [merged()]  # no PR at draft/2026-11-03
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")


@with_late_stub_and_target
def test_latest_merge_decides_what_is_late_when_both_heads_merged(j):
    """Real shape: 2026-07-08 merged from draft/ and, a day later, from compose/."""
    j.prs["draft/2026-11-03"] = [merged(300, at="2026-11-03T22:00:00Z")]
    j.prs["compose/2026-11-03"] = [merged(310, at="2026-11-04T23:00:00Z")]
    assert j.reconcile() == 0
    # The stub was committed after the first merge but before the last, so it was composed.
    assert "sessions/p/2026-11-04_120000.stub.md" not in j.tree("draft/2026-11-04")


@with_late_stub_and_target
def test_no_merged_pr_at_either_head_aborts_and_keeps_branch(j):
    assert j.reconcile() == 1
    assert j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_failed_lookup_aborts_and_keeps_branch(j):
    j.gh_error = True
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "gh" in str(e)
    else:
        raise AssertionError("a failed lookup must not read as 'no PR' or carry on")
    assert j.remote_has("draft/2026-11-03")


@with_journal
def test_name_collision_keeps_both_sessions_and_the_pair_together(j):
    j.source_with_late_stub()
    j.target({"sessions/p/2026-11-04_120000.stub.md": OTHER_STUB})
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    assert j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.stub.md").replace("\r\n", "\n") == OTHER_STUB
    assert j.show("draft/2026-11-04", "sessions/p/2026-11-04_120001.stub.md").replace("\r\n", "\n") == STUB
    entry = json.loads(j.show("draft/2026-11-04", "sessions/p/2026-11-04_120001.manifest.jsonl"))
    assert entry["stub"] == "sessions/p/2026-11-04_120001.stub.md"


@with_late_stub_and_target
def test_rerun_after_failed_delete_does_not_duplicate_the_session(j):
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    # The source branch survives the first run's failed delete: put it back.
    source_tip = j.git("rev-parse", "draft/2026-11-03").strip()
    j.git("push", "origin", f"{source_tip}:refs/heads/draft/2026-11-03")
    assert j.reconcile() == 0
    assert j.compose_stubs("draft/2026-11-04") == [
        "sessions/p/2026-11-04_090000.stub.md", "sessions/p/2026-11-04_120000.stub.md",
    ]
    assert not j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_stub_renamed_in_a_late_commit_still_moves(j):
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "draft/2026-11-03")
    j.git("mv", "sessions/p/2026-11-03_120000.stub.md", "sessions/p/2026-11-03_120500.stub.md")
    j.git("commit", "-m", "rename", env={"GIT_COMMITTER_DATE": AFTER_MERGE, "GIT_AUTHOR_DATE": AFTER_MERGE})
    j.push("draft/2026-11-03")
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120500.stub.md" in j.tree("draft/2026-11-04")


@with_journal
def test_legacy_per_day_manifest_is_split_into_a_shard_for_the_moved_stub(j):
    j.branch("draft/2026-11-03")
    entry = {"stub": "2026-11-03_120000.stub.md", "topic": "opened", "prs_opened": [7], "prs_closed": [],
             "tokens": {"input": 9, "output": 9, "cost": 2.0}}
    closed = {**entry, "topic": "merged", "prs_opened": [], "prs_closed": [7],
              "tokens": {"input": 0, "output": 0, "cost": 0}}
    other = {**entry, "stub": "2026-11-03_130000.stub.md", "topic": "another session"}
    legacy = "".join(json.dumps(e) + "\n" for e in (entry, closed, other))
    j.commit({
        "sessions/p/2026-11-03_120000.stub.md": STUB,
        "sessions/p/2026-11-03.manifest.jsonl": legacy,
    }, AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    tree = j.tree("draft/2026-11-04")
    assert "sessions/p/2026-11-03.manifest.jsonl" not in tree
    assert "sessions/p/2026-11-04.manifest.jsonl" not in tree
    shard = json.loads(j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.manifest.jsonl"))
    assert shard == {
        "stub": "2026-11-04_120000.stub.md", "topic": "merged", "prs_opened": [7], "prs_closed": [7],
        "tokens": {"input": 9, "output": 9, "cost": 2.0},
    }


@with_late_stub_and_target
def test_unreadable_legacy_manifest_aborts_before_anything_moves(j):
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "draft/2026-11-03")
    j.git("rm", "sessions/p/2026-11-03_120000.manifest.jsonl")
    j.commit({"sessions/p/2026-11-03.manifest.jsonl": "not json\n"}, AFTER_MERGE)
    j.push("draft/2026-11-03")
    before = j.tree("draft/2026-11-04")
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "2026-11-03.manifest.jsonl" in str(e)
    else:
        raise AssertionError("an unreadable manifest must stop the move")
    assert j.tree("draft/2026-11-04") == before
    assert j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_second_run_after_target_composed_does_not_move_the_stub_again(j):
    """dev-env#1262: without the tag, the second run picks a later branch and duplicates it."""
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    source_tip = j.git("rev-parse", "draft/2026-11-03").strip()
    assert j.git("ls-remote", "origin", "refs/tags/reconciled/draft/2026-11-03").startswith(source_tip)
    # The target composes, so a repeat run would now choose today's branch. The source
    # branch still exists, as it does after a failed delete or a push from another clone.
    j.compose_day("2026-11-04")
    j.git("push", "origin", f"{source_tip}:refs/heads/draft/2026-11-03")
    assert j.reconcile() == 0
    assert not j.remote_has(f"draft/{TODAY}"), "the repeat run moved the stub to another branch"
    assert not j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_stub_pushed_after_a_reconcile_still_moves_and_the_old_one_does_not(j):
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    j.compose_day("2026-11-04")
    j.git("checkout", "draft/2026-11-03")
    j.commit({"sessions/p/2026-11-03_180000.stub.md": "a later stub\n"}, AFTER_MERGE)
    j.git("push", "origin", "draft/2026-11-03")
    assert j.reconcile() == 0
    assert j.compose_stubs(f"draft/{TODAY}") == [f"sessions/p/{TODAY}_180000.stub.md"]


@with_late_stub_and_target
def test_stubs_moved_by_hand_and_marked_are_not_moved_by_a_later_run(j):
    j.prs["draft/2026-11-03"] = [merged()]
    source_tip = j.git("rev-parse", "draft/2026-11-03").strip()
    j.git("push", "origin", f"{source_tip}:refs/tags/reconciled/draft/2026-11-03")
    before = j.tree("draft/2026-11-04")
    assert j.reconcile() == 0
    assert j.tree("draft/2026-11-04") == before
    assert not j.remote_has("draft/2026-11-03")


@with_journal
def test_suffixed_branch_uses_its_days_merged_pr_and_moves_its_stubs(j):
    j.branch("draft/2026-11-03-late")
    j.commit({"sessions/p/2026-11-03_120000.stub.md": STUB}, AFTER_MERGE)
    j.push("draft/2026-11-03-late")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile("draft/2026-11-03-late") == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")
    assert not j.remote_has("draft/2026-11-03-late")


@with_journal
def test_suffixed_branch_with_commits_before_the_merge_is_kept(j):
    """It never merged itself, so the day's cutoff doesn't show those commits were composed."""
    j.branch("draft/2026-11-03-late")
    j.commit({"sessions/p/2026-11-03_120000.stub.md": STUB}, BEFORE_MERGE)
    j.push("draft/2026-11-03-late")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    stderr = io.StringIO()
    with mock.patch.object(sys, "stderr", stderr):
        assert j.reconcile("draft/2026-11-03-late") == 1
    assert "never merged itself" in stderr.getvalue(), stderr.getvalue()
    assert j.remote_has("draft/2026-11-03-late")


@with_late_stub_and_target
def test_late_file_of_an_unknown_kind_stops_the_run_and_keeps_the_branch(j):
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "draft/2026-11-03")
    j.commit({"sessions/p/retros/2026-11-03-retro.md": "a late retro\n"}, AFTER_MERGE)
    j.push("draft/2026-11-03")
    before = j.tree("draft/2026-11-04")
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "retros/2026-11-03-retro.md" in str(e), str(e)
    else:
        raise AssertionError("content the script can't move must not be deleted with the branch")
    assert j.tree("draft/2026-11-04") == before
    assert j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_commit_pushed_during_the_run_survives_the_delete(j):
    """The delete is conditional on the tip the run read."""
    j.prs["draft/2026-11-03"] = [merged()]
    real_fake_run = j.fake_run

    def push_during_the_run(cmd, *args, **kwargs):
        if "--delete" in cmd:
            j.git("checkout", "draft/2026-11-03")
            j.commit({"sessions/p/2026-11-03_190000.stub.md": "arrived mid-run\n"}, AFTER_MERGE)
            j.git("push", "origin", "draft/2026-11-03")
        return real_fake_run(cmd, *args, **kwargs)

    j.fake_run = push_during_the_run
    stderr = io.StringIO()
    with mock.patch.object(sys, "stderr", stderr):
        assert j.reconcile() == 1
    assert j.remote_has("draft/2026-11-03"), "the delete removed a commit it never saw"
    assert "sessions/p/2026-11-03_190000.stub.md" in j.tree("draft/2026-11-03")


@with_late_stub_and_target
def test_failed_tag_push_also_leaves_the_target_unmoved(j):
    """The files and the tag land together, so nothing can be moved without its mark."""
    j.prs["draft/2026-11-03"] = [merged()]
    # A stale local view of the target rejects the atomic push as a whole.
    j.branch("other", "origin/draft/2026-11-04")
    j.commit({"sessions/p/2026-11-04_100000.stub.md": "landed first\n"}, BEFORE_MERGE)
    real_fake_run = j.fake_run

    def race_the_target(cmd, *args, **kwargs):
        if "--atomic" in cmd:
            j.git("push", "origin", "other:refs/heads/draft/2026-11-04")
        return real_fake_run(cmd, *args, **kwargs)

    j.fake_run = race_the_target
    try:
        j.reconcile()
    except RuntimeError:
        pass
    else:
        raise AssertionError("a rejected atomic push must stop the run")
    assert not j.git("ls-remote", "origin", "refs/tags/reconciled/draft/2026-11-03").strip()
    assert "sessions/p/2026-11-04_120000.stub.md" not in j.tree("draft/2026-11-04")
    assert j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_rewritten_branch_stops_rather_than_moving_the_tag(j):
    j.prs["draft/2026-11-03"] = [merged()]
    old_tip = j.git("rev-parse", "draft/2026-11-03").strip()
    j.git("push", "origin", f"{old_tip}:refs/tags/reconciled/draft/2026-11-03")
    j.git("push", "--force", "origin", "origin/main:refs/heads/draft/2026-11-03")
    stderr = io.StringIO()
    with mock.patch.object(sys, "stderr", stderr):
        assert j.reconcile() == 1
    assert j.git("ls-remote", "origin", "refs/tags/reconciled/draft/2026-11-03").startswith(old_tip)
    assert j.remote_has("draft/2026-11-03")


@with_journal
def test_late_commit_with_an_older_child_is_still_found(j):
    """Clock skew: `git log --after` would stop at the child and hide the late parent."""
    j.branch("draft/2026-11-03")
    j.commit({"sessions/p/2026-11-03_120000.stub.md": STUB}, AFTER_MERGE)
    j.commit({"sessions/p/2026-11-03_130000.stub.md": "committed on a slow clock\n"}, BEFORE_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")


@with_journal
def test_commits_from_main_on_a_branch_cut_later_are_not_late(j):
    """A branch cut from main after the compose holds main's own later commits."""
    j.compose_day("2026-11-03")
    j.git("checkout", "main")
    j.commit({"sessions/p/2026-11-02_120000.stub.md": "already on main\n"}, AFTER_MERGE)
    j.git("push", "origin", "main")
    j.branch("draft/2026-11-03")
    j.push("draft/2026-11-03")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" not in j.tree("draft/2026-11-04")
    assert not j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_report_dated_for_uncomposed_day_does_not_mark_it_composed(j):
    """A report dated 2026-11-04 on main must not make draft/2026-11-04 look composed."""
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "main")
    j.commit({"sessions/p/reports/2026-11-04-analysis.md": "report\n"}, MERGED_AT)
    j.git("push", "origin", "main")
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")
    assert not j.remote_has(f"draft/{TODAY}"), "should not have fallen through to today's branch"


@with_late_stub_and_target
def test_composed_target_day_is_skipped_for_todays_branch_with_todays_date(j):
    j.prs["draft/2026-11-03"] = [merged()]
    j.compose_day("2026-11-04")
    assert j.reconcile() == 0
    assert f"sessions/p/{TODAY}_120000.stub.md" in j.compose_stubs(f"draft/{TODAY}")


@with_journal
def test_no_late_commits_deletes_branch_and_moves_nothing(j):
    j.branch("draft/2026-11-03")
    j.commit({"sessions/p/2026-11-03_080000.stub.md": "early\n"}, BEFORE_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["compose/2026-11-03"] = [merged()]
    before = j.tree("draft/2026-11-04")
    assert j.reconcile() == 0
    assert j.tree("draft/2026-11-04") == before
    assert not j.remote_has("draft/2026-11-03")


@with_journal
def test_todays_branch_is_kept_rather_than_deleted_with_its_stubs(j):
    """The fallback target is today's branch, which here is the source itself."""
    j.branch(f"draft/{TODAY}")
    j.commit({f"sessions/p/{TODAY}_120000.stub.md": STUB}, AFTER_MERGE)
    j.push(f"draft/{TODAY}")
    j.prs[f"draft/{TODAY}"] = [merged()]
    with mock.patch.object(sys, "stderr", io.StringIO()):
        assert j.reconcile(f"draft/{TODAY}") == 1
    assert f"sessions/p/{TODAY}_120000.stub.md" in j.tree(f"draft/{TODAY}")


@with_journal
def test_late_stub_brings_its_manifest_along(j):
    j.source_with_composed_pair()
    j.commit({"sessions/p/2026-11-03_120000.stub.md": STUB + "edited late\n"}, AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["compose/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    entry = json.loads(j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.manifest.jsonl"))
    assert entry["stub"] == "sessions/p/2026-11-04_120000.stub.md", entry["stub"]


@with_journal
def test_late_manifest_alone_is_not_moved_into_another_day(j):
    """A late prs_closed edit is bookkeeping for a session already composed."""
    j.source_with_composed_pair()
    j.commit({"sessions/p/2026-11-03_120000.manifest.jsonl":
              manifest("sessions/p/2026-11-03_120000.stub.md").replace('"prs_closed": []', '"prs_closed": [9]')},
             AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["compose/2026-11-03"] = [merged()]
    before = j.tree("draft/2026-11-04")
    assert j.reconcile() == 0
    assert j.tree("draft/2026-11-04") == before
    assert not j.remote_has("draft/2026-11-03")


@with_journal
def test_late_reports_and_tile_shards_survive_unrenamed(j):
    j.source_with_composed_pair()
    j.commit({
        "sessions/p/reports/2026-11-03-audit.md": "report\n",
        "sessions/p/tiles/55.json": '{"issue":55}\n',
    }, AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["compose/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    tree = j.tree("draft/2026-11-04")
    assert "sessions/p/reports/2026-11-03-audit.md" in tree
    assert "sessions/p/tiles/55.json" in tree
    assert "sessions/p/2026-11-04_120000.stub.md" not in tree, "no late stub in this commit"
    assert not j.remote_has("draft/2026-11-03")


@with_journal
def test_abort_on_the_way_to_todays_branch_leaves_the_remote_alone(j):
    """With no later draft the target is today's branch, which must not be created
    for a run that is about to stop."""
    j.branch("draft/2026-11-03")
    j.commit({"sessions/p/notes.stub.md": STUB}, AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.prs["draft/2026-11-03"] = [merged()]
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "notes.stub.md" in str(e)
    else:
        raise AssertionError("a stub with no YYYY-MM-DD_HHMMSS name must stop the run")
    assert not j.remote_has(f"draft/{TODAY}")


@with_journal
def test_manifest_with_no_stub_field_stops_and_names_the_file(j):
    """Real shape: four 2026-06-29 shards have no stub field."""
    j.branch("draft/2026-11-03")
    j.commit({
        "sessions/p/2026-11-03_120000.stub.md": STUB,
        "sessions/p/2026-11-03_120000.manifest.jsonl": '{"topic":"t"}\n',
    }, AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.compose_day("2026-11-03")
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "2026-11-03_120000.manifest.jsonl" in str(e), str(e)
    else:
        raise AssertionError("a manifest with no stub field must stop the run")
    assert j.remote_has("draft/2026-11-03")


@with_late_stub_and_target
def test_failed_delete_keeps_the_mark_so_a_rerun_moves_nothing(j):
    j.prs["draft/2026-11-03"] = [merged()]
    j.delete_fails = True
    stderr = io.StringIO()
    with mock.patch.object(sys, "stderr", stderr):
        assert j.reconcile() == 1
    assert "git push origin --delete draft/2026-11-03" in stderr.getvalue(), stderr.getvalue()
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")
    j.delete_fails = False
    j.compose_day("2026-11-04")
    assert j.reconcile() == 0
    assert not j.remote_has(f"draft/{TODAY}"), "the rerun moved the stub again"
    assert not j.remote_has("draft/2026-11-03")


def run_one(name: str) -> int:
    try:
        globals()[name]()
        print(f"PASS: {name}")
        return 0
    except AssertionError as e:
        print(f"FAIL: {name}: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3, file=sys.stdout)
    return 1


def main() -> int:
    global templates
    if len(sys.argv) == 3:  # A child of the run below: <test name> <templates directory>
        templates = Path(sys.argv[2])
        return run_one(sys.argv[1])

    tests = sorted(k for k, v in globals().items() if k.startswith("test_") and callable(v))
    failed = 0
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        templates = Path(tmp)
        build_templates()
        with ThreadPoolExecutor(WORKERS) as pool:
            for result in pool.map(
                lambda name: subprocess.run([sys.executable, __file__, name, tmp], capture_output=True),
                tests,
            ):
                # Bytes as the child wrote them, so a character this console can't encode can't
                # crash this. Written as each finishes, so a run killed at the runner's limit
                # still shows which tests passed.
                sys.stdout.buffer.write(result.stdout + result.stderr)
                sys.stdout.buffer.flush()
                # A child's exit code, not its output, decides: one that crashed printed no FAIL line.
                failed += result.returncode != 0
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
