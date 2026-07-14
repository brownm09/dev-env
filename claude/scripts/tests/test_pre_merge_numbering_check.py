#!/usr/bin/env python3
"""Tests for pre-merge-numbering-check.py.

Two layers:

  1. Pure-helper tests (offline, no disk/network/subprocess/git) for
     extract_section, extract_testing_numbers, extract_adr_numbers,
     is_dev_env_repo, find_new_collisions, find_gaps, find_new_gaps,
     format_block_message, and is_pr_merge_command. See dev-env issue #516.
  2. End-to-end main() tests via subprocess against a real throwaway git
     repo (mirroring test_canonical_mutate_guard.py's Layer 2 pattern) --
     unlike this hook's pre-merge-message-check.py sibling, main() here
     performs real git orchestration (fetch/merge-base/show) worth covering
     beyond the pure helpers it's built from.

Cases pinned:
- extract_section: heading found mid-file, heading absent, heading with no
  following ## (runs to end of file), section immediately followed by
  another heading (empty body).
- extract_testing_numbers: a realistic multi-item CLAUDE.md-shaped Testing
  section (with an indented code block inside one item, which must NOT be
  mistaken for a sibling item); a numbered list outside the Testing section
  is out of scope and ignored.
- extract_adr_numbers: realistic INDEX.md-shaped table rows; the header row
  and the `|---|` separator row are not table entries and must be skipped.
- is_dev_env_repo: https and ssh origin URLs for dev-env; a different repo
  (lifting-logbook); empty/None input.
- find_new_collisions: a genuinely new number that collides with main; an
  edited *existing* item (present at the merge-base) is never flagged even
  when its text differs between branch and main; no items in common -> no
  collisions; all-empty inputs.
- find_gaps: contiguous set -> no gaps; one gap; empty input; single element.
- find_new_gaps: a gap already present in main_numbers alone is suppressed
  (no re-nagging on a long-standing hole); a gap this branch's own new
  numbers create IS surfaced; no gaps at all.
- format_block_message: the rendered message names the file label, the
  colliding number, and both colliding lines.
- is_pr_merge_command: a bare top-level invocation; a `cd <repo> &&`-chained
  invocation; a `gh pr merge` mentioned only inside a heredoc body (must NOT
  match -- dev-env#499); an unrelated command (must NOT match).
- is_merge_help_only composition (dev-env#557): main() adds `if
  is_merge_help_only(command): sys.exit(0)` immediately after its existing
  `if not is_pr_merge_command(command): sys.exit(0)` gate, so a `gh pr merge
  --help` command is never evaluated for -- or blocked on -- an unrelated
  numbering-collision state. Pins that a --help command still passes the
  upstream is_pr_merge_command gate (so the new guard is actually reached),
  and that a genuine merge command is unaffected by the new guard.
  `is_merge_help_only` itself is exhaustively tested in `test_hookio.py`.
- main() end-to-end: not-dev-env-repo -> exit 0 no-op; a non-merge command
  -> exit 0 no-op; a genuine cross-branch collision -> exit 2 with the
  block message on stderr and empty stdout.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pre-merge-numbering-check.py")
SCRIPTS_DIR = os.path.dirname(_SCRIPT)
# The script imports _winsubp and _hookio (siblings in scripts/); make them resolvable.
sys.path.insert(0, SCRIPTS_DIR)
spec = importlib.util.spec_from_file_location("pre_merge_numbering_check", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

extract_section = mod.extract_section
extract_testing_numbers = mod.extract_testing_numbers
extract_adr_numbers = mod.extract_adr_numbers
is_dev_env_repo = mod.is_dev_env_repo
find_new_collisions = mod.find_new_collisions
find_gaps = mod.find_gaps
find_new_gaps = mod.find_new_gaps
format_block_message = mod.format_block_message
is_pr_merge_command = mod.is_pr_merge_command

# is_merge_help_only lives in _hookio (a sibling); SCRIPTS_DIR is already on
# sys.path via the insert above.
from _hookio import is_merge_help_only  # noqa: E402


# ---------------------------------------------------------------------------
# extract_section
# ---------------------------------------------------------------------------

def test_extract_section_mid_file():
    text = "# Title\n\n## Before\nignored\n\n## Testing\nline one\nline two\n\n## Observability\nignored\n"
    assert extract_section(text, "Testing") == "line one\nline two\n"

def test_extract_section_heading_absent():
    text = "# Title\n\n## Before\nignored\n"
    assert extract_section(text, "Testing") == ""

def test_extract_section_runs_to_end_of_file():
    text = "## Testing\nline one\nline two"
    assert extract_section(text, "Testing") == "line one\nline two"

def test_extract_section_empty_body():
    text = "## Testing\n## Observability\nignored\n"
    assert extract_section(text, "Testing") == ""


# ---------------------------------------------------------------------------
# extract_testing_numbers
# ---------------------------------------------------------------------------

_CLAUDE_MD_SAMPLE = """# dev-env

