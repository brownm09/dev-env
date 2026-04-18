---
name: cover-letter
description: Draft a cover letter for a job application following the full cover letter workflow. Invokes Haiku subagents for fit screening and style self-check. Invoke as /cover-letter [JD text, file path, PDF path, or URL].
argument-hint: "[JD text, file path, PDF path, or URL to job posting]"
allowed-tools: Read Edit Write Bash Glob Grep Agent WebFetch
---

You are drafting a cover letter for Mike Brown's job search, following the canonical workflow defined in `CLAUDE.md`.

Follow every step in order. Do not skip steps.

## Step 0 — Load the job description

The job description is provided in `$ARGUMENTS`. Determine input type and load accordingly:

- **No argument / pasted text:** If `$ARGUMENTS` is empty or looks like inline prose (not a path or URL), use it directly as the JD text. If empty, ask the user to paste the JD.
- **File path (`.md`, `.txt`, `.docx`):** Read the file.
- **PDF path (`.pdf`):** Read the file using the Read tool — it handles PDF extraction.
- **URL:** Fetch the page using WebFetch. Extract the job description text from the page body; discard navigation, footers, and cookie banners.

Store the extracted JD text. If the JD text is under 50 words after extraction, tell the user the source may not have loaded correctly and ask them to paste the JD directly.

## Step 1 — Company log check

Read `C:/Users/brown/Git/job-search/context/company_log.md`.
Check the "Roles Completed" and "Roles Skipped" tables for this company and role.
If already present, stop and tell the user: "This role is already logged as [completed/skipped]."

## Step 2 — Fit Screening (Haiku subagent)

Spawn a **Haiku** subagent with this task:

> You are performing a fit screen for a job application for Mike Brown, targeting Director and Senior Manager of Engineering roles.
>
> Read `C:/Users/brown/Git/job-search/context/session_instructions.md` — specifically the sections: "Fit Screening Protocol", "Technical Stack Reference", "Notable Gaps", "Compensation Floor", and "Auto-Skip Triggers".
>
> Evaluate the following job description against those rules and report:
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

Read `C:/Users/brown/Git/job-search/context/style_rules.md` in full. Internalize all rules before drafting. Do not proceed until read.

## Step 4 — Select model letter

Read `C:/Users/brown/Git/job-search/models/INDEX.md`.
Identify the closest matching model letter for this role type.
Read that model letter file.
Tell the user which model you selected and why.

## Step 5 — Check accomplishments

Read `C:/Users/brown/Git/job-search/context/accomplishments.md`.
Note which accomplishments are relevant to this JD. Only claim what is listed there.

## Step 6 — Draft the letter (Sonnet — this session)

Draft the cover letter body in Markdown. Apply all style rules from Step 3 while drafting:
- No em-dashes (anywhere, no exceptions)
- No banned constructions ("The outcomes were concrete" and all variants)
- Do not claim Mike "led a platform directorate" — use "led platform teams responsible for..." or "led programs within the platform organization"
- Body word ceiling: 400 words (body paragraphs only)
- Output is Markdown only

Use the model letter from Step 4 as a structural reference. Adapt tone and emphasis to the specific JD. Do not copy model letter text verbatim.

## Step 7 — Style self-check (Haiku subagent)

Spawn a **Haiku** subagent with this task:

> You are performing a style self-check on a cover letter draft. Read `C:/Users/brown/Git/job-search/context/style_rules.md` — the "Self-Check Before Presenting" section only. Then check the following letter body for every violation listed in that section. Report each violation with the offending text quoted and the rule it breaks. If no violations, report "PASS".
>
> Letter body:
> <paste full draft here>

Report the subagent's findings to the user.

## Step 8 — Fix violations

Apply every violation the self-check subagent flagged. Re-read each fixed passage to confirm it is clean.

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

The body must be under 400 words (count body paragraphs only — exclude header block, salutation, and sign-off). If over, trim.

Report the final word count to the user.

## Step 10 — Save the letter

Determine the output path:
- If this letter is intended as a new canonical model (a new role type not yet in `models/`), save to `C:/Users/brown/Git/job-search/models/` and update `models/INDEX.md`
- Otherwise, save to `C:/Users/brown/Git/job-search/applications/cover_letters/`

File name format: `MikeBrown_YYYYMMDD__Company__Role__Cover_Letter.md`
Use today's date. Confirm company name and role title with the user if not clear from the JD.

Write the file.

## Step 11 — Log the application

Read `C:/Users/brown/Git/job-search/context/company_log.md`.
Add a row to the "Roles Completed" table with: company name, role title, date, and the file path of the saved letter.
Write the updated file.

## Step 12 — Report to user

Before reporting the file path, compute a path relative to the current working directory so the link resolves correctly in the UI (this matters when the session runs in a git worktree):

```bash
python3 -c "
import os
abs_path = 'ABSOLUTE_SAVED_PATH'
rel = os.path.relpath(abs_path, os.getcwd()).replace('\\\\', '/')
print(rel)
"
```

Use the resulting relative path as the markdown link href.

Tell the user:
- Where the letter was saved (as a clickable markdown link using the relative path computed above)
- Final word count
- Any flags raised during fit screening that are still relevant
- Any open items (e.g., missing salary range, unclear hiring manager name)
