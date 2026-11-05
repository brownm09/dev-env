# ADR-154: One Shared Price Table, Unknown Models Unpriced, and Spend Attribution Read From Transcripts

**Date:** 2026-11-05
**Status:** Accepted
**Closes:** [dev-env#1214](https://github.com/brownm09/dev-env/issues/1214), [dev-env#1122](https://github.com/brownm09/dev-env/issues/1122)
**Tags:** token-efficiency, cost, pricing, token-tracker, token-report, transcripts, stop-hook, measurement
**Related:** [ADR-115](115-experimental-rigor-protocol.md), [ADR-144](144-gate-calibration-pass-3-dimension.md)

---

## Context

Reducing token spend ([dev-env#1213](https://github.com/brownm09/dev-env/issues/1213)) needs a measurement that can be repeated before and after each change. Three scripts each held their own `PRICING` copy, and all three priced any unknown model at Sonnet 4.6 rates, so Opus 5.5 was costed wrongly with no warning. The first measurement for #1213 also assumed cache multipliers that are wrong for the models in most use.

## Decision

1. **One price table.** `claude/scripts/_token_pricing.py` holds the prices, copied from the [published pricing page](https://platform.claude.com/docs/en/about-claude/pricing.md), and `token-tracker.py`, `token-report.py` and `backfill-tokens.py` import it.
2. **An unknown model is unpriced, never estimated.** `get_pricing` returns `None` unless the whole id is a table key with an optional date or bracket suffix. Session tables print `unpriced` and say what their totals leave out. Attribution lists such calls separately, along with fast-mode and US-only calls, which the table doesn't price.
3. **Attribution reads transcripts, not the session log.** `token-report.py --attribution` prices each API call from its own model and cache tier. A message id is one call across all files, and its last record with tokens wins, because a response repeats its usage across records and the last has the final output count.
4. **Compaction comes from the transcript's `compact_boundary` record**, not from a drop in context size.
5. **Aggregates only.** The report keeps no transcript text, and it prints a model id only when it looks like one, because the field comes from a transcript and the output gets pasted into issues.

## Alternatives considered

| # | Alternative | Why rejected |
|---|---|---|
| 1 | Keep a default rate for unknown models | This is the bug in #1122. A wrong number with no marker is worse than a visible gap, because it reads as a real cost. |
| 2 | Match model ids by substring, longest key first | A successor such as `claude-opus-5-6` would silently take `claude-opus-5`'s row, which is the same bug for the next release. |
| 3 | Keep a price table per script | Each copy needed the same edit for every new model, and none of them had Opus 5.5. |
| 4 | Fetch prices from the page at run time | `token-tracker.py` is a Stop hook and should make no network call. A hand-edited table is checked by tests that pin the published ratios between its columns. |
| 5 | Infer compaction from a sharp context drop | Only 107 of 155 compactions showed a drop past the 0.6 ratio tried first, while the transcript records every one directly. |
| 6 | Portal or the shunt plugin as the measurement or the fix | Full reads of large files are under 1% of spend, so they don't answer where spend goes. The spend decision itself is recorded with [dev-env#1215](https://github.com/brownm09/dev-env/issues/1215). |

## Consequences

- Rewriting stored `estimated_cost_usd` values is not part of this. Older log rows stay as recorded, because they're a record of what was reported at the time.
- `token-tracker.py` and `backfill-tokens.py` still sum every transcript record, so new rows in the session log overstate usage until [dev-env#1223](https://github.com/brownm09/dev-env/issues/1223) lands. Attribution doesn't read that log.
- Session totals can't see per-call detail, so the tracker can't apply the Haiku 5.5 long-prompt tier or detect fast mode. Attribution does both.
- The report's bands and ratios only sort spend into rows, and no check acts on them, so no gate is introduced ([ADR-144](144-gate-calibration-pass-3-dimension.md)). Any savings claim built on the report still needs `/experiment-audit` ([ADR-115](115-experimental-rigor-protocol.md)).
- Adding a model means adding a row by hand. Until then it shows as unpriced, which is the intended signal.
