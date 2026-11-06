"""Write one shuffled, opaque-ID batch of draft texts per generation for the query-writing agent.

Usage: py -3 make_batches.py <issue-cache-dir> <labels.json> <out-dir>

The writing agent sees sanitized draft text only: no labels, targets, issue numbers or results.
The ID map stays beside the batches. All of this is local, because the text can be private.
"""
import json
import random
import sys

from dup_eval import sanitize_query

GENERATIONS = {"g1": 11, "g2": 22, "g3": 33}


def draft_text(item, repos):
    if item["stratum"] == "A":
        issue = repos[item["repo"]][item["query_issue"]]
        return sanitize_query(f"{issue['title']}\n\n{issue['body'] or ''}")
    for comment in repos[item["repo"]][item["host_issue"]]["comments"]["nodes"]:
        if comment["createdAt"] == item["comment_created_at"]:
            return sanitize_query(comment["body"])
    raise SystemExit(f"comment not found for {item['id']}")


def main(cache_dir, labels_path, out_dir):
    with open(labels_path, encoding="utf-8") as f:
        inputs = json.load(f)["inputs"]
    repos = {}
    for repo in sorted({i["repo"] for i in inputs}):
        with open(f"{cache_dir}/issues_{repo}.json", encoding="utf-8") as f:
            repos[repo] = {i["number"]: i for i in json.load(f)}
    for gen, seed in GENERATIONS.items():
        order = list(inputs)
        random.Random(seed).shuffle(order)
        id_map, parts = {}, []
        for n, item in enumerate(order):
            opaque = f"d{n:02d}"
            id_map[opaque] = item["id"]
            parts.append(f"=== {opaque} (repository: {item['repo']})\n{draft_text(item, repos)}\n")
        with open(f"{out_dir}/batch_{gen}.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(parts))
        with open(f"{out_dir}/idmap_{gen}.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump(id_map, f)
        print(gen, len(order), "chars", sum(map(len, parts)))


if __name__ == "__main__":
    main(*sys.argv[1:4])
