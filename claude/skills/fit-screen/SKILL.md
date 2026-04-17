---
name: fit-screen
description: Run fit screening on a job description for Mike Brown's job search. Uses a Haiku subagent to check auto-skip triggers, soft flags, and compensation floor. Returns PROCEED, FLAG, or SKIP. Invoke as /fit-screen [JD text, file path, PDF path, or URL].
argument-hint: "[JD text, file path, PDF path, or URL to job posting]"
allowed-tools: Read Bash Agent WebFetch
---

You are running a fit screen for Mike Brown's job search.

## Step 0 — Load the job description

The job description is provided in `$ARGUMENTS`. Determine input type and load accordingly:

- **No argument / pasted text:** If `$ARGUMENTS` is empty or looks like inline prose (not a path or URL), use it directly as the JD text. If empty, ask the user to paste the JD.
- **File path (`.md`, `.txt`, `.docx`):** Read the file.
- **PDF path (`.pdf`):** Read the file using the Read tool — it handles PDF extraction.
- **URL:** Fetch the page using WebFetch. Extract the job description text from the page body; discard navigation, footers, and cookie banners.

Store the extracted JD text. If the JD text is under 50 words after extraction, tell the user the source may not have loaded correctly and ask them to paste the JD directly.

## Step 1 — Spawn fit screening subagent

Spawn a single **Haiku** subagent with this task:

> You are performing a fit screen for a job application for Mike Brown, targeting Director and Senior Manager of Engineering roles.
>
> Read `C:/Users/brown/Git/job-search/context/session_instructions.md` — specifically the sections: "Fit Screening Protocol", "Technical Stack Reference", "Notable Gaps", "Compensation Floor", and "Auto-Skip Triggers".
>
> Evaluate the following job description against those rules and produce a structured report:
>
> **Section 1 — Auto-Skip Triggers**
> List each triggered rule with the exact JD text that triggered it. If none triggered, write "None."
>
> **Section 2 — Soft Flags**
> List any concerns that do not auto-skip but warrant attention: compensation uncertainty, seniority mismatch, stack gaps, industry fit issues. For each, quote the relevant JD text and explain the concern briefly.
>
> **Section 3 — Fit Summary**
> 2–3 sentences summarizing how well this role matches Mike's target profile.
>
> **Section 4 — Recommendation**
> One of: PROCEED / FLAG / SKIP
> - PROCEED: No blockers, role is a strong fit
> - FLAG: Soft concerns worth discussing before proceeding
> - SKIP: One or more auto-skip triggers fired
>
> Job description:
> <JD text>

## Step 2 — Present results

Present the subagent's full report directly to the user without summarizing or editorializing. Then state the recommendation prominently:

```
Recommendation: [PROCEED / FLAG / SKIP]
```

## Step 3 — Follow-up action

**If PROCEED:** Tell the user "Run /cover-letter to draft the letter." Do not log anything.

**If FLAG:** Tell the user "Review the flags above, then run /cover-letter if you decide to proceed." Do not log anything.

**If SKIP:** Ask the user: "Log this role as skipped in `company_log.md`? (yes/no)"
- If yes: Read `C:/Users/brown/Git/job-search/context/company_log.md`, add a row to the "Roles Skipped" table with company name, role title, date, and the skip reason (from the triggered auto-skip rule), then write the updated file and confirm.
- If no: Do nothing.
