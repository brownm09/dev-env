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
     heredoc-mention lesson; `cd`/`-C`/`--git-dir` redirects are out of scope;
     dev-env#481 heredoc-command-substitution-body exclusion — a body line
     that itself STARTS with a mutating verb or the override token inside a
     `$(cat <<'MARKER' ... MARKER)` span, applied to both classify() and
     _has_override()).

     Since dev-env#511 (ADR-050 Amendment 7), segmenting comes from the
     shared `_hookio.split_top_level(cmd, split_pipe=True)` engine rather
     than a narrow regex splitter — the heredoc/override tests above still
     pass unchanged (the engine subsumes that behavior), and this file adds
     coverage for two false positives the narrow regex splitter had that the
     shared engine's quote-tracking and general heredoc-opacity fix: a
     mutating-looking verb inside a quoted `&&`/`|` (e.g. a `git log
     --grep=` pattern), and a *bare* (non-command-substitution) heredoc body
     line that itself starts with a mutating verb — plus the guard's new
     `split_pipe=True` capability (a mutating command after a pipe, e.g.
     `echo msg | git commit -F -`, is now correctly classified as mutating).
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


def test_heredoc_command_substitution_body_not_classified() -> str:
    """A heredoc body line that itself STARTS with a mutating verb — e.g. a
    markdown code-fence example inside a `gh issue create --body "$(cat
    <<'EOF' ... EOF)"` invocation — must not trigger. Distinct from
    test_heredoc_mention_does_not_trigger (mid-line mention): there, the body
    line does not start with `git`, so the pre-existing anchor-at-segment-
    start check already handled it. Here, the body line genuinely BEGINS with
    `git ...` after the `\\n`-split, making it indistinguishable from a real
    invocation without the dev-env#481 fix (`_strip_heredoc_command_subs()`
    removing the whole `$(cat <<'MARKER' ... MARKER)` span before segment
    splitting even happens).

    Reproduction (dev-env#481, 2026-07-01): the actual invoked command was
    `gh issue create` (a remote API call touching no local git state at all),
    but the pre-fix hook reported a `git commit` line lifted from inside the
    quoted (`<<'EOF'`, no shell expansion) heredoc body string as the blocked
    command.
    """
    cmd = (
        'gh issue create --repo brownm09/dev-env '
        '--title "canonical-mutate-guard heredoc gap" '
        '--body "$(cat <<\'EOF\'\n'
        '## Suggested fix\n'
        'Run this to land the change:\n'
        'git commit -m "fix: heredoc gap" -- sessions/some-project/stub.md\n'
        'EOF\n'
        ')"'
    )
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"heredoc body line starting with a mutating verb inside a "
            f"$(cat <<'MARKER'...) command-substitution argument should not "
            f"trigger, got {matched!r}"
        )
    return (
        "heredoc body line starting with a mutating verb inside "
        "$(cat <<'MARKER'...) does not trigger (dev-env#481)"
    )


def test_real_command_after_heredoc_catsub_still_classified() -> str:
    """The dev-env#481 fix must stay precise: a REAL mutating command chained
    (via `&&`) after a heredoc-fed `$(cat <<'MARKER'...)` command
    substitution closes must still be caught — the fix strips only the span
    between the opening heredoc line and its own closing MARKER line, not
    everything that follows it.
    """
    cmd = (
        'gh issue create --body "$(cat <<\'EOF\'\n'
        'git commit example text\n'
        'EOF\n'
        ')" && git checkout -b evil'
    )
    matched = cmg.classify(cmd)
    if matched is None or "checkout" not in matched:
        raise AssertionError(
            f"a real mutating command chained after a heredoc-fed command "
            f"substitution must still be caught, got {matched!r}"
        )
    return (
        "real mutating command after a $(cat <<'MARKER'...) span still "
        "classified (dev-env#481 fix stays precise)"
    )


def test_override_mention_at_heredoc_body_line_start_does_not_bypass() -> str:
    """The override token appearing at the START of a heredoc body line
    inside a `$(cat <<'MARKER' ... MARKER)` command-substitution argument
    (e.g. documentation prose showing the override syntax) must not bypass
    the block — the same class of gap as classify()'s heredoc-body false
    positive (dev-env#481), but on the override-detection path in
    _has_override() instead.
    """
    cmd = (
        'gh issue create --body "$(cat <<\'EOF\'\n'
        'ALLOW_CANONICAL_MUTATE=1 git checkout -b foo\n'
        'EOF\n'
        ')" && git checkout -b real-branch'
    )
    if cmg._has_override(cmd):
        raise AssertionError(
            f"override token at the start of a heredoc body line should not bypass: {cmd!r}"
        )
    return (
        "override token at heredoc-body-line-start (inside $(cat <<'MARKER'...)) "
        "does not bypass (dev-env#481)"
    )


