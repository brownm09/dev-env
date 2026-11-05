#!/usr/bin/env python3
"""Claude Code Stop hook — aggregates token usage from a session JSONL and appends to the
running token log.

Claude Code invokes this at session end, passing JSON on stdin:
    {"session_id": "uuid", "transcript_path": "/abs/path/to/session.jsonl", ...}

Outputs:
    ~/.claude/scratch/token-sessions.jsonl   — one record per session (append)
    ~/.claude/scratch/latest-session.json    — latest session (overwrite)
"""
import _hookout
import _hookutil
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from _token_pricing import SYNTHETIC_MODEL, compute_cost, flat_usage, get_pricing

CLAUDE_DIR = Path.home() / ".claude"
SCRATCH_DIR = CLAUDE_DIR / "scratch"
TOKEN_LOG = SCRATCH_DIR / "token-sessions.jsonl"
LATEST_SESSION = SCRATCH_DIR / "latest-session.json"

# Per-session sentinel prefix for the transcript-locate diagnostic, so it toasts at
# most once per session — a Stop hook fires at every turn-end, and a persistently
# unlocatable transcript would otherwise re-toast the user every turn.
LOCATE_FAIL_PREFIX = "token-tracker-locate-fail-"

def format_locate_error(session_id: str) -> str:
    """The user-facing diagnostic when the session transcript can't be located.

    Delivered via _hookout as a systemMessage (exit 0): token-tracker is a Stop
    hook and must never block the stop, and a Stop hook's exit-0 stdout/stderr are
    invisible to Claude and the user, so systemMessage is the only channel that
    surfaces this failure (ADR-103). ASCII by construction (session_id is a UUID),
    so it can't vanish under Claude Code's cp1252 hook-output pipe on Windows."""
    return (
        f"[token-tracker] Could not locate the transcript for session "
        f"{session_id!r}; token usage was not recorded for this session."
    )


def should_advise_locate_failure(session_id: str, scratch=None) -> bool:
    """True the FIRST time a transcript-locate failure is seen this session, else
    False — a once-per-session guard.

    A Stop hook fires at every turn-end, so a persistently unlocatable transcript
    would re-toast the user every turn without this guard (the same per-turn-spam
    reason the status echoes are dropped rather than routed to systemMessage).
    Best-effort: with no session_id (can't dedupe) or an unwritable scratch dir, err
    toward advising — a rare duplicate toast in a degraded state beats a silently
    lost diagnostic. *scratch* overrides SCRATCH for offline tests."""
    if not session_id:
        return True
    sentinel = _hookutil.sentinel_path(LOCATE_FAIL_PREFIX, session_id, scratch=scratch)
    if sentinel.exists():
        return False
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("")
    except Exception:
        pass
    return True


def _iter_file_lines_safely(path: Path):
    """Yield stripped, non-blank lines from path, one at a time.

    Best-effort like read_token_log_lines: stops silently (does not raise) on an
    unreadable file (OSError) or a non-UTF-8 byte anywhere in it (UnicodeDecodeError)
    — whatever was already yielded before the failure stands, so a caller
    accumulating totals as it consumes this keeps its partial progress rather than
    losing everything. Shared by _count_turns and aggregate_session, which each used
    to paste this same try/except scaffold around their own open()+for-loop
    (dev-env#804 review: a future third call site or exception-set change now needs
    only one edit)."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line
    except (OSError, UnicodeDecodeError):
        return


def _count_turns(jsonl_path: Path) -> tuple[dict, int, str]:
    """Return (token totals, turn count, last seen model) from a single JSONL file.

    Best-effort: an unreadable file (OSError) or a non-UTF-8 byte anywhere in it
    (UnicodeDecodeError) stops the scan rather than crashing aggregate_session/main()
    — whatever was accumulated before the failure (zero, if it failed immediately) is
    returned rather than propagating (dev-env#804)."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_write_1h_input_tokens": 0,
    }
    turn_count = 0
    model = ""
    for line in _iter_file_lines_safely(jsonl_path):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "assistant":
            msg = record.get("message", {})
            usage = flat_usage(msg.get("usage", {}))
            if usage:
                for key in totals:
                    totals[key] += usage.get(key, 0)
                turn_count += 1
                if msg.get("model") and msg["model"] != SYNTHETIC_MODEL:
                    model = msg["model"]
    return totals, turn_count, model


