"""The keyword arm's reformulation round (dev-env#1282): a query that returned nothing may be replaced once.

Usage:
  py -3 retry_round.py batch <issue-cache-dir> <labels.json> <queries.json> <round1.json> <batch-dir>
  # A query-writing agent reads each retry_<gen>.txt and writes raw_retry_<gen>.json.
  py -3 retry_round.py run <labels.json> <queries.json> <round1.json> <batch-dir> <final.json> <counts.json> <run_log.jsonl>

The writer sees the sanitized draft, its own queries, and which of them returned nothing, under the
opaque IDs of the first round. It never sees results, labels or targets. Batches and queries stay out
of the repo, because they can carry private words. final.json has the same shape as round1.json, so
score.py reads it unchanged.
"""
import json
import os
import re
import sys
import time

from dup_eval import draft_text, log_event
from keyword_arm import PACE_SECONDS, search

_QUALIFIER = re.compile(r"\b[a-z]+:\S")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def empty_slots(lists):
    return [n for n, results in enumerate(lists) if not results]


def retry_text(opaque, repo, draft, queries, parity_lists):
    """One draft's section of the batch, or None when no query came back empty."""
    empty = empty_slots(parity_lists)
    if not empty:
        return None
    lines = [f"=== {opaque} (repository: {repo})", draft, "", "Your searches:"]
    for n, query in enumerate(queries):
        lines.append(f"- {query} -> {'returned nothing' if n in empty else 'returned results'}")
    return "\n".join(lines) + "\n"


def check_replacements(opaque, replacements, empty_count):
    """Every empty query gets exactly one replacement. A dropped one would score against keyword search."""
    if len(replacements) != empty_count:
        raise SystemExit(f"{opaque}: {len(replacements)} replacements for {empty_count} empty queries")
    for query in replacements:
        if not query.strip() or _QUALIFIER.search(query):
            raise SystemExit(f"{opaque}: blank query or qualifier in a replacement")


def merge_round(per_scope, replacements_by_slot):
    """A round-one result with each replaced slot filled in. Slots are chosen on title-and-body scope,
    and a query empty there can still have default-scope results, which a replacement must not lose."""
    merged = {scope: list(lists) for scope, lists in per_scope.items()}
    for slot, by_scope in replacements_by_slot.items():
        for scope, results in by_scope.items():
            if not merged[scope][slot]:
                merged[scope][slot] = results
    return merged


def check_all_replacements(raw, reverse, round1_gen):
    """Validate one generation's writer output before any search runs."""
    unknown = set(raw) - set(reverse.values())
    if unknown:
        raise SystemExit(f"unknown draft IDs: {sorted(unknown)}")
    for iid, per_scope in round1_gen.items():
        check_replacements(reverse[iid], raw.get(reverse[iid], []), len(empty_slots(per_scope["parity"])))


def batch(cache_dir, labels_path, queries_path, round1_path, batch_dir):
    inputs = {i["id"]: i for i in load(labels_path)["inputs"]}
    repos = {}
    for repo in {i["repo"] for i in inputs.values()}:
        repos[repo] = {i["number"]: i for i in load(f"{cache_dir}/issues_{repo}.json")}
    queries, round1 = load(queries_path), load(round1_path)
    for gen in sorted(queries):
        parts = []
        for opaque, iid in load(f"{batch_dir}/idmap_{gen}.json").items():
            item = inputs[iid]
            section = retry_text(opaque, item["repo"], draft_text(item, repos), queries[gen][iid], round1[gen][iid]["parity"])
            if section:
                parts.append(section)
        with open(f"{batch_dir}/retry_{gen}.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(parts))
        print(gen, "drafts with an empty query", len(parts), "chars", sum(map(len, parts)))


def run(labels_path, queries_path, round1_path, batch_dir, final_path, counts_path, log_path):
    inputs = {i["id"]: i for i in load(labels_path)["inputs"]}
    round1 = load(round1_path)
    cache, calls, started = {}, 0, time.time()
    log_event(log_path, stage="retry_search", event="start")
    final, counts = {}, {}
    for gen in sorted(round1):
        final[gen], counts[gen] = {}, {}
        raw = load(f"{batch_dir}/raw_retry_{gen}.json")
        idmap = load(f"{batch_dir}/idmap_{gen}.json")
        reverse = {iid: opaque for opaque, iid in idmap.items()}
        check_all_replacements(raw, reverse, round1[gen])
        for iid, per_scope in round1[gen].items():
            item, empty = inputs[iid], empty_slots(per_scope["parity"])
            by_slot = {}
            for slot, query in zip(empty, raw.get(reverse[iid], [])):
                by_slot[slot] = {}
                for scope in per_scope:
                    if per_scope[scope][slot]:
                        continue
                    key = (item["repo"], query, scope, item["created_before"])
                    if key not in cache:
                        cache[key] = search(item["repo"], query, scope, item["created_before"])
                        calls += 1
                        time.sleep(PACE_SECONDS)
                    by_slot[slot][scope] = cache[key]
            final[gen][iid] = merge_round(per_scope, by_slot)
            counts[gen][iid] = {"empty": len(empty), "replaced": len(by_slot), "still_empty": len(empty_slots(final[gen][iid]["parity"]))}
        log_event(log_path, stage="retry_search", event="generation_done", generation=gen, calls=calls)
    for path, data in ((final_path, final), (counts_path, counts)):
        partial = path + ".partial"
        with open(partial, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f)
        os.replace(partial, path)
    log_event(log_path, stage="retry_search", event="end", calls=calls, seconds=round(time.time() - started))
    print(f"calls={calls} seconds={round(time.time() - started)}")


if __name__ == "__main__":
    {"batch": batch, "run": run}[sys.argv[1]](*sys.argv[2:])
