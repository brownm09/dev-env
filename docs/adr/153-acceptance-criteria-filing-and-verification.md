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
needs the same per-criterion evidence, in a form a parser can read.

## Decision

1. **Filing rule** (global `claude/CLAUDE.md` → Git Workflow). Every implementation issue carries
   a `## Acceptance criteria` checklist. Each item names how it is checked. Investigation and
   tracking issues declare `Exempt: <reason>` instead.
2. **`/review` Step 2g.** For each linked issue (GitHub's `closingIssuesReferences`), the step
   extracts the criteria, then emits a block: a table with one row per criterion (verbatim
   criterion, evidence, `met` / `not met` / `not verifiable`) and a per-issue marker.
   - **What `not met` triggers.** Each `not met` row is a `[correctness]` finding, classified
     Blocking while Step 2g's severity line says so.
   - **What enforces it.** `pre-merge-findings-gate.py` (ADR-039) blocks a merge until every
     finding has a recorded disposition. It sums blocking and non-blocking findings and does not
     tell them apart. What keeps a `not met` from being waved through with "filed #N" is the
     global fix-everything rule, which Step 2g's finding text restates: the criterion is fixed,
     or the issue is amended to link a follow-up, which turns the row into `not verifiable`.
     Blocking versus non-blocking is a review-level classification. It decides what the summary
     says must change before merge.
   - **Nothing passes vacuously.** A missing section, an empty section, or no linked issue is
     reported as its own outcome, never as a pass (the ADR-144 non-empty-extraction
     requirement). A failed read stops the review rather than becoming an outcome.
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
| 4 | Classify `not met` as non-blocking | A missed criterion is a correctness defect against the issue's stated goal, the same class as a missing test (ADR-022). Calling it non-blocking would tell the author it can wait. The classification is Blocking only because calibration supports it, and Step 2g's severity line can demote it without other edits. |
| 5 | Teach `pre-merge-findings-gate.py` to refuse a disposition while `not_met > 0` | Not rejected outright, only deferred. The gate would need a definition of an amended issue, and calibration would need to cover it. The fix-everything rule covers the case in prose today. Revisit if `not met` findings start being disposed as "filed". |
| 6 | Define the schema separately in each consumer | Two copies drift, and Switchyard needs the identical format. |

## Calibration

Per Pass 3 Gate calibration (ADR-144), the blocking verdict is a check that classifies inputs
this PR doesn't enumerate, so it was calibrated before being allowed to block.

- **Measured property:** for each extracted criterion, whether the PR's diff, body, or head-ref
  files contain concrete evidence that it was delivered. The check is not judging whether the
  PR "looks complete".
- **Labeller and scorer were separate agents.** One agent assembled the corpus and labelled each
  PR. A second agent, given only the PR and issue URLs in shuffled order plus the Step 2g text and
  the schema, scored them blind. A fresh scorer was used for round 2.
- **Where the known-bad labels came from,** case by case. All are real; none is synthetic.
  - **Nine** rest on the PR body admitting the gap: the item left unchecked, struck through, or
    described as deferred, re-scoped, "remaining open", or "done post-merge". These are PRs 115,
    157, 212, 213, 354, 367, 510, 686 and 709.
  - **One** rests on a partial-closure keyword in the body, `Closes #283 Part 1` (career-playbook
    PR 284).
  - **One** rests on a closing comment marking two criteria "Replaced" (career-playbook PR 2054).
  - No case came from a reopened issue. The labeller found reopened issues, but none was a missed
    criterion, and those were excluded.
- **What this means for the hit rate.** Rule 2 keys on the same signal as most of these labels,
  a PR-body admission with no link. So the 7 of 7 on the round-1 corpus is partly circular:
  rules revised against those cases, scored on the same kind of evidence the labels used.
  - **The meaningful figures** are the held-out 3 of 4, and the false-block rate on known-good
    PRs, which nothing circular inflates.
  - **Gaps found only from the diff** (a criterion silently not built) are shown by two cases:
    lifting-logbook PR 115, whose diff recomputes weights the criterion said must come from
    `calculateLiftWeights`, and career-playbook PR 2054, a design replaced without amending the
    issue. That is enough to show the step looks past the PR body, not enough to measure how
    reliably it does.

### Round 1 (14 PRs, first draft of the rules)

| # | Set | Blocked | Hit rate |
|---|---|---|---|
| 1 | Known-good (7) | 0 | 0 false blocks of 7 |
| 2 | Known-bad (7) | 4 | 4 of 7 (57%) |

All three misses had one cause. The PR body admitted the criterion wasn't done, but the scorer
put the row in `not verifiable`, which doesn't block:

- lifting-logbook PR 510: "remain open pending the operator"
- dev-env PR 354: "done post-merge"
- lifting-logbook PR 157: the app-reachable item left unchecked

One block, lifting-logbook PR 115, landed on a different criterion than the labelled one. Its
criterion 4 asks for weights from `calculateLiftWeights`, and the diff recomputes them in the
web app. That block is correct: the labelled criteria were deferred to a linked issue, so the
schema rates them `not verifiable`.

**Revision.** Schema §2 gained the deferral and admission rules: no link means `not met`;
`not verifiable` never means "not done"; a skipped pre-merge check is `not met`; a replaced
design is `not met` unless the issue was amended; plus rules for compound, conditional, and
locator criteria, accepted evidence, and an all-`not verifiable` table. Each rule answers one of
the ten ambiguities the round-1 scorer reported.

### Round 2 (24 PRs: the round-1 14 plus 10 held out, revised rules, fresh blind scorer)

| # | Set | Blocked | Hit rate |
|---|---|---|---|
| 1 | Known-good, round-1 corpus (7) | 0 | 0 false blocks of 7 |
| 2 | Known-bad, round-1 corpus (7) | 7 | 7 of 7 (in-sample: the rules were revised against these) |
| 3 | Known-good, held out (6) | 0 | 0 false blocks of 6 |
| 4 | Known-bad, held out (4) | 3 | 3 of 4 |
| 5 | **All** | 13 good / 11 bad | **0 of 13 false blocks; 10 of 11 caught (91%)** |

Corpus, by PR:

- **Known-good (13):**
  - dev-env PRs 105, 224, 285, 307, 422
  - career-playbook PRs 91, 561
  - lifting-logbook PRs 96, 120, 586, 759, 955, 1030
- **Known-bad (11):**
  - dev-env PR 354
  - career-playbook PRs 284, 2054
  - lifting-logbook PRs 115, 157, 212, 213, 367, 510, 686, 709

Per-PR outcomes in round 2, for diffing against a later recalibration:

| # | PR | Label | Verdict | Rows `not met` |
|---|---|---|---|---|
| 1 | dev-env 105 | good | pass | none |
| 2 | dev-env 224 | good | pass | none |
| 3 | dev-env 285 | good | pass | none |
| 4 | dev-env 307 | good | pass | none |
| 5 | dev-env 422 | good | pass | none |
| 6 | career-playbook 91 | good | pass | none |
| 7 | career-playbook 561 | good | pass | none |
| 8 | lifting-logbook 96 | good | pass | none |
| 9 | lifting-logbook 120 | good | pass | none |
| 10 | lifting-logbook 586 | good | pass | none |
| 11 | lifting-logbook 759 | good | pass | none |
| 12 | lifting-logbook 955 | good | pass | none |
| 13 | lifting-logbook 1030 | good | pass | none |
| 14 | dev-env 354 | bad | block | 3 |
| 15 | career-playbook 284 | bad | block | 3 |
| 16 | career-playbook 2054 | bad | block | 1, 3, 6 |
| 17 | lifting-logbook 115 | bad | block | 2, 4, 8 |
| 18 | lifting-logbook 157 | bad | block | 6 |
| 19 | lifting-logbook 212 | bad | **pass (miss)** | none |
| 20 | lifting-logbook 213 | bad | block | 7 |
| 21 | lifting-logbook 367 | bad | block | 1, 4, 5 |
| 22 | lifting-logbook 510 | bad | block | 1, 2, 3 |
| 23 | lifting-logbook 686 | bad | block | 6 |
| 24 | lifting-logbook 709 | bad | block | 1 |

The labels' evidence text and the scorers' full tables quote content from career-playbook, a
private repo, so they live in the private engineering journal rather than here.

- **Margin to the worst known-good observation.** That observation is lifting-logbook PR 759,
  criterion 5 ("Required Checks Drift passes after the change"). It is post-merge by nature. It
  passed as `not verifiable` only because it cited linked issue #764. Without that link, rule 2
  would have scored it `not met` and blocked the PR. A good PR is therefore one missing issue
  link away from a false block, and that link is the thing the filing rule asks authors to
  provide.
- **Known miss (false pass), lifting-logbook PR 212.** Criterion 4, the manual Tempo check, was
  deferred to #207, which built the stack the check needed but did not track running it. Under
  rule 1, a link to any issue counts as a deferral. Tightening that, so the linked issue must
  name the criterion as its own work, is an open refinement ([dev-env#1197](https://github.com/brownm09/dev-env/issues/1197)). It was not applied here, because it
  would be tuned on the held-out case it fixes. The miss is in the safe direction: a pass, not a
  false block.
- **The figures were measured on the round-2 schema text, not on the text as merged.** After
  round 2, and again in response to this PR's own review, the schema gained changes that no
  scorer has run against:
  - a precedence line (rule 2 outranks rules 3 and 6; the lower-numbered rule wins);
  - linked issues taken from GitHub's `closingIssuesReferences`;
  - wider task-list markers, a fenced-code exclusion, and an `exempt` outcome;
  - a per-issue block and marker;
  - the all-`not verifiable` rule rewritten to change row 1's status.

  Each change either records how the round-2 scorer already decided the colliding rows, or
  concerns parsing and format rather than status judgments. That is the author's reading, not a
  measurement. The first recalibration, [dev-env#1197](https://github.com/brownm09/dev-env/issues/1197),
  scores the merged text and settles it.

**Verdict for the gate:** no false blocks in 13 known-good PRs (6 of them held out). Every
unmet criterion the check caught was real. This supports classifying `not met` as Blocking.
If a false block is observed, record it here and change Step 2g's severity line until the step
is recalibrated.

## Consequences

- Every `/review` comment carries one evidence block per linked issue: a table, or a plain
  statement that none was possible, plus that issue's `acceptance-criteria` marker.
- An issue filed without criteria (and not marked `Exempt:`) draws a non-blocking finding at
  review time. Under ADR-028 and ADR-039 every finding needs a recorded disposition before merge.
  So the cost is one disposition line, such as adding the criteria to the issue, not a hard stop.
  That puts pressure on how issues are filed without making the gap a reason to hold the PR.
- `/review` now depends on reading the schema. It reads `~/.claude/templates/`, falls back to the
  dev-env copy over the API, and stops if neither works. This came up because the templates
  junction was missing on the authoring machine.
- The judgment is an LLM's, so a false `not met` is possible. The calibration bounds it, and the
  recovery path is stated in Step 2g: record the false block here and demote `not met` to
  non-blocking until recalibrated.
- The issue's remaining criterion, "one completed PR in each of dev-env, career-playbook, and
  lifting-logbook shows the table, and its linked issue shows ticked boxes or an evidence
  comment", is post-merge by construction. It is tracked in [dev-env#1196](https://github.com/brownm09/dev-env/issues/1196) rather than claimed here.
