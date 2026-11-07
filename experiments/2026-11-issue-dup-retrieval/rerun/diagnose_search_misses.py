"""Why did search leave out a target whose text holds every term of the query? Post hoc, added after
scoring because a reviewer raised it. It runs live searches and prints counts, and it can't change
any registered figure.

Usage: py -3 rerun/diagnose_search_misses.py <issue-cache-dir> <labels.json> <queries.json> <round1.json> <out.json>

Run from the experiment directory. Queries and issue text stay local: the output has issue numbers,
counts and character offsets only.
"""
import json
import re
import subprocess
import sys
import time

sys.path.insert(0, ".")
from keyword_arm import repo_slug, search  # noqa: E402

PACE_SECONDS = 1.0


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def holds(term, text):
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None


def term_finds_target(repo, term, number, created_at):
    """Search one term on the target's creation day alone, so a common word can't push it past the limit."""
    args = ["gh", "issue", "list", "--repo", repo_slug(repo), "--state", "all", "--limit", "100"]
    args += ["--search", f"{term} in:title,body created:{created_at[:10]}", "--json", "number"]
    out = subprocess.run(args, capture_output=True, timeout=60, check=True)
    return number in [i["number"] for i in json.loads(out.stdout.decode("utf-8"))]


def main(cache_dir, labels_path, queries_path, round1_path, out_path):
    inputs = load(labels_path)["inputs"]
    queries = load(queries_path)
    round1 = load(round1_path)
    issues = {}
    for repo in {i["repo"] for i in inputs}:
        issues[repo] = {i["number"]: i for i in load(f"{cache_dir}/issues_{repo}.json")}

    cases = []
    for gen, by_input in round1.items():
        for item in inputs:
            for slot, query in enumerate(queries[gen][item["id"]]):
                listed = by_input[item["id"]]["parity"][slot]
                terms = [t.lower() for t in query.replace('"', "").split()]
                for number in item["targets"]:
                    target = issues[item["repo"]][number]
                    text = f"{target['title']}\n{target['body'] or ''}".lower()
                    if number not in listed and all(holds(t, text) for t in terms):
                        cases.append((gen, item, slot, query, terms, number, target, text, listed))

    term_cache = {}
    rows = []
    for gen, item, slot, query, terms, number, target, text, listed in cases:
        repo = item["repo"]
        again = search(repo, query, "parity", item["created_before"])
        time.sleep(PACE_SECONDS)
        no_date = search(repo, query, "parity", "2100-01-01")
        time.sleep(PACE_SECONDS)
        failing = []
        for term in terms:
            key = (repo, number, term)
            if key not in term_cache:
                term_cache[key] = term_finds_target(repo, term, number, target["createdAt"])
                time.sleep(PACE_SECONDS)
            if not term_cache[key]:
                failing.append(term)
        rows.append({
            "generation": gen, "input": item["id"], "slot": slot, "target": number,
            "terms": len(terms), "was_empty": not listed,
            "target_found_on_rerun": number in again, "empty_on_rerun": not again,
            "same_list_on_rerun": again == listed,
            "target_found_without_date_limit": number in no_date,
            "terms_not_finding_target": len(failing),
            "failing_terms_with_punctuation": sum(1 for t in failing if re.search(r"[^\w]", t)),
            "failing_terms_in_title": sum(1 for t in failing if holds(t, target["title"].lower())),
            "failing_term_offsets": [re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text).start() for t in failing],
            "text_length": len(text),
        })

    terms_found = sum(1 for v in term_cache.values() if v)
    summary = {
        "cases": len(rows),
        "inputs": sorted({r["input"] for r in rows}),
        "same_list_on_rerun": sum(r["same_list_on_rerun"] for r in rows),
        "target_found_on_rerun": sum(r["target_found_on_rerun"] for r in rows),
        "target_found_without_date_limit": sum(r["target_found_without_date_limit"] for r in rows),
        "cases_with_a_term_missing_target": sum(1 for r in rows if r["terms_not_finding_target"]),
        "distinct_terms_checked": len(term_cache),
        "distinct_terms_finding_target": terms_found,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:6])
