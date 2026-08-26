# ADR-032 — Top-of-README "Start here" Dashboard in journal-compose

**Date:** 2026-05-28
**Status:** Accepted
**Closes:** [dev-env#286](https://github.com/brownm09/dev-env/issues/286)
**Tags:** journal, composition, skill, readme, manifest, dashboard
**Related:** [ADR-001](001-per-session-stub-files.md), [ADR-002](002-journal-compose-session-isolation.md), [ADR-019](019-doc-reconciliation-enforcement.md)

---

## Context

`engineering-journal/README.md` is the user's daily on-ramp to 11 active project journals. Today every project gets a `### <Project Name>` section with `Recent:` / `Open:` / `Next:` / `Repo:` / `Journal:` bullets (`journal-compose` Step 8), and each `sessions/<project>/README.md` gets a Progress Summary plus a "Where to start next session" paragraph (Step 7).

In practice, opening the root README the next morning still requires scanning all 11 project sections to reconstruct two questions: "what was actively worked yesterday?" and "what are the 3–5 most important things to pick up next?" The data is present — distributed across 11 `Next:` bullets and `sessions/*/open-prs.jsonl` files — but no view aggregates it. The user explicitly asked for a single landing block that surfaces both, refreshed on every compose, plus a freshness stamp so staleness is obvious.

The composition rules ([ADR-001](001-per-session-stub-files.md), [ADR-002](002-journal-compose-session-isolation.md)) already pass per-session metadata through the manifest (`stub`, `topic`, `tokens`, `prs_opened`, `prs_closed`). Cross-project priority is the one signal the user has been tracking in their head — extending the manifest is the natural place to make it explicit and aggregatable.

---

## Decision

`/journal-compose` writes a new marker-delimited block at the top of `engineering-journal/README.md`, above `## Projects`, on every successful compose:

```markdown
<!-- start-here:begin -->
**Last composed:** YYYY-MM-DD

## Start here

Top priorities across all projects (max 5):

1. **[ref](url) — label** *(project)* — why
2. ...
<!-- start-here:end -->
```

The block's content is derived from two sources, aggregated at compose time, deduped by `ref`, capped at 5:

1. **`priorities` arrays from every manifest written for the compose date**, across all `sessions/*/`. This is the explicit "I'm flagging this" channel — highest signal.
2. **`open-prs.jsonl` entries across all projects**, used to fill the list when today's flags total fewer than 5.

The manifest schema gains one optional field:

```json
"priorities": [
  {"label": "Staging integration test gate fix", "ref": "lifting-logbook#346", "why": "blocks next staging deploy"}
]
```

`label` is required (string). `ref` and `why` are optional (string). No other keys are accepted. The validator (`engineering-journal/scripts/validate-jsonl.js`) enforces this in a companion PR.

The empty case (no priorities flagged today and no open PRs) renders an explicit `*No flagged priorities and no open PRs.*` line rather than omitting the block — explicit empty state beats invisible drift.

---

## Rationale

**Why a marker-delimited block instead of a section heading the existing Step 8 rewrites?** Step 8 finds each project's `### <Project Name>` heading by name and rewrites within it. The new block sits above `## Projects` and has no semantic parent to anchor on, so explicit `<!-- start-here:begin -->` / `<!-- start-here:end -->` markers are needed for the overwrite to be idempotent. The markers are inert in rendered Markdown and visible in source — there is no risk of user content accidentally landing inside them.

**Why aggregate at compose time rather than maintaining the list incrementally?** Composes already touch every manifest and `open-prs.jsonl` for the date. Aggregation adds one `node -e` pass over data the skill is already loading. Maintaining the list incrementally (e.g., a stop-hook that recomputes after every session) would couple a daily artifact to per-session events and add a cross-project read on every session close.

**Why an explicit `priorities` field instead of inferring priority from per-project `Next:` bullets?** Inference is unreliable — `Next:` bullets are written by the skill after composition and reflect the *end* of a session, not what the user views as cross-project priority. The explicit field lets the session author flag what they care about during stub authoring, when intent is freshest. The field is optional; sessions that don't set it simply contribute nothing to the top-5 list.

**Why dedupe by `ref`?** A priority flagged in today's manifest and the same PR sitting in `open-prs.jsonl` is one item, not two. `ref` is the natural key — `owner/repo#N` for GitHub references, freeform otherwise. Entries without `ref` are never considered duplicates of anything.

**Why cap at 5?** The user explicitly asked for "3–5 high-priority issues." Beyond 5 the dashboard becomes a second catalog, defeating the purpose. If more than 5 items are flagged, the surplus is silently dropped — the manifest is the audit trail, not the dashboard.

---

## Consequences

**Forced:** every compose rewrites the marker-delimited block. The freshness stamp surfaces stale-ness without a separate check. Sessions that flag priorities see them propagate to the top of the README on the next compose. The validator rejects malformed `priorities` entries at the manifest level, before they reach compose.

**Cost:** one new optional manifest field that future schema migrations have to account for. The skill gains one new step (Step 8a) with a new aggregation rule. The user has to remember to flag priorities in stubs — there is no enforcement that priorities are flagged, only that flagged priorities are well-formed.

**Detection:** if the `## Start here` block disappears or stops updating, the symptoms are (a) `**Last composed:**` showing a stale date, (b) the marker pair missing from the rendered README, (c) priorities flagged in recent manifests not appearing in the block. (b) means the skill failed during Step 8a; (a) and (c) without (b) mean composes have stopped running.

---

## Alternatives considered

**A — Single hand-curated `START_HERE.md` at repo root.** Rejected: requires the user to maintain it between composes; defeats the goal of automated daily refresh.

**B — Aggregate from per-project `Next:` bullets only.** Rejected: see Rationale — `Next:` is end-of-session output, not user intent. Inference produces a noisy list.

**C — Persist priorities across days until explicitly resolved.** Rejected: today's manifest is the highest-signal channel. Persisting yesterday's priorities risks the list becoming a stale to-do log. `open-prs.jsonl` already carries the cross-day signal for in-flight work; that's enough.

**D (chosen) — Marker-delimited block at top of root README, fed by explicit manifest `priorities` + `open-prs.jsonl` fallback, rewritten every compose.** Preserves user control over what's flagged, automates the aggregation, makes freshness visible.

---

## Amendment 1 — Source 3's project→repo mapping is the root README, and every skip is named (2026-08-25)

**Closes:** [dev-env#1045](https://github.com/brownm09/dev-env/issues/1045)

The Decision above describes **two** sources. A third — open issues labeled `start-here` across
project repos, filling the list when the first two total fewer than 5 — was added later by
[dev-env#292](https://github.com/brownm09/dev-env/issues/292). That addition needed a
project→repo-slug mapping, which this ADR had never specified, and the one it invented was wrong.

**The defect.** Source 3 resolved each `sessions/<project>/` directory's slug by regex against that
project's own `README.md`:

```js
const m = txt.match(/Repo:\s*\[([^\]]+)\]\(https:\/\/github\.com\/([^\/]+\/[^\/\)]+)\)/);
if (!m) continue;
```

Verified live 2026-08-25, that pattern matched **zero of the 11 project READMEs**, so Source 3 had
never contributed an entry for any repo since the day it was added. Two independent reasons, either
fatal alone:

1. **The file it read is the wrong file.** Ten of the eleven project READMEs carry no repo-link line
   at all — so no per-project pattern, however wide, can resolve them. The `**Repo:**` bullets the
   regex was written for live in the **root** `README.md`, which Step 8 writes.
2. **The one file that does carry a line spells it differently.** `sessions/gas-lifting-logbook/README.md`
   uses `**Repository:**`, and `Repo:` is not a substring of `Repository:` (after `Repo` comes `s`,
   not `:`). The pattern also did not tolerate the `**` bold markers that precede the colon.

**Why it went undetected for months** is the part worth recording: the loop's failure branch was a
bare `continue`. A project skipped because the mapping was broken produced output *identical* to a
project with no labeled issues — and since the dashboard's empty state is a legitimate, expected
condition ("no flagged priorities and no open PRs"), there was no observable difference between
working and completely inert. The Detection section above anticipated the block *disappearing*; it
did not anticipate one of its sources being silently absent from a block that still rendered.

**The decision.** The **root `README.md` is the canonical project→slug mapping**, because Step 8
regenerates it on every compose immediately before Step 8a runs. Each `### ` section's `**Repo:**`
bullet is paired with its `**Journal:** [sessions/<project>/` bullet — the *Journal* bullet, never
the heading, since section titles deliberately do not match directory names (`### Job Search` →
`sessions/job-search/`). The project's own README is a documented fallback, accepting `Repo:` **or**
`Repository:` with or without bold markers, so a project given a repo line locally but not yet a
root-README section still resolves.

Resolution moves out of the skill's inline `node -e` block and into
`claude/scripts/journal-project-repo-map.py`, mirroring how Step 8b's logic lives in
`validate-composed-output.py` ([ADR-121](121-composed-output-stray-terminal-scan.md)). Two reasons:
the inline form was untestable, and the resolved slug is interpolated straight into a `gh issue list
--repo <slug>` command line, so shape-validating it belongs in a unit that has tests
(`## Testing` item 97).

**Every unresolved project must be named.** This is the half that prevents a recurrence, and it is a
new requirement this ADR did not previously impose: the script emits `SOURCE3_RESOLVED=`/
`SOURCE3_SKIPPED=` counts, one `SOURCE3_SKIP <project> -- <reason>` line per unresolved project, and
a distinct `SOURCE3_MAPPING_EMPTY` signal (exit 1) when projects exist and none resolve. The skill
reports all of it to the user, including a zero skip count — a visible zero is what makes a later
non-zero meaningful. A wholesale mapping failure can no longer look like a quiet day.

**Failure direction.** Skips alone exit 0: some projects legitimately have no repo
(`engineering-journal` itself), and a partial resolution must never fail a compose.
`SOURCE3_MAPPING_EMPTY` exits 1, and Step 8a reports it and **continues** — matching the
`START_HERE_INSERT_FAILED` convention already in that step. One dashboard sub-source must never
abort a compose.

**Live result at the time of the fix:** 10 of 12 project directories resolve, versus 0 before. The
two that do not (`engineering-journal`, `research-notes`) have no root-README section, and both are
now reported by name with a reason rather than silently dropped.

---

## References

- [ADR-001 — Per-Session Stub Files for Journal Composition](001-per-session-stub-files.md)
- [ADR-002 — Journal-Compose Session Isolation](002-journal-compose-session-isolation.md)
- [ADR-019 — Documentation Reconciliation Enforcement](019-doc-reconciliation-enforcement.md)
