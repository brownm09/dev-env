#!/usr/bin/env python3
"""Read ~/.claude/scratch/token-sessions.jsonl and produce a formatted token-usage report.

Usage:
    py -3 token-report.py                  # all sessions
    py -3 token-report.py --date 2026-04-08  # sessions for one calendar date (UTC)
    py -3 token-report.py --days 7         # last N days
    py -3 token-report.py --project engineering-journal  # filter by cwd substring
    py -3 token-report.py --format json    # raw JSON instead of markdown
    py -3 token-report.py --latest         # single latest session only
    py -3 token-report.py --show-subagents # break out per-subagent token contributions
    py -3 token-report.py --attribution --since 2026-10-22 --until 2026-11-05
                                           # where spend went, read from transcripts
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from _hookio import split_top_level
from _token_pricing import (
    SYNTHETIC_MODEL,
    compute_cost,
    cost_buckets,
    flat_usage,
    get_pricing,
    has_tokens,
    is_standard_rate,
)

TOKEN_LOG = Path.home() / ".claude" / "scratch" / "token-sessions.jsonl"
CLAUDE_DIR = Path.home() / ".claude"
TRANSCRIPT_ROOT = CLAUDE_DIR / "projects"

# Attribution report labels. These only sort spend into rows, and no check acts on them.
CONTEXT_BANDS = [
    (200_000, "under 200k"),
    (400_000, "200k to 400k"),
    (600_000, "400k to 600k"),
    (800_000, "600k to 800k"),
    (float("inf"), "800k and over"),
]
IDLE_GAP_BANDS = [
    (300, "under 5 minutes"),
    (3600, "5 to 60 minutes"),
    (float("inf"), "over 60 minutes"),
]
REWRITE_MIN_TOKENS = 50_000  # smaller writes are ordinary growth, whatever the cache read did
PREFIX_LOST_RATIO = 0.5  # a cache read below this share of the last context means the cache expired
LARGE_READ_LINES = 350  # the threshold the Spotify shunt plugin redirects at
# Tool results carry no token count, so their size is estimated. Measured on 2,278 single-result
# turns of at least 2,000 characters on 2026-11-05: context grew by about 1 token per 2.6 characters.
CHARS_PER_TOKEN = 2.6
SHELL_READERS = {"cat", "head", "tail", "less", "more", "Get-Content"}
SAFE_MODEL_LABEL = re.compile(r"[A-Za-z0-9._:\[\]-]{1,64}")
COMPACTION_ROW = "First call after a compaction"
DEFAULT_WINDOW_DAYS = 14


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
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                  "cache_write_1h_input_tokens": 0}
        turns = 0
        model = ""
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
                        usage = flat_usage(msg.get("usage", {}))
                        if usage:
                            for key in totals:
                                totals[key] += usage.get(key, 0)
                            turns += 1
                            if msg.get("model") and msg["model"] != SYNTHETIC_MODEL:
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
            "unpriced": prices is None,
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
            f"| {'unpriced' if sa['unpriced'] else '$' + format(sa['cost'], '.4f')} | {captured} |"
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
    unpriced = sum(1 for s in sessions if s.get("model_unpriced"))

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
            f"| {'unpriced' if s.get('model_unpriced') else f'${cost:.4f}'} |"
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
            f"| **${totals['cost']:.4f}**{f' (excludes {unpriced} unpriced)' if unpriced else ''} |"
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
    unpriced = sum(1 for s in sessions if s.get("model_unpriced"))
    return (
        f"{len(sessions)} session(s), {totals['turns']} turns | "
        f"in={fmt_k(totals['input_tokens'])} out={fmt_k(totals['output_tokens'])} "
        f"cache_r={fmt_k(totals['cache_read'])} cache_w={fmt_k(totals['cache_write'])} | "
        f"est. ${totals['cost']:.4f}{f' (excludes {unpriced} unpriced)' if unpriced else ''}"
    )


def _classify_tool_result(name: str, tool_input: dict, text: str) -> str:
    """Name the attribution row for a tool result. Only its size and shape are used."""
    lines = text.count("\n") + 1
    if name == "Read" and not (tool_input.get("offset") or tool_input.get("limit")):
        return f"Full file reads over {LARGE_READ_LINES} lines" if lines > LARGE_READ_LINES else "Other reads"
    if name == "Read":
        return "Other reads"
    if name in ("Bash", "PowerShell"):
        command = str(tool_input.get("command", ""))
        words = command.split()
        if words and words[0] in SHELL_READERS and len(split_top_level(command, split_pipe=True)) == 1:
            return f"Shell file reads over {LARGE_READ_LINES} lines" if lines > LARGE_READ_LINES else "Shell output"
        return "Shell output"
    return "Agent results" if name == "Agent" else "Other tools"


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def _add_record(record: dict, transcript: dict, tool_uses: dict, call_index_by_id: dict) -> None:
    """Add one transcript record. A wrong-shaped record raises and read_transcript skips it."""
    calls = transcript["calls"]
    if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
        transcript["compactions"].add(len(calls))
        return
    msg = record.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if record.get("type") == "assistant":
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_uses[block.get("id")] = (block.get("name") or "", block.get("input") or {})
        usage = flat_usage(msg.get("usage"))
        if not usage or not has_tokens(usage) or msg.get("model") == SYNTHETIC_MODEL:
            return
        model = msg.get("model")
        call = {
            "id": msg.get("id"),
            "model": model if isinstance(model, str) else "",
            "usage": usage,
            "ts": _parse_ts(record.get("timestamp")),
        }
        message_id = msg.get("id")
        if message_id in call_index_by_id:
            calls[call_index_by_id[message_id]] = call
        else:
            if message_id:
                call_index_by_id[message_id] = len(calls)
            calls.append(call)
    elif record.get("type") == "user" and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                name, tool_input = tool_uses.get(block.get("tool_use_id"), ("", {}))
                text = _tool_result_text(block.get("content"))
                transcript["results"].append((
                    len(calls),
                    _classify_tool_result(name, tool_input, text),
                    len(text) / CHARS_PER_TOKEN,
                ))


def read_transcript(path: Path) -> dict | None:
    """Return one transcript's API calls, tool results and compactions, or None if unreadable.

    A response spans several records that repeat its usage, so each message id is one
    call and its last record with tokens wins, because that one has the final output
    count. Tool results keep only their row and estimated tokens, so no transcript text
    is retained. `compactions` holds the index of the first call after each compaction.
    """
    transcript: dict = {"calls": [], "results": [], "compactions": set(), "skipped_lines": 0}
    tool_uses: dict = {}
    call_index_by_id: dict = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    _add_record(json.loads(line), transcript, tool_uses, call_index_by_id)
                except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                    transcript["skipped_lines"] += 1
    except OSError:
        return None
    return transcript


def _parse_ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _context_tokens(usage: dict) -> int:
    return (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )


def _band(value: float, bands: list[tuple]) -> str:
    return next(label for limit, label in bands if value < limit)


def _model_label(model: str) -> str:
    """Return a model id that is safe to print, since this field comes from a transcript."""
    if not model:
        return "(no model)"
    return model if SAFE_MODEL_LABEL.fullmatch(model) else "(unrecognised)"


def _starts_after(path: Path, until: float) -> bool:
    """Return True if a transcript's first timestamp is at or after `until`."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(record.get("timestamp")) if isinstance(record, dict) else None
                if ts is not None:
                    return ts >= until
    except OSError:
        pass
    return False


