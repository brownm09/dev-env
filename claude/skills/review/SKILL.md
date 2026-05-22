---
name: review
description: Review a PR or diff for correctness, security, reliability, and maintainability. Produces a structured report with blocking findings, non-blocking findings, questions for the author, and optional style notes. Invoke as /review <PR-URL> [--no-style] [--author junior|mid|senior] [--focus security|correctness|perf] [--no-comment].
argument-hint: "<PR-URL | --diff> [--no-style] [--author <level>] [--focus <area>] [--no-comment]"
allowed-tools: Bash Read Grep Agent
---

You are conducting a structured code review. Your goal is to produce a report the PR author
can act on without a follow-up conversation — every finding must have a "what to do" line.

---

## Step 1 — Parse $ARGUMENTS

`$ARGUMENTS` takes one of these forms:

- **PR URL only:** `https://github.com/owner/repo/pull/123`
- **With flags:** `https://github.com/owner/repo/pull/123 --no-style --author mid`
- **Diff mode:** `--diff` (no URL — you will ask the user to paste the diff)

Parse rules:
1. If `$ARGUMENTS` starts with `http`, treat the first token as **PR_URL**.
   Otherwise if `$ARGUMENTS` starts with `--diff`, set **DIFF_MODE=true**.
   Otherwise ask: "Provide a PR URL or use --diff to paste a diff."
2. Extract optional flags from remaining tokens:
   - `--no-style` → **STYLE=false** (default: true)
   - `--author <level>` → **AUTHOR_LEVEL** = junior | mid | senior (default: mid)
   - `--focus <area>` → **FOCUS** = security | correctness | perf (default: all)
   - `--no-comment` → **POST_COMMENT=false** (default: true) — skip posting the review as a PR comment

Tell the user what you parsed:
- "Reviewing: `<PR_URL>`" (or "Diff mode — paste your diff")
- Flags in effect (omit defaults): e.g., "--no-style, author=junior, focus=security, --no-comment"

---

## Step 2 — Fetch the diff and PR context

**If PR_URL is set:**

```bash
gh pr view "<PR_URL>" --json title,body,additions,deletions,changedFiles,baseRefName,headRefName,labels
```

Store **PR_TITLE**, **PR_BODY**, **ADDITIONS**, **DELETIONS**, **CHANGED_FILES**, and **LABELS**.

**Duplicate-review guard:** If `reviewed-by-claude` appears in LABELS, tell the user:

> "⚠️ This PR already has the `reviewed-by-claude` label — it appears to have been reviewed.
> Re-review anyway? (y/n) [default: n]"

If the user answers `y`, continue. Otherwise (answer is `n`, no response, or non-interactive context), stop.

Then fetch the diff:

```bash
gh pr diff "<PR_URL>"
```

Store:
- **DIFF** — the full diff text
- **DIFF_SIZE** = ADDITIONS + DELETIONS

**If DIFF_MODE is true:**

Ask: "Paste the diff below. Send an empty line when done."
Accept the pasted content as **DIFF**. Set PR_TITLE="(pasted diff)", PR_BODY="", DIFF_SIZE=estimated line count.

---

## Step 2b — Documentation Reconciliation Check

Applies to PR_URL mode only. Skip if DIFF_MODE is true.

Using the changed file list from Step 2, check whether the repo has a Documentation Maintenance
table. Fetch the project's `CLAUDE.md` from the remote PR branch (per ADR-004, always read
from the remote, not the local worktree):

```bash
git show origin/<headRefName>:.claude/CLAUDE.md 2>/dev/null \
  || git show origin/<headRefName>:CLAUDE.md 2>/dev/null
```

Search the output for the phrase `Documentation Maintenance`. If not found, skip this step and note
"No doc-reconciliation rules defined for this repo."

If the table exists (dev-env and any repo that adopts the pattern):

1. Check whether any changed path matches `claude/skills/**`, `claude/hooks/**`,
   `claude/scripts/**`, or `claude/routines/**`.
2. If yes, check whether `README.md` or `docs/REFERENCE.md` also appears in the changed files.
3. If neither README.md nor docs/REFERENCE.md appears, record a **Documentation** blocking
   finding to include in the Step 6 output:

   > **[documentation]** Missing reference doc update
   > Paths under `claude/skills/`, `claude/hooks/`, `claude/scripts/`, or `claude/routines/`
   > were changed, but neither `README.md` nor `docs/REFERENCE.md` appears in the diff.
   > **Fix:** Per the Documentation Maintenance table in `CLAUDE.md`, update the relevant
   > section(s) of `README.md` and/or `docs/REFERENCE.md` in this PR.

Proceed to Step 2c.

---

## Step 2c — Documentation Coverage Check

Applies to both PR_URL mode and DIFF_MODE.

