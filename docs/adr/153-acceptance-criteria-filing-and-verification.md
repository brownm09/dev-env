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
     or the issue is amended to link a follow-up that names it as its own work, which turns the
     row into `not verifiable`.
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

Round 3 below corrects row 5: one bad label hid a false block, so the real figures were 1 false
block in 14 good PRs and 9 of 10 bad PRs caught.

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
  name the criterion as its own work, was left to a later refinement, because it would be tuned on
  the held-out case it fixes. The miss is in the safe direction: a pass, not a false block. Fixed
  in round 3 below ([dev-env#1197](https://github.com/brownm09/dev-env/issues/1197)).
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
  measurement. Round 3 below scored the merged text and settles it.

**Verdict for the gate, as of round 2:** no false blocks in 13 known-good PRs (6 of them held out).
Every unmet criterion the check caught was real. This supported classifying `not met` as Blocking.
If a false block is observed, record it here and change Step 2g's severity line until the step
is recalibrated. Round 3 below found that this verdict rested on one wrong label.

### Round 3 (2026-11-06, dev-env#1197: follow-up links must qualify)

**Rule change.** A linked follow-up now has to take the criterion on as its own work: the issue
names the criterion's check or outcome, and it is open or was closed by a change that did that
work (schema §2).

**False block found: career-playbook PR 284.** Round 2 counted this PR as a catch, because its
label said no PR or issue recorded the decision in criterion 3. That label was wrong.
tech-leadership-reference PR 51, opened before PR 284 merged, recorded the decision and said it
closed that part of the issue. It merged 16 seconds after PR 284. Step 2g blocked PR 284 because
the issue timeline wasn't an evidence source. So the round-2 result was really 1 false block in 14
good PRs, and 9 of 10 bad PRs caught.

**Response, per the demotion rule.** Step 2g now reads PRs that cross-reference a linked issue
(schema §2 rule 9, which requires reading that PR's diff). The severity line stays `blocking` because this round is the recalibration, and it measured no
false blocks after the fix. Had it measured one, the line would have changed to `non-blocking`.

**Scoring.** Labeller and scorers were separate agents, and the scorers saw only shuffled URLs.

- The ADR-153 corpus was scored at `35602e2`, the merged text plus the tightened rule. That covers
  every schema change listed above as unmeasured.
- The fresh corpus and the 11 PRs that blocked were scored by a second scorer, at `2955b03`, which
  adds cross-referencing PRs. Added evidence can only clear a block, so the 13 passes weren't
  re-scored.
- After PR 1265's review, the rule moved into the schema as rule 9. A third scorer ran on that
  final text, `e6cf18a`, over the 3 new PRs and the 5 PRs that most directly test the changes:
  lifting-logbook 1112, 212 and 165, dev-env 1198, and career-playbook 284. The other PRs weren't
  re-scored on this text.

| # | Set | Blocked | Result |
|---|---|---|---|
| 1 | ADR-153 corpus, known-good, with PR 284 relabelled (14) | 0 | 0 false blocks of 14 |
| 2 | ADR-153 corpus, known-bad, with PR 284 relabelled (10) | 10 | 10 of 10 caught, including PR 212 on criterion 4 |
| 3 | Fresh corpus, known-good (7) | 0 | 0 false blocks of 7 |
| 4 | Fresh corpus, known-bad (4) | 3 | 3 of 4 caught |

Fresh corpus, by PR:

| # | PR | Label | Verdict | What it tested |
|---|---|---|---|---|
| 1 | lifting-logbook 1112 | good | pass | Qualifying follow-up accepted (criterion 9, #1113) |
| 2 | dev-env 1198 | good | pass | Qualifying follow-up accepted (criterion 4, #1196) |
| 3 | lifting-logbook 800 | good | pass | Nothing: the deferred clause was only an "ideally", so no row depended on it |
| 4 | career-playbook 791 | good | pass | Nothing: plain bullets with no checkboxes, outcome `empty` |
| 5 | lifting-logbook 810 | good | pass | Nothing for the deferral: #806 has no criteria section, outcome `none` |
| 6 | lifting-logbook 165 | bad | block | Non-qualifying follow-up rejected (criterion 2, #166) |
| 7 | lifting-logbook 562 | bad | block | Admitted gap with no link (criterion 5) |
| 8 | lifting-logbook 613 | bad | **pass (miss)** | Nothing: plain bullets with no checkboxes, outcome `empty` |
| 9 | win11-init-tools 75 | good | pass | Qualifying follow-up accepted (criterion 6, #63) |
| 10 | switchyard 48 | good | pass | Qualifying follow-up accepted (criterion 2, #10), named only in a comment |
| 11 | switchyard 13 | bad | block | Non-qualifying follow-up rejected (criterion 2, #12) |

**What these figures do and don't show.**

- **Four fresh good PRs reached the rule, not the five #1197 first asked for.** Three of the seven
  fresh good PRs never reached the new definition. Direct evidence that a qualifying link still
  passes is PRs 1112, 1198, 75 and 48 here, plus lifting-logbook PR 759 in the ADR-153 corpus. Direct
  evidence that a non-qualifying link blocks is PRs 165 and 13 here, plus PR 212 in the ADR-153
  corpus. A wording search over about 70 candidate PRs found only these three new cases of the right
  shape, and a search of review markers since 2026-11-04 found no others, though about 45 dev-env
  PRs went unread. The user chose to amend the criterion to four rather than hold the fixes for a
  fifth case.
- **The last scoring pass wasn't fully blind.** To score PR 1198 the scorer had to read its follow-up,
  #1196, whose comment calls PR 284 a false block and lists PR 1112's review as `met`. So those two
  scores are weaker evidence. The scorer also saw author evidence tables in three PR bodies, and
  treated them as claims.
- **Some verdicts depend on how the scorer reads the rule.** PR 13 blocks because #12 closed undone 35
  minutes after merge; judged at merge, it passes. A review run at merge would therefore have passed
  it. PR 48 would block under a stricter reading of a partly tested criterion. PR 212 would pass if
  a near-match follow-up counted. Round 4 below settles these readings, and PR 13 becomes a miss.
- **The rule can be too strict on a single row.** lifting-logbook PR 115 deferred criteria 6 and 7
  to #106, and PR 132 later closed #106 (corrected in *Follow-ups closed after merge* below: PR 132
  only edited a proposal, and PR 134 shipped the screen). Both scorers still marked those rows
  `not met`, because #106 describes the destination screens rather than the click-through. The PR's
  verdict didn't depend on it, since rows 2 and 4 block anyway. On a PR with no other gap, this
  reading would be a false block, and it is the likeliest source of the next one
  ([dev-env#1267](https://github.com/brownm09/dev-env/issues/1267)).
- **The PR 284 result is in-sample.** The evidence source was added after seeing that case. No
  other PR in either corpus tests it, so its reliability on a new case is unmeasured.
- **Plain-bullet criteria aren't scored.** Issues whose criteria have no checkboxes get outcome
  `empty`. That is a reported non-blocking finding, not a verified pass, and it accounts for PR 613.
  The filing rule asks for checkboxes, so this should fade as older issues close.
- **PR 212 needs a judgment call to block.** "Blocked on #207" had to be read as a deferral, and
  #207 had to be read as not taking the check on. Its own manual check covers the API only.
- **The margin is unchanged.** PR 759 still passes only because #764 qualifies as a follow-up.
- **Scorers vary on individual rows.** Across the two scorers, career-playbook PR 2054's `not met`
  rows differed (criterion 7), though its verdict did not.

**Verdict for the gate:** no false blocks measured in 21 known-good PRs after the fix, and 13 of 14
known-bad PRs caught. This supports keeping `not met` as Blocking, which the user chose on
2026-11-06 knowing the limits above. It rests on one in-sample fix, so the demotion rule above
still applies to the next false block.

### Round 4 (2026-11-06, dev-env#1267: does "It names the work" falsely block a delivered deferral?)

**Result: no false blocks, but the wording's false-block risk stays largely unmeasured.** The search
found 2 known-good PRs of the shape the issue asks for, not 3, and in both the verdict didn't depend
on the follow-up. The user chose on 2026-11-06 to amend the criterion to 2 and merge, knowing that
and that switchyard PR 13 becomes a miss. The wording's core test stays.

**Corpus.** A separate labeller confirmed delivery from the delivering diff or a recorded run. It
searched about 66 review markers, scanned about 155 older lifting-logbook PRs, re-read the candidates
earlier rounds excluded, and searched in reverse from follow-ups to the issues they came from. The
reverse phrase search sampled 2 pages per phrase, so it isn't a census.

| # | PR | Follow-up | Label | What the labeller found |
|---|---|---|---|---|
| 1 | lifting-logbook 800 | #798, via PR 805 | good, other words | Delivered. The deferred clause was only an "ideally" |
| 2 | lifting-logbook 1069 | #1057, via PR 1074 | good, other words (weak) | Delivered. Only part of one criterion was deferred, and a comment names it |
| 3 | switchyard 11 | #10, open | good, other words (weak) | Delivery can't be proven while #10 is open |
| 4 | lifting-logbook 544 | #556, via PR 558 | ambiguous | Criterion 1 only partly delivered, because PR 558 is advisory |
| 5 | lifting-logbook 655 | #659 and #660, via PRs 661 and 664 | good, control | Delivered. The follow-ups restate the criteria almost word for word |

No fresh known-bad PR turned up. The distractors came from earlier rounds.

**Scoring.** Labeller and scorers were separate agents, and each scorer saw only a list of URLs. One
scorer ran on the text as it stood (`8fb1b75`) over rows 1 to 5 above, PR 115 and 4 known-bad PRs. Two
more ran on the final text (`1b12aab`), over 19 PRs: rows 1 to 5, every other round-3 PR whose table
has a deferred row, PR 284, and four known-bad PRs that block on a skipped check or an admitted gap
(157, 213, 562, 709), as a guard on the new rule 5 sentence. After scoring, review changed rule 5's
"any accepted form above" to "any accepted evidence form", because rule 8's accepted forms come
below it. No scorer ran on that wording.

| # | Text | Set | Blocked | Result |
|---|---|---|---|---|
| 1 | `8fb1b75` | Rows 1 to 5 above | 0 | None blocked |
| 2 | `8fb1b75` | Known-bad (lifting-logbook 115, 165, 212, 562; switchyard 13) | 4 | 4 of 5 caught; PR 13 passed, judged at merge |
| 3 | `1b12aab` | Known-good (lifting-logbook 655, 759, 800, 1069, 1112; dev-env 1198; win11-init-tools 75; switchyard 48; career-playbook 284) | 0 | 0 false blocks of 9 |
| 4 | `1b12aab` | Weak or ambiguous (switchyard 11; lifting-logbook 544) | 0 | Both pass |
| 5 | `1b12aab` | Known-bad (lifting-logbook 115, 157, 165, 212, 213, 562, 709; switchyard 13) | 7 | 7 of 8 caught; PR 13 passes |

The other 20 round-3 PRs weren't re-scored. The issue requires re-scoring all of them only after a
false block, and none occurred. They have no row that depends on a follow-up's wording or state. The
rule 5 sentence could in principle clear a block on known-bad PRs 354, 367, 510, 686 or 2054; the
four guards didn't move, but those five weren't run.

**The three readings, now in the schema.**

| # | Reading | Settled as | Why | Effect |
|---|---|---|---|---|
| 1 | When a follow-up's state is judged (switchyard PR 13) | A review judges it when it runs. A re-score judges the state as of the merge, but reads the follow-up's wording as it stands now | A re-score should measure the gate a reviewer had. Wording stays current because adding a comment is the finding's stated fix: win11-init-tools PR 75's naming comment came 33 seconds after merge | PR 13 now passes, so the catch rate falls. It is counted as a miss, not relabelled: #12 closed undone 35 minutes after merge, and nothing in this gate would notice |
| 2 | A partly tested compound criterion (switchyard PR 48) | Each clause needs only the evidence it asks for. A stronger test the criterion didn't ask for isn't a skipped check | The accepted evidence forms already include a file path and line | PR 48 passes, row 4 `met` |
| 3 | A near-match follow-up (lifting-logbook PR 212) | Other words count if they describe the same check or outcome. A narrower check doesn't | The single web-to-API trace is the point of that criterion, and #207 checks the API only | PR 212 blocks, row 4 `not met` |

**Why the wording's core test stays.** The issue's trigger is a false block, and none was measured.
The text gained a "When to judge" paragraph and a sentence on near-matches, for readings 1 and 3
above, but "names this criterion's check or outcome" is unchanged. PR 115's rows 6 and 7 stay `not met` under every scorer who has read them, though
#106 was later closed and PR 134 shipped its screen. On a PR with no other gap, that would block until someone added a
comment to #106 naming the click-through, and that's what the finding asks for. A review can't see
later delivery, so what it can check is whether anything tracks the work, and at merge nothing on
#106 did. Loosening the test to pass PR 115 would also pass PR 212's near-match.

**What these figures don't show.**

- **The hard case was barely exercised.** PR 800's deferred clause was optional. PR 1069's row was
  `met` on the developer glossary, so its follow-up didn't matter. Among rows that did reach the test
  with a follow-up in other words, switchyard 11's was accepted, with delivery unproven, and PR 115's
  were rejected. That is too few to give a rate.
- **Known-good PRs remain one reading away from a block.** The closest are lifting-logbook PR 759,
  whose #764 is an auto-filed alert with a comment promising to close once the run is green, and
  switchyard PR 48, whose #10 names the sandbox run only in a comment worded "the other thing to
  check". A stricter reading of "as something it will do" would block both.
- **Readings outside this issue still flip verdicts.** The final-text scorers named 10 they thought
  unsettled, for example whether an unticked click-through needing a live stack is `not met` (PR 213)
  or `not verifiable`. PRs 544, 562, 655 and 709 also have one. These weren't in the issue's scope.
- **Blinding was partial.** Bodies and comments on PRs 11, 13, 48, 75, 1112 and 1198 carried authors'
  evidence tables, and #1196 mentions PR 284's false block. Scorers treated those as claims.
- **Scorers vary on rows, not verdicts.** PR 115's row 2 was `met` for one scorer and `not met` for
  another. It blocks either way.
- **PR 544 is unresolved.** It passes, but its label is ambiguous, so it counts for neither side.

**Verdict for the gate:** taking each PR's latest score across rounds 3 and 4, no false blocks in 23
known-good PRs, and 12 of 14 known-bad PRs caught (PR 13 and PR 613 missed). This supports keeping `not met` as Blocking, with
the demotion rule still in force. It says little about follow-ups in other words, for which the
corpus barely exists.

## Follow-ups closed after merge (2026-11-06, dev-env#1292)

Round 4 left a known miss: Step 2g runs before merge, so it can't see a follow-up that closes undone
afterwards.

**Decision: build a small report-only sweep, and run it from `biweekly-retro`.** It is
`claude/scripts/deferred-followup-sweep.py`. It reads merged PRs' deferrals and reports a follow-up
that closed after the merge in one of two ways: as not planned or a duplicate, or by a PR whose body
says it doesn't close that issue. Everything else counts as "not judged". It never blocks and never
writes to GitHub. A person reads each line and reopens the issue or records why the work was dropped.

**Why build anything: the numbers.** Across 2,032 merged PRs in 7 repos, an exploratory extractor,
wider than the shipped one, found 67 follow-up references closed after their deferring PR merged. All
27 closures with an unusual shape were labelled, plus 15 of the other 40 picked at random. A separate agent labelled the 42 entries from diffs and code. One
is a duplicate of another under a renamed repo, and 8 weren't real deferrals, which leaves 33:

| # | Label | Count | Issues |
|---|---|---|---|
| 1 | Delivered | 26 | |
| 2 | Undone, and nothing records it | 4 | switchyard#12, lifting-logbook#468, lifting-logbook#259, one in career-playbook |
| 3 | Undone, with a recorded decision | 3 | dev-env#1266, dev-env#356, dev-env#1120 |

So about 1 in 8 labelled deferrals was silently dropped. The 25 unlabelled closures may hold more.
This isn't only history: among the 85 PRs carrying an evidence marker, all merged in the few days
since the schema started, 8 deferred a criterion, to 7 follow-ups. One of those was dropped (#12), one
was shelved on the record (#1266), and one was closed with evidence (#1277).

**What the closures have in common.** Two of the four silent drops were accidents of GitHub's keyword
parsing. Switchyard PR 21 says "It doesn't close #12", and lifting-logbook PR 510 says it does not
close #468, yet GitHub closed both. A closing PR proves little either way. switchyard#12 and
lifting-logbook#106 look the same to every mechanical signal except that wording: each closed as
completed by a merged PR whose diff didn't do the work, with its boxes unticked. #106's work came
from elsewhere. **This corrects round 3, round 4 and this issue's own text:** PR 132 didn't deliver
#106. It is a docs-only change to a proposal, and its `Closes #106` closed the issue about 10 hours before
PR 134 shipped the screen. The labeller confirmed the screen and its routes but checked the dashboard's
click-through only indirectly, so #106 stays the known-good reference on that weaker footing.

**Calibration, per ADR-144.** The check stays a diagnostic.

- **Measured property:** how the follow-up closed, not whether the work exists.
- **On the labelled corpus:**

| # | Set | Reported | Rate |
|---|---|---|---|
| 1 | Undone, unrecorded (4) | 2 | Misses lifting-logbook#259 and the career-playbook issue, both closed by ordinary PRs |
| 2 | Undone, recorded (3) | 2 | Misses dev-env#1120, closed by a docs PR |
| 3 | Delivered (26) | 1 | lifting-logbook#480, closed as not planned as a duplicate of #768, and delivered under #768 |

- **The worst known-good observation** is that #480 report, which costs a person one read.
- **Across all 7 repos** (2,038 merged PRs when last run) it reports 5 follow-ups: #12, #468, #480,
  #1266 and #356. It leaves 41 closures not judged.
- **The negation pattern is in-sample.** It was written after reading #12 and #468, and no held-out
  case exists. It requires the negation right before the keyword and this issue's number, and every
  keyword naming the issue must be negated, table rows aside. So "doesn't block closing #388", and
  "did not fix #12" followed by a `Closes #12` line, don't match. Two fixes came from these same
  references: PR 21 also has a table row "fixed: #12 is amended", and PR 510 also says "**Not**
  `Closes #468`".
- **Extraction:** the script prints the number of deferrals it found and warns at zero, so an empty
  extraction can't read as a clean run. It found 102, 9 of them from evidence tables, and printing that
  split means an evidence path gone empty can't hide behind the prose path. The shipped extractor is
  narrower than the one that built the corpus. It drops 6 of the 8 labelled non-deferrals, and also 8 of
  the 26 delivered follow-ups and dev-env#1120, all of whose lines use other wording. All 4 silent drops
  are still extracted, and the reported set didn't change. A silent drop worded like those 8 would be
  missed.
- **Read by anyone.** Evidence blocks are read from comments by any author, and three swept repos are
  public. A forged block can only add a line a person reads, and a reference to nothing is counted as
  unresolved instead of stopping the run, so neither can silence the report.
- **The references** from this issue hold: switchyard PR 13 reports #12, and lifting-logbook PR 115
  reports nothing and counts #106 as not judged. Unit fixtures pin both, because a live run of PR 13
  would change if #12 is reopened.

The labels quote private content, so they live in the shared notes for this work and the private
journal rather than here.

**Alternatives considered.**

| # | Alternative | Why rejected |
|---|---|---|
| 1 | Do nothing, and record why | About 1 in 8 labelled deferrals dropped silently, and #12 happened within days of the schema. The sweep is about 300 lines and spends no model tokens |
| 2 | A step when a follow-up issue closes (a hook on `gh issue close`) | It can't see #12 or #468: both closed through a merge keyword, with no such command. A workflow on GitHub's `issues.closed` event would see them, but needs deploying per repo and still couldn't judge the work |
| 3 | Report any closure with unticked boxes | It can't separate #12 from #106, and 72 of 75 closed checklists were unticked when ADR-153 was written |
| 4 | Report every docs-only closing PR | 2 undone among the 9 real deferrals closed that way. Too noisy without a person reading diffs |
| 5 | An agent reads every closed follow-up in the sweep | It would catch more, as the labeller did, at about 5,000 subscription tokens per follow-up on each run. Possible later, if the retro's "not judged" count proves worth reading. It is also a judge that would need its own calibration |
| 6 | Have `/review` read the closing PR's linked issues more strictly | It already should have: PR 21's `closingIssuesReferences` includes #12, and its review reported `no-issue`. The body was edited after that review, so what the reviewer saw is unknown. That covers only one path anyway |

**What this doesn't fix.** It finds half of the silent drops it was measured on. The two accidents
would be better prevented than detected, by flagging a negated closing keyword before merge, which is
tracked in [dev-env#1300](https://github.com/brownm09/dev-env/issues/1300). Reporting doesn't reopen anything, and a person still has to act on the line.

**Where it runs.** `biweekly-retro` isn't registered as a scheduled task or a cloud trigger on the
machine this was written on, though its report branches exist through 2026-10-31. So the step takes
effect only where that routine's live prompt reads this file (dev-env#1110 tracks the routine host).

## Negated closing keywords (2026-11-06, dev-env#1300)

GitHub [honors a closing keyword](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
wherever it appears, so "It doesn't close #12" closes #12. The sweep reports that after the merge.
This warns before it.

**Decision: warn from `pre-merge-findings-gate.py`, and don't block yet.** The negation moved to
`claude/scripts/_closing_negation.py`, which the gate and the sweep both import. The gate is the last
read of the body before merge, so it covers switchyard PR 21's body, which was edited after review,
and it adds no `gh` call. The warning doesn't cover a merge through the REST fallback, which skips
this hook.

**Calibration, per ADR-144.**

- **Measured property:** the wording says the PR doesn't close the issue, in a form GitHub reads as
  closing it. GitHub's `closingIssuesReferences` confirmed that second half for every match.
- **Corpus:** all 2,035 merged PRs in the 7 repos #1292 swept. #1292 didn't list them. They are
  dev-env, lifting-logbook, career-playbook, cover-letter-runtime, switchyard, win11-init-tools and
  tech-leadership-reference, inferred because these total 2,035 against its 2,032 about an hour earlier.
- **Known-bad:** 4 matches, all true. Each PR closed the issue it said it didn't:

| # | PR | Issue it closed | Label |
|---|---|---|---|
| 1 | switchyard PR 21 | #12 | true |
| 2 | lifting-logbook PR 510 | #468 | true |
| 3 | career-playbook PR 651 | #646 | true |
| 4 | career-playbook PR 1743 | #1095 | true |

- **Known-good:** the other 2,031 PRs produced no match. That is weak evidence, since they weren't read
  one by one. Three near misses show the margin, and each is one form away from a false warning:
  - lifting-logbook PR 478 mentions a keyword in a code span, which GitHub didn't link.
  - career-playbook PR 1173 has emphasis closing after the keyword's colon, which GitHub didn't link, so
    that issue is still open. Across the corpus that form went unlinked in 2 of 2 cases.
  - career-playbook PR 1533 has a negated mention in a code span and a real closing keyword for the same
    issue. An earlier pattern matched it, and a first label called it true because GitHub linked it. It
    was false: that PR meant to close the issue. Requiring every mention to be negated fixed it.
- **Where the pattern came from:** the first form matched rows 1, 2 and 4. A wider search of the same
  corpus found row 3, and emphasis was added. The #1292 session then rewrote the negation for its own
  review findings, and its form matched the same rows. Only row 4 was found by a pattern not written from
  it, and there's no held-out set, which is why this still doesn't block.
- **Extraction:** the check reads one body, and the fixtures assert a non-empty result on rows 1 and 2.

**What this misses.** An accidental closure with no negation. The wider search found dev-env PR 800
linking #2 through "proposed fix #2", and cover-letter-runtime PR 89 linking #83 through a sentence
about another PR's fix. Matching those would mean warning on every closing keyword.

**Alternatives considered.**

| # | Alternative | Why rejected |
|---|---|---|
| 1 | Check in `pre-pr-create-check.py` | It reads the command, not the PR, and it would miss a body edited later, as switchyard PR 21's was |
| 2 | A separate hook on `gh pr merge` | A second `gh pr view` per merge for a body the gate already fetches |
| 3 | Block now | Only one match came from a pattern not written from it |
| 4 | Report only the linked issues that also have negated wording, using `closingIssuesReferences` | Every match is already linked, so it adds a field to the query and changes no result. If a new unlinked form causes a false warning, this is the fix to reach for |

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
