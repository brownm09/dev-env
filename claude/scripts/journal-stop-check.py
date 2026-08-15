#!/usr/bin/env python3
"""
Stop hook: after a stub push, block the stop so Claude archives the session;
and (non-blocking) remind the user of stale journal work at session end.

Check 1 — stub-push sentinel (CLAUDE-facing, BLOCKING via exit 2 + stderr):
  If stub-push-archive-reminder.py wrote a sentinel flag (meaning a stub was
  pushed to engineering-journal THIS session -- the sentinel is scoped to the
  pushing session's own session_id, dev-env#980, ADR-091 Amendment 2), consume
  the flag and emit the archive instruction on STDERR with exit 2. Only a Stop
  event whose own session_id matches the sentinel's ever consumes it -- prior
  to the fix, a single global sentinel let any concurrent session's Stop
  consume it, producing both false-positive archive instructions (a session
  that never pushed) and missed reminders (the actual pushing session's own
  Stop losing the race). The reminder asks CLAUDE to call the
  ccd_session_mgmt__archive_session MCP tool — an action only Claude can
  take — so it must reach Claude's context. A Stop hook's exit-0 stdout does
  NOT (only UserPromptSubmit / UserPromptExpansion / SessionStart get exit-0
  stdout added to context), so the former stdout emission was invisible to
  Claude and the intended session-archiving silently never happened. Exit 2 +
  stderr is the channel that reaches Claude (ADR-091; same failure class
  ADR-088's tile gate fixed). Fires at most once per session (the sentinel is
  consumed on read) and honors the stop_hook_active loop guard.

Checks 2–3 (user-facing, NON-blocking — exit 0, systemMessage):
1. Stale *_draft.md / *.stub.md files from before today
2. Unmerged remote draft/* branches
  These point at work for a LATER, dedicated session (journal composition is
  dedicated-session-only and must never be triggered proactively; stale-PR
  merges are separate work), so they must not block the stop. A Stop hook's
  exit-0 stdout is invisible (transcript-only), so these ride the _hookout
  systemMessage channel — the one exit-0 channel a Stop hook delivers to the
  user (ADR-103) — NOT plain stdout, which never surfaced them at all.

Also cleans up orphaned draft files: physical files left on disk as untracked
after git rm. This prevents new-day-journal-check.py false positives on the
next session (see dev-env#31).
"""

import _winsubp  # noqa: F401  -- suppress console windows on Windows
import _hookout
import _hookutil
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

JOURNAL_REPO = Path.home() / "Git" / "engineering-journal"
# Must match stub-push-archive-reminder.py's SENTINEL_PREFIX -- per-session
# sentinel (dev-env#980, ADR-091 Amendment 2), duplicated as a literal in
# both files like JOURNAL_REPO already is rather than shared via import.
SENTINEL_PREFIX = "stub-pushed-"
# session_id is trusted harness-generated input (a UUID -- see token-tracker.py's
# comment on the same field) on every other _hookutil.sentinel_path caller, but
# this hook's operation is a DELETE (not the exists()/write_text("") every other
# caller performs), so an unsanitized session_id here would let a crafted value
# escape ~/.claude/scratch via embedded path separators (dev-env#980 review
# finding). Treat anything outside this class the same as a missing session_id.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")
TODAY = date.today().strftime("%Y-%m-%d")


def archive_reminder_message() -> str:
    """The Claude-facing archive instruction, emitted on stderr with exit 2 so it
    reaches Claude's context (a Stop hook's exit-0 stdout does not — ADR-091).

    ASCII-only: Claude Code pipes hook output as cp1252 on Windows, so a char
    outside it (an arrow, an em-dash) would raise UnicodeEncodeError and the whole
    reminder would vanish — mirrors stop-tile-enumeration-gate.py's constraint.
    """
    return (
        "Stub committed and pushed to engineering-journal. "
        "Archive this session now: call ccd_session_mgmt__archive_session "
        "(use list_sessions to look up the current session_id if needed). "
        "Then stop."
    )


