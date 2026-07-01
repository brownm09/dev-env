#!/usr/bin/env python3
"""Unit + integration tests for pre-tool-use-canonical-mutate-guard.py.

Two layers, both hermetic (no real git repos beyond a throwaway `git init`,
no worktrees needed):

  1. Pure-function tests of `classify()` / `is_mutating_segment()` — the full
     mutating-verb matrix (checkout/-b, switch, commit, merge, rebase, reset,
     cherry-pick, revert, stash pop/apply, branch -d/-D, bare pull) versus the
     explicitly-allowed read-only surface (status, log, diff, show, fetch,
     branch --show-current, rev-parse, ls-tree, blame, remote -v, plain
     branch, stash list/show, checkout -- <path>, pull --ff-only), plus the
     segment-split/anchor and redirect-skip behavior (career-playbook #442
     heredoc-mention lesson; `cd`/`-C`/`--git-dir` redirects are out of scope).
  2. End-to-end main() via subprocess — drives the real hook over stdin against
     a real throwaway git repo (so `git -C <cwd> rev-parse --show-toplevel`
     resolves for real) and asserts exit codes for:
       - a mutating command from a canonical (non-worktree) git repo is
         BLOCKED (exit 2) with the expected reason text;
       - the same command from a worktree-pattern cwd is allowed (exit 0);
       - the override token bypasses the block (exit 0);
       - a non-git cwd fails open (exit 0);
       - malformed JSON / missing cwd / non-Bash tool_name fail open (exit 0);
       - a read-only command (git status) from a canonical root is allowed;
       - `git pull --ff-only` is allowed, bare `git pull` is blocked;
       - a mutating verb mentioned only inside a heredoc body does not trigger.

Usage:
    py -3 claude/scripts/tests/test_canonical_mutate_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS_DIR / "pre-tool-use-canonical-mutate-guard.py"

# The module's first line is `import _winsubp`; ensure scripts/ is importable.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location("canonical_mutate_guard", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cmg = _load_module()

# --------------------------------------------------------------------------
# Layer 1: pure-function tests
# --------------------------------------------------------------------------

_MUTATING_CASES = [
    ("git checkout -b foo", "checkout -b"),
    ("git checkout main", "bare checkout <branch-like arg>"),
    ("git switch main", "switch"),
    ('git commit -m "msg"', "commit"),
    ("git merge feature", "merge"),
    ("git rebase origin/main", "rebase"),
    ("git reset --hard", "reset"),
    ("git cherry-pick abc123", "cherry-pick"),
    ("git revert abc123", "revert"),
    ("git stash pop", "stash pop"),
    ("git stash apply", "stash apply"),
    ("git branch -d foo", "branch -d"),
    ("git branch -D foo", "branch -D"),
    ("git branch --delete foo", "branch --delete"),
    ("git pull", "bare pull"),
    ("git pull origin main", "pull with refspec, no --ff-only"),
]

_READONLY_CASES = [
    ("git status", "status"),
    ("git log", "log"),
    ("git log --oneline -5", "log with flags"),
    ("git diff", "diff"),
    ("git diff origin/main", "diff with ref"),
    ("git show HEAD", "show"),
    ("git fetch", "fetch"),
    ("git fetch origin", "fetch with remote"),
    ("git branch --show-current", "branch --show-current"),
    ("git rev-parse --show-toplevel", "rev-parse"),
    ("git ls-tree -r HEAD", "ls-tree"),
    ("git blame foo.py", "blame"),
    ("git remote -v", "remote -v"),
    ("git branch", "plain branch (list)"),
    ("git branch foo", "branch create (no switch)"),
    ("git stash list", "stash list"),
    ("git stash show", "stash show"),
    ("git checkout -- file.txt", "checkout -- <path> (file restore)"),
    ("git checkout -- some/nested/path.py", "checkout -- <nested path>"),
    ("git pull --ff-only", "pull --ff-only"),
    ("git pull origin main --ff-only", "pull with refspec and --ff-only"),
    ("ls -la", "non-git command"),
    ("npm install", "non-git command"),
    ("echo hello", "non-git command"),
]


def test_mutating_verbs_classified_as_mutating() -> str:
    for cmd, label in _MUTATING_CASES:
        if not cmg.is_mutating_segment(cmd):
            raise AssertionError(f"{label!r} ({cmd!r}) should be classified as mutating")
    return f"{len(_MUTATING_CASES)} mutating-verb cases correctly classified"


def test_readonly_commands_classified_as_safe() -> str:
    for cmd, label in _READONLY_CASES:
        if cmg.is_mutating_segment(cmd):
            raise AssertionError(f"{label!r} ({cmd!r}) should NOT be classified as mutating")
    return f"{len(_READONLY_CASES)} read-only cases correctly classified as safe"


def test_classify_returns_first_mutating_segment() -> str:
    cmd = "git status && git checkout -b foo && git log"
    matched = cmg.classify(cmd)
    if matched is None or "checkout" not in matched:
        raise AssertionError(f"expected the checkout segment to be flagged, got {matched!r}")
    return "classify() finds the mutating segment among safe ones"


def test_classify_returns_none_when_all_segments_safe() -> str:
    cmd = "git status && git log && git diff"
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(f"expected no match, got {matched!r}")
    return "classify() returns None when every segment is read-only"


def test_heredoc_mention_does_not_trigger() -> str:
    """A mutating verb mentioned only inside a heredoc body (not the actual
    invoked command) must not trigger — the career-playbook #442 lesson.
    Splitting on newlines means each heredoc body line is its own segment
    that does not START with `git`, so it cannot match the anchored verb
    classifier.
    """
    cmd = (
        'cat <<EOF > notes.txt\n'
        'Remember: do not run git checkout -b or git reset --hard here.\n'
        'Also avoid git commit without review.\n'
        'EOF\n'
        'git status'
    )
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"heredoc body mentioning mutating verbs should not trigger, got {matched!r}"
        )
    return "heredoc/prose mention of a mutating verb does not trigger (career-playbook #442 lesson)"


def test_env_prefixed_mutating_command_still_classified() -> str:
    cmd = "ALLOW_CANONICAL_MUTATE=1 git checkout -b foo"
    if not cmg.is_mutating_segment(cmd):
        raise AssertionError("leading env-var assignment should not hide the git verb")
    return "leading env-var assignment does not prevent verb classification"


def test_cd_redirect_takes_whole_command_out_of_scope() -> str:
    """A bare `cd <path>` persists across the rest of the shell invocation —
    `cd X && git checkout -b foo` really does run the checkout in X, not in
    `cwd`. So once a `cd` appears anywhere, the WHOLE command is out of scope,
    not just the segment carrying it — a later segment must not be flagged
    against the wrong (unknown) directory.
    """
    cases = [
        "cd C:/Users/brown/Git/dev-env && git checkout -b foo",
        "cd C:/Users/brown/Git/dev-env && git status && git checkout -b foo",
    ]
    for cmd in cases:
        matched = cmg.classify(cmd)
        if matched is not None:
            raise AssertionError(f"cd anywhere should take the whole command out of scope, got {matched!r} for {cmd!r}")
    return f"{len(cases)} cd-redirect commands correctly out of scope in full (not just the cd segment)"


def test_dashC_redirect_only_skips_its_own_segment() -> str:
    """`git -C <path>` / `git --git-dir=<path>` redirects only the single git
    invocation carrying the flag — unlike `cd`, it does not change the shell's
    directory for later segments, so a DIFFERENT, non-redirected mutating
    segment elsewhere in the same command must still be caught.
    """
    only_redirect_cases = [
        "git -C C:/Users/brown/Git/dev-env checkout -b foo",
        "git --git-dir=C:/Users/brown/Git/dev-env/.git checkout -b foo",
    ]
    for cmd in only_redirect_cases:
        matched = cmg.classify(cmd)
        if matched is not None:
            raise AssertionError(f"-C/--git-dir segment should be skipped, got {matched!r} for {cmd!r}")

    mixed = "git -C C:/Users/brown/Git/dev-env status && git checkout -b foo"
    matched = cmg.classify(mixed)
    if matched is None or "checkout" not in matched:
        raise AssertionError(
            f"a non-redirected mutating segment after a -C segment must still be caught, got {matched!r}"
        )
    return "git -C/--git-dir skips only its own segment; other segments still classified"


def main_unit() -> list:
    return [
        ("mutating verbs classified as mutating", test_mutating_verbs_classified_as_mutating),
        ("read-only commands classified as safe", test_readonly_commands_classified_as_safe),
        ("classify() finds first mutating segment", test_classify_returns_first_mutating_segment),
        ("classify() returns None when all safe", test_classify_returns_none_when_all_segments_safe),
        ("heredoc mention does not trigger", test_heredoc_mention_does_not_trigger),
        ("env-prefixed mutating command still classified", test_env_prefixed_mutating_command_still_classified),
        ("cd redirect takes whole command out of scope", test_cd_redirect_takes_whole_command_out_of_scope),
        ("-C/--git-dir redirect skips only its own segment", test_dashC_redirect_only_skips_its_own_segment),
    ]


# --------------------------------------------------------------------------
# Layer 2: end-to-end subprocess tests
# --------------------------------------------------------------------------


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _init_throwaway_repo(root: Path) -> None:
    """Initialize a minimal real git repo at `root` so `git -C <root> rev-parse
    --show-toplevel` resolves for real — the hook's canonical-root resolution
    step needs an actual repo, not a bare temp dir.
    """
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )


def test_main_blocks_mutating_command_from_canonical_root() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout -b some-branch"},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        try:
            reason = json.loads(proc.stdout).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stdout was not JSON: {proc.stdout!r}")
        if "canonical-mutate-guard" not in reason or "ALLOW_CANONICAL_MUTATE=1" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "mutating command from a canonical (non-worktree) git repo blocked (exit 2)"


def test_main_allows_readonly_command_from_canonical_root() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (allow), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "git status from a canonical root is allowed (exit 0)"

def test_main_allows_pull_ff_only_blocks_bare_pull() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)

        allowed = _run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git pull --ff-only"},
            "cwd": str(repo),
        })
        if allowed.returncode != 0:
            raise AssertionError(f"git pull --ff-only should be allowed, got {allowed.returncode}")

        blocked = _run_hook({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git pull"},
            "cwd": str(repo),
        })
        if blocked.returncode != 2:
            raise AssertionError(f"bare git pull should be blocked, got {blocked.returncode}")
    return "git pull --ff-only allowed, bare git pull blocked"


def test_main_allows_any_command_from_worktree_cwd() -> str:
    """cwd matching the worktree pattern is entirely out of scope — even a
    mutating command is allowed, since ADR-024's hook covers the worktree
    surface and this hook's whole purpose is the non-worktree case.
    """
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout -b foo"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (out of scope) from worktree cwd, got {proc.returncode}. "
                f"stdout={proc.stdout!r}"
            )
    return "mutating command from a worktree-pattern cwd allowed (out of scope, exit 0)"


def test_main_override_token_bypasses_block() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ALLOW_CANONICAL_MUTATE=1 git checkout -b foo"},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (override applied), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "ALLOW_CANONICAL_MUTATE=1 override bypasses the block (exit 0)"


def test_main_failsopen_on_nongit_cwd() -> str:
    """A cwd that is not a git repo at all (git rev-parse fails) fails open —
    the hook cannot determine a canonical root to warn about, so it must not
    block an unrelated non-git directory.
    """
    with tempfile.TemporaryDirectory() as tmp:
        non_repo = Path(tmp) / "not-a-repo"
        non_repo.mkdir()
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout -b foo"},
            "cwd": str(non_repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (fail open, not a repo), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
    return "non-git cwd fails open (exit 0) — cannot resolve a canonical root"


def test_main_failsopen_on_malformed_json() -> str:
    proc = subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input="{not valid json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"expected exit 0 (fail open) on malformed JSON, got {proc.returncode}")
    return "malformed JSON fails open (exit 0)"


def test_main_failsopen_on_missing_cwd() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git checkout -b foo"},
        # no "cwd" key at all
    }
    proc = _run_hook(payload)
    if proc.returncode != 0:
        raise AssertionError(f"expected exit 0 (fail open) on missing cwd, got {proc.returncode}")
    return "missing cwd fails open (exit 0)"


def test_main_failsopen_on_empty_cwd() -> str:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git checkout -b foo"},
        "cwd": "",
    }
    proc = _run_hook(payload)
    if proc.returncode != 0:
        raise AssertionError(f"expected exit 0 (fail open) on empty cwd, got {proc.returncode}")
    return "empty cwd fails open (exit 0)"


def test_main_noop_on_non_bash_tool() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(repo / "foo.txt")},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0 (non-Bash no-op), got {proc.returncode}")
    return "non-Bash tool_name is a no-op (exit 0)"


def test_main_empty_stdin_noop() -> str:
    proc = subprocess.run(
        [sys.executable, str(MODULE_PATH)],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"expected exit 0 on empty stdin, got {proc.returncode}")
    return "empty stdin is a no-op (exit 0)"


def main_e2e() -> list:
    return [
        ("main() blocks mutating command from canonical root", test_main_blocks_mutating_command_from_canonical_root),
        ("main() allows read-only command from canonical root", test_main_allows_readonly_command_from_canonical_root),
        ("main() allows pull --ff-only, blocks bare pull", test_main_allows_pull_ff_only_blocks_bare_pull),
        ("main() allows any command from worktree cwd", test_main_allows_any_command_from_worktree_cwd),
        ("main() override token bypasses block", test_main_override_token_bypasses_block),
        ("main() fails open on non-git cwd", test_main_failsopen_on_nongit_cwd),
        ("main() fails open on malformed JSON", test_main_failsopen_on_malformed_json),
        ("main() fails open on missing cwd", test_main_failsopen_on_missing_cwd),
        ("main() fails open on empty cwd", test_main_failsopen_on_empty_cwd),
        ("main() no-ops on non-Bash tool", test_main_noop_on_non_bash_tool),
        ("main() no-ops on empty stdin", test_main_empty_stdin_noop),
    ]


def main() -> int:
    tests = main_unit() + main_e2e()
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
