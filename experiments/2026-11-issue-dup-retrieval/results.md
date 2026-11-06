# Duplicate-issue retrieval: results

Tracking issue: [dev-env#1260](https://github.com/brownm09/dev-env/issues/1260). Pre-registration: [comment frozen 2026-11-06T16:16:10Z](https://github.com/brownm09/dev-env/issues/1260#issuecomment-6689587273), citing commit `a4139e8`. Decision record: [ADR-157](../../docs/adr/157-duplicate-issue-retrieval-experiment.md).

**Result:** on 43 inputs, embedding search put the existing issue in the first 10 candidates 39 times, and keyword search 30 times. The registered verdict is **supported**.

This file carries issue numbers and metrics only. Issue text, queries and vectors stayed on the machine that ran this.

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

Three findings that aren't in the verdict but matter for what to build:

1. **Reading more doesn't rescue keyword search.** Its recall stops at 3 candidates. The median input returned 1 result, and 7 of its 13 misses returned nothing in most generations.
2. **Reading load runs the other way.** Keyword search shows a session about 1 result to read. Embedding search always has a full list, and nothing in it says stop. The first-candidate row still favours vectors, so a short list is viable.
3. **A similarity threshold can't decide what's a duplicate.** At cosine 0.85, 94% of duplicates pass, and so do 43% of pairs an author declared distinct. Random pairs fall away (1%), so it can rank and screen, but it can't judge. These thresholds are post hoc and descriptive.

## Experiment audit

```
[experiment-audit] Gate 0-1 — duplicate-issue retrieval
Gate 0: PASS. Freeze 2026-11-06T16:16:10Z. First generation after it: query batch 1 at 16:16:27Z, vector arm about 16:16:33Z.
Gate 1: one deviation, listed below. Nothing undisclosed found.
```

**Deviation ledger**

| # | Field | Deviation | Impact |
|---|-------|-----------|--------|
| 1 | 10 (provenance) | `score.py` crashed on first run with a `KeyError`, before printing anything. One line of logic changed after the freeze. `git diff a4139e8 -- experiments` also shows two docstring lines, in `keyword_arm.py` and `vector_arm.py`, changed afterwards with no effect on behaviour. New files added afterwards: `report.py`, results, data. | None on the outcome map, which is in `dup_eval.py`, unchanged. The aggregates were also checked by hand from the per-input table: 39/43, 30/43, 10/13, 7/13. |

```
[experiment-audit] Gate 2 — instruments
All four registered instruments were calibrated before arm scoring and passed. Nothing quarantined.
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
| T2 | Uncalibrated instrument | PASS | Scorer, outcome map, `gh` search and vector ranking all calibrated first. |
| T3 | Circular criteria | PASS | Recall isn't built from either arm's known failure. |
| T4 | Baseline non-neutrality | PASS | Fresh run, 3 queries per draft, at or above the 2 phrasings a real session recorded. The default-scope check also clears. Residual: the harness doesn't reformulate after seeing results, and a real session might. |
| T5 | Processing inequality | PASS | Same stage, raw candidate list. Reading load differs and is reported. |
| T6 | Corpus bias | PASS, with a limit | Stratum A leans toward vectors by survivorship, and the registered counterweight is stratum B, where vectors also lead. Limits: searches that found the issue and left no comment are invisible, so absolute recall here understates the incumbent. Stratum B's keyword recall of 0.538 also shows it isn't proven keyword-friendly, since a comment makes a weaker draft than an issue, and at 10 against 7 of 13 it isn't significant alone. This is the weakest point of the audit: trust the direction, not the levels. |
| T7 | Power and stopping | PASS | n and k fixed, one primary, no stopping. |
| T8 | Peeking | PASS | Bar frozen first. The scorer fix came before any output existed. |
| T9 | Judge contamination | PASS | Scoring is a script. Writers had separate contexts, model verified as `claude-sonnet-5-5` from transcripts. Residual: one writer saw all 43 drafts at once, including drafts from one family. |
| T10 | Criterion substitution | PASS, with a limit | Read off the primary criterion. Limit: recall measures whether the issue was surfaced, not whether a reader recognised it. |

```
[experiment-audit] Verdict — duplicate-issue retrieval
Verdict: supported.
Scope: holds for these 43 declared cases in dev-env, career-playbook and lifting-logbook, under
BAAI/bge-small-en-v1.5 (fastembed 0.9.1, onnxruntime 1.31.1), title and body text only, against
keyword queries written by claude-sonnet-5-5 and run through gh issue list --search on 2026-11-06,
compared at the raw candidate list.
It doesn't establish that sessions would recognise the duplicate, how often duplicates occur, or
absolute recall outside these declared cases.
Decision: adopt as an addition to keyword search, built as a follow-up, not a gate. Legal under
Tier 1. The adoption rider isn't attached, since the standing search rule isn't replaced.
```

## Spend, estimated against actual

Dollars are API-equivalent from `claude/scripts/_token_pricing.py` at the 1-hour cache-write rate. The plan's limits are what's actually consumed.

| # | Stage | Estimated | Actual | Stop rule fired? |
|---|-------|-----------|--------|------------------|
| 1 | Query writing | $1.75 | **$1.73** ($0.558, $0.596, $0.577), plus the $0.40 pilot | No |
| 2 | Keyword search | At most 657 calls, about 19 min | 573 calls, 1,202 s | No |
| 3 | Embedding | 2,996 documents, about 12 min, $0 | 2,996 documents, 618 s, $0 | No |
| 4 | Main session | About $5 from the freeze on | $8.45 for all 40 turns up to scoring, including design before the freeze | No rule set |

The main-session figure is a lower bound, because it stops at scoring. No call failed, so the stage figures have no unpriced gap. A 16 GB GPU was available and not needed.

## Privacy scan

`DUP_EVAL_ISSUE_CACHE=<local issue cache> py -3 claude/scripts/tests/test_issue_dup_eval.py` passes against 1,614 titles from the private career-playbook and cover-letter-runtime repos. CI runs the structural and fixture checks only. Sanitized queries were written by a subagent on this machine, and their SHA-256 is `da0096c309b0c0c571bd4e5685cde29c223bcc48472a143907dad4b70396ea2f`. They aren't committed, since they carry words from private issues.

## Reproduce the verdict

```bash
py -3 experiments/2026-11-issue-dup-retrieval/score.py experiments/2026-11-issue-dup-retrieval/labels.json experiments/2026-11-issue-dup-retrieval/vector_results.json experiments/2026-11-issue-dup-retrieval/keyword_results.json C:/Users/brown/.claude/scratch/scored_check.json
```

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

Per generation, keyword recall@10 and gap:
  g1 keyword 0.698 gap 0.209
  g2 keyword 0.651 gap 0.256
  g3 keyword 0.651 gap 0.256

Per repo, vector and keyword recall@10:
  career-playbook n=25 vector 21/25 keyword 17/25
  dev-env n=9 vector 9/9 keyword 6/9
  lifting-logbook n=9 vector 9/9 keyword 7/9

Worst vector rows (miss, or below rank 5):
  A:career-playbook#1114 vector 6 keyword [3, None, None]
  A:career-playbook#1768 vector 20 keyword [1, 1, 1]
  B:career-playbook#1471 vector None keyword [None, None, None]
  B:career-playbook#1681 vector 18 keyword [4, None, None]
  B:career-playbook#724 vector None keyword [None, None, None]
  B:dev-env#1029 vector 6 keyword [None, None, None]

Reading load, results available per query input (median across generations):
  keyword median 1, max 6; vector always shows the full budget
  keyword returned nothing in the majority of generations: 7 inputs

Cosine thresholds (post hoc, descriptive): share of each pair class at or above t
  t=0.80 duplicate 0.97 declared-non-duplicate 0.61 random 0.03
  t=0.85 duplicate 0.94 declared-non-duplicate 0.43 random 0.01
  t=0.90 duplicate 0.77 declared-non-duplicate 0.13 random 0.00

Sensitivity: drop inputs where both arms hit trivially at rank 1 in all generations
  n=27 vector 23/27 keyword 14/27
  edited after the input was filed: A:career-playbook#1768 -> #1767

Bodies edited after the input's filing time: 1
