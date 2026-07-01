#!/usr/bin/env python3
"""Unit tests for post-pr-merge-pull.py's merge-detection predicate.

`post-pr-merge-pull.py` fast-forwards the local `main` after a successful
`gh pr merge`. The "was this a successful merge?" decision is the pure
`is_successful_merge()` predicate (extracted in #380, mirroring
post-pr-merge-reclaim.py), exercised offline here. Before #380 the hook read the
legacy `output` field (always empty on the real payload), so the stdout/stderr
success-marker fallback was dead and only a clean exit-0 merge triggered the
pull; the predicate now receives output via the shared `read_command_output`.

dev-env#485 removed the `exit_code` parameter entirely: `exit_code == 0 OR
marker` fired on any exit-0 command matching "gh pr merge" as a substring,
including `gh pr merge --help`. The predicate now gates solely on the success
marker, matching post-pr-merge-project.py's `merge_succeeded()`.

`pull_command()` (dev-env#488) is the pure decision of which git invocation
fast-forwards local main: `git fetch origin main:main` fails ('refusing to fetch
into branch ... checked out') whenever main is the branch currently checked out
at the target path — always true for dev-env's own canonical, which must stay on
`main` per its symlink architecture — so a plain `pull --ff-only` is used there
instead; the feature-branch-checked-out case (issue #275) is unchanged.

The `pull_main` / `extract_repo` git calls are intentionally not tested (they
shell out and the repo avoids subprocess mocks).

Usage:
    py -3 claude/scripts/tests/test_post_pr_merge_pull.py

Exit 0 = all pass.
"""

import importlib.util
import sys
from pathlib import Path

# tests/ -> scripts/ -> claude/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "claude" / "scripts" / "post-pr-merge-pull.py"

# The script imports _winsubp and _hookio (siblings in scripts/); make resolvable.
sys.path.insert(0, str(SCRIPT.parent))

# Hyphenated filename — import by path rather than `import`.
_spec = importlib.util.spec_from_file_location("post_pr_merge_pull", SCRIPT)
assert _spec and _spec.loader, f"cannot load module spec from {SCRIPT}"
ppmp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ppmp)  # safe: main() is guarded by __main__
is_successful_merge = ppmp.is_successful_merge
extract_repo = ppmp.extract_repo
pull_command = ppmp.pull_command


def test_clean_merge_with_marker_pulls() -> str:
    # The success marker is what confirms a completed merge; the exit code is
    # no longer consulted at all (dev-env#485) — true whether it came from a
    # clean canonical-checkout exit or a worktree's non-zero cleanup failure
    # (issue #275).
    assert is_successful_merge(
        "gh pr merge 380 --squash --delete-branch",
        "Squashed and merged pull request #380",
    )
    return "'Squashed and merged' marker -> pull"


def test_non_merge_command_ignored() -> str:
    assert not is_successful_merge("gh pr create --fill", "")
    assert not is_successful_merge("git push", "")
    return "non-merge commands -> no-op"


def test_failed_merge_no_marker_ignored() -> str:
    # A genuine merge failure (no success marker) must not pull.
    assert not is_successful_merge(
        "gh pr merge 380 --squash", "X Pull request #380 is not mergeable",
    )
    return "gh pr merge failed (no marker) -> no-op"


def test_help_invocation_no_marker_ignored() -> str:
    # dev-env#485 regression: `gh pr merge --help` exits 0 but prints no
    # success marker. The old exit_code==0 OR marker gate fired here; gating
    # on the marker alone fixes it.
    assert not is_successful_merge(
        "gh pr merge --help",
        "FLAGS\n      --admin   Use administrator privileges to merge a pull request",
    )
    return "gh pr merge --help (exit 0, no marker) -> no-op (dev-env#485)"


# ---------------------------------------------------------------------------
# extract_repo — pure-string resolution paths (dev-env#446 / ADR-067)
#
# The git-remote subprocess fallback is not tested (repo convention: no mocks).
# The --repo flag path is already exercised implicitly by is_successful_merge
# tests; these cover the new URL-extraction path added in ADR-067.
# ---------------------------------------------------------------------------

def test_extract_repo_from_url_in_command() -> str:
    repo = extract_repo(
        "gh pr merge https://github.com/brownm09/dev-env/pull/443 --squash --delete-branch",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"got {repo!r}"
    return "GitHub PR URL in command -> correct owner/repo regardless of cwd"


def test_extract_repo_from_url_other_repo() -> str:
    repo = extract_repo(
        "gh pr merge https://github.com/brownm09/lifting-logbook/pull/99 --squash",
        "/Git/dev-env",
    )
    assert repo == "brownm09/lifting-logbook", f"got {repo!r}"
    return "GitHub PR URL for a different repo -> that repo's slug"


def test_extract_repo_repo_flag_takes_precedence() -> str:
    # --repo flag wins over any URL in the command string (first check in order).
    repo = extract_repo(
        "gh pr merge --repo brownm09/dev-env https://github.com/brownm09/lifting-logbook/pull/1",
        "/Git/lifting-logbook",
    )
    assert repo == "brownm09/dev-env", f"--repo flag should win: got {repo!r}"
    return "--repo flag takes precedence over GitHub URL in command"


def test_pull_command_on_main_uses_ff_only_pull() -> str:
    cmd = pull_command("C:/Users/brown/Git/dev-env", True)
    assert cmd == ["git", "-C", "C:/Users/brown/Git/dev-env", "pull", "--ff-only", "origin", "main"], cmd
    return "canonical on main -> plain ff-only pull ('git fetch origin main:main' would fail: refusing to fetch into branch ... checked out)"


def test_pull_command_off_main_uses_fetch_into_ref() -> str:
    cmd = pull_command("C:/Users/brown/Git/lifting-logbook", False)
    assert cmd == ["git", "-C", "C:/Users/brown/Git/lifting-logbook", "fetch", "origin", "main:main"], cmd
    return "canonical on a feature branch (or worktree squatting main) -> fetch-into-ref, unchanged (issue #275)"


def main() -> int:
    tests = [
        ("merge marker present -> pulls", test_clean_merge_with_marker_pulls),
        ("non-merge command ignored", test_non_merge_command_ignored),
        ("failed merge with no marker ignored", test_failed_merge_no_marker_ignored),
        ("gh pr merge --help (no marker) ignored (dev-env#485)", test_help_invocation_no_marker_ignored),
        ("extract_repo: GitHub URL in command -> owner/repo", test_extract_repo_from_url_in_command),
        ("extract_repo: URL for different repo", test_extract_repo_from_url_other_repo),
        ("extract_repo: --repo flag beats URL", test_extract_repo_repo_flag_takes_precedence),
        ("pull_command: canonical on main -> ff-only pull", test_pull_command_on_main_uses_ff_only_pull),
        ("pull_command: canonical off main -> fetch-into-ref", test_pull_command_off_main_uses_fetch_into_ref),
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
