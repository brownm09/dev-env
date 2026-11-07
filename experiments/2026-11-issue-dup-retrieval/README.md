# Duplicate-issue retrieval experiment

Compares embedding search with `gh issue list --search` for finding an existing issue before filing a duplicate. Tracking issue: [dev-env#1260](https://github.com/brownm09/dev-env/issues/1260). Verdict and audit: [`results.md`](results.md). Decision: [ADR-157](../../docs/adr/157-duplicate-issue-retrieval-experiment.md). The re-run is tracked in [dev-env#1282](https://github.com/brownm09/dev-env/issues/1282), and its verdict and audit are in [`rerun/results.md`](rerun/results.md).

## What's where

| # | File | Purpose |
|---|---|---|
| 1 | `labels.json` | The evaluation set: issue numbers, filing timestamps and strata |
| 2 | `dup_eval.py` | Scoring helpers and the frozen outcome map, standard library only |
| 3 | `score.py`, `report.py` | Read the arm outputs, and print the verdict inputs and the results tables |
| 4 | `vector_results.json`, `keyword_results.json`, `scored.json`, `run_log.jsonl` | Committed output of the run, numbers only |
| 5 | `requirements.txt` | Pinned packages for the vector arm. Everything else is standard library |
| 6 | `retry_round.py`, `calibrate_search.py`, `report_rerun.py` | The re-run's reformulation round, live search check and report |
| 7 | `rerun/` | The re-run's output, numbers only, plus its results and stage boundaries. Scripts: `make_labels_all.py` builds `labels_all.json`, and `mine_search_behaviour.py` and `diagnose_search_misses.py` read private data locally and print counts |

## Run order

Issue text, batches, queries and vectors stay out of this repo. `CACHE`, `BATCH` and `STORE` are local directories outside it.

```bash
py -3 fetch_issues.py brownm09/dev-env "$CACHE"                     # also career-playbook, lifting-logbook (merickvaughn), cover-letter-runtime, win11-init-tools
py -3 build_labels.py "$CACHE" labels.json                          # labels are frozen; this only regenerates them
py -3 make_batches.py "$CACHE" labels.json "$BATCH"
# A query-writing agent reads each batch_<gen>.txt and writes raw_<gen>.json. Its prompt is in results.md.
py -3 merge_queries.py "$BATCH" g1 g2 g3
py -3 keyword_arm.py labels.json "$BATCH/queries.json" keyword_results.json run_log.jsonl
<venv-python> calibrate_vector.py "$CACHE"                          # a smoke check, not a calibration
<venv-python> vector_arm.py "$CACHE" labels.json "$STORE" vector_results.json run_log.jsonl
py -3 score.py labels.json vector_results.json keyword_results.json scored.json
py -3 report.py labels.json vector_results.json keyword_results.json scored.json "$CACHE"
```

The re-run replaces the keyword step with these, using `rerun/labels_all.json`, built by `rerun/make_labels_all.py`, which adds one fresh input. The writers' prompts are in `rerun/results.md` and the pre-registration:

```bash
py -3 calibrate_search.py "$CACHE" rerun/run_log.jsonl
py -3 keyword_arm.py rerun/labels_all.json "$BATCH/queries.json" rerun/keyword_round1.json rerun/run_log.jsonl
py -3 retry_round.py batch "$CACHE" rerun/labels_all.json "$BATCH/queries.json" rerun/keyword_round1.json "$BATCH"
# A fresh writer per generation reads retry_<gen>.txt and writes raw_retry_<gen>.json.
py -3 retry_round.py run rerun/labels_all.json "$BATCH/queries.json" rerun/keyword_round1.json "$BATCH" rerun/keyword_final.json rerun/retry_counts.json rerun/run_log.jsonl
py -3 score.py labels.json rerun/vector_results.json rerun/keyword_round1.json rerun/scored_round1.json
py -3 score.py labels.json rerun/vector_results.json rerun/keyword_final.json rerun/scored_final.json
py -3 report_rerun.py labels.json rerun/labels_all.json rerun/vector_results.json rerun/keyword_round1.json rerun/keyword_final.json rerun/scored_round1.json rerun/scored_final.json rerun/retry_counts.json
```

Run these from this directory, because the scripts import `dup_eval`. The tests are at `claude/scripts/tests/test_issue_dup_eval.py`.
