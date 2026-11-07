"""Scoring core for the duplicate-issue retrieval experiment (dev-env#1260).

Standard library only, so CI can test it without the embedding stack. `score.py` builds the verdict
from these helpers and `decide`. Nothing here reads issue text except `sanitize_query`, `draft_text`
and `privacy_scan`.
"""
import json
import re
import time
from math import comb

K_PRIMARY = 10
K_CURVE = (1, 3, 5, 10, 20, 30)
WIN_BAR = 0.15
NO_GAIN = 0.05
ALPHA = 0.05
STRATUM_B_GUARD = 0.15

_REF = re.compile(
    r"https?://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+(?:#[\w-]+)?"
    r"|\b[\w.-]+/[\w.-]+#\d+|\b[\w.-]+#\d+|(?<![\w/])#\d+"
)
_DECLARATION = re.compile(
    r"duplicat|\bdupe?\b|supersed|near-dup|already (tracked|filed|covered)|not filing|no new issue"
    r"|rather than fil|rather than open|instead of fil|filing (separately|fresh)|same (bug|issue|root cause) as"
    r"|check-before-filing|search-before-filing",
    re.I,
)


def sanitize_query(text):
    """Remove what would not exist at filing time, or names the answer: issue and PR references,
    and lines declaring a duplicate relationship. Both arms get the same output."""
    kept = [line for line in (text or "").splitlines() if not _DECLARATION.search(line)]
    return _REF.sub("", "\n".join(kept)).strip()


def draft_text(item, repos):
    """The sanitized draft both arms start from. An empty draft raises, because the frozen rule is
    to discard and list such an input, never to score it."""
    if item["stratum"] == "A":
        issue = repos[item["repo"]][item["query_issue"]]
        text = sanitize_query(f"{issue['title']}\n\n{issue['body'] or ''}")
    else:
        comments = repos[item["repo"]][item["host_issue"]]["comments"]["nodes"]
        matching = [c for c in comments if c["createdAt"] == item["comment_created_at"]]
        if not matching:
            raise SystemExit(f"comment not found for {item['id']}")
        text = sanitize_query(matching[0]["body"])
    if not text:
        raise SystemExit(f"empty sanitized draft for {item['id']}: discard and list it")
    return text


def log_event(path, **event):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}) + "\n")


def round_robin(lists):
    """Merge per-query result lists into one ranking: first of each, then second of each, deduped."""
    merged, seen = [], set()
    for rank in range(max((len(x) for x in lists), default=0)):
        for results in lists:
            if rank < len(results) and results[rank] not in seen:
                seen.add(results[rank])
                merged.append(results[rank])
    return merged


def hit_rank(ranked, targets):
    """1-based rank of the first accepted target, or None."""
    targets = set(targets)
    for i, number in enumerate(ranked, 1):
        if number in targets:
            return i
    return None


def hit_at(ranked, targets, k):
    rank = hit_rank(ranked, targets)
    return rank is not None and rank <= k


def majority(flags):
    if not flags:
        raise ValueError("majority got no generations")
    return sum(bool(f) for f in flags) * 2 > len(flags)


def clusters(edges):
    """Connected components over (repo, number) nodes: queries about one duplicate family are not
    independent, so the significance test counts each family once."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    return find


def sign_test_one_sided(wins, losses):
    """Exact one-sided binomial p for at least `wins` successes among the discordant families."""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(comb(n, i) for i in range(wins, n + 1)) / 2**n


def cluster_discordance(rows):
    """rows: dicts with cluster, v_hit, k_hit. Returns (wins, losses) at the family level."""
    by_cluster = {}
    for row in rows:
        by_cluster.setdefault(row["cluster"], []).append(row)
    wins = losses = 0
    for members in by_cluster.values():
        diff = sum(m["v_hit"] for m in members) - sum(m["k_hit"] for m in members)
        wins += diff > 0
        losses += diff < 0
    return wins, losses


def decide(gap, p, gap_stratum_b, gap_default_scope_a):
    """The frozen outcome map. Inputs are vector minus keyword recall@10:
    gap (pooled), p (family sign test), gap_stratum_b, gap_default_scope_a (keyword at GitHub's
    default scope, stratum A only). Returns the map's reading and the reason. The audit's threat
    sweep can still force inconclusive on top of this (see results.md)."""
    if gap <= NO_GAIN:
        if gap_default_scope_a > WIN_BAR:
            return "inconclusive", "confounded by search scope: no pooled gain, but a large gain at default scope"
        return "refuted", "pooled gain is at or below the no-gain line"
    if gap < WIN_BAR:
        return "inconclusive", "pooled gain is between the no-gain line and the win bar"
    if p > ALPHA:
        return "inconclusive", "pooled gain clears the bar but the family sign test does not"
    if gap_stratum_b < -STRATUM_B_GUARD:
        return "inconclusive", "pooled gain clears the bar but stratum B loses beyond the guard"
    if gap_default_scope_a <= NO_GAIN:
        return "inconclusive", "confounded by search scope: the gain disappears at default scope"
    return "supported", "pooled gain clears the bar, the sign test, the stratum B guard and the scope check"


def privacy_scan(text, titles, min_len=12):
    """Return the private issue titles found verbatim in text (case-insensitive). Titles shorter
    than min_len are skipped, since a short title is ordinary wording rather than a leak."""
    lowered = text.lower()
    return [t for t in titles if len(t) >= min_len and t.lower() in lowered]
