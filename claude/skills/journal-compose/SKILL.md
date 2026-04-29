---
name: journal-compose
description: Compose the end-of-day engineering journal from today's stub files. Discovers all YYYY-MM-DD_*.stub.md files, sorts and merges them, produces the canonical 11-section document, updates READMEs, commits, and opens the PR. Invoke as /journal-compose [YYYY-MM-DD].
argument-hint: "[YYYY-MM-DD]"
allowed-tools: Read Edit Write Bash Glob Grep Agent
---

You are composing the end-of-day engineering journal from the day's draft file.
Follow every step in order. Do not skip steps.

Supporting files:
- `~/.claude/skills/sources.md` — shared primary source library, organized by topic tag;
  use Grep on this file before spawning any research subagent (see Section 11)

## Step 0 — Session isolation check

Before doing anything else, check the conversation history *prior to the message that
triggered this skill invocation*. Tool calls made during this skill's own execution do not
count. Only pre-invocation user turns and assistant responses are evidence of prior task work.

If any user turns or assistant responses exist in the conversation before the
`/journal-compose` invocation (i.e., other tasks were handled first), stop immediately and respond:

> "Journal composition must run in a dedicated session with no prior task work.
> Open a fresh Claude Code session and invoke `/journal-compose` there."

Do not proceed to Step 1.

## Step 0.5 — Plan-then-optimize (required)

Before reading any file or spawning any agent, write out:
1. The steps you are about to execute (numbered)
2. Which file reads can be skipped entirely (e.g., if manifest data is sufficient, skip the stub read)
3. Which agent spawns will be batched in a single message with `run_in_background: true`

Do not proceed to Step 1 until this plan is written. No tool calls before the revision pass completes.

## Step 0.8 — Validate JSONL files

Before reading any stubs or manifests, run the JSONL validator:

```bash
[ -f "C:/Users/brown/Git/engineering-journal/scripts/validate-jsonl.js" ] || \
  { echo "validate-jsonl.js not found — merge the companion engineering-journal PR first"; exit 1; }
node "C:/Users/brown/Git/engineering-journal/scripts/validate-jsonl.js"
```

- **Exit 0:** All `.jsonl` files under `sessions/` are valid — proceed to Step 1.
- **Exit non-zero:** Stop immediately. Report the offending file(s) and line(s) printed by the validator. Do not proceed with composition until the errors are resolved — a malformed manifest line can cause sessions to be silently misread or omitted from the composed journal.

---

## Step 1 — Locate stubs and acquire compose lock

**Determine the date:**

If `$ARGUMENTS` is provided and matches `YYYY-MM-DD`, use it as the date. Otherwise, detect
from the current branch:
```bash
git -C C:/Users/brown/Git/engineering-journal branch --show-current
```
The branch name is `draft/YYYY-MM-DD`. Extract `YYYY-MM-DD` from it.

**Check for a manifest (fast path):**

```bash
ls C:/Users/brown/Git/engineering-journal/sessions/*/YYYY-MM-DD.manifest.jsonl 2>/dev/null
```

If one or more manifests exist, read them to get a session overview before touching stubs:
- Number of sessions per project (manifest line count)
- Topics (for slug synthesis and day structure)
- Token data per session (supplemental for Step 4 — JSONL log is still authoritative)

If the manifest count differs from the stub glob count below, treat stubs as authoritative.

**Check for open-PR context:**

```bash
ls C:/Users/brown/Git/engineering-journal/sessions/*/open-prs.jsonl 2>/dev/null
```

If found, read each file and record the open-PR list as `OPEN_PRS`. This is used in Step 5
to group sessions that span multiple days under the same PR. For each entry:
- If the PR's `prs_closed` appears in today's manifest, the PR was opened in a previous session
  (possibly a previous day). The `stub` field identifies the opening session for cross-referencing.
- If the PR has no `prs_closed` in today's manifest, it is still open — do not group anything
  for it; the file carries forward unchanged to the next day.

**Find stub files:**

```bash
ls C:/Users/brown/Git/engineering-journal/sessions/*/YYYY-MM-DD_*.stub.md 2>/dev/null | sort
```