def test_quoted_ampersand_with_fake_verb_not_misclassified() -> str:
    """dev-env#511: without quote-tracking, the prior regex-based `_SEGMENT_SPLIT`
    splitter would carve `git checkout -b evil"` out of this grep pattern as
    its own segment and misclassify a harmless `git log` search as a
    `checkout`. The shared `_hookio.split_top_level` engine tracks quote
    state, so `&&` inside the double-quoted `--grep` value is never a segment
    boundary — the whole command stays one `git log` segment.
    """
    cmd = 'git log --grep="foo && git checkout -b evil"'
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"&& inside a quoted --grep value should not split out a fake "
            f"mutating segment, got {matched!r}"
        )
    return "quoted && containing a fake mutating verb does not misclassify a git log (dev-env#511)"


def test_bare_heredoc_body_starting_with_verb_not_misclassified() -> str:
    """dev-env#511: the pre-existing `_HEREDOC_CATSUB_RE` only stripped the
    `$(cat <<MARKER...)` command-substitution idiom (dev-env#481) — a *bare*
    heredoc (no command substitution wrapper) was never touched by it, so a
    body line that itself starts with a mutating verb would become its own
    segment after the old splitter's `\\n`-split and be misclassified as a
    real invocation. The shared engine treats any heredoc body (bare or
    inside a subshell) as one opaque span.
    """
    cmd = "git status <<EOF\ngit commit --amend\nEOF"
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"a bare heredoc body line starting with a mutating verb should "
            f"not be classified as a real invocation, got {matched!r}"
        )
    return "bare heredoc body line starting with a mutating verb does not misclassify (dev-env#511)"


def test_pipe_splits_and_classifies_mutating_segment() -> str:
    """The guard passes `split_pipe=True` to the shared engine (unlike
    `scan_top_level`'s other two callers) because a mutating git invocation
    can read its input from a pipe, e.g. `git commit -F -` reading a piped
    commit message. This is unchanged behavior from the pre-#511 regex
    splitter (which also split on `|`) — pinned here against the new engine.
    """
    cmd = "echo commit message | git commit -F -"
    matched = cmg.classify(cmd)
    if matched is None or "commit" not in matched:
        raise AssertionError(f"a mutating command after a pipe should be classified, got {matched!r}")
    return "a mutating command after a pipe is still classified (split_pipe=True)"


def test_pipe_inside_quotes_does_not_falsely_split() -> str:
    """Quote-tracking applies to `|` exactly as it does to `&&` — a `|`
    character inside a quoted string must not be mistaken for a pipe even
    though the guard enables `split_pipe=True`.
    """
    cmd = 'git log --grep="foo | git checkout -b evil"'
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"| inside a quoted --grep value should not split out a fake "
            f"mutating segment, got {matched!r}"
        )
    return "quoted | containing a fake mutating verb does not misclassify a git log"


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


def test_flag_prefixed_stash_pop_apply_classified_as_mutating() -> str:
    """`git -c gc.auto=0 stash pop`, `git --no-optional-locks stash apply`,
    and `git stash --quiet pop` all put a flag between the verb and the real
    subcommand, pushing it off a fixed tokens[1] position — a prior version
    checked only tokens[1] and let these slip through unblocked. The stash
    branch now scans tokens[1:] like every other verb branch.
    """
    cases = [
        "git -c gc.auto=0 stash pop",
        "git --no-optional-locks stash apply",
        "git stash --quiet pop",
        "git stash -u apply",
    ]
    for cmd in cases:
        if not cmg.is_mutating_segment(cmd):
            raise AssertionError(f"flag-prefixed stash pop/apply should be classified as mutating: {cmd!r}")
    return f"{len(cases)} flag-prefixed stash pop/apply cases correctly classified as mutating"


