# ADR 012 — Two-Pass Voice Analysis Workflow

**Date:** 2026-05-03  
**Status:** Accepted

---

## Context

Voice-quality work in the career-playbook through early May 2026 was guardrail-only: prohibited constructions (filler phrases, wind-up constructions, AI-tell patterns) were enumerated in `context/prose_style.md` and stripped during revision. The rules were accurate but incomplete — guardrails describe what to remove, not what to keep. Prose edited against guardrails alone tended to become grammatically clean while losing the register qualities that make Mike's actual writing recognizable.

The May 3, 2026 voice analysis session examined the Fetch cover letter (`models/letters/MikeBrown_20260421__Fetch__Director_Engineering__Cover_Letter.md`) and the Vaughn essays (`models/voice/Vaughn_*.md`) to extract what Mike's prose *does*, not only what it avoids. Seven clusters of design decisions followed from that analysis.

**Root finding:** positive structural choices (definitional opener, em-dash parenthetical carrying qualifying detail, semicolon cause-effect, working aphorism, people-as-grammatical-subject) establish register before guardrails are applied. Applying guardrails before calibrating the positive target strips tics but also strips the moves that gave the prose its voice.

---

## Decision

Seven sub-decisions made in the same session, each grounded in the root finding:

**1. Two-pass workflow.**  
Read 2–3 actual Mike samples (register-matched model letter + one Vaughn essay) before applying any rules. The samples calibrate the positive target; guardrails catch tics that crept in during drafting. When a rule and a positive move conflict, hold the move and find a different tic to fix. Encoded in `prose_style.md` → `## Positive Moves` as the opening workflow note.

**2. Wind-up rule broadened to four categories.**  
The existing wind-up ban covered action narration only ("The work I am most proud of is..."). The session identified three additional wind-up categories where meta-framing delays substance: insight wind-ups ("X has shaped how I think about Y"), credential wind-ups ("X gave me the discipline this work requires"), and value/opinion wind-ups ("What I have come to believe is Y"). All four categories follow the same correction: lead with the substance, let the source emerge from the work. Rationale: the tendency to announce what is coming before landing it is a Claude generation tic, not an action-specific one. Encoded in `prose_style.md` → `## Voice and Style`.

**3. Aphorism placement + honesty test.**  
Working aphorisms (short declarative working principles) function in three positions: letter opener (attention bid before evidence exists), paragraph close (earned distillation of evidence just presented), and letter close (portable principle the reader carries away). Aphorisms fail at evidence paragraph openers because they perform conviction before earning it. Separately, identity-claim aphorisms that flatten distinct things into "X is Y" when X and Y are not literally the same fail an honesty test — they produce the form of insight without the substance. Real aphorisms compress an earned observation that survives scrutiny. Encoded in `prose_style.md` → `## Positive Moves` → Move 5.

**4. Closing CTA framework.**  
Closing constructions were previously defined as Construction 1–5 without explicit bans on patterns that undermine them. The session identified three banned forms — hedge-asks that soften the close into a disclaimer, deposit-want constructions ("I want to bring my record to your team") that frame Mike as depositing credentials without earning the destination, and claim-of-fit constructions that assert Mike belongs without inviting engagement. Six endorsed patterns replace them: bridge (strong outcome → role mandate conversation), open-question (open-ended question inviting the company's perspective), specific-thread (named thread worth taking deeper), operating claim (what Mike will do in the role, stated as a claim), two-level (research signal → bridge → specific question, three sentences), and mandate-reframe (restated mandate + record assertion, no conversation ask — working default). Rationale: closing constructions should invite engagement, not deposit or claim. Encoded in `letter_rules.md` → `## Closing CTAs`.

**5. Signal calibration card as sidecar.**  
The 17-dimension signal calibration card was previously embedded as an HTML comment in the cover letter file. The session moved it to a separate `__Signal_Card.md` sidecar file. Rationale: the letter is the artifact; the calibration card is instrumentation. Embedding instrumentation in the artifact conflates the two and makes the card invisible in rendered output. Sidecars also persist after draft files are deleted. Encoded in `CLAUDE.md` → How to Draft step 11. Cross-referenced in issue #86 (signal calibration reconciliation).

**6. "Proposed and owned" verb pattern.**  
The compact construction "proposed and owned [the deprecation/retirement of X]" surfaces engineering taste (Signaling Dimension 16) explicitly by separating the proposal (judgment to retire a system) from the execution (ownership of the work). Most engineers maintain inherited systems; proposing retirement signals taste distinct from execution discipline. The pattern applies heaviest in platform, infrastructure, and DevEx contexts; lighter in pure delivery or program management contexts. Encoded in `prose_style.md` → `## Signaling Dimensions` → Dimension 16.

**7. Restraint and People-first sub-rules scoped by artifact.**  
The restraint sub-rule (Dim 9: signal density and word economy) and People-first sub-rule (Dim 7: Mike as grammatical subject in active verbs) are calibrated by what the artifact is selling. Cover letters sell Mike: Mike-as-agent verbs are appropriate. Engineering journals and retrospectives sell the work: attribution patterns that foreground the team or the problem are appropriate. Sub-rules that work in one register can undermine another. Encoded in `prose_style.md` → `## Signaling Dimensions` notes for Dim 7 and Dim 9.

---

## Alternatives Considered

**Guardrails-only revision (prior approach).** Enumerating prohibited constructions and stripping them during revision. Rejected because guardrails describe what to remove; they cannot restore the positive structural choices that give Mike's voice its register. Prose that survives a guardrails pass can still read as generic competent prose rather than as Mike's voice. The guardrails remain — they are applied in the second pass — but they are not the primary voice-setting mechanism.

**Per-artifact positive-moves lists.** Maintaining separate positive-moves catalogs for cover letters, LinkedIn posts, journal entries, etc. Rejected because the moves are sourced from Mike's actual writing samples, which transfer across registers with minor calibration adjustments (not structural rewrites). A single list with register-calibration notes (see `prose_style.md` → `## Positive Moves` → "For other artifacts: see the register calibration notes in `docs/voice-analysis-2026-05-03.md`") is sufficient and avoids drift between parallel lists.

---

## Consequences

- `career-playbook/context/prose_style.md` — `## Positive Moves` section added documenting the two-pass workflow (opening note) and Moves 1–10 sourced from Fetch and Vaughn samples.
- `career-playbook/context/prose_style.md` — `## Voice and Style` → wind-up rule extended from action category to four categories.
- `career-playbook/context/letter_rules.md` — `## Closing CTAs` section added with banned and endorsed forms.
- `career-playbook/CLAUDE.md` — Step 11 added to How to Draft (signal calibration card as sidecar); Non-Negotiable Rules updated.
- `career-playbook/docs/voice-analysis-2026-05-03.md` — primary reference document for the session analysis and move sourcing.
- Signal calibration cards (`__Signal_Card.md`) are committed alongside every new cover letter; draft files are deleted at PR merge; cards persist permanently.

---

## References

- `career-playbook/context/prose_style.md` — implementation of decisions 1, 2, 3, 6, 7
- `career-playbook/context/letter_rules.md` — implementation of decision 4
- `career-playbook/CLAUDE.md` — implementation of decision 5 (sidecar workflow)
- `career-playbook/docs/voice-analysis-2026-05-03.md` — session analysis and register calibration notes for non-cover-letter artifacts
- dev-env issue #169 — this ADR
- career-playbook issue #82 — audit of non-cover-letter artifacts for wind-up violations (decision 2 follow-on)
- career-playbook issue #83 — codify positive moves and closing CTA patterns (decisions 1, 4 follow-on)