If no stubs are found, fall back to a legacy draft:
```bash
find C:/Users/brown/Git/engineering-journal/sessions -name "YYYY-MM-DD_draft.md"
```
If a legacy draft is found, use it as a monolithic draft (skip the lock step below and proceed
as in the old single-file workflow — read it once in Step 2).

If stubs span multiple project directories (e.g., both `sessions/lifting-logbook/` and
`sessions/meta/`), use **Multi-project mode** (see section below) — do NOT compose projects
sequentially in this session. Proceed directly to that section instead of Step 2.

**Acquire the compose lock:**

Check for a lock at `sessions/<project>/.draft-compose.lock`:
```bash
LOCK="C:/Users/brown/Git/engineering-journal/sessions/<project>/.draft-compose.lock"
if [ -f "$LOCK" ]; then
  LOCK_TIME=$(cat "$LOCK")
  LOCK_EPOCH=$(date -d "$LOCK_TIME" +%s 2>/dev/null || echo 0)
  NOW_EPOCH=$(date +%s)
  AGE=$(( NOW_EPOCH - LOCK_EPOCH ))
  if [ "$AGE" -lt 600 ]; then
    echo "LOCK_ACTIVE=true AGE=$AGE"
  else
    echo "LOCK_STALE=true AGE=$AGE"
  fi
fi
```

- **`LOCK_ACTIVE`:** Abort. Tell the user: "Compose is already running (lock age: ${AGE}s). If
  this is stale, delete `sessions/<project>/.draft-compose.lock` and retry."
- **`LOCK_STALE`:** Warn the user ("Stale compose lock (${AGE}s old) — overriding."), then continue.
- **No lock:** Continue.

Create the lock:
```bash
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > \
  "C:/Users/brown/Git/engineering-journal/sessions/<project>/.draft-compose.lock"
```

Tell the user: "Composing journal from N stub(s): `<stub1>`, `<stub2>`, ..."

**Note for retries after a crash:** If compose was interrupted and you are re-running it, first
delete the lock file manually (`rm sessions/<project>/.draft-compose.lock`) and verify that no
partial output file (e.g., `YYYY-MM-DD-<slug>.md`) was written before restarting from Step 1.

## Multi-project mode

Use this when Step 1 finds stubs in more than one project directory.

Running projects sequentially inflates context with each pass — by project 3 you are paying
for projects 1 and 2 sitting idle in the window. Instead, spawn one isolated Haiku subagent
per project in parallel (Steps 2–6), then run the shared git work once in this session (Steps 7–11).

### Phase 1 — Parallel compose (one Haiku subagent per project)

For each project directory, spawn an Agent with `model: "haiku"`. Send all spawns in a
single message so they run concurrently. Use this prompt template per subagent (substitute
the bracketed values):

---

```
You are composing the engineering journal for one project. Follow these steps exactly.

**Date:** YYYY-MM-DD
**Project path:** sessions/<project>/
**Stub files (in order):** <stub1>, <stub2>, ...

<!-- mirrors Step 0.5 in main flow — keep in sync; intentional differences: item 2 scoped to skip/read (not grep), item 3 broader (all parallel tool calls, not only agent spawns) -->
Step 0.5 — Plan-then-optimize (required). Before any tool call, write out:
  1. The steps you will execute (numbered)
  2. Which reads can be skipped entirely (e.g., if manifest data is sufficient, skip the stub read)
  3. Confirm no sequential tool calls exist that could run in parallel
  Do not proceed until this plan is written.

Step 1 — Acquire compose lock for this project using this project-scoped path:
  C:/Users/brown/Git/engineering-journal/sessions/<project>/.draft-compose.lock
  Follow the lock check/create procedure in SKILL.md Step 1 ("Acquire the compose lock").

Step 1b — If a manifest exists at `sessions/<project>/YYYY-MM-DD.manifest.jsonl`, read it
  for a session overview (topics, token data) before reading individual stubs.

Step 2 — Read stubs following SKILL.md Step 2 extraction format.

Step 2b — Meta trigger check. Do NOT prompt the user, ask questions, or create any meta
  draft files — the Phase 2 coordinator handles user interaction. Instead, scan each session
  block against the trigger table in SKILL.md Step 2b and record any matches for the final
  report. Proceed immediately to Step 3.

Step 3 — Determine the slug. If unclear, synthesize from the session H2 headings; do not
  ask the user. Report your chosen slug.

Step 4 — Fetch real token data. Sanitize the project name first (slashes → underscores):
  PROJECT_SAFE=$(echo "<project>" | tr '/' '_')
  python3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD --format json > \
    "C:/Users/brown/.claude/scratch/tmp_tokens_${PROJECT_SAFE}_$$.json"
  then parse as shown in SKILL.md Step 4.

Step 5 — Compose the 11-section document. Follow SKILL.md Step 5 exactly.

Step 6 — Write the output file to:
  C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD-<slug>.md

Do NOT do Steps 7–11 (no README edits, no git add/commit/push, no PR).

When done, report exactly this structure:
  OUTPUT_FILE=<absolute path>
  SLUG=<slug>
  META_TRIGGERS=<none | comma-separated list of trigger types found>
  STATUS=done
```

