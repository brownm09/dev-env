"""Check each generation's query file against its ID map, and merge them into queries.json keyed by
input ID. Prints counts only.

Usage: py -3 merge_queries.py <batch-dir> g1 g2 g3

Reads raw_<gen>.json (written by the query-writing agent) and idmap_<gen>.json from <batch-dir>.
"""
import json
import statistics
import sys


def main(batch_dir, generations):
    merged = {}
    for gen in generations:
        with open(f"{batch_dir}/raw_{gen}.json", encoding="utf-8") as f:
            raw = json.load(f)
        with open(f"{batch_dir}/idmap_{gen}.json", encoding="utf-8") as f:
            idmap = json.load(f)
        missing = sorted(set(idmap) - set(raw))
        extra = sorted(set(raw) - set(idmap))
        if missing or extra:
            raise SystemExit(f"{gen}: missing {missing}, extra {extra}")
        counts = [len(v) for v in raw.values()]
        print(gen, "drafts", len(raw), "queries per draft", min(counts), statistics.median(counts), max(counts))
        merged[gen] = {idmap[k]: v[:3] for k, v in raw.items()}
    with open(f"{batch_dir}/queries.json", "w", encoding="utf-8") as f:
        json.dump(merged, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
