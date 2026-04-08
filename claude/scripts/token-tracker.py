#!/usr/bin/env python3
"""Claude Code Stop hook — aggregates token usage from a session JSONL and appends to the
running token log.

Claude Code invokes this at session end, passing JSON on stdin:
    {"session_id": "uuid", "transcript_path": "/abs/path/to/session.jsonl", ...}

Outputs:
    ~/.claude/scratch/token-sessions.jsonl   — one record per session (append)
    ~/.claude/scratch/latest-session.json    — latest session (overwrite)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
SCRATCH_DIR = CLAUDE_DIR / "scratch"
TOKEN_LOG = SCRATCH_DIR / "token-sessions.jsonl"
LATEST_SESSION = SCRATCH_DIR / "latest-session.json"

# Pricing per million tokens — Sonnet 4.6 as of 2026-04
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


def find_transcript(session_id: str) -> Path | None:
    projects_dir = CLAUDE_DIR / "projects"
    matches = list(projects_dir.glob(f"**/{session_id}.jsonl"))
    return matches[0] if matches else None


def _count_turns(jsonl_path: Path) -> tuple[dict, int, str]:
    """Return (token totals, turn count, last seen model) from a single JSONL file."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    turn_count = 0
    model = "claude-sonnet-4-6"
    with open(jsonl_path, encoding="utf-8") as f:
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


def aggregate_session(transcript_path: Path) -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    turn_count = 0
    model = "claude-sonnet-4-6"
    first_ts = None
    last_ts = None
    cwd = None
    git_branch = None
    entrypoint = None

    with open(transcript_path, encoding="utf-8") as f:
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


def main() -> None:
    raw = sys.stdin.read().strip()
    hook_data = json.loads(raw) if raw else {}

    session_id = hook_data.get("session_id", "")
    transcript_path_str = hook_data.get("transcript_path", "")

    transcript_path: Path | None = None
    if transcript_path_str:
        p = Path(transcript_path_str)
        if p.exists():
            transcript_path = p

    if transcript_path is None and session_id:
        transcript_path = find_transcript(session_id)

    if transcript_path is None:
        print(f"[token-tracker] ERROR: cannot locate transcript for session {session_id!r}", file=sys.stderr)
        sys.exit(0)  # non-fatal — don't block Claude Code

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
    }

    SCRATCH_DIR.mkdir(exist_ok=True)

    # Skip if already recorded (e.g. backfill ran after hook, or hook fired twice)
    if TOKEN_LOG.exists():
        with open(TOKEN_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("session_id") == session_id:
                        print(f"[token-tracker] {session_id[:8]}… already in log — skipping write")
                        return
                except json.JSONDecodeError:
                    continue

    with open(TOKEN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")

    with open(LATEST_SESSION, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    t = data["tokens"]
    sa_note = (
        f" +{data['subagent_count']} subagent(s)/{data['subagent_turn_count']} turns"
        if data["subagent_count"] > 0
        else ""
    )
    print(
        f"[token-tracker] {session_id[:8]}… | {data['turn_count']} turns{sa_note} | "
        f"in={t['input_tokens']:,} out={t['output_tokens']:,} "
        f"cache_r={t['cache_read_input_tokens']:,} cache_w={t['cache_creation_input_tokens']:,} "
        f"| est. ${estimated_cost:.4f}"
    )


if __name__ == "__main__":
    main()
