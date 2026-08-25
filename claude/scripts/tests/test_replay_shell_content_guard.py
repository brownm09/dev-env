#!/usr/bin/env python3
"""Unit + integration tests for replay-shell-content-guard.py (ADR-138
Amendment 1 -- the reader half of dev-env#1046 item 1).

Fully hermetic: every test builds a synthetic transcript tree under a
temporary directory and points the reader at it with `--scan-dir`. Nothing
here reads the developer's real ~/.claude/projects, which is both a
correctness requirement (the real corpus changes constantly, so any assertion
against it would be flaky) and a privacy one (real transcripts carry live
command text).

The load-bearing cases:

  - ENRICHMENT is the number the whole tool exists to produce, and it is a
    RATIO of two rates over two different denominators -- easy to get subtly
    wrong and impossible to eyeball on real data. `test_enrichment_*` pin it
    against a hand-computed fixture.
  - `truncate` is the only thing standing between a transcript's secrets and
    this tool's stdout. Pinned separately.
  - `gap_heredocs` must EXCLUDE the content-argument shapes Amendment 1 moved
    into scope; if it did not, the reported gap size would silently
    double-count commands the guard already blocks.

Usage:
    py -3 claude/scripts/tests/test_replay_shell_content_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "replay-shell-content-guard.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("replay_shell_content_guard", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


replay = _load_module()
GUARD = replay.load_guard(SCRIPTS_DIR)

APOS = chr(39)

# A shell parse failure, verbatim in the shape a real tool_result carries it.
SHELL_ERR = ("Exit code 1\n/usr/bin/bash: -c: line 12: unexpected EOF while "
             "looking for matching " + APOS + APOS + APOS)


def _use(uid, cmd, tool="Bash"):
    return json.dumps({"message": {"content": [
        {"type": "tool_use", "id": uid, "name": tool, "input": {"command": cmd}}]}})


def _result(uid, text="ok", is_error=False):
    return json.dumps({"message": {"content": [
        {"type": "tool_result", "tool_use_id": uid,
         "content": [{"type": "text", "text": text}], "is_error": is_error}]}})


def _write_transcript(root, name, lines):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _Corpus:
    """Temp transcript tree, removed on exit."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp(prefix="replay-guard-test-"))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)
        return False

    def add(self, name, lines):
        return _write_transcript(self.root, name, lines)


# --------------------------------------------------------------------------
# Layer 1: extraction
# --------------------------------------------------------------------------


def test_iter_commands_pairs_use_with_result() -> str:
    with _Corpus() as corpus:
        corpus.add("proj-a/s1.jsonl", [
            _use("u1", "git status"),
            _result("u1", "clean"),
            _use("u2", "npm test", tool="PowerShell"),
            _result("u2", "passed"),
        ])
        got = list(replay.iter_commands(corpus.root))
    if len(got) != 2:
        raise AssertionError(f"expected 2 commands, got {len(got)}: {got!r}")
    by_cmd = {cmd: (tool, out) for tool, cmd, out in got}
    if by_cmd["git status"][1] != "clean":
        raise AssertionError(f"result text not paired: {by_cmd!r}")
    if by_cmd["npm test"][0] != "PowerShell":
        raise AssertionError("PowerShell tool_name must be preserved")
    return "iter_commands pairs each Bash/PowerShell tool_use with its tool_result"


def test_iter_commands_ignores_non_shell_tools() -> str:
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [
            json.dumps({"message": {"content": [
                {"type": "tool_use", "id": "u1", "name": "Write",
                 "input": {"file_path": "a.md", "content": "x"}}]}}),
            _use("u2", "ls"),
            _result("u2", "ok"),
        ])
        got = list(replay.iter_commands(corpus.root))
    if [c for _t, c, _o in got] != ["ls"]:
        raise AssertionError(f"only Bash/PowerShell calls may be yielded, got {got!r}")
    return "iter_commands ignores Write/Edit/other tool_use blocks"


def test_iter_commands_survives_malformed_lines() -> str:
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [
            "not json at all",
            '{"tool_use": "truncated',
            json.dumps({"message": {"content": "not-a-list"}}),
            json.dumps({"message": {"content": [{"type": "tool_use", "id": "u0",
                                                 "name": "Bash", "input": "not-a-dict"}]}}),
            _use("u1", "echo hi"),
            _result("u1", "hi"),
        ])
        got = list(replay.iter_commands(corpus.root))
    if [c for _t, c, _o in got] != ["echo hi"]:
        raise AssertionError(f"malformed lines must be skipped, got {got!r}")
    return "iter_commands skips malformed JSON, non-list content, and non-dict input"


def test_iter_commands_yields_unanswered_calls() -> str:
    # A session that ended mid-turn leaves a tool_use with no tool_result. The
    # command still happened, so dropping it would understate every denominator.
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [_use("u1", "cat > a.md <<" + APOS + "EOF" + APOS)])
        got = list(replay.iter_commands(corpus.root))
    if len(got) != 1 or got[0][2] != "":
        raise AssertionError(f"an unanswered tool_use must still be yielded, got {got!r}")
    return "iter_commands yields a tool_use whose result never arrived, with empty output"


