---
name: journal-compose
description: Compose the end-of-day engineering journal from today's draft file. Produces the canonical 11-section document, updates READMEs, commits, and prompts for a PR. Invoke as /journal-compose [draft-file-path].
argument-hint: "[sessions/<project>/YYYY-MM-DD_draft.md]"
disable-model-invocation: true
allowed-tools: Read Edit Write Bash Glob Grep Agent
---

You are composing the end-of-day engineering journal from the day's draft file.
Follow every step in order. Do not skip steps.

Supporting files:
- `~/.claude/skills/sources.md` — shared primary source library, organized by topic tag;
  use Grep on this file before spawning any research subagent (see Section 11)

## Step 1 — Locate the draft file

If `$ARGUMENTS` is provided, use it as the draft file path (relative to the repo root
`C:/Users/brown/Git/engineering-journal`).

If no argument is given, detect from the current branch:
```bash
git -C C:/Users/brown/Git/engineering-journal branch --show-current
```
The branch name is `draft/YYYY-MM-DD`. List draft files on that branch:
```bash
find C:/Users/brown/Git/engineering-journal/sessions -name "*_draft.md"
```
If multiple draft files exist (multiple projects active today), ask the user which one to
compose. If exactly one draft file is found, proceed with it.

Confirm the path and tell the user: "Composing journal from: `<path>`"

## Step 2 — Read the draft file once

Read the entire draft file in a single `Read` call. Do not read it again during composition.
Capture all content in memory for use in all subsequent steps.

From the draft file, extract:
- **Date** — from the `<!-- draft: YYYY-MM-DD -->` comment
- **Project path** — the `sessions/<project>/` directory component of the draft file path
- **Opening brief** — the text after `Opening brief:` at the top, before the first `<!-- session: ... -->`
- **Session blocks** — each `<!-- session: <slug> -->` … `<!-- tokens: ... -->` … `<!-- next-session-context -->` unit
  - Session slug, H2 heading, body content
  - Token comment (may be absent — note if missing)
  - Next-session-context paragraph
- **Last next-session-context** — the final one in the file (used in Section 9)

## Step 3 — Determine the slug

The slug for the final filename comes from the overall day's theme.

Check the draft for a clear unifying theme across session blocks:
- If one session dominated the day, use its slug
- If multiple sessions share a theme, synthesize a slug (e.g., `token-tracker-and-dev-env`)
- If the theme is unclear, ask the user: "What slug should I use for the final filename?
  (e.g., `v02-core-api-sprint`)"

The output filename will be: `sessions/<project>/YYYY-MM-DD-<slug>.md`

Tell the user the proposed filename and confirm before writing.

## Step 4 — Fetch real token data

Token data comes from two sources. Collect both; the JSONL log is authoritative.

**Source A — Real JSONL data (authoritative):**

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_tokens_$$.json"
python3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD --format json > "$TMPFILE"
node -e "
  const d = JSON.parse(require('fs').readFileSync('$TMPFILE','utf8'));
  console.log('SESSION_COUNT=' + d.length);
  d.forEach((s, i) => {
    const t = s.tokens || {};
    console.log('S' + i + '_ID=' + (s.session_id||'').slice(0,8));
    console.log('S' + i + '_BRANCH=' + (s.git_branch||''));
    console.log('S' + i + '_TURNS=' + (s.turn_count||0));
    console.log('S' + i + '_INP=' + (t.input_tokens||0));
    console.log('S' + i + '_OUT=' + (t.output_tokens||0));
    console.log('S' + i + '_CR=' + (t.cache_read_input_tokens||0));
    console.log('S' + i + '_CW=' + (t.cache_creation_input_tokens||0));
    console.log('S' + i + '_COST=' + (s.estimated_cost_usd||0).toFixed(4));
    console.log('S' + i + '_SUB=' + (s.subagent_count||0));
  });
