"""Score both arms against the labels and print the per-input table and the frozen verdict.

Usage: py -3 score.py <labels.json> <vector.json> <keyword.json> <out.json>

Reads numbers only. A keyword hit for one input is the majority across generations.
"""
import json
import statistics
import sys

from dup_eval import (
    K_CURVE,
    K_PRIMARY,
    cluster_discordance,
    clusters,
    decide,
    hit_at,
    hit_rank,
    majority,
    round_robin,
    sign_test_one_sided,
)


def auc(positive, negative):
    """Probability a duplicate pair scores above a non-duplicate pair (ties count half)."""
    if not positive or not negative:
        raise ValueError("auc got an empty class")
    wins = sum((p > n) + 0.5 * (p == n) for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def main(labels_path, vector_path, keyword_path, out_path):
    with open(labels_path, encoding="utf-8") as f:
        labels = json.load(f)
    with open(vector_path, encoding="utf-8") as f:
        vector = json.load(f)
    with open(keyword_path, encoding="utf-8") as f:
        keyword = json.load(f)
    inputs = labels["inputs"]
    generations = sorted(keyword)

    edges = []
    for item in inputs:
        anchor = (item["repo"], item["targets"][0])
        node = item["query_issue"] if item["stratum"] == "A" else item["host_issue"]
        edges.append(((item["repo"], node), anchor))
        edges += [(anchor, (item["repo"], t)) for t in item["targets"]]
    family = clusters(edges)

    rows = []
    for item in inputs:
        iid, targets = item["id"], item["targets"]
        v_rank = hit_rank(vector["ranks"][iid], targets)
        row = {
            "id": iid,
            "stratum": item["stratum"],
            "repo": item["repo"],
            "cluster": str(family((item["repo"], targets[0]))),
            "v_rank": v_rank,
            "v_hit": hit_at(vector["ranks"][iid], targets, K_PRIMARY),
        }
        merged = {g: round_robin(keyword[g][iid]["parity"]) for g in generations}
        row["k_ranks"] = [hit_rank(merged[g], targets) for g in generations]
        row["k_hit"] = majority([hit_at(merged[g], targets, K_PRIMARY) for g in generations])
        row["k_shown"] = statistics.median(min(len(merged[g]), K_PRIMARY) for g in generations)
        row["k_total_returned"] = statistics.median(len(merged[g]) for g in generations)
        row["k_any30"] = majority([hit_rank(merged[g], targets) is not None for g in generations])
        if item["stratum"] == "A":
            default = {g: round_robin(keyword[g][iid]["default"]) for g in generations}
            row["k_default_hit"] = majority([hit_at(default[g], targets, K_PRIMARY) for g in generations])
        rows.append(row)

    def recall(key, subset):
        return sum(r[key] for r in subset) / len(subset)

    stratum_a = [r for r in rows if r["stratum"] == "A"]
    stratum_b = [r for r in rows if r["stratum"] == "B"]
    gap = recall("v_hit", rows) - recall("k_hit", rows)
    wins, losses = cluster_discordance(rows)
    p = sign_test_one_sided(wins, losses)
    gap_b = recall("v_hit", stratum_b) - recall("k_hit", stratum_b)
    gap_default_a = recall("v_hit", stratum_a) - recall("k_default_hit", stratum_a)
    verdict, reason = decide(gap, p, gap_b, gap_default_a)

    curve = {}
    for k in K_CURVE:
        v = sum(hit_at(vector["ranks"][i["id"]], i["targets"], k) for i in inputs) / len(inputs)
        kw = 0
        for i in inputs:
            flags = [hit_at(round_robin(keyword[g][i["id"]]["parity"]), i["targets"], k) for g in generations]
            kw += majority(flags)
        curve[k] = {"vector": round(v, 3), "keyword": round(kw / len(inputs), 3)}

    pair_scores = {name: [p_[3] for p_ in values] for name, values in vector["pairs"].items()}
    summary = {
        "n_inputs": len(rows),
        "n_families": len({r["cluster"] for r in rows}),
        "recall_at_10": {
            "pooled": {"vector": recall("v_hit", rows), "keyword": recall("k_hit", rows)},
            "stratum_A": {"vector": recall("v_hit", stratum_a), "keyword": recall("k_hit", stratum_a),
                          "keyword_default_scope": recall("k_default_hit", stratum_a)},
            "stratum_B": {"vector": recall("v_hit", stratum_b), "keyword": recall("k_hit", stratum_b)},
        },
        "gap_pooled": gap,
        "family_sign_test": {"wins": wins, "losses": losses, "p_one_sided": p},
        "gap_stratum_B": gap_b,
        "gap_default_scope_A": gap_default_a,
        "verdict": verdict,
        "reason": reason,
        "recall_curve": curve,
        "keyword_any_returned_recall": recall("k_any30", rows),
        "keyword_median_results_returned": statistics.median(r["k_total_returned"] for r in rows),
        "input_wins_losses": {
            "vector_only": sum(r["v_hit"] and not r["k_hit"] for r in rows),
            "keyword_only": sum(r["k_hit"] and not r["v_hit"] for r in rows),
            "both": sum(r["v_hit"] and r["k_hit"] for r in rows),
            "neither": sum(not r["v_hit"] and not r["k_hit"] for r in rows),
        },
        "pair_auc": {
            "duplicate_vs_known_negative": auc(pair_scores["duplicate"], pair_scores["known_negative"]),
            "duplicate_vs_easy_negative": auc(pair_scores["duplicate"], pair_scores["easy_negative"]),
        },
        "pair_score_medians": {k: statistics.median(v) for k, v in pair_scores.items()},
        "cross_repo": vector["cross_repo"],
        "auditor_near_dup_scores": vector["pairs"]["auditor_near_dup"],
    }
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:5])