def test_env_prefixed_cd_recognized_as_redirect() -> str:
    """`FOO=1 cd /tmp && git checkout -b x` must be recognized as a cd-redirect
    (whole command out of scope) — an unstripped cd match would miss the env
    prefix and let the checkout get falsely blocked even though it targets
    `/tmp`, not the canonical root.
    """
    cases = [
        "FOO=1 cd /tmp && git checkout -b x",
        "FOO=1 BAR=2 cd C:/Users/brown/Git/dev-env && git checkout -b x",
    ]
    for cmd in cases:
        matched = cmg.classify(cmd)
        if matched is not None:
            raise AssertionError(f"env-prefixed cd should take the whole command out of scope, got {matched!r} for {cmd!r}")
    return f"{len(cases)} env-prefixed cd commands correctly recognized as a redirect (out of scope)"


def test_override_anchored_prefix_bypasses() -> str:
    """A genuine leading prefix on the command or a segment bypasses the
    block — the existing, intended override usage.
    """
    cases = [
        "ALLOW_CANONICAL_MUTATE=1 git checkout -b foo",
        "git status && ALLOW_CANONICAL_MUTATE=1 git checkout -b foo",
    ]
    for cmd in cases:
        if not cmg._has_override(cmd):
            raise AssertionError(f"expected override to be recognized as a leading prefix: {cmd!r}")
    return f"{len(cases)} genuine leading-prefix override cases correctly bypass"


def test_override_mention_only_does_not_bypass() -> str:
    """The override token merely MENTIONED inside a commit message (a
    substring, not a leading prefix on any segment) must NOT bypass the
    block — this was the #2 bug: a bare `tok in cmd` substring test let
    `git commit -m "ALLOW_CANONICAL_MUTATE=1 was mentioned"` silently
    disable the guard.
    """
    cases = [
        'git commit -m "ALLOW_CANONICAL_MUTATE=1 was mentioned"',
        'git commit -m "please dont ALLOW_CANONICAL_MUTATE=1 here"',
        "echo ALLOW_CANONICAL_MUTATE=1 && git checkout -b foo",
    ]
    for cmd in cases:
        if cmg._has_override(cmd):
            raise AssertionError(f"mention-only occurrence should NOT bypass the block: {cmd!r}")
    return f"{len(cases)} mention-only (non-prefix) occurrences correctly do NOT bypass"


def test_resolve_git_toplevel_failsopen_on_timeout_and_oserror() -> str:
    """`_resolve_git_toplevel` is the actual fail-open guarantee for the whole
    hook — every caller treats `None` as "can't determine a canonical root,
    exit 0." Inject a stub `subprocess.run` that raises
    `subprocess.TimeoutExpired` (a hung git process) and one that raises
    `OSError` (e.g. git binary missing / exec failure) and assert both paths
    return None rather than propagating the exception.
    """
    import subprocess as _subprocess_module

    original_run = cmg.subprocess.run

    def _raise_timeout(*args, **kwargs):
        raise _subprocess_module.TimeoutExpired(cmd="git", timeout=10)

    def _raise_oserror(*args, **kwargs):
        raise OSError("git binary not found")

    try:
        cmg.subprocess.run = _raise_timeout
        result = cmg._resolve_git_toplevel("C:/some/cwd")
        if result is not None:
            raise AssertionError(f"expected None on TimeoutExpired, got {result!r}")

        cmg.subprocess.run = _raise_oserror
        result = cmg._resolve_git_toplevel("C:/some/cwd")
        if result is not None:
            raise AssertionError(f"expected None on OSError, got {result!r}")
    finally:
        cmg.subprocess.run = original_run

    return "_resolve_git_toplevel returns None on both TimeoutExpired and OSError (fail-open guarantee)"