"
rm -f "$TMPFILE"
```

Also run the markdown report for use in the Token Usage section:
```bash
python3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD
```

Note the session count from the JSONL log. **Important:** the current journal-compose
session will not appear in the log yet — the Stop hook fires after this conversation ends.
That session's data will need to be added retroactively if it matters.

**Source B — Draft token comments (supplemental):**

For each `<!-- session: <slug> -->` block in the draft, extract the
`<!-- tokens: ... -->` comment if present. Format variants:
- `<!-- tokens: input=N output=N cost≈$N -->`
- `<!-- tokens: input=N output=N cache_r=N cache_w=N cost≈$N -->`

These values were pulled from the CLI session summary at the time of writing and
should roughly match the JSONL data. Use them to correlate slugs to JSONL session rows.

**Matching JSONL rows to draft slugs:**

Sort JSONL sessions by `first_turn_ts` ascending. Match to draft session blocks in order
(first JSONL session → Session 1 slug, second → Session 2 slug, etc.).

If the JSONL count ≠ draft session block count, note the discrepancy. Possible causes:
- A scratch/exploratory Claude Code session ran that day with no corresponding draft block
- The journal-compose session itself (current) is absent from the log (expected)
- Multiple draft slugs correspond to one CLI session (unlikely but possible)

When counts don't match, use real JSONL data for Combined Totals and note which sessions
couldn't be matched by slug.

## Step 5 — Compose the 11-section document

Compose the complete document in order. Every section is required.

---

### Section 1 — Header block

```
# Session Transcript — YYYY-MM-DD