---

### Phase 2 — Serial coordinator (this session)

After all subagents complete, collect `OUTPUT_FILE`, `SLUG`, and `META_TRIGGERS` from each.

**Error check first:** If any subagent did not return `STATUS=done`, stop immediately and
report which project(s) failed before touching any README or running git commands. Do not
proceed with a partial set — a missing output file will cause the commit to fail silently.

If all subagents returned `STATUS=done`, check meta triggers.
If any subagent reported `META_TRIGGERS` (non-none), present them to the user now:
```
Meta triggers found in <project> session(s): <list>
Should I open a meta draft block? (y/n)
```
Handle the response as described in Step 2b before continuing.

Then for each project in sequence:
- **Step 7** — Update `sessions/<project>/README.md`
- **Step 8** — Update the top-level `README.md` (one pass covering all projects)
- **Step 9** — Delete stubs and release lock for this project

Finally, do one combined commit and PR (**Steps 10–11**) that stages all projects' files:
```bash
# Stage all composed files and README updates
git -C C:/Users/brown/Git/engineering-journal add \
  sessions/project-a/YYYY-MM-DD-<slug-a>.md \
  sessions/project-b/YYYY-MM-DD-<slug-b>.md \
  ... \
  sessions/project-a/README.md \
  sessions/project-b/README.md \
  README.md
# Stage deleted stubs across all projects
git -C C:/Users/brown/Git/engineering-journal add -u sessions/
git -C C:/Users/brown/Git/engineering-journal commit -m \
  "[docs] Add YYYY-MM-DD journals: <slug-a>, <slug-b>, ..."
git -C C:/Users/brown/Git/engineering-journal push
```

Open one PR covering all projects (Step 11). List each composed journal in the PR body.

After completing Phase 2, skip to the end — do not re-run Steps 2–9 individually.

---

## Step 2 — Read all stubs

Read each stub file in ascending filename order (which equals chronological session order).
Do not re-read stubs after this step.

From the stubs, extract:
- **Date** — the `YYYY-MM-DD` prefix from any stub filename
- **Project path** — the `sessions/<project>/` directory component of the stub paths
- **Opening brief** — from the `<!-- opening-brief -->` block in the **first** stub only.
  If absent, use `"First session for this project — no prior Next Session Context."`
- **Session blocks** — from each stub, in filename order:
  `<!-- session: <slug> -->` … `<!-- tokens: ... -->` … `<!-- next-session-context -->` unit
  - Session slug, H2 heading, body content
  - Token comment (may be absent — note if missing)
  - Next-session-context paragraph
- **Last next-session-context** — the final `<!-- next-session-context -->` paragraph across
  all stubs (used in Section 9)

## Step 2b — Check for meta-relevant content (project drafts only)

Skip this step if the draft path is `sessions/meta/`.

Using the session blocks already extracted in Step 2, scan each block's body text for
content matching the meta journal trigger criteria from `~/.claude/CLAUDE.md`:

| Trigger | What to look for |
|---|---|
| `CLAUDE.md` modified | References to "CLAUDE.md" alongside a change, addition, or update |
| New platform constraint | Platform-specific discovery (Windows, Git Bash, nvm, PATH, temp file paths) paired with a fix or workaround |
| Workflow failure remediated | A hook, script, or process that failed, was diagnosed, and fixed |
| Cross-project convention established | Language like "all repos", "global", "convention", "from now on" |
| `dev-env` PR merged | Any `brownm09/dev-env#N` PR reference |
| Journal structure changed | Changes to journal sections, skill logic, draft format, or token tracking |
| New canonical reference identified | A new external resource, repo, or tool added as a persistent reference |

