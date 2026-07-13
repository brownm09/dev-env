#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-journal-draft-worktree-guard.py.

Two layers, both hermetic (no real git repos beyond throwaway `git init`s):

  1. Pure-function tests of `find_worktree_add_blocks()` / `find_checkout_candidates()` /
     `DRAFT_BRANCH_RE` / `_worktree_add_target()` / `_has_override()` — no git subprocess.
     Covers two asymmetric `cd`-scope decisions: `find_worktree_add_blocks` needs no cwd at
     all (blocks purely on the branch-name token) so a leading `cd` provides NO exemption
     there, while `find_checkout_candidates` genuinely can't resolve a target after a `cd`
     and correctly stays out of scope. Also covers `--detach` (never a candidate — a
     detached HEAD holds no branch ref) and a trailing `--` with no paths after it (still a
     branch switch, not a file restore).
  2. End-to-end main() via subprocess — drives the real hook over stdin against real
     throwaway git repos (a "canonical" and a "worktree") and asserts exit codes for:
       - `git worktree add <path> ... draft/YYYY-MM-DD` from any cwd is BLOCKED (exit 2),
         including when preceded by an unrelated `cd`;
       - `git checkout draft/YYYY-MM-DD` redirected at the (env-overridden) journal
         canonical via `-C`, with the path rendered in FORWARD slashes (matching the
         documented production convention — see the dedicated comment on this test: a
         backslash-rendered path silently defeats `shlex.split(posix=True)`, which would
         make this test pass for the wrong reason and give zero regression protection for
         the hook's single most important invariant), is ALLOWED (exit 0) — the one
         legitimate case;
       - the identical checkout with no redirect, ambient cwd NOT the canonical, is
         BLOCKED (exit 2);
       - the override token bypasses the block (exit 0);
       - `git checkout main` (not a draft branch) is allowed;
       - `git checkout draft/2026-07-12 -- somefile.txt` (file restore, not a branch
         switch) is allowed;
       - malformed JSON / missing cwd / non-Bash tool_name fail open.

Usage:
    py -3 claude/scripts/tests/test_journal_draft_worktree_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "pre-tool-use-journal-draft-worktree-guard.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("journal_draft_worktree_guard", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jdwg = _load_module()

# --------------------------------------------------------------------------
# Layer 1: pure-function tests
# --------------------------------------------------------------------------


def test_draft_branch_re() -> str:
    matches = ["draft/2026-07-12", "draft/2026-01-01-recovery"]
    non_matches = ["draft/2026-7-12", "draft/2026-07-12-late", "main", "feature/draft/2026-07-12-preview", ""]
    for b in matches:
        if not jdwg.DRAFT_BRANCH_RE.match(b):
            raise AssertionError(f"{b!r} should match DRAFT_BRANCH_RE")
    for b in non_matches:
        if jdwg.DRAFT_BRANCH_RE.match(b):
            raise AssertionError(f"{b!r} should NOT match DRAFT_BRANCH_RE")
    return "draft/YYYY-MM-DD and -recovery suffix match; malformed dates/other suffixes/other branches don't"


def test_find_worktree_add_blocks_basic() -> str:
    got = jdwg.find_worktree_add_blocks("git worktree add .claude/worktrees/foo draft/2026-07-12")
    if got != ["git worktree add .claude/worktrees/foo draft/2026-07-12"]:
        raise AssertionError(f"expected the bare worktree-add segment blocked, got {got}")
    return "git worktree add <path> draft/YYYY-MM-DD -> blocked, no git subprocess needed"


def test_find_worktree_add_blocks_dash_b_startpoint_allowed() -> str:
    # -b names the NEW branch actually checked out; a draft/* commit-ish elsewhere is just
    # a startpoint and must NOT be flagged.
    got = jdwg.find_worktree_add_blocks("git worktree add -b myfix .claude/worktrees/foo draft/2026-07-12")
    if got:
        raise AssertionError(f"-b <newbranch> with draft/* as mere startpoint must not block, got {got}")
    return "-b <new-branch> ... draft/YYYY-MM-DD (draft as startpoint only) -> not blocked"


def test_find_worktree_add_blocks_dash_b_draft_name_blocked() -> str:
    # -b explicitly naming a draft-shaped branch IS the harmful shape.
    got = jdwg.find_worktree_add_blocks("git worktree add -b draft/2026-07-12 .claude/worktrees/foo origin/main")
    if not got:
        raise AssertionError("-b draft/YYYY-MM-DD must block (that new branch IS draft/YYYY-MM-DD)")
    return "-b draft/YYYY-MM-DD ... -> blocked (the new branch itself is the shared name)"


def test_find_worktree_add_blocks_no_draft_branch_allowed() -> str:
    got = jdwg.find_worktree_add_blocks("git worktree add .claude/worktrees/foo origin/main")
    if got:
        raise AssertionError(f"ordinary worktree add (no draft branch) must not block, got {got}")
    return "git worktree add <path> origin/main -> not blocked"


def test_find_worktree_add_blocks_not_exempted_by_leading_cd() -> str:
    # Review finding: worktree-add needs no cwd resolution at all (it blocks purely on the
    # branch-name token), so a preceding `cd` must NOT provide cover the way it does for
    # find_checkout_candidates (which genuinely can't resolve a target after a cd). Before
    # the fix, this exact shape silently bypassed the block, reproducing the incident.
    got = jdwg.find_worktree_add_blocks("cd /somewhere && git worktree add .claude/worktrees/foo draft/2026-07-12")
    if not got:
        raise AssertionError("a leading cd must NOT exempt worktree-add -- it needs no cwd at all")
    return "cd <path> && git worktree add ... draft/YYYY-MM-DD -> still blocked (cd provides no cover here)"


def test_find_checkout_candidates_cd_out_of_scope() -> str:
    # Unlike worktree-add, a checkout/switch candidate's blockability genuinely depends on
    # resolving a real cwd -- a preceding cd makes that cwd unknowable, so this extractor
    # (unlike find_worktree_add_blocks above) correctly stays out of scope.
    got = jdwg.find_checkout_candidates("cd /somewhere && git checkout draft/2026-07-12")
    if got:
        raise AssertionError(f"a cd anywhere must take checkout candidates out of scope, got {got}")
    return "cd <path> && git checkout draft/YYYY-MM-DD -> out of scope (real cwd unknowable after cd)"


def test_find_worktree_add_blocks_heredoc_mention_not_triggered() -> str:
    cmd = 'git commit -m "$(cat <<\'EOF\'\ngit worktree add foo draft/2026-07-12\nEOF\n)"'
    got = jdwg.find_worktree_add_blocks(cmd)
    if got:
        raise AssertionError(f"a heredoc body merely mentioning the pattern must not trigger, got {got}")
    return "worktree-add text inside a commit-message heredoc body -> not mistaken for a real invocation"


def test_find_checkout_candidates_basic() -> str:
    got = jdwg.find_checkout_candidates("git checkout draft/2026-07-12")
    if len(got) != 1 or got[0]["segment"] != "git checkout draft/2026-07-12" or got[0]["redirect_dirs"] != []:
        raise AssertionError(f"expected one ambient candidate, got {got}")
    return "ambient `git checkout draft/YYYY-MM-DD` -> one candidate, no redirect_dirs"


def test_find_checkout_candidates_switch() -> str:
    got = jdwg.find_checkout_candidates("git switch draft/2026-07-12")
    if len(got) != 1:
        raise AssertionError(f"git switch <draft-branch> must be a candidate too, got {got}")
    return "git switch draft/YYYY-MM-DD -> also a candidate (not just checkout)"


def test_find_checkout_candidates_redirect_captured() -> str:
    got = jdwg.find_checkout_candidates("git -C C:/Users/brown/Git/engineering-journal checkout draft/2026-07-12")
    if len(got) != 1 or got[0]["redirect_dirs"] != ["C:/Users/brown/Git/engineering-journal"]:
        raise AssertionError(f"expected the -C target captured as redirect_dirs, got {got}")
    return "-C <dir> checkout draft/YYYY-MM-DD -> candidate with redirect_dirs captured (resolution deferred to main())"


def test_find_checkout_candidates_not_draft_branch_allowed() -> str:
    got = jdwg.find_checkout_candidates("git checkout main")
    if got:
        raise AssertionError(f"checkout of a non-draft branch must not be a candidate, got {got}")
    return "git checkout main -> not a candidate"


def test_find_checkout_candidates_file_restore_allowed() -> str:
    got = jdwg.find_checkout_candidates("git checkout draft/2026-07-12 -- somefile.txt")
    if got:
        raise AssertionError(f"a file restore (checkout <tree-ish> -- <paths>) must not be a candidate, got {got}")
    return "git checkout draft/YYYY-MM-DD -- <path> (file restore) -> not a candidate"


def test_find_checkout_candidates_trailing_dash_dash_still_switches() -> str:
    # Review finding: `checkout <branch> --` with NOTHING after the -- still switches
    # branches (verified against real git behavior) -- only a `--` followed by an actual
    # pathspec is a file restore. Before the fix, a bare trailing `--` was wrongly exempted.
    got = jdwg.find_checkout_candidates("git checkout draft/2026-07-12 --")
    if len(got) != 1:
        raise AssertionError(f"a trailing -- with no paths after it must still be a candidate, got {got}")
    return "git checkout draft/YYYY-MM-DD -- (trailing, no paths) -> still a candidate, not a file restore"


def test_find_checkout_candidates_dash_b_create() -> str:
    got = jdwg.find_checkout_candidates("git checkout -b draft/2026-07-12")
    if len(got) != 1:
        raise AssertionError(f"checkout -b draft/YYYY-MM-DD (create+switch) must be a candidate, got {got}")
    return "git checkout -b draft/YYYY-MM-DD -> a candidate (this is the legitimate first-session-of-the-day step, gated on target resolution in main(), not excluded here)"


def test_worktree_add_target_dash_b_vs_positional() -> str:
    if jdwg._worktree_add_target(["-b", "myfix", "path", "draft/2026-07-12"]) != ["myfix"]:
        raise AssertionError("-b present -> only its value is a candidate")
    if jdwg._worktree_add_target(["path", "draft/2026-07-12"]) != ["path", "draft/2026-07-12"]:
        raise AssertionError("no -b -> every non-flag token is a candidate")
    if jdwg._worktree_add_target(["-b"]) != []:
        raise AssertionError("dangling -b with no value -> no candidates, not an index error")
    return "-b <val> isolates the real target; no -b scans all positionals; dangling -b doesn't crash"


def test_worktree_add_target_detach_never_a_candidate() -> str:
    # Review finding: --detach checks out a DETACHED HEAD, which holds no branch ref at all
    # -- it can never be a squat regardless of what the other tokens look like. Before the
    # fix, `--detach <draft-branch>` was a false-positive over-block.
    if jdwg._worktree_add_target(["--detach", "path", "draft/2026-07-12"]) != []:
        raise AssertionError("--detach must yield zero candidates -- nothing gets checked out as a branch")
    return "--detach ... draft/YYYY-MM-DD -> no candidates (detached HEAD holds no branch ref)"


def test_has_override() -> str:
    if not jdwg._has_override("ALLOW_JOURNAL_DRAFT_WORKTREE=1 git checkout draft/2026-07-12"):
        raise AssertionError("leading override token must be recognized")
    if jdwg._has_override('git commit -m "ALLOW_JOURNAL_DRAFT_WORKTREE=1 was mentioned"'):
        raise AssertionError("override token inside a quoted string must NOT bypass")
    return "leading override token recognized; quoted mention does not bypass"


# --------------------------------------------------------------------------
# Layer 2: end-to-end subprocess tests
# --------------------------------------------------------------------------


def _run_hook(payload: dict, env_overrides: dict = None) -> subprocess.CompletedProcess:
    import os
    env = None
    if env_overrides:
        env = dict(os.environ)
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _init_throwaway_repo(root: Path) -> None:
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=", "init", "-q", str(root)],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True, capture_output=True)


def test_main_blocks_worktree_add_onto_draft_branch() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        other_repo = Path(tmp) / "other-repo"
        other_repo.mkdir()
        _init_throwaway_repo(other_repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .claude/worktrees/foo draft/2026-07-12"},
            "cwd": str(other_repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
        if proc.stdout.strip():
            raise AssertionError(f"reason must go to stderr, not stdout: {proc.stdout!r}")
        reason = json.loads(proc.stderr).get("reason", "")
        if "journal-draft-worktree-guard" not in reason or "ALLOW_JOURNAL_DRAFT_WORKTREE=1" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "git worktree add <path> draft/YYYY-MM-DD blocked (exit 2), reason on stderr, from ANY repo"


def test_main_allows_redirect_at_journal_canonical() -> str:
    # The -C path is deliberately rendered with forward slashes, matching the documented
    # production convention (`git -C C:/Users/brown/Git/engineering-journal ...`) -- NOT
    # str(Path(...)), which renders Windows backslashes. _tokenize() uses shlex.split(
    # posix=True), which treats backslash as an escape character and silently EATS every
    # backslash in a raw Windows path (confirmed: shlex.split(r'C:\Users\brown\...')
    # collapses to a single mangled token with the separators stripped). A prior version of
    # this test used str(journal) directly, which meant the -C target never resolved to the
    # real journal directory at all -- the test passed only because BOTH a broken resolution
    # AND a correct one exit 0 for different reasons, so it gave zero regression protection
    # for the hook's single most important invariant (distinguishing the one legitimate case
    # from every squat). Verified by sabotaging _is_journal_canonical to always return False
    # (which would break the documented daily workflow for every session) -- with the
    # backslash path, the full suite still passed; with the forward-slash fix below, this
    # test correctly fails.
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "engineering-journal"
        journal.mkdir()
        _init_throwaway_repo(journal)
        elsewhere = Path(tmp) / "elsewhere"
        elsewhere.mkdir()
        _init_throwaway_repo(elsewhere)
        journal_fwd = str(journal).replace("\\", "/")
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {journal_fwd} checkout draft/2026-07-12"},
            "cwd": str(elsewhere),
        }
        proc = _run_hook(payload, env_overrides={"JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH": str(journal)})
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0 (the one legitimate case), got {proc.returncode}. stderr={proc.stderr!r}")
    return "-C <journal-canonical> checkout draft/YYYY-MM-DD (forward-slash path) allowed (exit 0) -- the documented workflow itself"


def test_main_blocks_ambient_checkout_outside_canonical() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "engineering-journal"
        journal.mkdir()
        _init_throwaway_repo(journal)
        worktree = Path(tmp) / "some-worktree"
        worktree.mkdir()
        _init_throwaway_repo(worktree)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout draft/2026-07-12"},
            "cwd": str(worktree),
        }
        proc = _run_hook(payload, env_overrides={"JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH": str(journal)})
        if proc.returncode != 2:
            raise AssertionError(f"expected exit 2, got {proc.returncode}. stderr={proc.stderr!r}")
        reason = json.loads(proc.stderr).get("reason", "")
        # git rev-parse --show-toplevel normalizes to forward slashes; str(Path(...)) on
        # Windows does not -- compare basenames, not raw path strings, to avoid a
        # separator-only false failure.
        if worktree.name not in reason:
            raise AssertionError(f"reason should name the resolved (wrong) target: {reason!r}")
    return "ambient checkout draft/YYYY-MM-DD outside the canonical blocked (exit 2), names the wrong target"


def test_main_override_bypasses_block() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "engineering-journal"
        journal.mkdir()
        _init_throwaway_repo(journal)
        worktree = Path(tmp) / "some-worktree"
        worktree.mkdir()
        _init_throwaway_repo(worktree)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ALLOW_JOURNAL_DRAFT_WORKTREE=1 git checkout draft/2026-07-12"},
            "cwd": str(worktree),
        }
        proc = _run_hook(payload, env_overrides={"JOURNAL_DRAFT_WORKTREE_GUARD_REPO_PATH": str(journal)})
        if proc.returncode != 0:
            raise AssertionError(f"override must bypass the block, got exit {proc.returncode}. stderr={proc.stderr!r}")
    return "ALLOW_JOURNAL_DRAFT_WORKTREE=1 prefix bypasses the block (exit 0)"

