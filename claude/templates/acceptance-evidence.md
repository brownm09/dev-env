# Acceptance-criteria evidence table — shared schema

This file is the single definition of how acceptance criteria are read from an issue and how
the per-criterion evidence for them is recorded. Two consumers rely on it, and neither may
redefine any part of it locally:

- dev-env `/review` Step 2g (`claude/skills/review/SKILL.md`), which writes the table into the
  review comment. An LLM fills it in.
- The Switchyard Definition-of-Done gate ([switchyard#7](https://github.com/brownm09/switchyard/issues/7)),
  which parses the table from a PR body without LLM calls. Everything below is therefore
  specified so that a plain-text parser can apply it.

Decision record: [ADR-153](../../docs/adr/153-acceptance-criteria-filing-and-verification.md).
Available at run time as `~/.claude/templates/acceptance-evidence.md` (junction).

---

## 1. Reading criteria from an issue

1. **Section.** A criteria section is a Markdown heading at level 2 or 3 (`##` or `###`) whose
   text contains `acceptance criteria`, matched case-insensitively (`## Acceptance criteria`,
   `## Acceptance Criteria`, `### Additional acceptance criteria`). It runs until the next
   heading of the same or a higher level. An issue may have more than one; read them all, in
   document order.
2. **Criteria.** Each top-level task-list item in a section (a line starting `- [ ]` or `- [x]`,
   with no indentation) is one criterion. Plain bullets without a checkbox are not criteria.
3. **Sub-items.** Indented lines under a criterion (nested bullets, continuation text) belong to
   that criterion. They are part of what must be verified, but the table's Criterion cell holds
   only the top-level line.
4. **Already-ticked boxes** (`- [x]`) are still criteria. A tick is a claim, not evidence.
5. **Numbering.** Criteria are numbered 1..N in document order across all sections. That number
   is the row's `#`, and it is stable: re-reviewing the same issue gives the same numbers.

### Outcomes

| # | Outcome | Condition |
|---|---|---|
| 1 | `parsed` | At least one criterion was found. |
| 2 | `none` | The issue has no criteria section. |
| 3 | `empty` | A criteria section exists but holds no task-list items. |
| 4 | `no-issue` | The PR links no issue (no `Closes`/`Fixes`/`Resolves #N`), or the issue could not be read. |

**Only `parsed` produces a table.** `none`, `empty`, and `no-issue` are reported as findings,
never treated as a pass. An extraction that finds nothing has verified nothing, so it must not
read as clean.

---

## 2. The evidence table

Placed under the heading `### Acceptance criteria evidence`, with exactly these four columns, in
this order:

```markdown
### Acceptance criteria evidence

| # | Criterion | Evidence | Status |
|---|---|---|---|
| 1 | <criterion text, verbatim> | <what shows it> | met |
| 2 | <criterion text, verbatim> | <what is missing> | not met |
| 3 | <criterion text, verbatim> | <why it can't be checked from the PR> | not verifiable |
```

- **`#`**: the criterion's number from §1.5. One row per criterion, none skipped or merged.
- **Criterion**: the top-level item's text with the `- [ ] ` / `- [x] ` prefix removed, otherwise
  verbatim. Escape a literal `|` as `\|`. Do not paraphrase. A parser matches rows to criteria by
  this text, after trimming whitespace.
- **Evidence**: something a reader can check without trusting the reviewer:
  - a test name and its file,
  - a command plus the output or exit code it produced,
  - a file path and line, or a diff hunk,
  - a URL to a run, comment, or artifact.

  "Looks right", "implemented", or a restatement of the criterion are not evidence. For
  `not met`, say what is missing. For `not verifiable`, say what would verify it and why the PR
  can't show it (for example: post-merge behaviour, another repo, a manual step).
- **Status**: exactly one of these three lowercase values:

| # | Status | Meaning |
|---|---|---|
| 1 | `met` | The evidence shows the criterion is satisfied by this PR, or by an earlier merged change that the evidence cites. |
| 2 | `not met` | The PR is the change meant to satisfy this criterion, and the evidence shows it does not. Includes a criterion this PR's diff simply leaves out. |
| 3 | `not verifiable` | The criterion plausibly holds but cannot be confirmed from the PR: it depends on production data or post-merge behaviour, lives in another repo, needs a live service, is deferred to a linked issue, or is too vague to test (see §4). A pre-merge check that was skipped is `not met`, not this. |

**Deferral and admission rules.** These decide the cases that calibration showed are easy to get
wrong (ADR-153 → *Calibration*):

1. **Deferred to a linked issue → `not verifiable`.** A criterion that the issue or PR body
   explicitly defers to a follow-up *issue link* is `not verifiable`, with the link as its
   evidence. It is not `met`.
2. **Admitted not done, no link → `not met`.** The PR body or a comment may say a criterion is
   not done, skipped, re-scoped, descoped, "remaining open", "done post-merge", or "by design not
   done". If no follow-up issue is linked, the row is `not met`, however good the explanation.
   An explanation without a tracking issue is a silent drop with a reason attached.
3. **`not verifiable` is only for work the PR could not show.** Use it when the criterion
   plausibly holds but can't be confirmed from the PR: production data, behaviour after merge,
   another repo, a live service. It never means "the PR didn't do it". A check that could have
   run before merge (a local build, a manual click-through) and was skipped is `not met`.
4. **Replaced by a different design → `not met`,** unless the issue was amended or the
   replacement was recorded against the issue before merge. A rejected-alternatives section in
   the PR's ADR does not change the issue's criteria.
5. **Compound criteria.** A criterion with several clauses ("X and Y") takes the worst status of
   its clauses, in the order `not met` > `not verifiable` > `met`.
6. **Conditional criteria** ("update X if Y"). The row is `met` when the evidence shows Y didn't
   happen, or that X was done. It is `not verifiable` when Y can't be known from the PR.
7. **Locators** (line numbers, step numbers, file names) are judged by intent. A file landing on
   line 2 instead of line 4 is `met`. A name counts only when the name itself is the point of the
   criterion (a filename other tools read, a CLI flag, a heading a parser keys on).
8. **Accepted evidence.** CI check results on the PR's head commit, and a test-summary line in
   the PR body (`Tests: N passed, ...`), count as command-plus-output evidence. A bare claim
   ("tested locally", "verified") does not.

**Not every row `not verifiable`.** If every row of a `parsed` table is `not verifiable` and none
cites a linked follow-up, report it as `not met` on the first row. A PR that closes an issue while
verifying none of it has not shown that it did the work.

---

## 3. Machine-readable marker

Every review or PR body that carries this table, or reports one of the non-`parsed` outcomes,
also carries this marker on its own line:

```
<!-- acceptance-criteria: outcome=<parsed|none|empty|no-issue> total=<N> met=<N> not_met=<N> not_verifiable=<N> -->
```

For a non-`parsed` outcome, all counts are `0`. `total` must equal `met + not_met + not_verifiable`.
If it doesn't, the table is malformed and a parser must reject it.

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

---

## 5. Recording verification on the issue (after merge)

Once the PR merges, record what was verified on the linked issue in one of two ways:

- tick each box whose row is `met`, by editing the issue body, or
- post the evidence table as a comment, with a first line linking the PR:
  `Verified by <PR URL>:` followed by the table from §2 and the marker from §3.

Rows that are `not met` or `not verifiable` stay unticked, and the comment says what tracks each
of them. Do not tick a box without a `met` row behind it.
