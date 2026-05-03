---
name: nightly-research
description: Research pending topics from the queue overnight, write structured markdown notes, and update the queue with completion status.
schedule: "0 8 * * *"
---

Research pending topics from the queue file. Run fully autonomously — never call AskUserQuestion.

**Schedule note:** `0 8 * * *` = 3:00 AM CDT (UTC−5). Update to `0 9 * * *` for CST (UTC−6) in winter.

**Objective:** Process as many pending topics from `research-queue.md` as fit within a 5-hour runtime budget, write one structured markdown note per topic, update the queue to mark completed items (or annotate failed items), and commit everything to the research-notes repo.

---

## Step 0 — Initialize

```bash
RUN_DATE=$(date +%Y-%m-%d)
QUEUE_FILE="C:/Users/brown/Git/research-notes/research-queue.md"
NOTES_DIR="C:/Users/brown/Git/research-notes/notes/${RUN_DATE}"
SCRATCH="C:/Users/brown/.claude/scratch"
mkdir -p "$NOTES_DIR"
START_TS=$(date +%s)
BUDGET_SECS=18000   # 5 hours = 18,000 seconds
```

Verify the queue file exists. If it does not exist: send a push notification — "nightly-research: queue file not found at ${QUEUE_FILE}" — and exit.

---

## Step 1 — Read the queue

Read the full contents of `research-queue.md`. Parse all lines in the **Pending** section (between `## Pending` and `## Done`) that match:

```
^- \[ \] \*\*(.+?)\*\* — (.+)$
```

Group 1 = topic title, Group 2 = guiding question. Stop parsing at `## Done`.

If the Pending section contains zero matching items: send a push notification — "nightly-research: queue is empty — nothing to research" — and exit cleanly.

---

## Step 2 — Research loop

For each topic in order, repeat Steps 2a–2e until the budget is exhausted or the list is empty.

**Budget check (before each topic):**

```bash
NOW=$(date +%s)
REMAINING=$(( BUDGET_SECS - (NOW - START_TS) ))
```

If `REMAINING < 600` (less than 10 minutes left): stop the loop and go to Step 3. Do not start a new topic that cannot be completed.

---

### Step 2a — Web research

Use `WebSearch` and `WebFetch` directly in the main agent (Sonnet). No subagent spawns. No approval gate.

1. Run **2–3 `WebSearch` queries** derived from the guiding question, varying the framing:
   - Precise technical (e.g., `"scaled dot-product attention mechanism transformer"`)
   - Question-form (e.g., `"why does attention outperform RNN for sequence modeling"`)
   - Source-seeking (e.g., `"attention is all you need paper summary"`)

2. For each promising result URL, run `WebFetch` to retrieve the content. Fetch **at most 4 URLs** per topic. Prioritize: arxiv.org, official documentation, named-practitioner engineering blogs, university pages.

3. Identify **1–3 primary sources**. A source qualifies if:
   - It has a named author or authoritative organization
   - It is not Wikipedia, Reddit, or an anonymous blog
   - It directly addresses the guiding question
   - Its URL was confirmed by a successful `WebFetch` (no hallucinated citations)

4. If **zero confirmed primary sources** are found: mark the topic as FAILED (Step 2e). Do not write a note. Proceed to the next topic.

---

### Step 2b — Synthesize the note

Using the fetched content and source metadata, compose the note using this template:

```markdown
---
title: "<TOPIC TITLE>"
date: <RUN_DATE>
tags: [research, <1-2 inferred topic tags>]
guiding-question: "<GUIDING QUESTION>"
sources: <N>
status: complete
---

# <TOPIC TITLE>

**Guiding question:** <GUIDING QUESTION>
**Researched:** <RUN_DATE> | **Sources found:** <N>

---

## Summary

<3–5 sentences. Direct answer to the guiding question. No hedging.>

---

## Key Concepts

- **<Concept>:** 1–2 sentence explanation.

---

## Primary Sources

1. [Title](URL) — Author/Org, Year. One sentence on why it answers the guiding question.

---

## Open Questions

- <Unresolved sub-questions surfaced during research. Omit section if none.>

---

## Related Topics

<Comma-separated list of related topics, freeform.>
```

