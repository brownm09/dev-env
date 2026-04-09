#!/usr/bin/env python3
"""Read ~/.claude/scratch/token-sessions.jsonl and produce a formatted token-usage report.

Usage:
    python3 token-report.py                  # all sessions
    python3 token-report.py --date 2026-04-08  # sessions for one calendar date (UTC)
    python3 token-report.py --days 7         # last N days
    python3 token-report.py --project engineering-journal  # filter by cwd substring
    python3 token-report.py --format json    # raw JSON instead of markdown
    python3 token-report.py --latest         # single latest session only
    python3 token-report.py --show-subagents # break out per-subagent token contributions
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TOKEN_LOG = Path.home() / ".claude" / "scratch" / "token-sessions.jsonl"
CLAUDE_DIR = Path.home() / ".claude"

PRICING = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-haiku-4-5":  {"input": 0.80, "output": 4.00,  "cache_read": 0.08, "cache_write": 1.00},
}
_DEFAULT_PRICES = PRICING["claude-sonnet-4-6"]


def get_pricing(model: str) -> dict:
    for key, prices in PRICING.items():
        if key in model:
            return prices
    return _DEFAULT_PRICES


def compute_cost(usage: dict, prices: dict) -> float:
    return (
        usage.get("input_tokens", 0)               * prices["input"]       / 1_000_000
        + usage.get("output_tokens", 0)            * prices["output"]      / 1_000_000
        + usage.get("cache_read_input_tokens", 0)  * prices["cache_read"]  / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * prices["cache_write"] / 1_000_000
    )


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


def read_subagents(transcript_path_str: str) -> list[dict]:
    """Read per-subagent token data from the subagents/ directory at report time.

    Returns a list of dicts: {name, agent_type, description, turns, tokens, cost, captured}.
    `captured` is True if the subagent existed when the Stop hook fired (i.e. was counted
    in the session totals); False means it ran after the session was paused and was missed.
    """
    if not transcript_path_str:
        return []
    transcript_path = Path(transcript_path_str)
    subagents_dir = transcript_path.with_suffix("") / "subagents"
    if not subagents_dir.is_dir():
        return []

    results = []
    for sa_path in sorted(subagents_dir.glob("agent-*.jsonl")):
        meta_path = sa_path.with_name(sa_path.name.replace(".jsonl", ".meta.json"))
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                pass

        totals = {"input_tokens": 0, "output_tokens": 0,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        turns = 0
        model = "claude-sonnet-4-6"
        try:
            with open(sa_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("type") == "assistant":
                        msg = record.get("message", {})
                        usage = msg.get("usage", {})
                        if usage:
                            for key in totals:
                                totals[key] += usage.get(key, 0)
                            turns += 1
                            if msg.get("model"):
                                model = msg["model"]
        except OSError:
            continue

        prices = get_pricing(model)
        cost = compute_cost(totals, prices)
        results.append({
            "name": sa_path.stem,
            "agent_type": meta.get("agentType", "unknown"),
            "description": meta.get("description", ""),
            "turns": turns,
            "tokens": totals,
            "cost": cost,
        })
    return results


def render_subagents_table(session: dict) -> str:
    """Return a markdown sub-table of per-subagent contributions for one session.

    Reads from filesystem at report time so it reflects the current directory state,
    which may include agents captured AFTER the Stop hook fired (the pause/resume case).
    Flags uncaptured agents with a warning marker.
    """
    subagents = read_subagents(session.get("transcript_path") or "")
    if not subagents:
        return ""

    captured_turns = session.get("subagent_turn_count", 0)
    # Determine which agents were captured by matching cumulative turn sums in sorted order.
    # The hook sums agents in sorted() order and stops at the first Stop event, so all
    # agents whose mtime <= the session's last_turn_ts were captured.
    last_turn_ts = session.get("last_turn_ts") or ""

    lines = []
    lines.append(
        "  | Agent | Type | Description | Turns | Input | Output | Est. Cost | Status |"
    )
    lines.append(
        "  |-------|------|-------------|------:|------:|-------:|----------:|--------|"
    )
    for sa in subagents:
        t = sa["tokens"]
        # Mark as captured if cumulatively counted (heuristic: check filesystem mtime vs last_turn_ts)
        sa_path = None
        if session.get("transcript_path"):
            tp = Path(session["transcript_path"])
            candidate = tp.with_suffix("") / "subagents" / (sa["name"] + ".jsonl")
            if candidate.exists():
                sa_path = candidate
        captured = "ok"
        if sa_path and last_turn_ts:
            try:
                mtime_utc = datetime.fromtimestamp(sa_path.stat().st_mtime, tz=timezone.utc).isoformat()
                if mtime_utc > last_turn_ts.rstrip("Z") + "+00:00":
                    captured = "MISSED"
            except OSError:
                pass
        lines.append(
            f"  | `{sa['name'][-8:]}` | {sa['agent_type']} | {sa['description'][:40]} "
            f"| {sa['turns']} | {fmt_k(t['input_tokens'])} | {fmt_k(t['output_tokens'])} "
            f"| ${sa['cost']:.4f} | {captured} |"
        )
    return "\n".join(lines)


def render_markdown(sessions: list[dict], show_subagents: bool = False) -> str:
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
        sa_count = s.get("subagent_count", 0)
        sa_suffix = f" (+{sa_count}sa)" if sa_count > 0 else ""

        lines.append(
            f"| {d} | `{sid}` | {branch} | {turns}{sa_suffix} "
            f"| {fmt_k(inp)} | {fmt_k(out)} | {fmt_k(cr)} | {fmt_k(cw)} "
            f"| ${cost:.4f} |"
        )

        if show_subagents and sa_count > 0:
            sub_table = render_subagents_table(s)
            if sub_table:
                lines.append(sub_table)

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
    parser.add_argument(
        "--show-subagents", action="store_true",
        help="Break out per-subagent token contributions for sessions that used the Agent tool. "
             "Reads from filesystem at report time; marks agents missed by the Stop hook as ⚠ missed.",
    )
    args = parser.parse_args()

    sessions = load_sessions()
    sessions = filter_sessions(sessions, args)

    if args.format == "json":
        print(json.dumps(sessions, indent=2))
    elif args.format == "summary":
        print(render_summary(sessions))
    else:
        print(render_markdown(sessions, show_subagents=getattr(args, "show_subagents", False)))


if __name__ == "__main__":
    main()
