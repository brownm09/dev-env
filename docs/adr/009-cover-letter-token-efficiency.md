# ADR 009 — Cover Letter Token Efficiency: Inline Drafting and Session Reuse

**Date:** 2026-05-01  
**Status:** Accepted

---

## Context

A token-profile analysis of the `/cover-letter` skill (May 2026) found that the draft subagent in Step 6 was the dominant per-letter cost. The subagent received all four context files verbatim (~2,000 tokens per letter) even though the main agent already held that content in its context window. For batch runs — generating three to five letters in a single session — this re-pass compounded without any proportional quality gain.

Four concrete inefficiencies were identified:

1. **Redundant subagent spawn for Sonnet.** The draft subagent was always spawned, regardless of model selection. When the user selected Sonnet, the subagent received ~2,000 tokens of context the main agent already had. The re-pass served no purpose: the main agent and subagent were the same model, so spawning added overhead without adding capability.

2. **Full accomplishments list passed to subagent.** `accomplishments.md` (~540 tokens) was passed verbatim to the Opus draft subagent even though only 4–8 rows are typically relevant to any given JD. The main agent had already read the full file and identified the relevant rows; the subagent received redundant context.

3. **Stable context files re-read every letter.** Steps 3 (`style_rules.md`), 5 (`accomplishments.md`), and 5b (`VOICE_SYNOPSIS.md`) always re-read from disk on every invocation. In a batch session, these files do not change between letters. Re-reading them burns main-agent tokens that could be avoided by reusing the content already loaded.

4. **Word-count ceiling mismatch.** The skill instructed the draft subagent to target 400 words, but the canonical ceiling in `CLAUDE.md`, `style_rules.md`, and `applications/README.md` is 475 words. The discrepancy was an authoring error, not an intentional margin.

Estimated token cost per letter before this change: ~4,400 tokens (Haiku fit screen ~650 + main agent context reads ~1,235 + draft subagent ~1,970 + Haiku self-check ~550).

---

## Decision

Four targeted changes to `claude/skills/cover-letter/SKILL.md` and a documentation update to the job-search `CLAUDE.md`:

**1. Default to Sonnet; inline drafting for Sonnet path.**  
Step -1 now presents Sonnet as the recommended model. When DRAFT_MODEL is "sonnet", Step 6 drafts the letter directly in the main agent — all context is already in context from Steps 3–5b. The main agent also runs the self-check inline, eliminating the separate Haiku self-check subagent (Step 7). The Opus path is unchanged: an Opus subagent is still spawned and the Haiku self-check still follows it.

**2. Filter accomplishments before Opus subagent pass (Step 5c).**  
After reading `accomplishments.md` in Step 5, a new Step 5c identifies the 4–8 rows most relevant to the current JD and stores them as RELEVANT_ACCOMPLISHMENTS. The Opus draft subagent (Step 6) receives RELEVANT_ACCOMPLISHMENTS rather than the full list.

**3. Session-cache notes for stable context files.**  
Steps 3, 5, and 5b include an explicit session-cache check: if the file is already in context from a previous letter this session, the read is skipped and the existing content is reused. Step 4 (model letter) and Step 2 (fit screening) always re-run — the model letter changes per letter, and fit screening always spawns a fresh Haiku subagent regardless.

**4. Word-count ceiling corrected to 475.**  
Steps 6 and 9 both changed from 400 → 475 to match the canonical ceiling. The job-search `CLAUDE.md` model routing table was updated to document Sonnet as the default for cover letter drafting.

---

## Consequences

Projected token savings:

| Scenario | Before | After | Saved |
|---|---|---|---|
| Single Sonnet letter | ~4,400 tokens | ~2,450 tokens | ~44% |
| Single Opus letter | ~4,400 tokens | ~4,050 tokens | ~8% |
| Batch of 5 Sonnet letters | ~22,000 tokens | ~7,300 tokens | ~67% |
| Batch of 5 Opus letters | ~22,000 tokens | ~15,750 tokens | ~28% |

The Sonnet inline-drafting path eliminates the draft subagent spawn and the Haiku self-check subagent — approximately 2,520 tokens per letter. Batch savings for letters 2–5 add ~1,170 tokens each (stable context reads eliminated). The Opus path savings are smaller: filtered accomplishments (~200–350 tokens per letter) plus session reuse on letters 2–5.

A follow-on fix (same PR) closed a pre-existing gap: the self-check previously targeted only the `## Cover-Letter-Specific Self-Check` section from `style_rules.md`, which explicitly does not duplicate the Universal Self-Check items from `prose_style.md`. `prose_style.md` was never read by the skill, so the 23-item Universal Self-Check (em-dash ban, filler phrases, AI-tell patterns, rhythm checks) was applied at drafting time but not verified at self-check time. The fix adds a session-cached `prose_style.md` read as Step 3b and updates both self-check paths to run the Universal check first, then the cover-letter-specific extensions. Token cost of the fix: ~500 tokens to main agent context (first letter per session; cached after that) and ~150 tokens to the Haiku subagent (Opus path only, Universal Self-Check section passed inline). The savings figures above remain accurate within rounding.

Prose quality for Sonnet letters is expected to be comparable to the previous Opus output for routine applications, because the main agent has full context and applies the same style rules. Opus remains available for high-stakes applications where the user explicitly needs it.

The word-count ceiling correction has no impact on letters that were already staying under 400 words; letters where the draft was being trimmed unnecessarily will now have 75 words of additional room.

---

## References

- `claude/skills/cover-letter/SKILL.md` — implementation of all changes
- `claude/skills/cover-letter/WORKFLOW.md` — visual flow diagram of the skill execution path
- `job-search/CLAUDE.md` (brownm09/career-playbook) — Model Routing table updated (Opus → Sonnet for cover letter draft)
- Engineering journal: `sessions/job-search/` — session covering this analysis and implementation
- dev-env issue #147 — token-efficiency analysis and proposed fixes
