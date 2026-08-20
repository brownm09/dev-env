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
directory, scoped to this session's own session_id (dev-env#980, ADR-091
Amendment 2 -- a session-id-less shared sentinel let any concurrent session's
Stop consume it, producing both false-positive and missed-reminder archive
instructions). The Stop hook (journal-stop-check.py) reads and clears the
SAME-session sentinel and issues the archive reminder on stderr with exit 2 —
the channel that reaches Claude for a Stop hook (exit-0 stdout is NOT added to
Claude's context for Stop, so the reminder must block the stop to be seen;
ADR-091).

Exit 0 on every code path — never blocks.

Stdin JSON shape (PostToolUse):
  {
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "session_id": "...",
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

from _hookio import read_command, read_command_output, scan_top_level
import _hookutil
from _journal_schema import decode_shard_bytes, has_unresolved_open_pr, parse_manifest_text

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
# Per-session sentinel (dev-env#980, ADR-091 Amendment 2) -- the file is
# named f"{SENTINEL_PREFIX}{session_id}.flag" via _hookutil.sentinel_path, so
# each session's push arms only its OWN sentinel. Prior to this fix, a single
# global "stub-pushed.flag" let any concurrent session's Stop consume it.
SENTINEL_PREFIX = "stub-pushed-"
LEGACY_SENTINEL = Path.home() / ".claude" / "scratch" / "stub-pushed.flag"
# session_id is trusted harness-generated input (a UUID) on every other
# _hookutil.sentinel_path caller, but this hook's write target is
# payload-derived, so an unsanitized session_id could otherwise escape
# ~/.claude/scratch via embedded path separators (dev-env#980 review finding).
# Must match journal-stop-check.py's _SAFE_SESSION_ID.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")

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
    or non-UTF-8/BOM-prefixed manifest, an unparseable line, or an empty/whitespace-only
    manifest (parses to zero entries -- a truncated or not-yet-fully-written shard, not
    "nothing was ever opened") all return True (fail toward NOT archiving) -- the cost of
    wrongly skipping one archive reminder is far lower than wrongly destroying a worktree
    mid-review (the bug this fixes). Decodes via the shared `_journal_schema.decode_shard_bytes`
    (BOM handling) rather than a raw `read_text`, matching how this module's other two
    consumers (`validate-manifest.py`, `journal-shard-write-advisory.py`) decode shard bytes --
    a bare `read_text(encoding="utf-8")` would raise `UnicodeDecodeError` (not `OSError`) on a
    non-UTF-8 file, bypassing the intended per-manifest conservative-True branch below and
    relying only on the outer `try/except Exception: sys.exit(0)` in `__main__` for safety.

    Known accepted limitation (dev-env#651): a cross-session merge that updates the
    *original* opening session's stub in place (rather than writing a new stub) leaves that
    session's own manifest permanently showing the PR unresolved, since `prs_closed` is set
    in the *merging* session's manifest instead (per claude/CLAUDE.md's "New session: update
    the opening stub in place ... set prs_closed:[N] in this session's manifest shard").
    Because this function derives a manifest from each touched stub's own path, the merge
    commit's touch of the original stub still reads the original session's stale manifest and
    reports unresolved -- suppressing the archive reminder for a merge that just genuinely
    completed. This is the same fail-safe direction as every other ambiguity here (a missed
    nudge, not a false archive), and is deliberately not fixed in this pass -- see the tracked
    follow-up issue for the design tradeoff (bringing back an `open-prs/<N>.json`-deletion
    signal would resolve it but reintroduces the per-PR-shard data source this fix's ADR
    amendment already considered and rejected as the primary source).
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
            raw = manifest_file.read_bytes()
        except OSError:
            return True
        text, _problem = decode_shard_bytes(raw)
        if text is None:
            return True  # not valid UTF-8 even past a BOM check
        entries = parse_manifest_text(text)
        if not entries:
            return True  # empty/whitespace-only manifest -- can't confirm resolved
        for _lineno, entry in entries:
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
    _hookutil.record_heartbeat("stub-push-archive-reminder")
    raw = sys.stdin.read().strip()
    if not raw:
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if not isinstance(data, dict):
        # A valid-JSON-but-non-dict top-level payload (a list, string, number,
        # or null) would otherwise crash the very next line (dev-env#1031/
        # #1033, mirroring usage-snapshot.py's dev-env#1028 post-review fix).
        sys.exit(0)

    # A sentinel that can't be scoped to a session must never fall back to a
    # shared path -- that's exactly the dev-env#980 bug. Forgo rather than
    # use a synthetic fallback id (mirrors posttooluse-inert-advisory.py's
    # "forgo the advisory in an anomalous session" choice). Also reject a
    # session_id containing anything outside _SAFE_SESSION_ID -- this value
    # is interpolated into a filesystem path and unlink()'d by the reader, so
    # an unsanitized value could otherwise escape ~/.claude/scratch via
    # embedded path separators (dev-env#980 review finding).
    session_id = str(data.get("session_id") or "")
    if not session_id or not _SAFE_SESSION_ID.match(session_id):
        sys.exit(0)

    # dev-env#1031/#1033: read_command() never raises on a present-but-non-dict
    # tool_input (dev-env#1028's payload shape). The pre-fix `(data.get(
    # "tool_input") or {}).get("command", "")` chain only substitutes `{}`
    # for a FALSY non-dict tool_input (None, "", 0) -- a TRUTHY non-dict
    # value survives `or {}` unchanged and crashes on the next `.get()`.
    command = read_command(data)
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

    # Write sentinel, scoped to this session -- the Stop hook of the SAME
    # session will consume it and issue the reminder (dev-env#980: a global
    # sentinel previously let any session's Stop consume it).
    try:
        _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)
        # One-time migration cleanup: the pre-fix global sentinel used a
        # non-hyphenated filename cleanup_stale_sentinels' prefix glob never
        # matches -- remove it opportunistically so an in-flight legacy flag
        # doesn't sit in scratch forever, unmatched by anything (dev-env#980
        # review finding).
        LEGACY_SENTINEL.unlink(missing_ok=True)
        sentinel = _hookutil.sentinel_path(SENTINEL_PREFIX, session_id)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("1")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