For every file that was **added, deleted, or renamed** in the diff (structural changes),
and for every file whose change reflects any of the **4 Ds** — a **decision made**, a
**discovery surfaced**, a **dependency introduced** (or removed), or a **deviation from
documented process** — apply the following check. (The 4 Ds match the dev-env stub-update
criteria in `claude/CLAUDE.md`; minor bug fixes, typo corrections, and content-only updates
to an existing file do not qualify.)

1. Identify the file's directory and all ancestor directories up to depth 3 from the
   changed file (stop at repo root). When multiple files share an ancestor, deduplicate
   the ancestor set before issuing `git show` calls — query each unique ancestor once.
2. For each unique ancestor directory, check whether a `README.md` exists there.
   - In PR_URL mode: `git show origin/<headRefName>:<dir>/README.md 2>/dev/null`
   - In DIFF_MODE: a pasted diff cannot reveal unchanged READMEs. Skip the blocking
     branch entirely (step 4 below) — only the non-blocking suggestion branch (step 5)
     applies. Note in the review output: "DIFF_MODE — README-staleness check skipped;
     paste the relevant README contents or use PR_URL mode for full coverage."
3. Use judgment to determine whether this specific change warrants a documentation update
   at any of those levels. Consider:
   - **New file in a directory that has a README index** → the index likely needs a new entry.
   - **Deleted or renamed file in a directory with a README** → the index reference may need removal or updating.
   - **File rewritten to serve a different purpose** → the README description may be stale.
   - **Cross-tree impact**: a skill, hook, or workflow change referenced in a parent README or
     in a related doc outside the immediate directory tree → flag the relevant parent or sibling
     doc even if it is not in the same directory as the changed file.
4. (PR_URL mode only.) For each ancestor where a README **exists** and the change
   **warrants** an update and the README **does not appear in the diff**: record a
   **blocking Documentation finding**.
5. For each ancestor where no README exists but one **would add navigational value**
   (e.g., a directory now has several files with no index): record a **non-blocking
   Documentation finding** suggesting creation.

If no ancestor directories have READMEs and no cross-tree impact is identified, note
"No README coverage gaps found." and proceed to Step 3.

**Example findings:**

> **[documentation]** `context/sentence_density.md` added without README update
> `context/README.md` is an index of all files in this directory. Adding a new file here
> without an entry leaves sessions that orient from `context/README.md` unaware of the
> new file.
> **Fix:** Add a `### \`sentence_density.md\`` entry to `context/README.md` describing
> its purpose and when to load it.

> **[documentation]** `claude/skills/new-skill/` added; top-level README not updated
> `README.md` contains a Skills table that lists all available skills. Adding a skill
> without updating the table leaves the skill undiscoverable to sessions that orient from
> `README.md`.
> **Fix:** Add a row for `new-skill` to the Skills table in `README.md` and the Skills
> section of `docs/REFERENCE.md`.

Proceed to Step 2d.

---

## Step 2d — Test Coverage Gate Check

Applies to both PR_URL mode and DIFF_MODE.

Per the global "Test before PR" rule in `claude/CLAUDE.md` (see ADR-022), every PR that
introduces new testable behavior must either include tests for it or document the deferral
in the PR body.

Inspect the DIFF and PR_BODY:

1. Determine whether the diff introduces **new testable behavior**. Examples that qualify:
   - A new API endpoint, route handler, controller method, or RPC procedure.
   - A new frontend page, interactive component, or user-facing feature.
   - A new exported function or class in a library/package.
   - A new CLI command, flag, or subcommand.
   - A bug fix (any change under `fix/` or that the PR_BODY frames as a bug fix).

   Changes that do **not** qualify: pure refactors with no behavior change, documentation,
   comments, formatting, dependency bumps, config-only tweaks, generated files.

2. If new behavior is present, check whether the diff also includes corresponding test
   files (test directories, `*.spec.*`, `*.test.*`, `e2e/**`, `tests/**`, fixtures, etc.).

