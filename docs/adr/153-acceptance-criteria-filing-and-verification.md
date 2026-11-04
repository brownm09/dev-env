# ADR-153: Acceptance Criteria Are Filed as Verifiable Checklists and Verified Per Criterion in `/review`

**Date:** 2026-11-03
**Status:** Accepted
**Closes:** [dev-env#1195](https://github.com/brownm09/dev-env/issues/1195)
**Tags:** workflow, acceptance-criteria, definition-of-done, issues, review-skill, evidence-table, shared-schema, switchyard, gates, calibration, known-bad-reference, vacuous-gate, claude-md, global-rule, claude-facing, adr-011, adr-028, adr-039, adr-144, adr-145, adr-149
**Related:** [ADR-028](028-all-findings-merge-gate.md), [ADR-039](039-merge-gate-findings-enforcement.md), [ADR-144](144-gate-calibration-pass-3-dimension.md), [ADR-149](149-scope-of-a-change-rules.md)

---

## Context

Acceptance criteria are written inconsistently and almost never verified. Measured on 2026-11-03:

| # | Repo | Last 100 issues mentioning acceptance criteria | Last 100 closed issues: checklists closed with boxes unticked |
|---|---|---|---|
| 1 | dev-env | 25 | 7 of 7 |
| 2 | career-playbook | 53 | 7 of 8 |
| 3 | lifting-logbook | 59 | 58 of 60 |
| 4 | cover-letter-runtime | 1 | — |

`Closes #N` closes the issue whether or not any criterion was met. `/review` read the linked
issue only for the scope-of-change check (ADR-149). Only `/propose` templated a criteria
section (`claude/templates/proposal.md`). An unticked box does not prove a criterion was unmet.
It proves the verification was never recorded, so nobody can tell from the issue whether "done"
was checked against what was asked.

Switchyard's Definition-of-Done gate ([switchyard#7](https://github.com/brownm09/switchyard/issues/7))
needs the same per-criterion evidence, parsed deterministically from a PR body.

## Decision

1. **Filing rule** (global `claude/CLAUDE.md` → Git Workflow). Every implementation issue carries
   a `## Acceptance criteria` checklist. Each item names how it is checked. Investigation and
   tracking issues are exempt.
2. **`/review` Step 2g.** For each linked issue, extract the criteria, then emit a table with one
   row per criterion: verbatim criterion, evidence, and `met` / `not met` / `not verifiable`.
   Each `not met` row is a blocking `[correctness]` finding, so the existing findings marker and
   `pre-merge-findings-gate.py` (ADR-039) enforce it with no new parser. A missing section, an
   empty section, or no linked issue is reported as its own outcome, never as a pass (the ADR-144
   non-empty-extraction requirement).
3. **One schema file**, `claude/templates/acceptance-evidence.md`, holds the extraction rules, the
   table columns, the status values, a machine-readable marker, and the post-merge recording step.
   `/review` and Switchyard both read it, and neither restates it.
4. **After merge**, verification is recorded on the issue: tick the `met` boxes, or post the
   evidence table as a comment linking the PR.

## Alternatives considered

| # | Alternative | Why rejected |
|---|---|---|
| 1 | Keep criteria only in `/propose` and leave `/review` as is | This is the status quo that produced the table above. Most issues aren't filed through `/propose`. |
| 2 | A deterministic hook that blocks `gh pr merge` while the linked issue has unticked boxes | It measures ticks, not delivery. It would teach tick-without-evidence, and it can't tell a deferred criterion from a missed one. Switchyard takes the deterministic route, but against an evidence table, not ticks. |
| 3 | Put the evidence table only in the PR body, written by the author | The author is the party being checked. The review is the independent read. The schema still lets an author's PR body carry the same table, which is what Switchyard parses. |
| 4 | Make `not met` non-blocking (advisory only) | ADR-028 already makes every finding fix-or-file. A missed criterion is a correctness defect against the stated goal, the same class as a missing test (ADR-022). It is blocking only because calibration below supports it. |
| 5 | Define the schema separately in each consumer | Two copies drift. Switchyard#7 states one-schema as its own acceptance criterion. |

## Calibration

Per Pass 3 Gate calibration (ADR-144), the blocking verdict is a check that classifies inputs
this PR doesn't enumerate, so it was calibrated before being allowed to block.

- **Measured property:** for each extracted criterion, whether the PR's diff, body, or head-ref
  files contain concrete evidence that it was delivered. The check is not judging whether the
  PR "looks complete".
- **Labeling was independent of scoring.** One agent assembled the corpus and labelled each PR
  from evidence outside the check: issue reopen events, follow-up issues or PRs naming the missed
  item, or a PR body admitting an item wasn't done. A second agent, given only the PR and issue
  URLs in shuffled order plus the Step 2g text and the schema, scored them blind.

CALIBRATION_RESULTS_PLACEHOLDER

## Consequences

- Every `/review` comment carries an evidence table, or a plain statement that none was possible,
  plus the `acceptance-criteria` marker.
- Issues filed without criteria now draw a non-blocking finding at review time. This puts
  pressure on filing without blocking a PR on an issue-hygiene gap.
- The judgment is an LLM's, so a false `not met` is possible. The calibration bounds it, and the
  recovery path is stated in Step 2g: record the false block here and demote `not met` to
  non-blocking until recalibrated.
- The issue's remaining criterion, "one completed PR in each of dev-env, career-playbook, and
  lifting-logbook shows the table, and its linked issue shows ticked boxes or an evidence
  comment", is post-merge by construction. It is tracked as a follow-up rather than claimed here.
