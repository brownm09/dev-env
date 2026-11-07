"""Print the re-run's per-input table and how much the retry round recovered (dev-env#1282).

Usage: py -3 report_rerun.py <labels.json> <labels_all.json> <vector.json> <round1.json> <final.json> <scored_round1.json> <scored_final.json> <counts.json>

The verdict comes from scored_final.json through score.py. Everything here is a report on it.
Numbers only. Inputs in labels_all.json but not labels.json are fresh, and print apart.
"""
import json
import sys

from dup_eval import K_PRIMARY, WIN_BAR, hit_at, hit_rank, majority, round_robin

MEASURED_RETRY_RATE = 4 / 16  # Filing-time empty searches followed by another search, from the pre-registration.


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ranks_text(ranks):
    return ", ".join("-" if r is None else str(r) for r in ranks)


def fresh_row(item, vector, round1, final, counts):
    gens = sorted(final)
    targets = item["targets"]
    r1 = [hit_rank(round_robin(round1[g][item["id"]]["parity"]), targets) for g in gens]
    fin = [hit_rank(round_robin(final[g][item["id"]]["parity"]), targets) for g in gens]
    hit = lambda data: majority([hit_at(round_robin(data[g][item["id"]]["parity"]), targets, K_PRIMARY) for g in gens])
    return {
        "id": item["id"],
        "v_rank": hit_rank(vector["ranks"][item["id"]], targets),
        "v_hit": hit_at(vector["ranks"][item["id"]], targets, K_PRIMARY),
        "r1": r1,
        "final": fin,
        "r1_hit": hit(round1),
        "final_hit": hit(final),
        "queries": sum(len(final[g][item["id"]]["parity"]) for g in gens),
        "empty": sum(counts[g][item["id"]]["empty"] for g in gens),
        "still": sum(counts[g][item["id"]]["still_empty"] for g in gens),
        "merged_empty_gens": sum(not round_robin(final[g][item["id"]]["parity"]) for g in gens),
    }


