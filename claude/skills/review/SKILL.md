---
name: review
description: Review a PR or diff for correctness, security, reliability, and maintainability. Produces a structured report with blocking findings, non-blocking findings, questions for the author, and optional style notes. Invoke as /review <PR-URL> [--no-style] [--author junior|mid|senior] [--focus security|correctness|perf].
argument-hint: "<PR-URL | --diff> [--no-style] [--author <level>] [--focus <area>]"
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

Tell the user what you parsed:
- "Reviewing: `<PR_URL>`" (or "Diff mode — paste your diff")
- Flags in effect (omit defaults): e.g., "--no-style, author=junior, focus=security"

---

## Step 2 — Fetch the diff and PR context

**If PR_URL is set:**

```bash
gh pr view "<PR_URL>" --json title,body,additions,deletions,changedFiles,baseRefName,headRefName
```

Then fetch the diff:

```bash
gh pr diff "<PR_URL>"
```

Store:
- **PR_TITLE**, **PR_BODY**, **ADDITIONS**, **DELETIONS**, **CHANGED_FILES**
- **DIFF** — the full diff text
- **DIFF_SIZE** = ADDITIONS + DELETIONS

**If DIFF_MODE is true:**

Ask: "Paste the diff below. Send an empty line when done."
Accept the pasted content as **DIFF**. Set PR_TITLE="(pasted diff)", PR_BODY="", DIFF_SIZE=estimated line count.

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
3. **Category** — one of: correctness | security | reliability | performance | maintainability | style
4. **What to do** — a concrete action the author can take

Apply the cap: collect all findings, then keep the 5–7 highest-severity blocking findings and
the 5 most impactful non-blocking findings. If you found more, note the count in the Summary.

Proceed to Step 6.

---

## Step 5 — Parallel subagent analysis (large diffs only)

Spawn two subagents in parallel using the Agent tool. Pass each the full DIFF and PR context.

**Subagent A — Correctness & Security:**

> You are reviewing a code diff for correctness and security issues only.
> PR: "<PR_TITLE>"
> PR description: "<PR_BODY>"
> Diff:
> <DIFF>
>
> For each finding, answer: (1) what the code does, (2) why it matters in this context,
> (3) whether it is correctness or security, (4) what the author should do.
> Limit: 5 blocking findings max. Do not comment on style, performance, or maintainability.
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
> Limit: 5 findings max. Do not comment on correctness, security, or style.
> Format each finding as:
> **[reliability|performance|maintainability]** <file>:<line> — <what> / <why here> / <what to do>

Wait for both subagents to complete, then merge their findings. If the same issue is surfaced
by both, merge into one entry and use the higher-severity category.

Proceed to Step 6.

---

## Step 6 — Classify and structure findings

Assign each finding to one of four buckets:

**Blocking** (correctness | security | reliability):
- Incorrect behavior, crashes, data loss, race conditions
- Security vulnerabilities (injection, auth bypass, secrets in code, unsafe deserialization)
- Reliability failures (unhandled errors in critical paths, missing retries where required)
- Missing test coverage for a behavior change in a tested codebase

**Non-Blocking** (performance | maintainability):
- Performance concerns that do not affect correctness
- Code that works but will be hard to extend, test, or debug

**Questions for Author:**
- Ambiguities where intent is genuinely unclear — frame as a question, not a criticism
- Example: "Is the `None` return here intentional, or should this raise?" not "This should raise."

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
is appropriately scoped or should be split. If additional findings were omitted beyond
the cap, state the count here.>

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
<Bullet list of genuine ambiguities. Each is a question, not a criticism.>
<Omit section if no questions.>

---

### Style Notes
<Grouped, not itemized. One short paragraph per concern.>
<Omit section if STYLE=false or no style findings.>
```

---

## Notes

- "Why it matters here" is the hardest part and the most important. Do not substitute abstract
  principles ("this violates DRY") for contextual consequences ("this means the two copies of
  this logic can diverge silently").
- If the PR description is empty and the diff is ambiguous, include a Question asking the author
  to describe the intent — a missing description is itself a review finding.
- Do not comment on lines that a linter or formatter would catch if STYLE=false. Automation
  handles those; your job is everything automation misses.
- The cap (5–7 blocking, 5 non-blocking) is a signal discipline, not a quality compromise.
  A review with 20 findings gets ignored. A review with 5 targeted findings gets acted on.