Quality rules:
- The Summary must **directly answer the guiding question** — not merely restate the topic
- Every source URL must have been fetched (no hallucinated citations)
- Prefer depth on 1–3 sources over breadth across many shallow ones

---

### Step 2c — Write the note

Generate the slug from the title:
- Lowercase
- Replace spaces with hyphens
- Strip characters that are not alphanumeric or hyphens
- Truncate to 60 characters

Example: `"B-Tree vs LSM-Tree Storage Engines"` → `b-tree-vs-lsm-tree-storage-engines`

Write the note to `${NOTES_DIR}/<slug>.md`. If a file at that path already exists (same topic run twice on the same day), append `-2` to the slug.

---

### Step 2d — Record as COMPLETED

Add the topic to an in-memory completed list:

```
completed_topics = [{title: "<TITLE>", date: "<RUN_DATE>"}, ...]
```

---

### Step 2e — Record as FAILED (no sources found)

Add the topic to an in-memory failed list:

```
failed_topics = [{title: "<TITLE>", date: "<RUN_DATE>"}, ...]
```

The topic will be annotated in the queue at Step 3 (kept in Pending for manual review) rather than moved to Done.

---

## Step 3 — Update the queue file

Write a Node.js update script to scratch and execute it. (`jq` is not available in this environment — use `node -e`.)

```bash
UPDATE_SCRIPT="${SCRATCH}/nightly-research-update-$$.js"
```

The script must:

1. Read the current `research-queue.md`
2. For each **completed** topic:
   - Find the line: `- [ ] **<title>** — <question>`
   - Replace the entire line with: `- [x] **<title>** — *(researched <date>)*`
   - Remove the line from the Pending section
   - Append it below the `<!-- Format: ... -->` comment in the Done section
3. For each **failed** topic:
   - Find the line: `- [ ] **<title>** — <question>`
   - Add `<!-- attempted <date>, no sources found -->` on the line immediately after it (keep the original line in Pending unchanged)
4. Remove 3+ consecutive blank lines left by deletions in the Pending section
5. Write the updated content back to `research-queue.md`

After execution:
```bash
node "$UPDATE_SCRIPT" && rm -f "$UPDATE_SCRIPT"
```

---

## Step 4 — Commit

```bash
cd C:/Users/brown/Git/research-notes
git add notes/${RUN_DATE}/ research-queue.md
git commit -m "research: ${RUN_DATE} — <N> topic(s): <comma-separated titles>"
```

The commit message includes the count and titles so the git log is scannable without opening files.

If the commit fails: send a push notification — "nightly-research: git commit failed — notes written to ${NOTES_DIR} but not committed" — and exit with a non-zero status.

**Do NOT push to a remote.** This is a local-only repo during the testing phase.

---

## Step 5 — Final report

Send a push notification:

```
nightly-research complete — <N> researched, <M> failed, <K> remain (<RUN_DATE>):
  ✓ Topic A
  ✓ Topic B
  ✗ Topic C (no sources found — kept in Pending)
```

If zero topics were completed: "nightly-research: 0 topics completed on <RUN_DATE> — check scratch logs for errors."

Clean up scratch files created during this run:

```bash
rm -f "${SCRATCH}/nightly-research-"*"-$$."*
```

---

## Constraints

- **Queue file:** `C:/Users/brown/Git/research-notes/research-queue.md`
- **Notes dir:** `C:/Users/brown/Git/research-notes/notes/YYYY-MM-DD/`
- **Scratch dir:** `C:/Users/brown/.claude/scratch/` — all temp files go here; never use `/tmp/`
- **No `jq`** — use `node -e` for all JSON and string manipulation
- **No subagent spawns** — `WebSearch` and `WebFetch` run directly in the main agent
- **No user interaction** — never call `AskUserQuestion`; run fully autonomously
- **No remote push** — local-only repo during testing phase
- **Model:** Sonnet for all steps
- **Platform:** Windows 11, Git Bash
