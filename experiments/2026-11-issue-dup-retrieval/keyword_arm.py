"""Keyword arm: replay `gh issue list --search` for each input as of its filing time.

Usage: py -3 keyword_arm.py <labels.json> <queries.json> <out.json> <run_log.jsonl>

queries.json holds {generation: {input_id: [query, ...]}} and stays out of the repo, because queries
written from private issues carry private words. They do go to GitHub's search. The output holds
issue numbers only, and appears only when every generation has finished.

A failed `gh` call aborts the run. It must never read as an empty result, since an empty result
scores as a miss.
"""
import json
import os
import subprocess
import sys
import time

from dup_eval import log_event

OWNERS = {"lifting-logbook": "merickvaughn"}
SCOPES = {"parity": " in:title,body", "default": ""}
PACE_SECONDS = 1.0
CALL_TIMEOUT_SECONDS = 60
ATTEMPTS = 4


def repo_slug(repo):
    return f"{OWNERS.get(repo, 'brownm09')}/{repo}"


def search(repo, query, scope, created_before):
    full = f"{query}{SCOPES[scope]} created:<{created_before}"
    args = ["gh", "issue", "list", "--repo", repo_slug(repo), "--state", "all", "--limit", "30"]
    args += ["--search", full, "--json", "number"]
    error = ""
    for attempt in range(ATTEMPTS):
        try:
            out = subprocess.run(args, capture_output=True, timeout=CALL_TIMEOUT_SECONDS)
            if out.returncode == 0:
                return [i["number"] for i in json.loads(out.stdout.decode("utf-8"))]
            error = out.stderr.decode("utf-8", "replace")[:400]
        except subprocess.TimeoutExpired:
            error = f"timed out after {CALL_TIMEOUT_SECONDS} s"
        if attempt < ATTEMPTS - 1:
            time.sleep(30 * (attempt + 1))
    raise SystemExit(f"gh failed after {ATTEMPTS} attempts: {error}")


def check_queries(inputs, generations):
    """Every input needs at least one non-blank query per generation. A missing query would score
    as a miss, and a blank one would search on qualifiers alone."""
    for gen, by_input in generations.items():
        for item in inputs:
            queries = by_input.get(item["id"])
            if not queries or not all(q.strip() for q in queries):
                raise SystemExit(f"{gen}: missing or blank queries for {item['id']}")


def main(labels_path, queries_path, out_path, log_path):
    with open(labels_path, encoding="utf-8") as f:
        inputs = json.load(f)["inputs"]
    with open(queries_path, encoding="utf-8") as f:
        generations = json.load(f)
    check_queries(inputs, generations)
    cache, calls, started = {}, 0, time.time()
    log_event(log_path, stage="keyword_search", event="start", generations=len(generations))
    results = {}
    for gen, by_input in generations.items():
        results[gen] = {}
        for item in inputs:
            per_scope = {}
            scopes = ["parity", "default"] if item["stratum"] == "A" else ["parity"]
            for scope in scopes:
                lists = []
                for query in by_input[item["id"]]:
                    key = (item["repo"], query, scope, item["created_before"])
                    if key not in cache:
                        cache[key] = search(item["repo"], query, scope, item["created_before"])
                        calls += 1
                        time.sleep(PACE_SECONDS)
                    lists.append(cache[key])
                per_scope[scope] = lists
            results[gen][item["id"]] = per_scope
        log_event(log_path, stage="keyword_search", event="generation_done", generation=gen, calls=calls)
    partial = out_path + ".partial"
    with open(partial, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f)
    os.replace(partial, out_path)
    log_event(log_path, stage="keyword_search", event="end", calls=calls, seconds=round(time.time() - started))
    print(f"calls={calls} seconds={round(time.time() - started)}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
