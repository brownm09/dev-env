#!/usr/bin/env python3
"""Unit + integration tests for replay-scratch-rm-allow.py (ADR-147 section 6;
PR #1135 review finding 3).

Fully hermetic: every test builds a synthetic transcript tree under a temp
directory and points the replay at it, with `SCRATCH_RM_ALLOW_DIR_OVERRIDE`
naming a temp scratch directory. Nothing here reads the real
~/.claude/projects -- the real corpus changes constantly (flaky assertions)
and carries live command text (privacy).

The load-bearing cases:

  - The replay must call the hook's own decision function: its approvals must
    agree command-for-command with the hook's `decide()`, so the measurement
    can't drift from what the hook actually does.
  - Counting: total vs. unique (dedup), the rm+scratch candidate population,
    approvals, and approvals OUTSIDE the candidate set (the anomaly counter).
  - Rejection reasons come from the hook's own Reject messages, for
    candidates only.
  - Only Bash tool_use blocks count; PowerShell, non-tool_use blocks, and
    malformed lines are skipped without aborting the file.
  - `truncate` is the only thing between a transcript's secrets and stdout.
  - Replaying never writes the hook's approval log.

Usage:
    py -3 claude/scripts/tests/test_replay_scratch_rm_allow.py

Exit 0 = all pass.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "replay-scratch-rm-allow.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location("replay_scratch_rm_allow", MODULE_PATH)
replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay)
HOOK = replay.load_hook(SCRIPTS_DIR)
OVERRIDE_ENV = HOOK.SCRATCH_DIR_OVERRIDE_ENV


def _use(uid, cmd, tool="Bash"):
    return json.dumps({"message": {"content": [
        {"type": "tool_use", "id": uid, "name": tool, "input": {"command": cmd}}]}})


def _write(root, name, lines):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _Env:
    """A temp tree with `scratch/`, `outside/`, and `projects/`, and the scratch
    override pointed at `scratch/` (or at *scratch_name*) for the duration."""

    def __init__(self, scratch_name="scratch"):
        # No "scratch" in the root's own name: the zone case needs paths that
        # lack the word.
        self.root = Path(tempfile.mkdtemp(prefix="replay_srm_"))
        self.scratch = self.root / scratch_name
        self.scratch.mkdir()
        (self.root / "outside").mkdir()
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.s = str(self.scratch).replace("\\", "/")
        self._saved = None

    def __enter__(self):
        self._saved = os.environ.get(OVERRIDE_ENV)
        os.environ[OVERRIDE_ENV] = str(self.scratch)
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop(OVERRIDE_ENV, None)
        else:
            os.environ[OVERRIDE_ENV] = self._saved
        shutil.rmtree(self.root, ignore_errors=True)


def _standard_corpus(env):
    s = env.s
    approved_literal = f'rm -f "{s}/a.json"'
    approved_var = f'S={s}; rm -f "$S/b.json"'
    rejected_redirect = f'gh pr view 1 > "{s}/x.json" && rm -f "{s}/x.json"'
    rejected_dotdot = f'rm -f "{s}/../outside/c.json"'
    _write(env.projects, "p1/session1.jsonl", [
        _use("u1", approved_literal),
        _use("u2", rejected_redirect),
        _use("u3", "git status"),
        _use("u4", approved_literal),                 # duplicate
        _use("u5", f"rm -f {s}/a.json", tool="PowerShell"),  # not Bash: ignored
        "{not json",
        json.dumps({"message": {"content": "plain text"}}),
        json.dumps({"message": {"content": [{"type": "text", "text": '"tool_use"'}]}}),
    ])
    _write(env.projects, "p2/nested/session2.jsonl", [
        _use("v1", approved_var),
        _use("v2", rejected_dotdot),
    ])
    return {
        "approved": [approved_literal, approved_var],
        "rejected": [rejected_redirect, rejected_dotdot],
    }


def test_iter_bash_commands_only_bash_and_tolerates_malformed():
    with _Env() as env:
        _standard_corpus(env)
        stats = replay.Counter()
        got = list(replay.iter_bash_commands(env.projects, stats))
    assert len(got) == 6, got
    assert all("PowerShell" not in c for c in got)
    assert stats["transcripts"] == 2, stats
    return "6 Bash commands from 2 transcripts; PowerShell, text blocks, and malformed lines skipped"


def test_analyze_counts():
    with _Env() as env:
        _standard_corpus(env)
        report = replay.analyze(HOOK, env.projects)
    st = report["stats"]
    assert st["commands_total"] == 6, st
    assert st["commands_unique"] == 5, st
    assert st["candidates"] == 4, st
    assert st["approved"] == 2, st
    assert st.get("approved_outside_candidates", 0) == 0, st
    assert st.get("replay_errors", 0) == 0, st
    return "total 6, unique 5, candidates 4, approved 2, none outside candidates"


def test_approvals_agree_with_hook_decide():
    with _Env() as env:
        corpus = _standard_corpus(env)
        report = replay.analyze(HOOK, env.projects, width=10_000)
        listed = sorted(item["command"] for item in report["approved"])
        for cmd in corpus["approved"]:
            assert HOOK.decide({"tool_name": "Bash", "tool_input": {"command": cmd}}), cmd
        for cmd in corpus["rejected"]:
            assert HOOK.decide({"tool_name": "Bash", "tool_input": {"command": cmd}}) is None, cmd
        a_json = os.path.normcase(os.path.join(os.path.realpath(env.scratch), "a.json"))
        targets = [os.path.normcase(t) for item in report["approved"] for t in item["targets"]]
    assert listed == sorted(corpus["approved"]), listed
    assert a_json in targets, targets
    return "replay approvals == the hook's decide() approvals, with resolved targets"


def test_reject_reasons_are_the_hooks_own_for_candidates_only():
    with _Env() as env:
        _standard_corpus(env)
        report = replay.analyze(HOOK, env.projects)
    reasons = report["reject_reasons"]
    assert reasons == {"unsupported character '>'": 1, ".. component": 1}, reasons
    # `git status` is rejected by the hook too, but it's not a candidate.
    assert sum(reasons.values()) == report["stats"]["candidates"] - report["stats"]["approved"]
    return f"reasons {reasons}; non-candidate rejections not counted"


def test_approval_outside_candidates_is_flagged():
    # A scratch directory whose path lacks the word "scratch": its approvals
    # are real but fall outside the candidate filter, and must be counted.
    with _Env(scratch_name="zone") as env:
        _write(env.projects, "p/s.jsonl", [_use("z1", f'rm -f "{env.s}/a.json"')])
        report = replay.analyze(HOOK, env.projects)
        text = replay.format_report(report)
    st = report["stats"]
    assert st["approved"] == 1 and st["approved_outside_candidates"] == 1, st
    assert st.get("candidates", 0) == 0, st
    assert "[not a candidate]" in text, text
    return "an approval outside the rm+scratch filter is counted and marked"


def test_is_candidate():
    assert replay.is_candidate("rm -f C:/Users/x/.claude/scratch/a")
    assert replay.is_candidate("S=~/.claude/SCRATCH; cd $S && rm a")
    assert not replay.is_candidate("git rm C:/x/notes.md")  # "scratch" absent
    assert not replay.is_candidate("echo scratch; ls")     # no rm word
    assert not replay.is_candidate("npm run scratch-format")
    return "rm word + 'scratch' (case-insensitive) selects candidates"


def test_truncate_flattens_and_caps():
    assert replay.truncate("a\n  b\tc") == "a b c"
    out = replay.truncate("x" * 500, 50)
    assert out == "x" * 50 + "...", out
    with _Env() as env:
        long_cmd = f'rm -f "{env.s}/' + "y" * 400 + '.json"'
        _write(env.projects, "p/s.jsonl", [_use("t1", long_cmd)])
        report = replay.analyze(HOOK, env.projects, width=80)
    listed = report["approved"][0]["command"]
    assert len(listed) == 83 and listed.endswith("..."), listed
    return "commands are flattened and capped at --width in every report"


def test_replay_never_writes_the_approval_log():
    with _Env() as env:
        _standard_corpus(env)
        replay.analyze(HOOK, env.projects)
        assert not (env.scratch / HOOK.APPROVAL_LOG_NAME).exists()
    return "check_command replay leaves scratch-rm-allow.log untouched"


def _run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = replay.main(argv)
    return rc, out.getvalue(), err.getvalue()


def test_main_missing_scan_dir_exits_nonzero():
    rc, out, err = _run_main(["--scan-dir", str(Path(tempfile.gettempdir()) / "no_such_dir_replay_scratch")])
    assert rc == 1 and out == "" and "nothing to replay" in err, (rc, out, err)
    return "missing --scan-dir -> exit 1 with a stderr note"


def test_main_json_and_text_reports():
    with _Env() as env:
        _standard_corpus(env)
        rc, out, _ = _run_main(["--scan-dir", str(env.projects), "--json", "--scripts-dir", str(SCRIPTS_DIR)])
        assert rc == 0, rc
        data = json.loads(out)
        assert data["stats"]["approved"] == 2 and len(data["approved"]) == 2, data
        rc, text, _ = _run_main(["--scan-dir", str(env.projects)])
    assert rc == 0
    for needle in ("rm+scratch candidates      : 4", "approved                   : 2",
                   "unsupported character '>'", "spot-check every one", "targets: "):
        assert needle in text, (needle, text)
    return "--json parses with the same counts; the text report names candidates, approvals, reasons, targets"


def main() -> int:
    tests = [
        ("iter_bash_commands: Bash only, malformed-tolerant", test_iter_bash_commands_only_bash_and_tolerates_malformed),
        ("analyze: counts", test_analyze_counts),
        ("approvals agree with the hook's decide()", test_approvals_agree_with_hook_decide),
        ("reject reasons: hook's own, candidates only", test_reject_reasons_are_the_hooks_own_for_candidates_only),
        ("approval outside candidates is flagged", test_approval_outside_candidates_is_flagged),
        ("is_candidate", test_is_candidate),
        ("truncate flattens and caps", test_truncate_flattens_and_caps),
        ("replay never writes the approval log", test_replay_never_writes_the_approval_log),
        ("main(): missing scan dir exits non-zero", test_main_missing_scan_dir_exits_nonzero),
        ("main(): --json and text reports", test_main_json_and_text_reports),
    ]
    failed = 0
    for name, fn in tests:
        try:
            detail = fn()
            print(f"PASS: {name}")
            print(f"      {detail}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {name}")
            for line in str(e).splitlines():
                print(f"      {line}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
    print()
    print(f"Tests: {len(tests) - failed} passed, 0 skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