def attribute_spend(paths: list[Path], since: float, until: float) -> dict:
    """Attribute the cost of every API call with since <= timestamp < until.

    Resumed sessions copy earlier calls into a new transcript, so a message id is priced
    once across all files. Main transcripts go first, so a call shared with a subagent
    transcript counts as main-session spend.
    """
    tokens: Counter = Counter()
    buckets: Counter = Counter()
    by_model: Counter = Counter()
    by_band: Counter = Counter()
    band_calls: Counter = Counter()
    by_scope: Counter = Counter()
    rewrite_cost: Counter = Counter()
    rewrite_count: Counter = Counter()
    compaction_cost: Counter = Counter()
    compaction_count: Counter = Counter()
    tool_cost: Counter = Counter()
    tool_count: Counter = Counter()
    unpriced: Counter = Counter()
    floor_cost = 0.0
    calls_total = 0
    first_contexts: list[int] = []
    seen_ids: set = set()
    files_read = 0
    files_unreadable = 0
    lines_skipped = 0

    for path in sorted(paths, key=lambda p: ("subagents" in p.parts, str(p))):
        if _starts_after(path, until):
            continue
        transcript = read_transcript(path)
        if transcript is None:
            files_unreadable += 1
            continue
        files_read += 1
        lines_skipped += transcript["skipped_lines"]
        is_subagent = "subagents" in path.parts
        calls = transcript["calls"]
        compactions = transcript["compactions"]
        contexts = [_context_tokens(c["usage"]) for c in calls]
        starting = next((ctx for ctx in contexts if ctx > 0), 0)
        counted = []
        for call in calls:
            in_window = call["ts"] is not None and since <= call["ts"] < until
            counted.append(in_window and call["id"] not in seen_ids)
            if counted[-1] and call["id"]:
                seen_ids.add(call["id"])
        if not is_subagent and counted and counted[0] and starting:
            first_contexts.append(starting)

        for i, call in enumerate(calls):
            if not counted[i]:
                continue
            usage = call["usage"]
            calls_total += 1
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                tokens[key] += usage.get(key, 0)
            prices = get_pricing(call["model"], contexts[i])
            if prices is None or not is_standard_rate(usage):
                label = _model_label(call["model"])
                if prices is not None:
                    label += " (fast mode or US-only inference)"
                unpriced[label] += contexts[i] + usage.get("output_tokens", 0)
                continue
            cost = cost_buckets(usage, prices)
            total = sum(cost.values())
            buckets.update(cost)
            by_model[call["model"]] += total
            by_scope["Subagents" if is_subagent else "Main sessions"] += total
            if is_subagent:
                continue
            band = _band(contexts[i], CONTEXT_BANDS)
            by_band[band] += total
            band_calls[band] += 1
            floor_cost += min(contexts[i], starting) * prices["cache_read"] / 1_000_000
            if i in compactions:
                compaction_cost[COMPACTION_ROW] += cost["cache_write"]
                compaction_count[COMPACTION_ROW] += 1
                continue
            writes = usage.get("cache_creation_input_tokens", 0)
            previous_ts = calls[i - 1]["ts"] if i else None
            prefix_lost = i and usage.get("cache_read_input_tokens", 0) < PREFIX_LOST_RATIO * contexts[i - 1]
            if prefix_lost and writes >= REWRITE_MIN_TOKENS and call["ts"] and previous_ts:
                gap = _band(call["ts"] - previous_ts, IDLE_GAP_BANDS)
                rewrite_cost[gap] += cost["cache_write"]
                rewrite_count[gap] += 1

        for index, row, size in transcript["results"]:
            # A compaction right after a result means no call ever carried it.
            if index >= len(calls) or index in compactions:
                continue
            prices = get_pricing(calls[index]["model"], contexts[index])
            if prices is None:
                continue
            usage = calls[index]["usage"]
            hourly = usage.get("cache_write_1h_input_tokens", 0) * 2 >= usage.get("cache_creation_input_tokens", 0)
            write_price = prices["cache_write_1h"] if hourly else prices["cache_write"]
            end = min((c for c in compactions if c > index), default=len(calls))
            reads = sum(counted[j] for j in range(index + 1, end))
            if counted[index]:
                tool_count[row] += 1
            tool_cost[row] += size * ((write_price if counted[index] else 0) + prices["cache_read"] * reads) / 1_000_000

    total_cost = sum(buckets.values())

    def rows(costs: Counter, order=None, counts: Counter | None = None) -> list[dict]:
        names = order if order is not None else [name for name, _ in costs.most_common()]
        return [
            {
                "name": name,
                "cost_usd": round(costs[name], 2),
                "share_pct": round(100 * costs[name] / total_cost, 1) if total_cost else 0.0,
                **({"count": counts[name]} if counts is not None else {}),
            }
            for name in names
        ]

    return {
        "calls": calls_total,
        "main_sessions": len(first_contexts),
        "files_read": files_read,
        "files_unreadable": files_unreadable,
        "lines_skipped": lines_skipped,
        "tokens": dict(tokens),
        "total_cost_usd": round(total_cost, 2),
        "by_bucket": rows(buckets),
        "by_model": rows(by_model),
        "by_scope": rows(by_scope),
        "by_context_band": rows(by_band, [label for _, label in CONTEXT_BANDS], band_calls),
        "starting_context_median": int(statistics.median(first_contexts)) if first_contexts else 0,
        "starting_context_floor": rows(Counter({"Re-reading the starting context": floor_cost})),
        "mid_session_recache_by_gap": rows(rewrite_cost, [label for _, label in IDLE_GAP_BANDS], rewrite_count),
        "cache_writes_at_compaction": rows(compaction_cost, [COMPACTION_ROW], compaction_count),
        "tool_results_carried": rows(tool_cost, None, tool_count),
        "unpriced_models": dict(unpriced),
    }


