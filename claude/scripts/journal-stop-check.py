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

  Before firing, the reminder is best-effort AUGMENTED (never suppressed,
  never re-fired) with an in-flight-work caveat when the session's own
  transcript still shows pending/in_progress TodoWrite items or a
  backgrounded Agent call with no observed completion notification: the same
  message that instructs Claude to archive also states that
  ccd_session_mgmt__archive_session requires the user's explicit agreement
  and must never be called speculatively while approved work is unfinished
  (dev-env#1002, ADR-091 Amendment 3). This never changes whether or how
  often the reminder fires or touches the sentinel's one-shot consume-on-read
  logic, and degrades to the original unmodified reminder on any failure to
  resolve or parse the transcript — see in_flight_work_note().

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

# Bare-name import for these two specific _hookutil helpers -- matches the
# established convention in sibling Stop hooks (stop-tile-enumeration-gate.py,
# stop-experiment-verdict-gate.py), which import _content_items and
# _user_message_texts the same way while leaving sentinel_path /
# cleanup_stale_sentinels / record_heartbeat / find_transcript / load_records
# module-qualified (dev-env#1002).
from _hookutil import _content_items, _user_message_texts

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


def pending_todo_count(records: list) -> int:
    """Return the count of pending/in_progress todos in the LAST TodoWrite
    tool_use call found in *records* (transcript order), or 0 when no
    TodoWrite call exists or its todos are malformed/absent.

    A TodoWrite call fully replaces the prior list -- it is not additive --
    so only the most recent call reflects current state; earlier calls are
    superseded, never summed. Used by in_flight_work_note() to append an
    in-flight-work caveat to the archive reminder (dev-env#1002) so the
    instruction to archive doesn't ignore a still-open, approved TODO list.

    Never raises: isinstance guards throughout, matching _content_items'
    own defensive contract -- safe on malformed/hand-built record lists.
    """
    found_any = False
    last_todos = None
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "TodoWrite"
            ):
                continue
            found_any = True
            inp = item.get("input")
            last_todos = inp.get("todos") if isinstance(inp, dict) else None
    if not found_any or not isinstance(last_todos, list):
        return 0
    return sum(
        1
        for t in last_todos
        if isinstance(t, dict) and t.get("status") in ("pending", "in_progress")
    )


def open_background_agent_count(records: list) -> int:
    """Return the count of backgrounded Agent tool_use calls in *records*
    whose completion has not been observed anywhere later in the transcript.

    Pass 1 collects the tool_use ``id`` of every assistant-record Agent call
    whose ``input.run_in_background`` is strictly ``True`` (not merely
    truthy -- an omitted or falsy flag is a separate, already upstream-
    blocked problem; see pre-tool-use-nested-agent-background-guard.py,
    dev-env#935 -- treating it as "backgrounded" here would be wrong). Pass 2
    checks, for each such id, whether the literal substring
    ``f"<tool-use-id>{tid}</tool-use-id>"`` occurs in any ``type=="user"``
    record's text (via ``_user_message_texts``) -- the harness's own
    task-notification delivery shape for a finished background Agent call,
    confirmed by direct observation (dev-env#1002). A backgrounded call's
    own immediate tool_result only confirms the async LAUNCH ("Async agent
    launched successfully...") -- never its completion -- so
    ``iter_bash_calls``'s tool_use/tool_result id-pairing pattern does not
    apply here.

    Deliberately a per-text-item containment check, never a joined/flattened
    haystack: checking each text independently means two unrelated messages
    can never coincidentally concatenate into a false substring match at
    their boundary.

    Never raises: isinstance guards throughout.
    """
    backgrounded_ids: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        for item in _content_items(rec):
            if not (
                isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "Agent"
            ):
                continue
            inp = item.get("input")
            if isinstance(inp, dict) and inp.get("run_in_background") is True:
                tid = item.get("id")
                if isinstance(tid, str) and tid:
                    backgrounded_ids.add(tid)
    if not backgrounded_ids:
        return 0

    user_texts: list[str] = []
    for rec in records:
        if isinstance(rec, dict) and rec.get("type") == "user":
            user_texts.extend(_user_message_texts(rec))

    open_count = 0
    for tid in backgrounded_ids:
        needle = f"<tool-use-id>{tid}</tool-use-id>"
        if not any(needle in text for text in user_texts):
            open_count += 1
    return open_count


def format_in_flight_note(pending_todos: int, open_agents: int) -> str:
    """Return an ASCII caveat naming *pending_todos* pending/in-progress
    TodoWrite items and/or *open_agents* still-open backgrounded Agent
    calls, or "" when both are zero.

    Appended to archive_reminder_message()'s text so the same stderr message
    that instructs Claude to archive also names any approved work still in
    flight and states the tension explicitly (dev-env#1002).

    ASCII-only, like archive_reminder_message() -- Claude Code pipes hook
    output as cp1252 on Windows, so a non-cp1252 character here would raise
    UnicodeEncodeError and silently drop the WHOLE combined stderr message.
    """
    if not pending_todos and not open_agents:
        return ""
    parts = []
    if pending_todos:
        parts.append(f"{pending_todos} pending/in-progress todo item(s)")
    if open_agents:
        parts.append(f"{open_agents} still-running background agent(s)")
    joined = " and ".join(parts)
    return (
        f"Caution: {joined} may still be in flight. Do not archive if that "
        "work is approved and unfinished -- archive_session requires the "
        "user's explicit agreement and must never be called speculatively."
    )


def in_flight_work_note(
    transcript_path_str: str, session_id: str, *, projects: Path | None = None
) -> str:
    """Best-effort in-flight-work caveat for the archive reminder (dev-env#1002).

    Resolves a transcript path -- *transcript_path_str* if it names a real
    file, else `_hookutil.find_transcript(session_id, projects=projects)`
    when *session_id* is truthy, else gives up -- loads its records, and
    returns `format_in_flight_note()`'s caveat, or "" on ANY failure.

    *projects* overrides _hookutil.find_transcript's default search root --
    injectable for offline tests, mirroring find_transcript's own *projects*
    param and this file's *scratch*-param convention.

    Mirrors this file's other best-effort helpers: a missing or malformed
    transcript must never suppress or break the base archive reminder that
    is already firing.
    """
    try:
        path: Path | None = None
        if transcript_path_str:
            candidate = Path(transcript_path_str)
            if candidate.is_file():
                path = candidate
        if path is None and session_id:
            path = _hookutil.find_transcript(session_id, projects=projects)
        if path is None:
            return ""
        records = _hookutil.load_records(path)
        pending = pending_todo_count(records)
        open_agents = open_background_agent_count(records)
        return format_in_flight_note(pending, open_agents)
    except Exception:
        return ""


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


def parse_transcript_path(raw: str) -> str:
    """Return the Stop payload's transcript_path, or "" on empty/malformed/
    non-dict stdin or a missing field. Never raises -- mirrors
    parse_session_id's tolerant-parsing shape (dev-env#1002).
    """
    if not raw:
        return ""
    try:
        return str(json.loads(raw).get("transcript_path") or "")
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
    transcript_path_str = parse_transcript_path(raw)

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
    #
    # Before delivery, the reminder is best-effort augmented (never suppressed,
    # never re-fired) with an in-flight-work caveat naming any pending
    # TodoWrite items or open backgrounded Agent calls (dev-env#1002, ADR-091
    # Amendment 3) -- see in_flight_work_note().
    if not stop_hook_active:
        reminder = consume_stub_pushed_sentinel(session_id)
        if reminder:
            note = in_flight_work_note(transcript_path_str, session_id)
            if note:
                reminder = f"{reminder} {note}"
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
