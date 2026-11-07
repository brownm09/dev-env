# Duplicate-issue retrieval: results

Tracking issue: [dev-env#1260](https://github.com/brownm09/dev-env/issues/1260). Pre-registration: [comment frozen 2026-11-06T16:16:10Z](https://github.com/brownm09/dev-env/issues/1260#issuecomment-6689587273), citing commit `a4139e8`. Decision record: [ADR-157](../../docs/adr/157-duplicate-issue-retrieval-experiment.md). Re-run: [dev-env#1282](https://github.com/brownm09/dev-env/issues/1282), also inconclusive: [`rerun/results.md`](rerun/results.md).

**Result:** on 43 inputs, embedding search put the existing issue in the first 10 candidates 39 times, and keyword search 30 times. The frozen outcome map reads that as supported, but the audit's verdict is **inconclusive, confounded by threat T4**. The keyword arm couldn't retry after an empty result, half its queries returned nothing, and recovering 3 of the 7 inputs that came back empty would take the gain below the win bar.

The first audit read this as supported. [Review](https://github.com/brownm09/dev-env/pull/1276#issuecomment-6692043564) raised the sensitivity, and the owner chose to correct the verdict and re-run.

**Where the text went.** Embedding text and vectors stayed on the machine that ran this. Sanitized drafts from private repos went to the query-writing subagent through the Anthropic API, on the same terms as any Claude Code session, and the queries went to GitHub's search. This repo holds issue numbers, filing timestamps and metrics. It holds no issue text. The owner accepted the timestamps.

## Headline

| # | Measure | Vector | Keyword | Gap | Registered need |
|---|---------|--------|---------|-----|-----------------|
| 1 | **Recall@10, pooled (primary), n=43** | **0.907 (39)** | **0.698 (30)** | **+0.209** | At least +0.15 |
| 2 | Sign test across 38 families: 9 wins, 1 loss | | | p = 0.011 | At most 0.05 |
| 3 | Stratum B, a session found the issue and didn't file, n=13 | 0.769 (10) | 0.538 (7) | +0.231 | Above -0.15 |
| 4 | Stratum A at GitHub's default scope, n=30 | 0.967 (29) | 0.833 (25) | +0.133 | Above 0.05 |
| 5 | Stratum A at title and body scope, n=30 | 0.967 (29) | 0.767 (23) | +0.200 | Reported |

**Worst row.** `A:career-playbook#1768`: vector rank 20 against keyword rank 1 in all three generations. It's the only input keyword hit and vector missed. Its target's body was edited the same day to take in material from this issue, which may favour keyword and can't be separated here.

**Both missed:** `B:career-playbook#1471`, `B:career-playbook#1681`, `B:career-playbook#724`.

## What the curve shows

| # | Candidates read | Vector | Keyword |
|---|-----------------|--------|---------|
| 1 | 1 | 0.744 | 0.605 |
| 2 | 3 | 0.860 | 0.698 |
| 3 | 5 | 0.860 | 0.698 |
| 4 | 10 | 0.907 | 0.698 |
| 5 | 20 | 0.953 | 0.698 |
| 6 | 30 | 0.953 | 0.698 |

Three findings that aren't in the verdict but matter for what comes next:

1. **Keyword lists are short.** Its recall stops at 3 candidates mostly because there's almost nothing further to read: 193 of 387 queries returned nothing, and the median input returned 1 result. This says little about its ranking. It does say that retrying could matter, which is what the re-run tests.
2. **Reading load runs the other way.** Keyword search shows a session about 1 result to read. Embedding search always has a full list, and nothing in it says stop. The first-candidate row still favours vectors, so a short list is viable.
3. **A similarity threshold doesn't look able to decide what's a duplicate.** At cosine 0.85, 94% of duplicate pairs pass, and so do 43% of pairs an author declared distinct. Random pairs fall away (1%). These figures are post hoc and compare raw documents, so they don't measure a filing-time draft, and the size of that difference wasn't measured.

## Experiment audit

```
[experiment-audit] Gate 0-1 — duplicate-issue retrieval
Gate 0: PASS. Freeze 2026-11-06T16:16:10Z. First generation after it: query batch 1 at 16:16:27Z, vector arm about 16:16:33Z (inferred from its end time, since no start event was logged then).
Gate 1: the first audit said nothing undisclosed was found. Review found items it had missed, so that claim was wrong. All are listed below, and the verdict is re-read on the full ledger.
```

**Deviation ledger**

| # | Field | Deviation | Impact |
|---|-------|-----------|--------|
| 1 | 10 (provenance) | `score.py` crashed on first run with a `KeyError`, before printing anything. One line of logic changed after the freeze. | None on the outcome map. Aggregates were hand-checked from the per-input table: 39/43, 30/43, 10/13, 7/13. |
| 2 | 7 (calibration) | The hit-test known-good and known-bad rankers exercised a helper the verdict didn't use, so `score.py`'s aggregation path wasn't calibrated before scoring. The review's fix runs fixtures through the real path. It can't now precede scoring. | The real path now passes known-good 1.0 and known-bad 0.0, and reproduces `scored.json`. An independent reviewer also recomputed every headline figure. |
| 3 | 7 (calibration) | Registered instrument 4, vector ranking, can't fail on its known-good side: a unit vector's similarity to itself is 1.0. Its 0.7 limit has no source, and it skips the arm's filters. | Reclassified as a smoke check, so `calibrate_vector.py` says so. A reviewer checked the arm's output directly: 0 time-filter violations, and targets rank as listed. |
| 4 | 6 (draft text) | `B:career-playbook#1471` kept the phrase that qualified it for stratum B, because the selection and sanitizing patterns disagree on "rather than opening". | One input, both arms missed, so no count changes. Patterns left as they are for this run, and raised in #1282. |
| 5 | 10 (discards) | The rule to discard and list an empty sanitized draft wasn't implemented. | None: the shortest draft is 353 characters, so "no discards" is true. Now raises. |
| 6 | 3 (criterion 6) | Cross-repo ranks and near-duplicate scores were left out of the first report. | Diagnostic only. Now reported below. |
| 7 | Post-verdict cleanup | Review findings changed harness code after scoring: shared helpers, timeouts and input checks in `keyword_arm.py`, the store read path, `build_labels.py`'s family logic, and `k_shown` removed from `scored.json`. | Verified no effect: `labels.json` regenerates byte for byte, all 3 draft batches match, and the vector arm reads the store and reproduces `vector_results.json` exactly. `keyword_arm.py` hasn't been re-run, as that needs live searches. |

```
[experiment-audit] Gate 2 — instruments
Hit-test scorer and outcome map: calibrated, the end-to-end path only after scoring (ledger row 2).
gh search: calibrated before scoring, known-good and known-bad.
Vector ranking: smoke check, not calibrated (ledger row 3). It's an arm rather than a judge, and its output was checked directly.
Cosine pair separation stays a diagnostic, as registered.
```

```
[experiment-audit] Gate 3 — power and reporting
Registered n=43, k=3 (keyword), k=1 (vector). Achieved the same. No discards.
Keyword generations alone: 0.698, 0.651, 0.651, so the gap is 0.209 to 0.256 whichever generation is read.
Per-input table below. Worst row is stated beside the aggregate above.
Direction by repo: career-playbook 21 v 17 of 25; dev-env 9 v 6 of 9; lifting-logbook 9 v 7 of 9. No repo reverses.
Direction by stratum: no stratum reverses.
```

```
[experiment-audit] Threat sweep — duplicate-issue retrieval
```

| # | Threat | Verdict | Evidence |
|---|--------|---------|----------|
| T1 | Treatment contamination | PASS | The challenger read draft text only. The query writer loaded a CLAUDE.md naming 2 corpus pairs, which can only help the baseline. |
| T2 | Uncalibrated instrument | PASS, with a limit | The verdict-bearing scorer is calibrated, though its end-to-end path only after scoring. See ledger rows 2 and 3. |
| T3 | Circular criteria | PASS | Recall isn't built from either arm's known failure. |
| T4 | Baseline non-neutrality | **FLAG, load-bearing** | Fresh run, 3 queries per draft, with no retry. 193 of 387 queries returned nothing, and all 3 were empty on 25 of 129 generation-inputs. Recovering 3 of the 7 empty-result inputs takes the gap to 0.140, under the win bar, and all 7 take it to 0.047. That's arithmetic, not a measurement, and some real sessions don't retry at all (#1103 records a search chained with `gh issue create`). Still, the result depends on something this run can't see. |
| T5 | Processing inequality | PASS | Same stage, raw candidate list. Reading load differs and is reported. |
| T6 | Corpus bias | PASS, with a limit | Stratum A leans toward vectors by survivorship, and the registered counterweight is stratum B, where vectors also lead. Limits: searches that found the issue and left no comment are invisible, so absolute recall here understates the incumbent. Stratum B's keyword recall of 0.538 also shows it isn't proven keyword-friendly: a comment makes a weaker draft than an issue, and sanitizing removed up to 60% of some comments (table below). At 10 against 7 of 13 it isn't significant alone. Trust the direction, not the levels. |
| T7 | Power and stopping | PASS | n and k fixed, one primary, no stopping. |
| T8 | Peeking | PASS | Bar frozen first. The scorer fix came before any output existed. The later verdict change makes the conclusion weaker, not stronger. |
| T9 | Judge contamination | PASS | Scoring is a script. Writers had separate contexts, model verified as `claude-sonnet-5-5` from transcripts. Residual: one writer saw all 43 drafts at once, including drafts from one family. |
| T10 | Criterion substitution | PASS, with a limit | Read off the primary criterion. Limit: recall measures whether the issue was surfaced, not whether a reader recognised it. |

```
[experiment-audit] Verdict — duplicate-issue retrieval
Verdict: inconclusive — confounded by T4 (baseline non-neutrality).
The frozen outcome map alone reads supported: gap +0.209, p 0.011, guards clear. A flag on a
load-bearing threat forces inconclusive under Gate 4.
Scope: these 43 declared cases in dev-env, career-playbook and lifting-logbook, under
BAAI/bge-small-en-v1.5 (fastembed 0.9.1, onnxruntime 1.31.1), title and body text only, against
one-shot keyword queries written by claude-sonnet-5-5 and run through gh issue list --search on
2026-11-06, compared at the raw candidate list.
Decision: proper re-run with a keyword baseline that can retry (#1282). Neither adopt nor reject.
The helper in #1275 waits on that result.
```

## Spend, estimated against actual

Dollars are API-equivalent from `claude/scripts/_token_pricing.py` at the 1-hour cache-write rate. The plan's limits are what's actually consumed.

| # | Stage | Estimated | Actual | Stop rule fired? |
|---|-------|-----------|--------|------------------|
| 1 | Query writing | $1.75 | **$1.73** ($0.558, $0.596, $0.577), plus the $0.40 pilot | No |
| 2 | Keyword search | At most 657 calls, about 19 min | 573 calls, 1,202 s | No |
| 3 | Embedding | 2,996 documents, about 12 min, $0 | 2,996 documents, 618 s, $0 | No |
| 4 | Main session | About $5 from the freeze on | $8.45 for all 40 turns up to scoring, including design before the freeze | No rule set |

The main-session figure is a lower bound, because it stops at scoring and excludes the review that followed. No call failed, so the stage figures have no unpriced gap. A 16 GB GPU was available and not needed.

## Privacy scan

`DUP_EVAL_ISSUE_CACHE=<local issue cache> py -3 claude/scripts/tests/test_issue_dup_eval.py` passes against 1,614 titles from the private career-playbook and cover-letter-runtime repos.

**What it checks:** full verbatim titles, in the files directly in this directory and in ADR-157, with JSON decoded first. CI can't run it, and skips it visibly. It doesn't check bodies, comments or partial titles. A reviewer's separate one-off check found 0 title hits across the whole PR diff and body, and only boilerplate in 7-word overlaps between the prose files and private text.

Sanitized queries have SHA-256 `da0096c309b0c0c571bd4e5685cde29c223bcc48472a143907dad4b70396ea2f`. They aren't committed, since they carry words from private issues.

## Reproduce the map's reading

From this directory, with `<out>` any path outside the repo:

```bash
py -3 score.py labels.json vector_results.json keyword_results.json <out>
```

The query-writer prompt is the one used for all 3 batches: it gave the team's search rule, asked for up to 3 keyword searches per draft with no qualifiers, and said not to run any searches or read any other file. See [`README.md`](README.md) for the full run order.

## Per-input results and diagnostics

Generated by `report.py`. A dash means the target wasn't returned. Stratum A is a filed duplicate, and stratum B is a session that found the issue and didn't file.

| # | Input | Vector rank | Keyword ranks, 3 generations | Vector hit | Keyword hit | Keyword returned (median) |
|---|---|---|---|---|---|---|
| 1 | A:career-playbook#1114 | 6 | 3, -, - | yes | no | 3 |
| 2 | A:career-playbook#1247 | 1 | 2, 2, 3 | yes | yes | 3 |
| 3 | A:career-playbook#1505 | 1 | 1, 1, 1 | yes | yes | 1 |
| 4 | A:career-playbook#1523 | 1 | 1, 1, 1 | yes | yes | 1 |
| 5 | A:career-playbook#1590 | 1 | -, 1, 1 | yes | yes | 1 |
| 6 | A:career-playbook#1594 | 1 | 1, 1, - | yes | yes | 1 |
| 7 | A:career-playbook#1596 | 1 | -, -, - | yes | no | 0 |
| 8 | A:career-playbook#1608 | 1 | 1, 1, 1 | yes | yes | 3 |
| 9 | A:career-playbook#1702 | 2 | 1, 1, 2 | yes | yes | 3 |
| 10 | A:career-playbook#1731 | 1 | 1, 1, 1 | yes | yes | 1 |
| 11 | A:career-playbook#1768 | 20 | 1, 1, 1 | no | yes | 1 |
| 12 | A:career-playbook#1769 | 2 | -, -, - | yes | no | 5 |
| 13 | A:career-playbook#2000 | 1 | -, 1, - | yes | no | 0 |
| 14 | A:career-playbook#2014 | 1 | -, -, - | yes | no | 0 |
| 15 | A:career-playbook#268 | 1 | 1, 1, 1 | yes | yes | 1 |
| 16 | A:career-playbook#709 | 1 | 1, 1, 1 | yes | yes | 1 |
| 17 | A:career-playbook#930 | 1 | 1, 1, 1 | yes | yes | 3 |
| 18 | A:dev-env#495 | 1 | 1, 1, 1 | yes | yes | 5 |
| 19 | A:dev-env#627 | 1 | 2, 1, 1 | yes | yes | 3 |
| 20 | A:dev-env#862 | 3 | -, -, - | yes | no | 5 |
| 21 | A:dev-env#899 | 1 | 1, 1, 1 | yes | yes | 1 |
| 22 | A:dev-env#918 | 1 | 1, 1, 1 | yes | yes | 3 |
| 23 | A:lifting-logbook#1080 | 1 | -, -, - | yes | no | 0 |
| 24 | A:lifting-logbook#276 | 1 | 1, 1, 1 | yes | yes | 2 |
| 25 | A:lifting-logbook#612 | 1 | 1, 1, 1 | yes | yes | 5 |
| 26 | A:lifting-logbook#638 | 1 | 1, 1, 1 | yes | yes | 1 |
| 27 | A:lifting-logbook#768 | 1 | 2, 2, 2 | yes | yes | 6 |
| 28 | A:lifting-logbook#798 | 1 | 1, 1, 1 | yes | yes | 2 |
| 29 | A:lifting-logbook#803 | 1 | 1, 1, 1 | yes | yes | 2 |
| 30 | A:lifting-logbook#854 | 1 | 1, -, 2 | yes | yes | 3 |
| 31 | B:career-playbook#1471 | - | -, -, - | no | no | 0 |
| 32 | B:career-playbook#1575 | 1 | 2, 2, 1 | yes | yes | 2 |
| 33 | B:career-playbook#1659 | 3 | 1, 1, - | yes | yes | 2 |
| 34 | B:career-playbook#1681 | 18 | 4, -, - | no | no | 3 |
| 35 | B:career-playbook#724 | - | -, -, - | no | no | 0 |
| 36 | B:career-playbook#839 | 1 | 1, -, 1 | yes | yes | 1 |
| 37 | B:career-playbook#842 | 1 | 1, 1, 1 | yes | yes | 1 |
| 38 | B:career-playbook#916 | 1 | 1, -, 1 | yes | yes | 1 |
| 39 | B:dev-env#1029 | 6 | -, -, - | yes | no | 1 |
| 40 | B:dev-env#1086 | 1 | -, -, - | yes | no | 1 |
| 41 | B:dev-env#903 | 2 | 1, 1, 1 | yes | yes | 4 |
| 42 | B:dev-env#945 | 1 | -, 1, 1 | yes | yes | 4 |
| 43 | B:lifting-logbook#899 | 1 | -, -, - | yes | no | 0 |

**Per generation, keyword recall@10 and gap:**

- g1: keyword 0.698, gap 0.209
- g2: keyword 0.651, gap 0.256
- g3: keyword 0.651, gap 0.256

**Per repo, hits out of inputs:**

- career-playbook: vector 21, keyword 17, of 25
- dev-env: vector 9, keyword 6, of 9
- lifting-logbook: vector 9, keyword 7, of 9

**Keyword searches that returned nothing (title and body scope):**

- 193 of 387 queries returned nothing
- All queries came back empty on 25 of 129 generation-inputs
- 7 of 13 keyword misses returned nothing in most generations

**Sensitivity to the keyword arm recovering those empty-result inputs (upper-bound arithmetic, not a measurement):**

| # | Inputs recovered | Keyword hits | Gap | Clears the 0.15 win bar? |
|---|---|---|---|---|
| 1 | 0 | 30 | 0.209 | yes |
| 2 | 1 | 31 | 0.186 | yes |
| 3 | 2 | 32 | 0.163 | yes |
| 4 | 3 | 33 | 0.140 | no |
| 5 | 4 | 34 | 0.116 | no |
| 6 | 5 | 35 | 0.093 | no |
| 7 | 6 | 36 | 0.070 | no |
| 8 | 7 | 37 | 0.047 | no |

**Pre-registered diagnostics (criteria 5 and 6), not verdict-bearing:**

- Pair separation, as the chance a duplicate pair outscores another pair: 0.914 against declared non-duplicates, 0.998 against random pairs
- Cross-repo, vector arm only: lifting-logbook#638 finds dev-env#527 at rank 2 of 916
- Cross-repo, vector arm only: career-playbook#1065 finds dev-env#876 at rank 9 of 1571
- Backlog-audit near-duplicate, dev-env#424 and #785: cosine 0.847
- Backlog-audit near-duplicate, dev-env#566 and #571: cosine 0.866
- Backlog-audit near-duplicate, dev-env#594 and #609: cosine 0.826
- Backlog-audit near-duplicate, dev-env#510 and #514: cosine 0.808

**Cosine thresholds (post hoc, descriptive): share of each pair class at or above t.** These compare raw documents, with no sanitizing, unlike the ranking, so they don't measure a filing-time draft. The duplicate class is 35 pairs from 30 inputs and isn't independent.

- t=0.80: duplicate 0.97, declared non-duplicate 0.61, random 0.03
- t=0.85: duplicate 0.94, declared non-duplicate 0.43, random 0.01
- t=0.90: duplicate 0.77, declared non-duplicate 0.13, random 0.00

**Without inputs both arms hit at rank 1 in every generation:** n=27, vector 23, keyword 14

**Bodies edited after the input's filing time:** 1 (A:career-playbook#1768 -> #1767)

**Share of each stratum B comment kept after sanitizing:**

- B:career-playbook#1471: 99%
- B:career-playbook#1575: 93%
- B:career-playbook#1659: 86%
- B:career-playbook#1681: 83%
- B:career-playbook#724: 40%
- B:career-playbook#839: 72%
- B:career-playbook#842: 87%
- B:career-playbook#916: 80%
- B:dev-env#1029: 51%
- B:dev-env#1086: 88%
- B:dev-env#903: 70%
- B:dev-env#945: 96%
- B:lifting-logbook#899: 83%
