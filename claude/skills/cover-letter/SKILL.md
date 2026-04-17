---
name: cover-letter
description: Draft a cover letter for a job application following the full 10-step job-search workflow. Invokes Haiku subagents for fit screening and style self-check. Invoke as /cover-letter [job-description-text or file path].
argument-hint: "[JD text or path to JD file]"
allowed-tools: Read Edit Write Bash Glob Grep Agent
---

You are drafting a cover letter for Mike Brown's job search, following the canonical workflow defined in `CLAUDE.md`.

Follow every step in order. Do not skip steps.

The job description is provided in `$ARGUMENTS`. If it is a file path, read it. If it is inline text, use it directly. If neither is provided, ask the user for the JD before proceeding.

## Step 1 — Fit Screening (Haiku subagent)

Spawn a **Haiku** subagent with this task:

> You are performing a fit screen for a job application. Read `C:/Users/brown/Git/job-search/context/session_instructions.md` — specifically the "Fit Screening Protocol", "Technical Stack Reference", and "Notable Gaps" sections. Then evaluate this job description against those rules and report:
> 1. Any auto-skip triggers (list the rule and the JD text that triggered it)
> 2. Any soft flags (compensation, seniority, stack mismatches)
> 3. Overall recommendation: PROCEED, FLAG, or SKIP
>
> Job description:
> <paste JD here>

If the subagent returns SKIP, stop and report the result to the user. Do not proceed.
If the subagent returns FLAG, surface the flags and ask the user whether to proceed.
If PROCEED, continue.

## Step 2 — Company log check

Read `C:/Users/brown/Git/job-search/context/company_log.md`.
Check the "Roles Completed" and "Roles Skipped" tables for the company and role.
If already present, stop and tell the user: "This role is already logged as [completed/skipped]."

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
- Body word ceiling: 470 words
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

Run:
```bash
echo "<letter body>" | wc -w
```
Or count manually. The body must be under 470 words (subtract approximately 30–35 words for header, salutation, and sign-off). If over, trim.

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

Tell the user:
- Where the letter was saved
- Final word count
- Any flags raised during fit screening that are still relevant
- Any open items (e.g., missing salary range, unclear hiring manager name)