def main_unit() -> list:
    return [
        ("mutating verbs classified as mutating", test_mutating_verbs_classified_as_mutating),
        ("read-only commands classified as safe", test_readonly_commands_classified_as_safe),
        ("classify() finds first mutating segment", test_classify_returns_first_mutating_segment),
        ("classify() returns None when all safe", test_classify_returns_none_when_all_segments_safe),
        ("heredoc mention does not trigger", test_heredoc_mention_does_not_trigger),
        ("heredoc command-substitution body not classified", test_heredoc_command_substitution_body_not_classified),
        ("real command after heredoc catsub still classified", test_real_command_after_heredoc_catsub_still_classified),
        ("override mention at heredoc body line start does not bypass", test_override_mention_at_heredoc_body_line_start_does_not_bypass),
        ("quoted && with fake verb not misclassified (dev-env#511)", test_quoted_ampersand_with_fake_verb_not_misclassified),
        ("bare heredoc body starting with verb not misclassified (dev-env#511)", test_bare_heredoc_body_starting_with_verb_not_misclassified),
        ("pipe splits and classifies mutating segment", test_pipe_splits_and_classifies_mutating_segment),
        ("pipe inside quotes does not falsely split", test_pipe_inside_quotes_does_not_falsely_split),
        ("env-prefixed mutating command still classified", test_env_prefixed_mutating_command_still_classified),
        ("cd redirect takes whole command out of scope", test_cd_redirect_takes_whole_command_out_of_scope),
        ("-C/--git-dir redirect skips only its own segment", test_dashC_redirect_only_skips_its_own_segment),
        ("flag-prefixed stash pop/apply classified as mutating", test_flag_prefixed_stash_pop_apply_classified_as_mutating),
        ("env-prefixed cd recognized as redirect", test_env_prefixed_cd_recognized_as_redirect),
        ("override anchored prefix bypasses", test_override_anchored_prefix_bypasses),
        ("override mention-only does not bypass", test_override_mention_only_does_not_bypass),
        ("_resolve_git_toplevel fails open on timeout/OSError", test_resolve_git_toplevel_failsopen_on_timeout_and_oserror),
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

    `-c init.templateDir= -c core.hooksPath=` neutralizes any global template
    directory / hooks path the developer's machine has configured, so this
    throwaway repo's `git init` can't pick up unrelated local hooks/templates
    and behave differently across machines.
    """
    subprocess.run(
        [
            "git", "-c", "init.templateDir=", "-c", "core.hooksPath=",
            "init", "-q", str(root),
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )


def test_main_blocks_mutating_command_from_canonical_root() -> str:
    """The block reason must land on stderr, not stdout — Claude Code
    discards stdout on a PreToolUse hook exit code 2 and surfaces only
    stderr to the model. Asserting against proc.stdout here would pass even
    if the reason were silently invisible to the model.
    """
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
        if proc.stdout.strip():
            raise AssertionError(f"expected empty stdout (reason must go to stderr), got {proc.stdout!r}")
        try:
            reason = json.loads(proc.stderr).get("reason", "")
        except json.JSONDecodeError:
            raise AssertionError(f"stderr was not JSON: {proc.stderr!r}")
        if "canonical-mutate-guard" not in reason or "ALLOW_CANONICAL_MUTATE=1" not in reason:
            raise AssertionError(f"block reason missing expected markers: {reason!r}")
    return "mutating command from a canonical (non-worktree) git repo blocked (exit 2), reason on stderr"


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


def test_main_allows_quoted_fake_verb_git_log_from_canonical_root() -> str:
    """End-to-end proof the dev-env#511 fix reaches the real hook process,
    not just the pure classify() function: before the shared-engine
    convergence, this git log would have been wrongly BLOCKED (exit 2) by
    the old regex splitter's quote-mis-split.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": 'git log --grep="foo && git checkout -b evil"'},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (allow), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "git log with a quoted && + fake verb is allowed end-to-end (dev-env#511)"


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


def test_main_failsopen_on_nondict_json() -> str:
    """Valid JSON that is not an object (`[]`, `"x"`, `123`, `null`) must fail
    open, not raise an uncaught AttributeError from `data.get(...)` — the
    hook's own documented contract is "fail open on anything unparseable,"
    and a non-dict payload is exactly that even though json.loads() succeeds.
    """
    for payload_str in ("[]", '"x"', "123", "null"):
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input=payload_str,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (fail open) on non-dict JSON {payload_str!r}, "
                f"got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "non-dict JSON payloads ([], \"x\", 123, null) all fail open (exit 0)"


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
        ("main() allows quoted fake-verb git log (dev-env#511)", test_main_allows_quoted_fake_verb_git_log_from_canonical_root),
        ("main() allows pull --ff-only, blocks bare pull", test_main_allows_pull_ff_only_blocks_bare_pull),
        ("main() allows any command from worktree cwd", test_main_allows_any_command_from_worktree_cwd),
        ("main() override token bypasses block", test_main_override_token_bypasses_block),
        ("main() fails open on non-git cwd", test_main_failsopen_on_nongit_cwd),
        ("main() fails open on malformed JSON", test_main_failsopen_on_malformed_json),
        ("main() fails open on non-dict JSON", test_main_failsopen_on_nondict_json),
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
