#!/usr/bin/env python3
"""End-to-end tests for reconcile-late-stubs.py (dev-env#1203).

Runs the script's real main() against a throwaway bare origin and clone. Only `gh` is
faked: the fake answers `gh pr list --head <head>` from a per-test table, and every git
command runs for real, so what is asserted is the tree actually pushed to origin.

The property that matters is the one `/journal-compose` depends on: it finds stubs with
`sessions/*/<DATE>_*.stub.md`, so a moved stub must carry the target branch's date
(`compose_stubs` applies that same glob). The second is the lookup: a day whose draft
conflicted merges from compose/<D>, so the merge has to be found there too.

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


TEMPLATE = tempfile.TemporaryDirectory()


class Journal:
    """A bare origin plus a clone, on `main` with one composed day. The pair is built
    once and copied for each test, since building it runs many real git commands."""

    def __init__(self, root: Path):
        template = Path(TEMPLATE.name)
        self.prs: dict[str, list[dict]] = {}
        self.gh_error = False
        self.delete_fails = False
        if not (template / "clone").exists():
            self.origin = template / "origin.git"
            self.clone = template / "clone"
            no_hooks = template / "no-hooks"
            no_hooks.mkdir()
            REAL_RUN(["git", "init", "--bare", "-b", "main", str(self.origin)], capture_output=True, check=True)
            REAL_RUN(["git", "clone", str(self.origin), str(self.clone)], capture_output=True, check=True)
            self.git("config", "user.name", "test")
            self.git("config", "user.email", "test@example.com")
            self.git("config", "core.autocrlf", "false")
            # The machine's global pre-push hook must not run against the fixture.
            self.git("config", "core.hooksPath", str(no_hooks))
            self.git("checkout", "-b", "main")
            self.commit({"sessions/p/2026-11-02-composed.md": "composed\n"}, BEFORE_MERGE)
            self.git("push", "origin", "main")
        self.origin = root / "origin.git"
        self.clone = root / "clone"
        shutil.copytree(template / "origin.git", self.origin)
        shutil.copytree(template / "clone", self.clone)
        self.git("remote", "set-url", "origin", str(self.origin))

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
        self.git("fetch", "origin")
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
        self.git("fetch", "origin", "--prune")
        out = self.git("ls-tree", "-r", "--name-only", f"origin/{branch}", "sessions/")
        return out.splitlines()

    def show(self, branch: str, rel_path: str) -> str:
        return self.git("show", f"origin/{branch}:{rel_path}")

    def compose_stubs(self, branch: str) -> list[str]:
        """The stubs /journal-compose would find: sessions/*/<DATE>_*.stub.md."""
        date = branch.removeprefix("draft/")
        return [
            p for p in self.tree(branch)
            if p.count("/") == 2 and p.split("/")[-1].startswith(f"{date}_") and p.endswith(".stub.md")
        ]

    def remote_has(self, branch: str) -> bool:
        return bool(self.git("ls-remote", "--heads", "origin", branch).strip())


def merged(number: int = 310, at: str = MERGED_AT) -> dict:
    return {"number": number, "mergedAt": at, "url": f"https://example.test/pull/{number}"}


def with_journal(test):
    def run_test():
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp))
            try:
                test(journal)
            finally:
                # Release any worktree registration before the tempdir is removed.
                REAL_RUN(["git", "worktree", "prune"], cwd=journal.clone, capture_output=True)
    run_test.__name__ = test.__name__
    return run_test


@with_journal
def test_moved_stub_takes_target_date_and_compose_finds_it(j):
    j.source_with_late_stub()
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.compose_stubs("draft/2026-11-04")
    tree = j.tree("draft/2026-11-04")
    assert "sessions/p/2026-11-03_120000.stub.md" not in tree
    assert "sessions/p/2026-11-03_120000.manifest.jsonl" not in tree
    assert j.show("draft/2026-11-04", "sessions/p/2026-11-04_120000.stub.md").replace("\r\n", "\n") == STUB
    assert not j.remote_has("draft/2026-11-03"), "source branch should be deleted"


@with_journal
def test_manifest_moves_with_stub_and_names_its_new_path(j):
    j.source_with_late_stub()
    j.target()
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


@with_journal
def test_day_merged_from_compose_branch_is_found(j):
    j.source_with_late_stub()
    j.target()
    j.prs["compose/2026-11-03"] = [merged()]  # no PR at draft/2026-11-03
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")


@with_journal
def test_latest_merge_decides_what_is_late_when_both_heads_merged(j):
    """Real shape: 2026-07-08 merged from draft/ and, a day later, from compose/."""
    j.source_with_late_stub()
    j.target()
    j.prs["draft/2026-11-03"] = [merged(300, at="2026-11-03T22:00:00Z")]
    j.prs["compose/2026-11-03"] = [merged(310, at="2026-11-04T23:00:00Z")]
    assert j.reconcile() == 0
    # The stub was committed after the first merge but before the last, so it was composed.
    assert "sessions/p/2026-11-04_120000.stub.md" not in j.tree("draft/2026-11-04")


@with_journal
def test_no_merged_pr_at_either_head_aborts_and_keeps_branch(j):
    j.source_with_late_stub()
    j.target()
    assert j.reconcile() == 1
    assert j.remote_has("draft/2026-11-03")


@with_journal
def test_failed_lookup_aborts_and_keeps_branch(j):
    j.source_with_late_stub()
    j.target()
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


@with_journal
def test_rerun_after_failed_delete_does_not_duplicate_the_session(j):
    j.source_with_late_stub()
    j.target()
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


@with_journal
def test_stub_renamed_in_a_late_commit_still_moves(j):
    j.source_with_late_stub()
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "draft/2026-11-03")
    j.git("mv", "sessions/p/2026-11-03_120000.stub.md", "sessions/p/2026-11-03_120500.stub.md")
    j.git("commit", "-m", "rename", env={"GIT_COMMITTER_DATE": AFTER_MERGE, "GIT_AUTHOR_DATE": AFTER_MERGE})
    j.push("draft/2026-11-03")
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120500.stub.md" in j.tree("draft/2026-11-04")


@with_journal
def test_legacy_per_day_manifest_aborts_before_anything_moves(j):
    j.source_with_late_stub()
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "draft/2026-11-03")
    j.commit({"sessions/p/2026-11-03.manifest.jsonl": manifest("2026-11-03_120000.stub.md")}, AFTER_MERGE)
    j.push("draft/2026-11-03")
    before = j.tree("draft/2026-11-04")
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "2026-11-03.manifest.jsonl" in str(e)
    else:
        raise AssertionError("a legacy manifest must not be moved under a name compose ignores")
    assert j.tree("draft/2026-11-04") == before
    assert j.remote_has("draft/2026-11-03")


@with_journal
def test_report_dated_for_uncomposed_day_does_not_mark_it_composed(j):
    """A report dated 2026-11-04 on main must not make draft/2026-11-04 look composed."""
    j.source_with_late_stub()
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    j.git("checkout", "main")
    j.commit({"sessions/p/reports/2026-11-04-analysis.md": "report\n"}, MERGED_AT)
    j.git("push", "origin", "main")
    assert j.reconcile() == 0
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")
    assert not j.remote_has(f"draft/{TODAY}"), "should not have fallen through to today's branch"


@with_journal
def test_composed_target_day_is_skipped_for_todays_branch_with_todays_date(j):
    j.source_with_late_stub()
    j.target()
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
    j.commit({"sessions/p/2026-11-03.manifest.jsonl": manifest("2026-11-03_120000.stub.md")}, AFTER_MERGE)
    j.push("draft/2026-11-03")
    j.prs["draft/2026-11-03"] = [merged()]
    try:
        j.reconcile()
    except RuntimeError as e:
        assert "2026-11-03.manifest.jsonl" in str(e)
    else:
        raise AssertionError("a legacy manifest must stop the run")
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


@with_journal
def test_failed_delete_says_not_to_rerun(j):
    j.source_with_late_stub()
    j.target()
    j.prs["draft/2026-11-03"] = [merged()]
    j.delete_fails = True
    stderr = io.StringIO()
    with mock.patch.object(sys, "stderr", stderr):
        assert j.reconcile() == 1
    assert "Do not re-run" in stderr.getvalue(), stderr.getvalue()
    assert "sessions/p/2026-11-04_120000.stub.md" in j.tree("draft/2026-11-04")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {test.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc(limit=3, file=sys.stdout)
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
