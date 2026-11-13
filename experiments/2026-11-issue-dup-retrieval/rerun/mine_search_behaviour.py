"""How do real sessions run `gh issue list --search`? Reads local transcripts, prints counts only.

Usage: py -3 -I mine_search_behaviour.py <projects-dir>

A search is a Bash or PowerShell tool call running `gh issue list ... --search` or `gh search issues`.
An episode is a run of searches in one transcript, each within WINDOW tool calls of the last. A
filing episode is one whose last search is followed by `gh issue create` within WINDOW tool calls, or
contains it in the same command.
"""
import collections
import glob
import json
import os
import re
import statistics
import sys

SEARCH = re.compile(r"gh\s+issue\s+list\b[^|;&\n]*--search\b|gh\s+search\s+issues\b")
QUERY = re.compile(r"--search[= ]+(\"[^\"]*\"|'[^']*'|\S+)|gh\s+search\s+issues\s+(\"[^\"]*\"|'[^']*'|\S+)")
CREATE = re.compile(r"gh\s+issue\s+create\b")
WINDOW = 6
NO_OUTPUT = re.compile(r"^\(?(bash |powershell |command )?(completed|ran|tool ran)? ?(with|without)? ?no output\)?\.?$", re.I)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def is_empty(result):
    t = result.strip()
    if not t or t == "[]" or NO_OUTPUT.match(t):
        return True
    return bool(re.search(r"no issues match|no issues found|Showing 0 of|no output", t[:200], re.I))


def calls_in(path):
    order, results = [], {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    cmd = (c.get("input") or {}).get("command") if c.get("name") in ("Bash", "PowerShell") else None
                    order.append((c.get("id"), cmd))
                elif c.get("type") == "tool_result":
                    results[c.get("tool_use_id")] = text_of(c.get("content"))
    return [(cmd, results.get(i)) for i, cmd in order]


def episodes(calls):
    runs, current, last = [], [], None
    for n, (cmd, result) in enumerate(calls):
        if cmd and SEARCH.search(cmd):
            if current and n - last > WINDOW:
                runs.append((current, last))
                current = []
            current.append((cmd, result))
            last = n
    if current:
        runs.append((current, last))
    return runs


def reactions(run):
    """For each single-query search with a result in an episode, (is it empty, did a later call search again).

    "Later" is by position in the episode and the same for empty and non-empty results: any call after
    this one counts, including a multi-query or chained call, and none before it does.
    """
    out = []
    for k, (c, r) in enumerate(run):
        if len(SEARCH.findall(c)) == 1 and not CREATE.search(c) and r is not None:
            out.append((is_empty(r), k < len(run) - 1))
    return out


def main(root):
    stats = collections.Counter()
    short = collections.Counter()
    sizes, query_terms = [], []
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        calls = calls_in(path)
        stats["transcripts"] += 1
        for run, last in episodes(calls):
            stats["episodes"] += 1
            later = [c for c, _ in calls[last + 1 : last + 1 + WINDOW] if c]
            chained = any(CREATE.search(c) for c, _ in run)
            filed_after = any(CREATE.search(c) for c in later)
            if not (chained or filed_after):
                continue
            stats["filing_episodes"] += 1
            stats["filing_chained_in_same_command"] += chained
            sizes.append(len(run))
            singles = [(c, r) for c, r in run if len(SEARCH.findall(c)) == 1 and not CREATE.search(c)]
            for c, r in run:
                stats["filing_search_calls"] += 1
                stats[f"filing_queries_in_call_{min(len(SEARCH.findall(c)), 4)}"] += 1
                for m in QUERY.finditer(c):
                    q = re.sub(r"\b(is|in|state|label|author|repo|created|updated):\S+", "", (m.group(1) or m.group(2) or "").strip("\"'"))
                    query_terms.append(len(q.split()))
            flags = [is_empty(r) for c, r in singles if r is not None]
            for c, r in singles:
                if r is not None and len(r.strip()) < 50:
                    short[re.sub(r"\d+", "N", r.strip())[:50]] += 1
            stats["filing_single_query_calls"] += len(flags)
            stats["filing_single_query_empty"] += sum(flags)
            # Reaction: whether another search call came later in the episode, by position.
            for empty, followed in reactions(run):
                kind = "empty" if empty else "nonempty"
                stats[f"filing_{kind}_followed_by_search"] += followed
                stats[f"filing_{kind}_not_followed"] += not followed
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")
    print("searches per filing episode:", sorted(collections.Counter(sizes).items()))
    if query_terms:
        print("terms per query: median", statistics.median(query_terms), "p10", sorted(query_terms)[len(query_terms) // 10], "p90", sorted(query_terms)[len(query_terms) * 9 // 10], "n", len(query_terms))
    print("short result shapes:", short.most_common(12))


if __name__ == "__main__":
    main(sys.argv[1])
