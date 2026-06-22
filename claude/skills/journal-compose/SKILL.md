---
name: journal-compose
description: Compose the end-of-day engineering journal from today's stub files. Discovers all YYYY-MM-DD_*.stub.md files, sorts and merges them, produces the canonical 11-section document, updates READMEs, commits, and opens the PR. Invoke as /journal-compose [YYYY-MM-DD] [--force].
argument-hint: "[YYYY-MM-DD] [--force]"
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

**Parse `$ARGUMENTS`:**

- If `$ARGUMENTS` contains `--force`, set `FORCE=true` and strip it before further parsing.
  Otherwise set `FORCE=false`.
- If the remaining `$ARGUMENTS` matches `YYYY-MM-DD`, use it as the date.
- Otherwise, detect the date from the current branch:
  ```bash
  git -C C:/Users/brown/Git/engineering-journal branch --show-current
  ```
  The branch name is `draft/YYYY-MM-DD`. Extract `YYYY-MM-DD` from it.

**Guard — refuse to compose today's branch:**

Compare the resolved date to today's date:

```bash
TODAY=$(date +%Y-%m-%d)
```

If the date equals `$TODAY` **and** `FORCE` is false, stop immediately and respond:

> "`/journal-compose` targets completed days only. `draft/YYYY-MM-DD` is **today's** branch —
> stubs may still be written during later sessions today.  
> To compose today's journal intentionally (all stubs written, end of day):
> `/journal-compose --force`"

Do **not** proceed to stub discovery, manifest reading, lock acquisition, or any further step.

If the date equals `$TODAY` and `FORCE` is true, proceed — intentional same-day compose.

If the date is not today, proceed normally (regardless of `FORCE`).

**Check for manifests (fast path):**