3. If new behavior is present and no tests appear in the diff, scan PR_BODY for a written
   deferral rationale. A valid deferral explicitly names the deferred coverage and the
   reason (e.g., "Playwright tests deferred until #259 lands" or "Manual test plan below;
   automation tracked in #N"). A checkbox marked "no new testable behavior" on a PR that
   clearly does introduce new behavior is **not** a valid deferral — flag it.

4. Record findings:
   - **Blocking [correctness] — Missing test coverage** when new behavior is present, no
     tests appear, and no deferral rationale is in PR_BODY.
     **Fix:** Add tests for the new behavior, or document in the PR body the specific tests
     deferred, what tracks them, and why deferral is acceptable for this PR.
   - **Blocking [correctness] — Coverage checkbox mismatch** when the PR body marks
     "no new testable behavior" but the diff clearly adds new behavior.
     **Fix:** Correct the checkbox and either add tests or document the deferral rationale.
   - **Non-blocking [maintainability] — Coverage rationale is vague** when a deferral
     rationale exists but does not name what is deferred or how it will be tracked.
     **Suggestion:** Reference a tracking issue and state which test tier (unit, integration,
     E2E) is deferred.

If the diff is docs-only, refactor-only, or otherwise introduces no new testable behavior,
note "No new testable behavior — coverage gate not applicable." and proceed.

Proceed to Step 3.

---

## Step 3 — Decide analysis path

**DIFF_SIZE ≤ 400 lines or FOCUS is set:** Proceed to Step 4 (single-pass analysis).

**DIFF_SIZE > 400 lines and FOCUS is not set:** Proceed to Step 5 (parallel subagent analysis).

---

## Step 4 — Single-pass analysis

Read the DIFF in full. For each changed file, identify its intent (what is this change trying
to do?) before looking for problems.

For every substantive finding, answer all four questions:
1. **What** — a factual description of what the code does
2. **Why it matters here** — the consequence in this specific codebase/context, not in the abstract
3. **Category** — one of: correctness | security | reliability | documentation | performance | maintainability | style
4. **What to do** — a concrete action the author can take

Report every substantive finding — do not cap. Quality discipline comes from the four-question
gate above (what / why here / category / what to do), not from a count limit. A finding that
cannot meet all four questions should be dropped or downgraded; one that can meet them belongs
in the report regardless of how many others are already listed.

Proceed to Step 6.

---

## Step 5 — Parallel subagent analysis (large diffs only)

Spawn two subagents in parallel using the Agent tool with `model: "opus"`. Pass each the full DIFF and PR context.

**Subagent A — Correctness & Security:**

> You are reviewing a code diff for correctness and security issues only.
> PR: "<PR_TITLE>"
> PR description: "<PR_BODY>"
> Diff:
> <DIFF>
>
> For each finding, answer: (1) what the code does, (2) why it matters in this context,
> (3) whether it is correctness or security, (4) what the author should do.
> Report every substantive finding that meets the four-question gate. Do not cap. Do not comment on style, performance, or maintainability.
> Format each finding as:
> **[correctness|security]** <file>:<line> — <what> / <why here> / <what to do>

**Subagent B — Reliability, Performance & Maintainability:**

> You are reviewing a code diff for reliability, performance, and maintainability issues only.
> PR: "<PR_TITLE>"
> PR description: "<PR_BODY>"
> Diff:
> <DIFF>
>
> For each finding, answer: (1) what the code does, (2) why it matters in this context,
> (3) whether it is reliability, performance, or maintainability, (4) what the author should do.
> Report every substantive finding that meets the four-question gate. Do not cap. Do not comment on correctness, security, or style.
> Format each finding as:
> **[reliability|performance|maintainability]** <file>:<line> — <what> / <why here> / <what to do>

Wait for both subagents to complete, then merge their findings. If the same issue is surfaced
by both, merge into one entry and use the higher-severity category.

Proceed to Step 6.

---

## Step 6 — Classify and structure findings

Assign each finding to one of four buckets:

**Blocking** (correctness | security | reliability | documentation):
- Incorrect behavior, crashes, data loss, race conditions
- Security vulnerabilities (injection, auth bypass, secrets in code, unsafe deserialization)
- Reliability failures (unhandled errors in critical paths, missing retries where required)
- Missing test coverage for a behavior change in a tested codebase
- Documentation gaps from Step 2b (skill/hook/script/routine changed without updating README.md or docs/REFERENCE.md)
- Documentation gaps from Step 2c (README exists at a relevant directory level and warrants an update, but was not changed in this PR)

**Non-Blocking** (performance | maintainability | documentation):
- Performance concerns that do not affect correctness
- Code that works but will be hard to extend, test, or debug
- Step 2c suggestion to create a README where none exists (not required, but advisable)

**Questions for Author:**
- Only for ambiguities where intent is genuinely unclear — frame as a question, not a criticism.
- Every question must include three parts:
  1. **Question** — the question itself, phrased so the author can answer yes/no or pick an option.
  2. **Context** — what you saw in the diff or surrounding code that made the intent unclear (file, line, the specific shape of the ambiguity). Without this the author cannot tell whether you misread the change or spotted a real gap.
  3. **Tradeoffs** — the realistic alternatives you considered and what each implies for this codebase (behavior, callers, future maintenance, performance, etc.). One sentence per alternative is enough; two or three alternatives is typical.
- A question that cannot supply Context and Tradeoffs is not yet a review question — either resolve it by reading more of the code, or convert it to a non-blocking finding with a concrete suggestion.
- Example shape:
  > **Question:** Is the `None` return in `parse_user(payload)` intentional, or should it raise?
  > **Context:** `parse_user` in [`api/users.py:42`](api/users.py:42) returns `None` when `payload["id"]` is missing, but every existing caller in `api/handlers/` immediately dereferences `.id` on the result, which will `AttributeError` instead of producing a 400.
  > **Tradeoffs:** (a) Raise `ValidationError` — callers get a typed exception they already handle in the request middleware, but any non-HTTP caller (e.g., the batch importer) has to add a try/except. (b) Keep returning `None` and fix each caller to check — preserves the current type signature but spreads the null check across ~8 sites. (c) Return a sentinel `INVALID_USER` — avoids both, but is a new pattern in this codebase.

**Style** (only if STYLE=true):
- Naming, formatting, comment quality
- Always non-blocking, always grouped (not itemized per line)
- Skip entirely if STYLE=false

---

## Step 7 — Apply AUTHOR_LEVEL tone

Adjust explanation depth based on AUTHOR_LEVEL:

**junior:** Include the "why it matters here" reasoning in full. For security/correctness issues,
briefly explain the class of problem (e.g., "SQL injection occurs when user input is interpolated
directly into a query string..."). Suggest a concrete fix, not just a direction.

**mid (default):** State the problem and its consequence. Suggest direction, not the full fix.

**senior:** State the problem. Skip background explanations. Omit "consider" hedging — be direct.

---

## Step 8 — Emit the review

```
## Review: <PR_TITLE>

### Summary
<1 paragraph: overall readiness — merge / merge with minor changes / needs revision.
Name the single highest-severity finding. If DIFF_SIZE > 400, note whether the PR
is appropriately scoped or should be split.>

---

### Blocking Findings
<For each finding — omit section header if no blocking findings>

**[category]** `<file>:<line>`
<What the code does.>
<Why it matters in this specific context.>
**Fix:** <Concrete action.>

---

### Non-Blocking Findings
<For each finding — omit section header if no non-blocking findings>

**[category]** `<file>:<line>`
<What the code does.>
<Why it matters here — or why it will matter later.>
**Suggestion:** <Direction for improvement.>

---

### Questions for Author
<For each question — omit section header if no questions>

**Question:** <The question itself.>
**Context:** <What in the diff or surrounding code made the intent unclear. Cite file:line.>
**Tradeoffs:** <The realistic alternatives and what each implies for this codebase. One sentence per alternative; two or three alternatives is typical.>

---

### Style Notes
<Grouped, not itemized. One short paragraph per concern.>
<Omit section if STYLE=false or no style findings.>

---

*Reviewed by `<your model ID>` via Claude Code*
```

---

## Step 9 — Post comment (skipped only if --no-comment)

Step 8 always emits the review to the terminal. If **POST_COMMENT=true** and **PR_URL** is set,
also post it as a PR comment and apply the `reviewed-by-claude` label so both are always present.

Write the review output to a temp file to avoid shell-quoting issues with backticks and
special characters, then post it:

```bash
TMPFILE="C:/Users/brown/.claude/scratch/review_comment_$$.md"
cat > "$TMPFILE" << 'REVIEW_EOF'
<full Step 8 review output>
REVIEW_EOF
gh pr comment "<PR_URL>" --body-file "$TMPFILE"
gh pr edit "<PR_URL>" --add-label "reviewed-by-claude"
rm -f "$TMPFILE"
```

If either `gh` command fails, report the error to the user and note that the review was
still emitted to the terminal.

Report: "Review posted as comment: <PR_URL>"

If POST_COMMENT is false (i.e., `--no-comment` was passed), or DIFF_MODE is true, skip this step.

---

## Notes

- "Why it matters here" is the hardest part and the most important. Do not substitute abstract
  principles ("this violates DRY") for contextual consequences ("this means the two copies of
  this logic can diverge silently").
- If the PR description is empty and the diff is ambiguous, include a Question asking the author
  to describe the intent — a missing description is itself a review finding.
- Do not comment on lines that a linter or formatter would catch if STYLE=false. Automation
  handles those; your job is everything automation misses.
- Signal discipline comes from the four-question gate (what / why here / category / what to do)
  and the "genuine ambiguity" gate on author questions — not from a finding count cap. A finding
  that meets the gate belongs in the report; one that does not should be dropped, not held back
  to stay under a number.
- **Follow-up / merge-readiness checks:** When verifying whether findings have been addressed on
  an existing PR, always fetch the remote branch first — never read files from the local working
  tree or current worktree. Protocol: `git fetch origin <headRefName>`, then read via
  `git show origin/<headRefName>:<path>`. The local tree may be stale or on a different branch,
  producing false "still outstanding" results.