def aggregate_session(transcript_path: Path) -> dict:
    """Aggregate one session's token usage from its transcript JSONL.

    Best-effort like _count_turns: an unreadable file (OSError) or a non-UTF-8 byte
    anywhere in it (UnicodeDecodeError) stops the scan rather than crashing main()
    before it can record anything for this session — whatever was accumulated before
    the failure is kept, and subagent aggregation below still runs (dev-env#804)."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_write_1h_input_tokens": 0,
    }
    turn_count = 0
    model = ""
    first_ts = None
    last_ts = None
    cwd = None
    git_branch = None
    entrypoint = None

    for line in _iter_file_lines_safely(transcript_path):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "assistant":
            msg = record.get("message", {})
            usage = flat_usage(msg.get("usage", {}))
            if usage:
                for key in totals:
                    totals[key] += usage.get(key, 0)
                turn_count += 1
                if msg.get("model") and msg["model"] != SYNTHETIC_MODEL:
                    model = msg["model"]
            ts = record.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
        if cwd is None and record.get("cwd"):
            cwd = record["cwd"]
        if git_branch is None and record.get("gitBranch"):
            git_branch = record["gitBranch"]
        if entrypoint is None and record.get("entrypoint"):
            entrypoint = record["entrypoint"]

    # Aggregate subagent JSONLs (session-uuid/subagents/agent-*.jsonl)
    subagents_dir = transcript_path.with_suffix("") / "subagents"
    subagent_count = 0
    subagent_turn_count = 0
    if subagents_dir.is_dir():
        for sa_path in sorted(subagents_dir.glob("agent-*.jsonl")):
            sa_totals, sa_turns, _ = _count_turns(sa_path)
            for key in totals:
                totals[key] += sa_totals[key]
            subagent_turn_count += sa_turns
            subagent_count += 1

    return {
        "model": model,
        "cwd": cwd,
        "git_branch": git_branch,
        "entrypoint": entrypoint,
        "first_turn_ts": first_ts,
        "last_turn_ts": last_ts,
        "turn_count": turn_count,
        "subagent_count": subagent_count,
        "subagent_turn_count": subagent_turn_count,
        "tokens": totals,
    }


def read_token_log_lines(path: Path) -> list[str]:
    """Best-effort read of TOKEN_LOG's lines for the existing-session dedup scan in main().

    Returns [] for a missing/unreadable file (OSError, incl. FileNotFoundError and
    IsADirectoryError) or non-UTF-8 bytes (UnicodeDecodeError) — a corrupted or
    mid-write log degrades to a fresh log rather than crashing main() before it can
    record the current session's summary (dev-env#804). This helper does no JSON
    parsing, so it only needs these two exception types, not _bash_state.read_state's
    broader ValueError catch (which also covers json.JSONDecodeError there)."""
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return []


def main() -> None:
    _hookutil.record_heartbeat("token-tracker")
    _hookutil.cleanup_stale_sentinels(LOCATE_FAIL_PREFIX)

    raw = sys.stdin.read().strip()
    hook_data = json.loads(raw) if raw else {}

    session_id = hook_data.get("session_id", "")
    transcript_path = _hookutil.resolve_transcript(hook_data.get("transcript_path", ""), session_id)
    if transcript_path is None:
        # A Stop hook's exit-0 stdout/stderr are invisible, so surface the
        # transcript-locate failure via the shared _hookout user channel (ADR-103) —
        # at most once per session (should_advise_locate_failure), since a Stop hook
        # fires every turn-end and a persistently unlocatable transcript would
        # otherwise re-toast every turn. emit_advisory exits 0; the explicit
        # sys.exit(0) keeps the "nothing to aggregate, never block" stop visible even
        # when the once-guard suppresses the emit (and guards a future NoReturn break).
        if should_advise_locate_failure(session_id):
            _hookout.emit_advisory("Stop", format_locate_error(session_id), audience="user")
        sys.exit(0)

    data = aggregate_session(transcript_path)
    prices = get_pricing(data["model"])
    estimated_cost = compute_cost(data["tokens"], prices)

    summary = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "model": data["model"],
        "cwd": data["cwd"],
        "git_branch": data["git_branch"],
        "entrypoint": data["entrypoint"],
        "first_turn_ts": data["first_turn_ts"],
        "last_turn_ts": data["last_turn_ts"],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "turn_count": data["turn_count"],
        "subagent_count": data["subagent_count"],
        "subagent_turn_count": data["subagent_turn_count"],
        "tokens": data["tokens"],
        "estimated_cost_usd": round(estimated_cost, 6),
        "model_unpriced": prices is None,
    }

    SCRATCH_DIR.mkdir(exist_ok=True)

    # If a prior record exists for this session_id, update it only if this firing has
    # a later last_turn_ts (session was paused and resumed) or more subagents.
    # This handles claude-desktop's pause/resume behavior where the same session_id
    # can accumulate more Agent calls after the first Stop event fires.
    existing_line_idx: int | None = None
    existing_last_turn: str = ""
    lines = read_token_log_lines(TOKEN_LOG)
    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
            if rec.get("session_id") == session_id:
                existing_line_idx = i
                existing_last_turn = rec.get("last_turn_ts") or ""
                break
        except json.JSONDecodeError:
            continue

    new_last_turn = summary.get("last_turn_ts") or ""
    if existing_line_idx is not None:
        if new_last_turn <= existing_last_turn:
            # Already in the log with no new turns — nothing to write. (The former
            # stdout echo here was invisible on a Stop hook and fired every turn-end;
            # a systemMessage in its place would be per-turn toast spam, so drop it.)
            return
        # Update in-place: replace the existing line, rewrite the file
        lines[existing_line_idx] = json.dumps(summary) + "\n"
        TOKEN_LOG.write_text("".join(lines), encoding="utf-8")
    elif lines:
        with open(TOKEN_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
    else:
        # No existing lines to append after -- missing, empty, or unreadable/corrupt
        # log (read_token_log_lines can't distinguish these; all three return []).
        # Write fresh rather than append: appending onto a non-UTF-8 log would leave
        # it permanently undecodable, so every future run would keep getting []
        # here too, silently breaking dedup for every session forever (dev-env#804
        # review).
        TOKEN_LOG.write_text(json.dumps(summary) + "\n", encoding="utf-8")

    with open(LATEST_SESSION, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # The durable record is the token log + latest-session.json written above. The
    # former per-turn status echo here (`recorded | N turns | in=.. out=.. | $cost`)
    # was invisible on a Stop hook and fired every turn-end, so a systemMessage in
    # its place would be per-turn toast spam — drop it entirely.


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