def consume_stub_pushed_sentinel(
    session_id: str = "", sentinel: Path | None = None, scratch: Path | None = None
) -> str | None:
    """Return the archive reminder if THIS session's stub-push sentinel exists, else None.

    Deletes the sentinel before returning so the reminder fires only once — this
    consume-on-read is the primary one-shot guard for the exit-2 archive block.
    Any I/O failure is swallowed — the sentinel check is best-effort.

    *sentinel*, when given (the tests' fixture-injection path), is used as-is.
    Otherwise the path is derived from *session_id* via
    `_hookutil.sentinel_path(SENTINEL_PREFIX, session_id, scratch=scratch)` --
    production always takes this branch with *scratch* left at its default
    (~/.claude/scratch); tests pass *scratch* directly instead of
    monkeypatching `_hookutil.SCRATCH`. A *session_id* that is falsy OR does
    not match `_SAFE_SESSION_ID` (see the module-level comment) returns None
    immediately without touching the filesystem, with no explicit *sentinel*
    override: computing a path from an empty or unsanitized id would degrade
    to a shared/attacker-influenced path, resurrecting the dev-env#980
    cross-session bug in miniature (ADR-091 Amendment 2).
    """
    try:
        if sentinel is not None:
            path = sentinel
        elif session_id and _SAFE_SESSION_ID.match(session_id):
            path = _hookutil.sentinel_path(SENTINEL_PREFIX, session_id, scratch=scratch)
        else:
            return None
        if path.exists():
            path.unlink()
            return archive_reminder_message()
    except Exception:
        pass
    return None


def composed_project_dates_on_main() -> set[tuple[str, str]]:
    """Return (project, YYYY-MM-DD) pairs that have a composed file on origin/main.

    Finer-grained than composed_dates_on_main(): used to suppress false positives
    when stubs from a composed date are still present on disk because a new-day
    branch was cut from the previous day's draft branch instead of from main.

    Note: reads the local remote-tracking ref (origin/main) without fetching —
    results reflect the last fetch. A stale ref could miss composed dates, causing
    branch-lineage artifacts to appear stale. The next fetch will self-correct.
    """
    try:
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "origin/main", "sessions/"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
        pairs: set[tuple[str, str]] = set()
        for line in result.stdout.splitlines():
            parts = line.split("/")
            if len(parts) < 3:
                continue
            project = parts[1]
            fname = parts[-1]
            if fname.endswith(".stub.md") or not fname.endswith(".md"):
                continue
            if len(fname) >= 10 and fname[4] == "-" and fname[7] == "-":
                pairs.add((project, fname[:10]))
        return pairs
    except Exception:
        return set()


def stale_draft_artifacts() -> list[str]:
    """Return stub/draft paths whose (project, date) lacks a composed file on main.

    Stubs whose project+date already have a composed file on main are branch-lineage
    artifacts (carried forward because the new-day branch was cut from the previous
    day's draft instead of from main) — not genuine unComposed drafts.
    """
    composed = composed_project_dates_on_main()
    artifacts = []
    for pattern in (
        str(JOURNAL_REPO / "sessions" / "**" / "*_draft.md"),
        str(JOURNAL_REPO / "sessions" / "**" / "????-??-??_*.stub.md"),
    ):
        artifacts.extend(glob.glob(pattern, recursive=True))
    stale = []
    for f in artifacts:
        if os.path.basename(f).startswith(TODAY):
            continue
        date_prefix = os.path.basename(f)[:10]
        try:
            parts = Path(f).parts
            sessions_idx = next(i for i, p in enumerate(parts) if p == "sessions")
            project = parts[sessions_idx + 1]
        except (StopIteration, IndexError):
            project = None
        if project and (project, date_prefix) in composed:
            continue  # Already composed on main — branch-lineage artifact, not stale
        stale.append(f)
    stale.sort(key=lambda f: os.path.basename(f), reverse=True)
    return stale


