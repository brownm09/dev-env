#!/usr/bin/env python3
"""Read ~/.claude/scratch/token-sessions.jsonl and produce a formatted token-usage report.

Usage:
    python3 token-report.py                  # all sessions
    python3 token-report.py --date 2026-04-08  # sessions for one calendar date (UTC)
    python3 token-report.py --days 7         # last N days
    python3 token-report.py --project engineering-journal  # filter by cwd substring
    python3 token-report.py --format json    # raw JSON instead of markdown
    python3 token-report.py --latest         # single latest session only
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TOKEN_LOG = Path.home() / ".claude" / "scratch" / "token-sessions.jsonl"


def load_sessions() -> list[dict]:
    if not TOKEN_LOG.exists():
        return []
    sessions = []
    with open(TOKEN_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sessions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return sessions


def session_date(s: dict) -> str:
    """Return YYYY-MM-DD for the session's first turn (UTC)."""
    ts = s.get("first_turn_ts") or s.get("recorded_at", "")
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.rstrip("Z") + "+00:00")
        return dt.date().isoformat()
    except ValueError:
        return ""


def filter_sessions(sessions: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.latest:
        return sessions[-1:] if sessions else []

    if args.date:
        target = args.date
        sessions = [s for s in sessions if session_date(s) == target]

    if args.days:
        cutoff = (date.today() - timedelta(days=args.days)).isoformat()
        sessions = [s for s in sessions if session_date(s) >= cutoff]

    if args.project:
        q = args.project.lower()
        sessions = [
            s for s in sessions
            if q in (s.get("cwd") or "").lower()
            or q in (s.get("transcript_path") or "").lower()
        ]

    return sessions


def fmt_k(n: int) -> str:
    """Format large token counts as e.g. 1,234k or 45."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def short_path(path_str: str | None) -> str:
    if not path_str:
        return ""
    p = Path(path_str)
    # Return last 2 components of the path for readability
    parts = p.parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else str(p)


def render_markdown(sessions: list[dict]) -> str:
    if not sessions:
        return "_No sessions found._\n"

    lines = []
    lines.append("| Date | Session | Branch | Turns | Input | Output | Cache R | Cache W | Est. Cost |")
    lines.append("|------|---------|--------|------:|------:|-------:|--------:|--------:|----------:|")

    totals = dict(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                  cache_creation_input_tokens=0, turns=0, cost=0.0)

    for s in sessions:
        sid = (s.get("session_id") or "")[:8] + "…"
        d = session_date(s)
        branch = s.get("git_branch") or "—"
        turns = s.get("turn_count", 0)
        t = s.get("tokens", {})
        inp = t.get("input_tokens", 0)
        out = t.get("output_tokens", 0)
        cr = t.get("cache_read_input_tokens", 0)
        cw = t.get("cache_creation_input_tokens", 0)
        cost = s.get("estimated_cost_usd", 0.0)

        lines.append(
            f"| {d} | `{sid}` | {branch} | {turns} "
            f"| {fmt_k(inp)} | {fmt_k(out)} | {fmt_k(cr)} | {fmt_k(cw)} "
            f"| ${cost:.4f} |"
        )

        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["cache_read_input_tokens"] += cr
        totals["cache_creation_input_tokens"] += cw
        totals["turns"] += turns
        totals["cost"] += cost

    if len(sessions) > 1:
        lines.append(
            f"| **Total** | {len(sessions)} sessions | — | {totals['turns']} "
            f"| {fmt_k(totals['input_tokens'])} | {fmt_k(totals['output_tokens'])} "
            f"| {fmt_k(totals['cache_read_input_tokens'])} | {fmt_k(totals['cache_creation_input_tokens'])} "
            f"| **${totals['cost']:.4f}** |"
        )

    return "\n".join(lines) + "\n"


def render_summary(sessions: list[dict]) -> str:
    """One-line summary suitable for embedding in journal prose."""
    if not sessions:
        return "No sessions found."
    totals = dict(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0, cost=0.0, turns=0)
    for s in sessions:
        t = s.get("tokens", {})
        totals["input_tokens"] += t.get("input_tokens", 0)
        totals["output_tokens"] += t.get("output_tokens", 0)
        totals["cache_read"] += t.get("cache_read_input_tokens", 0)
        totals["cache_write"] += t.get("cache_creation_input_tokens", 0)
        totals["cost"] += s.get("estimated_cost_usd", 0.0)
        totals["turns"] += s.get("turn_count", 0)
    return (
        f"{len(sessions)} session(s), {totals['turns']} turns | "
        f"in={fmt_k(totals['input_tokens'])} out={fmt_k(totals['output_tokens'])} "
        f"cache_r={fmt_k(totals['cache_read'])} cache_w={fmt_k(totals['cache_write'])} | "
        f"est. ${totals['cost']:.4f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Code token usage report")
    parser.add_argument("--date", help="Filter by YYYY-MM-DD (UTC session start)")
    parser.add_argument("--days", type=int, help="Last N calendar days")
    parser.add_argument("--project", help="Filter by cwd/path substring")
    parser.add_argument("--latest", action="store_true", help="Show latest session only")
    parser.add_argument("--format", choices=["markdown", "json", "summary"], default="markdown")
    args = parser.parse_args()

    sessions = load_sessions()
    sessions = filter_sessions(sessions, args)

    if args.format == "json":
        print(json.dumps(sessions, indent=2))
    elif args.format == "summary":
        print(render_summary(sessions))
    else:
        print(render_markdown(sessions))


if __name__ == "__main__":
    main()