Per [ADR-055](https://github.com/brownm09/dev-env/blob/main/docs/adr/055-per-session-sharding-journal-companion-files.md),
each session writes its own manifest shard `YYYY-MM-DD_HHMMSS.manifest.jsonl` (a single JSON object,
paired 1:1 with its stub). A day composed before ADR-055 may instead have one legacy per-day
`YYYY-MM-DD.manifest.jsonl` (one line per session). Read **both** and union the entries, deduped by the
`stub` field:

```bash
# per-session manifest shards (current format — one object per file)
ls C:/Users/brown/Git/engineering-journal/sessions/*/YYYY-MM-DD_*.manifest.jsonl 2>/dev/null
# legacy per-day manifest (pre-ADR-055; one line per session; may be absent)
ls C:/Users/brown/Git/engineering-journal/sessions/*/YYYY-MM-DD.manifest.jsonl 2>/dev/null
```

The two globs are disjoint (the shard glob requires the `_HHMMSS` underscore; the legacy name has none).
If any manifests exist, read them to get a session overview before touching stubs:
- Number of sessions per project (shard count + legacy line count, deduped by `stub`)
- Topics (for slug synthesis and day structure)
- Token data per session (supplemental for Step 4 — JSONL log is still authoritative)

If the manifest entry count (shards + any legacy lines, deduped by `stub`) differs from the stub glob
count below, treat stubs as authoritative.

**Check for open-PR context:**

Per [ADR-055](https://github.com/brownm09/dev-env/blob/main/docs/adr/055-per-session-sharding-journal-companion-files.md),
open PRs are tracked as per-PR shards `sessions/<project>/open-prs/<N>.json` (one object per open PR). A
pre-ADR-055 day may instead carry a single legacy `sessions/<project>/open-prs.jsonl`. Read **both**:

```bash
# per-PR shards (current format — one object per file)
ls C:/Users/brown/Git/engineering-journal/sessions/*/open-prs/*.json 2>/dev/null
# legacy single file (pre-ADR-055; one line per open PR; may be absent)
ls C:/Users/brown/Git/engineering-journal/sessions/*/open-prs.jsonl 2>/dev/null
```

If found, read each shard / line and record the union (deduped by `pr`) as `OPEN_PRS`. This is used in
Step 5 to group sessions that span multiple days under the same PR. For each entry:
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

Step 1b — Read this day's manifest for a session overview (topics, token data) before reading
  individual stubs. Per ADR-055 each session has its own shard `YYYY-MM-DD_HHMMSS.manifest.jsonl`
  (one object per file); a pre-ADR-055 day may instead have a legacy per-day
  `YYYY-MM-DD.manifest.jsonl` (one line per session). Read both globs and union by `stub`.

Step 2 — Read stubs following SKILL.md Step 2 extraction format.

Step 2b — Meta trigger check. Do NOT prompt the user, ask questions, or create any meta
  draft files — the Phase 2 coordinator handles user interaction. Instead, scan each session
  block against the trigger table in SKILL.md Step 2b and record any matches for the final
  report. Proceed immediately to Step 3.

Step 3 — Determine the slug. If unclear, synthesize from the session H2 headings; do not
  ask the user. Report your chosen slug.

Step 4 — Fetch real token data. Sanitize the project name first (slashes → underscores):
  PROJECT_SAFE=$(echo "<project>" | tr '/' '_')
  py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD --format json > \
    "C:/Users/brown/.claude/scratch/tmp_tokens_${PROJECT_SAFE}_$$.json"
  then parse as shown in SKILL.md Step 4.

Step 5 — Compose the 11-section document. Follow SKILL.md Step 5 exactly.
  **Fidelity floor.** Your composed dialogue sections must preserve the technical detail in the source stubs. As a rough heuristic, your composed document's line count should be ≥ 80% of the combined stub line count. A journal that compresses to < 50% of source-stub length is a quality failure — re-read the stubs and expand before writing. Reproduce code blocks, file paths, exact PR/issue numbers, and decision rationale verbatim where the stub provides them. Synthesis is allowed only when stubs duplicate each other; raw compression of unique content is not.

Step 6 — Write the output file to:
  C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD-<slug>.md
  **Canonical path check.** Your output path must begin with `C:/Users/brown/Git/engineering-journal/sessions/<project>/`. If your current working directory is a worktree (path contains `.claude/worktrees/`), DO NOT use the worktree-relative path — use the absolute canonical path. Writing to a worktree path requires a post-hoc file move and breaks the commit flow.

Step 6.5 — Self-check before claiming done. After writing the file, run `wc -l` on it and compare to the combined source stub line count. If the journal is < 50% of source length, the compose is incomplete — expand it before proceeding. Report the ratio in your final status as `LINE_COUNT=<n> SOURCE_LINES=<m> FIDELITY=<n/m>`.

Do NOT do Steps 7–11 (no README edits, no git add/commit/push, no PR).

When done, report exactly this structure:
  OUTPUT_FILE=<absolute path>
  SLUG=<slug>
  META_TRIGGERS=<none | comma-separated list of trigger types found>
  LINE_COUNT=<n>
  SOURCE_LINES=<m>
  FIDELITY=<n/m>
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
py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD --format json > "$TMPFILE"
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
py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD
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

**Fidelity floor.** The composed dialogue sections must preserve the technical detail in the source stubs. As a rough heuristic, the composed document's line count should be ≥ 80% of the combined stub line count. A journal that compresses to < 50% of source-stub length is a quality failure — re-read the stubs and expand before writing. Reproduce code blocks, file paths, exact PR/issue numbers, and decision rationale verbatim where the stub provides them. Synthesis is allowed only when stubs duplicate each other; raw compression of unique content is not.

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
*Run `py -3 ~/.claude/scripts/token-report.py --date YYYY-MM-DD` after session close*
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

- **Canonical path check.** The output path must begin with `C:/Users/brown/Git/engineering-journal/sessions/<project>/`. If the current working directory is a worktree (path contains `.claude/worktrees/`), DO NOT use the worktree-relative path — use the absolute canonical path. Writing to a worktree path requires a post-hoc file move and breaks the commit flow.

## Step 6.5 — Self-check before claiming done

After writing the file, run `wc -l` on it and compare to the combined source stub line count. If the journal is < 50% of source length, the compose is incomplete — expand it before proceeding. Report the ratio in your final status: `LINE_COUNT=<n> SOURCE_LINES=<m> FIDELITY=<n/m>`.

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
section uses a labeled-bullet structure (defined in `engineering-journal/CLAUDE.md` →
`## Top-level README Format`). Entry tables live only in the folder READMEs (already updated
in Step 7).

For the project you just composed:
- Find its `### <Project Name>` section in the top-level README
- Update the bullets to reflect any changes from today's journal (Open Items, Next Session
  Context, PRs opened/merged)
- **Hyperlink every PR and issue reference** — `[#N](https://github.com/owner/repo/pull/N)`
  for PRs, `[#N](https://github.com/owner/repo/issues/N)` for issues
- Omit any bullet whose value is empty — never write placeholder dashes
- Do not add entry rows — the folder README owns the table

Top-level entry format (labeled bullets, no prose block):
```markdown
### <Project Name>

<One-line lead sentence — what the project is; no dates or status.>

**Status:** Archived / Superseded / Complete   ← only for finished projects; omit for active ones
**Recent:** [#N](url) — description, [#M](url) — description   ← omit if no work in last 1–2 sessions
**Open:** [#N](url) — topic; [#M](url) — topic   ← use `None` if explicitly closed out; omit if not applicable
**Next:** One concrete sentence.   ← omit if no clear next step; no dates
**Repo:** [owner/repo](https://github.com/owner/repo)   ← always include
**Journal:** [sessions/<project>/](sessions/<project>/README.md)   ← always include
```

If the project does not yet appear in the README, add a new `### <Project>` section
under `## Projects` using this format.

## Step 8a — Update top-level "Start here" dashboard block

After Step 8, refresh the marker-delimited block at the top of `engineering-journal/README.md`. This block surfaces a freshness stamp and the top 3–5 cross-project priorities, drawn from (a) priorities flagged in today's manifests, (b) PRs currently open across projects, and (c) open issues labeled `start-here` across project repos — top 5 across all sources, deduped by ref. It is rewritten on every compose; it is not hand-edited between composes. See [ADR-032](https://github.com/brownm09/dev-env/blob/main/docs/adr/032-journal-start-here-dashboard.md) for rationale.

**Aggregate the priority list (max 5 entries, deduped by `ref`):**

```bash
TMPFILE="C:/Users/brown/.claude/scratch/tmp_start_here_$$.json"
node -e "
  const fs = require('fs'); const path = require('path');
  const root = 'C:/Users/brown/Git/engineering-journal';
  const date = 'YYYY-MM-DD';   // <-- substitute the compose date
  const items = [];
  const seen = new Set();
  // Normalize refs to a single canonical form so manifest-style 'repo#N' and
  // open-prs-derived 'owner/repo#N' dedup against each other. Convention:
  // strip the owner prefix — manifests are owner-less by convention.
  const normRef = (r) => {
    const s = (r || '').trim();
    if (!s) return '';
    const m = s.match(/^[^\/]+\/([^#]+#\d+)$/);
    return m ? m[1] : s;
  };
  const push = (it) => {
    const norm = normRef(it.ref);
    const key = norm || ('__no_ref_' + items.length);
    if (seen.has(key)) return;
    seen.add(key);
    items.push(it);
  };
  const sessionsDir = path.join(root, 'sessions');
  const projects = fs.readdirSync(sessionsDir).filter(d =>
    fs.statSync(path.join(sessionsDir, d)).isDirectory());
  // Source 1: today's manifests — per-session shards (ADR-055) + legacy per-day file
  for (const proj of projects) {
    const projDir = path.join(sessionsDir, proj);
    const manifestLines = [];
    // per-session shards: <date>_*.manifest.jsonl (one JSON object each)
    let names = [];
    try { names = fs.readdirSync(projDir); } catch (e) { names = []; }
    for (const name of names) {
      if (name.startsWith(date + '_') && name.endsWith('.manifest.jsonl')) {
        try { manifestLines.push(fs.readFileSync(path.join(projDir, name), 'utf8').trim()); } catch (e) {}
      }
    }
    // legacy per-day file: <date>.manifest.jsonl (one object per line)
    const legacy = path.join(projDir, date + '.manifest.jsonl');
    if (fs.existsSync(legacy)) {
      for (const line of fs.readFileSync(legacy,'utf8').split('\n').filter(Boolean)) manifestLines.push(line);
    }
    for (const line of manifestLines) {
      try {
        const m = JSON.parse(line);
        for (const p of (m.priorities || [])) {
          push({ source:'manifest', project:proj, label:p.label, ref:p.ref||'', why:p.why||'' });
        }
      } catch (e) {}
    }
  }
  // Source 2: open PRs across projects — per-PR shards (ADR-055) + legacy file (fills to 5)
  if (items.length < 5) {
    for (const proj of projects) {
      if (items.length >= 5) break;
      const projDir = path.join(sessionsDir, proj);
      const prLines = [];
      // per-PR shards: open-prs/<N>.json (one object each)
      const shardDir = path.join(projDir, 'open-prs');
      if (fs.existsSync(shardDir)) {
        let shardNames = [];
        try { shardNames = fs.readdirSync(shardDir); } catch (e) { shardNames = []; }
        for (const name of shardNames) {
          if (name.endsWith('.json')) {
            try { prLines.push(fs.readFileSync(path.join(shardDir, name), 'utf8').trim()); } catch (e) {}
          }
        }
      }
      // legacy single file (pre-ADR-055)
      const legacy = path.join(projDir, 'open-prs.jsonl');
      if (fs.existsSync(legacy)) {
        for (const line of fs.readFileSync(legacy,'utf8').split('\n').filter(Boolean)) prLines.push(line);
      }
      for (const line of prLines) {
        if (items.length >= 5) break;
        try {
          const o = JSON.parse(line);
          const ref = (o.url||'').replace(/^https:\/\/github.com\//,'').replace(/\/pull\//,'#');
          push({ source:'open-prs', project:proj, label:o.topic||('PR #'+o.pr), ref, why:'open since '+o.opened, url:o.url });
        } catch (e) {}
      }
    }
  }
  // Source 3: open issues labeled 'start-here' across project repos (fills to 5).
  // Project -> repo slug is read from each sessions/<proj>/README.md 'Repo:' line;
  // projects without a parseable Repo: line are skipped.
  if (items.length < 5) {
    const { execSync } = require('child_process');
    for (const proj of projects) {
      if (items.length >= 5) break;
      const readme = path.join(sessionsDir, proj, 'README.md');
      if (!fs.existsSync(readme)) continue;
      const txt = fs.readFileSync(readme, 'utf8');
      const m = txt.match(/Repo:\s*\[([^\]]+)\]\(https:\/\/github\.com\/([^\/]+\/[^\/\)]+)\)/);
      if (!m) continue;
      const slug = m[2].replace(/\.git$/,'');
      let out = '';
      try {
        out = execSync('gh issue list --repo ' + slug + ' --label start-here --state open --json number,title,url --limit 5', { encoding: 'utf8' });
      } catch (e) { continue; }
      let issues = [];
      try { issues = JSON.parse(out); } catch (e) { continue; }
      for (const iss of issues) {
        if (items.length >= 5) break;
        const ref = slug + '#' + iss.number;
        push({ source:'issue', project:proj, label:iss.title, ref, why:'open issue', url:iss.url });
      }
    }
  }
  fs.writeFileSync('$TMPFILE', JSON.stringify(items.slice(0,5), null, 2));
  console.log('PRIORITY_COUNT=' + Math.min(items.length, 5));
"
```

**Render the block:**

Read `$TMPFILE` (an array of `{source, project, label, ref, why, url?}` objects, max 5).
For each entry, render one numbered bullet per the rules below the template.

```
<!-- start-here:begin -->
**Last composed:** YYYY-MM-DD

## Start here

Top priorities across all projects (max 5):

1. **[<ref-or-label>](<url>) — <label>** *(<project>)* — <why>
2. ...

<!-- start-here:end -->
```

If `PRIORITY_COUNT=0`, render the body as `*No flagged priorities and no open PRs.*` (omit the numbered list).

For each item:
- If the source is `open-prs` or `issue`, use `url` directly as the link target.
- Else if `ref` (post-normalization) matches `repo#N` or `owner/repo#N`, render the link as `https://github.com/<owner>/<repo>/pull/N`. Use `brownm09` as the owner when the manifest ref omits it. A GitHub `/pull/N` URL 302s to `/issues/N` when N is an issue, so always emitting `/pull/` is safe.
- If neither a `url` nor a parseable `ref` is available, render the label without a link.
- Always italicize the project name in parentheses after the label.
- Append `— <why>` only if `why` is non-empty.

**Insert or replace in `engineering-journal/README.md`:**

1. Read the current README.
2. If `<!-- start-here:begin -->` and `<!-- start-here:end -->` both exist, replace everything between (and including) the markers with the new block.
3. Otherwise, insert the new block immediately above the first `## Projects` heading, with one blank line before `## Projects`.
4. Use the Edit tool with `replace_all: false` — there is only ever one such block in the file.
5. **Anchor-missing abort:** if neither the marker pair nor a `## Projects` heading is present, do not silently skip. Report `START_HERE_INSERT_FAILED: anchor not found` to the user and continue the compose (Step 9 onward) — the rest of the journal still ships, but the dashboard refresh is flagged so the README structural drift gets diagnosed.

The block lives above any `## Projects` H2 and below the file's top-level `# Engineering Journal` title.

Tell the user: "Start here block refreshed with N item(s)."

Clean up:
```bash
rm -f "$TMPFILE"
```

**Promoting an issue to the dashboard:**

To promote an open issue to the top-level dashboard, apply the `start-here` label in its repo:
```bash
gh issue edit <N> --repo <owner/repo> --add-label start-here
```
The label is auto-created on first use. Remove it when the issue is no longer top-priority.

## Step 9 — Delete stub files and release lock

Delete all stubs and this day's manifest for the date and release the compose lock. Delete the
per-session manifest shards (ADR-055) **and** any legacy per-day manifest. **Do not** delete the
open-PR records (`open-prs.jsonl` or the `open-prs/` shard directory) — they are carried forward to
the next day:
```bash
rm C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.stub.md
# per-session manifest shards (ADR-055)
rm -f C:/Users/brown/Git/engineering-journal/sessions/<project>/YYYY-MM-DD_*.manifest.jsonl
# legacy per-day manifest (pre-ADR-055; harmless if absent)
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

**Before proceeding to Step 11**, run Step 10.5 to check whether the draft branch can be
cleanly merged into main. Do not skip this check — a conflicting draft branch requires a
different PR head.

## Step 10.5 — Detect merge conflict; switch to compose branch if needed

After pushing the draft branch, check whether it can be cleanly merged into `origin/main`.
A draft branch that accumulated many commits (e.g., 50+ commits from iterative sessions)
may have diverged from main via squash-merges, making it unmergeable.

```bash
git -C C:/Users/brown/Git/engineering-journal fetch origin main
MERGE_BASE=$(git -C C:/Users/brown/Git/engineering-journal merge-base HEAD origin/main)
if [ -z "$MERGE_BASE" ]; then
  echo "ERROR: could not compute merge base — inspect manually before opening PR"
  exit 1
fi
CONFLICT_LINES=$(git -C C:/Users/brown/Git/engineering-journal \
  merge-tree "$MERGE_BASE" HEAD origin/main | grep -c "^<<<<<<<" || true)
echo "CONFLICT_LINES=$CONFLICT_LINES"
```

**If `CONFLICT_LINES` is 0** — no conflicts detected; proceed to Step 11 using the
draft branch as the PR head. Set `PR_HEAD=draft/YYYY-MM-DD`.

**If `CONFLICT_LINES` > 0** — conflicts detected. Recover via a clean compose branch:

```bash
# 1. Create a clean branch from origin/main
git -C C:/Users/brown/Git/engineering-journal checkout -b compose/YYYY-MM-DD origin/main

# 2. Cherry-pick only the composed output files from the draft branch.
#    Include the open-PR records if present — today's sessions may have updated them.
#    Per ADR-055 these are per-PR shards under open-prs/; a pre-ADR-055 day uses open-prs.jsonl.
git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- \
  sessions/<project>/YYYY-MM-DD-<slug>.md \
  sessions/<project>/README.md \
  README.md
[ -f "C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs.jsonl" ] && \
  git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- \
    sessions/<project>/open-prs.jsonl
[ -d "C:/Users/brown/Git/engineering-journal/sessions/<project>/open-prs" ] && \
  git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- \
    sessions/<project>/open-prs

# 3. Commit and push
git -C C:/Users/brown/Git/engineering-journal commit -m \
  "[docs] Add YYYY-MM-DD journal: <slug> (compose branch — draft had conflicts)"
git -C C:/Users/brown/Git/engineering-journal push -u origin compose/YYYY-MM-DD

# 4. Delete the remote draft branch so the stale-branch hook does not fire on it
git -C C:/Users/brown/Git/engineering-journal push origin --delete draft/YYYY-MM-DD || true
```

Set `PR_HEAD=compose/YYYY-MM-DD`.

Tell the user: "Draft branch had merge conflicts with main — composed journal pushed to
`compose/YYYY-MM-DD` instead. Remote draft branch deleted. Opening PR from clean branch."

**Multi-project mode:** apply this check once (after the combined `git push` at the end
of Phase 2). If conflicts are detected, run the recovery for all projects' composed files
together on a single `compose/YYYY-MM-DD` branch before opening the combined PR. Example
for two projects `meta` and `lifting-logbook`:

```bash
git -C C:/Users/brown/Git/engineering-journal checkout -b compose/YYYY-MM-DD origin/main
git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- \
  sessions/meta/YYYY-MM-DD-<slug-a>.md \
  sessions/meta/README.md \
  sessions/lifting-logbook/YYYY-MM-DD-<slug-b>.md \
  sessions/lifting-logbook/README.md \
  README.md
# open-PR records per project (conditional) — legacy file and/or ADR-055 shard dir
for proj in meta lifting-logbook; do
  [ -f "C:/Users/brown/Git/engineering-journal/sessions/$proj/open-prs.jsonl" ] && \
    git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- \
      "sessions/$proj/open-prs.jsonl"
  [ -d "C:/Users/brown/Git/engineering-journal/sessions/$proj/open-prs" ] && \
    git -C C:/Users/brown/Git/engineering-journal checkout draft/YYYY-MM-DD -- \
      "sessions/$proj/open-prs"
done
git -C C:/Users/brown/Git/engineering-journal commit -m \
  "[docs] Add YYYY-MM-DD journals: <slug-a>, <slug-b> (compose branch — draft had conflicts)"
git -C C:/Users/brown/Git/engineering-journal push -u origin compose/YYYY-MM-DD
git -C C:/Users/brown/Git/engineering-journal push origin --delete draft/YYYY-MM-DD || true
```

## Step 11 — Open PR

Open the PR immediately using `gh`, using `PR_HEAD` determined in Step 10.5
(`draft/YYYY-MM-DD` if clean, `compose/YYYY-MM-DD` if conflicts were detected).

Before composing the PR body, read `~/.claude/templates/pr-body.md` and use it as the
structural guide. This is a journal PR — use the "Journal PR" pattern from that file.

```bash
gh pr create \
  --repo brownm09/engineering-journal \
  --base main \
  --head <PR_HEAD> \
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

This squash-merges the branch and deletes the remote branch in one step, preventing
the stale-branch hook from false-positive firing. Wait for the merge to complete, then
clean up local branches:

```bash
# Delete whichever branch was used as PR head
git -C C:/Users/brown/Git/engineering-journal branch -D <PR_HEAD> 2>/dev/null || true
# If a compose branch was used, also clean up the draft branch locally
git -C C:/Users/brown/Git/engineering-journal branch -D draft/YYYY-MM-DD 2>/dev/null || true
```

Tell the user: "Merged: <PR-URL>. Journal published."
