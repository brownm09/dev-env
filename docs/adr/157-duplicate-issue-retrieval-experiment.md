# ADR-157: The First Duplicate-Issue Retrieval Comparison Is Inconclusive, So Re-Run It Against a Baseline That Can Retry

**Date:** 2026-11-06
**Status:** Accepted
**Amended:** 2026-11-07 (the re-run is also inconclusive, see below)
**Amended:** 2026-11-07 (#1275 is shelved, untested, see below)
**Closes:** [dev-env#1260](https://github.com/brownm09/dev-env/issues/1260)
**Tags:** retrieval, embeddings, vector-search, duplicate-issues, experiment, tier-1, privacy, local-model, cost-model, adr-115
**Related:** [ADR-115](115-experimental-rigor-protocol.md), [ADR-048](048-memory-immortalization-issue-pairing.md), [ADR-154](154-token-attribution-and-shared-pricing.md)

---

## Context

The global CLAUDE.md requires `gh issue list --search` before filing an issue, and duplicates still land. Nobody had measured how often that search finds the existing issue, or whether embedding search would find more. This is the first slice of the retrieval track in [dev-env#1259](https://github.com/brownm09/dev-env/issues/1259), chosen first because declared duplicates give real ground truth and the search rule gives a real baseline.

Three constraints shaped the choice of tools:

- career-playbook and cover-letter-runtime are private, and their text shouldn't go to a new third-party service without the owner's approval.
- This repo is public, so anything committed from those repos is limited to issue numbers, metrics and filing timestamps. The owner accepted the timestamps.
- The corpus is small: 2,996 issues across the 4 repos embedded.

## Decision

1. **Don't adopt or reject embedding search yet: re-run it** ([#1282](https://github.com/brownm09/dev-env/issues/1282)). A Tier 1 comparison, pre-registered [before any result](https://github.com/brownm09/dev-env/issues/1260#issuecomment-6689587273), found recall@10 of 0.907 against 0.698 on 43 inputs, and the frozen outcome map reads that as supported. But the keyword arm wrote its queries once and never saw a result. 193 of its 387 queries returned nothing, and recovering just 3 of the 7 inputs that came back empty takes the gain below the win bar. That's a load-bearing threat, so the audit's verdict is **inconclusive, confounded by T4**. Full audit: [`results.md`](../../experiments/2026-11-issue-dup-retrieval/results.md).
2. **Keep embedding local for this track:** `BAAI/bge-small-en-v1.5` through [FastEmbed](https://qdrant.github.io/fastembed/). It has 384 dimensions, a 512-token limit and an MIT license per its [model card](https://huggingface.co/BAAI/bge-small-en-v1.5). No text goes to an embedding provider. The whole corpus embedded on CPU in 618 s for $0. Embedding is the only step that stayed local in this experiment: query-writing drafts went through the Anthropic API, as in any Claude Code session, and queries went to GitHub.
3. **Store vectors in a flat file per repo, searched by brute force.** Embedding and ranking all 43 inputs took 9.78 s in total, most of it embedding, so an index would add a dependency and solve nothing yet.
4. **Show ranked candidates, and don't auto-decide, if this is ever built.** At cosine 0.85, 94% of duplicate pairs pass and so do 43% of pairs an author declared distinct, while random pairs fall away (1%). Those figures are post hoc and compare raw documents rather than filing-time drafts, so treat them as a warning and not a calibration. Any gate would owe its own calibration under [ADR-144](144-gate-calibration-pass-3-dimension.md).
5. **Hold the helper.** [#1275](https://github.com/brownm09/dev-env/issues/1275) waits on the re-run.

## Alternatives Considered

**Keep "supported" and state the limit.** One reviewer suggested this, on the grounds that the frozen map still reads supported and 7 of 7 recoveries is an upper bound. Rejected by the owner: 3 of 7 is enough to clear the win bar away, the audit's rule is that a load-bearing flag forces inconclusive, and the question costs little to measure.

**Shelve instead of re-running.** Legal, and cheaper. Rejected because the 39 against 30 result is a real signal, and the track's next targets depend on knowing whether it holds.

**Voyage AI embeddings.** [Anthropic's embeddings guide](https://platform.claude.com/docs/en/build-with-claude/embeddings) states "Anthropic does not offer its own embedding model" and points to Voyage AI. Rejected for this slice because it adds a third party receiving private issue text, which would need the owner's approval, and because no key is configured. It may be stronger: this result doesn't test it.

**`voyage-4-nano`, the open-weight model in that same guide, run locally.** Not chosen because it adds a heavier model for a first slice with no evidence the small one falls short. A fair candidate if misses turn out to be about model quality.

**`sqlite-vec`.** A real option for "is SQLite enough", and its [README](https://github.com/asg017/sqlite-vec) describes a small dependency-free extension. Rejected for now because that README also says "pre-v1, so expect breaking changes", and nothing here needs SQL. Plain SQLite remains a reasonable home if incremental updates are ever needed.

**pgvector or a vector database such as Qdrant.** Rejected because a server is unjustified for a few thousand vectors on one machine. pgvector stays the candidate for lifting-logbook, target 3 in #1259, where Postgres already exists.

## Consequences

**Limits beyond the verdict.** Recall measures whether the issue was surfaced, not whether a reader recognised it. Searches that found an issue and left no comment are invisible, so these recall levels understate keyword search, and only the direction should be trusted.

**Reading load would change.** Keyword search returned a median of 1 result per input, and embedding search always has a full list. The first-candidate recall (0.744 against 0.605) suggests a very short list would do.

**The cost model held where it was measured.** Following the shape proposed in [dev-env#1224](https://github.com/brownm09/dev-env/issues/1224), a pilot and a stage-boundary check put query writing at $1.73 against a $1.75 estimate. A single case doesn't validate the method. The main session was the largest cost and the one the pilot didn't measure: $8.45 through scoring, against an estimate of about $5 from the freeze on.

**The first audit passed a threat it should have flagged.** The author marked T4 as a residual note, and the same audit said nothing undisclosed was found. Independent reviewers given the code and not the conclusion found both. A review from outside the session that ran the experiment is worth keeping as a step before a verdict is treated as final.

**Tripwire and rollback.** Nothing is wired in, so nothing to roll back. `score.py` only reads stored output, so re-running it can't catch a change in behaviour. The tripwire for any future helper is to produce fresh rankings in `vector_results.json`'s format through that helper, and score those.

## Amendment (2026-11-07): the re-run is also inconclusive

[#1282](https://github.com/brownm09/dev-env/issues/1282) re-ran the comparison, [pre-registered](https://github.com/brownm09/dev-env/issues/1282#issuecomment-6702622020) before any result, against a keyword arm that replaced every query that returned nothing. Full audit: [`rerun/results.md`](../../experiments/2026-11-issue-dup-retrieval/rerun/results.md).

**Result.** Recall@10 of 0.907 against 0.791 on the same 43 inputs, 39 against 34, a gap of +0.116. That's below the 0.15 win bar and above the 0.05 no-gain line, and the family sign test gives p = 0.109. The retry recovered 4 inputs and lost none, and 3 of the 4 turn on one generation. The verdict is **inconclusive, confounded by T1 and T4**.

**Decision.**

1. **Neither adopt nor reject embedding search.** This replaces decision 1 above: the re-run it called for has happened and didn't settle the question.
2. **[#1275](https://github.com/brownm09/dev-env/issues/1275) stays blocked.** This extends decision 5. The owner chooses between another run and shelving, and nothing here treats embedding search as adopted.
3. **Decisions 2 to 4 stand as conditions on any future helper:** local embedding, flat-file storage, and candidates shown rather than decided. The re-run tested none of them further.

**Why not supported or refuted.** Baseline choice alone moves the reading across the bar: +0.209 with no retry, +0.186 at the recorded 25% retry rate, and +0.116 at full retry. Writers could also see other drafts in their batch, and for 5 inputs that includes the target, which favours keyword search: losing 2 of those 4 hits would clear the bar. An approximate 95% interval on the gap, computed after the run, runs from 0.00 to 0.23.

**Why not simply run again on these inputs.** The vector arm is deterministic, so its 39 isn't a new sample, and another keyword arm on the same 43 can't resolve an interval that wide. Trying arms until one reads as wanted would be generate-until-win. A run that could settle it needs new inputs, and only 1 new declared duplicate appeared in the 62 new issues and 68 new comments since the first fetch.

**Rejected: read this as refuted because the first gap shrank.** The result is two inputs short of the bar, against an arm more generous than recorded retry behaviour. Also rejected: treating the +0.186 at the recorded retry rate as supporting adoption. It's a secondary figure, conditional on filing, and can't decide the verdict.

**A finding worth keeping.** GitHub's search can miss identifiers and pieces of file names that an issue's text holds: `test_pyw_stdio` misses dev-env#734, whose title holds `test_pyw_stdio.py`, while `test pyw stdio` finds it. That's one public case, and the mechanism isn't established. 30 of 37 query and target pairs that looked like harness misses hold a term that can't find its target alone, and the affected queries cover 4 of the 6 inputs only the vector arm hit. Those searches gave identical lists when re-run, so it's search behaviour and not a harness defect. The other 7 pairs are unexplained. Whether telling sessions to split identifiers into words closes part of the gap is untested. This is an observation, not documented search behaviour; the [search documentation](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests) doesn't describe tokenizing at this level.

**Review caught the audit again.** Two reviewers in fresh contexts reproduced the figures and the reading. They also found an undisclosed deviation, a T7 pass that contradicted its evidence, an understated T4 and the batch contamination behind T1. The first run's lesson held: a review from outside the session belongs before a verdict is final.

**Cost.** About $27.50 at list price against a $30 budget, on the subscription, so this estimates plan usage and isn't a charge. Query writing ran above estimate ($4.03 against at most $3.50), and the session stayed within its $10 to $15 estimate.

## Amendment (2026-11-07): #1275 is shelved, untested

The owner chose shelving over another run, which settles the choice the amendment above left open. [#1275](https://github.com/brownm09/dev-env/issues/1275) is [closed as not planned](https://github.com/brownm09/dev-env/issues/1275#issuecomment-6710135871).

**This doesn't reject embedding search.** The verdict stays inconclusive, and the [`/experiment-audit` skill](../../claude/skills/experiment-audit/SKILL.md) records abandoning a question for cost as shelved and untested, not as refuted.

**Why shelve now.** Settling this needs new inputs, and only 1 new duplicate has been declared since the first fetch. The two runs together used about $50 at list price, which estimates plan usage and isn't a charge.

**Criterion 2 of #1282 is accepted as not met.** For 5 inputs, the target's text was another draft in the query writer's batch, and only a new run could change that.

**What reopening would need.** Enough newly declared duplicates to serve as new inputs, and writers' batches that keep each target's text away from its writer. Decisions 2 to 4 still stand as conditions on any future helper. The other retrieval targets in [#1259](https://github.com/brownm09/dev-env/issues/1259) are separate questions, and this doesn't close them.

## References

- [BAAI/bge-small-en-v1.5 model card](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [FastEmbed documentation](https://qdrant.github.io/fastembed/)
- [Anthropic: Embeddings](https://platform.claude.com/docs/en/build-with-claude/embeddings)
- [GitHub: Searching issues and pull requests](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [sqlite-vec](https://github.com/asg017/sqlite-vec)
