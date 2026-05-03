---
name: cover-letter
description: Draft a cover letter for a job application following the full cover letter workflow. Two-pass workflow: Opus completeness draft, then Sonnet density revision. Invokes Haiku subagents for fit screening and style self-check. Invoke as /cover-letter [JD text, file path, PDF path, or URL].
argument-hint: "[JD text, file path, PDF path, or URL to job posting]"
allowed-tools: Read Edit Write Bash Glob Grep Agent WebFetch AskUserQuestion
---

You are drafting a cover letter for Mike Brown's job search, following the canonical workflow defined in `CLAUDE.md` (job-search project).

Follow every step in order. Do not skip steps. The workflow is two-pass: an Opus completeness draft is followed by a Sonnet density revision; both files are committed in the application PR so the cut is reviewable, and the draft is deleted before the PR is merged.

## Step 0 — Load the job description

The job description is provided in `$ARGUMENTS`. Determine input type and load accordingly:

- **No argument / pasted text:** If `$ARGUMENTS` is empty or looks like inline prose (not a path or URL), use it directly as the JD text. If empty, ask the user to paste the JD.
- **File path (`.md`, `.txt`, `.docx`):** Read the file.
- **PDF path (`.pdf`):** Read the file using the Read tool — it handles PDF extraction.
- **URL:** Fetch the page using WebFetch. Extract the job description text from the page body; discard navigation, footers, and cookie banners.

Store the extracted JD text. If the JD text is under 50 words after extraction, tell the user the source may not have loaded correctly and ask them to paste the JD directly.

Also collect the JD source string (the URL, file path, or `pasted text` literal) and today's date in `YYYYMMDD` format. These are used in Step 0b after fit screening passes.

## Step 1 — Company log check

Read `C:/Users/brown/Git/job-search/context/company_log.md`. Keep this content in context — Step 10 will reuse it without re-reading the file.
Identify the company and role from the JD text (no filename slugification needed yet — that happens in Step 0b after fit screening).
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

## Step 0b — Save the JD to disk

Run this step only after Step 2 returns PROCEED (or FLAG with user override). Auto-skipped JDs are not saved — the skip itself is logged in `Roles Skipped` and the JD source string remains in `$ARGUMENTS` history if needed.

Confirm the company name and role title with the user if not clearly recoverable from the JD. Slugify both for the filename: keep alphanumerics, replace spaces with single underscores, no other punctuation. Use the same `Company` and `Role` slugs in every artifact for this application.

Construct the JD path:

```
C:/Users/brown/Git/job-search/applications/cover_letters/MikeBrown_YYYYMMDD__Company__Role__JD.md
```

Use today's local calendar date for `YYYYMMDD`.

If a file at that path already exists, compare its `<!-- source: -->` comment to the current source string (URL or file path). If they match, skip silently — the JD is already preserved. If they differ, surface a one-line note to the user ("existing JD at <path> has source <X>; this run's source is <Y> — keeping existing file") and continue without overwriting. Otherwise, write the file with this structure:

```
<!-- source: <URL or file path or "pasted text">; fetched: YYYY-MM-DD -->

<JD text verbatim>
```

## Step 3 — Read letter writer briefing

**Session cache:** If `letter_writer_briefing.md` is already in context from a previous letter this session, skip this file read and re-use the content already loaded.

Otherwise: Read `C:/Users/brown/Git/job-search/context/letter_writer_briefing.md` in full. This file contains: universal prose rules, cover-letter-specific style rules, voice patterns, leadership positioning reference, and model letter index. Internalize all rules before drafting. Do not proceed until read.

## Step 4 — Select model letter

The `## Model Letter Index` table is in the briefing already in context. Identify the closest matching model letter for this role type from that table.
Read that model letter file (always re-read — application-specific).
Tell the user which model you selected and why.

## Step 5 — Check accomplishments

**Session cache:** If `accomplishments.md` is already in context from a previous letter this session, skip this file read and re-use the content already loaded.

Otherwise: Read `C:/Users/brown/Git/job-search/context/accomplishments.md`.

Note which accomplishments are relevant to this JD. Only claim what is listed there.

## Step 5b — Filter accomplishments

From the full accomplishments list in context, identify the 4–8 rows most directly relevant to this JD. Criteria: direct match to the JD's stated technical requirements, team size expectations, industry context, or compliance posture. Exclude rows with no plausible relevance to this specific role.

Store the filtered set as RELEVANT_ACCOMPLISHMENTS.

## Step 6 — Completeness draft (Opus subagent)

Spawn an Agent with `model: "opus"`. Pass all of the following inline — do not instruct the subagent to read any files:

- The full briefing content (from Step 3, verbatim — includes style rules, both self-checks, voice patterns, and leadership positioning reference)
- The model letter (from Step 4, verbatim)
- RELEVANT_ACCOMPLISHMENTS (from Step 5b — filtered rows only, not the full list)
- The full JD text (from Step 0)

Subagent task:

