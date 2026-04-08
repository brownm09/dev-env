#!/usr/bin/env python3
"""One-time backfill: scan all existing session JSONL files and populate
~/.claude/scratch/token-sessions.jsonl.

Run once after deploying the token-tracker Stop hook to seed historical data.
Safe to re-run — deduplicates on session_id.

Usage:
    python3 backfill-tokens.py [--dry-run]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SCRATCH_DIR = CLAUDE_DIR / "scratch"
TOKEN_LOG = SCRATCH_DIR / "token-sessions.jsonl"

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


def _count_turns_from_jsonl(jsonl_path: Path) -> tuple[dict, int, str]:
    """Return (token totals, turn count, last seen model) from a single JSONL file."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    turn_count = 0
    model = "claude-sonnet-4-6"
    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
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
                    turn_count += 1
                    if msg.get("model"):
                        model = msg["model"]
    return totals, turn_count, model


def process_transcript(transcript_path: Path) -> dict | None:
    """Return a session summary dict or None if no usage data found."""
    totals, turn_count, model = _count_turns_from_jsonl(transcript_path)
    first_ts = last_ts = None
    session_id = cwd = git_branch = entrypoint = None

    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if session_id is None and record.get("sessionId"):
                session_id = record["sessionId"]
            if cwd is None and record.get("cwd"):
                cwd = record["cwd"]
            if git_branch is None and record.get("gitBranch"):
                git_branch = record["gitBranch"]
            if entrypoint is None and record.get("entrypoint"):
                entrypoint = record["entrypoint"]

            if record.get("type") == "assistant":
                ts = record.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

    if turn_count == 0:
        return None  # no useful data

    # Aggregate subagent JSONLs (session-uuid/subagents/agent-*.jsonl)
    subagents_dir = transcript_path.with_suffix("") / "subagents"
    subagent_count = 0
    subagent_turn_count = 0
    if subagents_dir.is_dir():
        for sa_path in sorted(subagents_dir.glob("agent-*.jsonl")):
            sa_totals, sa_turns, _ = _count_turns_from_jsonl(sa_path)
            for key in totals:
                totals[key] += sa_totals[key]
            subagent_turn_count += sa_turns
            subagent_count += 1

    prices = get_pricing(model)
    cost = compute_cost(totals, prices)

    return {
        "session_id": session_id or transcript_path.stem,
        "transcript_path": str(transcript_path),
        "model": model,
        "cwd": cwd,
        "git_branch": git_branch,
        "entrypoint": entrypoint,
        "first_turn_ts": first_ts,
        "last_turn_ts": last_ts,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "turn_count": turn_count,
        "subagent_count": subagent_count,
        "subagent_turn_count": subagent_turn_count,
        "tokens": totals,
        "estimated_cost_usd": round(cost, 6),
        "backfilled": True,
    }


def load_existing_ids() -> set[str]:
    if not TOKEN_LOG.exists():
        return set()
    ids = set()
    with open(TOKEN_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
                if s.get("session_id"):
                    ids.add(s["session_id"])
            except json.JSONDecodeError:
                continue
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill token-sessions.jsonl from existing JSONL files")
    parser.add_argument("--dry-run", action="store_true", help="Print summaries without writing")
    args = parser.parse_args()

    projects_dir = CLAUDE_DIR / "projects"
    # Only process root-level session files (not subagent files)
    transcripts = sorted(
        p for p in projects_dir.glob("*/*.jsonl")
        if "subagents" not in str(p)
    )
    # Also catch top-level (some sessions land directly in projects/)
    transcripts += sorted(
        p for p in projects_dir.glob("*.jsonl")
    )

    existing_ids = load_existing_ids()
    print(f"Found {len(transcripts)} transcript(s). {len(existing_ids)} already logged.")

    new_sessions = []
    for path in transcripts:
        try:
            summary = process_transcript(path)
        except Exception as e:
            print(f"  SKIP {path.name}: {e}", file=sys.stderr)
            continue

        if summary is None:
            print(f"  SKIP {path.name}: no assistant turns")
            continue

        sid = summary["session_id"]
        if sid in existing_ids:
            print(f"  DUP  {path.name}: already in log")
            continue

        new_sessions.append(summary)
        t = summary["tokens"]
        print(
            f"  ADD  {path.name[:36]} | {summary['turn_count']} turns | "
            f"in={t['input_tokens']:,} out={t['output_tokens']:,} "
            f"cache_r={t['cache_read_input_tokens']:,} cache_w={t['cache_creation_input_tokens']:,} "
            f"| ${summary['estimated_cost_usd']:.4f}"
        )

    if args.dry_run:
        print(f"\n[dry-run] Would append {len(new_sessions)} session(s) — not writing.")
        return

    if not new_sessions:
        print("Nothing new to write.")
        return

    SCRATCH_DIR.mkdir(exist_ok=True)
    with open(TOKEN_LOG, "a", encoding="utf-8") as f:
        for s in new_sessions:
            f.write(json.dumps(s) + "\n")

    print(f"\nAppended {len(new_sessions)} session(s) to {TOKEN_LOG}")


if __name__ == "__main__":
    main()
