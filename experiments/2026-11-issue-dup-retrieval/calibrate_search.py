"""Live check of the keyword arm's search before any arm is scored (dev-env#1282).

Usage: py -3 calibrate_search.py <issue-cache-dir> <run_log.jsonl>

Known-good: a public issue's own title finds that issue, at both scopes. Known-bad: a string that
occurs nowhere returns an empty list. Issue dev-env#610 is public, and the log holds numbers only.
"""
import json
import sys

from dup_eval import log_event
from keyword_arm import search

KNOWN_ISSUE = 610
NONSENSE = "zzqxvjk wqpzmxv"


def main(cache_dir, log_path):
    with open(f"{cache_dir}/issues_dev-env.json", encoding="utf-8") as f:
        issue = next(i for i in json.load(f) if i["number"] == KNOWN_ISSUE)
    words = " ".join(w for w in issue["title"].split() if ":" not in w)
    before = "2026-11-06T00:00:00Z"
    good = {scope: KNOWN_ISSUE in search("dev-env", words, scope, before) for scope in ("parity", "default")}
    bad = {scope: search("dev-env", NONSENSE, scope, before) for scope in ("parity", "default")}
    passed = all(good.values()) and not any(bad.values())
    log_event(log_path, stage="calibrate_search", known_good=good, known_bad_results=sum(map(len, bad.values())), passed=passed)
    if not passed:
        raise SystemExit(f"search calibration failed: good={good} bad={bad}")
    print("search calibration passed")


if __name__ == "__main__":
    main(*sys.argv[1:3])
