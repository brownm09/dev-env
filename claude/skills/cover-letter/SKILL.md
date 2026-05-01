---
name: cover-letter
description: Draft a cover letter for a job application following the full cover letter workflow. Invokes Haiku subagents for fit screening and (on the Opus path) style self-check. Invoke as /cover-letter [JD text, file path, PDF path, or URL].
argument-hint: "[JD text, file path, PDF path, or URL to job posting]"
allowed-tools: Read Edit Write Bash Glob Grep Agent WebFetch AskUserQuestion
---

You are drafting a cover letter for Mike Brown's job search, following the canonical workflow defined in `CLAUDE.md`.

Follow every step in order. Do not skip steps.

## Step -1 — Model selection

Use AskUserQuestion with:
- Question: "Which model should draft this cover letter?"
- Header: "Draft model"
- Options:
  - "Sonnet (Recommended)" — faster and cheaper; sufficient for most applications; drafts inline (no subagent spawn)
  - "Opus" — better prose quality; higher cost (~5–10× Sonnet); use for high-stakes applications

Store the answer as DRAFT_MODEL: `"sonnet"` if Sonnet was selected, `"opus"` if Opus.

## Step 0 — Load the job description

The job description is provided in `$ARGUMENTS`. Determine input type and load accordingly:

- **No argument / pasted text:** If `$ARGUMENTS` is empty or looks like inline prose (not a path or URL), use it directly as the JD text. If empty, ask the user to paste the JD.
- **File path (`.md`, `.txt`, `.docx`):** Read the file.
- **PDF path (`.pdf`):** Read the file using the Read tool — it handles PDF extraction.
- **URL:** Fetch the page using WebFetch. Extract the job description text from the page body; discard navigation, footers, and cookie banners.

Store the extracted JD text. If the JD text is under 50 words after extraction, tell the user the source may not have loaded correctly and ask them to paste the JD directly.

## Step 1 — Company log check

Read `C:/Users/brown/Git/job-search/context/company_log.md`. Keep this content in context — Step 11 will reuse it without re-reading the file.
Check the "Roles Completed" and "Roles Skipped" tables for this company and role.
If already present, stop and tell the user: "This role is already logged as [completed/skipped]."

## Step 2 — Fit Screening (Haiku subagent)

Read `C:/Users/brown/Git/job-search/context/session_instructions.md`. Extract these three sections verbatim: "Fit Screening Protocol", "Technical Stack Reference" (including all sub-bullets and notes), and the compensation floor from "Job Search Preferences" ($180,000).

Spawn a **Haiku** subagent. Pass the extracted sections inline — do not instruct the subagent to read any files:

> You are performing a fit screen for a job application for Mike Brown, targeting Director and Senior Manager of Engineering roles.
>
> **Fit Screening Protocol:**
> <paste extracted "Fit Screening Protocol" section verbatim>
>
> **Technical Stack Reference:**
> <paste extracted "Technical Stack Reference" section verbatim>
>
> **Compensation floor:** $180,000 — flag any role where the range ceiling is below this.
>
> Evaluate the following job description and report:
> 1. Any auto-skip triggers (list the rule and the JD text that triggered it). If none, write "None."
> 2. Any soft flags (compensation uncertainty, seniority mismatch, stack gaps, industry fit). For each, quote the relevant JD text.
> 3. Overall recommendation: PROCEED, FLAG, or SKIP
>
> Job description:
> <JD text>

If the subagent returns SKIP, stop and report the result to the user. Do not proceed.
If the subagent returns FLAG, surface the flags and ask the user whether to proceed.
If PROCEED, continue.

## Step 3 — Read style rules

**Session cache:** If `style_rules.md` is already in context from a previous letter this session, skip this file read and re-use the content already loaded.

Otherwise: Read `C:/Users/brown/Git/job-search/context/style_rules.md` in full. Internalize all rules before drafting. Do not proceed until read.

## Step 4 — Select model letter

Read `C:/Users/brown/Git/job-search/models/INDEX.md`.
Identify the closest matching model letter for this role type.
Read that model letter file.
Tell the user which model you selected and why.

## Step 5 — Check accomplishments

**Session cache:** If `accomplishments.md` is already in context from a previous letter this session, skip this file read and re-use the content already loaded.

Otherwise: Read `C:/Users/brown/Git/job-search/context/accomplishments.md`.

Note which accomplishments are relevant to this JD. Only claim what is listed there.

## Step 5b — Voice calibration

**Session cache:** If `VOICE_SYNOPSIS.md` is already in context from a previous letter this session, skip this file read and re-use the content already loaded.

Otherwise: Read `C:/Users/brown/Git/job-search/models/voice/VOICE_SYNOPSIS.md`.

Apply the patterns in the synopsis to calibrate opening sentence rhythm, paragraph structure, and declarative confidence. Style rules from Step 3 govern all constraints; voice calibration informs structure and rhythm only.

(The full voice reference files are at `models/voice/` and can be consulted if a specific passage is needed, but the synopsis is sufficient for drafting.)

## Step 5c — Filter accomplishments (Opus path only)

**Skip this step if DRAFT_MODEL is `"sonnet"`.**

From the full accomplishments list in context, identify the 4–8 rows most directly relevant to this JD. Criteria: direct match to the JD's stated technical requirements, team size expectations, industry context, or compliance posture. Exclude rows with no plausible relevance to this specific role.

