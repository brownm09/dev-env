"""Vector arm: rank earlier issues in the same repo by cosine similarity to the input's text.

Usage: <venv-python> vector_arm.py <issue-cache-dir> <labels.json> <store-dir> <out.json> <run_log.jsonl>

Model and text template are fixed before any result and are not tuned. Issue text and vectors stay
in <store-dir>, on this machine. The output holds issue numbers and scores only.

Store: one flat .npz per repo, searched by brute force. See ADR-156 for why nothing heavier is used.
"""
import json
import os
import sys
import time

import numpy as np
from fastembed import TextEmbedding

from dup_eval import sanitize_query

MODEL = "BAAI/bge-small-en-v1.5"
REPOS = ["dev-env", "career-playbook", "lifting-logbook", "cover-letter-runtime"]
TOP = 50


def doc_text(issue):
    return f"{issue['title']}\n\n{issue['body'] or ''}"


def log(path, **event):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}) + "\n")


def unit(rows):
    rows = np.asarray(rows, dtype=np.float32)
    return rows / np.linalg.norm(rows, axis=1, keepdims=True)


def query_text(item, repos):
    if item["stratum"] == "A":
        issue = repos[item["repo"]][item["query_issue"]]
        return sanitize_query(f"{issue['title']}\n\n{issue['body'] or ''}")
    for comment in repos[item["repo"]][item["host_issue"]]["comments"]["nodes"]:
        if comment["createdAt"] == item["comment_created_at"]:
            return sanitize_query(comment["body"])
    raise SystemExit(f"comment not found for {item['id']}")


def main(cache_dir, labels_path, store_dir, out_path, log_path):
    with open(labels_path, encoding="utf-8") as f:
        labels = json.load(f)
    repos = {}
    for repo in REPOS:
        with open(f"{cache_dir}/issues_{repo}.json", encoding="utf-8") as f:
            repos[repo] = {i["number"]: i for i in json.load(f)}
    os.makedirs(store_dir, exist_ok=True)
    model = TextEmbedding(MODEL)
    started = time.time()
    numbers, created, vectors = {}, {}, {}
    docs = 0
    for repo in REPOS:
        ordered = sorted(repos[repo])
        numbers[repo] = np.array(ordered)
        created[repo] = [repos[repo][n]["createdAt"] for n in ordered]
        vectors[repo] = unit(list(model.embed([doc_text(repos[repo][n]) for n in ordered])))
        np.savez(f"{store_dir}/{repo}.npz", numbers=numbers[repo], vectors=vectors[repo])
        docs += len(ordered)
    log(log_path, stage="embed_corpus", docs=docs, seconds=round(time.time() - started), model=MODEL)

    ranks, scores = {}, {}
    started = time.time()
    query_vectors = unit(list(model.embed([query_text(i, repos) for i in labels["inputs"]])))
    for item, vector in zip(labels["inputs"], query_vectors):
        repo = item["repo"]
        eligible = np.array([c < item["created_before"] for c in created[repo]])
        if item["stratum"] == "A":
            eligible &= numbers[repo] != item["query_issue"]
        sims = vectors[repo] @ vector
        sims[~eligible] = -2.0
        top = np.argsort(-sims)[:TOP]
        top = top[sims[top] > -2.0]
        ranks[item["id"]] = [int(numbers[repo][i]) for i in top]
        scores[item["id"]] = [round(float(sims[i]), 4) for i in top]
    log(log_path, stage="rank_inputs", inputs=len(labels["inputs"]), seconds=round(time.time() - started, 2))

    def pair_score(repo, a, b):
        ia = int(np.where(numbers[repo] == a)[0][0])
        ib = int(np.where(numbers[repo] == b)[0][0])
        return round(float(vectors[repo][ia] @ vectors[repo][ib]), 4)

    def duplicate_pairs():
        for item in labels["inputs"]:
            if item["stratum"] == "A":
                for target in item["targets"]:
                    yield item["repo"], target, item["query_issue"]

    pairs = {
        "duplicate": [[r, a, b, pair_score(r, a, b)] for r, a, b in duplicate_pairs()],
        "known_negative": [[r, a, b, pair_score(r, a, b)] for r, a, b in labels["known_negative"]],
        "easy_negative": [[r, a, b, pair_score(r, a, b)] for r, a, b in labels["easy_negative"]],
        "auditor_near_dup": [[r, a, b, pair_score(r, a, b)] for r, a, b in labels["auditor_near_dup"]],
    }

    cross = []
    for case in labels["cross_repo"]:
        repo_a, num_a = case["a"]
        repo_b, num_b = case["b"]
        first, later = (
            ((repo_a, num_a), (repo_b, num_b))
            if repos[repo_a][num_a]["createdAt"] < repos[repo_b][num_b]["createdAt"]
            else ((repo_b, num_b), (repo_a, num_a))
        )
        before = repos[later[0]][later[1]]["createdAt"]
        issue = repos[later[0]][later[1]]
        vector = unit(list(model.embed([sanitize_query(doc_text(issue))])))[0]
        pool = []
        for repo in REPOS:
            sims = vectors[repo] @ vector
            for n, c, s in zip(numbers[repo], created[repo], sims):
                if c < before:
                    pool.append((float(s), repo, int(n)))
        pool.sort(reverse=True)
        rank = next((i for i, p in enumerate(pool, 1) if p[1:] == first), None)
        cross.append({"query": list(later), "target": list(first), "rank_in_merged_pool": rank, "pool": len(pool)})

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"model": MODEL, "ranks": ranks, "scores": scores, "pairs": pairs, "cross_repo": cross}, f)
    print(f"docs={docs} inputs={len(ranks)}")


if __name__ == "__main__":
    main(*sys.argv[1:6])
