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

- **Ground truth for the known-bad PRs** was independent evidence in each case: the PR body
  leaving the item unchecked or struck through, a closing comment marking it "Replaced", or a
  partial-closure keyword ("Closes #283 Part 1"). All cases are real; none is synthetic.

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

The labels and both scorers' full per-PR tables are kept in the private engineering journal
(`sessions/dev-env/reports/2026-11-04-acceptance-criteria-calibration.md`), not here. They quote
content from career-playbook, which is a private repo. Anyone with access to the three repos can
re-derive them from the PR list above.

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
- **Precedence clarifications added after round 2,** for two collisions the round-2 scorer
  reported: rule 2 outranks rules 3 and 6, and a negated `Closes #N` still links. Both write down
  how that scorer already decided those rows, so no round-2 outcome depends on them.

**Verdict for the gate:** no false blocks in 13 known-good PRs, and every unmet criterion it
caught was real. Blocking on `not met` is supported. If a false block is observed, record it here
and demote `not met` to non-blocking until recalibrated (Step 2g → *Calibration*).

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
  comment", is post-merge by construction. It is tracked in [dev-env#1196](https://github.com/brownm09/dev-env/issues/1196) rather than claimed here.