For each session block, note which triggers (if any) apply and why.

If **no triggers match**, continue to Step 3.

If **one or more triggers match**, present the findings to the user before continuing:

```
The following session blocks contain content that may belong in sessions/meta/:

- Session N (`<slug>`): <trigger type> — <one-line reason>

Should I open a meta draft block for sessions/meta/YYYY-MM-DD_draft.md now?
Enter: y (append meta block and continue), n (skip meta, continue composing), or describe what to do.
```

If the user confirms (`y` or equivalent):
1. Check whether `sessions/meta/YYYY-MM-DD_draft.md` exists on the current branch.
   - If not: create it with `<!-- draft: YYYY-MM-DD -->\nOpening brief: Meta entries from project session — see source journal.\n`
2. Append one `<!-- session: <meta-slug> -->` block per matched trigger, summarizing the
   meta-relevant content. Use a slug like `platform-constraint-<topic>` or `dev-env-pr-N`.
3. Add `<!-- tokens: input=0 output=0 cost≈$0.00 -->` and a `<!-- next-session-context -->`
   paragraph at the end of each block.
4. `git add`, `git commit -m "draft: YYYY-MM-DD meta — <topic>"`, `git push`.
5. Resume at Step 3.

If the user declines (`n` or equivalent), continue to Step 3 without creating a meta entry.

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

**PR grouping (before writing any H2s):** Scan the manifest(s) for `prs_opened` and
`prs_closed` fields, and check `OPEN_PRS` (from Step 1). For any PR number that either:
- appears in both `prs_opened` and `prs_closed` in today's manifests (same-day lifecycle), or
- appears in `OPEN_PRS` and also in `prs_closed` in today's manifests (cross-day lifecycle),

group all related session blocks under one `## Session N — <PR topic>` H2 instead of
producing a separate H2 per stub. Order: opening session content first, any iteration sessions
next, closing session content last. Annotate the end of the section with "→ merged in session N"
where N is the 1-based ordinal of the closing stub for the day. For cross-day PRs, the opening
stub is identified by the `stub` field in `OPEN_PRS` — note the original session date inline:
"(opened YYYY-MM-DD — see [stub-filename])".

Any stub that has neither `prs_opened` nor `prs_closed` set for a grouped PR but was written
on a day when that PR was open in `OPEN_PRS` should also be merged into that H2.

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

## Step 9 — Delete stub files and release lock

Delete all stubs for the date and release the compose lock:
```bash
rm C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md
rm -f C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD.manifest.jsonl
rm -f C:/Users/brown/Git/engineering-journal/sessions/<project>/.draft-compose.lock
```

For legacy single-file compose, delete the draft file instead:
```bash
rm C:/Users/brown/Git/engineering-journal/<draft-file-path>
```

Tell the user: "Draft artifacts deleted."

**Lock file hygiene:** `.draft-compose.lock` is ephemeral and must never be committed. If it
appears in `git status` as an untracked file during compose, that is expected — `git add -u`
(Step 10) will not stage it because it has never been committed. Do not run `git add .` or
`git add sessions/<project>/` (without `-u`) — that would stage the lock file.

## Step 10 — Commit

```bash
git -C C:/Users/brown/Git/engineering-journal add sessions/<project>/YYYY-MM-DD-<slug>.md
git -C C:/Users/brown/Git/engineering-journal add sessions/<project>/README.md
git -C C:/Users/brown/Git/engineering-journal add README.md
# Stage deleted stubs (and any other modifications/deletions in sessions/<project>/)
git -C C:/Users/brown/Git/engineering-journal add -u sessions/<project>/
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

**Auto-merge the PR immediately after creation:**

```bash
gh pr merge <PR-URL> \
  --repo brownm09/engineering-journal \
  --squash \
  --delete-branch
```

This squash-merges the draft branch and deletes the remote branch in one step, preventing
the stale-branch hook from false-positive firing. Wait for the merge to complete, then
delete the local draft branch:

```bash
git -C C:/Users/brown/Git/engineering-journal branch -D draft/YYYY-MM-DD 2>/dev/null || true
```

Tell the user: "Merged: <PR-URL>. Journal published."