# --------------------------------------------------------------------------
# Layer 2: analysis
# --------------------------------------------------------------------------


def test_analyze_counts_blocks_and_mechanisms() -> str:
    hazard = "cat > body.md <<" + APOS + "EOF" + APOS + "\nIt" + APOS + "s prose\nmore\nEOF"
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [
            _use("u1", hazard), _result("u1", "ok"),
            _use("u2", "git status"), _result("u2", "clean"),
        ])
        report = replay.analyze(GUARD, corpus.root)
    if report["stats"]["blocked"] != 1:
        raise AssertionError(f"expected 1 block, got {report['stats']}")
    if "heredoc" not in report["mechanisms"]:
        raise AssertionError(f"expected a heredoc mechanism, got {report['mechanisms']!r}")
    if report["stats"]["commands_unique"] != 2:
        raise AssertionError(f"expected 2 unique commands, got {report['stats']}")
    return "analyze counts blocked commands and buckets them by mechanism"


def test_analyze_deduplicates_repeated_commands() -> str:
    with _Corpus() as corpus:
        corpus.add("proj/s1.jsonl", [_use("u1", "git status"), _result("u1", "ok")])
        corpus.add("proj/s2.jsonl", [_use("u2", "git status"), _result("u2", "ok")])
        report = replay.analyze(GUARD, corpus.root)
    stats = report["stats"]
    if stats["commands_total"] != 2 or stats["commands_unique"] != 1:
        raise AssertionError(f"expected total=2 unique=1, got {stats!r}")
    return "analyze reports total and unique separately, deduplicating across sessions"


def test_enrichment_inputs_are_computed_over_the_right_denominators() -> str:
    # Fixture, hand-computed: 4 unique commands, 2 blocked. One blocked command
    # failed at the shell; one unblocked command also failed.
    #   baseline    = 2/4 = 50%
    #   blocked set = 1/2 = 50%   -> enrichment 1.0x (deliberately NOT >1, so a
    #                                 bug that conflates the two denominators
    #                                 cannot pass by accident)
    hz1 = "cat > a.md <<" + APOS + "EOF" + APOS + "\nIt" + APOS + "s prose\nx\nEOF"
    hz2 = "cat > b.md <<" + APOS + "EOF" + APOS + "\nmore prose\ny\nEOF"
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [
            _use("u1", hz1), _result("u1", SHELL_ERR),
            _use("u2", hz2), _result("u2", "ok"),
            _use("u3", "git rebase main"), _result("u3", SHELL_ERR),
            _use("u4", "git status"), _result("u4", "clean"),
        ])
        report = replay.analyze(GUARD, corpus.root)
    stats = report["stats"]
    for key, want in (("commands_unique", 4), ("blocked", 2),
                      ("shellfail_all", 2), ("blocked_shellfail", 1)):
        if stats.get(key) != want:
            raise AssertionError(f"{key}: expected {want}, got {stats.get(key)} ({stats!r})")
    text = replay.format_report(report)
    if "ENRICHMENT                 : 1.0x" not in text:
        raise AssertionError(f"expected a 1.0x enrichment line, got:\n{text}")
    return "enrichment uses blocked-set rate over baseline rate, each on its own denominator"


def test_enrichment_handles_a_zero_baseline() -> str:
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [_use("u1", "git status"), _result("u1", "clean")])
        report = replay.analyze(GUARD, corpus.root)
    text = replay.format_report(report)
    if "n/a (no baseline failures observed)" not in text:
        raise AssertionError(f"a zero baseline must not divide by zero, got:\n{text}")
    return "format_report reports n/a rather than dividing by a zero baseline"


def test_analyze_counts_override_tokens() -> str:
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [
            _use("u1", "ALLOW_SHELL_CONTENT_WRITE=1 cat > a.md <<" + APOS + "EOF" + APOS
                 + "\nIt" + APOS + "s prose\nx\nEOF"),
            _result("u1", "ok"),
        ])
        report = replay.analyze(GUARD, corpus.root)
    if report["overrides"].get("ALLOW_SHELL_CONTENT_WRITE") != 1:
        raise AssertionError(f"override token must be counted, got {report['overrides']!r}")
    if report["stats"].get("blocked"):
        raise AssertionError("an overridden command must not count as blocked")
    return "override-token use is counted separately and does not count as a block"


def test_gap_excludes_amendment1_content_arguments() -> str:
    # If the gap report still counted `gh pr create --body-file -`, it would
    # double-count commands the guard now blocks and overstate the open gap.
    content_arg = ("gh pr create --body-file - <<" + APOS + "EOF" + APOS
                   + "\nIt" + APOS + "s prose\nmore\nEOF")
    interpreter = "py -3 - <<" + APOS + "PY" + APOS + "\nimport os\nprint(os.getcwd())\nPY"
    if replay.gap_heredocs(GUARD, content_arg, "Bash"):
        raise AssertionError(
            "a content-argument heredoc is BLOCKED as of Amendment 1 and must not "
            "also be reported as an open gap")
    if not replay.gap_heredocs(GUARD, interpreter, "Bash"):
        raise AssertionError(
            "an interpreter-stdin heredoc is the remaining accepted gap and must be "
            "reported")
    return "gap_heredocs reports only the still-open interpreter-stdin gap"


