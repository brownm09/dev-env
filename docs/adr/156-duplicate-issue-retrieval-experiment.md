# ADR-156: Embedding Search Finds More Duplicate Issues Than Keyword Search, So Add It Beside Keyword Search

**Date:** 2026-11-06
**Status:** Accepted
**Closes:** [dev-env#1260](https://github.com/brownm09/dev-env/issues/1260)
**Tags:** retrieval, embeddings, vector-search, duplicate-issues, experiment, tier-1, privacy, local-model, cost-model, adr-115
**Related:** [ADR-115](115-experimental-rigor-protocol.md), [ADR-048](048-memory-immortalization-issue-pairing.md), [ADR-154](154-token-attribution-and-shared-pricing.md)

---

## Context

The global CLAUDE.md requires `gh issue list --search` before filing an issue, and duplicates still land. Nobody had measured how often that search finds the existing issue, or whether embedding search would find more. This is the first slice of the retrieval track in [dev-env#1259](https://github.com/brownm09/dev-env/issues/1259), chosen first because declared duplicates give real ground truth and the search rule gives a real baseline.

Three constraints shaped the choice of tools:

- career-playbook and cover-letter-runtime are private, and their text shouldn't go to a third-party service without the owner's approval.
- This repo is public, so anything committed from those repos has to be numbers only.
- The corpus is small: 2,996 issues across the 4 repos embedded.

## Decision

1. **Add embedding search beside keyword search, and don't replace it.** A Tier 1 comparison, pre-registered [before any result](https://github.com/brownm09/dev-env/issues/1260#issuecomment-6689587273), returned **supported**: recall@10 of 0.907 against 0.698 on 43 inputs, sign test p = 0.011 across 38 families. Both strata and all three repos lean the same way. Full audit: [`results.md`](../../experiments/2026-11-issue-dup-retrieval/results.md). Scope: these declared cases, this model, title and body text. Keyword search is still the only arm that won `career-playbook#1768`, and it costs a session almost nothing to read.
2. **Use a local model:** `BAAI/bge-small-en-v1.5` through [FastEmbed](https://qdrant.github.io/fastembed/). It has 384 dimensions, a 512-token limit and an MIT license per its [model card](https://huggingface.co/BAAI/bge-small-en-v1.5). No issue text leaves the machine. The whole corpus embedded on CPU in 618 s for $0.
3. **Store vectors in a flat file per repo, searched by brute force.** Embedding and ranking all 43 inputs took 9.78 s in total, most of it embedding, so an index would add a dependency and solve nothing yet.
4. **Show ranked candidates, and never auto-decide.** At cosine 0.85, 94% of duplicates pass and so do 43% of pairs an author declared distinct. A threshold can screen out random pairs (1% pass) but can't call something a duplicate, so this must not become a blocking gate.
5. **Build the session-facing helper as a follow-up.** This PR holds only the experiment. Any wiring into a skill or rule has to be calibrated in its own right under [ADR-144](144-gate-calibration-pass-3-dimension.md).

## Alternatives Considered

**Voyage AI embeddings.** [Anthropic's embeddings guide](https://platform.claude.com/docs/en/build-with-claude/embeddings) states "Anthropic does not offer its own embedding model" and points to Voyage AI. Rejected for this slice because it sends private issue text to a third party, which would need the owner's approval, and because no key is configured. It may be stronger: this result doesn't test it.

**`voyage-4-nano`, the open-weight model in that same guide, run locally.** Not chosen because it adds a heavier model for a first slice with no evidence the small one falls short. A fair candidate if the helper's misses turn out to be about model quality.

**`sqlite-vec`.** A real option for "is SQLite enough", and its [README](https://github.com/asg017/sqlite-vec) describes a small dependency-free extension. Rejected for now because that README also says "pre-v1, so expect breaking changes", and nothing here needs SQL. Plain SQLite remains a reasonable home if the helper needs incremental updates.

**pgvector or a vector database such as Qdrant.** Rejected because a server is unjustified for a few thousand vectors on one machine. pgvector stays the candidate for lifting-logbook, target 3 in #1259, where Postgres already exists.

**Improving keyword search instead.** Not tested. 7 of keyword search's 13 misses returned nothing in most generations, which suggests over-specific queries, though this run didn't verify the cause. Better query guidance could close part of the gap. It's a fair experiment for later, and nothing here rules it out.

**Report the probe as Tier 0.** Rejected because 30 declared pairs were enough for a real test, and a Tier 0 probe can't support any adopt or reject.

## Consequences

**Limits the verdict carries.** Recall measures whether the issue was surfaced, not whether a reader recognised it. Searches that found an issue and left no comment are invisible, so these recall levels understate keyword search, and only the direction should be trusted. The harness writes queries once and doesn't reformulate.

**Reading load changes.** Keyword search returned a median of 1 result per input, and embedding search always has a full list. A session has to read more to get the extra recall. The first-candidate recall (0.744 against 0.605) suggests a very short list would do.

**The cost model held.** Following the shape proposed in [dev-env#1224](https://github.com/brownm09/dev-env/issues/1224), a pilot and a stage-boundary check put query writing at $1.73 against a $1.75 estimate. A single case doesn't validate the method, but it's the opposite of the fourfold miss that prompted that issue. The main session was the largest cost and the one the pilot didn't measure: $8.45 through scoring, against an estimate of about $5 from the freeze on.

**Rollback.** Nothing wired in, so nothing to roll back here. If the helper lands, deleting it restores today's behaviour, and re-running `score.py` on `labels.json` is the tripwire.

## References

- [BAAI/bge-small-en-v1.5 model card](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [FastEmbed documentation](https://qdrant.github.io/fastembed/)
- [Anthropic: Embeddings](https://platform.claude.com/docs/en/build-with-claude/embeddings)
- [GitHub: Searching issues and pull requests](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests)
- [sqlite-vec](https://github.com/asg017/sqlite-vec)
