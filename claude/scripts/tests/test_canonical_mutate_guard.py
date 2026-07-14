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
     segment-split/anchor and redirect behavior (career-playbook #442
     heredoc-mention lesson; a bare `cd` is out of scope, while
     `-C`/`--git-dir`/`--work-tree` targets are captured and resolved against
     the canonical-root check per dev-env#576;
     dev-env#481 heredoc-command-substitution-body exclusion — a body line
     that itself STARTS with a mutating verb or the override token inside a
     `$(cat <<'MARKER' ... MARKER)` span, applied to both classify() and
     _has_override()).

     Also covers `classify()` / `is_mutating_gh_segment()` (dev-env#558,
     ADR-071 Amendment 1): `gh pr merge -d`/`--delete-branch` (any flag order,
     other flags present) is classified as mutating — it checks out the base
     branch and deletes the local branch locally, the same silent-HEAD-thrash
     harm model reached through a `gh` invocation instead of a `git` verb —
     while a bare `gh pr merge` or `gh pr merge --squash` (no delete-branch
     flag, remote-API-only) stays classified as safe. Same anchoring/heredoc-
     mention/override machinery as the git-verb classifier, reused as-is.

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

     `/review` on the dev-env#511 PR caught the equal-and-opposite regression
     the convergence introduced: `split_top_level`'s heredoc/subshell opacity
     means a segment can now span multiple physical lines, which
     `is_mutating_segment()`'s `$`-anchored, non-DOTALL `_GIT_INVOCATION_RE`
     (and, since dev-env#576, the redirect-dir capture now in
     `_parse_git_prefix`, historically an unanchored `_REDIRECT_RE.search()`)
     were never designed for — a real `git commit -m "$(cat <<'EOF' ...)"` (this repo's
     own documented commit-message idiom) silently failed to match at all,
     and a real commit whose heredoc body merely *mentioned* "git -C
     /somewhere" as prose was wrongly treated as redirected to another repo
     and skipped. Both are now restricted to each segment's first physical
     line via `_first_line()`; see its docstring for why `re.DOTALL` would
     have been the wrong fix (it re-exposes heredoc body words to the stash
     pop/apply and checkout `--` token scans).
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
       - a mutating verb mentioned only inside a heredoc body does not trigger;
       - `gh pr merge --delete-branch`/`-d` from a canonical root is BLOCKED
         (exit 2); the same command from a worktree-pattern cwd is allowed
         (exit 0); a bare `gh pr merge` / `gh pr merge --squash` is allowed
         from a canonical root; and the override token bypasses the block
         (dev-env#558, ADR-071 Amendment 1).

Usage:
    py -3 claude/scripts/tests/test_canonical_mutate_guard.py

Exit 0 = all pass.
"""
import importlib.util
import json
import os
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


_GH_MUTATING_CASES = [
    ("gh pr merge --delete-branch", "gh pr merge --delete-branch"),
    ("gh pr merge -d", "gh pr merge -d"),
    ("gh pr merge --squash --delete-branch", "gh pr merge --squash --delete-branch (flag order/other flags present)"),
    ("gh pr merge -d --squash", "gh pr merge -d --squash (delete-branch flag first)"),
    ("gh pr merge --delete-branch=true", "gh pr merge --delete-branch=true (explicit-value Cobra form, review finding on PR #560)"),
]

_GH_READONLY_CASES = [
    ("gh pr merge", "bare gh pr merge (remote-only, no local mutation)"),
    ("gh pr merge --squash", "gh pr merge --squash (no delete-branch flag)"),
    ("gh pr merge --auto", "gh pr merge --auto (no delete-branch flag)"),
    ("gh pr merge --delete-branch=false", "gh pr merge --delete-branch=false (explicit opt-out, no local mutation)"),
    ("gh pr create --title foo", "gh pr create (not a merge)"),
    ("gh pr view 42", "gh pr view (not a merge)"),
    ("gh issue create --title foo", "gh issue create (not pr merge)"),
]


def test_gh_pr_merge_delete_branch_classified_as_mutating() -> str:
    """dev-env#558: `gh pr merge -d`/`--delete-branch`, run from the branch
    it's merging, must check out the base branch and delete the local branch
    locally (a checked-out branch can't be deleted) — the exact same silent
    local-HEAD-thrash harm model is_mutating_segment() already blocks for
    `git checkout`/`git branch -d`, reached through a `gh` invocation instead
    of a `git` verb.
    """
    for cmd, label in _GH_MUTATING_CASES:
        if not cmg.is_mutating_gh_segment(cmd):
            raise AssertionError(f"{label!r} ({cmd!r}) should be classified as mutating")
    return f"{len(_GH_MUTATING_CASES)} gh pr merge -d/--delete-branch cases correctly classified as mutating"


def test_gh_pr_merge_without_delete_branch_classified_as_safe() -> str:
    """A bare `gh pr merge` (no delete-branch flag) merges only remotely via
    the GitHub API and touches no local state at all — must stay unblocked,
    matching gh's own remote-only behavior and this hook's zero-friction
    treatment of every other non-mutating command.
    """
    for cmd, label in _GH_READONLY_CASES:
        if cmg.is_mutating_gh_segment(cmd):
            raise AssertionError(f"{label!r} ({cmd!r}) should NOT be classified as mutating")
    return f"{len(_GH_READONLY_CASES)} gh commands without delete-branch correctly classified as safe"


def test_classify_flags_gh_pr_merge_delete_branch() -> str:
    """classify()'s per-segment loop must catch a `gh pr merge -d` segment
    exactly like a `git checkout -b` segment — same severity, same existing
    cd-scope/override machinery, wired via the same loop.
    """
    cmd = "git status && gh pr merge --delete-branch"
    matched = cmg.classify(cmd)
    if matched is None or "gh pr merge" not in matched:
        raise AssertionError(f"expected the gh pr merge segment to be flagged, got {matched!r}")
    return "classify() finds a gh pr merge --delete-branch segment among safe ones"


def test_classify_allows_bare_gh_pr_merge() -> str:
    cmd = "git status && gh pr merge --squash"
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(f"expected no match for a delete-branch-less gh pr merge, got {matched!r}")
    return "classify() allows gh pr merge --squash (no delete-branch flag)"


def test_gh_pr_merge_override_bypasses() -> str:
    """The override token must bypass a gh pr merge -d match exactly like it
    bypasses a git-verb match — same generic override machinery, no
    gh-specific carve-out needed.
    """
    cmd = "ALLOW_CANONICAL_MUTATE=1 gh pr merge -d"
    if not cmg._has_override(cmd):
        raise AssertionError(f"expected override to be recognized ahead of a gh pr merge -d: {cmd!r}")
    return "ALLOW_CANONICAL_MUTATE=1 override recognized ahead of gh pr merge -d"


def test_gh_pr_merge_delete_branch_mention_in_heredoc_does_not_trigger() -> str:
    """A heredoc body merely mentioning "gh pr merge -d" as prose (e.g. a
    commit message describing this very fix) must not trigger — mirrors
    test_heredoc_mention_does_not_trigger for the git-verb classifier.
    """
    cmd = (
        'cat <<EOF > notes.txt\n'
        'This hook now also blocks gh pr merge -d and gh pr merge --delete-branch.\n'
        'EOF\n'
        'git status'
    )
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"heredoc body mentioning 'gh pr merge -d' should not trigger, got {matched!r}"
        )
    return "heredoc/prose mention of 'gh pr merge -d' does not trigger"


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


def test_commit_with_heredoc_command_sub_message_still_classified() -> str:
    """/review finding (dev-env#511): `split_top_level`'s heredoc/subshell
    opacity means a segment can now span multiple physical lines, but
    `_GIT_INVOCATION_RE` is `$`-anchored with no `re.DOTALL` — without
    `is_mutating_segment()` restricting its match to the segment's first
    physical line (`_first_line()`), this real commit (this repo's own
    documented commit-message idiom) silently failed to match at all and was
    classified as non-mutating. The existing suite only covered the inverse
    (a heredoc body line that merely *looks* like a command must not
    trigger) — this covers a REAL command whose own argument happens to
    contain a heredoc/command substitution.
    """
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        "subject line\n"
        "EOF\n"
        ")\""
    )
    matched = cmg.classify(cmd)
    if matched is None:
        raise AssertionError(
            f"a real git commit whose -m argument is a heredoc command "
            f"substitution must still be classified as mutating, got {matched!r}"
        )
    return "git commit -m \"$(cat <<'EOF'...)\" (this repo's own commit idiom) still classified as mutating (dev-env#511 follow-up)"


def test_bare_heredoc_commit_message_still_classified() -> str:
    """Same class of bug as above, for the bare (non-command-substitution)
    heredoc-as-stdin idiom `git commit -F - <<EOF ... EOF`.
    """
    cmd = "git commit -F - <<EOF\nsome commit message\nEOF"
    matched = cmg.classify(cmd)
    if matched is None:
        raise AssertionError(
            f"a real git commit reading a piped/heredoc message must still "
            f"be classified as mutating, got {matched!r}"
        )
    return "git commit -F - <<EOF...EOF still classified as mutating (dev-env#511 follow-up)"


def test_redirect_mention_in_heredoc_body_does_not_skip_real_mutating_command() -> str:
    """/review finding (dev-env#511): `_REDIRECT_RE.search()` is unanchored,
    and its `(?:^|\\s)` alternation treats an embedded newline the same as a
    space — so a commit whose heredoc BODY merely mentions "git -C
    /somewhere" as prose text was wrongly treated as redirected to another
    repo and skipped, letting a real mutating commit through unblocked.
    `_first_line()` restricts the `_REDIRECT_RE` check in `classify()` to
    the segment's own first physical line, where the actual git invocation
    lives.
    """
    cmd = (
        "git commit -m \"$(cat <<'EOF'\n"
        "See also: git -C /tmp status for context\n"
        "EOF\n"
        ")\""
    )
    matched = cmg.classify(cmd)
    if matched is None:
        raise AssertionError(
            f"a real commit must not be skipped just because its heredoc "
            f"body mentions 'git -C' as prose, got {matched!r}"
        )
    return "heredoc body merely mentioning 'git -C' does not skip a real mutating commit (dev-env#511 follow-up)"


def test_dotall_alternative_would_have_been_wrong() -> str:
    """Documents why the fix is 'restrict to first line', not 'add
    re.DOTALL to _GIT_INVOCATION_RE': DOTALL would make `rest.split()`
    tokenize the ENTIRE segment including heredoc body words, so a
    read-only `git stash list` whose heredoc body happens to contain the
    word "apply" would wrongly match the stash verb's pop/apply token scan.
    This must stay allowed.
    """
    cmd = "git stash list <<EOF\nplease apply this later\nEOF"
    matched = cmg.classify(cmd)
    if matched is not None:
        raise AssertionError(
            f"a read-only git stash list must not be blocked just because "
            f"its heredoc body contains the word 'apply', got {matched!r}"
        )
    return "read-only git stash list with 'apply' in its heredoc body stays allowed (dev-env#511 follow-up)"


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


def test_dashC_redirect_captured_and_classified() -> str:
    """dev-env#576: `git -C <path>` / `git --git-dir=<path>` /
    `git --work-tree=<path>` are no longer skipped. The invocation is now
    recognized as mutating (classify() returns the segment) AND its target dir
    is captured by _segment_redirect_dirs() so main() can resolve it and apply
    the canonical-root check to the *target*. `--git-dir=<path>/.git` is
    normalized to its parent (the worktree top). The pre-#576 behavior asserted
    the opposite (classify() returned None for these) — that was the gap.
    """
    redirect_cases = [
        ("git -C C:/Users/brown/Git/dev-env checkout -b foo", "C:/Users/brown/Git/dev-env"),
        ("git --git-dir=C:/Users/brown/Git/dev-env/.git checkout -b foo", "C:/Users/brown/Git/dev-env"),
        ("git --work-tree=C:/Users/brown/Git/dev-env commit -m x", "C:/Users/brown/Git/dev-env"),
    ]
    for cmd, expected_dir in redirect_cases:
        matched = cmg.classify(cmd)
        if matched is None:
            raise AssertionError(f"a -C/--git-dir/--work-tree mutating invocation must classify, got None for {cmd!r}")
        dirs = cmg._segment_redirect_dirs(cmd)
        if expected_dir not in dirs:
            raise AssertionError(f"expected redirect dir {expected_dir!r} captured for {cmd!r}, got {dirs!r}")

    # A DIFFERENT, non-redirected mutating segment after a read-only -C segment
    # is still caught (find_mutating_segments returns the first mutating one).
    mixed = "git -C C:/Users/brown/Git/dev-env status && git checkout -b foo"
    matched = cmg.classify(mixed)
    if matched is None or "checkout" not in matched:
        raise AssertionError(
            f"a non-redirected mutating segment after a read-only -C segment must still be caught, got {matched!r}"
        )
    return "git -C/--git-dir/--work-tree redirect target captured + classified (dev-env#576)"


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


def test_parse_git_prefix_captures_redirect_dirs() -> str:
    """dev-env#576: `_parse_git_prefix` walks git-level options — the existing
    `-c <v>` / `--no-optional-locks` set PLUS the new redirect flags — leaving
    the real verb at remaining[0] and capturing every -C/--git-dir/--work-tree
    target dir (both `=` and space forms; `--git-dir=.../.git` -> its parent).
    """
    cases = [
        (["-C", "/repo", "checkout", "-b", "x"], ["/repo"], ["checkout", "-b", "x"]),
        (["--git-dir=/repo/.git", "status"], ["/repo"], ["status"]),
        (["--git-dir", "/repo/.git", "status"], ["/repo"], ["status"]),
        (["--work-tree=/wt", "commit"], ["/wt"], ["commit"]),
        (["--work-tree", "/wt", "commit"], ["/wt"], ["commit"]),
        (["-C", "/a", "--work-tree=/b", "checkout"], ["/a", "/b"], ["checkout"]),
        (["-c", "gc.auto=0", "stash", "pop"], [], ["stash", "pop"]),
        (["--no-optional-locks", "stash", "apply"], [], ["stash", "apply"]),
        (["commit", "-m", "x"], [], ["commit", "-m", "x"]),
    ]
    for tokens, want_dirs, want_rest in cases:
        dirs, rest = cmg._parse_git_prefix(list(tokens))
        if dirs != want_dirs or rest != want_rest:
            raise AssertionError(
                f"_parse_git_prefix({tokens!r}) = ({dirs!r}, {rest!r}), want ({want_dirs!r}, {want_rest!r})"
            )
    return f"{len(cases)} _parse_git_prefix cases capture redirect dirs + expose the real verb"


def test_work_tree_and_git_dir_redirect_classified_as_mutating() -> str:
    """dev-env#576: before this fix `git --work-tree=<path> commit` misclassified
    as NON-mutating (the leading flag was mistaken for the verb, silently falling
    through to the default `False`). The redirect flags are now consumed as
    git-level options so the real verb (`commit`/`checkout`) is classified.
    """
    cases = [
        "git --work-tree=C:/Users/brown/Git/dev-env commit -m x",
        "git --work-tree C:/Users/brown/Git/dev-env checkout -b foo",
        "git -C C:/Users/brown/Git/dev-env checkout -b foo",
        "git --git-dir=C:/Users/brown/Git/dev-env/.git reset --hard",
    ]
    for cmd in cases:
        if not cmg.is_mutating_segment(cmd):
            raise AssertionError(f"redirect-flagged mutating invocation should classify as mutating: {cmd!r}")
    # A redirect-flagged READ-ONLY verb stays non-mutating.
    if cmg.is_mutating_segment("git -C C:/Users/brown/Git/dev-env status"):
        raise AssertionError("git -C <path> status must stay non-mutating")
    return f"{len(cases)} redirect-flagged mutating invocations classified (dev-env#576 --work-tree fix)"


def test_segment_redirect_dirs_first_line_only() -> str:
    """`_segment_redirect_dirs` reads only the segment's first physical line
    (via `_git_rest_tokens`/`_first_line`), so a heredoc body that merely
    *mentions* `git -C /tmp` as prose cannot inject a spurious redirect target
    into a real (ambient) commit — the same anchoring guarantee is_mutating_segment
    relies on.
    """
    if cmg._segment_redirect_dirs("git -C /repo checkout -b x") != ["/repo"]:
        raise AssertionError("expected ['/repo'] captured from a first-line -C redirect")
    heredoc_commit = 'git commit -m "$(cat <<\'EOF\'\nsee git -C /tmp status for context\nEOF\n)"'
    dirs = cmg._segment_redirect_dirs(heredoc_commit)
    if dirs:
        raise AssertionError(f"a heredoc-body -C mention must not be captured as a redirect, got {dirs!r}")
    return "_segment_redirect_dirs captures first-line redirects only, ignores heredoc-body mentions"


def test_is_allowlisted_root_journal_carveout() -> str:
    """dev-env#576 carve-out: a redirect target whose resolved toplevel EXACTLY
    matches the configured journal path (default: the real
    C:/Users/brown/Git/engineering-journal, separator- and case-normalized) is
    exempt, while every other repo — INCLUDING one that merely shares the same
    basename at a different path — is not (dev-env#576/PR#584 review finding:
    the original implementation matched on basename alone, which would have
    wrongly exempted the last case below).
    """
    exempt = [
        "C:/Users/brown/Git/engineering-journal",
        "C:\\Users\\brown\\Git\\engineering-journal",
        "C:/Users/brown/Git/engineering-journal/",
    ]
    for root in exempt:
        if not cmg._is_allowlisted_root(root):
            raise AssertionError(f"expected {root!r} to be carve-out-exempt")
    not_exempt = (
        "C:/Users/brown/Git/career-playbook",
        "C:/Users/brown/Git/dev-env",
        "C:/x/engineering-journal-notes",
        "C:/other/place/engineering-journal",  # same basename, wrong path -- must NOT match
    )
    for root in not_exempt:
        if cmg._is_allowlisted_root(root):
            raise AssertionError(f"{root!r} must NOT be carve-out-exempt")
    return "engineering-journal redirect target is carve-out-exempt by exact path; same-basename-elsewhere is not"


def test_tokenize_quoted_redirect_path_with_space() -> str:
    """dev-env#576/PR#584 review finding: a plain whitespace `.split()` shatters
    a quoted redirect value containing a space into multiple bogus tokens,
    silently defeating the block for any Windows path under a
    space-containing directory (e.g. "C:/Program Files/..."). `_tokenize` must
    capture it as one token via a quote-aware split.
    """
    cmd = 'git -C "C:/Program Files/some-canonical" checkout -b foo'
    dirs = cmg._segment_redirect_dirs(cmd)
    if dirs != ["C:/Program Files/some-canonical"]:
        raise AssertionError(f"expected the quoted space-bearing path captured whole, got {dirs!r}")
    if not cmg.is_mutating_segment(cmd):
        raise AssertionError("quoted -C target with a mutating verb must still classify as mutating")
    return "_tokenize captures a quoted, space-bearing redirect path as one token (dev-env#576/PR#584)"


def test_tokenize_falls_back_on_unbalanced_quote() -> str:
    """`_tokenize` must fall back to a plain whitespace split (not raise) when
    shlex hits unbalanced quoting -- the exact shape `_first_line()` produces
    when it truncates a real multi-line heredoc/command-substitution segment
    mid-quote. The real segment is `git commit -m "$(cat <<'EOF'\\n...\\nEOF\\n)"`
    (see test_commit_with_heredoc_command_sub_message_still_classified);
    `_first_line()` keeps only `git commit -m "$(cat <<'EOF'` -- one opening
    double-quote with no closing partner on this truncated line (the `'EOF'`
    single-quote pair IS balanced on its own). This is what keeps the existing
    heredoc-still-classifies-as-mutating tests passing rather than crashing
    the hook on a ValueError.
    """
    truncated = "commit -m \"$(cat <<'EOF'"
    tokens = cmg._tokenize(truncated)
    if tokens != truncated.split():
        raise AssertionError(f"expected fallback to plain .split() on unbalanced quote, got {tokens!r}")
    return "_tokenize falls back to plain split() on unbalanced quoting instead of raising"


def test_resolve_git_toplevel_failsopen_on_null_byte() -> str:
    """dev-env#576/PR#584 review finding: a redirect dir containing a null byte
    makes `subprocess.run`'s `Popen` raise `ValueError: embedded null
    character`, which was NOT in `_resolve_git_toplevel`'s except tuple and
    crashed the hook outright (verified end-to-end before this fix: exit 1
    with a traceback, instead of exit 0 fail-open). This is a newly-relevant
    case because dev-env#576 routes a command-string-derived value (a
    redirect target) into this function, not just the harness-provided cwd.
    """
    result = cmg._resolve_git_toplevel("C:/some/path\x00with/null")
    if result is not None:
        raise AssertionError(f"expected None (fail open) on a null-byte path, got {result!r}")
    return "_resolve_git_toplevel returns None (fail open) on a null-byte path rather than raising"


def test_memoized_toplevel_dedupes_across_callers() -> str:
    """dev-env#758: `_memoized_toplevel(path, cache)` must call
    `_resolve_git_toplevel` at most once per distinct path, no matter how many
    separate call sites ask for it with the same `cache` dict — this is the
    mechanism `main()` relies on to avoid resolving an identical `cwd` twice
    (once inside `_is_live_worktree()`'s liveness check, once again in the
    ambient branch) in the narrow case where cwd is worktree-shaped with a
    `.git` link present but git resolves it to a different root (not live).

    Also confirms a `None` result (git couldn't resolve the path) is itself
    memoized rather than retried — the pre-existing `_blockable_redirect_root`
    memoization already relied on this (a resolution failure is stable within
    one command), and `_memoized_toplevel` must preserve it now that both
    call sites share the one helper.
    """
    original_resolve = cmg._resolve_git_toplevel
    calls = {"n": 0}

    def _counting_resolve(path):
        calls["n"] += 1
        return None if path == "C:/unresolvable" else f"RESOLVED:{path}"

    try:
        cmg._resolve_git_toplevel = _counting_resolve

        cache = {}
        first = cmg._memoized_toplevel("C:/some/cwd", cache)
        second = cmg._memoized_toplevel("C:/some/cwd", cache)
        if first != "RESOLVED:C:/some/cwd" or second != first:
            raise AssertionError(f"expected consistent resolved value, got {first!r} then {second!r}")
        if calls["n"] != 1:
            raise AssertionError(f"expected exactly 1 resolver call for a repeated path, got {calls['n']}")

        none_result_first = cmg._memoized_toplevel("C:/unresolvable", cache)
        none_result_second = cmg._memoized_toplevel("C:/unresolvable", cache)
        if none_result_first is not None or none_result_second is not None:
            raise AssertionError("expected None to be memoized, not just non-None results")
        if calls["n"] != 2:
            raise AssertionError(f"expected exactly 1 additional resolver call for the None-path, got {calls['n'] - 1}")

        third = cmg._memoized_toplevel("C:/another/cwd", cache)
        if third != "RESOLVED:C:/another/cwd" or calls["n"] != 3:
            raise AssertionError(f"expected a distinct path to still resolve fresh, got {third!r}, calls={calls['n']}")
    finally:
        cmg._resolve_git_toplevel = original_resolve

    return "_memoized_toplevel resolves each distinct path at most once, including a memoized None (dev-env#758)"


# dev-env#749: fixtures for the _worktree_root_from_cwd / _is_live_worktree tests below.
_WORKTREE_ROOT_FIXTURE = "C:/Users/brown/Git/dev-env/.claude/worktrees/some-worktree-name"
_CANONICAL_FIXTURE = "C:/Users/brown/Git/dev-env"


def test_worktree_root_from_cwd_matches_and_extracts() -> str:
    """dev-env#749: `_worktree_root_from_cwd` extracts the worktree-root PREFIX
    of a cwd anchored inside `.claude/worktrees/<name>`, including when cwd is
    a subdirectory nested deeper inside the worktree — and returns None for a
    cwd that isn't inside one at all (a canonical root, or an unrelated path).
    """
    cases = [
        (_WORKTREE_ROOT_FIXTURE, _WORKTREE_ROOT_FIXTURE, "bare worktree root"),
        (f"{_WORKTREE_ROOT_FIXTURE}/some/nested/path", _WORKTREE_ROOT_FIXTURE, "nested subdirectory cwd"),
        (
            "C:\\Users\\brown\\Git\\dev-env\\.CLAUDE\\WORKTREES\\Some-Worktree-Name",
            "C:\\Users\\brown\\Git\\dev-env\\.CLAUDE\\WORKTREES\\Some-Worktree-Name",
            "mixed-separator, uppercase variant",
        ),
    ]
    for cwd, expected_root, label in cases:
        got = cmg._worktree_root_from_cwd(cwd)
        if got != expected_root:
            raise AssertionError(f"{label}: expected {expected_root!r}, got {got!r} for cwd {cwd!r}")

    non_worktree = (_CANONICAL_FIXTURE, "C:/Users/brown/Git/unrelated-repo")
    for cwd in non_worktree:
        got = cmg._worktree_root_from_cwd(cwd)
        if got is not None:
            raise AssertionError(f"expected None for non-worktree cwd {cwd!r}, got {got!r}")
    return f"{len(cases)} worktree-shaped extractions + {len(non_worktree)} non-worktree None cases correct"


def test_is_live_worktree_decision_table() -> str:
    """Mirrors `test_worktree_path_check.py`'s `test_worktree_is_live_decision_table`
    (ADR-024's dev-env#328 addendum) for this file's copy of the same liveness
    logic (dev-env#749).
    """
    wt = _WORKTREE_ROOT_FIXTURE
    canon = _CANONICAL_FIXTURE
    cases = [
        # (label, git_link_present, git_toplevel_return, expected_live)
        ("live: .git present, toplevel == worktree_root", True, wt, True),
        ("orphan: .git link missing", False, wt, False),
        ("orphan: git resolves up to canonical root", True, canon, False),
        ("orphan: git returns unrelated path", True, "C:/somewhere/else", False),
        (".git present but git exec failed (None) -> don't false-block", True, None, True),
        ("live: toplevel differs only by case/sep", True, wt.replace("/", "\\").upper(), True),
    ]
    for label, link_present, top, expected in cases:
        live = cmg._is_live_worktree(
            wt,
            wt,
            path_exists=lambda _p, _present=link_present: _present,
            git_toplevel=lambda _c, _top=top: _top,
        )
        if live != expected:
            raise AssertionError(f"{label}: _is_live_worktree = {live}, expected {expected}")
    return f"{len(cases)} liveness combinations classified correctly"


def test_is_live_worktree_short_circuits_before_git() -> str:
    """When the .git link is missing, git_toplevel must not even be consulted
    — mirrors test_worktree_path_check.py's identical short-circuit test.
    """
    called = {"n": 0}

    def _spy(_cwd):
        called["n"] += 1
        return _WORKTREE_ROOT_FIXTURE

    live = cmg._is_live_worktree(
        _WORKTREE_ROOT_FIXTURE, _WORKTREE_ROOT_FIXTURE, path_exists=lambda _p: False, git_toplevel=_spy
    )
    if live is not False:
        raise AssertionError("missing .git link should yield not-live")
    if called["n"] != 0:
        raise AssertionError("git_toplevel was called despite missing .git link (no short-circuit)")
    return "missing .git link blocks without spawning git"


def test_blockable_ambient_root_guards_against_worktree_shaped_resolution() -> str:
    """Review finding, dev-env#749: mirrors `_blockable_redirect_root`'s
    identical `not _WORKTREE_RE.search(root)` guard. The ambient branch's
    original invariant ("cwd already failed the worktree pattern, so any
    resolved toplevel IS canonical") no longer strictly holds once
    cwd_is_worktree can be False for a cwd that IS worktree-shaped but wasn't
    confirmed live — `_blockable_ambient_root` closes that latent gap.
    """
    if cmg._blockable_ambient_root(None) is not None:
        raise AssertionError("None input should stay None")
    if cmg._blockable_ambient_root(_CANONICAL_FIXTURE) != _CANONICAL_FIXTURE:
        raise AssertionError("a canonical (non-worktree-shaped) root should pass through unchanged")
    if cmg._blockable_ambient_root(_WORKTREE_ROOT_FIXTURE) is not None:
        raise AssertionError("a worktree-shaped resolved root must not be treated as blockable")
    return "_blockable_ambient_root guards against a worktree-shaped resolved root (dev-env#749 review finding)"


def main_unit() -> list:
    return [
        ("mutating verbs classified as mutating", test_mutating_verbs_classified_as_mutating),
        ("read-only commands classified as safe", test_readonly_commands_classified_as_safe),
        ("gh pr merge -d/--delete-branch classified as mutating (dev-env#558)", test_gh_pr_merge_delete_branch_classified_as_mutating),
        ("gh pr merge without delete-branch classified as safe (dev-env#558)", test_gh_pr_merge_without_delete_branch_classified_as_safe),
        ("classify() flags gh pr merge --delete-branch (dev-env#558)", test_classify_flags_gh_pr_merge_delete_branch),
        ("classify() allows bare gh pr merge (dev-env#558)", test_classify_allows_bare_gh_pr_merge),
        ("gh pr merge -d override bypasses (dev-env#558)", test_gh_pr_merge_override_bypasses),
        ("gh pr merge -d mention in heredoc does not trigger (dev-env#558)", test_gh_pr_merge_delete_branch_mention_in_heredoc_does_not_trigger),
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
        ("commit with heredoc command-sub message still classified (dev-env#511 follow-up)", test_commit_with_heredoc_command_sub_message_still_classified),
        ("bare heredoc commit message still classified (dev-env#511 follow-up)", test_bare_heredoc_commit_message_still_classified),
        ("redirect mention in heredoc body does not skip real mutating command (dev-env#511 follow-up)", test_redirect_mention_in_heredoc_body_does_not_skip_real_mutating_command),
        ("DOTALL alternative would have been wrong (dev-env#511 follow-up)", test_dotall_alternative_would_have_been_wrong),
        ("env-prefixed mutating command still classified", test_env_prefixed_mutating_command_still_classified),
        ("cd redirect takes whole command out of scope", test_cd_redirect_takes_whole_command_out_of_scope),
        ("-C/--git-dir/--work-tree redirect captured + classified (dev-env#576)", test_dashC_redirect_captured_and_classified),
        ("flag-prefixed stash pop/apply classified as mutating", test_flag_prefixed_stash_pop_apply_classified_as_mutating),
        ("env-prefixed cd recognized as redirect", test_env_prefixed_cd_recognized_as_redirect),
        ("override anchored prefix bypasses", test_override_anchored_prefix_bypasses),
        ("override mention-only does not bypass", test_override_mention_only_does_not_bypass),
        ("_resolve_git_toplevel fails open on timeout/OSError", test_resolve_git_toplevel_failsopen_on_timeout_and_oserror),
        ("_parse_git_prefix captures redirect dirs (dev-env#576)", test_parse_git_prefix_captures_redirect_dirs),
        ("--work-tree/--git-dir/-C redirect classified as mutating (dev-env#576)", test_work_tree_and_git_dir_redirect_classified_as_mutating),
        ("_segment_redirect_dirs first-line only (dev-env#576)", test_segment_redirect_dirs_first_line_only),
        ("_is_allowlisted_root journal carve-out (dev-env#576)", test_is_allowlisted_root_journal_carveout),
        ("_tokenize captures quoted space-bearing redirect path (dev-env#576/PR#584)", test_tokenize_quoted_redirect_path_with_space),
        ("_tokenize falls back on unbalanced quote (dev-env#576/PR#584)", test_tokenize_falls_back_on_unbalanced_quote),
        ("_resolve_git_toplevel fails open on null byte (dev-env#576/PR#584)", test_resolve_git_toplevel_failsopen_on_null_byte),
        ("_memoized_toplevel dedupes across callers, incl. a memoized None (dev-env#758)", test_memoized_toplevel_dedupes_across_callers),
        ("_worktree_root_from_cwd matches and extracts (dev-env#749)", test_worktree_root_from_cwd_matches_and_extracts),
        ("_is_live_worktree decision table (dev-env#749)", test_is_live_worktree_decision_table),
        ("_is_live_worktree short-circuits before git (dev-env#749)", test_is_live_worktree_short_circuits_before_git),
        ("_blockable_ambient_root guards against worktree-shaped resolution (dev-env#749 review finding)", test_blockable_ambient_root_guards_against_worktree_shaped_resolution),
    ]


# --------------------------------------------------------------------------
# Layer 2: end-to-end subprocess tests
# --------------------------------------------------------------------------


def _run_hook(payload: dict, env_overrides: dict = None) -> subprocess.CompletedProcess:
    """Run the real hook over stdin. `env_overrides`, when given, merges onto
    a copy of the current environment (rather than replacing it wholesale) so
    a test can redirect e.g. CANONICAL_MUTATE_GUARD_JOURNAL_PATH at a
    disposable temp dir without losing PATH/other inherited variables the
    subprocess needs (to find `git`, etc.).
    """
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


def test_main_blocks_commit_with_heredoc_message_from_canonical_root() -> str:
    """End-to-end proof of the /review-caught regression fix: before
    `_first_line()` was added, a real `git commit -m "$(cat <<'EOF' ...)"`
    (this repo's own documented commit-message idiom) would have been
    wrongly ALLOWED (exit 0) — `_GIT_INVOCATION_RE`'s `$`-anchored,
    non-DOTALL match failed outright on the multi-line segment
    `split_top_level` now produces for it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": "git commit -m \"$(cat <<'EOF'\nsubject line\nEOF\n)\""
            },
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
    return "git commit -m \"$(cat <<'EOF'...)\" is blocked end-to-end (dev-env#511 follow-up)"


def test_main_blocks_gh_pr_merge_delete_branch_from_canonical_root() -> str:
    """dev-env#558 end-to-end: `gh pr merge -d`/`--delete-branch` from a
    canonical (non-worktree) checkout is blocked exactly like a `git
    checkout -b` — same severity, same reason format, same override/worktree
    machinery, reached through is_mutating_gh_segment() instead of
    is_mutating_segment().
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        for cmd in ("gh pr merge --delete-branch", "gh pr merge -d"):
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
                "cwd": str(repo),
            }
            proc = _run_hook(payload)
            if proc.returncode != 2:
                raise AssertionError(
                    f"expected exit 2 (block) for {cmd!r}, got {proc.returncode}. "
                    f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
                )
            if proc.stdout.strip():
                raise AssertionError(f"expected empty stdout for {cmd!r}, got {proc.stdout!r}")
            try:
                reason = json.loads(proc.stderr).get("reason", "")
            except json.JSONDecodeError:
                raise AssertionError(f"stderr was not JSON for {cmd!r}: {proc.stderr!r}")
            if "canonical-mutate-guard" not in reason or cmd not in reason:
                raise AssertionError(f"block reason missing expected markers for {cmd!r}: {reason!r}")
    return "gh pr merge --delete-branch and gh pr merge -d both blocked (exit 2) from a canonical root (dev-env#558)"


def test_main_allows_bare_gh_pr_merge_from_canonical_root() -> str:
    """A bare `gh pr merge` (no delete-branch flag) merges only via the
    GitHub API and touches no local state — must stay allowed even from a
    canonical (non-worktree) root.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        for cmd in ("gh pr merge", "gh pr merge --squash"):
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": cmd},
                "cwd": str(repo),
            }
            proc = _run_hook(payload)
            if proc.returncode != 0:
                raise AssertionError(
                    f"expected exit 0 (allow) for {cmd!r}, got {proc.returncode}. stderr={proc.stderr!r}"
                )
    return "bare gh pr merge and gh pr merge --squash allowed (exit 0) from a canonical root (dev-env#558)"


def test_main_allows_gh_pr_merge_delete_branch_from_worktree_cwd() -> str:
    """cwd matching the worktree pattern is out of scope for every mutating
    command this hook recognizes, gh-based or git-based alike — ADR-024's
    hook covers the worktree surface.
    """
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --delete-branch"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (out of scope) from worktree cwd, got {proc.returncode}. "
                f"stdout={proc.stdout!r}"
            )
    return "gh pr merge --delete-branch from a worktree-pattern cwd allowed (out of scope, exit 0, dev-env#558)"


def test_main_gh_pr_merge_delete_branch_override_bypasses() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "canonical-repo"
        repo.mkdir()
        _init_throwaway_repo(repo)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ALLOW_CANONICAL_MUTATE=1 gh pr merge -d"},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (override applied), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "ALLOW_CANONICAL_MUTATE=1 override bypasses a gh pr merge -d block (exit 0, dev-env#558)"


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
    """An *ambient* (no -C/--git-dir/--work-tree redirect) mutating command from
    a worktree-pattern cwd is allowed — it targets the worktree itself, which is
    fine, and ADR-024's hook covers that surface. Since dev-env#576 this is no
    longer "entirely out of scope": a worktree command that redirects a mutating
    verb at a *canonical* checkout IS evaluated (see
    test_main_blocks_redirect_into_canonical_from_worktree_cwd below).
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
    return "ambient (no-redirect) mutating command from a worktree-pattern cwd allowed (exit 0)"


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


def test_main_blocks_redirect_into_canonical_from_worktree_cwd() -> str:
    """dev-env#576 core case: a mutating verb redirected at a *canonical*
    checkout via `git -C <canonical>` — issued from a WORKTREE cwd (the exact
    incident shape: `git -C <other-repo> checkout` from a project worktree) — is
    now BLOCKED (exit 2), where the pre-#576 worktree short-circuit allowed it.
    The reason names the resolved TARGET root, not cwd.

    Uses `.as_posix()` for the path embedded in the COMMAND TEXT (not the `cwd`
    JSON field, which isn't tokenized) — matching this codebase's own universal
    forward-slash convention for -C targets. A raw Windows `str(Path)` here
    would embed backslashes that `_tokenize()`'s `shlex.split(posix=True)`
    treats as escape characters outside quotes, corrupting the path (review
    finding on dev-env#576/PR#584 while writing this very test file).
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "canonical-target"
        target.mkdir()
        _init_throwaway_repo(target)
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {target.as_posix()} checkout -b some-branch"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (block redirect into canonical), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        if proc.stdout.strip():
            raise AssertionError(f"expected empty stdout (reason must go to stderr), got {proc.stdout!r}")
        reason = json.loads(proc.stderr).get("reason", "")
        if "canonical-target" not in reason:
            raise AssertionError(f"block reason must name the resolved target root, got {reason!r}")
    return "git -C <canonical> mutating verb from a worktree cwd blocked, target named (dev-env#576)"


def test_main_allows_redirect_into_journal_carveout_from_worktree_cwd() -> str:
    """dev-env#576 carve-out: a `git -C <journal>` redirect from a worktree cwd
    stays ALLOWED (exit 0) — the documented stub workflow mutates the shared
    journal canonical checkout this way on every PR open/merge, and blocking it
    would force ALLOW_CANONICAL_MUTATE=1 onto an automated path.

    Matched by exact resolved-toplevel path (dev-env#576/PR#584 review finding
    hardened this from a basename-only match), which is overridden here via
    CANONICAL_MUTATE_GUARD_JOURNAL_PATH to point at a disposable temp repo
    rather than the developer's real engineering-journal checkout — this test
    must never touch that real checkout.
    """
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "engineering-journal"
        journal.mkdir()
        _init_throwaway_repo(journal)
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {journal.as_posix()} pull"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload, env_overrides={"CANONICAL_MUTATE_GUARD_JOURNAL_PATH": str(journal)})
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (journal carve-out), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "git -C <journal carve-out path> from a worktree cwd allowed (dev-env#576)"


def test_main_blocks_redirect_into_samename_repo_outside_carveout_path() -> str:
    """dev-env#576/PR#584 review finding, closed: a canonical checkout that
    merely happens to be NAMED `engineering-journal` at some OTHER path is NOT
    exempt — only the one path CANONICAL_MUTATE_GUARD_JOURNAL_PATH names is.
    Pre-fix, the carve-out matched on basename alone and would have wrongly
    exempted this too; this pins the hardened exact-path match end-to-end.
    """
    with tempfile.TemporaryDirectory() as tmp:
        lookalike = Path(tmp) / "engineering-journal"  # same basename, NOT the configured carve-out path
        lookalike.mkdir()
        _init_throwaway_repo(lookalike)
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {lookalike.as_posix()} checkout -b x"},
            "cwd": str(wt),
        }
        # Deliberately configure the carve-out path to somewhere else entirely
        # so `lookalike` (same basename, different path) is provably not it.
        other_path = Path(tmp) / "actual-carveout-target"
        proc = _run_hook(payload, env_overrides={"CANONICAL_MUTATE_GUARD_JOURNAL_PATH": str(other_path)})
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (same-basename repo at a different path must NOT be exempt), "
                f"got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "same-basename-but-wrong-path repo is correctly NOT carve-out-exempt (dev-env#576/PR#584)"


def test_main_allows_redirect_into_worktree_target() -> str:
    """A `git -C <target>` whose target resolves to a WORKTREE (not a canonical
    root) is allowed — the target is itself isolated, so no shared-checkout
    collision. Proves the block keys off the target's canonical-ness, not merely
    the presence of a redirect flag.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cwd_repo = Path(tmp) / "canonical-cwd"
        cwd_repo.mkdir()
        _init_throwaway_repo(cwd_repo)
        wt_target = Path(tmp) / ".claude" / "worktrees" / "wt-target"
        wt_target.mkdir(parents=True)
        _init_throwaway_repo(wt_target)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {wt_target.as_posix()} checkout -b x"},
            "cwd": str(cwd_repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (target is a worktree), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "git -C <worktree> mutating verb allowed (target is not a canonical root, dev-env#576)"


def test_main_blocks_redirect_into_other_canonical_from_canonical_cwd() -> str:
    """The `_REDIRECT_RE` backstop case: a session in canonical repo A that runs
    `git -C <canonical repo B> checkout` — the pre-#576 segment-skip let this
    through even from a non-worktree cwd. Now BLOCKED (exit 2).
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo_a = Path(tmp) / "canonical-a"
        repo_a.mkdir()
        _init_throwaway_repo(repo_a)
        repo_b = Path(tmp) / "canonical-b"
        repo_b.mkdir()
        _init_throwaway_repo(repo_b)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {repo_b.as_posix()} checkout -b x"},
            "cwd": str(repo_a),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (redirect into other canonical), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "git -C <other canonical> from a canonical cwd blocked (dev-env#576)"


def test_main_blocks_work_tree_redirect_into_canonical() -> str:
    """The previously-misclassified form: `git --work-tree=<canonical> commit`.
    Before dev-env#576 the leading flag was mistaken for the verb (classified
    non-mutating, never blocked). Now the flag is consumed, `commit` is the verb,
    the target resolves to a canonical root, and it is BLOCKED (exit 2).
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "canonical-wt-target"
        target.mkdir()
        _init_throwaway_repo(target)
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"git --work-tree={target.as_posix()} commit -m x"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (--work-tree into canonical), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "git --work-tree=<canonical> commit blocked (dev-env#576 --work-tree fix)"


def test_main_override_bypasses_redirect_block() -> str:
    """The `ALLOW_CANONICAL_MUTATE=1` override bypasses a redirect-into-canonical
    block exactly like an ambient block — a deliberate, visible human override.
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "canonical-target"
        target.mkdir()
        _init_throwaway_repo(target)
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"ALLOW_CANONICAL_MUTATE=1 git -C {target.as_posix()} checkout -b x"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (override applied), got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "ALLOW_CANONICAL_MUTATE=1 bypasses a -C-into-canonical block (dev-env#576)"


def test_main_blocks_quoted_space_bearing_redirect_target() -> str:
    """dev-env#576/PR#584 review finding, closed end-to-end: a `-C` target
    quoted because it contains a space (a common shape for a Windows path
    under a space-containing directory, e.g. "C:/Program Files/...") must
    still be recognized and blocked, not silently let through by a naive
    whitespace-based tokenizer.
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "canonical target with space"
        target.mkdir()
        _init_throwaway_repo(target)
        wt = Path(tmp) / ".claude" / "worktrees" / "some-worktree-name"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f'git -C "{target.as_posix()}" checkout -b some-branch'},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (quoted space-bearing -C target blocked), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
    return "quoted, space-bearing -C target blocked end-to-end (dev-env#576/PR#584)"


def test_main_blocks_relative_redirect_resolved_against_command_cwd() -> str:
    """dev-env#576/PR#584 review finding, closed end-to-end: a RELATIVE `-C`
    target must resolve against the command's OWN cwd (the payload's `cwd`),
    not the hook script's unrelated process cwd.

    `wt` sits 3 levels below `tmp` (tmp/.claude/worktrees/wt-rel);
    `canonical_sibling` sits directly under `tmp` — so "../../.." from `wt`
    reaches `tmp`, and appending the sibling's own name reaches the real
    canonical repo. Pre-fix, this resolved against the HOOK SCRIPT's own
    process cwd instead (verified: exit 0, allowed) — an unrelated directory
    that does not contain any such path, so the redirect silently missed the
    real collision this PR exists to prevent.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        canonical_sibling = tmp / "canonical-rel-target"
        canonical_sibling.mkdir()
        _init_throwaway_repo(canonical_sibling)
        wt = tmp / ".claude" / "worktrees" / "wt-rel"
        wt.mkdir(parents=True)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git -C ../../../canonical-rel-target checkout -b some-branch"},
            "cwd": str(wt),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (relative -C target resolved against command cwd, blocked), "
                f"got {proc.returncode}. stderr={proc.stderr!r}"
            )
    return "relative -C target resolved against the command's own cwd, blocked (dev-env#576/PR#584)"


def _init_repo_with_live_worktree(canonical: Path, worktree_path: Path) -> None:
    """Real canonical repo + a REAL, actually-registered `git worktree add` at
    worktree_path -- for the one test needing genuine worktree liveness, not
    just a worktree-shaped directory (dev-env#749). `git worktree add` needs
    an existing commit, which `_init_throwaway_repo` deliberately doesn't
    create (kept that way since 20+ existing tests in this file rely on its
    no-commit behavior). Mirrors the commit-then-worktree-add sequence already
    proven in `test_journal_canonical_guard.py`'s own `_init_throwaway_repo`.

    The worktree is created on a NEW branch (`-b`), not `main` itself: git
    only allows one worktree to hold a given branch at a time, and `main` is
    already checked out in the canonical after `branch -M main` above --
    confirmed by reproducing `fatal: 'main' is already checked out at ...`
    when this used `worktree add <path> main` directly. Git DOES auto-create
    the nested `.claude/worktrees/` intermediate directories on its own (also
    confirmed while debugging this); that was never the actual gap.
    """
    _init_throwaway_repo(canonical)
    subprocess.run(
        ["git", "-C", str(canonical), "commit", "--allow-empty", "-q", "-m", "init"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(canonical), "branch", "-M", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(canonical), "worktree", "add", "-q", "-b", "worktree-live-branch", str(worktree_path)],
        check=True, capture_output=True,
    )


def test_main_allows_ambient_mutating_command_from_live_worktree() -> str:
    """dev-env#749's explicit ask: "a real worktree still passes." Every other
    worktree-cwd test in this file uses a bare worktree-SHAPED directory that
    was never a real git repo at all -- none of them actually prove the hook
    still gives zero-friction treatment to a genuinely live, registered
    worktree once liveness is confirmed rather than assumed from shape.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical-repo"
        canonical.mkdir()
        worktree_path = canonical / ".claude" / "worktrees" / "real-live-worktree"
        _init_repo_with_live_worktree(canonical, worktree_path)
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout -b some-branch"},
            "cwd": str(worktree_path),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (live worktree stays zero-friction), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
    return "ambient mutating command from a REAL, registered live worktree allowed (exit 0, dev-env#749)"


def test_main_blocks_orphaned_worktree_shaped_cwd_nested_in_canonical() -> str:
    """dev-env#749 core case, reproducing the dev-env#630 live-confirmed
    signature exactly: an orphaned worktree-shaped directory NESTED inside a
    real canonical repo's own tree, with no `.git` of its own. Real git
    naturally walks up and resolves `git rev-parse --show-toplevel` to the
    canonical repo -- no mocking needed. Before this fix, `_WORKTREE_RE`
    matched the path's shape alone and exited 0 without ever asking git;
    after, liveness is confirmed false, cwd falls through to normal
    canonical-root resolution, and the mutating command is correctly blocked
    against the canonical root git actually resolves to.
    """
    with tempfile.TemporaryDirectory() as tmp:
        canonical = Path(tmp) / "canonical-repo"
        canonical.mkdir()
        _init_throwaway_repo(canonical)
        orphan = canonical / ".claude" / "worktrees" / "orphan-name"
        orphan.mkdir(parents=True)  # no .git of its own -- the orphan signature
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git checkout -b some-branch"},
            "cwd": str(orphan),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (orphaned worktree-shaped dir resolves to canonical, blocked), "
                f"got {proc.returncode}. stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        if proc.stdout.strip():
            raise AssertionError(f"expected empty stdout (reason must go to stderr), got {proc.stdout!r}")
        reason = json.loads(proc.stderr).get("reason", "")
        if "canonical-repo" not in reason:
            raise AssertionError(f"block reason must name the resolved canonical root, got {reason!r}")
    return "orphaned worktree-shaped cwd nested in a real canonical is blocked, canonical root named (dev-env#749)"


def main_e2e() -> list:
    return [
        ("main() blocks mutating command from canonical root", test_main_blocks_mutating_command_from_canonical_root),
        ("main() allows read-only command from canonical root", test_main_allows_readonly_command_from_canonical_root),
        ("main() allows quoted fake-verb git log (dev-env#511)", test_main_allows_quoted_fake_verb_git_log_from_canonical_root),
        ("main() blocks commit with heredoc message (dev-env#511 follow-up)", test_main_blocks_commit_with_heredoc_message_from_canonical_root),
        ("main() blocks gh pr merge --delete-branch/-d from canonical root (dev-env#558)", test_main_blocks_gh_pr_merge_delete_branch_from_canonical_root),
        ("main() allows bare gh pr merge from canonical root (dev-env#558)", test_main_allows_bare_gh_pr_merge_from_canonical_root),
        ("main() allows gh pr merge --delete-branch from worktree cwd (dev-env#558)", test_main_allows_gh_pr_merge_delete_branch_from_worktree_cwd),
        ("main() override token bypasses gh pr merge -d block (dev-env#558)", test_main_gh_pr_merge_delete_branch_override_bypasses),
        ("main() allows pull --ff-only, blocks bare pull", test_main_allows_pull_ff_only_blocks_bare_pull),
        ("main() allows any command from worktree cwd", test_main_allows_any_command_from_worktree_cwd),
        ("main() override token bypasses block", test_main_override_token_bypasses_block),
        ("main() blocks -C into canonical from worktree cwd (dev-env#576)", test_main_blocks_redirect_into_canonical_from_worktree_cwd),
        ("main() allows -C into journal carve-out from worktree cwd (dev-env#576)", test_main_allows_redirect_into_journal_carveout_from_worktree_cwd),
        ("main() blocks -C into same-basename repo outside carve-out path (dev-env#576/PR#584)", test_main_blocks_redirect_into_samename_repo_outside_carveout_path),
        ("main() allows -C into worktree target (dev-env#576)", test_main_allows_redirect_into_worktree_target),
        ("main() blocks -C into other canonical from canonical cwd (dev-env#576)", test_main_blocks_redirect_into_other_canonical_from_canonical_cwd),
        ("main() blocks --work-tree into canonical (dev-env#576)", test_main_blocks_work_tree_redirect_into_canonical),
        ("main() override bypasses redirect block (dev-env#576)", test_main_override_bypasses_redirect_block),
        ("main() blocks quoted space-bearing redirect target (dev-env#576/PR#584)", test_main_blocks_quoted_space_bearing_redirect_target),
        ("main() blocks relative redirect resolved against command cwd (dev-env#576/PR#584)", test_main_blocks_relative_redirect_resolved_against_command_cwd),
        ("main() allows ambient mutating command from a real live worktree (dev-env#749)", test_main_allows_ambient_mutating_command_from_live_worktree),
        ("main() blocks orphaned worktree-shaped cwd nested in canonical (dev-env#749)", test_main_blocks_orphaned_worktree_shaped_cwd_nested_in_canonical),
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
