# Duplicate-issue retrieval experiment

Compares embedding search with `gh issue list --search` for finding an existing issue before filing a duplicate. Tracking issue: [dev-env#1260](https://github.com/brownm09/dev-env/issues/1260). Verdict and audit: [`results.md`](results.md). Decision: [ADR-157](../../docs/adr/157-duplicate-issue-retrieval-experiment.md). The re-run is tracked in [dev-env#1282](https://github.com/brownm09/dev-env/issues/1282).

## What's where

| # | File | Purpose |
|---|---|---|
| 1 | `labels.json` | The evaluation set: issue numbers, filing timestamps and strata |
| 2 | `dup_eval.py` | Scoring helpers and the frozen outcome map, standard library only |
| 3 | `score.py`, `report.py` | Read the arm outputs, and print the verdict inputs and the results tables |
| 4 | `vector_results.json`, `keyword_results.json`, `scored.json`, `run_log.jsonl` | Committed output of the run, numbers only |
| 5 | `requirements.txt` | Pinned packages for the vector arm. Everything else is standard library |

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

Run these from this directory, because the scripts import `dup_eval`. The tests are at `claude/scripts/tests/test_issue_dup_eval.py`.