def main(labels_path, all_path, vector_path, round1_path, final_path, sc1_path, sc2_path, counts_path):
    primary = load(labels_path)["inputs"]
    everything = load(all_path)["inputs"]
    vector, round1, final, counts = map(load, (vector_path, round1_path, final_path, counts_path))
    s1, s2 = load(sc1_path), load(sc2_path)
    rows = [fresh_row(i, vector, round1, final, counts) for i in primary]
    n = len(rows)
    if n != s2["summary"]["n_inputs"]:
        raise SystemExit("the report and the scored file disagree on n")

    print("| # | Input | Vector rank | Keyword ranks, round one | Keyword ranks, after retry | Vector hit | Round-one hit | Hit after retry | Empty queries, of queries written | Still empty |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for k, r in enumerate(rows, 1):
        print(f"| {k} | {r['id']} | {'-' if r['v_rank'] is None else r['v_rank']} | {ranks_text(r['r1'])} | {ranks_text(r['final'])} | {'yes' if r['v_hit'] else 'no'} | {'yes' if r['r1_hit'] else 'no'} | {'yes' if r['final_hit'] else 'no'} | {r['empty']} of {r['queries']} | {r['still']} |")

    v, k1, k2 = (sum(r[key] for r in rows) for key in ("v_hit", "r1_hit", "final_hit"))
    recovered = [r["id"] for r in rows if r["final_hit"] and not r["r1_hit"]]
    lost = [r["id"] for r in rows if r["r1_hit"] and not r["final_hit"]]
    print(f"\n**Totals, n={n}:** vector {v}, keyword round one {k1}, keyword after retry {k2}.")
    print(f"**Recovered by the retry round:** {len(recovered)} ({', '.join(recovered) or 'none'}). **Lost:** {len(lost)} ({', '.join(lost) or 'none'}).")
    print(f"**Gap:** round one {(v - k1) / n:.3f}, after retry {(v - k2) / n:.3f}. Map readings: round one {s1['summary']['verdict']}, after retry {s2['summary']['verdict']}.")
    blended = k1 + MEASURED_RETRY_RATE * (k2 - k1)
    print(f"**Diagnostic, at the measured retry rate of {MEASURED_RETRY_RATE:.2f}:** expected keyword hits {blended:.2f}, gap {(v - blended) / n:.3f}.")

    gens = sorted(final)
    print("\n**Per generation, keyword recall@10 and gap (round one, after retry):**\n")
    for g in gens:
        a = sum(hit_at(round_robin(round1[g][i["id"]]["parity"]), i["targets"], K_PRIMARY) for i in primary) / n
        b = sum(hit_at(round_robin(final[g][i["id"]]["parity"]), i["targets"], K_PRIMARY) for i in primary) / n
        print(f"- {g}: {a:.3f} (gap {v / n - a:.3f}), {b:.3f} (gap {v / n - b:.3f})")

    ids = {i["id"] for i in primary}
    empty = sum(counts[g][i]["empty"] for g in gens for i in ids)
    replaced = sum(counts[g][i]["replaced"] for g in gens for i in ids)
    still = sum(counts[g][i]["still_empty"] for g in gens for i in ids)
    print(f"\n**Queries:** {sum(len(round1[g][i]['parity']) for g in gens for i in ids)} written in round one, {empty} returned nothing, {replaced} replaced, {still} still empty after retry.")
    misses = [r for r in rows if not r["final_hit"]]
    residual = [r for r in misses if r["merged_empty_gens"] * 2 > len(gens)]
    print(f"**Misses after retry:** {len(misses)} ({', '.join(r['id'] for r in misses) or 'none'}). Of those, {len(residual)} returned nothing at all in most generations (residual inputs).")
    t4_residual = (v - k2 - len(residual)) / n
    any_gap = v / n - s2["summary"]["keyword_any_returned_recall"]
    print(f"**T4 checks as registered:** gap minus residual inputs {t4_residual:.3f}; gap counting a keyword hit anywhere in its returned list {any_gap:.3f}. Flag if the map reads supported and either is below {WIN_BAR}: {'FLAG' if s2['summary']['verdict'] == 'supported' and min(t4_residual, any_gap) < WIN_BAR else 'no flag'}.")
    curve = s2["summary"]["recall_curve"]
    print("**Recall curve after retry (candidates: vector, keyword):** " + "; ".join(f"{k}: {c['vector']}, {c['keyword']}" for k, c in curve.items()) + ".")
    print("\n**Sensitivity of the final gap to those residual inputs (arithmetic, not a measurement):**\n")
    print("| # | Inputs recovered | Keyword hits | Gap | Clears the 0.15 win bar? |")
    print("|---|---|---|---|---|")
    for row, extra in enumerate(range(len(residual) + 1), 1):
        gap = (v - k2 - extra) / n
        print(f"| {row} | {extra} | {k2 + extra} | {gap:.3f} | {'yes' if gap >= WIN_BAR else 'no'} |")

    recall = s2["summary"]["recall_at_10"]
    print(f"\n**Stratum A at default scope:** vector {recall['stratum_A']['vector']:.3f}, keyword {recall['stratum_A']['keyword_default_scope']:.3f}, gap {s2['summary']['gap_default_scope_A']:.3f}.")
    for label, items in (("Stratum A", [r for r in rows if r["id"].startswith("A:")]), ("Stratum B", [r for r in rows if r["id"].startswith("B:")])):
        a, b, c = (sum(r[key] for r in items) for key in ("v_hit", "r1_hit", "final_hit"))
        print(f"\n**{label}, n={len(items)}:** vector {a}, round one {b}, after retry {c}, gap after retry {(a - c) / len(items):.3f}.", end="")
    print()

    fresh = [i for i in everything if i["id"] not in ids]
    print(f"\n**Fresh inputs, n={len(fresh)}, not in the verdict:**\n")
    for item in fresh:
        r = fresh_row(item, vector, round1, final, counts)
        print(f"- {r['id']}: vector rank {'-' if r['v_rank'] is None else r['v_rank']}; keyword ranks round one {ranks_text(r['r1'])}; after retry {ranks_text(r['final'])}; empty queries {r['empty']} of {r['queries']}, still empty {r['still']}.")


if __name__ == "__main__":
    main(*sys.argv[1:9])
