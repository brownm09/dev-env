"""Print the per-input table and the diagnostics for results.md. None of this bears on the frozen
outcome map, which comes from score.py.

Usage: py -3 report.py <labels.json> <vector.json> <keyword.json> <scored.json> [issue-cache-dir]

Prints issue numbers and metrics only.
"""
import json
import sys

from dup_eval import K_PRIMARY, WIN_BAR, draft_text, hit_at, round_robin


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(labels_path, vector_path, keyword_path, scored_path, cache_dir=None):
    labels, vector, keyword, scored = map(load, (labels_path, vector_path, keyword_path, scored_path))
    inputs = labels["inputs"]
    rows = scored["rows"]
    summary = scored["summary"]
    gens = sorted(keyword)

    print("| # | Input | Vector rank | Keyword ranks, 3 generations | Vector hit | Keyword hit | Keyword returned (median) |")
    print("|---|---|---|---|---|---|---|")
    for n, r in enumerate(rows, 1):
        ranks = ", ".join("-" if x is None else str(x) for x in r["k_ranks"])
        vr = "-" if r["v_rank"] is None else r["v_rank"]
        print(f"| {n} | {r['id']} | {vr} | {ranks} | {'yes' if r['v_hit'] else 'no'} | {'yes' if r['k_hit'] else 'no'} | {r['k_total_returned']:g} |")

    v_hits = sum(r["v_hit"] for r in rows)
    k_hits = sum(r["k_hit"] for r in rows)
    print("\n**Per generation, keyword recall@10 and gap:**\n")
    for g in gens:
        rec = sum(hit_at(round_robin(keyword[g][i["id"]]["parity"]), i["targets"], K_PRIMARY) for i in inputs) / len(inputs)
        print(f"- {g}: keyword {rec:.3f}, gap {v_hits / len(rows) - rec:.3f}")

    print("\n**Per repo, hits out of inputs:**\n")
    for repo in sorted({i["repo"] for i in inputs}):
        subset = [r for r in rows if r["repo"] == repo]
        print(f"- {repo}: vector {sum(r['v_hit'] for r in subset)}, keyword {sum(r['k_hit'] for r in subset)}, of {len(subset)}")

    print("\n**Keyword searches that returned nothing (title and body scope):**\n")
    queries = [q for g in gens for i in inputs for q in keyword[g][i["id"]]["parity"]]
    all_empty = sum(not any(keyword[g][i["id"]]["parity"]) for g in gens for i in inputs)
    misses = [r for r in rows if not r["k_hit"]]
    empty_misses = [r for r in misses if r["k_total_returned"] == 0]
    print(f"- {sum(not q for q in queries)} of {len(queries)} queries returned nothing")
    print(f"- All queries came back empty on {all_empty} of {len(gens) * len(inputs)} generation-inputs")
    print(f"- {len(empty_misses)} of {len(misses)} keyword misses returned nothing in most generations")

    print("\n**Sensitivity to the keyword arm recovering those empty-result inputs (upper-bound arithmetic, not a measurement):**\n")
    print("| # | Inputs recovered | Keyword hits | Gap | Clears the 0.15 win bar? |")
    print("|---|---|---|---|---|")
    for n, recovered in enumerate(range(len(empty_misses) + 1), 1):
        gap = (v_hits - k_hits - recovered) / len(rows)
        print(f"| {n} | {recovered} | {k_hits + recovered} | {gap:.3f} | {'yes' if gap >= WIN_BAR else 'no'} |")

    print("\n**Pre-registered diagnostics (criteria 5 and 6), not verdict-bearing:**\n")
    auc = summary["pair_auc"]
    print(f"- Pair separation, as the chance a duplicate pair outscores another pair: {auc['duplicate_vs_known_negative']:.3f} against declared non-duplicates, {auc['duplicate_vs_easy_negative']:.3f} against random pairs")
    for case in summary["cross_repo"]:
        print(f"- Cross-repo, vector arm only: {case['query'][0]}#{case['query'][1]} finds {case['target'][0]}#{case['target'][1]} at rank {case['rank_in_merged_pool']} of {case['pool']}")
    for repo, a, b, score in summary["auditor_near_dup_scores"]:
        print(f"- Backlog-audit near-duplicate, {repo}#{a} and #{b}: cosine {score:.3f}")

    print("\n**Cosine thresholds (post hoc, descriptive): share of each pair class at or above t.** These compare raw documents, with no sanitizing, unlike the ranking, so they don't measure a filing-time draft. The duplicate class is 35 pairs from 30 inputs and isn't independent.\n")
    pairs = vector["pairs"]
    for t in (0.80, 0.85, 0.90):
        share = {k: sum(p[3] >= t for p in v) / len(v) for k, v in pairs.items()}
        print(f"- t={t:.2f}: duplicate {share['duplicate']:.2f}, declared non-duplicate {share['known_negative']:.2f}, random {share['easy_negative']:.2f}")

    hard = [r for r in rows if not (r["v_rank"] == 1 and all(x == 1 for x in r["k_ranks"]))]
    print(f"\n**Without inputs both arms hit at rank 1 in every generation:** n={len(hard)}, vector {sum(r['v_hit'] for r in hard)}, keyword {sum(r['k_hit'] for r in hard)}")

    if cache_dir:
        repos = {}
        for repo in sorted({i["repo"] for i in inputs}):
            repos[repo] = {i["number"]: i for i in load(f"{cache_dir}/issues_{repo}.json")}
        edited = []
        for item in inputs:
            for number in item["targets"] + ([item["query_issue"]] if item["stratum"] == "A" else []):
                when = repos[item["repo"]][number]["lastEditedAt"]
                if when and when > item["created_before"]:
                    edited.append(f"{item['id']} -> #{number}")
        print(f"\n**Bodies edited after the input's filing time:** {len(edited)} ({', '.join(edited)})")
        print("\n**Share of each stratum B comment kept after sanitizing:**\n")
        for item in inputs:
            if item["stratum"] == "B":
                comment = next(c for c in repos[item["repo"]][item["host_issue"]]["comments"]["nodes"] if c["createdAt"] == item["comment_created_at"])
                print(f"- {item['id']}: {len(draft_text(item, repos)) / len(comment['body']):.0%}")


if __name__ == "__main__":
    main(*sys.argv[1:])