Store the filtered set as RELEVANT_ACCOMPLISHMENTS.

## Step 6 — Draft the letter

### Sonnet path (DRAFT_MODEL = "sonnet")

Draft the letter body directly — do not spawn a subagent. All context (style rules, model letter, accomplishments, voice synopsis) is already in context from Steps 3–5b.

Apply all style rules from Step 3. Use the model letter from Step 4 as a structural reference. Adapt tone and emphasis to the specific JD. Draw only from accomplishments in Step 5. Calibrate rhythm using the voice synopsis from Step 5b.

Constraints:
- No em-dashes (anywhere, no exceptions)
- No banned constructions ("The outcomes were concrete" and all variants)
- Do not claim Mike "led a platform directorate" — use "led platform teams responsible for..." or "led programs within the platform organization"
- Body word ceiling: 475 words (body paragraphs only)
- Output is Markdown only

After drafting, run a self-check inline — do not spawn a separate subagent. Using the "Cover-Letter-Specific Self-Check" section from `style_rules.md` (already in context from Step 3), check the draft for every violation. Fix any violations before continuing. Report "PASS" or list each fix made.

Collect the final draft as DRAFT. Skip Step 7 and proceed to Step 8.

### Opus path (DRAFT_MODEL = "opus")

Spawn an Agent with `model: "opus"`. Pass all of the following inline — do not
instruct the subagent to read any files:

- All style rules (from Step 3, verbatim)
- The model letter (from Step 4, verbatim)
- RELEVANT_ACCOMPLISHMENTS (from Step 5c — filtered rows only, not the full list)
- The voice synopsis (from Step 5b, verbatim)
- The full JD text (from Step 0)

Subagent task:

> Draft the cover letter body in Markdown. Apply all style rules provided:
> - No em-dashes (anywhere, no exceptions)
> - No banned constructions ("The outcomes were concrete" and all variants)
> - Do not claim Mike "led a platform directorate" — use "led platform teams responsible for..." or "led programs within the platform organization"
> - Body word ceiling: 475 words (body paragraphs only)
> - Output is Markdown only
>
> Use the model letter as a structural reference. Adapt tone and emphasis to the specific JD.
> Do not copy model letter text verbatim.
>
> Return only the letter body — no preamble, no commentary.

Collect the subagent's output as DRAFT.

## Step 7 — Style self-check (Haiku subagent — Opus path only)

**Skip this step if DRAFT_MODEL is `"sonnet"` (self-check was run inline in Step 6).**

The `style_rules.md` file is already in context from Step 3. Extract the "Cover-Letter-Specific Self-Check" section verbatim and pass it inline in the subagent prompt — do not instruct the subagent to read any files.

Spawn a **Haiku** subagent with this task:

> You are performing a style self-check on a cover letter draft. Check the letter body below against every item in the Self-Check list. Report each violation with the offending text quoted and the rule it breaks. If no violations, report "PASS".
>
> **Cover-Letter-Specific Self-Check:**
> <paste the "Cover-Letter-Specific Self-Check" section from style_rules.md verbatim>
>
> **Letter body:**
> <paste full draft here>

Report the subagent's findings to the user.

## Step 8 — Fix violations

Apply every violation flagged in Step 6 (Sonnet) or Step 7 (Opus). Re-read each fixed passage to confirm it is clean.

## Step 9 — Word count

Write the letter body to a temp file and count words:
```bash
TMPFILE="C:/Users/brown/.claude/scratch/wc_$$.txt"
cat > "$TMPFILE" << 'LETTER'
<letter body here>
LETTER
wc -w < "$TMPFILE"
rm -f "$TMPFILE"
```

The body must be under 475 words (count body paragraphs only — exclude header block, salutation, and sign-off). If over, trim.

Report the final word count to the user.

## Step 10 — Save the letter

Determine the output path:
- If this letter is intended as a new canonical model (a new role type not yet in `models/letters/`), save to `C:/Users/brown/Git/job-search/models/letters/` and update `models/INDEX.md`
- Otherwise, save to `C:/Users/brown/Git/job-search/applications/cover_letters/`

File name format: `MikeBrown_YYYYMMDD__Company__Role__Cover_Letter.md`
Use today's date. Confirm company name and role title with the user if not clear from the JD.

Write the file.

## Step 11 — Log the application

The `context/company_log.md` content is already in context from Step 1 — do not re-read the file.
Add a row to the "Roles Completed" table with: company name, role title, date, and the file path of the saved letter.
Write the updated file.

## Step 12 — Report to user

Before reporting the file path, compute a path relative to the current working directory so the link resolves correctly in the UI (this matters when the session runs in a git worktree).

Replace `<abs>` with the absolute path written in Step 10, then run:

```bash
ABS="<abs>"
PYBIN=$(command -v python3 || command -v python)
"$PYBIN" -c "import os; print(os.path.relpath('$ABS', os.getcwd()).replace('\\', '/'))"
```

Use the printed path as the markdown link href — e.g., `[filename](relative/path/filename.md)`.

Tell the user:
- Where the letter was saved (as a clickable markdown link using the relative path computed above)
- Final word count
- Draft model used (Sonnet or Opus)
- Any flags raised during fit screening that are still relevant
- Any open items (e.g., missing salary range, unclear hiring manager name)