**Topic:** <one-line summary of the day's work — synthesize from all session H2s>
**Repo / Branch:** <repo and branches worked on, drawn from draft content>
**Issues closed:** <comma-separated linked issues, or "None">
**PRs merged:** <comma-separated linked PRs, or "None">
```

---

### Section 2 — Table of Contents

Generate anchor links for all H2 and H3 sections that follow.
Format: `- [Section Name](#anchor)` where anchor is lowercase, spaces → hyphens, special chars dropped.
Nest H3 entries under their parent H2 with two-space indent.

Always include these entries (in this order):
- Opening Brief
- Key Decisions
- (one entry per session dialogue section, with sub-entries for H3s)
- Open Items / Next Steps
- Token Usage
- Token Optimization Suggestions
- Next Session Context
- Reflection
- Further Reading

---

### Section 3 — Opening Brief

```
## Opening Brief

> *[paste the opening brief from the draft verbatim, or "First session for this project — no prior Next Session Context." if applicable]*
```

---

### Section 4 — Key Decisions

```
## Key Decisions

### Session N

- **<decision title>** — <rationale, one sentence>. ([§N.M](#anchor-to-dialogue-subsection)[, Issue #N](url)])
```

One `### Session N` heading per session block.
Pull decisions from the draft body — look for choices made, tradeoffs resolved, patterns adopted.
Link each decision to its dialogue subsection using the anchor format `#nN--slug`.
Link to issues/PRs where referenced in the draft.

---

### Section 5 — Dialogue sections

One `## Session N — <Title>` per session block, drawn from the draft's H2s.
Use H3s for sub-topics within a session.
Reproduce the draft content faithfully — do not summarize or omit technical detail.
Reformat for readability (code fences, bullets) but preserve meaning.

Label sessions: `## Session 1 — <slug title>`, `## Session 2 — <slug title>`, etc.

---

### Section 6 — Open Items / Next Steps

```
## Open Items / Next Steps

- [ ] <item>
- [ ] <item>
```

Extract from:
1. Any explicit "next steps" listed in draft session blocks
2. The last `<!-- next-session-context -->` block (convert implied next work to checkbox items)
3. Any items flagged as deferred or TODO in the draft

---

### Section 7 — Token Usage

```
## Token Usage
```

**Primary source is the JSONL log fetched in Step 4.** Draft `<!-- tokens: ... -->` comments
are supplemental and used only for slug labeling or when no JSONL data exists.

**7a — Per-session breakdown**

For each JSONL session row (matched to a draft slug where possible):

```
### Session N — <slug> (or <session-id[:8]> if unmatched)

| | Value |
|---|---|
| Model | claude-sonnet-4-6 |
| Input tokens | N |
| Output tokens | N |
| Cache read tokens | N |
| Cache write tokens | N |
| Turns | N (+N subagent turns if applicable) |
| Estimated cost | $N |
```

If a JSONL row cannot be matched to a draft slug, label it with the short session ID
and note: *"No corresponding draft session block — may be a scratch session or the
journal-compose session itself."*

If the current journal-compose session is absent from the log (expected, since Stop hook
hasn't fired yet), add a placeholder:

```
### Session N — journal-compose (current session, pending)

*Token data not yet available — Stop hook fires after this conversation ends.*
*Run `python3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD` after session close*
*to get the actual figures and update this section if needed.*
```

If **no JSONL data exists at all** for this date (e.g., token tracking was not yet active
for this project's sessions), fall back to draft comments and label each table:
*"Source: draft token comment — retroactive estimate, not from JSONL log."*
If a draft comment is also absent, use a retroactive estimate based on session scope:
short session (< 30 min) ≈ 15k input / 3k output; medium (1–2 hours) ≈ 50k / 8k;
long (> 2 hours) ≈ 100k+ / 15k+. Label: *"Retroactive estimate — no JSONL data or draft comment."*

**7b — Raw session table from token-report.py**

Insert the markdown output of `token-report.py --date YYYY-MM-DD` verbatim under a
`#### All sessions (from token-report.py)` sub-heading. This is the authoritative
unmodified record.

**7c — Combined Totals**

Sum across all JSONL rows for the date (excluding the current compose session if absent):

```
### Combined Totals

| Session | Input | Output | Cache R | Cache W | Turns | Cost |
|---|---|---|---|---|---|---|
| 1 — <slug> | N | N | N | N | N | $N |
| 2 — <slug> | N | N | N | N | N | $N |
| *(compose — pending)* | — | — | — | — | — | — |
| **Total** | **N** | **N** | **N** | **N** | **N** | **$N** |
```

Total row excludes any pending/unresolved rows.

---

### Section 8 — Token Optimization Suggestions

```
## Token Optimization Suggestions

### Session N

- <observation about context efficiency, prompt length, or tool call patterns>
- <2–4 observations per session>

### Cross-Session Patterns

- <generalizable findings that apply across sessions>
```

Observations should be specific and actionable. Examples:
- "Draft opened with full file read; a targeted grep would have reduced input by ~20k tokens"
- "Three agent spawns in one turn — could have been batched with one multi-task prompt"
- "Session ran long without a context-reset; splitting at the halfway point would have saved cache misses"

---

### Section 9 — Next Session Context

```
## Next Session Context

<paste the final <!-- next-session-context --> paragraph from the draft verbatim>
```

This section is required in all published journals — project journals and meta entries alike.

---

### Section 10 — Reflection

```
## Reflection

<2–5 bullet points covering: gaps in the work, risks introduced, strategic questions
raised but not resolved, anything surprising or worth revisiting>
```

Write this section last (logically). Pull from the full day's content.

---

### Section 11 — Further Reading

```
## Further Reading
```

1–3 primary sources per session. The goal is sources that explain the *reasoning* behind
key decisions made in that session — not tutorials or summaries. Prefer:
- Named practitioners writing from real enterprise experience
- Official documentation for technology choices
- Peer-reviewed papers or specifications
- Books with free canonical URLs (SRE book, SE@Google, etc.)

**Do this in two passes:**

**Pass 1 — Grep the source library (zero token cost):**

For each session, extract the 2–4 most significant decision keywords from the Key Decisions
section (e.g., "hexagonal architecture", "testcontainers", "monorepo", "ADR", "REST").

Grep `~/.claude/skills/sources.md` for each keyword:
```bash
grep -i "<keyword>" "~/.claude/skills/sources.md" -A 2
```

A match in the tags line of a section means that section contains relevant sources.
Read the matched entries. Select the 1–3 most directly applicable to the decisions made.
Do not cite a source just because the keyword matched — read the one-sentence relevance
note and confirm it fits.

**Pass 2 — Spawn a research subagent only if Pass 1 yields fewer than 1 source for a session:**

Use the Agent tool to spawn a general-purpose subagent with this task:

> Find 1–2 primary sources (no summaries, no blog posts without named authors) that a
> senior engineer at a company like Stripe, Netflix, Google, or Uber would cite when
> making this decision: "<decision description from Key Decisions>".
> Prefer: named-practitioner engineering blog posts, peer-reviewed papers, official specs,
> or free book chapters. Return title, author/org, URL, and one sentence on relevance.
> Do not fabricate URLs — only return sources you can verify exist.

The subagent runs in isolation so its research does not expand this session's context.
Use its output to supplement the source library entry if the source is high-quality;
add it as a new entry to `~/.claude/skills/sources.md` under the appropriate tag
section for future use (this grows the library over time without extra effort).

**Output format per session:**

```
### Session N — <slug>

- [<Title>](<URL>) — <one sentence: what this source explains and why it matters for the
  specific decision made in this session>. *(<Author/Org>, <year if known>)*
```

If a session had no externally-referenceable decisions (pure implementation/tooling work
with no architectural choices), write:
```
### Session N — <slug>

*No primary sources — session was implementation work with no architectural decisions.*
```

Do not pad with tangentially related sources to hit a count. One precise citation
is better than three loose ones.

---

## Step 6 — Write the output file

Write the composed document to:
```
C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD-<slug>.md
```

## Step 7 — Update the folder README

Check whether `sessions/<project>/README.md` exists.

**If it does not exist**, create it with this structure:
```markdown
# <Project Name> — Journal

Session transcripts for the `<project>` project.

## Progress Summary

<2–3 sentence narrative: what the project is, what phase it is currently in,
and what the most recent session accomplished or decided. Write from the perspective
of someone opening this folder for the first time.>

**Where to start next session:** <paste the Next Session Context from the journal
you just wrote, so the folder README always points to the current thread.>

## Entries

| Date | Session | Topics |
|---|---|---|
| YYYY-MM-DD | [<slug title>](<filename>.md) | <comma-separated topic keywords> |
```

**If it already exists**, read it and append a new row to the Entries table and
update the Progress Summary and "Where to start next session" sections to reflect
today's journal.

## Step 8 — Update the top-level README

Read `C:/Users/brown/Git/engineering-journal/README.md`.

The top-level README uses a hub-and-spoke layout: **no inline entry tables**. Each project
section has a 2–3 sentence description, current state, and a link to its folder README.
Entry tables live only in the folder READMEs (already updated in Step 7).

For the project you just composed:
- Find its `### <Project Name>` section in the top-level README
- Update the description to reflect the current milestone, next step, and any open blockers
  that changed today (draw from the journal's Open Items and Next Session Context sections)
- Do not add entry rows — the folder README owns the table

Top-level entry format (description + link only, no table):
```markdown
### <Project Name>

<2–3 sentences: what the project is, current milestone, next step, open blockers if any.>

**Repository:** [brownm09/<repo>](https://github.com/brownm09/<repo>)
**Journal:** [sessions/<project>/](sessions/<project>/README.md)
```

If the project does not yet appear in the README, add a new `### <Project>` section
under `## Projects` using this format.

## Step 9 — Delete the draft file

Delete the draft file:
```bash
rm C:/Users/brown/Git/engineering-journal/<draft-file-path>
```

Tell the user: "Draft file deleted."

## Step 10 — Commit

```bash
git -C C:/Users/brown/Git/engineering-journal add sessions/<project>/YYYY-MM-DD-<slug>.md
git -C C:/Users/brown/Git/engineering-journal add sessions/<project>/README.md
git -C C:/Users/brown/Git/engineering-journal add README.md
git -C C:/Users/brown/Git/engineering-journal commit -m "[docs] Add YYYY-MM-DD journal: <slug>"
git -C C:/Users/brown/Git/engineering-journal push
```

## Step 11 — Open PR

Open the PR immediately using `gh`.

Before composing the PR body, read `~/.claude/templates/pr-body.md` and use it as the
structural guide. This is a journal PR — use the "Journal PR" pattern from that file.

```bash
gh pr create \
  --repo brownm09/engineering-journal \
  --base main \
  --title "YYYY-MM-DD: <slug>" \
  --body "$(cat <<'EOF'
End-of-day journal: <one-line topic summary>.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL to the user. After the PR is merged (squash merge), delete the
`draft/YYYY-MM-DD` branch.