## Testing

1. **Hook-script syntax check** — run from the repo root:

   ```bash
   py -3 -c "pass"
   ```

   Some indented prose that continues item 1 -- must not look like a new item.

2. **Second check** — required when changing foo.

## Observability

Not part of Testing.

1. This numbered line is outside the Testing section and must be ignored.
"""

def test_extract_testing_numbers_basic():
    items = extract_testing_numbers(_CLAUDE_MD_SAMPLE)
    assert set(items) == {1, 2}
    assert items[1].startswith("1. **Hook-script syntax check**")
    assert items[2].startswith("2. **Second check**")

def test_extract_testing_numbers_ignores_indented_and_out_of_section_lines():
    # The indented code-block/prose inside item 1, and the numbered line
    # after "## Observability", must not add phantom entries.
    items = extract_testing_numbers(_CLAUDE_MD_SAMPLE)
    assert len(items) == 2

def test_extract_testing_numbers_no_section():
    assert extract_testing_numbers("# Title\nno testing section here\n") == {}


# ---------------------------------------------------------------------------
# extract_adr_numbers
# ---------------------------------------------------------------------------

_INDEX_MD_SAMPLE = """# Architectural Decision Records

| # | Title | Date | Status | Tags |
|---|-------|------|--------|------|
| [001](001-per-session-stub-files.md) | Per-Session Stub Files | 2026-03-27 | Accepted | journal |
| [002](002-journal-compose-session-isolation.md) | Journal-Compose Isolation | 2026-04-04 | Accepted | journal |
"""

def test_extract_adr_numbers_basic():
    items = extract_adr_numbers(_INDEX_MD_SAMPLE)
    assert set(items) == {1, 2}
    assert "Per-Session Stub Files" in items[1]

def test_extract_adr_numbers_skips_header_and_separator():
    # Only the two real entries -- the header row and the |---| separator
    # row have no leading "| [", so neither is mistaken for an ADR entry.
    items = extract_adr_numbers(_INDEX_MD_SAMPLE)
    assert len(items) == 2


# ---------------------------------------------------------------------------
# is_dev_env_repo
# ---------------------------------------------------------------------------

def test_is_dev_env_repo_https():
    assert is_dev_env_repo("https://github.com/brownm09/dev-env.git") is True

def test_is_dev_env_repo_https_no_git_suffix():
    assert is_dev_env_repo("https://github.com/brownm09/dev-env") is True

def test_is_dev_env_repo_ssh():
    assert is_dev_env_repo("git@github.com:brownm09/dev-env.git") is True

def test_is_dev_env_repo_different_repo():
    assert is_dev_env_repo("https://github.com/brownm09/lifting-logbook.git") is False

def test_is_dev_env_repo_empty():
    assert is_dev_env_repo("") is False

def test_is_dev_env_repo_none():
    assert is_dev_env_repo(None) is False


# ---------------------------------------------------------------------------
# find_new_collisions
# ---------------------------------------------------------------------------

def test_find_new_collisions_genuine_collision():
    base = {1: "1. **A**", 2: "2. **B**"}
    branch = {1: "1. **A**", 2: "2. **B**", 3: "3. **Branch's new item**"}
    main = {1: "1. **A**", 2: "2. **B**", 3: "3. **Someone else's new item**", 4: "4. **Another**"}
    result = find_new_collisions(base, branch, main)
    assert result == {3: ("3. **Branch's new item**", "3. **Someone else's new item**")}

def test_find_new_collisions_no_collision():
    base = {1: "1. **A**"}
    branch = {1: "1. **A**", 2: "2. **Branch's item**"}
    main = {1: "1. **A**", 3: "3. **Someone else's item**"}
    assert find_new_collisions(base, branch, main) == {}

def test_find_new_collisions_edited_existing_item_not_flagged():
    # Item 1 existed at the merge-base; the branch reworded it. Even though
    # main's text for #1 also differs, this is an edit, not a fresh claim --
    # never a collision.
    base = {1: "1. **A**"}
    branch = {1: "1. **A, reworded by this branch**"}
    main = {1: "1. **A, reworded differently by another PR**"}
    assert find_new_collisions(base, branch, main) == {}

def test_find_new_collisions_empty_inputs():
    assert find_new_collisions({}, {}, {}) == {}


# ---------------------------------------------------------------------------
# find_gaps
# ---------------------------------------------------------------------------

def test_find_gaps_contiguous():
    assert find_gaps({1, 2, 3}) == []

def test_find_gaps_one_gap():
    assert find_gaps({1, 2, 4}) == [3]

def test_find_gaps_empty():
    assert find_gaps(set()) == []

def test_find_gaps_single_element():
    assert find_gaps({1}) == []


# ---------------------------------------------------------------------------
# find_new_gaps
# ---------------------------------------------------------------------------

def test_find_new_gaps_suppresses_pre_existing_gap():
    # main already has a gap at 4 (e.g. a retired item) -- not this branch's
    # doing, so it must not be reported even though it's still a gap after
    # the union.
    assert find_new_gaps({1, 2, 3, 5}, set()) == []

def test_find_new_gaps_surfaces_gap_the_branch_creates():
    # main has a pre-existing gap at 4; the branch's own new number (7) skips
    # 6, creating a second, fresh gap that IS worth surfacing.
    assert find_new_gaps({1, 2, 3, 5}, {7}) == [6]

def test_find_new_gaps_no_gaps():
    assert find_new_gaps({1, 2, 3}, {4}) == []


# ---------------------------------------------------------------------------
# is_pr_merge_command
# ---------------------------------------------------------------------------

def test_is_pr_merge_command_bare():
    assert is_pr_merge_command("gh pr merge 518 --squash --delete-branch") is True

def test_is_pr_merge_command_cd_chained():
    assert is_pr_merge_command("cd C:/Users/brown/Git/dev-env && gh pr merge --squash") is True

def test_is_pr_merge_command_heredoc_body_not_matched():
    # dev-env#499: a "gh pr merge" mentioned only inside a heredoc body
    # (e.g. as prose in a commit message) must not count as a genuine
    # top-level invocation.
    command = 'git commit -m "$(cat <<\'EOF\'\nmentions gh pr merge in prose\nEOF\n)"'
    assert is_pr_merge_command(command) is False

def test_is_pr_merge_command_unrelated_command():
    assert is_pr_merge_command("git status") is False


# ---------------------------------------------------------------------------
# is_merge_help_only composition (dev-env#557)
# ---------------------------------------------------------------------------

def test_help_command_is_pr_merge_command_and_is_help_only():
    # The new guard sits BEHIND is_pr_merge_command's own gate -- confirm a
    # --help command still passes that upstream gate (so the new guard is
    # actually reached), and that is_merge_help_only correctly classifies it.
    command = "gh pr merge --help"
    assert is_pr_merge_command(command) is True, "gh pr merge --help must still pass the upstream gate"
    assert is_merge_help_only(command) is True

def test_unresolved_real_merge_command_is_not_help_only():
    # A genuine merge command (no --help anywhere) must not be excluded by
    # the new guard -- the numbering-collision evaluation must still proceed.
    command = "gh pr merge 518 --squash --delete-branch"
    assert is_pr_merge_command(command) is True
    assert is_merge_help_only(command) is False


# ---------------------------------------------------------------------------
# format_block_message
# ---------------------------------------------------------------------------

def test_format_block_message_names_file_and_number():
    findings = {
        "CLAUDE.md Testing section": {
            34: ("34. **Branch's item**", "34. **Main's item**"),
        }
    }
    msg = format_block_message(findings)
    assert "CLAUDE.md Testing section" in msg
    assert "#34" in msg
    assert "Branch's item" in msg
    assert "Main's item" in msg
    assert "BLOCKED" in msg


# ---------------------------------------------------------------------------
# Layer 2: end-to-end subprocess tests
# ---------------------------------------------------------------------------


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _git(args, cwd, check=True):
    return subprocess.run(["git"] + args, cwd=str(cwd), check=check, capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    """A real, throwaway `git init` repo on branch `main`, with local
    identity configured. `-c init.templateDir= -c core.hooksPath=`
    neutralizes any global template/hooks config so this repo's behavior
    doesn't depend on the developer's machine (mirrors
    test_canonical_mutate_guard.py's `_init_throwaway_repo`).
    """
    subprocess.run(
        ["git", "-c", "init.templateDir=", "-c", "core.hooksPath=", "init", "-q", str(root)],
        check=True, capture_output=True,
    )
    _git(["checkout", "-q", "-b", "main"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Test"], root)


def _write_claude_md(root: Path, items: list) -> None:
    """Write a minimal but well-formed CLAUDE.md with a `## Testing` section
    containing *items* (a list of "N. **Title**" strings). Also writes an
    empty-but-well-formed docs/adr/INDEX.md alongside it -- the hook checks
    both files on every run, and in the real dev-env repo both always exist
    at every ref; a fixture missing one would (correctly) trip the "could
    not read origin/main" skip-and-advise path for a reason unrelated to
    whatever this fixture is actually testing.
    """
    body = "# dev-env\n\n## Testing\n\n" + "\n\n".join(items) + "\n\n## Observability\n\nN/A\n"
    (root / "CLAUDE.md").write_text(body, encoding="utf-8")
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    (adr_dir / "INDEX.md").write_text(
        "# Architectural Decision Records\n\n| # | Title | Date | Status | Tags |\n|---|-------|------|--------|------|\n",
        encoding="utf-8",
    )


def _commit_all(root: Path, message: str) -> None:
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", message], root)


def test_main_not_dev_env_repo_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "some-other-repo"
        repo.mkdir()
        _init_repo(repo)  # no origin remote configured at all
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge --squash --delete-branch"},
            "cwd": str(repo),
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(
                f"expected exit 0 (no-op, no origin remote), got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
    return "a repo with no dev-env origin remote is a silent no-op (exit 0)"


def test_main_non_merge_command_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "cwd": tmp,
        }
        proc = _run_hook(payload)
        if proc.returncode != 0:
            raise AssertionError(f"expected exit 0 (non-merge command), got {proc.returncode}. stderr={proc.stderr!r}")
    return "a non-merge command is a silent no-op (exit 0), never touches git"


def test_main_blocks_genuine_cross_branch_collision():
    """End-to-end reproduction of the dev-env#516 incident shape -- see
    `_run_collision_test`'s own docstring for the full scenario."""
    return _run_collision_test("Bash")


def test_main_blocks_genuine_cross_branch_collision_via_powershell_tool_name():
    """dev-env#620 (ADR-071 Amendment 4): the identical collision scenario,
    with tool_name=PowerShell instead of Bash -- proves the PowerShell
    PreToolUse extension reaches this hook's actual git-fetch-and-compare
    logic, not just a settings.json wiring assumption. If tool_name filtering
    were still Bash-only, this would incorrectly exit 0 (no-op) instead of
    blocking."""
    return _run_collision_test("PowerShell")


def _run_collision_test(tool_name: str):
    """End-to-end reproduction of the dev-env#516 incident shape: two
    independent branches both add Testing item 2 with different text; the
    one that reaches `gh pr merge` second must be blocked.

    Builds a real bare "origin" (a local filesystem path ending in
    brownm09/dev-env so `is_dev_env_repo` matches) plus two independent
    clones -- "competitor" pushes its own item 2 straight to origin/main;
    "work" (this test's stand-in PR branch) commits a *different* item 2
    without ever seeing the competitor's push, then runs the real hook via
    subprocess. The hook's own `git fetch` must discover the competitor's
    already-pushed commit for the collision to be detected -- this is the
    one behavior the pure find_new_collisions tests cannot exercise, since
    they hand-construct the three snapshots instead of deriving them from
    real git refs.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Path must end in .../brownm09/dev-env for is_dev_env_repo to match.
        origin = tmp_path / "brownm09" / "dev-env"
        origin.parent.mkdir(parents=True)
        # -b main: without an explicit initial branch, the bare repo's HEAD
        # symref defaults to whatever init.defaultBranch resolves to (often
        # "master") and is never updated by a later push -- clones then hit
        # "remote HEAD refers to nonexistent ref, unable to checkout" and
        # silently get no local branch at all.
        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "-q", "--bare", "-b", "main", str(origin)],
            check=True, capture_output=True,
        )
        origin_url = origin.as_posix()

        # Seed: base commit with only item 1, pushed to origin/main.
        seed = tmp_path / "seed"
        seed.mkdir()
        _init_repo(seed)
        _write_claude_md(seed, ["1. **First item**"])
        _commit_all(seed, "base")
        _git(["push", origin_url, "main:main"], seed)

        # Work: clone the base and branch off it NOW, before the competitor
        # pushes below -- this must happen first, or work's clone would
        # already include the competitor's item 2 as part of its own base
        # (nothing "new" left for the branch to introduce, no collision to
        # find). Adds a DIFFERENT item 2 on its own branch -- self-consistent
        # in isolation, exactly like #506.
        work = tmp_path / "work"
        _git(["clone", "-q", origin_url, str(work)], tmp_path)
        _git(["checkout", "-q", "-b", "feat/my-branch"], work)
        _write_claude_md(work, ["1. **First item**", "2. **Bob's item**"])
        _commit_all(work, "bob adds item 2")

        # Competitor: a separate clone of the same base, which adds item 2
        # and pushes to origin/main FIRST -- simulates another PR merging
        # while "work" (this test's stand-in PR branch) is still in review.
        competitor = tmp_path / "competitor"
        _git(["clone", "-q", origin_url, str(competitor)], tmp_path)
        _write_claude_md(competitor, ["1. **First item**", "2. **Alice's item**"])
        _commit_all(competitor, "alice adds item 2")
        _git(["push", origin_url, "main:main"], competitor)

        payload = {
            "tool_name": tool_name,
            "tool_input": {"command": "gh pr merge --squash --delete-branch"},
            "cwd": str(work),
        }
        proc = _run_hook(payload)
        if proc.returncode != 2:
            raise AssertionError(
                f"expected exit 2 (collision) for tool_name={tool_name!r}, got {proc.returncode}. "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )
        if proc.stdout.strip():
            raise AssertionError(f"expected empty stdout (block reason must go to stderr), got {proc.stdout!r}")
        stderr = proc.stderr
        for expected in ("BLOCKED", "#2", "CLAUDE.md Testing section", "Alice's item", "Bob's item"):
            if expected not in stderr:
                raise AssertionError(f"expected {expected!r} in block message, got: {stderr!r}")
    return (
        f"a genuine cross-branch collision discovered via a real git fetch blocks the merge "
        f"(exit 2) for tool_name={tool_name!r}, reason on stderr"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    total = passed + failed
    print(f"\nTests: {passed} passed, 0 skipped, {failed} failed")
    sys.exit(1 if failed else 0)