def transcript_paths(root: Path, since: float, project: str | None) -> list[Path]:
    """Every transcript written to since the window opened, optionally by project.

    Project directories are the session's cwd with each non-alphanumeric character
    replaced by a hyphen, so the query is encoded the same way before matching.
    """
    query = re.sub(r"[^a-z0-9]", "-", project.lower()) if project else None
    paths = []
    for path in root.rglob("*.jsonl"):
        try:
            recent = path.stat().st_mtime >= since
        except OSError:
            continue
        if recent and (query is None or query in path.relative_to(root).parts[0].lower()):
            paths.append(path)
    return paths


def _utc_day_start(day: date) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()


def render_attribution(report: dict, since: str, until: str) -> str:
    def table(title: str, rows: list[dict]) -> list[str]:
        has_count = any("count" in row for row in rows)
        head = "| # | Row | Cost | Share |" + (" Count |" if has_count else "")
        rule = "|---|-----|-----:|------:|" + ("------:|" if has_count else "")
        body = [
            f"| {i} | {row['name']} | ${row['cost_usd']:,.2f} | {row['share_pct']:.1f}% |"
            + (f" {row.get('count', 0):,} |" if has_count else "")
            for i, row in enumerate(rows, 1)
        ]
        return [f"### {title}", "", head, rule, *body, ""]

    t = report["tokens"]
    lines = [
        f"## Token spend attribution, {since} to {until} (UTC)",
        "",
    ]
    if not report["files_read"]:
        lines += ["**No transcripts matched this window and project, so the figures below are empty, not zero spend.**", ""]
    lines += [
        f"{report['calls']:,} API calls, {report['main_sessions']:,} main sessions started in the window, "
        f"${report['total_cost_usd']:,.2f} at published API prices.",
        f"Tokens: input {t.get('input_tokens', 0):,}, output {t.get('output_tokens', 0):,}, "
        f"cache read {t.get('cache_read_input_tokens', 0):,}, cache write {t.get('cache_creation_input_tokens', 0):,}.",
        f"Median starting context of a main session: {report['starting_context_median']:,} tokens.",
        f"Read {report['files_read']:,} transcripts. Could not read {report['files_unreadable']:,}, "
        f"and skipped {report['lines_skipped']:,} malformed lines.",
        "",
        "The sections below overlap and do not add up to one total. Dollars are published API prices, "
        "not what a subscription bills. Tool-result rows estimate tokens from text length, at a ratio "
        "measured on real turns, and price later reads at the arrival call's model, so they rank costs "
        "and are not exact.",
        "",
        *table("By billing bucket", report["by_bucket"]),
        *table("By model", report["by_model"]),
        *table("Main sessions and subagents", report["by_scope"]),
        *table("Main-session calls by context size", report["by_context_band"]),
        *table("Starting context", report["starting_context_floor"]),
        *table("Mid-session re-caching by idle gap", report["mid_session_recache_by_gap"]),
        *table("Cache writes at compaction", report["cache_writes_at_compaction"]),
        *table("Tool results, from arrival until compaction", report["tool_results_carried"]),
    ]
    if report["unpriced_models"]:
        lines += ["### Unpriced calls (excluded from every cost above)", ""]
        lines += [f"- `{model}`: {count:,} tokens" for model, count in report["unpriced_models"].items()]
        lines.append("")
    return "\n".join(lines)