> Draft the cover letter body in Markdown. This is the **completeness draft**: prioritize narrative arc, leadership philosophy, signal calibration, and accomplishment density over compactness. No upper word cap; aim for whatever length the argument actually needs. If the draft exceeds roughly 700 words, return early with a flag — over-700 typically signals two role mandates or three threads where there should be two, and the letter plan needs revision before drafting continues.
>
> **Thread selection:** Before drafting, read the `## Leadership Positioning Reference` section of the briefing. Identify the 2–3 differentiating characteristics from `## Differentiating Characteristics` that most directly answer this role's mandate. Use the `## Positioning Threads by Role Context` table to confirm the right thread for the role type (Platform/DevEx, fintech, gov/civic, healthcare, startup, etc.).
>
> **Philosophy placement:** At least one paragraph must surface how Mike thinks about managing engineers — drawn from `## Core Philosophy` in the briefing — not just what he built. If the letter could describe a strong IC who never managed anyone, the philosophy is absent.
>
> **Narrative arc:** The career-arc paragraph must use the trajectory framing from `## Narrative Arc` in the briefing (scope growth + platform deepening + mission-driven moves), not parallel credential-listing ("At X I..., at Y I...").
>
> Apply all style rules provided:
> - No em-dashes (anywhere, no exceptions)
> - No banned constructions ("The outcomes were concrete" and all variants)
> - Do not claim Mike "led a platform directorate" — use "led platform teams responsible for..." or "led programs within the platform organization"
> - Output is Markdown only
>
> Use the model letter as a structural reference; do not copy text verbatim. Adapt tone and emphasis to the specific JD.
>
> Return only the letter body — no preamble, no commentary.

Collect the subagent's output as DRAFT.

If the subagent flagged over-700, surface to the user and ask whether to proceed (revise the letter plan and re-run) or override and continue with the long draft.

Save DRAFT to:

```
C:/Users/brown/Git/job-search/applications/cover_letters/MikeBrown_YYYYMMDD__Company__Role__Cover_Letter_Draft.md
```

Same `YYYYMMDD`, `Company`, and `Role` slugs as the JD file from Step 0b.

## Step 7 — Density revision (Sonnet, inline)

Apply the precision-then-compactness pass from `prose_style.md` (`## Precision in Word Choice` and `## Compactness Techniques`) aggressively to DRAFT. Target 400 words; 450 is the hard ceiling. The revision should sharpen verbs, remove hedges, collapse redundant clauses, and tighten transitions while preserving the narrative arc, philosophy paragraph, and signal calibration of the completeness draft.

Save the revised letter to:

```
C:/Users/brown/Git/job-search/applications/cover_letters/MikeBrown_YYYYMMDD__Company__Role__Cover_Letter.md
```

If this letter is intended as a new canonical model (a new role type not yet in `models/letters/`), also copy the revised version to `C:/Users/brown/Git/job-search/models/letters/` and update `models/INDEX.md`.

## Step 8 — Style self-check (Haiku subagent)

The briefing is already in context from Step 3. Extract verbatim: (a) the `## Universal Self-Check` section from the briefing's `## Universal Prose Rules` and (b) the `## Cover-Letter-Specific Self-Check` section from the briefing's `## Cover Letter Rules`. Pass both inline — do not instruct the subagent to read any files.

Spawn a **Haiku** subagent with this task:

> You are performing a style self-check on a cover letter. Run both checks in sequence. Report each violation with the offending text quoted and the rule it breaks. If no violations in either check, report "PASS".
>
> **Check 1 — Universal Self-Check:**
> <paste the "## Universal Self-Check" section from the briefing verbatim>
>
> **Check 2 — Cover-Letter-Specific Self-Check:**
> <paste the "## Cover-Letter-Specific Self-Check" section from the briefing verbatim>
>
> **Letter body:**
> <paste the Step 7 density-revised letter here>

Report the subagent's findings to the user.

## Step 9 — Fix violations and verify word count

Apply every violation flagged in Step 8 to the `__Cover_Letter.md` file. Re-read each fixed passage to confirm it is clean.

Write the final letter body to a temp file and count words:
```bash
TMPFILE="C:/Users/brown/.claude/scratch/wc_$$.txt"
cat > "$TMPFILE" << 'LETTER'
<letter body here>
LETTER
wc -w < "$TMPFILE"
rm -f "$TMPFILE"
```

The body must be at most 450 words with a 400-word target (count body paragraphs only — exclude header block, salutation, and sign-off). If over 450, trim further. Report the final word count to the user.

## Step 10 — Log the application

The `context/company_log.md` content is already in context from Step 1 — do not re-read the file.
Add a row to the "Roles Completed" table with: company name, role title, date, and the file path of the saved `__Cover_Letter.md` file.
Write the updated file.

## Step 11 — Report to user

Before reporting file paths, compute paths relative to the current working directory so links resolve correctly in the UI (this matters when the session runs in a git worktree).

For each of the three artifacts (`__JD.md`, `__Cover_Letter_Draft.md`, `__Cover_Letter.md`), replace `<abs>` with the absolute path written and run:

```bash
ABS="<abs>"
PYBIN=$(command -v python3 || command -v python)
"$PYBIN" -c "import os; print(os.path.relpath('$ABS', os.getcwd()).replace('\\', '/'))"
```

Use the printed paths as the markdown link hrefs — e.g., `[filename](relative/path/filename.md)`.

Tell the user:
- Where each of the three artifacts was saved (clickable markdown links using the relative paths computed above)
- Final word count of `__Cover_Letter.md`
- Any flags raised during fit screening that are still relevant
- Any open items (e.g., missing salary range, unclear hiring manager name)

## Step 12 — Pre-merge cleanup note

Remind the user that before the application PR is merged, the `__Cover_Letter_Draft.md` artifact must be deleted from the branch — only `__JD.md` and `__Cover_Letter.md` should land on `main`. The density revision is the canonical artifact; the draft was a process aid that exists in the PR for reviewable contrast only. The skill itself does not delete the draft (the PR has not yet been opened at this point in the workflow); the cleanup is the responsibility of the author or the merge-time session, per `CLAUDE.md` "How to Draft" Step 13.
