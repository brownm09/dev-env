#!/usr/bin/env python3
"""PostToolUse/Bash hook — set a sentinel flag after a stub is pushed to
engineering-journal so the Stop hook can remind Claude to archive the session.

Fires on every Bash tool call. Most calls are skipped quickly:
  1. Command must contain a top-level `git push` invocation (scan_top_level-
     anchored — not text inside a heredoc body, a quoted argument, or a $()
     subshell)
  2. Command must reference the engineering-journal repo via a top-level `cd`
     or `git -C` directory argument (also scan_top_level-anchored — not text
     inside a heredoc body, a quoted argument, or a $() subshell)
  3. Push must have succeeded (no error output)
  4. Most-recent commit in engineering-journal must touch a .stub.md file
  5. None of the touched stub(s)' paired manifest shard(s) may show an
     unresolved open PR (dev-env#651, ADR-091 Amendment 1) — a stub is pushed
     immediately after `gh pr create` and again after each subsequent push in
     the same session, well before `/review` and `gh pr merge`; arming the
     reminder then would instruct archiving (destroying) a worktree the
     review/merge still needs.

When all five conditions are met, writes a sentinel file to the scratch
directory. The Stop hook (journal-stop-check.py) reads and clears the
sentinel and issues the archive reminder on stderr with exit 2 — the channel
that reaches Claude for a Stop hook (exit-0 stdout is NOT added to Claude's
context for Stop, so the reminder must block the stop to be seen; ADR-091).

Exit 0 on every code path — never blocks.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "...", ...},
    "tool_response": {"stdout": "...", "stderr": "..."}  # NOT "output" — ADR-049
  }
"""
import _winsubp  # noqa: F401  -- suppress console windows on Windows
import json
import re
import subprocess
import sys
from pathlib import Path

from _hookio import read_command_output, scan_top_level
from _journal_schema import has_unresolved_open_pr, parse_manifest_text

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
SENTINEL = Path.home() / ".claude" / "scratch" / "stub-pushed.flag"

# Anchored top-level match — identical to pr-merge-reminder.py's
# _check_push_stmt / is_git_push_command (ADR-050 Amendment 5 and this
# Amendment 10).
_PUSH_RE = re.compile(r"(?:cd\s+\S+\s+&&\s+)?git\s+push\b")


def _check_push_stmt(token: str) -> bool:
    return bool(_PUSH_RE.match(token.lstrip()))


def is_git_push_command(command: str) -> bool:
    """Return True only when *command* contains a top-level `git push`.

    Anchored via `scan_top_level` rather than a raw substring test, so
    `git push` text inside a heredoc body, a quoted argument, or a `$()`
    subshell does not count as an invocation — identical to
    pr-merge-reminder.py's own `is_git_push_command()`, the only other hook
    with a git-push-detection need (dev-env#532, ADR-050 Amendment 10).
    """
    return scan_top_level(command, _check_push_stmt)


# A genuine reference to the engineering-journal repo: the literal name
# (either spelling) appears as the directory argument of a top-level `cd` or
# `git -C` at the START of a scan_top_level segment — not merely present
# anywhere in the raw command, which would also match text buried in a
# heredoc body, a quoted argument, or a $() subshell (dev-env#539, ADR-050
# Amendment 12).
_EJ_REF_RE = re.compile(r"(?:cd|git\s+-C)\s+\S*engineering[-_]journal\S*")


def _check_engineering_journal_ref(token: str) -> bool:
    return bool(_EJ_REF_RE.match(token.lstrip()))


def references_engineering_journal(command: str) -> bool:
    """Return True only when *command* contains a top-level reference to the
    engineering-journal repo, via a `cd <path>` or `git -C <path>` directory
    argument (either the hyphenated or underscored spelling).

    Anchored via `scan_top_level` rather than a raw substring test, so
    "engineering-journal"/"engineering_journal" text inside a heredoc body, a
    quoted argument, or a `$()` subshell does not count as a reference — same
    false-positive shape `is_git_push_command` above already guards against
    (dev-env#532, ADR-050 Amendment 10), applied to a repo-name literal
    instead of a CLI-invocation literal (dev-env#539, ADR-050 Amendment 12).
    """
    return scan_top_level(command, _check_engineering_journal_ref)


def head_commit_files(repo: Path) -> list[str]:
    """Return the list of file paths touched by HEAD in *repo*, or [] on any failure."""
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()
    except Exception:
        return []