def run_attribution(args: argparse.Namespace) -> None:
    until = args.until or datetime.now(timezone.utc).date()
    since = args.since or until - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    since_ts = _utc_day_start(since)
    paths = transcript_paths(TRANSCRIPT_ROOT, since_ts, args.project)
    report = attribute_spend(paths, since_ts, _utc_day_start(until + timedelta(days=1)))
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(render_attribution(report, since.isoformat(), until.isoformat()))


def main() -> None:
    # Windows falls back to the console codepage on redirected (non-tty) stdout,
    # silently mangling non-ASCII output (e.g. the ellipsis/em-dash below) instead
    # of writing valid UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

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
    parser.add_argument(
        "--attribution", action="store_true",
        help="Attribute spend by context size, model, re-caching and tool results, read from "
             "session transcripts. Takes --since, --until, --project and --format markdown|json.",
    )
    parser.add_argument("--since", type=date.fromisoformat,
                        help=f"With --attribution: first day, YYYY-MM-DD UTC (default: the {DEFAULT_WINDOW_DAYS} days ending --until)")
    parser.add_argument("--until", type=date.fromisoformat,
                        help="With --attribution: last day, inclusive, YYYY-MM-DD UTC (default: today, UTC)")
    args = parser.parse_args()

    if args.attribution:
        ignored = [
            flag for flag, is_set in (
                ("--date", args.date),
                ("--days", args.days),
                ("--latest", args.latest),
                ("--show-subagents", args.show_subagents),
                ("--format summary", args.format == "summary"),
            ) if is_set
        ]
        if ignored:
            parser.error(f"--attribution can't be combined with {', '.join(ignored)}")
        if args.since and args.until and args.since > args.until:
            parser.error("--since is after --until")
        run_attribution(args)
        return
    if args.since or args.until:
        parser.error("--since and --until need --attribution")

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
