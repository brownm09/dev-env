"""Print the per-input table and the post-hoc diagnostics for results.md. None of this bears on the
verdict, which comes from score.py and the frozen outcome map.

Usage: py -3 report.py <labels.json> <vector.json> <keyword.json> <scored.json> [issue-cache-dir]
"""
import json
import sys

from dup_eval import K_PRIMARY, hit_at, majority, round_robin


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(labels_path, vector_path, keyword_path, scored_path, cache_dir=None):
    labels, vector, keyword, scored = map(load, (labels_path, vector_path, keyword_path, scored_path))
    inputs = labels["inputs"]
    gens = sorted(keyword)

    print("| # | Input | Vector rank | Keyword ranks, 3 generations | Vector hit | Keyword hit | Keyword returned (median) |")
    print("|---|---|---|---|---|---|---|")
    for n, r in enumerate(scored["rows"], 1):
        ranks = ", ".join("-" if x is None else str(x) for x in r["k_ranks"])
        vr = "-" if r["v_rank"] is None else r["v_rank"]
        print(f"| {n} | {r['id']} | {vr} | {ranks} | {'yes' if r['v_hit'] else 'no'} | {'yes' if r['k_hit'] else 'no'} | {r['k_total_returned']:g} |")

    vector_pooled = sum(r["v_hit"] for r in scored["rows"]) / len(scored["rows"])
    print("\nPer generation, keyword recall@10 and gap:")
    for g in gens:
        rec = sum(hit_at(round_robin(keyword[g][i["id"]]["parity"]), i["targets"], K_PRIMARY) for i in inputs) / len(inputs)
        print(f"  {g} keyword {rec:.3f} gap {vector_pooled - rec:.3f}")

    print("\nPer repo, vector and keyword recall@10:")
    for repo in sorted({i["repo"] for i in inputs}):
        rows = [r for r in scored["rows"] if r["repo"] == repo]
        print(f"  {repo} n={len(rows)} vector {sum(r['v_hit'] for r in rows)}/{len(rows)} keyword {sum(r['k_hit'] for r in rows)}/{len(rows)}")

    print("\nWorst vector rows (miss, or below rank 5):")
    for r in scored["rows"]:
        if r["v_rank"] is None or r["v_rank"] > 5:
            print(f"  {r['id']} vector {r['v_rank']} keyword {r['k_ranks']}")

    print("\nReading load, results available per query input (median across generations):")
    returned = sorted(r["k_total_returned"] for r in scored["rows"])
    print(f"  keyword median {returned[len(returned) // 2]:g}, max {returned[-1]:g}; vector always shows the full budget")
    print(f"  keyword returned nothing in the majority of generations: {sum(r['k_total_returned'] == 0 for r in scored['rows'])} inputs")

    print("\nCosine thresholds (post hoc, descriptive): share of each pair class at or above t")
    pairs = vector["pairs"]
    for t in (0.80, 0.85, 0.90):
        row = {k: sum(p[3] >= t for p in v) / len(v) for k, v in pairs.items()}
        print(f"  t={t:.2f} duplicate {row['duplicate']:.2f} declared-non-duplicate {row['known_negative']:.2f} random {row['easy_negative']:.2f}")

    print("\nSensitivity: drop inputs where both arms hit trivially at rank 1 in all generations")
    hard = [r for r in scored["rows"] if not (r["v_rank"] == 1 and all(x == 1 for x in r["k_ranks"]))]
    print(f"  n={len(hard)} vector {sum(r['v_hit'] for r in hard)}/{len(hard)} keyword {sum(r['k_hit'] for r in hard)}/{len(hard)}")

    if cache_dir:
        edited = 0
        for item in inputs:
            issues = {i["number"]: i for i in load(f"{cache_dir}/issues_{item['repo']}.json")}
            for number in item["targets"] + ([item["query_issue"]] if item["stratum"] == "A" else []):
                when = issues[number]["lastEditedAt"]
                if when and when > item["created_before"]:
                    edited += 1
                    print(f"  edited after the input was filed: {item['id']} -> #{number}")
        print(f"\nBodies edited after the input's filing time: {edited}")


if __name__ == "__main__":
    main(*sys.argv[1:])