def most_recent_commit_has_stub(files: list[str]) -> bool:
    """Return True if *files* (HEAD's touched-file list) includes a .stub.md file."""
    return any(f.endswith(".stub.md") for f in files)


def manifest_path_for_stub(stub_path: str) -> str:
    """Map a session's stub path to its 1:1-paired manifest path.

    ``sessions/<project>/YYYY-MM-DD_HHMMSS.stub.md`` pairs with the same directory's
    ``YYYY-MM-DD_HHMMSS.manifest.jsonl`` (docs/REFERENCE.md -> "Manifest shard format":
    named to pair 1:1 with the session's stub). Non-``.stub.md`` input is returned
    unchanged (defensive; the sole caller already filters to ``.stub.md`` paths).
    """
    if not stub_path.endswith(".stub.md"):
        return stub_path
    return stub_path[: -len(".stub.md")] + ".manifest.jsonl"


def head_commit_has_unresolved_pr(repo: Path, files: list[str]) -> bool:
    """True if any session whose .stub.md HEAD touched has an unresolved open PR.

    Reads each touched stub's *paired* manifest shard's CURRENT on-disk content --
    deliberately NOT restricted to manifest files this specific commit's own diff-tree
    touched. A session's manifest is written once, alongside its stub, right after
    `gh pr create`; a later stub-only push in the same session (e.g. a review-finding-fixed
    update, or the "PR updated" case in claude/CLAUDE.md's Update triggers) does not
    re-touch it even though the PR it names may still be open. Confirmed against this
    repo's own history (dev-env#651, ADR-091 Amendment 1): dev-env PR #633's
    2026-07-08_183908 session wrote its manifest once with prs_opened:[633] at 18:40, then
    pushed a stub-only "review finding fixed" commit at 18:44 (no manifest in that commit's
    own diff) two minutes before merging. Checking only the triggering commit's own diff
    would silently miss that window.

    Only stub paths that still exist on disk are considered -- a .stub.md path this
    commit *deleted* (e.g. journal-compose consuming it) has no in-progress session to
    protect and is skipped.

    Conservative on every ambiguity: a still-live stub with no manifest yet, an unreadable
    manifest, or an unparseable line all return True (fail toward NOT archiving) -- the
    cost of wrongly skipping one archive reminder is far lower than wrongly destroying a
    worktree mid-review (the bug this fixes).
    """
    manifest_paths: set[str] = set()
    for f in files:
        if not f.endswith(".stub.md"):
            continue
        if not (repo / f).exists():
            continue  # deleted by this commit (e.g. journal-compose) -- not an active session
        manifest_paths.add(manifest_path_for_stub(f))

    for manifest_rel in manifest_paths:
        manifest_file = repo / manifest_rel
        if not manifest_file.exists():
            return True
        try:
            text = manifest_file.read_text(encoding="utf-8")
        except OSError:
            return True
        for _lineno, entry in parse_manifest_text(text):
            if entry is None:
                return True
            if has_unresolved_open_pr(entry):
                return True
    return False


def has_push_error(output: str) -> bool:
    """Return True if push output shows an obvious failure.

    git reports push failures with `error:` / `fatal:` lines on stderr. Before
    #380 this guard read the legacy `output` field, which is always empty on the
    real payload, so the guard was a no-op — a failed journal push could still
    arm the archive reminder. The shared `read_command_output` helper now feeds
    it the real stdout/stderr.
    """
    lower = output.lower()
    return "error:" in lower or "fatal:" in lower


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    command = (data.get("tool_input") or {}).get("command", "")
    output = read_command_output(data)

    # Must be a git push
    if not is_git_push_command(command):
        sys.exit(0)

    # Must reference engineering-journal
    if not references_engineering_journal(command):
        sys.exit(0)

    # Must not show an obvious error
    if has_push_error(output):
        sys.exit(0)

    # Confirm the pushed commit contains a stub file
    files = head_commit_files(JOURNAL_REPO)
    if not most_recent_commit_has_stub(files):
        sys.exit(0)

    # Must not have an unresolved open PR from this session -- a stub is pushed right
    # after `gh pr create` and again after each subsequent push, well before /review and
    # `gh pr merge` (dev-env#651, ADR-091 Amendment 1); archiving then destroys the
    # worktree that same-session work still needs.
    if head_commit_has_unresolved_pr(JOURNAL_REPO, files):
        sys.exit(0)

    # Write sentinel — the Stop hook will consume it and issue the reminder
    try:
        SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        SENTINEL.write_text("1")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
