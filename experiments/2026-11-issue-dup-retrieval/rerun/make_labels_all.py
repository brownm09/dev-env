"""labels_all.json: the frozen 43 inputs plus the duplicate declared since the first fetch. Numbers only."""
import json
import sys

labels_path, cache_dir, out_path = sys.argv[1:4]
labels = json.load(open(labels_path, encoding="utf-8"))
issues = {i["number"]: i for i in json.load(open(f"{cache_dir}/issues_dev-env.json", encoding="utf-8"))}
# Declared in a closing comment on dev-env#1283: a duplicate of #1142.
assert issues[1142]["createdAt"] < issues[1283]["createdAt"]
labels["inputs"] = labels["inputs"] + [
    {
        "id": "A:dev-env#1283",
        "stratum": "A",
        "repo": "dev-env",
        "query_issue": 1283,
        "created_before": issues[1283]["createdAt"],
        "targets": [1142],
    }
]
with open(out_path, "w", encoding="utf-8", newline="\n") as f:
    json.dump(labels, f, indent=1)
    f.write("\n")
print(len(labels["inputs"]))