def test_main_blocks_worktree_add_despite_leading_cd() -> str:
    # End-to-end regression proof for the cd-bypass fix (see
    # test_find_worktree_add_blocks_not_exempted_by_leading_cd for the pure-function version):
    # before the fix, `cd <repo> && git worktree add ... draft/YYYY-MM-DD` slipped through
    # main() unblocked (exit 0), reproducing the exact incident this hook exists to prevent.
    with tempfile.TemporaryDirectory() as tmp:
        other_repo = Path(tmp) / "other-repo"
        other_repo.mkdir()
        _init_throwaway_repo(other_repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"cd {other_repo} && git worktree add .claude/worktrees/foo draft/2026-07-12"},
            "cwd": str(other_repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(f"expected exit 2 despite the leading cd, got {proc.returncode}. stderr={proc.stderr!r}")
    return "cd <repo> && git worktree add ... draft/YYYY-MM-DD -- still blocked (exit 2) despite the leading cd"


def test_main_allows_non_draft_checkout() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout main"},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0, got {proc.returncode}. stderr={proc.stderr!r}")
    return "git checkout main (not a draft branch) allowed regardless of cwd"


def test_main_fails_open_on_malformed_input() -> str:
    cases = [
        ("", "empty stdin"),
        ("not json", "malformed JSON"),
        (json.dumps({"tool_name": "Bash", "tool_input": {"command": "git checkout draft/2026-07-12"}}), "missing cwd"),
        (json.dumps({"tool_name": "Write", "cwd": "/x", "tool_input": {"command": "git checkout draft/2026-07-12"}}), "non-Bash tool_name"),
        (json.dumps([]), "valid JSON but not an object"),
    ]
    for raw, desc in cases:
        proc = subprocess.run([sys.executable, str(MODULE_PATH)], input=raw, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise AssertionError(f"{desc} must fail open (exit 0), got {proc.returncode}")
    return "empty/malformed JSON, missing cwd, non-Bash tool_name, non-object JSON all fail open (exit 0)"


def main() -> int:
    tests = [
        ("DRAFT_BRANCH_RE matrix", test_draft_branch_re),
        ("find_worktree_add_blocks: basic block", test_find_worktree_add_blocks_basic),
        ("find_worktree_add_blocks: -b startpoint allowed", test_find_worktree_add_blocks_dash_b_startpoint_allowed),
        ("find_worktree_add_blocks: -b draft-name blocked", test_find_worktree_add_blocks_dash_b_draft_name_blocked),
        ("find_worktree_add_blocks: no draft branch allowed", test_find_worktree_add_blocks_no_draft_branch_allowed),
        ("find_worktree_add_blocks: NOT exempted by a leading cd", test_find_worktree_add_blocks_not_exempted_by_leading_cd),
        ("find_worktree_add_blocks: heredoc mention not triggered", test_find_worktree_add_blocks_heredoc_mention_not_triggered),
        ("find_checkout_candidates: basic", test_find_checkout_candidates_basic),
        ("find_checkout_candidates: switch", test_find_checkout_candidates_switch),
        ("find_checkout_candidates: redirect captured", test_find_checkout_candidates_redirect_captured),
        ("find_checkout_candidates: non-draft allowed", test_find_checkout_candidates_not_draft_branch_allowed),
        ("find_checkout_candidates: file restore allowed", test_find_checkout_candidates_file_restore_allowed),
        ("find_checkout_candidates: trailing -- still switches", test_find_checkout_candidates_trailing_dash_dash_still_switches),
        ("find_checkout_candidates: -b create", test_find_checkout_candidates_dash_b_create),
        ("find_checkout_candidates: cd out of scope", test_find_checkout_candidates_cd_out_of_scope),
        ("_worktree_add_target: -b vs positional scan", test_worktree_add_target_dash_b_vs_positional),
        ("_worktree_add_target: --detach never a candidate", test_worktree_add_target_detach_never_a_candidate),
        ("_has_override: leading vs quoted mention", test_has_override),
        ("main(): blocks worktree add onto draft branch", test_main_blocks_worktree_add_onto_draft_branch),
        ("main(): allows -C redirect at journal canonical", test_main_allows_redirect_at_journal_canonical),
        ("main(): blocks ambient checkout outside canonical", test_main_blocks_ambient_checkout_outside_canonical),
        ("main(): override bypasses block", test_main_override_bypasses_block),
        ("main(): blocks worktree add despite leading cd", test_main_blocks_worktree_add_despite_leading_cd),
        ("main(): allows non-draft checkout", test_main_allows_non_draft_checkout),
        ("main(): fails open on malformed input", test_main_fails_open_on_malformed_input),
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