def test_truncate_flattens_and_caps() -> str:
    long_cmd = "cat > a.md <<EOF\n" + ("x" * 500) + "\nEOF"
    got = replay.truncate(long_cmd, limit=40)
    if "\n" in got:
        raise AssertionError(f"truncate must flatten newlines, got {got!r}")
    if len(got) > 43:
        raise AssertionError(f"truncate must cap length, got {len(got)} chars")
    if not got.endswith("..."):
        raise AssertionError(f"a truncated value must be marked, got {got!r}")
    return "truncate flattens newlines and caps length (transcripts can carry secrets)"


def test_samples_are_truncated_not_verbatim() -> str:
    secret = "y" * 400
    cmd = "cat > a.md <<" + APOS + "EOF" + APOS + "\nIt" + APOS + "s " + secret + "\nEOF"
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [_use("u1", cmd), _result("u1", "ok")])
        report = replay.analyze(GUARD, corpus.root)
    rendered = json.dumps(report)
    if secret in rendered:
        raise AssertionError("a full command body must never reach the report verbatim")
    return "reported samples are truncated, so no full command body reaches output"


# --------------------------------------------------------------------------
# Layer 3: main() via subprocess
# --------------------------------------------------------------------------


def _run(args):
    return subprocess.run([sys.executable, str(MODULE_PATH)] + args,
                          capture_output=True, text=True, timeout=120)


def test_main_missing_scan_dir_exits_nonzero() -> str:
    proc = _run(["--scan-dir", str(Path(tempfile.gettempdir()) / "definitely-not-here-1046")])
    if proc.returncode == 0:
        raise AssertionError(f"a missing transcript dir must exit non-zero, got {proc.returncode}")
    if "nothing to replay" not in proc.stderr:
        raise AssertionError(f"expected an explanatory stderr line, got {proc.stderr!r}")
    return "main() exits non-zero with an explanation when the transcript dir is absent"


def test_main_json_output_is_valid() -> str:
    hazard = "cat > body.md <<" + APOS + "EOF" + APOS + "\nIt" + APOS + "s prose\nx\nEOF"
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [_use("u1", hazard), _result("u1", "ok")])
        proc = _run(["--scan-dir", str(corpus.root), "--json"])
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}\n{proc.stderr[:400]}")
        parsed = json.loads(proc.stdout)
    if parsed["stats"]["blocked"] != 1:
        raise AssertionError(f"--json must carry the same counts, got {parsed['stats']!r}")
    return "main() --json emits a parseable report carrying the same counts"


def test_main_text_report_names_the_headline_metrics() -> str:
    with _Corpus() as corpus:
        corpus.add("proj/s.jsonl", [_use("u1", "git status"), _result("u1", "ok")])
        proc = _run(["--scan-dir", str(corpus.root), "--gap"])
    if proc.returncode != 0:
        raise AssertionError(f"expected exit 0, got {proc.returncode}\n{proc.stderr[:400]}")
    for expected in ("Corpus", "Would block", "ENRICHMENT",
                     "Human disagreement", "Accepted gap still open"):
        if expected not in proc.stdout:
            raise AssertionError(
                f"report must contain {expected!r}\n  got: {proc.stdout[:600]}")
    return "main() text report names corpus, blocks, enrichment, overrides, and the gap"


def main() -> int:
    tests = [
        ("iter_commands pairs use with result", test_iter_commands_pairs_use_with_result),
        ("iter_commands ignores non-shell tools", test_iter_commands_ignores_non_shell_tools),
        ("iter_commands survives malformed lines", test_iter_commands_survives_malformed_lines),
        ("iter_commands yields unanswered calls", test_iter_commands_yields_unanswered_calls),
        ("analyze counts blocks and mechanisms", test_analyze_counts_blocks_and_mechanisms),
        ("analyze deduplicates repeated commands", test_analyze_deduplicates_repeated_commands),
        ("enrichment denominators", test_enrichment_inputs_are_computed_over_the_right_denominators),
        ("enrichment handles a zero baseline", test_enrichment_handles_a_zero_baseline),
        ("analyze counts override tokens", test_analyze_counts_override_tokens),
        ("gap excludes Amendment 1 content args", test_gap_excludes_amendment1_content_arguments),
        ("truncate flattens and caps", test_truncate_flattens_and_caps),
        ("samples are truncated, not verbatim", test_samples_are_truncated_not_verbatim),
        ("main(): missing scan dir exits non-zero", test_main_missing_scan_dir_exits_nonzero),
        ("main(): --json output is valid", test_main_json_output_is_valid),
        ("main(): text report names metrics", test_main_text_report_names_the_headline_metrics),
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