def unmerged_draft_branches() -> list[str]:
    """Return remote draft/* branch names not yet merged into origin/main."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", "refs/heads/draft/*"],
            cwd=JOURNAL_REPO,
            capture_output=True,
            text=True,
            timeout=15,
        )
        remote_dates = set()
        for line in result.stdout.splitlines():
            if "\t" in line:
                ref = line.split("\t", 1)[1].strip()
                remote_dates.add(ref.replace("refs/heads/draft/", ""))

        if not remote_dates:
            return []

        merged_pairs = composed_project_dates_on_main()
        merged = {d for _, d in merged_pairs}
        unmerged = sorted(
            [d for d in remote_dates if d != TODAY and d not in merged],
            reverse=True,
        )
        return unmerged
    except Exception:
        return []


def remove_orphaned_drafts(stale: list[str]) -> list[str]:
    """Delete stale draft files that are untracked (orphaned after git rm)."""
    removed = []
    for path_str in stale:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", path_str],
                cwd=JOURNAL_REPO,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout.startswith("??"):
                os.remove(path_str)
                removed.append(path_str)
        except Exception:
            pass
    return removed


def parse_stop_hook_active(raw: str) -> bool:
    """True iff the Stop payload's stop_hook_active flag is set. Tolerates empty
    or malformed stdin (returns False) so a parse hiccup never suppresses the
    archive block on a genuine first Stop.
    """
    if not raw:
        return False
    try:
        return bool(json.loads(raw).get("stop_hook_active"))
    except Exception:
        return False


def parse_session_id(raw: str) -> str:
    """Return the Stop payload's session_id, or "" on empty/malformed/non-dict
    stdin or a missing field. Never raises -- mirrors parse_stop_hook_active's
    tolerant-parsing shape as an independent sibling reader of the same raw
    string (dev-env#980, ADR-091 Amendment 2).
    """
    if not raw:
        return ""
    try:
        return str(json.loads(raw).get("session_id") or "")
    except Exception:
        return ""


def main() -> None:
    _hookutil.record_heartbeat("journal-stop-check")
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        raw = ""
    stop_hook_active = parse_stop_hook_active(raw)
    session_id = parse_session_id(raw)

    # Garbage-collect stale per-session sentinels (dev-env#980, ADR-091 Amendment
    # 2 review finding). stub-push-archive-reminder.py only sweeps on its own
    # (rare) success path -- this hook fires on every Stop, so it's the more
    # reliable backstop for orphaned sentinels from a crashed/never-Stopped
    # session. Best-effort; swallows its own errors, never raises.
    _hookutil.cleanup_stale_sentinels(SENTINEL_PREFIX)

    # Check 1 — stub-push sentinel (CLAUDE-facing archive instruction, BLOCKING).
    # A stub was pushed this session, so Claude must archive it by calling the
    # ccd_session_mgmt__archive_session MCP tool. Because this reminder asks
    # CLAUDE to act, it must reach Claude's context — and for a Stop hook that
    # means exit 2 + stderr: exit-0 stdout is NOT added to Claude's context for
    # Stop (only UserPromptSubmit / UserPromptExpansion / SessionStart get that),
    # so the former stdout emission was invisible to Claude (ADR-091; same failure
    # class ADR-088's tile gate fixed). Gate the consume on stop_hook_active so a
    # continuation from a prior block never consumes the flag without delivering
    # it; the consume-on-read then makes the block one-shot (no Stop-loop risk).
    if not stop_hook_active:
        reminder = consume_stub_pushed_sentinel(session_id)
        if reminder:
            sys.stderr.write(f"[journal-stop-hook] {reminder}\n")
            sys.exit(2)

    # Checks 2–3 — genuinely user-facing advisories (NON-blocking: exit 0,
    # systemMessage via _hookout). These point at work for a LATER, dedicated
    # session (composition is dedicated-session-only and must never be triggered
    # proactively; stale PRs are separate work), so they must not block the stop.
    # A Stop hook's exit-0 stdout is transcript-only (invisible), so the former
    # print() here surfaced nothing; the _hookout systemMessage channel is the one
    # exit-0 channel a Stop hook delivers to the user (ADR-103).
    #
    # Note: only Check 1 (the block) is gated on stop_hook_active — unlike
    # stop-tile-enumeration-gate.py, which exits 0 for the WHOLE hook when
    # stop_hook_active is set. These advisories are an independent, non-blocking
    # responsibility and must still surface on a continuation Stop, so do NOT add
    # a top-level `if stop_hook_active: sys.exit(0)` early-return here.
    messages = []

    stale = stale_draft_artifacts()

    removed = remove_orphaned_drafts(stale)
    for path_str in removed:
        messages.append(
            f"[journal-stop-hook] Removed orphaned draft: {Path(path_str).as_posix()}"
        )

    # After removing orphans, re-evaluate what's still stale
    still_stale = [f for f in stale if f not in removed]

    if still_stale:
        artifact_path = Path(still_stale[0]).as_posix()
        artifact_date = os.path.basename(still_stale[0])[:10]
        messages.append(
            f"[journal-stop-hook] Stale draft artifact: {artifact_path}\n"
            f"The engineering journal draft from {artifact_date} was never composed.\n"
            f"Run /journal-compose in a new session to close it out."
        )

    unmerged = unmerged_draft_branches()
    if unmerged:
        dates_str = ", ".join(unmerged)
        messages.append(
            f"[journal-stop-hook] Unmerged draft branch(es): {dates_str}\n"
            f"These branches still need a PR merged to main."
        )

    if messages:
        # systemMessage (exit 0) — the one exit-0 channel a Stop hook delivers to
        # the user. emit_advisory exits 0, so these advisories never block the stop.
        _hookout.emit_advisory("Stop", "\n".join(messages), audience="user")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
