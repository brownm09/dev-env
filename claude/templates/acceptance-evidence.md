# Acceptance-criteria evidence table — shared schema

This file is the single definition of how acceptance criteria are read from an issue and how the
per-criterion evidence for them is recorded. Three consumers rely on it, and none may redefine
any part of it locally:

- dev-env `/review` Step 2g (`claude/skills/review/SKILL.md`), which writes the table into the
  review comment.
- The Switchyard Definition-of-Done gate ([switchyard#7](https://github.com/brownm09/switchyard/issues/7)),
  which reads the table from a PR body.
- `claude/scripts/deferred-followup-sweep.py`, which reads the Evidence cell of `not verifiable` rows
  from merged PRs' blocks. Its regexes must change with the block's format.

Everything below is specified precisely enough that a plain-text parser can apply it.

Decision record: [ADR-153](../../docs/adr/153-acceptance-criteria-filing-and-verification.md).
Available at run time as `~/.claude/templates/acceptance-evidence.md` (junction).

---

## 1. Reading criteria from an issue

1. **Linked issues.** These are the issues GitHub will close when the PR merges:
   `gh pr view <PR> --json closingIssuesReferences`. Use that list, not a regex over the PR body.
   It matches GitHub's own parsing, including a negated or quoted `Closes #N`, which still
   closes the issue. Each linked issue is read and scored on its own.
2. **Section.** A criteria section is a Markdown heading at level 2 or 3 (`##` or `###`) whose
   text contains `acceptance criteria`, matched case-insensitively. It runs until the next
   heading of the same or a higher level. An issue may have more than one; read them all, in
   document order. A matching heading nested inside a section already being read is part of that
   section, not a second one. Headings and lines inside fenced code blocks (```` ``` ````) are
   ignored.
3. **Criteria.** Each top-level task-list item in a section is one criterion: a line with no
   indentation that starts with `-`, `*`, `+`, or `<digits>.`, then a space, then `[ ]`, `[x]` or
   `[X]`. Plain bullets without a checkbox are not criteria.
4. **Sub-items.** Indented lines under a criterion (nested bullets, continuation text) belong to
   that criterion. They are part of what must be verified, but the table's Criterion cell holds
   only the top-level line.
5. **Already-ticked boxes** are still criteria. A tick is a claim, not evidence.
6. **Numbering.** Criteria are numbered 1..N in document order across all of one issue's
   sections. That number is the row's `#`, and it is stable: re-reviewing the same issue gives
   the same numbers.
7. **Exemption.** An issue with no criteria by design (investigation-only, or a tracking/parent
   issue) declares it with a line `Exempt: <reason>` as the first non-blank line of its criteria
   section.

### Outcomes (per linked issue)

| # | Outcome | Condition |
|---|---|---|
| 1 | `parsed` | At least one criterion was found. |
| 2 | `exempt` | The criteria section begins with `Exempt: <reason>` (§1.7). |
| 3 | `none` | The issue has no criteria section. |
| 4 | `empty` | A criteria section exists but holds no task-list items and no exemption. |
| 5 | `no-issue` | The PR has no linked issue (`closingIssuesReferences` is empty). |

**Only `parsed` produces a table.** `none`, `empty`, and `no-issue` are reported as findings,
never treated as a pass; an extraction that finds nothing has verified nothing. `exempt`
produces no finding. **A failed read is not an outcome:** if the PR or the issue can't be fetched
(rate limit, network, auth), the consumer stops and reports a tool error. It must not fall back
to `no-issue`.

---

## 2. The evidence table

Each linked issue gets one block: the heading, an `Issue:` line holding the issue's full URL,
then the table and the §3 marker. With two linked issues, the block repeats.

```markdown
### Acceptance criteria evidence

Issue: https://github.com/<owner>/<repo>/issues/<N>

| # | Criterion | Evidence | Status |
|---|---|---|---|
| 1 | <criterion text, verbatim> | <what shows it> | met |
| 2 | <criterion text, verbatim> | <what is missing> | not met |
| 3 | <criterion text, verbatim> | <why it can't be checked, or the follow-up link> | not verifiable |

<!-- acceptance-criteria: issue=<N> outcome=parsed total=3 met=1 not_met=1 not_verifiable=1 -->
```

For a non-`parsed` outcome, the block has the heading, the `Issue:` line (omitted for
`no-issue`), one sentence saying "No acceptance criteria found (<outcome>), so no criterion was
verified" (or "Exempt: <reason>"), and the marker.

- **`#`**: the criterion's number from §1.6. One row per criterion, none skipped or merged.
- **Criterion**: the top-level line with its list marker and checkbox removed, otherwise
  verbatim. Do not append sub-items and do not paraphrase. Escape a literal `|` as `\|`. To
  match a row to a criterion, a parser unescapes `\|`, trims whitespace on both sides, and
  compares exactly.
- **Evidence**: something a reader can check without trusting the reviewer:
  - a test name and its file,
  - a command plus the output or exit code it produced,
  - a file path and line, or a diff hunk,
  - a URL to a run, comment, or artifact.

  "Looks right", "implemented", or a restatement of the criterion are not evidence. For
  `not met`, say what is missing. For `not verifiable`, give the follow-up link, or say what
  would verify it and why the PR can't show it.
- **Status**: exactly one of these three lowercase values:

| # | Status | Meaning |
|---|---|---|
| 1 | `met` | The evidence shows the criterion is satisfied by this PR, or by another merged change that the evidence cites. |
| 2 | `not met` | The PR is the change meant to satisfy this criterion, and the evidence shows it does not. This includes a criterion the diff simply leaves out, and every case rules 2-4 below assign here. |
| 3 | `not verifiable` | One of exactly three cases: (a) deferred to a linked follow-up (rule 1); (b) it plausibly holds but can't be confirmed from the PR, because it depends on production data, post-merge behaviour, another repo, or a live service; (c) it is too vague to test (§4). |

**Linked follow-up**, wherever this schema uses the term: an issue reference in the Evidence
cell, written as `#N`, `owner/repo#N`, or a `https://github.com/<owner>/<repo>/issues/<N>` URL,
to an issue that takes the criterion on as its own work. Read that issue to decide. It qualifies
only if both of these hold:

- **It names the work.** Its body, or a comment on it, names this criterion's check or outcome as
  something it will do. Other words are fine if they describe the same check or outcome. Sharing a
  topic isn't enough, and neither is building something the check depends on, or a narrower check
  that leaves out part of what the criterion asks.
- **The work is still ahead or was done.** The issue is open, or it was closed by a change that
  did that work. An issue closed with the work still undone no longer tracks it.

The Evidence cell says where the issue names the work (the body item, or a comment URL). A
reference that fails either test is not a linked follow-up, so the row is scored as having no
link. If the issue can't be read, stop and report a tool error, as in §1.

**When to judge.** A review judges both tests as the issue stands when the review runs. A re-score
of a merged PR replays that review, so it judges the second test, and rule 9's open-or-merged
state, as of the PR's merge: a later closure or a PR opened later doesn't count for or against the
row. The first test still reads the issue as it stands now, comments after the merge included,
because a missing name is what the finding asks the author to fix. A closure after the merge is
never this gate's to catch; `claude/scripts/deferred-followup-sweep.py` reports some of them, and it
never changes a row (ADR-153 → *Follow-ups closed after merge*).

### Deferral and admission rules

These rules decide the cases that calibration showed are easy to get wrong (ADR-153 →
*Calibration*). Where two of them apply to the same row, the lower-numbered rule wins.

1. **Deferred to a linked follow-up → `not verifiable`.** The issue or PR body explicitly defers
   the criterion to a linked follow-up, which goes in the Evidence cell. Apart from an open PR
   under rule 9, this is the one case where work the PR did not do is `not verifiable` rather than
   `not met`. It is not `met`.
2. **Admitted not done, with no link → `not met`.** The PR body or a comment says the criterion
   is not done, skipped, re-scoped, descoped, "remaining open", "done post-merge", or "by design
   not done", and no linked follow-up is given. The row is `not met` however good the
   explanation is: an explanation with no tracking issue is a silent drop with a reason attached.
   This rule outranks rules 3 and 6.
3. **Skipped pre-merge check → `not met`.** A check that could have run before merge, such as a
   local build or a manual click-through, was skipped. `not verifiable` never means "the PR
   didn't do it", except under rules 1 and 9.
4. **Replaced by a different design → `not met`,** unless the issue was amended, or the
   replacement was recorded against the issue before merge. A rejected-alternatives section in
   the PR's ADR does not change the issue's criteria.
5. **Compound criteria.** A criterion with several clauses ("X and Y") takes the worst status of
   its clauses, in the order `not met` > `not verifiable` > `met`. Each clause needs only the
   evidence it asks for: a check the clause names, or else any accepted evidence form, including a
   file path and line. A stronger test the criterion didn't ask for isn't a skipped check under rule 3.
6. **Conditional criteria** ("update X if Y"). The row is `met` when the evidence shows Y didn't
   happen, or that X was done. It is `not verifiable` when Y can't be known from the PR.
7. **Locators** (line numbers, step numbers, file names) are judged by intent. A file landing on
   line 2 instead of line 4 is `met`. A name counts only when the name itself is the point of the
   criterion: a filename other tools read, a CLI flag, or a heading a parser keys on.
8. **Accepted evidence.** CI check results on the PR's head commit, and a test-summary line in
   the PR body (`Tests: N passed, ...`), count as command-plus-output evidence. A bare claim
   ("tested locally", "verified") does not.
9. **Delivered by another PR.** Another PR, in any repo, can deliver a criterion if it
   cross-references the linked issue. List them with
   `gh api --paginate "repos/<owner>/<repo>/issues/<N>/timeline"`, and read the diff of any that
   may deliver it: a PR saying it delivers the criterion is a claim, not evidence. If the diff does
   the work, the row is `met` if that PR has merged, or `not verifiable` (case b) if it's still
   open, with the PR's URL in the Evidence cell. This rule outranks rules 2-4, because the work
   isn't undone. If the timeline or that PR can't be read, stop and report a tool error, as in §1.

**When every row is `not verifiable`.** If every row of a `parsed` table is `not verifiable`, and
at least one of those rows has no linked follow-up, change the first such row's Status to
`not met`. Its Evidence says: "no criterion verified and no follow-up linked". The marker counts
the row as `not_met`. A PR that closes an issue while verifying none of it has not shown that it
did the work.

---

## 3. Machine-readable marker

One per linked issue, at the end of that issue's block, on its own line:

```
<!-- acceptance-criteria: issue=<N|none> outcome=<parsed|exempt|none|empty|no-issue> total=<N> met=<N> not_met=<N> not_verifiable=<N> -->
```

- **`issue`** is the issue number, or `none` for `no-issue`.
- **Counts:** for any non-`parsed` outcome, all counts are `0`. `total` must equal
  `met + not_met + not_verifiable` and equal the number of rows in the table. If either check
  fails, the block is malformed and a parser must reject it.

---

## 4. Writing verifiable criteria (filing side)

The global `claude/CLAUDE.md` Git Workflow rule requires every implementation issue to carry a
`## Acceptance criteria` checklist. Each item must name **how it is checked**: a command and its
expected result, a test, a file and what it contains, or an observable behaviour. Examples:

| # | Not verifiable | Verifiable |
|---|---|---|
| 1 | The hook works. | `py -3 claude/scripts/tests/test_foo.py` passes, including a case for an empty transcript. |
| 2 | Docs are updated. | `docs/REFERENCE.md` has a `foo-hook` row in the Hooks table. |
| 3 | The page is clean. | The page renders with no console errors at 375 px width. |

Keep each criterion to one top-level line (§1.3). Put detail in sub-items, since only the
top-level line is copied into the table.

---

## 5. Recording verification on the issue (after merge)

Once the PR merges, record what was verified on each linked issue, in one of two ways:

- tick each box whose row is `met`, by editing the issue body, or
- post a comment whose first line is `Verified by <PR URL>:`, followed by that issue's block
  from §2.

Rows that are `not met` or `not verifiable` stay unticked, and the comment says what tracks each
of them. Do not tick a box without a `met` row behind it.
