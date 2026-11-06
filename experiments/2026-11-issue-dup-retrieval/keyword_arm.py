"""Keyword arm: replay `gh issue list --search` for each input as of its filing time.

Usage: py -3 keyword_arm.py <labels.json> <queries.json> <out.json> <run_log.jsonl>

queries.json holds {generation: {input_id: [query, ...]}} and stays local, because queries written
from private issues carry private words. The output holds issue numbers only.

A failed `gh` call aborts the run. It must never read as an empty result, since an empty result
scores as a miss.
"""
import json
import subprocess
import sys
import time

OWNERS = {"lifting-logbook": "merickvaughn"}
SCOPES = {"parity": " in:title,body", "default": ""}
PACE_SECONDS = 1.0


def repo_slug(repo):
    return f"{OWNERS.get(repo, 'brownm09')}/{repo}"


def search(repo, query, scope, created_before):
    full = f"{query}{SCOPES[scope]} created:<{created_before}"
    args = ["gh", "issue", "list", "--repo", repo_slug(repo), "--state", "all", "--limit", "30"]
    args += ["--search", full, "--json", "number"]
    for attempt in range(4):
        out = subprocess.run(args, capture_output=True)
        if out.returncode == 0:
            return [i["number"] for i in json.loads(out.stdout.decode("utf-8"))]
        time.sleep(30 * (attempt + 1))
    raise SystemExit("gh failed after retries: " + out.stderr.decode("utf-8", "replace")[:400])


def log(path, **event):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}) + "\n")


def main(labels_path, queries_path, out_path, log_path):
    with open(labels_path, encoding="utf-8") as f:
        inputs = json.load(f)["inputs"]
    with open(queries_path, encoding="utf-8") as f:
        generations = json.load(f)
    missing = [i["id"] for g in generations.values() for i in inputs if i["id"] not in g]
    if missing:
        raise SystemExit(f"queries.json lacks inputs: {missing}")
    cache, calls, started = {}, 0, time.time()
    log(log_path, stage="keyword_search", event="start", generations=len(generations))
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
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(results, f)
        log(log_path, stage="keyword_search", event="generation_done", generation=gen, calls=calls)
    log(log_path, stage="keyword_search", event="end", calls=calls, seconds=round(time.time() - started))
    print(f"calls={calls} seconds={round(time.time() - started)}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
