---
name: fit-screen
description: Run fit screening on a job description for Mike Brown's job search. Uses a Haiku subagent to check auto-skip triggers, soft flags, and compensation floor. Returns PROCEED, FLAG, or SKIP. Invoke as /fit-screen [JD text or file path].
argument-hint: "[JD text or path to JD file]"
allowed-tools: Read Bash Agent
---

You are running a fit screen for Mike Brown's job search.

The job description is provided in `$ARGUMENTS`. If it is a file path, read it. If it is inline text, use it directly. If neither is provided, ask the user for the JD before proceeding.

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

After the subagent returns, present its report directly to the user without summarizing or editorializing. Then state the recommendation prominently:

```
Recommendation: [PROCEED / FLAG / SKIP]
```

If FLAG, add: "Run /cover-letter once you've reviewed the flags and decided to proceed."
If PROCEED, add: "Run /cover-letter to draft the letter."
If SKIP, add: "Role logged as skipped? If you want to override, re-run /cover-letter manually."
